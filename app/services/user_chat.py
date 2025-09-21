from app.utils.langchain_helper import load_llm, format_user_question, get_stream_ai_response, get_ai_response, _create_moderation_chain, load_llm_in_json_mode
from app.models.chatbot_model import ChatbotConfig, ChatbotGuardrail
from app.models.user import User, UserRole
from sqlalchemy.future import select
from app.common.env_config import get_envs_setting
from fastapi import HTTPException,status
from app.utils.database_helper import check_and_refresh_chat_cycle, format_user_chatbot_permissions,increment_chatbot_message_count, increment_chatbot_monthly_message_count,increment_chatbot_per_day_message_count,increment_admin_chatbot_message_count
# import redis
from app.schemas.request.user_chat import UserChat
import json
from app.utils.db_helpers import get_user_organization_admin
from sqlalchemy.ext.asyncio import AsyncSession
from app.schemas.request.user_chat import MessageFeedbackRequest, UserSecureChat, PublicMessageFeedbackRequest
from app.models.chatbot_model import MessagesFeedbacks, FeedbackStatus
from datetime import datetime, timedelta, timezone
from redis.asyncio import Redis
from redis.asyncio.cluster import RedisCluster
from langchain_core.messages import AIMessage, HumanMessage,ToolMessage
import json
from app.models.chatbot_model import Threads, Messages
import time
from fastapi import BackgroundTasks
from app.utils import langchain_helper
import logging
from app.models.user import UserRole, Plan
import boto3
import openai
import uuid
from app.schemas.response.user_chat import MessageFeedbackResponse,S3Response, S3ResponseItem, ShareChatbotResponse, PublicS3Response, PublicS3ResponseItem
from langchain_openai import ChatOpenAI
import ast
from app.schemas.response.user_chat import AllThreadResponse, AllThreadResponseItem
from urllib.parse import urlparse
import asyncio
from typing import AsyncGenerator, Dict, Any

envs = get_envs_setting()

import json
import asyncio

sqs_client = boto3.client("sqs", region_name=envs.AWS_REGION)

# Configure logging
logging.basicConfig(
    level=logging.INFO
)
def configure_persistence(redis_client):
    redis_client.config_set("save", "900 1")   #Not for elasitcache

    print("Redis persistence configured with RDB and AOF")

redis_store = Redis(
    host=envs.REDIS_HOST, 
    port=envs.REDIS_PORT, 
    decode_responses=True,
    username="default",
    password="4ZLcWah0ofyql0KvynHmb30l94AwyBx2",
    ) #for local

logger = logging.getLogger(__name__)


# redis_store = RedisCluster(
#     host=envs.REDIS_HOST, 
#     port=envs.REDIS_PORT, 
#     ssl=True ,
#     decode_responses=True,
#     max_connections=50, # per-client pool limit (tune)
#     socket_connect_timeout=10, # seconds to establish TCP
#     socket_timeout=7, # seconds for commands
#     ) # AWS redis does not have auth applied.


# configure_persistence(redis_store)
# Initialize S3 client
s3_client = boto3.client("s3")
from cryptography.fernet import Fernet
import base64
fernet = Fernet(envs.CHATBOT_SECRET_KEY)


def _encrypt_chatbot_id(chatbot_id: str, fernet) -> str:
    """Encrypts the chatbot ID."""
    try:
        return fernet.encrypt(str(chatbot_id).encode()).decode()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Encryption failed: {str(e)}")

def _decrypt_chatbot_id(encrypted_chatbot_id: str, fernet) -> str:
    """Decrypts the chatbot ID."""
    try:
        return fernet.decrypt(encrypted_chatbot_id.encode()).decode()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid chatbot ID")
        

async def generate_public_pre_signed_url(request):
    bot_id = _decrypt_chatbot_id(request.bot_id, fernet)
    fingerprint = request.fingerprint
    thread_id = request.thread_id
    files = request.upload_files
    if not thread_id:
            existing_thread = await redis_store.get(f"fingerprint:{fingerprint}")
            print(f"Existing thread id is : {existing_thread}")
           
            if existing_thread:
                raise HTTPException(
                    status_code=400,
                    detail={
                        "detail": "This fingerprint already has a thread",
                        "thread_id": existing_thread
                    }
                )
            
            new_thread_id = str(uuid.uuid4())
            print(f"New thread id is : {new_thread_id}")
          
            await redis_store.set(
                f"fingerprint:{fingerprint}", 
                new_thread_id, 
                ex=envs.FINGERPRINT_DURATION_SECONDS
            )

            await redis_store.set(f"thread:{new_thread_id}:tokens", 0, ex=envs.USER_KEY_DURATION_SECONDS)
            await redis_store.set(f"thread:{new_thread_id}:requests", 0, ex=envs.USER_KEY_DURATION_SECONDS)
            
            thread_id = new_thread_id

    if not files or not bot_id:
            raise HTTPException(status_code=400, detail="Missing required parameters")
    print(f'bot id == {bot_id}')
    user_file_uplaod_key = f"thread:{thread_id}:uploads"
    file_upload_count = int(await redis_store.get(user_file_uplaod_key) or 0)

    print(f"\nUser file upload count is {file_upload_count}\n\n")
    

    # Get the remaining time to live (TTL) for a key
    ttl = await redis_store.ttl(user_file_uplaod_key)

    if ttl == -1:
        ttl = 5
        print("ES: The user token key has no associated expiration.")
    elif ttl == -2:
        ttl = 5
        print("ES: The user token key does not exist.")
    else:
        print(f"ES: The user token key will expire in {ttl} seconds.")
    

    if file_upload_count >= envs.USER_UPLOADS_PER_X_SECONDS:
        raise HTTPException(status_code=429, detail="File upload limit exceeded. PLease try again after {ttl} seconds")
    
    presigned_urls = []
    
    for file_item in files:
        file_name = file_item.file_name
        file_types = file_item.file_type
    
        # Generate a unique S3 key (bot_id/thread_id/timestamp_filename)
        s3_key = f"uploaded_public_images/{bot_id}/{thread_id}/{file_name}"

        # Generate pre-signed URL (Valid for 5 minutes)
        presigned_url = s3_client.generate_presigned_url(
            "put_object",
            Params={
                "Bucket": envs.BUCKET_NAME,
                "Key": s3_key,
                "ContentType": file_types
            },
            ExpiresIn=300  # 5 minutes
        )
        # Append to list
        presigned_urls.append(PublicS3ResponseItem(
            upload_url=presigned_url,
            s3_key=s3_key,
            filename = file_item.file_name,
            thread_id=thread_id
        ))

        if not await redis_store.exists(user_file_uplaod_key):
            await redis_store.incr(user_file_uplaod_key)
            await redis_store.expire(user_file_uplaod_key, envs.USER_FILEUPLOADS_KEY_DURATION_SECONDS)
        else:
            await redis_store.incrby(user_file_uplaod_key, 1)

    return presigned_urls, thread_id if not request.thread_id else None

def get_total_file_size(org_id: str, bot_id: str) -> int:
    total_size = 0
    try:
        # List objects in the S3 folder
        response = s3_client.list_objects_v2(
            Bucket=envs.BUCKET_NAME,
            Prefix=f"uploaded_file_doc/{org_id}/{bot_id}/"  # Specify the folder path
        )

        # If files exist in the folder, sum their sizes
        if 'Contents' in response:
            for file in response['Contents']:
                total_size += file['Size']
                
        return total_size
    except Exception as e:
        print(f"Error fetching file size: {e}")
        return 0

async def generate_pre_signed_url(request, cur_user, org_id, session):
    bot_id = request.bot_id
    thread_id = request.thread_id
    files = request.upload_files
    upload_type = request.upload_type
    if cur_user.role == UserRole.SUPER_ADMIN:
        org_id = org_id
    else:
        org_id = cur_user.organization_id
    if not files:
            raise HTTPException(status_code=400, detail="Missing required parameters")
    created_thread_id = 0
    presigned_urls = []
    
    if upload_type == "profile":
        s3_key = f"profile/{cur_user.id}"
        for file_item in files:
            file_name = file_item.file_name
            file_types = file_item.file_type
            # Generate pre-signed URL (Valid for 5 minutes)
            presigned_url = s3_client.generate_presigned_url(
                "put_object",
                Params={
                    "Bucket": envs.BUCKET_NAME,
                    "Key": s3_key,
                    "ContentType": file_types
                },
                ExpiresIn=300  # 5 minutes
            )
            # Append to list
            presigned_urls.append(S3ResponseItem(
                upload_url=presigned_url,
                s3_key=s3_key,
                filename = file_item.file_name,
                thread_id=created_thread_id
            ))
    elif upload_type == 'image_upload':
        if not thread_id:
            create_thread = Threads(
            user_id = cur_user.id,
            chatbot_id = bot_id,
            title = 'New Chat',
            title_manual_update = True,
            )

            session.add(create_thread)
            await session.commit()
            await session.refresh(create_thread)
            thread_id = create_thread.thread_id
            created_thread_id = thread_id
        
        for file_item in files:
            file_name = file_item.file_name
            file_types = file_item.file_type    
            # Generate a unique S3 key (bot_id/thread_id/timestamp_filename)
            s3_key = f"uploaded_images/{cur_user.id}/{bot_id}/{thread_id}/{file_name}"

            # Generate pre-signed URL (Valid for 5 minutes)
            presigned_url = s3_client.generate_presigned_url(
                "put_object",
                Params={
                    "Bucket": envs.BUCKET_NAME,
                    "Key": s3_key,
                    "ContentType": file_types
                },
                ExpiresIn=300  # 5 minutes
            )
            # Append to list
            presigned_urls.append(S3ResponseItem(
                upload_url=presigned_url,
                s3_key=s3_key,
                filename = file_item.file_name,
                thread_id=created_thread_id
            ))
    elif upload_type == 'file_upload':
        total_file_size = get_total_file_size(org_id, bot_id)
        for file_item in files:
            total_file_size += file_item.file_size
        # If the total size exceeds 50 MB (50 * 1024 * 1024 bytes)
        if total_file_size > 50 * 1024 * 1024:
            raise HTTPException(status_code=400, detail="Total file size exceeds the allowed limit of 50 MB.")
        
        for file_item in files:
            # Generate a unique S3 key (bot_id/thread_id/timestamp_filename)
            file_name = file_item.file_name
            file_types = file_item.file_type
            file_size = file_item.file_size
            
            total_file_size += file_size
            s3_key = f"uploaded_file_doc/{org_id}/{bot_id}/{file_name}"

            # Generate pre-signed URL (Valid for 5 minutes)
            presigned_url = s3_client.generate_presigned_url(
                "put_object",
                Params={
                    "Bucket": envs.BUCKET_NAME,
                    "Key": s3_key,
                    "ContentType": file_types
                },
                ExpiresIn=300  # 5 minutes
            )
            # Append to list
            presigned_urls.append(S3ResponseItem(
                upload_url=presigned_url,
                s3_key=s3_key,
                filename = file_item.file_name,
                thread_id=created_thread_id
            ))
    elif upload_type == 'avatar':
        for file_item in files:
            # Generate a unique S3 key (bot_id/thread_id/timestamp_filename)
            file_name = file_item.file_name
            file_types = file_item.file_type
            s3_key = f"avatar/{org_id}/{bot_id}/{file_name}"


            # Generate pre-signed URL (Valid for 5 minutes)
            presigned_url = s3_client.generate_presigned_url(
                "put_object",
                Params={
                    "Bucket": envs.BUCKET_NAME,
                    "Key": s3_key,
                    "ContentType": file_types
                },
                ExpiresIn=300  # 5 minutes
            )
            # Append to list
            presigned_urls.append(S3ResponseItem(
                upload_url=presigned_url,
                s3_key=s3_key,
                filename = file_item.file_name,
                thread_id=created_thread_id
            ))
    elif upload_type == 'public':
        for file_item in files:
            file_name = file_item.file_name
            file_types = file_item.file_type
        
            # Generate a unique S3 key (bot_id/thread_id/timestamp_filename)
            s3_key = f"uploaded_public_images/{bot_id}/{thread_id}/{file_name}"

            # Generate pre-signed URL (Valid for 5 minutes)
            presigned_url = s3_client.generate_presigned_url(
                "put_object",
                Params={
                    "Bucket": envs.BUCKET_NAME,
                    "Key": s3_key,
                    "ContentType": file_types
                },
                ExpiresIn=300  # 5 minutes
            )
            # Append to list
            presigned_urls.append(S3ResponseItem(
                upload_url=presigned_url,
                s3_key=s3_key,
                filename = file_item.file_name,
                thread_id=created_thread_id
            ))
    elif upload_type == 'widgets':
        for file_item in files:
            file_name = file_item.file_name
            file_types = file_item.file_type
        
            # Generate a unique S3 key (bot_id/thread_id/timestamp_filename)
            s3_key = f"widgets/{bot_id}/{thread_id}/{file_name}"

            # Generate pre-signed URL (Valid for 5 minutes)
            presigned_url = s3_client.generate_presigned_url(
                "put_object",
                Params={
                    "Bucket": envs.BUCKET_NAME,
                    "Key": s3_key,
                    "ContentType": file_types
                },
                ExpiresIn=300  # 5 minutes
            )
            # Append to list
            presigned_urls.append(S3ResponseItem(
                upload_url=presigned_url,
                s3_key=s3_key,
                filename = file_item.file_name,
                thread_id=created_thread_id
            ))
    elif upload_type == 'bulk_users':
        if cur_user.role in [UserRole.SUPER_ADMIN, UserRole.USER]:
            cur_user = await get_user_organization_admin(db = session, organization_id = org_id)
            if cur_user.current_plan != Plan.enterprise:
                raise HTTPException(
                    status_code=443,
                    detail="Please upgrade your plan to create users."
                )
        for file_item in files:
            file_name = file_item.file_name
            file_types = file_item.file_type
        
            # Generate a unique S3 key (bot_id/thread_id/timestamp_filename)
            user_query = select(User).filter(User.id == cur_user.id)
            user_result = await session.execute(user_query)
            user = user_result.scalar_one_or_none()

            if not user:
                raise HTTPException(status_code=404, detail="User not found") 

            s3_key = f"bulk_upload/{org_id}/{file_name}"
            # s3_key = f"bulk-users/{bot_id}/{thread_id}/{file_name}"

            # Generate pre-signed URL (Valid for 5 minutes)
            presigned_url = s3_client.generate_presigned_url(
                "put_object",
                Params={
                    "Bucket": envs.BUCKET_NAME,
                    "Key": s3_key,
                    "ContentType": file_types
                },
                ExpiresIn=300  # 5 minutes
            )
            # Append to list
            presigned_urls.append(S3ResponseItem(
                upload_url=presigned_url,
                s3_key=s3_key,
                filename = file_item.file_name,
                thread_id=created_thread_id
            ))
    elif upload_type == "org_profile":
        s3_key = f"org_profile_image/{org_id}"
        for file_item in files:
            file_name = file_item.file_name
            file_types = file_item.file_type
            # Generate pre-signed URL (Valid for 5 minutes)
            presigned_url = s3_client.generate_presigned_url(
                "put_object",
                Params={
                    "Bucket": envs.BUCKET_NAME,
                    "Key": s3_key,
                    "ContentType": file_types
                },
                ExpiresIn=300  # 5 minutes
            )
            # Append to list
            presigned_urls.append(S3ResponseItem(
                upload_url=presigned_url,
                s3_key=s3_key,
                filename = file_item.file_name,
                thread_id=created_thread_id
            ))
    elif upload_type == "form_submission":
        for file_item in files:
            # Generate a unique S3 key (bot_id/thread_id/timestamp_filename)
            file_name = file_item.file_name
            file_types = file_item.file_type
            file_size = file_item.file_size
            
            
            s3_key = f"pending_events/{org_id}/external/{file_name}"
            # Generate pre-signed URL (Valid for 5 minutes)
            presigned_url = s3_client.generate_presigned_url(
                "put_object",
                Params={
                    "Bucket": envs.BUCKET_NAME,
                    "Key": s3_key,
                    "ContentType": file_types
                },
                ExpiresIn=300  # 5 minutes
            )
            # Append to list
            presigned_urls.append(S3ResponseItem(
                upload_url=presigned_url,
                s3_key=s3_key,
                filename = file_item.file_name,
                thread_id=created_thread_id
            ))
    else:
        raise HTTPException(status_code=400, detail="Please provide valid file type")
    return presigned_urls

# async def get_shared_bot_url_service(session, chatbot_id, organization_id, user):
#     chatbots = await list_organization_chatbots_service(session, organization_id, user)
#     if not any(chatbot.id == chatbot_id for chatbot in chatbots):
#         raise HTTPException(
#             status_code=status.HTTP_403_FORBIDDEN,
#             detail="You don't have access to this chatbot or it doesn't exist"
#         )
#     share_url = f"http://localhost:5173/{chatbot_id}/sharedchat"
#     return {"url": share_url}


async def linkchecks(chatbot_id, session, organization_id, user):
    if user.role == UserRole.USER:
        raise HTTPException(
                status_code=403,
                detail="You are not autherized to create link for chatbot."
            )
    query = select(ChatbotConfig).filter(
        ChatbotConfig.id == chatbot_id,
        ChatbotConfig.organization_id == organization_id
    )
    
    result = await session.execute(query)
    chatbot = result.scalar_one_or_none()
    if not chatbot:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You don't have access to this chatbot"
        )
    if chatbot.chatbot_type != "External":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can share only external chatbot"
        )
    return


async def get_share_service(session, chatbot_id, organization_id, user):
    await linkchecks(chatbot_id, session, organization_id, user)
    
    # if user.role == UserRole.ADMIN and user.current_plan == Plan.free and not user.is_paid:
    #     print('exception')
    #     raise HTTPException(
    #         status_code=403,
    #         detail="Please upgrade your plan from Free tier."
    #     )
    #     return

    # Secret key for encryption (should be stored securely)
    encrypted_chatbot_id = _encrypt_chatbot_id(chatbot_id, fernet)
      # Ensure 32-byte key
    share_url = f"{envs.FRONTEND_HOST}sharedchat/{encrypted_chatbot_id}"
    iframe = f'<iframe src="{share_url}" width="600" height="400" frameborder="0"></iframe>'
    script = f'<script src="{envs.FRONTEND_HOST}chatbubble.js" data-chatbot-id="{encrypted_chatbot_id}"></script>'
    # script = '<script src="http://127.0.0.1:8080/chatbubble.js"></script>'

    return ShareChatbotResponse(
        chatbot_id=chatbot_id,
        share_url=share_url,
        iframe=iframe,
        script=script
    )
    # return {"url": share_url, "iframe": iframe, "script": script}

async def get_public_thread_service(fingerprint_id):
    existing_thread = await redis_store.get(f"fingerprint:{fingerprint_id}")
    print(f"Existing thread id is : {existing_thread}")
    if not existing_thread:
        existing_thread = ""
        # new_thread_id = str(uuid.uuid4())
        # print(f"New thread id is : {new_thread_id}")
        
        # await redis_store.set(
        #     f"fingerprint:{existing_thread}", 
        #     new_thread_id, 
        #     ex=envs.FINGERPRINT_DURATION_SECONDS
        #     )
        # raise HTTPException(status_code=404, detail="Thread not found")
    
    return existing_thread

from app.models.user import Plan
from app.services.organization import get_organization_chabot_name

async def _get_chatbot_config(data, session):
    result = await session.execute(select(ChatbotConfig).filter(ChatbotConfig.id == data.bot_id)) 
    return result.scalars().first()

async def get_chatbot_config_by_id(bot_id, session):
    result = await session.execute(select(ChatbotConfig).filter(ChatbotConfig.id == bot_id)) 
    return result.scalars().first()

async def _get_user(user_id, session):
    result = await session.execute(select(User).filter(User.id == user_id)) 
    return result.scalars().first()

async def _get_admin(organization_id, session):
    result = await session.execute(
        select(User).where(User.organization_id == organization_id, User.role == UserRole.ADMIN)
    )
    return result.scalars().first()

async def _get_guardrails(bot_id, session):
    result = await session.execute(
        select(ChatbotGuardrail.guardrail_text).filter(ChatbotGuardrail.chatbot_id == bot_id)
    )
    guardrails = result.scalars().all()  # Fetch all guardrail texts as a list
    return ", ".join(guardrails) if guardrails else ""

async def chat_with_external_bot(data: UserSecureChat, session, background_tasks: BackgroundTasks):
    try:
        fingerprint = data.fingerprint
        thread_id = data.id

        if not thread_id:
            existing_thread = await redis_store.get(f"fingerprint:{fingerprint}")
            print(f"Existing thread id is : {existing_thread}")
           
            if existing_thread:
                raise HTTPException(
                    status_code=400,
                    detail={
                        "detail": "This fingerprint already has a thread",
                        "thread_id": existing_thread
                    }
                )
            
            new_thread_id = str(uuid.uuid4())
            print(f"New thread id is : {new_thread_id}")
          
            await redis_store.set(
                f"fingerprint:{fingerprint}", 
                new_thread_id, 
                ex=envs.FINGERPRINT_DURATION_SECONDS
            )

            await redis_store.set(f"thread:{new_thread_id}:tokens", 0, ex=envs.USER_KEY_DURATION_SECONDS)
            await redis_store.set(f"thread:{new_thread_id}:requests", 0, ex=envs.USER_KEY_DURATION_SECONDS)
            
            thread_id = new_thread_id
        # Validate fingerprint-thread_id relationship
        stored_thread_id = await redis_store.get(f"fingerprint:{fingerprint}")
        if not stored_thread_id or stored_thread_id != thread_id:
            raise HTTPException(400, "Fingerprint does not match thread_id")
        
        user_token_key = f"public:{fingerprint}:{thread_id}:tokens"
        user_req_key = f"public:{fingerprint}:{thread_id}:requests"


        token_count = int(await redis_store.get(user_token_key) or 0)
        request_count = int(await redis_store.get(user_req_key) or 0)
        print(f"\nUser request count is {request_count} and user token count is {token_count}\n\n")
       

        request_ttl = await redis_store.ttl(user_req_key)

        if request_ttl == -1:
            request_ttl = 5
            print("ES: App request key has no associated expiration.")
        elif request_ttl == -2:
            request_ttl = 5
            print("ES: App request key does not exist.")
        else:
            # seconds_till_next_minute = 60 - (time.time() % 60)
            print(f"ES: App request key will expire in {request_ttl} seconds.")
        
        # Get the remaining time to live (TTL) for a key
        token_ttl = await redis_store.ttl(user_token_key)

        if token_ttl == -1:
            token_ttl = 5
            print("ES: The user token key has no associated expiration.")
        elif token_ttl == -2:
            token_ttl = 5
            print("ES: The user token key does not exist.")
        else:
            print(f"ES: The user token key will expire in {token_ttl} seconds.")
        
        # if request_count >= envs.USER_REQUESTS_PER_X_SECONDS:
        #     raise HTTPException(status_code=429, detail=f"Thread message limit exceeded. Please try again after {request_ttl} seconds")
        
        # if token_count > envs.USER_TOKENS_PER_X_SECONDS:
        #     raise HTTPException(status_code=429, detail=f"Thread token limit exceeded. Please try again after {token_ttl} seconds")
        
        question = await format_user_question(user_input=data.question, images_urls=data.images_urls)
        # encrypted_bot_id = data.bot_id
        # data.bot_id = _decrypt_chatbot_id(data.bot_id, fernet = fernet)
        data.bot_id=40
        print(f'bot id being extracted = {data.bot_id}')
        # bot_config = await get_chatbot_config_by_id(int(data.bot_id), session)
        # org_admin = await _get_admin(organization_id=bot_config.organization_id, session=session)
            
        
        # llm = load_llm(api_key=envs.OPENAI_API_KEY, name='gpt-4o', temperature=bot_config.llm_temperature)
        llm = load_llm(api_key=envs.OPENAI_API_KEY, name="gpt-4.1", temperature=0.1)

        # llm = load_llm(api_key=envs.OPENAI_API_KEY, name=bot_config.llm_model_name, temperature=bot_config.llm_temperature)
        
        data.id = thread_id
        chat_messages = await get_external_bot_chat_messagegs(fingerprint, thread_id, redis_store)

        # print(f"\nChat messages are: {chat_messages}\n\n")
        chat_history = []

        if chat_messages is not None:
            for msg in chat_messages:
                # msg = json.loads(msg)
                print(f'message while creating history {msg}')
                if msg["role"] == "ai":
                    message_content = msg.get("message", [{}])[0]
                    additional_kwargs = message_content.get("additional_kwargs", {})

                    if additional_kwargs:
                        chat_history.append(
                                AIMessage(
                                    content=message_content.get("text", ""),
                                    additional_kwargs=additional_kwargs,
                                    response_metadata=msg.get("response_metadata", {}),
                                    tool_calls=msg.get("tool_calls", [])
                                )
                            )
                    else:
                        print(f'else AI history')
                        chat_history.append(AIMessage(content=msg["message"][0]["text"]))

                elif  msg["role"] == "human":
                    # chat_history.append(HumanMessage(content=msg["message"][0]["text"]))
                    message_content = msg["message"][0]
                    if "image_url" in message_content:  # Handle image URLs
                        chat_history.append(HumanMessage(content=[{"type": "image_url", "image_url": message_content["image_url"]}]))

                        # chat_history.append(HumanMessage(content=[{"type": "image_url", "image_url": message_content["image_url"]["url"]}]))
                    else:  # Handle text
                        chat_history.append(HumanMessage(content=message_content.get("text", "")))
                    
                elif msg["role"] == "tool":
                    chat_history.append(ToolMessage(content=msg["message"][0]["text"], tool_call_id=msg["id"]))

        chat_history.append(HumanMessage(content=question["content"]))
        print(f'chat hisotry created')
        
        organization_id = 20

        response = None
        image_description = None
        url_to_image = None
        image_generation = False
        response = await get_ai_response(db_session=session, memory_status=False, LLM_ROLE="friendly", llm = llm, chatbot_id = int(data.bot_id),  org_id =organization_id, chat_history = chat_history, scaffolding_level ="") #  21 10 current_user.id  current_user.organization_id
        response_content = response["answer"]
        response = {"role": "assistant", "message": f"{response_content}"}

        llm_tokens = 100
       
        if not await redis_store.exists(user_token_key):
            await redis_store.set(user_token_key, llm_tokens, ex=envs.USER_KEY_DURATION_SECONDS)
        else:
            await redis_store.incrby(user_token_key, llm_tokens)


        if not await redis_store.exists(user_req_key):
            await redis_store.incr(user_req_key)
            await redis_store.expire(user_req_key, envs.USER_KEY_DURATION_SECONDS)
        else:
            await redis_store.incrby(user_req_key, 1)

    
        app_tokens_key = "app:tokens"
        if not await redis_store.exists(app_tokens_key):
            await redis_store.set(app_tokens_key, llm_tokens, ex=envs.APP_KEY_DURATION_SECONDS)
        else:
            await redis_store.incrby(app_tokens_key, llm_tokens)

        await redis_store.rpush(f"public:conversation:{fingerprint}:{thread_id}", json.dumps({"role": "human", "message": [{'type': 'text', 'text': data.question}]}))
        await redis_store.rpush(f"public:conversation:{fingerprint}:{thread_id}", json.dumps({"role": "ai", "message": [{"type": "text", "text": response['message']}]}))
        if data.images_urls:
            for url in data.images_urls:
                await redis_store.rpush(f"public:conversation:{fingerprint}:{thread_id}", json.dumps({"role": "human", "message": [{"type": "image_url", "image_url": {"url": url}}]}))

        await redis_store.expire(f"public:conversation:{fingerprint}:{thread_id}", envs.PUBLIC_TIME_TO_LIVE_IN_SECONDS)
        
        current_time = time.time()
        await redis_store.zadd("active_sessions", {str(data.id): current_time})
        if await redis_store.zcard("active_sessions") > envs.MAX_SESSIONS:
            lru_session_id = (await redis_store.zrange("active_sessions", 0, 0))[0]

            await redis_store.zrem("active_sessions", lru_session_id)

            await redis_store.delete(lru_session_id)
        
        return response, thread_id, #image_generation
    
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))



async def chat_with_bot(data: UserChat, session, current_user, background_tasks: BackgroundTasks):
    # try:
    data.question = "Please simplify your above reponse." if data.is_simplify else data.question
    verified_time_limit = current_user.verified_at + timedelta(days=envs.FREE_TRAIL_DAYS)

    if current_user.role == UserRole.ADMIN:            
        # Get the chatbot first
        query = select(ChatbotConfig).filter(
            ChatbotConfig.id == data.bot_id,
            ChatbotConfig.organization_id == current_user.organization_id
        )
        
        result = await session.execute(query)
        chatbot = result.scalar_one_or_none()
        # Verify user belongs to organization
        if not chatbot:
            raise HTTPException(status_code=404, detail="Chatbot not found")
        
        if (current_user.current_plan in [Plan.starter, Plan.enterprise] and "image_generation" not in (current_user.add_on_features or []) ) or (current_user.current_plan == Plan.free and datetime.now(timezone.utc) > verified_time_limit):
            if data.generate_image:
                raise HTTPException(status_code=427, detail="Please upgrade your plan to generate image.")
        
    elif current_user.role == UserRole.USER:
        query = select(ChatbotConfig).filter(
            ChatbotConfig.id == data.bot_id,
            ChatbotConfig.organization_id == current_user.organization_id
        )
        result = await session.execute(query)
        chatbot = result.scalar_one_or_none()
        # Verify user belongs to organization
        if not chatbot:
            raise HTTPException(status_code=404, detail="Chatbot not found")
        
        
        from app.utils.database_helper import get_rbac_groups_by_id
        chatbot_ids = []
        for group_id in current_user.group_ids:
            #Extract gropu information and extract ids from groups.
            gropu_details = await get_rbac_groups_by_id(session, current_user.organization_id, group_id)
            
            for bot_info in gropu_details.attributes:
                chatbot_ids.append(bot_info["chatbot_id"])
        if data.bot_id not in chatbot_ids:
            raise HTTPException(status_code=404, detail="Unautherized operation")

        from app.utils.db_helpers import get_user_organization_admin
        # admin = get_user_organization_admin(session, current_user.organization_id)
        admin = await _get_admin(organization_id=current_user.organization_id, session=session)
            
        if (admin.current_plan in [Plan.starter]) or (admin.current_plan == Plan.free and datetime.now(timezone.utc) > verified_time_limit):
            if data.generate_image:
                raise HTTPException(status_code=427, detail="Please ask your organization to upgrade plan for generate image.")

        if admin.current_plan in [Plan.enterprise]:
            if data.generate_image:
                if admin.add_on_features:
                    if "image_generation" not in admin.add_on_features:
                        raise HTTPException(status_code=427, detail="Please ask your organization to upgrade plan to generate image.")
                else:
                    raise HTTPException(status_code=427, detail="Please ask your organization to upgrade plan to generate image.")
 
    bot_config = await _get_chatbot_config(data, session)
    org_admin = await _get_admin(organization_id=bot_config.organization_id, session=session)
    print(f'::bot_config.monthly_messages_count:: {bot_config.monthly_messages_count}')
    if org_admin.current_plan == Plan.free and org_admin.is_paid:
        if bot_config.monthly_messages_count and bot_config.monthly_messages_count >= envs.STARTER_PLAN_EXTERNAL_BOT_MONTHLY_MESSAGES:
            is_expired = await check_and_refresh_chat_cycle(org_admin, bot_config, session=session)
            if not is_expired:
                raise HTTPException(status_code=404, detail="You have reached your monthly message limit. Please upgrade your plan.")
            print(f'no exception')
    elif org_admin.current_plan == Plan.starter:
        if bot_config.monthly_messages_count and bot_config.monthly_messages_count >= envs.STARTER_PLAN_EXTERNAL_BOT_MONTHLY_MESSAGES:
            is_expired = await check_and_refresh_chat_cycle(org_admin, bot_config, session=session)
            if not is_expired:    
                raise HTTPException(status_code=404, detail="You have reached your monthly message limit. Please upgrade your plan.")
    elif org_admin.current_plan == Plan.enterprise:
        if bot_config.monthly_messages_count and bot_config.monthly_messages_count >= envs.ENTERPRISE_PLAN_EXTERNAL_BOT_MONTHLY_MESSAGES:
            is_expired = await check_and_refresh_chat_cycle(org_admin, bot_config, session=session)
            if not is_expired:
                raise HTTPException(status_code=404, detail="You have reached your monthly message limit.")
    elif org_admin.current_plan == Plan.free:
        if bot_config.monthly_messages_count and bot_config.monthly_messages_count >= envs.FREE_PLAN_EXTERNAL_BOT_MONTHLY_MESSAGES:
            is_expired = await check_and_refresh_chat_cycle(org_admin, bot_config, session=session)
            if not is_expired:
                raise HTTPException(status_code=404, detail="You have reached your monthly message limit. Please upgrade your plan.")
        
    question = await format_user_question(user_input=data.question, images_urls=data.images_urls)
    
    if current_user.role == UserRole.ADMIN:
        if current_user.current_plan == Plan.free and datetime.now(timezone.utc) > verified_time_limit and bot_config.admin_per_days_messages_count >= envs.ADMIN_MESSAGES_WITH_EXTERNAL_PER_DAY_FREMIUM:
            raise HTTPException(status_code=404, detail="Limit reached for chating with bot. Please upgrade your plans.")
    if current_user.role == UserRole.SUPER_ADMIN:
        from app.utils.db_helpers import get_user_organization_admin
        admin = await _get_admin(organization_id=bot_config.organization_id, session=session)
        if (admin.current_plan in [Plan.starter]) or (admin.current_plan == Plan.free and datetime.now(timezone.utc) > verified_time_limit):
            if data.generate_image:
                raise HTTPException(status_code=427, detail="Please upgrade the organization plan to generate images.")
        
    guirdrails = await _get_guardrails(bot_config.id, session)
    if not bot_config:
        raise HTTPException(status_code=404, detail="Chatbot not found")
    
    llm_name  = await get_organization_chabot_name( db = session)
    llm = load_llm(api_key=envs.OPENAI_API_KEY, name=  llm_name if llm_name else "gpt-4.1", temperature=bot_config.llm_temperature) #"gpt-5"
    chat_history = []
    thread_id = data.id
    if not thread_id:
        print(f'create new threading')
        # firs message so create thread for chatting.
        create_thread = Threads(
            user_id = current_user.id,
            chatbot_id = data.bot_id,
            title = 'New Chat',
            title_manual_update = True,
        )

        session.add(create_thread)
        await session.commit()
        await session.refresh(create_thread)
        thread_id = create_thread.thread_id
        
    else:
        chat_messages = await get_chat_messagegs(data = data, current_user=current_user, session = session)
        for msg in chat_messages:
            print(f'message while creating history {msg}')
            if msg["role"] == "ai":
                message_content = msg.get("message", [{}])[0]
                # Extract additional_kwargs correctly
                additional_kwargs = message_content.get("additional_kwargs", {})

                if additional_kwargs:
                    chat_history.append(
                            AIMessage(
                                content=message_content.get("text", ""),
                                additional_kwargs=additional_kwargs,
                                response_metadata=msg.get("response_metadata", {}),
                                tool_calls=msg.get("tool_calls", [])
                            )
                        )
                else:
                    print(f'else AI history')
                    chat_history.append(AIMessage(content=msg["message"][0]["text"]))

            elif  msg["role"] == "human":
                chat_history.append(HumanMessage(content=msg["message"]))
                
            elif msg["role"] == "tool":
                chat_history.append(ToolMessage(content=msg["message"][0]["text"], tool_call_id=msg["id"]))
    chat_history.append(HumanMessage(content=question["content"]))
    # from app.utils.langchain_helper import test_graph
    # await test_graph(chat_history)
    
    print(f'history length -------------------------------------== {len(chat_history)}')
    if len(chat_history) % 11 == 0 and len(chat_history) != 0:
        print("chat history of the assistant.",len(chat_history))
        message_body = {
            "url":'empty', # to satisfy the condition
            "thread_id": thread_id,
            "org_id": chatbot.organization_id,
            "chatbot_id": chatbot.id,
            "content_source": "title_update"
        }
        # Send message to SQS
        response = sqs_client.send_message(
            QueueUrl=envs.SQS_QUEUE_URL,
            MessageBody=json.dumps(message_body),
            MessageGroupId=f"{str(chatbot.id)}-{str(uuid.uuid4())}-title-update",
            MessageDeduplicationId=str(uuid.uuid4())
        )
        print(f"Sent to SQS: {response['MessageId']}")
        
    message_uuid = f"{thread_id}_{thread_id}_{uuid.uuid4()}"
    response = None
    image_description = None
    url_to_image = None
    image_generation = False

    # Create moderation chain
    flag = True
    moderation_response = None
    if not data.is_simplify:
        moderation_llm = load_llm_in_json_mode(api_key=envs.OPENAI_API_KEY, name=llm_name if llm_name else "gpt-4.1", temperature=bot_config.llm_temperature)
        flag,moderation_response = await _create_moderation_chain(moderation_llm, guirdrails, user_input = data.question)
    print(f'flag from moderation == {flag}')
    if not flag:
        response = {"role": "assistant", "message": f"{moderation_response}"}
        print(f'moderation response {response}')
    else:
        if data.generate_image:
        # Create relevance check chain
            response = await get_ai_response(db_session=session, memory_status=bot_config.memory_status, LLM_ROLE=bot_config.llm_role, llm = llm, chatbot_id = data.bot_id, org_id =bot_config.organization_id, enable_image_generation=True, chatbot_type=bot_config.specialized_type, chat_history=chat_history,thread_id=thread_id, is_simplify = data.is_simplify,  scaffolding_level =bot_config.scaffolding_level)
            print(f'response from invorke == {response}')
            if response.tool_calls:
                background_tasks.add_task(langchain_helper._add_message_database,
                        thread_id=thread_id,
                        message_uuid = f"{thread_id}_{thread_id}_{uuid.uuid4()}",
                        role='User',
                        message= json.dumps(question["content"]),
                        is_image=True,
                        images_urls = data.images_urls,
                        db_session = session,
                        organization_id = current_user.organization_id,
                        is_simplify_on = data.is_simplify
                    )
                
                tool_call = response.tool_calls[0]
                ###############
                await redis_store.rpush(f"private_{current_user.id}_{thread_id}", json.dumps({"role": "human", "message": question["content"]}))
                await redis_store.rpush(f"private_{current_user.id}_{thread_id}", json.dumps({"role":"ai","message":[{"type": "text", "text": '',"additional_kwargs":response.additional_kwargs,"response_metadata":response.response_metadata,"id":response.id,"tool_calls":response.tool_calls}]}))
                chat_history.append(response)

                if tool_call["name"] == "generate_image":
                    filtered_response = response.dict()  # Convert response object to a dictionary
                    filtered_response.pop("content", None)
                    message_uuid = f"{thread_id}_{thread_id}_{uuid.uuid4()}"
                    background_tasks.add_task(langchain_helper._add_message_database,
                        thread_id=thread_id,
                        message_uuid = message_uuid,
                        role ='Tool',
                        message = json.dumps(filtered_response),
                        is_image=False,
                        images_urls=[str(tool_call["id"])],
                        db_session = session,
                        organization_id = current_user.organization_id,
                        is_simplify_on = data.is_simplify
                    )
                    message_body = {
                            "image_description": tool_call["args"]["image_prompt"],
                            "tool_call_id": tool_call["id"],
                            "chatbot_id": bot_config.id,
                            "user_id": current_user.id,
                            "thread_id": thread_id,
                            "user_input":data.question,
                            "message_id": message_uuid
                        }

                    # Send message to SQS
                    s3_response = sqs_client.send_message(
                            QueueUrl=envs.GENERATE_IMAGE_SQS_URL,
                            MessageBody=json.dumps(message_body),
                            MessageGroupId=f"{str(uuid.uuid4())}-generate_img",
                            MessageDeduplicationId=str(uuid.uuid4())
                        )
                    print(f"Sent to SQS: {s3_response}")
            
                    image_generation = True
                    result = await session.execute(select(Threads).filter(Threads.thread_id == thread_id))
                    thread = result.scalars().first()
                    # background_tasks.add_task(
                    #         increment_chatbot_per_day_message_count,
                    #         chatbot_id = bot_config.id,
                    #         session = session
                    # )
                    background_tasks.add_task(
                            increment_chatbot_message_count,
                            chatbot_id = bot_config.id,
                            session = session
                    )
                    if current_user.role == UserRole.ADMIN and bot_config.chatbot_type == "External":
                        background_tasks.add_task(
                                increment_admin_chatbot_message_count,
                                chatbot_id = bot_config.id,
                                session = session
                        )   
                    return response, url_to_image, thread_id, image_generation, thread.created_timestamp, thread.updated_timestamp, message_uuid, False if data.id else True

            else:
                json_object = json.loads(response)
                response = {"role": "assistant", "message": f"{json_object['answer']}"}
                print(f"Assistant: {response}")
        else:
                # Regular chatbot flow
                response = await get_ai_response(
                    db_session=session, 
                    memory_status=bot_config.memory_status, 
                    LLM_ROLE=bot_config.llm_role, 
                    llm = llm, 
                    chatbot_id = data.bot_id, 
                    org_id = bot_config.organization_id,
                    chatbot_type=bot_config.specialized_type, 
                    chat_history=chat_history,thread_id=thread_id, 
                    is_simplify = data.is_simplify,  
                    scaffolding_level =bot_config.scaffolding_level) #  21 10  
                print(f'response from else ==  {response}')
                response = {"role": "assistant", "message": f"{response['answer']}"}
            
        print(f'response from openai {response}')
        
    
    await redis_store.rpush(f"private_{current_user.id}_{thread_id}", json.dumps({"role": "human", "message": question["content"]}))
    # await redis_store.rpush(str(thread_id), json.dumps({"role": "ai", "message": response}))
    # await redis_store.rpush(str(thread_id), json.dumps(response))
    await redis_store.rpush(f"private_{current_user.id}_{thread_id}", json.dumps({"role":"ai","message":[{"type": "text", "text": response['message']}]}))
    await redis_store.expire(f"private_{current_user.id}_{thread_id}", envs.TIME_TO_LIVE_IN_SECONDS)

    current_time = time.time()
    await redis_store.zadd("active_sessions", {f"private_{current_user.id}_{thread_id}": current_time})
    if await redis_store.zcard("active_sessions") > envs.MAX_SESSIONS:
        lru_session_id_list = await redis_store.zrange("active_sessions", 0, 0)
        if lru_session_id_list:
            await redis_store.zrem("active_sessions", lru_session_id_list[0])
            await redis_store.delete(lru_session_id_list[0])
   
    if data.images_urls:
        message_uuid = f"{thread_id}_{thread_id}_{uuid.uuid4()}"
        background_tasks.add_task(langchain_helper._add_message_database,
            thread_id=thread_id,
            message_uuid = message_uuid,
            role='User',
            message = json.dumps(question["content"]),
            is_image=True,
            images_urls=data.images_urls,
            db_session = session,
            organization_id = current_user.organization_id,
            is_simplify_on = data.is_simplify
        )
    else:
        message_uuid = f"{thread_id}_{thread_id}_{uuid.uuid4()}"
        background_tasks.add_task(langchain_helper._add_message_database,
                                thread_id = thread_id, 
                                message_uuid = message_uuid,
                                role = 'User', 
                                message = json.dumps(question["content"]), 
                                db_session = session,
                                organization_id = current_user.organization_id,
                                is_simplify_on = data.is_simplify) 
    
    message_uuid = f"{thread_id}_{thread_id}_{uuid.uuid4()}"
    background_tasks.add_task(langchain_helper._add_message_database,
                                thread_id = thread_id, 
                                message_uuid = message_uuid,
                                role = 'Assistant', 
                                message = response['message'], 
                                db_session = session,
                                organization_id = current_user.organization_id,
                                is_simplify_on = data.is_simplify)    
    result = await session.execute(select(Threads).filter(Threads.thread_id == thread_id))
    thread = result.scalars().first()

    background_tasks.add_task(
            increment_chatbot_message_count,
            chatbot_id = bot_config.id,
            session = session
    )
    background_tasks.add_task(
            increment_chatbot_monthly_message_count,
            chatbot = bot_config,
            session = session
        )
    if current_user.role == UserRole.ADMIN and bot_config.chatbot_type == "External":
        background_tasks.add_task(
                increment_admin_chatbot_message_count,
                chatbot_id = bot_config.id,
                session = session
        )  
    return response, url_to_image, thread_id, image_generation, thread.created_timestamp, thread.updated_timestamp, message_uuid, False if data.id else True
    

async def stream_chat_with_bot(
    bot_id: int,
    data: UserChat,
    session: AsyncSession,
    user_id: str
) -> AsyncGenerator[Dict[str, Any], None]:
    """
    Stream chat responses while saving messages to database.
    Emits dict chunks for SSE encoding in the route.

    Events shape:
      - connected: { new_chat, is_simplify, url_to_image, thread_id, message_id, created_at }
      - generate:  { content, image_generation_flag, thread_id, message_id }
      - finish:    { content, image_generation_flag, thread_id, message_id }
    """
    # Resolve current user
    current_user = await _get_user(user_id, session)
    if not current_user:
        raise HTTPException(status_code=404, detail="User not found")

    # Copy payload to avoid external mutation and normalize fields
    payload = UserChat(**data.dict())
    payload.bot_id = bot_id
    payload.question = "Please simplify your above reponse." if payload.is_simplify else payload.question

    # Load bot config and LLM
    bot_config = await get_chatbot_config_by_id(int(payload.bot_id), session)
    if not bot_config:
        raise HTTPException(status_code=404, detail="Chatbot not found")

    llm_name = await get_organization_chabot_name(db=session)
    llm = load_llm(api_key=envs.OPENAI_API_KEY, name=llm_name if llm_name else "gpt-4.1", temperature=bot_config.llm_temperature)

    # Build chat history and thread
    chat_history = []
    thread_id = payload.id
    new_chat = False
    if not thread_id:
        create_thread = Threads(
            user_id=current_user.id,
            chatbot_id=payload.bot_id,
            title='New Chat',
            title_manual_update=True,
        )
        session.add(create_thread)
        await session.commit()
        await session.refresh(create_thread)
        thread_id = create_thread.thread_id
        new_chat = True
    else:
        # Load recent history from redis/db
        past_messages = await get_chat_messagegs(data=payload, current_user=current_user, session=session)
        for msg in past_messages:
            if msg["role"] == "ai":
                message_content = msg.get("message", [{}])[0]
                additional_kwargs = message_content.get("additional_kwargs", {})
                if additional_kwargs:
                    chat_history.append(
                        AIMessage(
                            content=message_content.get("text", ""),
                            additional_kwargs=additional_kwargs,
                            response_metadata=msg.get("response_metadata", {}),
                            tool_calls=msg.get("tool_calls", [])
                        )
                    )
                else:
                    chat_history.append(AIMessage(content=msg["message"][0]["text"]))
            elif msg["role"] == "human":
                chat_history.append(HumanMessage(content=msg["message"]))
            elif msg["role"] == "tool":
                chat_history.append(ToolMessage(content=msg["message"][0]["text"], tool_call_id=msg["id"]))

    # Format current user input
    formatted = await format_user_question(user_input=payload.question, images_urls=payload.images_urls)
    chat_history.append(HumanMessage(content=formatted["content"]))

    # Initial event
    message_uuid = f"{thread_id}_{thread_id}_{uuid.uuid4()}"
    url_to_image = None
    image_generation = False
    yield {
        "type": "connected",
        "message": "Connected to chat stream",
        "thread_id": thread_id,
        "message_id": message_uuid,
        "created_at": str(datetime.utcnow()),
        "new_chat": new_chat,
        "is_simplify": payload.is_simplify,
        "url_to_image": url_to_image,
    }

    # Persist user message immediately
    await redis_store.rpush(
        f"private_{current_user.id}_{thread_id}",
        json.dumps({"role": "human", "message": formatted["content"]})
    )
    await redis_store.expire(f"private_{current_user.id}_{thread_id}", envs.TIME_TO_LIVE_IN_SECONDS)

    if payload.images_urls:
        await langchain_helper._add_message_database(
            thread_id=thread_id,
            message_uuid=f"{thread_id}_{thread_id}_{uuid.uuid4()}",
            role='User',
            message=json.dumps(formatted["content"]),
            is_image=True,
            images_urls=payload.images_urls,
            db_session=session,
            organization_id=current_user.organization_id,
            is_simplify_on=payload.is_simplify
        )
    else:
        await langchain_helper._add_message_database(
            thread_id=thread_id,
            message_uuid=f"{thread_id}_{thread_id}_{uuid.uuid4()}",
            role='User',
            message=json.dumps(formatted["content"]),
            db_session=session,
            organization_id=current_user.organization_id,
            is_simplify_on=payload.is_simplify
        )
    ai_response_content = ''
    try:
        # Generate assistant response
        if payload.generate_image:
            async for chunk in get_stream_ai_response(
                db_session=session,
                memory_status=bot_config.memory_status,
                LLM_ROLE=bot_config.llm_role,
                llm=llm,
                chatbot_id=payload.bot_id,
                org_id=bot_config.organization_id,
                enable_image_generation=True,
                chatbot_type=bot_config.specialized_type,
                chat_history=chat_history,
                thread_id=thread_id,
                is_simplify=payload.is_simplify,
                scaffolding_level=bot_config.scaffolding_level
            ):
                yield chunk
                # Collect only actual AI content for database (exclude status messages)
                if chunk.get("type") == "content":
                    ai_response_content += chunk.get("content")

            # If tool call present, mark image generation and persist Tool message
            if chunk.get("type") == "tool_start" and chunk.get("tool_calls") == "image":
                await redis_store.rpush(
                    f"private_{current_user.id}_{thread_id}",
                    json.dumps({"role":"ai","message":[{"type": "text", "text": '',"additional_kwargs":chunk.get("additional_kwargs"),"response_metadata":chunk.get("response_metadata"),"id":chunk.get("id"),"tool_calls":chunk.get("tool_calls")}]})
                )
                image_generation = True
                tool_call = chunk.get("tool_calls")[0]
                filtered_response = chunk.get("tool_calls")[0]
                filtered_response.pop("content", None)
                await langchain_helper._add_message_database(
                    thread_id=thread_id,
                    message_uuid=message_uuid,
                    role='Tool',
                    message=json.dumps(filtered_response),
                    is_image=False,
                    images_urls=[str(tool_call["id"])],
                    db_session=session,
                    organization_id=current_user.organization_id,
                    is_simplify_on=payload.is_simplify
                )
                # Let the client know image generation started
                yield {"type": "generate", "content": '', "image_generation_flag": True, "thread_id": thread_id, "message_id": message_uuid}
                full_text = ''
            else:
                # Fallback textual content
                json_object = json.loads(response)
                full_text = f"{json_object['answer']}"
        else:
            response = await get_ai_response(
                db_session=session,
                memory_status=bot_config.memory_status,
                LLM_ROLE=bot_config.llm_role,
                llm=llm,
                chatbot_id=payload.bot_id,
                org_id=bot_config.organization_id,
                chatbot_type=bot_config.specialized_type,
                chat_history=chat_history,
                thread_id=thread_id,
                is_simplify=payload.is_simplify,
                scaffolding_level=bot_config.scaffolding_level
            )
            if isinstance(response, dict) and 'answer' in response:
                full_text = f"{response['answer']}"
            else:
                try:
                    full_text = str(response["answer"])  # type: ignore
                except Exception:
                    full_text = str(response)

        # Stream textual content in chunks
        if not image_generation:
            chunk_size = 120
            for i in range(0, len(full_text), chunk_size):
                part = full_text[i:i+chunk_size]
                yield {"type": "generate", "content": part, "image_generation_flag": False, "thread_id": thread_id, "message_id": message_uuid}

            # Save final assistant message
            await redis_store.rpush(
                f"private_{current_user.id}_{thread_id}",
                json.dumps({"role":"ai","message":[{"type": "text", "text": full_text}]})
            )
            await redis_store.expire(f"private_{current_user.id}_{thread_id}", envs.TIME_TO_LIVE_IN_SECONDS)
            await langchain_helper._add_message_database(
                thread_id=thread_id,
                message_uuid=f"{thread_id}_{thread_id}_{uuid.uuid4()}",
                role='Assistant',
                message=full_text,
                db_session=session,
                organization_id=current_user.organization_id,
                is_simplify_on=payload.is_simplify
            )

        # Counters
        if current_user.role == UserRole.ADMIN and bot_config.chatbot_type == "External":
            await increment_admin_chatbot_message_count(chatbot_id=bot_config.id, session=session)
        await increment_chatbot_message_count(chatbot_id=bot_config.id, session=session)
        await increment_chatbot_monthly_message_count(chatbot=bot_config, session=session)

        # Finish event
        yield {"type": "finish", "content": '' if image_generation else full_text, "image_generation_flag": image_generation, "thread_id": thread_id, "message_id": message_uuid}

    except Exception as e:
        logger.error(f"Error in stream_chat_with_bot for thread {thread_id}: {e}")
        yield {"error": f"Error: {str(e)}", "type": "error"}


from sqlalchemy import func
import math

from app.schemas.response.user_chat import  GetMessagesResponseInternal
async def get_route_chat_messagegs(data, current_user, session, offset: int, limit: int):
    try:
        print(f"Current user ID: {current_user.id}, Thread ID: {data.id}")
        user_thread = select(Threads).filter(
                Threads.thread_id == data.id,
                Threads.user_id == current_user.id
            )
            
        result = await session.execute(user_thread)
        thread_exist = result.scalar_one_or_none()
        # Verify user belongs to organization
        if not thread_exist:
            print(f"Thread {data.id} does not exist for user {current_user.id}. Raising 404 e")
            raise HTTPException(status_code=404, detail="The thread does exist for you.")
        if offset == 0: # On very first request to get message api send the latest messages.
            total_messages_query = await session.execute(
                select(func.count()).filter(Messages.thread_id == data.id)
            )
            total_messages = total_messages_query.scalar()
            if not total_messages:
                return GetMessagesResponseInternal(
                    id = data.id,
                    image_generation = False,
                    chat_messages=[],
                    offset = -1
                )            
            skip = total_messages - limit
            
        else:
            # skip = (page - 1) * page_size
            skip = offset - limit
            limit = offset
        if skip < 0:
            skip = 0
        
        results = await langchain_helper._load_message_history(thread_id = data.id, db_session = session, skip = skip, limit=limit, internal = True)
        return results
    except Exception as e:
        print(f"An unexpected error occurred while fetching messages: {e}")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='Error while feching data.')
    

async def admin_get_route_chat_messagegs(data, current_user, session,  limit, skip):
    try:
        user = await _get_user(user_id= data.user_id, session = session)
        if not user:
            raise HTTPException(status_code=404, detail="The owner of the chat has been deleted and all its data has been cleaned.")

        if user.organization_id != current_user.organization_id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="You can only see the threads ids of your organization users.")
        
        print(f"Current user ID: {current_user.id}, Thread ID: {data.thread_id}")
        user_thread = select(Threads).filter(
                Threads.thread_id == data.thread_id,
                Threads.user_id == data.user_id,
            )
        
        result = await session.execute(user_thread)
        thread_exist = result.scalar_one_or_none()
        # Verify user belongs to organization
        if not thread_exist:
            print(f"Thread {data.thread_id} does not exist for user {current_user.id}. Raising 404 e")
            raise HTTPException(status_code=404, detail="The thread does exist for you.")
        
        # checks on user to view chat messages.
        if current_user.role not in [UserRole.ADMIN, UserRole.SUPER_ADMIN]:
            user_groups, _ = await format_user_chatbot_permissions(session, user.organization_id, user.group_ids)
            
            #put those chatbot which are allowed for user to view its chat logs
            chatbot_ids = []
            for bot_info in user_groups:
                if bot_info['can_view_chat_logs'] and bot_info['chatbot_id'] not in chatbot_ids:
                    chatbot_ids.append(bot_info['chatbot_id'])
            if data.chatbot_id not in chatbot_ids:
                print(f'inside user exception the chatbot ids not mached')
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='Unautherized.')
            
        results = await langchain_helper._load_message_history(thread_id =  data.thread_id, db_session = session,limit = limit, skip = skip)
        return results
    except Exception as e:
        print(f"An unexpected error occurred while fetching messages: {e}")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='Error while feching data.')



async def get_chat_messagegs(data, current_user, session):
    try:
        print("Starting get_chat_messages...")
        print(f"Current user ID: {current_user.id}, Thread ID: {data.id}")
        user_thread = select(Threads).filter(
                Threads.thread_id == data.id,
                Threads.user_id == current_user.id
            )
            
        result = await session.execute(user_thread)
        thread_exist = result.scalar_one_or_none()
        # Verify user belongs to organization
        if not thread_exist:
            print(f"Thread {data.id} does not exist for user {current_user.id}. Raising 404 e")
            raise HTTPException(status_code=404, detail="The thread does exist for you.")

        print(f"User {current_user.id} verified for thread {data.id}.")
        # await redis_store.ltrim(str(data.id), -20, -1)
        messages = await redis_store.lrange(f"private_{current_user.id}_{str(data.id)}", 0, -1)
        print(f"Messages fetched from Redis: {messages}")
        if not messages:
            print(f"No messages found in Redis for thread {data.id}. Loading from database...")
            last_10_messages = await langchain_helper._load_last_10_messages(thread_id=data.id, db_session = session)
            for message in last_10_messages:
                redis_message = {"role": "human"}
                
                if isinstance(message, AIMessage):
                    redis_message['role'] = 'ai'
                    
                    if message.content:
                        redis_message["message"] = [{"type": "text", "text": message.content}]
                    else:
                        print(f'AI tool message == {message}')
                        redis_message["message"] = [{"type": "text", "text": message.content,"additional_kwargs":message.additional_kwargs,"response_metadata":message.response_metadata,"id":message.id,"tool_calls":message.tool_calls}]
                
                if isinstance(message, ToolMessage):
                    redis_message['role'] = 'tool'
                    redis_message["message"] = [{"type": "text", "text": message.content}]
                    redis_message["id"] = message.tool_call_id
                if isinstance(message, HumanMessage):
                    redis_message["message"] = message.content # [{"type": "text", "text": message.content}]
                
                # if isinstance(message.content, list):  
                #     # If the message contains text + image, store both
                #     redis_message["message"] = message.content  
                # else:
                #     # Otherwise, store just text
                #     redis_message["message"] = [{"type": "text", "text": message.content}]

                # Store message in Redis
                await redis_store.rpush(f"private_{current_user.id}_{str(data.id)}", json.dumps(redis_message))
                print(f"Added {redis_message['role']} message to Redis: {redis_message}")

                #await redis_store.ltrim(str(data.id), -10, -1)
                messages = await redis_store.lrange(f"private_{current_user.id}_{str(data.id)}", 0, -1)

            await redis_store.expire(f"private_{current_user.id}_{str(data.id)}", envs.TIME_TO_LIVE_IN_SECONDS)
            
            current_time = time.time()  
            await redis_store.zadd("active_sessions", {f"private_{current_user.id}_{str(data.id)}": current_time})
        total_messages = await redis_store.llen(f"private_{current_user.id}_{str(data.id)}")
        if total_messages >= 20:
            print(f"Total messages exceeded 20, trimming last 4 messages.")
            await redis_store.ltrim(f"private_{current_user.id}_{str(data.id)}", 0, -5)
        return [json.loads(msg) for msg in messages]
    except Exception as e:
        print(f"An unexpected error occurred while fetching messages: {e}")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='Error while feching data.')
    
from sqlalchemy import  desc
async def get_all_sessions(chatbot_id, user, session, limit, skip,thread_name = None):
    total_user_thread = select(Threads).filter(
                Threads.chatbot_id == chatbot_id,
                Threads.user_id == user.id
            )
    # If thread_name is provided, add search filter
    if thread_name:
        total_user_thread = total_user_thread.filter(Threads.title.ilike(f"%{thread_name}%"))

    total_result = await session.execute(total_user_thread)
    total_count = len(total_result.scalars().all())
    
    user_thread = select(Threads).filter(
        Threads.chatbot_id == chatbot_id,
        Threads.user_id == user.id
        ) #.order_by(desc(Threads.updated_timestamp)).offset(skip).limit(limit)
    # If thread_name is provided, add search filter
    if thread_name:
        user_thread = user_thread.filter(Threads.title.ilike(f"%{thread_name}%"))
    user_thread = user_thread.order_by(desc(Threads.updated_timestamp)).offset(skip).limit(limit)

    result = await session.execute(user_thread)
    final_data = result.scalars().all()
    # if skip == 0 and limit == 0:
    #     final_data.reverse()
    return final_data, total_count


async def get_all_sessions_with_search(chatbot_id, user, session, thread_name=None, limit=20, skip=0):
    # Base query filtering by chatbot_id and user_id
    query = select(Threads).filter(
        Threads.chatbot_id == chatbot_id,
        Threads.user_id == user.id
    )

    # If thread_name is provided, add search filter
    if thread_name:
        query = query.filter(Threads.title.ilike(f"%{thread_name}%"))

    # Order by updated_timestamp, then apply pagination
    query = query.order_by(desc(Threads.updated_timestamp)).offset(skip).limit(limit)

    result = await session.execute(query)
    final_data = result.scalars().all()

    # Get total count of threads (including those matching search)
    total_query = select(Threads).filter(
        Threads.chatbot_id == chatbot_id,
        Threads.user_id == user.id
    )

    if thread_name:
        total_query = total_query.filter(Threads.title.ilike(f"%{thread_name}%"))

    total_result = await session.execute(total_query)
    total_count = len(total_result.scalars().all())

    return final_data, total_count

from typing import List

async def get_all_org_messages_count(user, db, chatbot_ids):

    from app.services.organization import _fine_grain_role_checks
    if user.role == UserRole.USER:
        if not user.group_ids:
            raise HTTPException(status_code=404, detail="You don't have permissions.")
      
        user_groups, _ = await format_user_chatbot_permissions(db, user.organization_id, user.group_ids)
        user_chatbot_ids = []
        #put those chatbot which are allowed for user to view its feedback
        bot_details = None
        for bot_info in user_groups:
            if  bot_info['can_view_insight'] and bot_info['chatbot_id'] not in user_chatbot_ids:
                user_chatbot_ids.append(bot_info['chatbot_id'])
                
        print(f'bot info == {user_chatbot_ids}')
        if not user_chatbot_ids:
            raise HTTPException(status_code=404, detail="No chatbot feedback allowed to view.")
        
    else:
        await _fine_grain_role_checks(user, user.organization_id)
                 
    # Get all chatbot IDs of the user's organization
    from app.models.chatbot_model import Messages
    chatbot_query = await db.execute(
        select(
            ChatbotConfig.id,
            ChatbotConfig.chatbot_name,
            ChatbotConfig.chatbot_type,
            ChatbotConfig.total_chatbot_messages_count,
            ChatbotConfig.public_last_7_days_messages
        ).where(ChatbotConfig.organization_id == user.organization_id)
    )
    if user.role == UserRole.USER:
        chatbot_query = await db.execute(
            select(
                ChatbotConfig.id,
                ChatbotConfig.chatbot_name,
                ChatbotConfig.chatbot_type,
                ChatbotConfig.total_chatbot_messages_count,
                ChatbotConfig.public_last_7_days_messages
            ).where(
                ChatbotConfig.id.in_(user_chatbot_ids),
                ChatbotConfig.organization_id == user.organization_id
            )
        )
    
    chatbot_data = {}
    for row in chatbot_query.fetchall():
        cid = row[0]
        ctype = row[2]
        if ctype == 'External':
            # For external chatbots, sum up the JSON field (ensure it’s a dict)
            json_data = row[4] or {}
            if isinstance(json_data, str):
                json_data = json.loads(json_data)
            message_count = sum(json_data.values())
        else:
            # For internal chatbots, use the stored count (or default to 0)
            message_count = row[3] or 0
        chatbot_data[cid] = {
            "name": row[1],
            "type": ctype,
            "messages": 0,
            # Store the raw JSON data for external chatbots (if needed later)
            "json_data": row[4] if row[4] is not None else {}
        }
    if not chatbot_data:
        return {"total_messages": 0, "chatbot_messages": [], "daily_totals": []}

    org_chatbot_ids = set(chatbot_data.keys())
    # If filters are provided, restrict to those that belong to the organization.
    if chatbot_ids:
        chatbot_ids = set(chatbot_ids).intersection(org_chatbot_ids)
    else:
        chatbot_ids = org_chatbot_ids

    internal_chatbot_ids = [cid for cid in chatbot_ids if chatbot_data[cid]['type'] != 'External']

    thread_ids = []
    if internal_chatbot_ids:
        thread_query = await db.execute(
            select(Threads.thread_id)
            .where(Threads.chatbot_id.in_(internal_chatbot_ids))
        )
        thread_ids = [row[0] for row in thread_query.fetchall()]
    print(f'thread ids == {thread_ids}')
    # Build daily totals over the past 7 days.
    now = datetime.utcnow()
    daily_totals = []
    # Loop for the last 7 days (most recent day first)
    for i in range(8):
        # Define the daily window.
        start_date = (now - timedelta(days=i)).replace(hour=0, minute=0, second=0, microsecond=0)
        end_date = (now - timedelta(days=i)).replace(hour=23, minute=59, second=59, microsecond=999999)

        # start_date = now - timedelta(days=i)
        # end_date = now if i == 0 else now - timedelta(days=i - 1)
        if i == 0:
            end_date = now.replace(hour=23, minute=59, second=59, microsecond=999999)  # Capture entire day
            print(f'todya == {end_date}')

        #end_date = now - timedelta(days=i)
        # Format dates in ISO 8601 with Z (UTC) suffix.
        print(f'today start date = {start_date} today end date = {end_date}')
        start_str = start_date.strftime("%Y-%m-%dT%H:%M:%SZ")
        end_str = end_date.strftime("%Y-%m-%dT%H:%M:%SZ")
        
        # Internal count: count messages in Messages table if thread IDs exist.
        internal_count = 0
        if thread_ids:
            if i == 0:  # Today
                start_of_day = (now - timedelta(days=i)).replace(hour=0, minute=0, second=0, microsecond=0)
                end_of_day = now.replace(hour=23, minute=59, second=59, microsecond=999999)
            else:
                start_of_day = start_date.replace(hour=0, minute=0, second=0, microsecond=0)
                end_of_day = end_date.replace(hour=23, minute=59, second=59, microsecond=999999)
            daily_query = await db.execute(
                select(func.count())
                .where(Messages.thread_id.in_(thread_ids))
                .where(Messages.created_timestamp >= start_of_day)
                .where(Messages.created_timestamp < end_of_day)
            )
            internal_count = daily_query.scalar() or 0
            chatbot_data[cid]["messages"] += internal_count
        # External count: sum up values from each external chatbot's JSON for the date key.
        # Our increment function stores keys as date strings matching date.today().isoformat().
        # Here we use the end_date's date (i.e. the day the messages were recorded).
        date_key = end_date.date().isoformat()
        external_count = 0
        for cid in chatbot_ids:
            if chatbot_data[cid]['type'] == 'External':
                ext_data = chatbot_data[cid]['json_data']
                # If stored as a string, convert to dict.
                if isinstance(ext_data, str):
                    ext_data = json.loads(ext_data)
                external_count += int(ext_data.get(date_key, 0))
                chatbot_data[cid]["messages"] += external_count
        print(f'total internal == {internal_count} external == {external_count}')
        total_day = internal_count + external_count
        print(f'total == {total_day}')
        daily_totals.append({
            "start_date": start_str,
            "end_date": end_str,
            "total_messages": total_day
        })
        
    print(f'daily_totals = {daily_totals}')
    # Build the per-chatbot message data.
    chatbot_messages = [
        {
            "chatbot_id": cid,
            "chatbot_name": chatbot_data[cid]["name"],
            "message_count": chatbot_data[cid]["messages"]
        }
        for cid in chatbot_ids
    ]

    total_messages = sum(chatbot_data[cid]["messages"] for cid in chatbot_ids)

    return {
        "total_messages": total_messages,
        "chatbot_messages": chatbot_messages,
        "daily_totals": daily_totals  
    }

    # chatbot_query = await db.execute(
    #     select(ChatbotConfig.id, ChatbotConfig.chatbot_name, ChatbotConfig.total_chatbot_messages_count)
    #     .where(ChatbotConfig.organization_id == user.organization_id)
    # )
    # chatbot_data = {row[0]: {"name": row[1], "messages": row[2]} for row in chatbot_query.fetchall()}
    # if not chatbot_data:
    #     return {"total_messages": 0, "chatbot_messages": [], "daily_totals": []}
    # org_chatbot_ids = set(chatbot_data.keys())
    # # If chatbot_ids is provided, filter only those in the organization
    # if chatbot_ids:
    #     chatbot_ids = set(chatbot_ids).intersection(org_chatbot_ids)
    # else:
    #     chatbot_ids = org_chatbot_ids  # Return all if no filter
    
    # # Step 1: Get all Thread IDs for the chatbots
    # thread_query = await db.execute(
    #     select(Threads.thread_id)
    #     .where(Threads.chatbot_id.in_(chatbot_ids))
    # )
    # thread_ids = [row[0] for row in thread_query.fetchall()]

    # if not thread_ids:
    #     chatbot_messages = [
    #             {
    #                 "chatbot_id": chatbot_id,
    #                 "chatbot_name": chatbot_data[chatbot_id]["name"],
    #                 "message_count": chatbot_data[chatbot_id]["messages"]
    #             }
    #             for chatbot_id in chatbot_ids
    #         ]
    #     return {
    #         "total_messages": sum(chatbot_data[chatbot_id]["messages"] for chatbot_id in chatbot_ids),
    #         "chatbot_messages": chatbot_messages,#chatbot_messages
    #         "daily_totals": []
    #     }
    # print(f'thread ids == {thread_ids}')
    # # Step 2: Calculate total weekly message counts for the past 6 weeks
    # now = datetime.utcnow()
    # daily_totals = []
    # for i in range(7):
    #     start_date = now - timedelta(days=i + 1)
    #     end_date = now - timedelta(days=i)

    #     daily_query = await db.execute(
    #         select(func.count())
    #         .where(Messages.thread_id.in_(thread_ids))
    #         .where(Messages.created_timestamp >= start_date)
    #         .where(Messages.created_timestamp < end_date)
    #     )
        
    #     total_messages = daily_query.scalar() or 0  

    #     daily_totals.append({
    #         "start_date": start_date.strftime("%Y-%m-%dT%H:%M:%SZ"),  # Format to ISO 8601
    #         "end_date": end_date.strftime("%Y-%m-%dT%H:%M:%SZ"),
    #         "total_messages": total_messages
    #     })
        
    # # Step 3: Construct chatbot message data
    # chatbot_messages = [
    #     {
    #         "chatbot_id": chatbot_id,
    #         "chatbot_name": chatbot_data[chatbot_id]["name"],
    #         "message_count": chatbot_data[chatbot_id]["messages"]
    #     }
    #     for chatbot_id in chatbot_ids
    # ]

    # total_messages = sum(chatbot_data[chatbot_id]["messages"] for chatbot_id in chatbot_ids)

    # return {
    #     "total_messages": total_messages,
    #     "chatbot_messages": chatbot_messages,
    #     "daily_totals": daily_totals  
    # }

from sqlalchemy.orm import aliased
async def get_all_org_session(user, skip, limit, db,  chatbot_ids: List[int] = None, user_ids: List[int] = None, start_date = None, end_date = None):
    if user.role == UserRole.USER:
        chatbot_ids = []
        if not user.group_ids:
            return []
        
        user_groups, _ = await format_user_chatbot_permissions(db, user.organization_id, user.group_ids)
        
        #put those chatbot which are allowed for user to view its chat logs
        for bot_info in user_groups:
            if  bot_info['can_view_chat_logs'] and bot_info['chatbot_id'] not in chatbot_ids:
                chatbot_ids.append(bot_info['chatbot_id'])

        # for group_id in user.group_ids:
        #     #Extract gropu information and extract ids from groups.
        #     # from app.utils.database_helper import get_rbac_groups_by_id
        #     # gropu_details = await get_rbac_groups_by_id(db, user.organization_id, group_id)
            
        #     # for bot_info in gropu_details.attributes:
        #     #     if bot_info["chatbot_id"] not in chatbot_ids:
        #     #         chatbot_ids.append(bot_info["chatbot_id"])
        #     print(f'gropu_details == {chatbot_ids}')
        # for ids in current_user.chatbot_ids:
        # query = (select(ChatbotConfig)
        #             .filter(
        #                 ChatbotConfig.organization_id == user.organization_id,
        #                 ChatbotConfig.id.in_(chatbot_ids)  
        #             )
        #         )
    
    # if user.role not in [UserRole.ADMIN, UserRole.SUPER_ADMIN]:
    #     raise HTTPException(status_code=404, detail="Not autherized.")
    # if not chatbot_ids:
    #     return AllThreadResponse(thread_sessions=[], total_thread_session=0)  # No valid chatbot IDs
    
    query = select(
        ChatbotConfig.id, ChatbotConfig.chatbot_name, ChatbotConfig.chatbot_type).where(
            ChatbotConfig.organization_id == user.organization_id,
            ChatbotConfig.chatbot_type == 'Internal'
    )
    
    if user.role == UserRole.USER:
        query = query.where(ChatbotConfig.id.in_(chatbot_ids))
    chatbot_query = await db.execute(query)
    chatbot_data = {row[0]: {"name": row[1], "type": row[2]} for row in chatbot_query.fetchall()}
    
    org_chatbot_ids = set(chatbot_data.keys())
    # if not chatbot_data:
    #     return AllThreadResponse(thread_sessions=[], total_thread_session=0)
    # for bot in chatbot_data:
    #     print(f"chatbot data = {bot}")
    # chatbot_ids = set(chatbot_ids).intersection(org_chatbot_ids)
    # else:
    #     chatbot_ids = org_chatbot_ids 
    print(f'org chatbot == {org_chatbot_ids} chatbotdata  entered == {chatbot_ids}')
    if chatbot_ids is None or not chatbot_ids:
        chatbot_ids = org_chatbot_ids
    else:
        chatbot_ids = set(chatbot_ids).intersection(org_chatbot_ids)

    print(f'chatbotids == {chatbot_ids}')
    if not chatbot_ids:
        return AllThreadResponse(thread_sessions=[], total_thread_session=0)
    
    
    UserAlias = aliased(User)
    total_thread_query = (
        select(func.count())
        .select_from(Threads)
        .join(UserAlias, Threads.user_id == UserAlias.id)  # Join to access user roles
        .where(Threads.chatbot_id.in_(chatbot_ids))
        .where(UserAlias.role.notin_([UserRole.ADMIN, UserRole.SUPER_ADMIN]))  # Exclude admin users
    )
    # total_thread_query = select(func.count()).select_from(Threads).where(Threads.chatbot_id.in_(chatbot_ids))
    if user_ids:
        total_thread_query = total_thread_query.where(Threads.user_id.in_(user_ids))
    if start_date:
        start_date = start_date.replace(hour=0, minute=0, second=0, microsecond=0)
        total_thread_query = total_thread_query.where(Threads.updated_timestamp >= start_date)
    if end_date:
        end_date = end_date.replace(hour=23, minute=59, second=59, microsecond=999999)
        total_thread_query = total_thread_query.where(Threads.updated_timestamp <= end_date)
    
    total_thread_count = await db.scalar(total_thread_query)
    print(f'total thread count == {total_thread_count} chatbot_ids == {chatbot_ids}')

    # Step 3: Prepare thread query with filters
    thread_query = select(Threads).where(Threads.chatbot_id.in_(chatbot_ids))
    
    if user_ids:
        thread_query = thread_query.where(Threads.user_id.in_(user_ids))
    # Add date filters to thread_query
    if start_date:
        thread_query = thread_query.where(Threads.updated_timestamp >= start_date)

    if end_date:
        thread_query = thread_query.where(Threads.updated_timestamp <= end_date)

    thread_query = thread_query.order_by(Threads.created_timestamp.desc()).offset(skip).limit(limit)
    # Execute the filtered query
    thread_results = await db.execute(thread_query)
    threads = thread_results.scalars().all()

    formatted_threads = []
    for thread in threads:
        print(f'thread == {thread}')
        chatbot_info = chatbot_data.get(thread.chatbot_id, {"name": "Unknown", "type": "Unknown"})
        user_data = await _get_user(user_id=thread.user_id, session=db)
        if user_data.role not in [UserRole.ADMIN, UserRole.SUPER_ADMIN]:
            formatted_threads.append(AllThreadResponseItem(
                thread_id=thread.thread_id,
                title=thread.title,
                user_name=user_data.name,
                user_id=thread.user_id,
                chatbot_name=chatbot_info["name"],
                chatbot_type=chatbot_info["type"],
                chatbot_id=thread.chatbot_id,
                created_timestamp=thread.created_timestamp,
                updated_timestamp=thread.updated_timestamp
            ))

    return AllThreadResponse(
        thread_sessions=formatted_threads,
        total_thread_session=total_thread_count
    )


async def delete_session(thread_id, user, session, chatbot_flag = False):
    if not chatbot_flag:
        user_thread = select(Threads).filter(
                    Threads.thread_id == thread_id,
                    Threads.user_id == user.id
                )
        result = await session.execute(user_thread)
        thread = result.scalars().first()
        if not thread:
            raise HTTPException(
                status_code=404,
                detail="Thread not found or you do not have permission to delete it."
            )
    
    # Delete all messages associated with the thread
    delete_messages_query = select(Messages).filter(Messages.thread_id == thread_id)
    messages_result = await session.execute(delete_messages_query)
    messages = messages_result.scalars().all()
    # Extract S3 keys from stored image URLs
    
    s3_keys = []
    for message in messages:
        if message.role == "Tool" and message.is_image:
            if message.message_content:
                parsed_url = urlparse(message.message_content)
                s3_key = parsed_url.path.lstrip("/")  # Extract the key from the URL
                s3_keys.append(s3_key)
        if message.role == "User" and message.is_image:
            if message.images_urls:
                for url in message.images_urls:
                    parsed_url = urlparse(url)
                    s3_key = parsed_url.path.lstrip("/")  # Extract the key from the URL
                    s3_keys.append(s3_key)
        await session.delete(message)
    
    # Delete the thread itself
    await session.delete(thread)
     # Delete all S3 objects associated with the thread
    for key in s3_keys:
        try:
            s3_client.delete_object(Bucket=envs.BUCKET_NAME, Key=key)
            print(f"🗑️ Deleted S3 object: {key}")
        except Exception as e:
            print(f"⚠️ Failed to delete S3 object {key}: {str(e)}")
    try:
        await session.commit()
    except Exception as e:
        await session.rollback()
        raise HTTPException(
            status_code=500,
            detail=f"An error occurred while deleting the thread: {str(e)}"
        )


async def admin_delete_session(thread_id, chatbot_id, user, session):
    if user.role not in [UserRole.ADMIN or UserRole.SUPER_ADMIN]:
        raise HTTPException(status_code=404, detail="Unautherize.")
    
    user_thread = select(Threads).filter(
                Threads.thread_id == thread_id,
                Threads.chatbot_id == chatbot_id
            )
    result = await session.execute(user_thread)
    thread = result.scalars().first()
    if not thread:
        raise HTTPException(
            status_code=404,
            detail="Thread not found or you do not have permission to delete it."
        )

    # Delete all messages associated with the thread
    delete_messages_query = select(Messages).filter(Messages.thread_id == thread_id)
    messages_result = await session.execute(delete_messages_query)
    messages = messages_result.scalars().all()
    # Extract S3 keys from stored image URLs
    from urllib.parse import urlparse
    s3_keys = []
    for message in messages:
        if message.role == "Tool" and message.is_image:
            if message.message_content:
                parsed_url = urlparse(message.message_content)
                s3_key = parsed_url.path.lstrip("/")  # Extract the key from the URL
                s3_keys.append(s3_key)
        if message.role == "User" and message.is_image:
            if message.images_urls:
                for url in message.images_urls:
                    parsed_url = urlparse(url)
                    s3_key = parsed_url.path.lstrip("/")  # Extract the key from the URL
                    s3_keys.append(s3_key)
        await session.delete(message)
    
    # Delete the thread itself
    await session.delete(thread)
     # Delete all S3 objects associated with the thread
    for key in s3_keys:
        try:
            s3_client.delete_object(Bucket=envs.BUCKET_NAME, Key=key)
            print(f"🗑️ Deleted S3 object: {key}")
        except Exception as e:
            print(f"⚠️ Failed to delete S3 object {key}: {str(e)}")
    try:
        await session.commit()
    except Exception as e:
        await session.rollback()
        raise HTTPException(
            status_code=500,
            detail=f"An error occurred while deleting the thread: {str(e)}"
        )


async def update_thread_title(thread_id, user, session, new_title):
    user_thread = select(Threads).filter(
        Threads.thread_id == thread_id,
        Threads.user_id == user.id
    )
    result = await session.execute(user_thread)
    thread = result.scalars().first()
    
    if not thread:
        raise HTTPException(
            status_code=404,
            detail="Thread not found or you do not have permission to update it."
        )
    
    # Update the title
    thread.title = new_title
    
    # Commit the changes
    await session.commit()
    return 

async def get_external_bot_chat_messagegs(fingerprint, thread_id, redis_store, llm=None):
    try:
        if await redis_store.llen(f"public:conversation:{fingerprint}:{thread_id}") > 6:
            print(f"\n\nMessage limit exceeded for thread {thread_id} : Current length: {await redis_store.llen(f'public:conversation:{fingerprint}:{thread_id}')}\n\n")
            messages = await redis_store.lrange(f"public:conversation:{fingerprint}:{thread_id}", 0, -1)
            summary = await summarize_messages(messages, llm)
            summary = "Previous conversation history of user is as follows:" + summary
            print(f"\n\nConversation summary for {thread_id} is:\t {summary}\n{type(summary)}\n")
            
            # Replace existing messages with the summary
            await redis_store.delete(f"public:conversation:{fingerprint}:{thread_id}")
            await redis_store.rpush(f"public:conversation:{fingerprint}:{thread_id}", json.dumps({"role": "ai","message": [{"type": "text", "text": summary}]}))
            # await redis_store.rpush(f"conversation:{thread_id}", json.dumps({"role": "ai", "message": summary}))
            await redis_store.expire(f"public:conversation:{fingerprint}:{thread_id}", envs.PUBLIC_TIME_TO_LIVE_IN_SECONDS)

        # redis_store.ltrim(str(thread_id), -10, -1)
        messages = await redis_store.lrange(str(f"public:conversation:{fingerprint}:{thread_id}"), 0, -1)
        print(f"\n\nMessages stored in redis are: {messages}\n\n")
        if not messages:
            return []
        
        return [json.loads(msg) for msg in messages]
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    
async def summarize_messages(messages, llm=None):
    """
    Summarizes a list of chat messages using the configured language model.

    Args:
        messages (list): List of message dictionaries with 'role' and 'message' keys.
        llm (llm): LLM.

    Returns:
        str: The summary of the messages.
    """
    if llm is None:
        llm = ChatOpenAI(model='gpt-4o', api_key=envs.OPENAI_API_KEY, temperature=0)

    try:
        conversation_text = []
        for msg in messages:
            msg = json.loads(msg)
            role = msg['role'].upper()
            content = msg['message']
            conversation_text.append(f"{role}: {content}")
        
        full_conversation = "\n\n".join(conversation_text)
        
        prompt = f"""
        You are an AI assistant that specializes in conversation summarization.
        Please analyze the ENTIRE conversation below and create a COMPREHENSIVE summary.
        Identify the main participants, key topics discussed, important questions asked, and any decisions made.
        Your summary should capture the full arc of the conversation, NOT just repeat the last message.

        CONVERSATION:
        {full_conversation}

        SUMMARY (covering all key points from the entire conversation):
        """
        
        user_message = HumanMessage(content=prompt)
        
        summary_response = await llm.ainvoke([user_message])
        summary_text = summary_response.content.strip()
        print(f"\n\nSummary response text is: {summary_text}\n\n")
        return summary_text
    
    except Exception as e:
        error_msg = f"Error during summarization: {str(e)}"
        print(f"\n\n{error_msg}\n\n")
        return f"Failed to generate summary: {str(e)}"

async def create_public_message_feedback(
        feedback_request: PublicMessageFeedbackRequest,
        db: AsyncSession,
        ):
    feedback_request.bot_id = _decrypt_chatbot_id(feedback_request.bot_id, fernet = fernet)
    result = await db.execute(select(ChatbotConfig).where(ChatbotConfig.id == int(feedback_request.bot_id)))
    chatbot_details = result.scalars().first()
    if not chatbot_details:
        raise HTTPException(status_code=404, detail="Chatbot not found.")

    # result = await db.execute(select(Messages).where(Messages.message_uuid == feedback_request.message_id))
    # message_details = result.scalars().first()
    # if not message_details:
    #     raise HTTPException(status_code=404, detail="Message not found.")
    
    message_feedback = MessagesFeedbacks(
        chatbot_id = int(feedback_request.bot_id),
        message_id = None,
        user_name = None,
        feedback = feedback_request.feedback,
        chatbot_type = chatbot_details.chatbot_type,
        org_admin_id = chatbot_details.organization_id,
        chatbot_name = chatbot_details.chatbot_name,
        message_text = feedback_request.message_text
    )
    db.add(message_feedback)
    # Commit the transaction
    await db.commit()
    # Refresh to load any new data (like autogenerated id)
    await db.refresh(message_feedback)
    # return message_feedback
    return MessageFeedbackResponse(
        bot_id = message_feedback.chatbot_id,
        user_name = message_feedback.user_name,
        message_id = message_feedback.message_id,
        feedback = message_feedback.feedback,
        chatbot_type = message_feedback.chatbot_type,
        status = message_feedback.status,
        feedback_id = message_feedback.id,
        chatbot_name = message_feedback.chatbot_name,
        message_text = message_feedback.message_text
    ) 

async def create_message_feedback(
        feedback_request: MessageFeedbackRequest,
        current_user,
        db: AsyncSession, 
    ):
    # feedback_request.bot_id = _decrypt_chatbot_id(feedback_request.bot_id, fernet = fernet)
    # Extract chatbot details based on chatbot_id
    
    # from app.services.organization import _fine_grain_role_checks
    # if current_user.role == UserRole.USER:
    #     if not current_user.group_ids:
    #         raise HTTPException(status_code=404, detail="You don't have permissions.")
      
    #     user_groups = await format_user_chatbot_permissions(db, current_user.organization_id, current_user.group_ids)
        
    #     bot_details = None
    #     for bot_info in user_groups:
    #         if bot_info['chatbot_id'] == feedback_request.bot_id:
    #             bot_details = bot_info
    #             break
    #     print(f'bot info == {bot_details}')
    #     if not bot_details:
    #         raise HTTPException(status_code=404, detail="Unautherized operation")
    #     await _fine_grain_role_checks(current_user, current_user.organization_id, feedback_request.bot_id, bot_details, "can_view_feedback")
    # else:
    #     await _fine_grain_role_checks(current_user, current_user.organization_id)
    
    result = await db.execute(select(ChatbotConfig).where(ChatbotConfig.id == feedback_request.bot_id))
    chatbot_details = result.scalars().first()
    if not chatbot_details:
        raise HTTPException(status_code=404, detail="Chatbot not found.")

    result = await db.execute(select(Messages).where(Messages.message_uuid == feedback_request.message_id))
    message_details = result.scalars().first()
    if not message_details:
        raise HTTPException(status_code=404, detail="Message not found.")
    
    message_feedback = MessagesFeedbacks(
        chatbot_id = feedback_request.bot_id,
        message_id = feedback_request.message_id,
        user_name = current_user.name,
        feedback = feedback_request.feedback,
        chatbot_type = chatbot_details.chatbot_type,
        org_admin_id = current_user.organization_id,
        chatbot_name = chatbot_details.chatbot_name,
        message_text = message_details.message_content
    )
    db.add(message_feedback)
    # Commit the transaction
    await db.commit()
    # Refresh to load any new data (like autogenerated id)
    await db.refresh(message_feedback)
    # return message_feedback
    return MessageFeedbackResponse(
        bot_id = message_feedback.chatbot_id,
        user_name = message_feedback.user_name,
        message_id = message_feedback.message_id,
        feedback = message_feedback.feedback,
        chatbot_type = message_feedback.chatbot_type,
        status = message_feedback.status,
        feedback_id = message_feedback.id,
        chatbot_name = message_feedback.chatbot_name,
        message_text = message_feedback.message_text
    )

from typing import Optional, List

async def get_message_feedback(
        current_user,
        session, 
        Reviewed: Optional[int] = None,  # 1 for Reviewed, 2 for Unreviewed
        External: Optional[int] = None,   # 1 for External, 2 for Internal
        limit: int = 10,
        skip: int = 0
    ):
    from app.services.organization import _fine_grain_role_checks
    if current_user.role == UserRole.USER:
        if not current_user.group_ids:
            raise HTTPException(status_code=404, detail="You don't have permissions.")
      
        user_groups, _ = await format_user_chatbot_permissions(session, current_user.organization_id, current_user.group_ids)
        chatbot_ids = []
        #put those chatbot which are allowed for user to view its feedback
        bot_details = None
        for bot_info in user_groups:
            if  bot_info['can_view_feedback'] and bot_info['chatbot_id'] not in chatbot_ids:
                chatbot_ids.append(bot_info['chatbot_id'])
                
        print(f'bot info == {chatbot_ids}')
        if not chatbot_ids:
            raise HTTPException(status_code=404, detail="No chatbot feedback allowed to view.")
        
    else:
        await _fine_grain_role_checks(current_user, current_user.organization_id)
        
    query = select(MessagesFeedbacks)
    if current_user.role == UserRole.USER:
        query = query.where(MessagesFeedbacks.chatbot_id.in_(chatbot_ids))
    if current_user.organization_id:
        query = query.where(MessagesFeedbacks.org_admin_id == current_user.organization_id)
    if Reviewed:
        if Reviewed == 1:
            query = query.where(MessagesFeedbacks.status == FeedbackStatus.Reviewed.value)
        elif Reviewed == 2:
            query = query.where(MessagesFeedbacks.status == FeedbackStatus.Unreviewed.value)
    if External:
        if External == 1:
            query = query.where(MessagesFeedbacks.chatbot_type == "External")
        elif External == 2:
            query = query.where(MessagesFeedbacks.chatbot_type == "Internal")

    count_query = select(func.count()).select_from(query.subquery())
    total_count = (await session.execute(count_query)).scalar()


    query = query.offset(skip).limit(limit)
    result = await session.execute(query)
    return result.scalars().all(), total_count

async def delete_message_feedback(
        feedback_data,
        current_user,
        session, 
    ):
    from app.services.organization import _fine_grain_role_checks
    query = await session.execute(select(MessagesFeedbacks).where(MessagesFeedbacks.id == feedback_data.feedback_id))
    record = query.scalars().first()

    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Record not found"
        )
    
    if current_user.role == UserRole.USER:
        if not current_user.group_ids:
            raise HTTPException(status_code=404, detail="You don't have permissions.")
      
        user_groups, _ = await format_user_chatbot_permissions(session, current_user.organization_id, current_user.group_ids)
        
        bot_details = None
        for bot_info in user_groups:
            if bot_info['chatbot_id'] == record.chatbot_id:
                bot_details = bot_info
                break
        print(f'bot info == {bot_details}')
        if not bot_details:
            raise HTTPException(status_code=404, detail="Unautherized operation")
        await _fine_grain_role_checks(current_user, current_user.organization_id, record.chatbot_id, bot_details, "can_view_feedback")
    else:
        await _fine_grain_role_checks(current_user, current_user.organization_id)
    
    # Delete the record
    await session.delete(record)
    await session.commit()

    return 


async def update_feedback_status(feedback_id, current_user, session):
     # Fetch feedback by ID
    from app.services.organization import _fine_grain_role_checks
    query = await session.execute(
        select(MessagesFeedbacks).where(MessagesFeedbacks.id == feedback_id)
    )
    record = query.scalars().first()

    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Feedback not found."
        )
    
    if current_user.role == UserRole.USER:
        if not current_user.group_ids:
            raise HTTPException(status_code=404, detail="You don't have permissions.")

        user_groups, _ = await format_user_chatbot_permissions(session, current_user.organization_id, current_user.group_ids)
        
        bot_details = None
        for bot_info in user_groups:
            if bot_info['chatbot_id'] == record.chatbot_id:
                bot_details = bot_info
                break
        print(f'bot info == {bot_details}')
        if not bot_details:
            raise HTTPException(status_code=404, detail="Unautherized operation")
        await _fine_grain_role_checks(current_user, current_user.organization_id, record.chatbot_id, bot_details, "can_view_feedback")
    else:
        await _fine_grain_role_checks(current_user, current_user.organization_id)
    
    # Update status instead of deleting
    record.status = FeedbackStatus.Reviewed.value

    # Commit the changes
    await session.commit()
    await session.refresh(record)

    return record.status

async def view_feedback(feedback_id, current_user, session):
    from app.services.organization import _fine_grain_role_checks
    query = await session.execute(
        select(MessagesFeedbacks).where(MessagesFeedbacks.id == feedback_id)
    )
    record = query.scalars().first()

    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Feedback not found."
        )
    if current_user.role == UserRole.USER:
        if not current_user.group_ids:
            raise HTTPException(status_code=404, detail="You don't have permissions.")

        user_groups, _ = await format_user_chatbot_permissions(session, current_user.organization_id, current_user.group_ids)
        
        bot_details = None
        for bot_info in user_groups:
            if bot_info['chatbot_id'] == record.chatbot_id:
                bot_details = bot_info
                break
        print(f'bot info == {bot_details}')
        if not bot_details:
            raise HTTPException(status_code=404, detail="Unautherized operation")
        await _fine_grain_role_checks(current_user, current_user.organization_id, record.chatbot_id, bot_details, "can_view_feedback")
    else:
        await _fine_grain_role_checks(current_user, current_user.organization_id)
    
    # Update status instead of deleting
    return record.feedback


async def delete_s3_object(s3_key: str):
    """
    Deletes an object from S3.
    """
    try:
        s3_client.delete_object(
            Bucket=envs.BUCKET_NAME,
            Key=s3_key
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to delete file: {str(e)}")
    return 