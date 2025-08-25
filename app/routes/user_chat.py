from sqlalchemy.orm import Session
from fastapi.responses import JSONResponse
from fastapi import APIRouter, Depends, status , HTTPException
from app.schemas.response.user_chat import AllSessionsResponse, ChatbotResponse, S3ResponseItem, S3UploadRequest, Chats, S3PublicUpload, S3Response, PublicS3Response,  GetMessagesResponseInternal, GetMessagesResponse,ThreadResponse ,AllMessageFeedbackResponse ,MessageFeedbackResponse, MessageReviewResponse, MessageViewResponse, AnalyticsResponse, AllThreadResponse
from app.common import database_config
from app.services import user_chat
from app.utils.db_helpers import insert_logs
from fastapi import BackgroundTasks
from app.services.auth import get_current_user
from app.models.user import User
from fastapi import Query
from typing import Optional, List
from app.services.user_chat import create_message_feedback, create_public_message_feedback,view_feedback, update_feedback_status,delete_message_feedback, get_message_feedback
from typing import List
from app.schemas.response.user_chat import ExternalChatbotResponse, ShareChatbotResponse
from app.schemas.request.user_chat import Analytics,AdminFilterRequest, S3DeleteRequest, DeleteFeedback, UserSecureChat, UserChat, ChatId, PublicMessageFeedbackRequest,MessageFeedbackRequest, AdminChatId
import boto3
import os
import json
from app.common.env_config import get_envs_setting
import time

from app.utils.rate_limiter import AppRateLimiter

app_limiter = AppRateLimiter()

envs = get_envs_setting()

chats_routes = APIRouter(
    prefix="/api/v1/chat",
    tags=["Chat"],
    responses={404: {"description": "Not found"}},
    # dependencies=[Depends(oauth2_scheme), Depends(validate_access_token), Depends(is_admin)]
)

feedback_router = APIRouter(
    prefix="/api/v1/feedback",
    tags=["feedback"],
    responses={404: {"description": "Not found"}},
    # dependencies=[Depends(get_current_user)]
)


# @chats_routes.get("/external/get-shared-bot/{chatbot_id}", status_code=status.HTTP_200_OK)
# async def get_shared_bot_url(
#     chatbot_id: int,
#     current_user: User = Depends(get_current_user),
#     session: Session = Depends(database_config.get_async_db)
# ):
#     return await user_chat.get_shared_bot_url_service(
#         session, 
#         chatbot_id, 
#         current_user.organization_id, 
#         current_user
#     )

@chats_routes.get("/external/get-shared-option/{chatbot_id}", status_code=status.HTTP_200_OK, response_model=ShareChatbotResponse)
async def get_share_iframe(
    chatbot_id: int,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(database_config.get_async_db)
):
    try:
        return await user_chat.get_share_service(
            session,
            chatbot_id,
            current_user.organization_id,
            current_user
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@chats_routes.get("/external/get-public-thread/{fingerprint_id}", status_code=status.HTTP_200_OK)
async def get_share_iframe(
    fingerprint_id: str
):
    try:
        public_thread_id = await user_chat.get_public_thread_service(
            fingerprint_id
        )
        return JSONResponse({'thread_id': public_thread_id})
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    
@chats_routes.post("/external/send-message", status_code=status.HTTP_200_OK, response_model=ExternalChatbotResponse)
async def send_message(data: UserSecureChat, backgound_task:BackgroundTasks, session: Session = Depends(database_config.get_async_db)): 
    #application wide limit here

    await app_limiter.init_redis()
    await app_limiter.check_chat_limits()

    bot_answer, thread_id =  await user_chat.chat_with_external_bot(data, session, background_tasks=backgound_task)
    
    return ExternalChatbotResponse(id=thread_id, answer=bot_answer['message'])

@chats_routes.post("/external/s3-public-session-url", status_code=status.HTTP_200_OK, response_model=PublicS3Response)
async def generate_s3_presigned_url(request: S3PublicUpload):
    try:
        await app_limiter.init_redis()  # Will only assign once
        await app_limiter.check_url_limits()

        fingerprint = request.fingerprint
        presigned_urls, thread_id = await user_chat.generate_public_pre_signed_url(request)
        return PublicS3Response(files=presigned_urls, thread_id=thread_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@chats_routes.post("/s3-session-url", status_code=status.HTTP_200_OK, response_model=S3Response)
async def generate_s3_presigned_url(
            request: S3UploadRequest, 
            current_user: User = Depends(get_current_user),
            session = Depends(database_config.get_async_db)):
    try:
        presigned_urls = await user_chat.generate_pre_signed_url(request, current_user, request.org_id, session)
        print(f's3-session-url == {current_user.is_user_consent_given}')
        return S3Response(files=presigned_urls, is_user_consent_given=False if not current_user.is_user_consent_given else True)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@chats_routes.post("/internal/send-message", status_code=status.HTTP_200_OK, response_model=ChatbotResponse)
async def update_admin_config(
                            data: UserChat, backgound_task:BackgroundTasks, 
                            session = Depends(database_config.get_async_db),
                            current_user: User = Depends(get_current_user)
                            ): 
    print(f'start execuction')
    if not data.question:
        raise HTTPException(status_code=400, detail="Enter a question")
    print('send api start execution')
    
    bot_answer, url_to_image, thread_id, image_generation_flag, created_at, updated_at, message_id, new_chat =  await user_chat.chat_with_bot(data, session, current_user, background_tasks=backgound_task)
     
    return ChatbotResponse(id=thread_id, answer=bot_answer['message'] if not image_generation_flag else '', url_to_image = url_to_image, image_generation_flag=image_generation_flag, created_at=created_at, updated_at=updated_at, message_id = message_id, new_chat = new_chat)

@chats_routes.post("/internal/get-messages", status_code=status.HTTP_200_OK, response_model=GetMessagesResponseInternal)
async def update_admin_config(
                            data: ChatId, 
                            session = Depends(database_config.get_async_db),
                            current_user: User = Depends(get_current_user),
                            offset: int = Query(0, ge=0),  # Page number starts from 1
                            limit: int = Query(20, ge=1, le=100)  # Limit results per page
                            ): 
                            
    chat_messages =  await user_chat.get_route_chat_messagegs(data, current_user, session, offset, limit)
    return chat_messages
    # return GetMessagesResponse(id=data.id, chat_messages=chat_messages)

@chats_routes.get('/internal/list-sessions/{chatbot_id}',status_code = status.HTTP_200_OK, response_model=AllSessionsResponse)
async def all_thread(
                    chatbot_id: int,
                    thread_name: str = Query(None),
                    current_user: User = Depends(get_current_user),
                    session = Depends(database_config.get_async_db),
                    limit: int = Query(20),
                    skip: int = Query(0)
                    ):
    all_session,total_sessions = await user_chat.get_all_sessions(chatbot_id = chatbot_id, user = current_user, session=session, limit=limit, skip=skip,thread_name=thread_name,)
    sessions = []
    for session in all_session:
        sessions.append(ThreadResponse(
            thread_id = session.thread_id,
            title = session.title,
            created_timestamp = session.created_timestamp,
            updated_timestamp = session.updated_timestamp 
        ))
    response_sessions = AllSessionsResponse(sessions = sessions, total_session=total_sessions)
    return response_sessions

@chats_routes.get('/internal/search-sessions/{chatbot_id}', status_code=status.HTTP_200_OK, response_model=AllSessionsResponse)
async def search_thread(
                    chatbot_id: int,
                    thread_name: str = Query(None),  # Optional search parameter
                    current_user: User = Depends(get_current_user),
                    session = Depends(database_config.get_async_db),
                    limit: int = Query(20),
                    skip: int = Query(0)
                    ):
    all_session, total_sessions = await user_chat.get_all_sessions_with_search(
        chatbot_id=chatbot_id,
        user=current_user,
        session=session,
        thread_name=thread_name,  # Pass thread_name for filtering
        limit=limit,
        skip=skip
    )

    sessions = [
        ThreadResponse(
            thread_id=session.thread_id,
            title=session.title,
            created_timestamp=session.created_timestamp,
            updated_timestamp=session.updated_timestamp
        )
        for session in all_session
    ]

    response_sessions = AllSessionsResponse(sessions=sessions, total_session=total_sessions)
    return response_sessions


@chats_routes.delete('/delete-session/{thread_id}',status_code = status.HTTP_200_OK)
async def delete_thread(
                        thread_id: int, 
                        current_user: User = Depends(get_current_user),
                        session = Depends(database_config.get_async_db),):
    if not thread_id:
        raise HTTPException(status_code=400, detail="Enter project id")
    await user_chat.delete_session(thread_id=thread_id, user = current_user, session=session)
    
    return JSONResponse({'message':'The Thread has been deleted succefully!'})

@chats_routes.post('/update-thread-title/{thread_id}',status_code = status.HTTP_200_OK)
async def delete_thread(
                        thread_id: int, 
                        title: str,
                        current_user: User = Depends(get_current_user),
                        session = Depends(database_config.get_async_db),):
    if not thread_id:
        raise HTTPException(status_code=400, detail="Enter project id")
    await user_chat.update_thread_title(thread_id=thread_id, user = current_user, session=session, new_title=title)
    
    return JSONResponse({'message':'The thread title has been updated succefully!'})

# @chats_routes.post('/chatbot/{chatbot_id}/share', status_code = status.HTTP_200_OK)
# async def delete_thread(
#                         current_user: User = Depends(get_current_user),
#                         session: Session = Depends(database_config.get_async_db)):
#     await generate_sherable_link(current_user, session)



@feedback_router.post("/public",   status_code = status.HTTP_200_OK)
async def feedback_content_creation(
    feedback_request: PublicMessageFeedbackRequest,
    session = Depends(database_config.get_async_db),
):
    if len(feedback_request.feedback) > envs.PER_FEEDBACK_CHAR_LEN:
        raise HTTPException(status_code=422, detail=f"Maximum allowed character limit are 300.")
    response =  await create_public_message_feedback(
        feedback_request,
        session,
    )
    
    return JSONResponse({'message':'The feedback has been submitted.!'})


@feedback_router.post("/",  response_model=MessageFeedbackResponse)
async def feedback_content_creation(
    feedback_request: MessageFeedbackRequest,
    current_user: User = Depends(get_current_user),
    session = Depends(database_config.get_async_db),
):
    if len(feedback_request.feedback) > envs.PER_FEEDBACK_CHAR_LEN:
        raise HTTPException(status_code=422, detail=f"Maximum allowed character limit are 300.")
    response =  await create_message_feedback(
        feedback_request,
        current_user,
        session, 
    )
    # response = MessageFeedbackResponse(
    #     bot_id = created_announcement.chatbot_id,
    #     user_name = created_announcement.user_name,
    #     message_id = created_announcement.message_id,
    #     feedback = created_announcement.feedback,
    #     chatbot_type = created_announcement.chatbot_type,
    #     status = created_announcement.status,
    #     feedback_id = created_announcement.id
    # )
    # retun teh created chatbot emeory in format creator, text
    return response

@feedback_router.get("/",  response_model=AllMessageFeedbackResponse)
async def get_all_feedbacks(
    Reviewed: Optional[int] = Query(None), # 1 for review 2 for unreview
    External: Optional[int] = Query(None), # 1 for external 2 for internal
    current_user: User = Depends(get_current_user),
    session = Depends(database_config.get_async_db),
    limit: int = Query(10, ge=1),  # Pagination limit (default 10)
    skip: int = Query(0, ge=0),
):
    
    feedbacks_data, total_feedback =  await get_message_feedback(
        current_user,
        session, 
        Reviewed,
        External,
        limit,
        skip
    )
    feedbacks = []
    for fb in feedbacks_data:
        feedbacks.append(MessageFeedbackResponse(
            bot_id=fb.chatbot_id,
            user_name=fb.user_name,
            message_id=fb.message_id,
            feedback=fb.feedback,
            chatbot_type=fb.chatbot_type,
            status=fb.status,
            feedback_id=fb.id,
            chatbot_name = fb.chatbot_name,
            message_text = fb.message_text
        ))

    return AllMessageFeedbackResponse(feedbacks = feedbacks, total_feedback = total_feedback)


# @feedback_router.patch("/",  response_model=MessageFeedbackResponse)
# async def update_feedback_content(
#     feedback_request: MessageFeedbackRequest,
#     current_user: User = Depends(get_current_user),
#     session: Session = Depends(database_config.get_async_db),
# ):
    
#     created_announcement =  await create_message_feedback(
#         feedback_request,
#         current_user,
#         session, 
#     )
#     response = MessageFeedbackResponse(
#         bot_id = created_announcement.chatbot_id,
#         user_name = created_announcement.user_name,
#         message_id = created_announcement.message_id,
#         feedback = created_announcement.feedback,
#         chatbot_type = created_announcement.chatbot_type,
#         status = created_announcement.status,
#         feedback_id = created_announcement.id
#     )
#     # retun teh created chatbot emeory in format creator, text
#     return response

from fastapi import Body
@feedback_router.delete("/", status_code = status.HTTP_200_OK)
async def feedback_content_creation(
    feedback_update: DeleteFeedback = Body(...),
    current_user: User = Depends(get_current_user),
    session = Depends(database_config.get_async_db),
):
    delete_feedback =  await delete_message_feedback(
        feedback_update,
        current_user,
        session, 
    )
    return JSONResponse({'message':'The feedback has been deleted.!'})

@feedback_router.patch("/review", response_model=MessageReviewResponse)
async def review_feedback(
    feedback_update: DeleteFeedback,
    current_user: User = Depends(get_current_user),
    session = Depends(database_config.get_async_db),
):
    updated_feedback_status = await update_feedback_status(feedback_update.feedback_id, current_user, session)
    return MessageReviewResponse(status = updated_feedback_status)

@feedback_router.post("/view", response_model=MessageViewResponse)
async def review_feedback(
    view_update: DeleteFeedback,
    current_user: User = Depends(get_current_user),
    session = Depends(database_config.get_async_db),
):
    feedback_data = await view_feedback(view_update.feedback_id, current_user, session)
    return MessageViewResponse(feedback = feedback_data)

@chats_routes.delete("/delete-s3-object", status_code = status.HTTP_200_OK)
async def delete_s3_object(
    request: S3DeleteRequest,
    current_user: User = Depends(get_current_user),
):
    try:
        response = await user_chat.delete_s3_object(request.s3_key)
        return {"message": "File deleted successfully", "s3_key": request.s3_key}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@chats_routes.post('/list-all-sessions',status_code = status.HTTP_200_OK, response_model=AllThreadResponse)
async def all_thread(
                    filters_data: AdminFilterRequest,
                    current_user: User = Depends(get_current_user),
                    session = Depends(database_config.get_async_db),
                    skip: int = 0,
                    limit: int = 10,
                    ):
    formatted_results = await user_chat.get_all_org_session(user=current_user, skip=skip, limit=limit, db=session, chatbot_ids=filters_data.chatbot_ids, user_ids=filters_data.user_ids,start_date=filters_data.start_date, end_date=filters_data.end_date )
    
    return formatted_results

@chats_routes.post("/admin-list-session-messages", status_code=status.HTTP_200_OK, response_model=GetMessagesResponse)
async def update_admin_config(
                            data: AdminChatId, 
                            session = Depends(database_config.get_async_db),
                            current_user: User = Depends(get_current_user),
                            limit: int = Query(20),
                            skip: int = Query(0)
                            ): 
    chat_messages =  await user_chat.admin_get_route_chat_messagegs(data, current_user, session,  limit, skip)
    return chat_messages

@chats_routes.delete('/admin-delete-session/{thread_id}/{chatbot_id}',status_code = status.HTTP_200_OK)
async def delete_thread(
                        thread_id: int, 
                        chatbot_id: int,
                        current_user: User = Depends(get_current_user),
                        session = Depends(database_config.get_async_db),):
    if not thread_id:
        raise HTTPException(status_code=400, detail="Enter project id")
    await user_chat.admin_delete_session(thread_id=thread_id, chatbot_id = chatbot_id, user = current_user, session=session)
    
    return JSONResponse({'message':'The Thread has been deleted succefully!'})

@chats_routes.post('/analytics',status_code = status.HTTP_200_OK, response_model=AnalyticsResponse)
async def analytics(
                    filters_data: Analytics,
                    current_user: User = Depends(get_current_user),
                    session = Depends(database_config.get_async_db),
                    ):
    formatted_results = await user_chat.get_all_org_messages_count(user=current_user, db=session, chatbot_ids=filters_data.chatbot_ids)
    
    return formatted_results


