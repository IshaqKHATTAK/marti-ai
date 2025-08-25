from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import HTTPException, status, UploadFile, File
from app.models.organization import Organization
from app.models.user import User, UserRole, Plan
from app.models.chatbot_model import ChatbotSettings, MessagesFeedbacks, FeedbackStatus, BubbleSettings
from sqlalchemy.orm.attributes import flag_modified
from app.schemas.response.user import UserResponse, BulkUploadResponse, UserResponseCreate
from app.schemas.response.organization import OrganizationResponse, OrganizationListResponse, ChatbotInfo, OrganizationUsersResponse
from app.schemas.request.organization import (
    OrganizationCreate, 
    OrganizationUpdate, 
    OrganizationUserAdd, 
    OrganizationUserUpdate,
    
)
from botocore.exceptions import NoCredentialsError
from sqlalchemy import delete
from sqlalchemy.orm import selectinload, joinedload
from sqlalchemy.orm import contains_eager
from collections import defaultdict
from app.schemas.response.organization import SuperAdminOrganizations, AllSuperAdminOrganizations
from app.utils.database_helper import insert_document_entry,delete_document_entry,delete_webscrap_entry, insert_webscrap_entry, get_webscrap_entery, format_user_chatbot_permissions,get_rbac_groups_by_id, get_rbac_groups_by_org_id
from sqlalchemy import select
from typing import List, Optional
from datetime import datetime, UTC, timedelta
from app.models.chatbot_model import SuperAdminModel,ChatbotConfig, ChatbotDocument, Messages,Threads,QATemplate, ChatbotGuardrail, ChatSuggestion, ChatbotMemory, ChatSuggestion
from app.schemas.request.chatbot_config import CreateData, UpdateChatbotConfigRequest, ChatbotDetails
from app.schemas.request.user import UserCreate
from app.common.security import SecurityManager
from app.utils.db_helpers import get_user_by_email, get_user_by_id
from app.schemas.response.chatbot_config import KnowledgeBaseDoc, WebsiteUrlPagination, DocumentPagination,WebscrapUrl, DocumentInfo, QATemplateData,UrlValidationResponse, ChatbotFileUpdateResponse, ChatbotConfigResponse, GetGuardrails
from urllib.parse import urlparse, urlunparse
import os
import io
import pandas as pd
from app.common.env_config import get_envs_setting
import boto3
import json
import uuid


envs = get_envs_setting()

# Initialize S3 client
s3_client = boto3.client('s3')
sqs_client = boto3.client("sqs", region_name=envs.AWS_REGION) 

############################################Core route functions#########################
from urllib.parse import urlparse, urlunparse
def normalize_url(url: str) -> str:
    parsed = urlparse(url)
    # Remove query and fragment, strip trailing slash
    path = parsed.path.rstrip('/')
    normalized = urlunparse((
        parsed.scheme,
        parsed.netloc.lower(),
        path,
        '', '', ''  # clear params, query, fragment
    ))
    return normalized


async def _get_all_org_users(organization_id, db):
    users = await db.execute(select(User).filter(User.organization_id == organization_id))
    return users.scalars().all()

async def create_bulk_user_service(
    organization_id: int,
    db: AsyncSession,
    current_user: User,
    file: UploadFile = File(...),
):
    """Bulk upload users from CSV file and send password reset emails"""
    await _role_based_checks(current_user, organization_id)
    if current_user.current_plan == Plan.free:
        raise HTTPException(status_code=400, detail=f"Please upgrade your plan to create users.")
    if current_user.current_plan == Plan.starter:
        raise HTTPException(status_code=400, detail=f"Please upgrade your plan to create users.")
    
    organization = await get_organization(db, organization_id)
    
    if not file.filename.endswith(('.csv', '.xlsx')):
        raise HTTPException(status_code=400, detail="Only CSV or Excel files are allowed")
    
    content = await file.read()
    
    try:
        if file.filename.endswith('.csv'):
            df = pd.read_csv(io.StringIO(content.decode('utf-8')))
        else:
            df = pd.read_excel(io.BytesIO(content))
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Error parsing file: {str(e)}")
    
    # Check if required columns exist
    required_columns = ['Full Name', 'Email', 'RBAC ID', 'Password']
    missing_columns = [col for col in required_columns if col not in df.columns]
    if missing_columns:
        raise HTTPException(
            status_code=400,
            detail=f"Missing required columns: {', '.join(missing_columns)}"
        )
    
    if len(df) > envs.TOTAL_NO_OF_ALLOWED_BULK_USERS:
        raise HTTPException(
            status_code=400,
            detail="Maximum number of users exceeded. Please limit to 200 users per upload."
        )
    
    users = await _get_all_org_users(organization_id = organization_id, db = db)
    if current_user.current_plan == Plan.free: #and not current_user.is_active and (len(users)-1) >= envs.FREE_TIER_USERS
        if not current_user.is_paid:
            raise HTTPException(
                status_code=400,
                detail="Free users without a paid plan cannot create a chatbot."
            )
        if not current_user.is_active and (len(users) + len(df) - 1) >= envs.FREE_TIER_USERS:
            raise HTTPException(
                status_code=400,
                detail="Please upgrade your plan to create a chatbot."
            )
    if len(users) + len(df) >= envs.TOTAL_ALLOWED_USERS_FOR_PAID:
        raise HTTPException(
            status_code=400,
            detail="You reach the maximum allowed number of users."
        )

    file_key = f"bulk_upload/{organization_id}/{file.filename}"
    try:
        s3_client.put_object(
            Bucket=envs.BUCKET_NAME,
            Key=file_key,
            Body=content,
            ContentType=file.content_type
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error uploading file to S3: {str(e)}")

    return {
        "detail": "File uploaded successfully",
        "s3_file_key": file_key
    }
    

async def list_all_organizations_service(
    db: AsyncSession,
    current_user: User,
    limit: int = 10,
    skip: int = 0
):
    """List all organizations (Super Admin only)"""
    if current_user.role != UserRole.SUPER_ADMIN:
        raise HTTPException(
            status_code=403,
            detail="Only super admin can view all organizations"
        )
      # Get total count before pagination
    total_query = select(func.count()).select_from(Organization)
    total_organizations = (await db.execute(total_query)).scalar()

    query = (
        select(Organization)
        .options(selectinload(Organization.users))  # Efficient loading of related users
        .order_by(Organization.id)
        .offset(skip)  # Apply pagination
        .limit(limit)
    )
    result = await db.execute(query)
    organizations = result.scalars().all()
    
    # Prepare response data
    response = []
    for org in organizations:
        # Find the admin user for the organization
        admin_user = next((user for user in org.users if user.role == UserRole.ADMIN), None)
        if admin_user:
            response.append(
                SuperAdminOrganizations(
                    admin_id = admin_user.id,
                    organization_id=org.id,
                    admin_name=admin_user.name,
                    organization_name=org.name,
                    admin_email = admin_user.email,
                    is_active=admin_user.is_active,
                    is_paid = admin_user.is_paid,
                    current_plan = admin_user.current_plan
                )
            )
    
    return AllSuperAdminOrganizations(
        organizations=response,
        total_organizations=total_organizations
    )

from app.utils.db_helpers import get_user_organization_admin
async def create_organization_user_service(
    db: AsyncSession,
    organization_id: int,
    user_data: UserCreate,
    current_user: User
) -> User:
    """Create a new user in the organization"""
    # First verify the organization exists
    
    await _role_based_checks(current_user, organization_id)

    if current_user.role == UserRole.SUPER_ADMIN:
        current_user = await get_user_organization_admin(db = db, organization_id = organization_id)
    #-------------------------------------------THIS NEEDS TO BE COMMENT REMOVED.------------------------------------------------------------------------
    # from app.services.payment import allowed_users_checks
    # users_falg = await allowed_users_checks(db, current_user.stripeId)
    # if not users_falg:
    #     raise HTTPException(
    #         status_code=443,
    #         detail="Please upgrade your plan."
    #     )
    organization = await get_organization(db, organization_id)
    if user_data.password != user_data.confirm_password:
        raise HTTPException(
            status_code=400,
            detail="Password and confirm password does not match."
        )
    
    users = await _get_all_org_users(organization_id = organization_id, db = db)
    print(f'user len = {len(users)} envs = {envs.FREE_TIER_USERS} and active == {current_user.is_active}')
    user_group_ids = []
    user_group_ids_response = []
    for group_id in user_data.group_ids:
        user_group_detials = await get_rbac_groups_by_id(db, organization_id, group_id)
        if user_group_detials:
            user_group_ids.append(group_id)
            user_group_ids_response.append({"id":group_id, "name":user_group_detials.name})
    user_data.group_ids.clear()
    user_data.group_ids = user_group_ids
    if current_user.current_plan == Plan.free: # Free trial only --and current_user.is_paid
        print(f'inside free')
        raise HTTPException(
                status_code=400,
                detail="Please upgrade your plan to create a users."
            )
        # if not current_user.is_paid:
        #     raise HTTPException(
        #         status_code=400,
        #         detail="Please upgrade your users quote"
        #     )

        # if (len(users)-1) >= envs.FREE_TIER_USERS: #allow one user for free trial.
        #     raise HTTPException(
        #         status_code=400,
        #         detail="Please upgrade your plan to create a users."
        #     )
        # raise HTTPException(
        #     status_code=400,
        #     detail="Please upgrade your plan to create chatbot."
        # )
    else:
        from app.services.payment import get_current_user_seats

        total_users_paid = await get_current_user_seats(stripe_customer_id = current_user.stripeId)
        if len(users) > total_users_paid:
            raise HTTPException(
                status_code=400,
                detail="You reach the maximum allowed number of users."
            )
    # if current_user.current_plan == Plan.starter:
    #     raise HTTPException(
    #             status_code=400,
    #             detail="Please upgrade your plan to create a users."
    #         )
    # if (len(users)-1) >= envs.TOTAL_ALLOWED_USERS_FOR_PAID:
    #     raise HTTPException(
    #         status_code=400,
    #         detail="You reach the maximum allowed number of users."
    #     )
    
    # Delegate user creation to user service
    # import here to avoid circular import
    from app.services.user import create_user_in_organization
    created_user =  await create_user_in_organization(db, user_data, organization.id)
    
    # for chatid in user_data.chatbot_ids:
    #         chatbots_query = select(ChatbotConfig).filter(ChatbotConfig.id == chatid)
    #         chatbots_result = await db.execute(chatbots_query)
    #         chatbot = chatbots_result.scalar_one_or_none()
    #         if chatbot:
    #             user_chatbots.append(ChatbotInfo(
    #             chatbot_id=int(chatbot.id),
    #             chatbot_name=str(chatbot.chatbot_name)
    #             ).model_dump())
    
    # user_groups = []
    # for group_id in created_user.group_ids:
    #     user_group_detials = await get_rbac_groups_by_id(db, organization.id, group_id)
    #     if user_group_detials:
    #         user_groups.extend(user_group_detials.attributes)

    user_groups, _ = await format_user_chatbot_permissions(db,current_user.organization_id, current_user.group_ids)
    userresponse = UserResponseCreate(
                id=created_user.id,
                name=created_user.name,
                email=created_user.email,
                role = created_user.role,
                total_messages = created_user.total_messages,
                organization_id = created_user.organization_id,
                groups = user_groups,
                group_ids = user_group_ids_response,
                is_active = created_user.is_active,
                is_paid = created_user.is_paid,
                current_plan = created_user.current_plan,
                created_at = created_user.created_at,
                updated_at = created_user.updated_at,
                is_verified = created_user.is_verified,
                verified_at = created_user.verified_at
            )
    
    return userresponse

async def super_admin_list_organization_users_service(
    db: AsyncSession,
    organization_id: int,
    current_user: User
    ):
    await _role_based_checks(current_user, organization_id)
    users_response = []
    result = await db.execute(select(User).filter(User.organization_id == organization_id))
    users = result.scalars().all()
    for user in users:
        if user.role != UserRole.ADMIN:
            users_response.append(
                    OrganizationResponse(
                        id=user.id,
                        name=user.name,
                        email=user.email,
                        total_messages=user.total_messages,
                        is_active=user.is_active,
                        created_at=user.created_at,
                        updated_at=user.updated_at,
                        chatbots= [],
                        role = user.role
                    )
                )

    response = OrganizationUsersResponse(
        organization_name='',
        organization_id=0,
        users_data=users_response,
        total_users=len(users)
    )
    return response
from app.schemas.response.organization import UpdateOrganization, UpdateUserProfile

async def fetch_organization_profile(db: AsyncSession, organization_id: int, current_user):
    """
    Fetch organization profile details for the given organization_id.
    Ensures the current user has access to the organization.
    """
    result = await db.execute(select(Organization).filter(Organization.id == organization_id))
    organization = result.scalars().first()

    if not organization:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Organization not found"
        )
    if current_user.role == UserRole.ADMIN:
        # Optional: Check if the current user belongs to this organization
        if current_user.organization_id != organization_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have access to this organization"
            )

    return UpdateOrganization(
        name=organization.name,
        logo=organization.logo,
        website_url=organization.website_link
    )


async def update_user_profile(
    db: AsyncSession,
    current_user: User,
    updated_data: UpdateUserProfile
):
    """
    Update the admin user's profile details if any value is changed.
    """
    result = await db.execute(select(User).filter(User.id == current_user.id))
    user = result.scalars().first()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )

    # Update only if values are provided
    updated = False
    if updated_data.name is not None and updated_data.name != user.name:
        user.name = updated_data.name
        updated = True
    if updated_data.avatar_url is not None and updated_data.avatar_url != user.avatar_url:
        user.avatar_url = updated_data.avatar_url
        updated = True

    if not updated:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No changes detected in the user profile"
        )

    await db.commit()
    await db.refresh(user)

    return UpdateUserProfile(
        name=user.name,
        avatar_url=user.avatar_url
    )


async def update_organization_profile_data(
    db: AsyncSession,
    organization_id: int,
    current_user,
    updated_data: UpdateOrganization
):
    """
    Update the organization's profile details if any value is changed.
    """
    result = await db.execute(select(Organization).filter(Organization.id == organization_id))
    organization = result.scalars().first()

    if not organization:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Organization not found"
        )
    if current_user.role == UserRole.ADMIN:
        # Ensure the user has permission to modify the organization
        if current_user.organization_id != organization_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have permission to modify this organization"
            )

    # Update only if values are provided
    updated = False
    if updated_data.name is not None and updated_data.name != organization.name:
        organization.name = updated_data.name
        updated = True
    if updated_data.logo is not None and updated_data.logo != organization.logo:
        organization.logo = updated_data.logo
        updated = True
    if updated_data.website_url is not None and updated_data.website_url != organization.website_link:
        organization.website_link = updated_data.website_url
        updated = True

    if not updated:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No changes detected in the organization profile"
        )

    organization.updated_at = datetime.utcnow()

    await db.commit()
    await db.refresh(organization)

    return UpdateOrganization(
        name=organization.name,
        logo=organization.logo,
        website_url=organization.website_link
    )


async def list_chatbot_urls(
    db: AsyncSession,
    organization_id: int,
    chatbot_id: int,
    current_user: User,
    skip_urls: int,
    limit_urls: int
) -> List[User]:
    """List all users in an organization (Admin/Super Admin only)"""
    # await _role_based_checks(current_user, organization_id)
    # if current_user.role == UserRole.ADMIN:
    #only return the user which are having grole user
    if current_user.role == UserRole.USER:
        if not current_user.group_ids:
            raise HTTPException(status_code=404, detail="You don't have permissions.")
      
        user_groups = await format_user_chatbot_permissions(db, current_user.organization_id, current_user.group_ids)
        
        bot_details = None
        for bot_info in user_groups:
            if bot_info['chatbot_id'] == chatbot_id:
                bot_details = bot_info
                break
        print(f'bot info == {bot_details}')
        if not bot_details:
            raise HTTPException(status_code=404, detail="Unautherized operation")
        await _fine_grain_role_checks(current_user, organization_id, chatbot_id, bot_details, "can_edit_webscrap")
    else:
        await _fine_grain_role_checks(current_user, organization_id)

    # ---------- GET URLS (content_type = 'url') ---------- #
    total_urls_query = (
        select(func.count())
        .select_from(ChatbotDocument)
        .filter(
            ChatbotDocument.chatbot_id == chatbot_id,
            ChatbotDocument.content_type == 'url'
        )
    )
    total_urls_result = await db.execute(total_urls_query)
    total_urls = total_urls_result.scalar()

    urls_query = (
        select(ChatbotDocument)
        .filter(
            ChatbotDocument.chatbot_id == chatbot_id,
            ChatbotDocument.content_type == 'url'
        )
        .offset(skip_urls)
        .limit(limit_urls)
    )
    urls_result = await db.execute(urls_query)
    url_entries = urls_result.scalars().all()

    urls = []
    current_time = datetime.utcnow()
    for doc in url_entries:
        urls.append(
            WebscrapUrl(
                website_link=doc.document_name,
                website_url_id=doc.id,
                sweep_domain=(doc.url_sweep_option.lower() == "domain"),
                sweep_url=(doc.url_sweep_option.lower() == "website_page"),
                status=doc.status
            )
        )

        if doc.status == "Failed":
            await db.delete(doc)
        elif doc.status == "Uploaded":
            if current_time - doc.created_at > timedelta(minutes=5):
                print(f'Deleted the entry with upload status.')
                await db.delete(doc)
        # ---------- Return Combined Paginated Response ---------- # 
    return WebsiteUrlPagination(
                website_url=urls,
                total_webiste=total_urls
            )

    # return KnowledgeBaseDoc(
    #         websites=WebsiteUrlPagination(
    #             website_url=urls,
    #             total_webiste=total_urls
    #         ),
    #         documents=DocumentPagination(
    #             bot_documents=bot_documents,
    #             total_documents=total_documents
    #         )
    #     )


async def list_chatbot_docs(
    db: AsyncSession,
    organization_id: int,
    chatbot_id: int,
    current_user: User,
    skip_docs: int,
    limit_docs: int,
) -> List[User]:
    """List all users in an organization (Admin/Super Admin only)"""
    # await _role_based_checks(current_user, organization_id)
    # if current_user.role == UserRole.ADMIN:
    # Normalize URL — optional but smart
    if current_user.role == UserRole.USER:
        if not current_user.group_ids:
            raise HTTPException(status_code=404, detail="You don't have permissions.")
      
        user_groups = await format_user_chatbot_permissions(db, current_user.organization_id, current_user.group_ids)
        
        bot_details = None
        for bot_info in user_groups:
            if bot_info['chatbot_id'] == chatbot_id:
                bot_details = bot_info
                break
        print(f'bot info == {bot_details}')
        if not bot_details:
            raise HTTPException(status_code=404, detail="Unautherized operation")
        await _fine_grain_role_checks(current_user, organization_id, chatbot_id, bot_details, "can_edit_fileupload")
    else:
        await _fine_grain_role_checks(current_user, organization_id)

    #only return the user which are having grole user
    total_docs_query = (
        select(func.count())
        .select_from(ChatbotDocument)
        .filter(
            ChatbotDocument.chatbot_id == chatbot_id,
            ChatbotDocument.content_type != 'url'
        )
    )
    total_docs_result = await db.execute(total_docs_query)
    total_documents = total_docs_result.scalar()
    documents_query = (
        select(ChatbotDocument)
        .filter(
            ChatbotDocument.chatbot_id == chatbot_id,
            ChatbotDocument.content_type != 'url'
        )
        .offset(skip_docs)
        .limit(limit_docs)
    )
    documents_result = await db.execute(documents_query)
    document_entries = documents_result.scalars().all()
    bot_documents = []
    current_time = datetime.utcnow()
    for doc in document_entries:
        parsed_url = urlparse(doc.document_name)
        file_name = parsed_url.path.split("/")[-1]
        file_size_kb = get_s3_file_size(doc.document_name)

        bot_documents.append(
            DocumentInfo(
                document_name=file_name,
                document_id=doc.id,
                document_status=doc.status,
                document_size=file_size_kb
            )
        )

        if doc.status == "Failed":
            await db.delete(doc)
        elif doc.status == "Uploaded":
            if current_time - doc.created_at > timedelta(minutes=5):
                print(f'Deleted the entry with upload status.')
                await db.delete(doc)

    return DocumentPagination(
                bot_documents=bot_documents,
                total_documents=total_documents
            )


async def list_organization_users_service(
    db: AsyncSession,
    organization_id: int,
    current_user: User,
    skip: int, 
    limit: int,
    super_admin_api = False
) -> List[User]:
    """List all users in an organization (Admin/Super Admin only)"""
    await _role_based_checks(current_user, organization_id)
    # if current_user.role == UserRole.ADMIN:
        #only return the user which are having grole user
    users =  await get_organization_users(db, organization_id,  role_filter=UserRole.USER, skip=skip, limit=limit)
    
    users_response = []
    for user in users:
        # user_groups = []
        user_groups_ids = []
        if user.group_ids:
            for group_id in user.group_ids:
                user_group_detials = await get_rbac_groups_by_id(db, user.organization_id, group_id)
                print(f'details == {user_group_detials}')
                if user_group_detials:
                    user_groups_ids.append({"id":group_id, "name":user_group_detials.name})
                # if user_group_detials:
                #     user_groups.extend(user_group_detials.attributes)
        user_groups, _ = await format_user_chatbot_permissions(db, user.organization_id, user.group_ids)
    
        users_response.append(
            OrganizationListResponse(
                id=user.id,
                name=user.name,
                email=user.email,
                total_messages=user.total_messages,
                is_active=user.is_active,
                created_at=user.created_at,
                updated_at=user.updated_at,
                groups=user_groups,
                role = user.role,
                group_ids = user_groups_ids
            )
        )
    result = await db.execute(select(User).filter(User.organization_id == organization_id))
    users = result.scalars().all()

    org_data = select(Organization).filter(Organization.id == organization_id)
    org_result = await db.execute(org_data)
    organization_data = org_result.scalar_one_or_none()

    if super_admin_api:
        print(f'add admin')
        
        users_response.append(
            OrganizationListResponse(
                id=current_user.id,
                name=current_user.name,
                email=current_user.email,
                total_messages=current_user.total_messages,
                is_active=current_user.is_active,
                created_at=current_user.created_at,
                updated_at=current_user.updated_at,
                role = current_user.role,
                groups= []  
            )
        )
        return OrganizationUsersResponse(
            organization_name=organization_data.name,
            organization_id=organization_data.id,
            users_data=users_response,
            total_users=len(users)
        )
    
    response = OrganizationUsersResponse(
        organization_name=organization_data.name,
        organization_id=organization_data.id,
        users_data=users_response,
        total_users=len(users) -1
    )
    return response
    
async def get_user_name_email_by_id(db, organization_id, user_id, current_user):
    await _role_based_checks(current_user, organization_id)
    # Get user
    user = await get_user_by_id(db, user_id)

    if not user:
        raise HTTPException(status_code=404, detail="User does not exist")
    
    # Verify user belongs to organization
    if user.organization_id != organization_id:
        raise HTTPException(status_code=403, detail="User not in this organization")
    
    return user

async def update_organization_user_status_service(
    db: AsyncSession,
    organization_id: int,
    user_id: int,
    user_data: OrganizationUserUpdate,
    current_user: User
) -> User:
    """Update user status in organization (Admin/Super Admin only)"""
   
    await _role_based_checks(current_user, organization_id)
    # Get user
    user = await get_user_by_id(db, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    user_group_ids = []
    user_group_response = []
    for group_id in user_data.group_ids:
        user_group_detials = await get_rbac_groups_by_id(db, organization_id, group_id)
        if user_group_detials:
            user_group_ids.append(group_id)
            user_group_response.append({'id':group_id,"name":user_group_detials.name})
    user_data.group_ids.clear()
    user_data.group_ids = user_group_ids
    # Verify user belongs to organization
    if user.organization_id != organization_id:
        raise HTTPException(status_code=403, detail="User not in this organization")
    
    # Update user fields
    update_data = user_data.dict(exclude_unset=True)
    # Check for password change and handle it separately
    if "password" in update_data:
        if update_data["password"] != update_data.get("confirm_password"):
            raise HTTPException(status_code=400, detail="Passwords do not match")
        security_manager = SecurityManager()
        hashed_password = security_manager.get_password_hash(password=update_data["password"])
        update_data["hashed_password"] = hashed_password
        update_data.pop("password")  
        update_data.pop("confirm_password")

    # user_chatbots = []
    # for chatid in user_data.chatbot_ids:
    #         chatbots_query = select(ChatbotConfig).filter(ChatbotConfig.id == chatid)
    #         chatbots_result = await db.execute(chatbots_query)
    #         chatbot = chatbots_result.scalar_one_or_none()
    #         if chatbot:
    #             # user_chatbots.append(ChatbotInfo(
                #     chatbot_id=int(chatbot.id),
                #     chatbot_name=str(chatbot.chatbot_name)
                # ))
                # user_chatbots.append(ChatbotInfo(
                # chatbot_id=int(chatbot.id),
                # chatbot_name=str(chatbot.chatbot_name)
                # ).model_dump())
    
    for key, value in update_data.items():
        setattr(user, key, value)
    
    await db.commit()
    await db.refresh(user)
    # user_groups = []
    # for group_id in user.group_ids:
    #     user_group_detials = await get_rbac_groups_by_id(db, user.organization_id, group_id)
    #     print(f'details == {user_group_detials.attributes}')
    #     user_groups.extend(user_group_detials.attributes)
    user_groups, _ = await format_user_chatbot_permissions(db, user.organization_id, user.group_ids)
    
    # user_chatbots = []
    # for chatid in user.chatbot_ids:
    #     chatbots_query = select(ChatbotConfig).filter(ChatbotConfig.id == chatid)
    #     chatbots_result = await db.execute(chatbots_query)
    #     chatbot = chatbots_result.scalar_one_or_none()
    #     if chatbot:
    #         if chatbot.chatbot_type == 'Internal':
    #             user_chatbots.append(ChatbotInfo(
    #                 chatbot_id=chatbot.id,
    #                 chatbot_name=chatbot.chatbot_name
    #             ))

    users_response=OrganizationListResponse(
                id=user.id,
                name=user.name,
                email=user.email,
                total_messages=user.total_messages,
                is_active=user.is_active,
                created_at=user.created_at,
                updated_at=user.updated_at,
                groups=user_groups,
                group_ids = user_group_response
            )
    return users_response

async def remove_user_from_organization_service(
    db: AsyncSession,
    organization_id: int,
    user_id: int,
    current_user: User
) -> None:
    """Remove a user from an organization (Admin/Super Admin only)"""
    await _role_based_checks(current_user, organization_id)
    
    await delete_organization_user(db, organization_id, user_id)

from app.schemas.request.chatbot_config import BotType

async def create_organization_chatbot_service(
    db: AsyncSession,
    organization_id: int,
    chatbot_data: CreateData,
    current_user: User,
    avatar, 
    document_files,
    website_links,
    guardrails_list,
    qa_templates,
    bot_type = None
) -> ChatbotConfig:
    """Create a chatbot with permission check"""
    # Force role to be string for comparison
    if current_user.role == UserRole.SUPER_ADMIN:
        user_data = await get_user_organization_admin(db = db, organization_id = organization_id)
        user_role = user_data.role
    else:
        user_role = current_user.role

    # Explicit role check
    if user_role == UserRole.USER:
        raise HTTPException(
            status_code=403,
            detail=f"Regular users cannot create chatbots. Your role: {user_role}"
        )
    
    # Admin organization check
    if user_role == UserRole.ADMIN and current_user.organization_id != organization_id:
        raise HTTPException(
            status_code=403,
            detail="You can only create chatbots for your organization"
        )
    
    if user_role == UserRole.ADMIN and current_user.current_plan == Plan.starter:
        raise HTTPException(
            status_code=403,
            detail="Please upgrade your plan."
        )
    
    query = select(ChatbotConfig).filter(
        ChatbotConfig.organization_id == organization_id
    )
    
    result = await db.execute(query)
    admin_chatbots = result.scalars().all()

    chatbotnames = [chatbot.chatbot_name for chatbot in admin_chatbots]
    if chatbot_data.chatbot_name in chatbotnames:
        raise HTTPException(
            status_code=403,
            detail="You can't create chatbot with same name."
        )
    
    print(f"bots == {len(admin_chatbots) - 1} bot env = {envs.FREE_TIER_CHATBOTS} active option = {current_user.is_active}")
    if user_role == UserRole.ADMIN:
        print(f'inside admin')
        if current_user.current_plan == Plan.free:
            print(f'inside free')
            raise HTTPException(
                    status_code=400,
                    detail="Free users without a paid plan cannot create a chatbot."
                )
            # if not current_user.is_paid:
            #     raise HTTPException(
            #         status_code=400,
            #         detail="Free users without a paid plan cannot create a chatbot."
            #     )
            # if (len(admin_chatbots) - 1) >= envs.FREE_TIER_CHATBOTS:
            #     raise HTTPException(
            #         status_code=400,
            #         detail="Please upgrade your plan to create a chatbot."
            #     )
           
    #(admin_chatbots - 1) -1 to remove the external chatbot
    # if (len(admin_chatbots) - 1) >= envs.TEIR2_CHATBOTS and current_user.current_plan == Plan.tier_2:
    #     raise HTTPException(
    #         status_code=403,
    #         detail="You have reached your chatbots limit please upgrade your plan."
    #     )
    if (len(admin_chatbots)- 1) >= envs.TEIR3_CHATBOTS and current_user.current_plan == Plan.starter:
        raise HTTPException(
            status_code=403,
            detail="You have reached your chatbots limit."
        )
    if bot_type == BotType.teacher:
        from app.services.user import create_student_agent
        print(f'bot type -- ={bot_type}')
        chatbot = await create_student_agent(
            org_id = organization_id,
            db = db,
            llm_name = chatbot_data.llm_model_name,
            llm_tone = chatbot_data.llm_role,
            bot_type = bot_type
        )
    else:
        # Create chatbot
        chatbot = ChatbotConfig(
            organization_id=organization_id,
            llm_model_name=chatbot_data.llm_model_name,
            llm_temperature=chatbot_data.llm_temperature,
            llm_prompt=chatbot_data.llm_prompt,
            llm_role=chatbot_data.llm_role,
            prompt_status = "Uploaded",
            chatbot_type = 'Internal',
            chatbot_name = chatbot_data.chatbot_name,
            avatar_url=avatar,
            llm_streaming=chatbot_data.llm_streaming if chatbot_data.llm_streaming is not None else True
        )
        db.add(chatbot)
        await db.commit()
        await db.refresh(chatbot)
    if chatbot_data.llm_prompt:
        message_body = {
            "url": chatbot_data.llm_prompt,
            "org_id": chatbot.organization_id,
            "chatbot_id": chatbot.id,
            "domain_sweep": False,
            "content_source": "prompt"
        }
            # Send message to SQS
        response = sqs_client.send_message(
            QueueUrl=envs.SQS_QUEUE_URL,
            MessageBody=json.dumps(message_body),
            MessageGroupId=f"{str(chatbot.id)}-train",
            MessageDeduplicationId=str(uuid.uuid4())
        )
        print(f"Sent to SQS: {response['MessageId']}")

    if document_files:
        for document_file in document_files:
    
            # document_filename = f"{document_file.filename}"
            # document_s3_key = f"uploaded_file_doc/{chatbot_data.organization_id}/{chatbot.id}/{document_filename}"

            # content = await document_file.read()
            try:
                # Upload file to S3
                # s3_client.put_object(
                #     Bucket=envs.BUCKET_NAME,  # Ensure the environment variable is set
                #     Key=document_s3_key,
                #     Body=content,
                #     ContentType=document_file.content_type
                # )
                # print(f"File uploaded to S3: {document_s3_key}")

                # Save file metadata to DB
                file_extension = os.path.splitext(document_file)[1][1:]
                # document_file => https://marti-file-upload-bucket.s3.us-east-2.amazonaws.com/uploaded_file_doc/2/2/filename.docx
                await insert_document_entry(
                    chatbot.id, document_file, file_extension, 'Uploaded', db
                )


            except NoCredentialsError:
                print("AWS Credentials not found! Ensure they are set in environment variables.")
                raise HTTPException(status_code=500, detail="S3 upload failed due to missing credentials")

            # with open(document_path, "wb") as f:
            #     f.write(content)
            # print(f'file deatils {document_file.filename}, {document_file.content_type}')
            # await insert_document_entry(chatbot.id, document_file.filename, document_file.content_type,'Uploaded',db)

    if website_links:
        for website_link in website_links:
            if website_link.sweep_domain:
                entery_added = await insert_webscrap_entry(chatbot_id = chatbot.id, url = normalize_url(website_link.website_link), sweap_domain = website_link.sweep_domain, content_type = 'url',status = 'Uploaded',session = db)
            else:
                entery_added = await insert_webscrap_entry(chatbot_id = chatbot.id, url = normalize_url(website_link.website_link), sweap_domain = False, content_type = 'url',status = 'Uploaded',session = db)
            message_body = {
                "url": website_link.website_link,
                "org_id": chatbot.organization_id,
                "chatbot_id": chatbot.id,
                "domain_sweep": website_link.sweep_domain if website_link.sweep_domain else False,
                "content_source": "url",
                "database_id":entery_added.id
            }
                # Send message to SQS
            response = sqs_client.send_message(
                QueueUrl=envs.SQS_QUEUE_URL,
                MessageBody=json.dumps(message_body),
                MessageGroupId=f"{str(chatbot.id)}-url",
                MessageDeduplicationId=str(uuid.uuid4())
            )
            print(f"Sent to SQS: {response['MessageId']}")
    # Save the avatar file or process it
    
    # if avatar:
    #     avatar_file = avatar
    #     avatar_filename = f"{organization_id}_{chatbot.id}_{avatar_file.filename}"
    #     avatar_path = f"avatars/{avatar_filename}"
        
    #     os.makedirs(os.path.dirname(avatar_path), exist_ok=True)
    #     content = await avatar_file.read()
    #     with open(avatar_path, "wb") as f:
    #         f.write(content)
            
    #     chatbot.avatar_url = avatar_path
    #     db.add(chatbot)
    #     await db.commit()
    #     await db.refresh(chatbot)
    if not guardrails_list:
        guardrails_list = ["Avoid answering questions about religions."]
        for rails in guardrails_list:
            guardrails = ChatbotGuardrail(
                guardrail_text=rails,
                chatbot_id = chatbot.id
            )
            db.add(guardrails)
            await db.commit()
            await db.refresh(guardrails)
    else:
        for rails in guardrails_list:
            guardrails = ChatbotGuardrail(
                guardrail_text=rails,
                chatbot_id = chatbot.id
            )
            db.add(guardrails)
            await db.commit()
            await db.refresh(guardrails)

            # message_body = {
            #     "url": guardrails.guardrail_text,
            #     "org_id": chatbot.organization_id,
            #     "chatbot_id": guardrails.chatbot_id,
            #     "domain_sweep": guardrails.id,
            #     "content_source": "guardrails"
            # }
            # # Send message to SQS
            # response = sqs_client.send_message(
            #     QueueUrl=envs.SQS_QUEUE_URL,
            #     MessageBody=json.dumps(message_body),
            #     MessageGroupId=f"{str(chatbot.id)}-gr-{guardrails.id}",
            #     MessageDeduplicationId=str(uuid.uuid4())
            # )
            # print(f"Sent to SQS: {response['MessageId']}")
            
    await db.commit()

    count = 0
    if qa_templates:
        for qa in qa_templates:
            qa_template = QATemplate(
                chatbot_id=chatbot.id,
                question=qa.question,
                answer=qa.answer,
                status = "Uploaded"
            )
            db.add(qa_template)
        await db.commit()
        result = await db.execute(select(QATemplate).filter(QATemplate.chatbot_id == chatbot.id))
        qa_templates = result.scalars().all()
        for qa_template in qa_templates:
            qa_body = f"Question: {qa.question}\nAnswer: {qa.answer}"
            message_body = {
                "url": qa_body,
                "org_id": chatbot.organization_id,
                "chatbot_id": chatbot.id,
                "domain_sweep": qa_template.id,
                "content_source": "qa_pair"
            }
                # Send message to SQS
            response = sqs_client.send_message(
                QueueUrl=envs.SQS_QUEUE_URL,
                MessageBody=json.dumps(message_body),
                MessageGroupId=f"{str(chatbot.id)}-qa-{count}",
                MessageDeduplicationId=str(uuid.uuid4())
            )
            print(f"Sent to SQS: {response['MessageId']}")
            count += 1

    await db.commit()
    return chatbot

async def list_platform_pre_existing_agents(current_user):
    if current_user.role not in [UserRole.ADMIN, UserRole]:
        raise HTTPException(
            status_code=status.HTTP_203_NON_AUTHORITATIVE_INFORMATION,
            detail="Unautherize Operation."
        )
    pre_existing_chatbots = [
            {
                "chatbot_type": "Internal",
                "chatbot_name": "Student Agent",
                "avatar_url": "",
                # "llm_model_name": "gpt-4"
            },
            {
                "chatbot_type": "Internal",
                "chatbot_name": "Teacher Agent",
                "avatar_url": "",
                # "llm_model_name": "gpt-4"
            }
        ]
    return pre_existing_chatbots

async def list_organization_chatbots_service(
    db: AsyncSession,
    organization_id: int,
    current_user: User,
    cateogry_indicator : int = 0 #1-insight, 2-chatlog
) -> List[ChatbotConfig]:
    """List chatbots with permission check"""
    
    if current_user.role == UserRole.USER:
        
        if not current_user.group_ids:
            print(f'no group ids found')
            return []
        chatbot_ids = []
        user_groups, _ = await format_user_chatbot_permissions(db, current_user.organization_id, current_user.group_ids)
        for bot_info in user_groups:
            if cateogry_indicator == 0:
                if bot_info['chatbot_id'] not in chatbot_ids:
                    chatbot_ids.append(bot_info['chatbot_id'])
            if cateogry_indicator == 1:
                if bot_info['can_view_insight'] and bot_info['chatbot_id'] not in chatbot_ids:
                    chatbot_ids.append(bot_info['chatbot_id'])
            if cateogry_indicator == 2:
                if bot_info['can_view_chat_logs'] and bot_info['chatbot_id'] not in chatbot_ids:
                    chatbot_ids.append(bot_info['chatbot_id'])
            
        if not chatbot_ids:
            return []     
        
        # for ids in current_user.chatbot_ids:
        query = (select(ChatbotConfig)
                    .filter(
                        ChatbotConfig.organization_id == organization_id,
                        ChatbotConfig.id.in_(chatbot_ids)  
                    )
                )
            
    # Super admin can view all chatbots
    elif current_user.role == UserRole.SUPER_ADMIN:
        query = select(ChatbotConfig).filter(ChatbotConfig.organization_id == organization_id)
    elif current_user.role == UserRole.ADMIN:
        # Regular users and admins can only view their organization's chatbots
        if current_user.organization_id != organization_id:
            raise HTTPException(
                status_code=403,
                detail="You can only view chatbots from your organization"
            )
        query = select(ChatbotConfig).filter(ChatbotConfig.organization_id == organization_id)
    
    else:
        return []
    
    result = await db.execute(query)
    return result.scalars().all()

def get_s3_file_size(file_url):
    parsed_url = urlparse(file_url)
    
    # Extract key
    key = parsed_url.path.lstrip('/')  # remove leading slash
    
    try:
        response = s3_client.head_object(Bucket=envs.BUCKET_NAME, Key=key)
        size_bytes = response['ContentLength']
        size_kb = round(size_bytes / 1024, 2)
        print(f'file size == {[size_kb]}')
        return size_kb
    except Exception as e:
        print(f"Error retrieving file size: {e}")
        return 0.0

async def get_total_chatbot_filesize(
        db: AsyncSession,
    organization_id: int,
    chatbot_id: int,
    current_user: User
):
    # await _role_based_checks(current_user, organization_id)
    if current_user.role == UserRole.USER:
        if not current_user.group_ids:
            raise HTTPException(status_code=404, detail="You don't have permissions.")
      
        user_groups, _ = await format_user_chatbot_permissions(db, current_user.organization_id, current_user.group_ids)
        
        bot_details = None
        for bot_info in user_groups:
            if bot_info['chatbot_id'] == chatbot_id:
                bot_details = bot_info
                break
        print(f'bot info == {bot_details}')
        if not bot_details:
            raise HTTPException(status_code=404, detail="Unautherized operation")
        await _fine_grain_role_checks(current_user, organization_id, chatbot_id, bot_details, "can_edit_fileupload")
    else:
        await _fine_grain_role_checks(current_user, organization_id)

    query = (
        select(ChatbotConfig)
        .filter(
            ChatbotConfig.id == chatbot_id,
            ChatbotConfig.organization_id == organization_id
        )
    )
    
    result = await db.execute(query)
    chatbot = result.scalar_one_or_none()
    if not chatbot:
        raise HTTPException(status_code=404, detail="Chatbot not found")
    # Fetch documents
    documents_query = (
        select(ChatbotDocument)
        .filter(ChatbotDocument.chatbot_id == chatbot_id)
    )
    documents_result = await db.execute(documents_query)
    documents_data = documents_result.scalars().all()
    total_size_in_kb = 0
    for doc in documents_data:
        if doc.content_type != "url":
            parsed_url = urlparse(doc.document_name)
            file_name = parsed_url.path.split("/")[-1]
            file_size_kb = get_s3_file_size(doc.document_name)
            total_size_in_kb += file_size_kb

    return {"total_files_size":total_size_in_kb}

async def get_organization_chatbot_service(
    db: AsyncSession,
    organization_id: int,
    chatbot_id: int,
    current_user: User
) -> ChatbotConfig:
    """Get specific chatbot with permission check"""
    try:
        if current_user.role == UserRole.USER:
            if not current_user.group_ids:
                raise HTTPException(status_code=404, detail="You don't have permission.")
            # for group_id in current_user.group_ids:
            #     #Extract gropu information and extract ids from groups.
            #     group_details = await get_rbac_groups_by_id(db, current_user.organization_id, group_id)
            user_groups, _ = await format_user_chatbot_permissions(db, current_user.organization_id, current_user.group_ids)
            bot_details = None
            for bot_info in user_groups:
                if bot_info['chatbot_id'] == chatbot_id:
                    bot_details = bot_info
                    break
            print(f'bot info == {bot_details}')
            if bot_details:
                await _fine_grain_role_checks(current_user, organization_id, chatbot_id, bot_details)
        else:
            await _fine_grain_role_checks(current_user, organization_id)
        query = (
            select(ChatbotConfig)
            .filter(
                ChatbotConfig.id == chatbot_id,
                ChatbotConfig.organization_id == organization_id
            )
        )
        
        result = await db.execute(query)
        chatbot = result.scalar_one_or_none()
        if not chatbot:
            raise HTTPException(status_code=404, detail="Chatbot not found")
        
        # Fetch documents
        documents_query = (
            select(ChatbotDocument)
            .filter(ChatbotDocument.chatbot_id == chatbot_id)
        )
        documents_result = await db.execute(documents_query)
        documents_data = documents_result.scalars().all()

        # Fetch QA templates
        qa_templates_query = (
            select(QATemplate)
            .filter(QATemplate.chatbot_id == chatbot_id)
        )
        qa_templates_result = await db.execute(qa_templates_query)
        qa_templates = qa_templates_result.scalars().all()

        # Fetch Guardrails
        guardrails_query = (
            select(ChatbotGuardrail)
            .filter(ChatbotGuardrail.chatbot_id == chatbot_id)
        )
        guardrails_result = await db.execute(guardrails_query)
        guardrails_data = guardrails_result.scalars().all()

        # Separate documents and URLs
        bot_documents = []
        urls = []
        qa_templates_data = []
        current_time = datetime.utcnow()
        for doc in documents_data:
            if doc.content_type == "url":  
                urls.append(
                    WebscrapUrl(
                        website_link=doc.document_name,
                        website_url_id=doc.id,
                        sweep_domain=(doc.url_sweep_option.lower() == "domain"),
                        sweep_url=(doc.url_sweep_option.lower() == "website_page"),
                        status = doc.status
                    )
                )
            else:
                # doc.document_name => https://marti-file-upload-bucket.s3.us-east-2.amazonaws.com/uploaded_file_doc/2/2/filename.docx
                parsed_url = urlparse(doc.document_name)
                file_name = parsed_url.path.split("/")[-1]
                file_size_kb = get_s3_file_size(doc.document_name)

                bot_documents.append(
                    DocumentInfo(
                        document_name=file_name,
                        document_id=doc.id,
                        document_status = doc.status,
                        document_size = file_size_kb
                    )
                )
            if doc.status == "Failed":
                await db.delete(doc)
            elif doc.status == "Uploaded":
                if current_time - doc.created_at > timedelta(minutes=5):
                    print(f'delted the entry on upload status.')
                    await db.delete(doc)

        for qa in qa_templates:
            qa_templates_data.append(
                QATemplateData(
                    question=qa.question,
                    answer=qa.answer,
                    id = qa.id,
                    status = qa.status
                )
            )
            if qa.status == "Failed":
                await db.delete(doc)

            elif qa.status == "Uploaded": # and doc.updated_at
                # if current_time - doc.updated_at > timedelta(minutes=5):
                await db.delete(qa)

        guard_rails_data_prepared = [
                GetGuardrails(
                    guardrail=ga.guardrail_text,
                    id = ga.id
                )
                for ga in guardrails_data
            ]
        
        await db.commit()
        return ChatbotConfigResponse(
            id=chatbot.id,
            llm_model_name=chatbot.llm_model_name,
            llm_temperature=chatbot.llm_temperature,
            llm_prompt=chatbot.llm_prompt,
            llm_role=chatbot.llm_role,
            status=chatbot.prompt_status,
            llm_streaming=chatbot.llm_streaming,
            chatbot_type=chatbot.chatbot_type,
            chatbot_name=chatbot.chatbot_name,
            avatar=chatbot.avatar_url,
            website_url=urls,
            bot_documents=bot_documents,
            qa_templates=qa_templates_data,
            guardrails=guard_rails_data_prepared
        )
    except HTTPException as http_ex:
        raise http_ex  
    except Exception as ex:
        await db.rollback() 
        raise HTTPException(status_code=500, detail=f"There is some errro while retriving bot details.")

async def get_qa_templates_service(
    db: AsyncSession,
    organization_id: int,
    chatbot_id: int,
    current_user: User,
    skip: int,
    limit: int,
) -> List[QATemplateData]:
    if current_user.role == UserRole.USER:
        if not current_user.group_ids:
            raise HTTPException(status_code=404, detail="You don't have permissions.")
        user_groups, _ = await format_user_chatbot_permissions(db, current_user.organization_id, current_user.group_ids)
        if not any(bot['chatbot_id'] == chatbot_id for bot in user_groups):
            raise HTTPException(status_code=403, detail="Access to chatbot denied.")
    else:
        await _fine_grain_role_checks(current_user, organization_id)

    total_query = (
        select(QATemplate)
        .filter(QATemplate.chatbot_id == chatbot_id)
    )
    total_result = await db.execute(total_query)
    total_qa_templates = total_result.scalars().all()
    
    query = (
        select(QATemplate)
        .filter(QATemplate.chatbot_id == chatbot_id)
        .offset(skip)
        .limit(limit)
    )
    result = await db.execute(query)
    qa_templates = result.scalars().all()
    current_time = datetime.utcnow()
    filtered_results = []
    for qa in qa_templates:
        if qa.status == "Failed":
            await db.delete(qa)

        # elif qa.status == "Uploaded": # and doc.updated_at
        #     if current_time - qa.updated_at > timedelta(minutes=5):
        #         await db.delete(qa)
        filtered_results.append(QATemplateData(
            question=qa.question,
            answer=qa.answer,
            id=qa.id,
            status=qa.status,
            # qa_db_id = qa.message_id,
            # qa_thrd_id = qa.thread_id
        ))
    return filtered_results, len(total_qa_templates)

async def update_organization_chatbot_details(
    db: AsyncSession,
    organization_id: int,
    chatbot_id: int,
    chatbot_name,
    chatbot_role,
    current_user: User,
    avatar
):
    await _role_based_checks(current_user, organization_id)
    # Get the chatbot first
    query = select(ChatbotConfig).filter(
        ChatbotConfig.id == chatbot_id,
        ChatbotConfig.organization_id == organization_id
    )
    
    result = await db.execute(query)
    chatbot = result.scalar_one_or_none()
    
    if not chatbot:
        raise HTTPException(status_code=404, detail="Chatbot not found")
    
    chatbot.chatbot_name = chatbot_name
    chatbot.llm_role = chatbot_role
    db.add(chatbot)
    await db.commit()
    await db.refresh(chatbot)
    if avatar:
        # avatar_file = avatar
        # avatar_filename = f"{organization_id}_{chatbot.id}_{avatar_file.filename}"
        # avatar_path = f"avatars/{avatar_filename}"
        
        # os.makedirs(os.path.dirname(avatar_path), exist_ok=True)
        # content = await avatar_file.read()
        # with open(avatar_path, "wb") as f:
        #     f.write(content)
            
        chatbot.avatar_url = avatar
        db.add(chatbot)
        await db.commit()
        await db.refresh(chatbot)

from sqlalchemy.exc import SQLAlchemyError


async def get_organization_image_generation_chabot_name(
        db: AsyncSession,
):
        try:
            query = select(SuperAdminModel).filter(SuperAdminModel.id == 2)
            result = await db.execute(query)
            chatbot_config = result.scalar_one_or_none()

            # If no record exists, create one with the default model
            if not chatbot_config:
                chatbot_config = SuperAdminModel(id=2, llm_model_name="dall-e-3")
                db.add(chatbot_config)
                await db.commit()
                await db.refresh(chatbot_config)

            return chatbot_config.llm_model_name
        except SQLAlchemyError as db_err:
            await db.rollback()
            raise HTTPException(status_code=500, detail=f"Database error: {str(db_err)}")
        except Exception as e:
            await db.rollback()
            raise HTTPException(status_code=500, detail=f"An unexpected error occurred: {str(e)}")


async def get_organization_chabot_name(
        db: AsyncSession,
):
        try:
            query = select(SuperAdminModel).filter(SuperAdminModel.id == 1)
            result = await db.execute(query)
            chatbot_config = result.scalar_one_or_none()

            # If no record exists, create one with the default model
            if not chatbot_config:
                chatbot_config = SuperAdminModel(id=1, llm_model_name="gpt-4o-mini")
                db.add(chatbot_config)
                await db.commit()
                await db.refresh(chatbot_config)

            return chatbot_config.llm_model_name
        except SQLAlchemyError as db_err:
            await db.rollback()
            raise HTTPException(status_code=500, detail=f"Database error: {str(db_err)}")
        except Exception as e:
            await db.rollback()
            raise HTTPException(status_code=500, detail=f"An unexpected error occurred: {str(e)}")


async def update_image_generation_organization_chatbot_model_name(
        db: AsyncSession,
        model_name: str,
):
    try:
        query = select(SuperAdminModel).filter(
            SuperAdminModel.id == 2,
        )
        
        result = await db.execute(query)
        llm_data = result.scalar_one_or_none()
        
        if not llm_data:
            llm_data = SuperAdminModel(id=1, llm_model_name=model_name)
            db.add(llm_data)
        else:
            # If record exists, update the model name
            llm_data.llm_model_name = model_name
        await db.commit()
        
        return
    except HTTPException as http_err:
        raise http_err
    except SQLAlchemyError as db_err:
        await db.rollback() 
        raise HTTPException(status_code=500, detail=f"Database error: {str(db_err)}")
    except Exception as e:
        await db.rollback() 
        raise HTTPException(status_code=500, detail=f"An unexpected error occurred: {str(e)}")


async def update_organization_chatbot_model_name(
        db: AsyncSession,
        model_name: str,
):
    try:
        query = select(SuperAdminModel).filter(
            SuperAdminModel.id == 1,
        )
        
        result = await db.execute(query)
        llm_data = result.scalar_one_or_none()
        
        if not llm_data:
            llm_data = SuperAdminModel(id=1, llm_model_name=model_name)
            db.add(llm_data)
        else:
            # If record exists, update the model name
            llm_data.llm_model_name = model_name
        await db.commit()
        
        return
    except HTTPException as http_err:
        raise http_err
    except SQLAlchemyError as db_err:
        await db.rollback() 
        raise HTTPException(status_code=500, detail=f"Database error: {str(db_err)}")
    except Exception as e:
        await db.rollback() 
        raise HTTPException(status_code=500, detail=f"An unexpected error occurred: {str(e)}")

async def update_organization_chatbot_traning_text(
        db: AsyncSession, 
        organization_id: int,
        chatbot_id: int,
        training_text: str,
        current_user: User
):
    if current_user.role == UserRole.USER:
        if not current_user.group_ids:
            raise HTTPException(status_code=404, detail="You don't have permissions.")
        #for group_id in current_user.group_ids:
            #Extract gropu information and extract ids from groups.
            # group_details = await get_rbac_groups_by_id(db, current_user.organization_id, group_id)
        user_groups, _ = await format_user_chatbot_permissions(db, current_user.organization_id, current_user.group_ids)
        bot_details = None
        for bot_info in user_groups:
            if bot_info['chatbot_id'] == chatbot_id:
                bot_details = bot_info
                break
        print(f'bot info == {bot_details}')
        if bot_details:
            await _fine_grain_role_checks(current_user, organization_id, chatbot_id, bot_details, "can_edit_traning_text")
    else:
        await _fine_grain_role_checks(current_user, organization_id)

    # await _role_based_checks(current_user, organization_id)
    # Get the chatbot first
    query = select(ChatbotConfig).filter(
        ChatbotConfig.id == chatbot_id,
        ChatbotConfig.organization_id == organization_id
    )
    
    result = await db.execute(query)
    chatbot = result.scalar_one_or_none()
    
    if not chatbot:
        raise HTTPException(status_code=404, detail="Chatbot not found")
    
    chatbot.llm_prompt = training_text
    chatbot.prompt_status = 'Uploaded'
    db.add(chatbot)
    await db.commit()
    await db.refresh(chatbot)
    message_body = {
        "url": training_text,
        "org_id": chatbot.organization_id,
        "chatbot_id": chatbot.id,
        "domain_sweep": False,
        "content_source": "prompt",
        "request_type": "Update"
    }
    response = sqs_client.send_message(
        QueueUrl=envs.SQS_QUEUE_URL,
        MessageBody=json.dumps(message_body),
        MessageGroupId=f"{str(chatbot.id)}-train",
        MessageDeduplicationId=str(uuid.uuid4())
    )
    print(f"Sent to SQS: {response['MessageId']}")
    
    return chatbot

from sqlalchemy import func

async def get_total_guardrails_for_chatbot(db_session, chatbot_id: int) -> int:
    stmt = select(func.count(ChatbotGuardrail.id)).filter(ChatbotGuardrail.chatbot_id == chatbot_id)
    result = await db_session.execute(stmt)
    total_rails = result.scalar()  # Returns the count as an integer
    return total_rails

async def get_total_memory_for_chatbot(db_session, chatbot_id: int) -> int:
    stmt = select(func.count(ChatbotMemory.id)).filter(ChatbotMemory.chatbot_id == chatbot_id)
    result = await db_session.execute(stmt)
    total_rails = result.scalar()  # Returns the count as an integer
    return total_rails

async def get_total_documents_for_chatbot(db_session, chatbot_id: int, uploaded_files = False) -> int:
    stmt = select(func.count(ChatbotDocument.id)).filter(ChatbotDocument.chatbot_id == chatbot_id)
    if not uploaded_files:
        stmt = stmt.filter(ChatbotDocument.content_type == "url")
    else:
        stmt = stmt.filter(ChatbotDocument.content_type != "url")
    result = await db_session.execute(stmt)
    total_documents = result.scalar()  # Returns the count as an integer
    return total_documents

def get_base_url(url: str) -> str:
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc.lower()}"



async def check_if_file_exist(
        db: AsyncSession, 
        organization_id: int,
        request,
        current_user: User
    ):
    # await _role_based_checks(current_user, organization_id)
    # Normalize URL — optional but smart
    if current_user.role == UserRole.USER:
        if not current_user.group_ids:
            raise HTTPException(status_code=404, detail="You don't have permissions.")
      
        user_groups, _ = await format_user_chatbot_permissions(db, current_user.organization_id, current_user.group_ids)
        
        bot_details = None
        for bot_info in user_groups:
            if bot_info['chatbot_id'] == request.chatbot_id:
                bot_details = bot_info
                break
        print(f'bot info == {bot_details}')
        if not bot_details:
            raise HTTPException(status_code=404, detail="Unautherized operation")
        await _fine_grain_role_checks(current_user, organization_id, request.chatbot_id, bot_details, "can_edit_webscrap")
    else:
        await _fine_grain_role_checks(current_user, organization_id)

    normalized_url = normalize_url(str(request.url))
    query = select(ChatbotDocument).where(
        ChatbotDocument.chatbot_id == request.chatbot_id,
        # ChatbotDocument.document_name == normalized_url,
        # ChatbotDocument.url_sweep_option.ilike(request.sweep_option.value)
    )
    result = await db.execute(query)
    documents = result.scalars().all()

    base_url = get_base_url(str(request.url))
    for doc in documents:
        if doc.url_sweep_option.lower() == "website_page" and request.sweep_option.value == "website_page":
            if normalize_url(doc.document_name) == normalized_url:
                return {"exists": True, "message": "Exact URL already exists with page sweep."}
        elif doc.url_sweep_option.lower() == "domain":
            doc_base_url = get_base_url(doc.document_name)
            if doc_base_url == base_url:
                return {"exists": True, "message": "URL with same base domain already exists with domain sweep."}
    return {"exists": False}

async def update_organization_chatbot_files(
        db: AsyncSession, 
        organization_id: int,
        chatbot_id: int,
        document_added,
        document_removed,
        website_added,
        website_removed,
        current_user: User
    ):
    try:

        if current_user.role == UserRole.USER:
            if not current_user.group_ids:
                raise HTTPException(status_code=404, detail="You don't have permissions.")
            # for group_id in current_user.group_ids:
                #Extract gropu information and extract ids from groups.
                # group_details = await get_rbac_groups_by_id(db, current_user.organization_id, group_id)
            user_groups, _ = await format_user_chatbot_permissions(db, current_user.organization_id, current_user.group_ids)
        
            bot_details = None
            for bot_info in user_groups:
                if bot_info['chatbot_id'] == chatbot_id:
                    bot_details = bot_info
                    break
            print(f'bot info == {bot_details}')
            if not bot_details:
                raise HTTPException(status_code=404, detail="Unautherized operation")
        
            # if bot_details:
            #     await _fine_grain_role_checks(current_user, organization_id, chatbot_id, bot_details, "can_edit_traning_text")
                
            if document_added or document_removed:
                await _fine_grain_role_checks(current_user, organization_id, chatbot_id, bot_details, "can_edit_fileupload")
            if website_added or website_removed:
                await _fine_grain_role_checks(current_user, organization_id, chatbot_id, bot_details, "can_edit_webscrap")
        else:
            await _fine_grain_role_checks(current_user, organization_id)

        # await _role_based_checks(current_user, organization_id)
        # Get the chatbot first
        query = select(ChatbotConfig).filter(
            ChatbotConfig.id == chatbot_id,
            ChatbotConfig.organization_id == organization_id
        )
        
        result = await db.execute(query)
        chatbot = result.scalar_one_or_none()
        
        if not chatbot:
            raise HTTPException(status_code=404, detail="Chatbot not found")
        
        #check on total number of links
        if website_added:
            for website_link in website_added:
                from app.utils.user_helpers import extract_domain_urls
                if website_link.sweep_domain:
                    total_links = extract_domain_urls(website_link.website_link)
                    if len(total_links) > 200:
                        raise HTTPException(status_code=400, detail=f"Your domain has more than 200 links.")
        # Remove documents
        if document_removed:
            for document in document_removed: 
                # Assuming the documents are stored with a unique filename and path
                # document_path = f"documents/{organization_id}_{chatbot.id}_{document.document_name}"
                # if os.path.exists(document_path):
                #     os.remove(document_path)  # Delete the file from the filesystem
                
                await delete_document_entry(document.document_id, db)  # Delete database entry
                delete_message_body = {
                    "org_id": organization_id,
                    "chatbot_id": chatbot_id,
                    "database_id": document.document_name,
                    "content_source": "file"
                }
                print(f"envs.DELETE_SQS_URL == {envs.DELETE_SQS_URL}")

                response = sqs_client.send_message(
                    QueueUrl=envs.DELETE_SQS_URL,
                    MessageBody=json.dumps(delete_message_body),
                    MessageGroupId=f"{str(chatbot.id)}-url",
                    MessageDeduplicationId=str(uuid.uuid4())
                )
                print('inside docuemnt reoved delte lambda triggered.')

        bot_documents = []
        #document added
        if document_added:
            # total_doc = await get_total_documents_for_chatbot(db, chatbot.id, uploaded_files=True)
            # if total_doc + len(document_added) > envs.TOTAL_NO_OF_ALLOWED_DOCS:
            #     raise HTTPException(status_code=500, detail="File Limit reached maximum limit.")
        
            for document_file in document_added:            
                # document_filename = f"{document_file}"
                # document_s3_key = f"uploaded_file_doc/{organization_id}/{chatbot.id}/{document_filename}"

                
                # content = await document_file.read()
                try:
                    # Upload file to S3
                    # s3_client.put_object(
                    #     Bucket=envs.BUCKET_NAME,  # Ensure the environment variable is set
                    #     Key=document_s3_key,
                    #     Body=content,
                    #     ContentType=document_file.content_type
                    # )
                    # print(f"File uploaded to S3: {document_s3_key}")

                    file_extension = os.path.splitext(document_file)[1][1:]
                    # Save file metadata to DB
                    doc = await insert_document_entry(
                        chatbot.id, document_file, file_extension, 'Uploaded', db
                    )
                    print(f'databse entery inserted')
                    parsed_url = urlparse(document_file)
                    file_name = parsed_url.path.split("/")[-1]
                    file_size_kb = get_s3_file_size(doc.document_name)
                    bot_documents.append(
                        DocumentInfo(
                            document_name=file_name,
                            document_id=doc.id,
                            document_status = doc.status,
                            document_size = file_size_kb
                        )
                    )
                except NoCredentialsError:
                    print("AWS Credentials not found! Ensure they are set in environment variables.")
                    raise HTTPException(status_code=500, detail="S3 upload failed due to missing credentials")
        
        # website removed
        if website_removed:
            for website in website_removed:
                url_name = await get_webscrap_entery(id=website.website_url_id, chatbot_id = chatbot.id, session = db)
                await delete_webscrap_entry(website.website_url_id, db)
                if url_name:
                    delete_message_body = {
                        "org_id": organization_id,
                        "chatbot_id": chatbot_id,
                        "database_id": url_name.document_name, 
                        "content_source": "url"
                    }
                    response = sqs_client.send_message(
                        QueueUrl=envs.DELETE_SQS_URL,
                        MessageBody=json.dumps(delete_message_body),
                        MessageGroupId=f"{str(chatbot.id)}-url",
                        MessageDeduplicationId=str(uuid.uuid4())
                    )
                    print('lambda called in side link deltee')
        
        
        urls = []
        # website added
        if website_added:
            total_doc = await get_total_documents_for_chatbot(db, chatbot.id)
            if total_doc + len(website_added) > envs.TOTAL_NO_OF_ALLOWED_URLS:
                raise HTTPException(status_code=422, detail=f"Maximum allowed website limit are 50.")
            for website_link in website_added:
                if website_link.website_url_id:
                    webscrap_entery = await get_webscrap_entery(id=website_link.website_url_id, chatbot_id = chatbot.id, session = db)
                    #if link was updated.
                    if webscrap_entery:
                        delete_message_body = {
                            "org_id": organization_id,
                            "chatbot_id": chatbot_id,
                            "database_id": webscrap_entery.document_name,  # Assume `source` contains URL
                            "content_source": "url"
                        }
                        response = sqs_client.send_message(
                            QueueUrl=envs.DELETE_SQS_URL,
                            MessageBody=json.dumps(delete_message_body),
                            MessageGroupId=f"{str(chatbot.id)}-url",
                            MessageDeduplicationId=str(uuid.uuid4())
                        )

                        webscrap_entery.document_name = website_link.website_link
                        webscrap_entery.url_sweep_option = 'Domain' if website_link.sweep_domain else 'website_page'
                        db.add(webscrap_entery)
                        await db.commit()

                        message_body = {
                            "url": website_link.website_link,
                            "org_id": chatbot.organization_id,
                            "chatbot_id": chatbot.id,
                            "domain_sweep": website_link.sweep_domain if website_link.sweep_domain else False,
                            "content_source": "url",
                            "database_id":webscrap_entery.id
                            
                        }
                            # Send message to SQS
                        response = sqs_client.send_message(
                            QueueUrl=envs.SQS_QUEUE_URL,
                            MessageBody=json.dumps(message_body),
                            MessageGroupId=f"{str(chatbot.id)}-url",
                            MessageDeduplicationId=str(uuid.uuid4())
                        )
                        print('results updated and lambda called inisde links updates.')
                
                #if user has added new link on update with domain sweep
                elif website_link.sweep_domain and not website_link.website_url_id:
                    database_entry = await insert_webscrap_entry(chatbot_id = chatbot.id, url = normalize_url(website_link.website_link), sweap_domain = website_link.sweep_domain, content_type = 'url',status = 'Uploaded',session = db)
                    message_body = {
                        "url": website_link.website_link,
                        "org_id": chatbot.organization_id,
                        "chatbot_id": chatbot.id,
                        "domain_sweep": True,
                        "content_source": "url",
                        "database_id":database_entry.id
                    }
                        # Send message to SQS
                    response = sqs_client.send_message(
                        QueueUrl=envs.SQS_QUEUE_URL,
                        MessageBody=json.dumps(message_body),
                        MessageGroupId=f"{str(chatbot.id)}-url",
                        MessageDeduplicationId=str(uuid.uuid4())
                    )
                    print(f"Sent to SQS: {response['MessageId']} inside update create new link scrapping and domain swep.")
                    urls.append(
                        WebscrapUrl(
                            website_link=database_entry.document_name,
                            website_url_id=database_entry.id,
                            sweep_domain=(database_entry.url_sweep_option.lower() == "domain"),
                            sweep_url=(database_entry.url_sweep_option.lower() == "website_page"),
                            status = database_entry.status
                        )
                    )
                else: 
                    database_entry = await insert_webscrap_entry(chatbot_id = chatbot.id, url = normalize_url(website_link.website_link), sweap_domain = False, content_type = 'url',status = 'Uploaded',session = db)
                    urls.append(
                        WebscrapUrl(
                            website_link=database_entry.document_name,
                            website_url_id=database_entry.id,
                            sweep_domain=(database_entry.url_sweep_option.lower() == "domain"),
                            sweep_url=(database_entry.url_sweep_option.lower() == "website_page"),
                            status = database_entry.status
                        )
                    )
                    message_body = {
                        "url": website_link.website_link,
                        "org_id": chatbot.organization_id,
                        "chatbot_id": chatbot.id,
                        "domain_sweep": False,
                        "content_source": "url",
                        "database_id":database_entry.id
                    }
                    # Send message to SQS
                    response = sqs_client.send_message(
                        QueueUrl=envs.SQS_QUEUE_URL,
                        MessageBody=json.dumps(message_body),
                        MessageGroupId=f"{str(chatbot.id)}-url",
                        MessageDeduplicationId=str(uuid.uuid4())
                    )
                    print(f"Sent to SQS: {response['MessageId']} inside update create new link scrapping and single page.")
        
        return ChatbotFileUpdateResponse(
            website_url = urls,
            bot_documents = bot_documents
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Unable to submit QA. Please try later.")

async def update_organization_chatbot_traning_QAs(
        db: AsyncSession, 
        organization_id: int,
        chatbot_id: int,
        QAs_added,
        QAs_removed,
        current_user: User
    ):
    if current_user.role == UserRole.USER:
        if not current_user.group_ids:
            raise HTTPException(status_code=404, detail="You don't have permissions.")
        user_groups, _ = await format_user_chatbot_permissions(db, current_user.organization_id, current_user.group_ids)
    
        bot_details = None
        for bot_info in user_groups:
            if bot_info['chatbot_id'] == chatbot_id:
                bot_details = bot_info
                break
        print(f'bot info == {bot_details}')
        if not bot_details:
            raise HTTPException(status_code=404, detail="Unautherized operation")
        await _fine_grain_role_checks(current_user, organization_id, chatbot_id, bot_details, "can_edit_qa")
        # for group_id in current_user.group_ids:
        #     #Extract gropu information and extract ids from groups.
        #     group_details = await get_rbac_groups_by_id(db, current_user.organization_id, group_id)
        #     await _fine_grain_role_checks(current_user, organization_id, chatbot_id, group_details, "can_edit_qa")
            
    else:
        await _fine_grain_role_checks(current_user, organization_id)

    # await _role_based_checks(current_user, organization_id)
    # Get the chatbot first
    QAs_newly_added = []
    query = select(ChatbotConfig).filter(
        ChatbotConfig.id == chatbot_id,
        ChatbotConfig.organization_id == organization_id
    )
    
    result = await db.execute(query)
    chatbot = result.scalar_one_or_none()
    
    if not chatbot:
        raise HTTPException(status_code=404, detail="Chatbot not found")
    
    # Remove QAs
    if QAs_removed:
        for qa in QAs_removed:
            record = await db.scalar(
                select(QATemplate)
                .where(
                    QATemplate.id == qa.id,
                    QATemplate.chatbot_id == chatbot.id
                )
            )
            if record:
                thread_id = record.thread_id
                message_id = record.message_id
    
                # 3. Now delete the record
                delete_query = delete(QATemplate).where(
                    QATemplate.id == qa.id,
                    QATemplate.chatbot_id == chatbot.id
                )
                await db.execute(delete_query)
                # delete_query = delete(QATemplate).where(
                #     QATemplate.id == qa.id,
                #     QATemplate.chatbot_id == chatbot.id
                # )
                # await db.execute(delete_query)
                data_to_delete = {
                        "org_id": organization_id,
                        "chatbot_id": chatbot_id,
                        "database_id": qa.id,  
                        "content_source": "qa_pair"
                    }
                response = sqs_client.send_message(
                    QueueUrl=envs.DELETE_SQS_URL,
                    MessageBody=json.dumps(data_to_delete),
                    MessageGroupId=f"{str(chatbot_id)}-qa_pair-{qa.id}",
                    MessageDeduplicationId=str(uuid.uuid4())
                )
                
                if not thread_id or not message_id:
                    continue
                query = (
                    select(Messages)
                    .join(Threads)
                    .filter(
                        Messages.message_uuid == message_id,
                        Messages.thread_id == thread_id,
                        Threads.chatbot_id == chatbot_id
                    )
                )
                result = await db.execute(query)
                message = result.scalar_one_or_none()

                if not message:
                    return
                message.is_revised = False

    # Commit changes to the database
    await db.commit()
    
    # Add QAs
    if QAs_added:
        for qa in QAs_added:
            qa_query = select(QATemplate).filter(QATemplate.chatbot_id == chatbot_id)
            qatemplates = (await db.execute(qa_query)).scalars().all()
            new_qas_count = sum(1 for qa in QAs_added if not qa.id)
            if len(qatemplates) + new_qas_count > envs.TOTAL_NO_OF_QAS:
                raise HTTPException(status_code=422, detail=f"Maximum allowed number of QAs reached.")
        
            if qa.id:
                qa_query = select(QATemplate).filter(
                    QATemplate.id == qa.id,
                    QATemplate.chatbot_id == chatbot_id
                )
                qatemplate = (await db.execute(qa_query)).scalar_one_or_none()
                if qatemplate:
                    qatemplate.answer = qa.answer
                    qatemplate.question = qa.question
                    qatemplate.status = "Uploaded"
                    db.add(qatemplate)
                    qa_body = f"Question: {qa.question}\nAnswer: {qa.answer}"
                    message_body = {
                        "url": qa_body,
                        "org_id": chatbot.organization_id,
                        "chatbot_id": chatbot.id,
                        "domain_sweep": qa.id,
                        "content_source": "qa_pair",
                        "request_type": "Update"
                    }
                    response = sqs_client.send_message(
                        QueueUrl=envs.SQS_QUEUE_URL,
                        MessageBody=json.dumps(message_body),
                        MessageGroupId=f"{str(chatbot.id)}-qas-{qa.id}",
                        MessageDeduplicationId=str(uuid.uuid4())
                    )
                    print(f"Sent to SQS: {response['MessageId']}")
                
            else:
                qa_entry = QATemplate(
                    chatbot_id=chatbot.id,
                    question=qa.question,
                    answer=qa.answer,
                    message_id = qa.qa_db_id,
                    thread_id = qa.qa_thrd_id,
                    status = "Uploaded"
                )
                db.add(qa_entry)
                qa_body = f"Question: {qa.question}\nAnswer: {qa.answer}"
                await db.commit()
                # Refresh to load any new data (like autogenerated id)
                await db.refresh(qa_entry)
                print(f'added qa == {qa_entry.message_id} -- {qa_entry.thread_id}')
                QAs_newly_added.append(QATemplateData(
                    question = qa_entry.question,
                    answer = qa_entry.answer,
                    id = qa_entry.id,
                    status = "Uploaded"
                ))
                message_body = {
                    "url": qa_body,
                    "org_id": chatbot.organization_id,
                    "chatbot_id": chatbot.id,
                    "domain_sweep": qa_entry.id,
                    "content_source": "qa_pair"
                }
                    # Send message to SQS
                response = sqs_client.send_message(
                    QueueUrl=envs.SQS_QUEUE_URL,
                    MessageBody=json.dumps(message_body),
                    MessageGroupId=f"{str(chatbot.id)}-qa-{qa_entry.id}",
                    MessageDeduplicationId=str(uuid.uuid4())
                )
                print(f"Sent to SQS: {response['MessageId']}")
                thread_id = qa.qa_thrd_id
                message_id = qa.qa_db_id
                if not thread_id or not message_id:
                    continue
                query = (
                    select(Messages)
                    .join(Threads)
                    .filter(
                        Messages.message_uuid == message_id,
                        Messages.thread_id == thread_id,
                        Threads.chatbot_id == chatbot_id
                    )
                )
                result = await db.execute(query)
                message = result.scalar_one_or_none()

                if not message:
                    raise HTTPException(status_code=404, detail=f"Message ID {message_id} not found in chatbot {chatbot_id}.")

                message.is_revised = True
                
    # Commit changes to the database
    await db.commit()
    return QAs_newly_added

async def update_organization_chatbot_traning_guardrails(
        db: AsyncSession, 
        organization_id: int,
        chatbot_id: int,
        guardrails_added,
        guardrails_removed,
        current_user: User
    ):
    if current_user.role == UserRole.USER:
        if not current_user.group_ids:
            raise HTTPException(status_code=404, detail="You don't have permissions.")
        user_groups, _ = await format_user_chatbot_permissions(db, organization_id, current_user.group_ids)
    
        bot_details = None
        for bot_info in user_groups:
            if bot_info['chatbot_id'] == chatbot_id:
                bot_details = bot_info
                break
        print(f'bot info == {bot_details}')
        if not bot_details:
            raise HTTPException(status_code=404, detail="Unautherized operation")
        await _fine_grain_role_checks(current_user, organization_id, chatbot_id, bot_details, "can_edit_guardrails")
        
        # for group_id in current_user.group_ids:
        #     #Extract gropu information and extract ids from groups.
        #     group_details = await get_rbac_groups_by_id(db, current_user.organization_id, group_id)
        #     await _fine_grain_role_checks(current_user, organization_id, chatbot_id, group_details, "can_edit_guardrails")
       
    else:
        await _fine_grain_role_checks(current_user, organization_id)
    # await _role_based_checks(current_user, organization_id)
    # Get the chatbot first
    query = select(ChatbotConfig).filter(
        ChatbotConfig.id == chatbot_id,
        ChatbotConfig.organization_id == organization_id
    )
    
    result = await db.execute(query)
    chatbot = result.scalar_one_or_none()
    
    if not chatbot:
        raise HTTPException(status_code=404, detail="Chatbot not found")
    total_doc = await get_total_documents_for_chatbot(db, chatbot.id, uploaded_files=True)
    total_roals = await get_total_guardrails_for_chatbot(db, chatbot.id)
    newly_added_rails = []
    
    # Remove guardrails
    if guardrails_removed:
        for rail in guardrails_removed:
            guardrail_query = select(ChatbotGuardrail).filter(
                ChatbotGuardrail.id == rail.id,
                ChatbotGuardrail.chatbot_id == chatbot_id
            )
            guardrail = (await db.execute(guardrail_query)).scalar_one_or_none()
            if guardrail:
                
                # data_to_delete = {
                #     "org_id": organization_id,
                #     "chatbot_id": chatbot_id,
                #     "database_id": guardrail.id,  
                #     "content_source": "guardrails"
                # }
                # response = sqs_client.send_message(
                #     QueueUrl=envs.DELETE_SQS_URL,
                #     MessageBody=json.dumps(data_to_delete),
                #     MessageGroupId=f"{str(chatbot_id)}-gr-{guardrail.id}",
                #     MessageDeduplicationId=str(uuid.uuid4())
                # )
                await db.delete(guardrail)
    

    # Add guardrails
    if guardrails_added:
        added_rails_count = sum(1 for rail in guardrails_added if not rail.id)
        if total_roals + added_rails_count > envs.TOTAL_NO_OF_GUARDRAILS:
                raise HTTPException(
                    status_code=442,
                    detail=f"Maximum limit reached."
                )
        for rail in guardrails_added:
            if rail.id:
                guardrail_query = select(ChatbotGuardrail).filter(
                    ChatbotGuardrail.id == rail.id,
                    ChatbotGuardrail.chatbot_id == chatbot_id
                )
                guardrail = (await db.execute(guardrail_query)).scalar_one_or_none()
                if guardrail:
                    guardrail.guardrail_text = rail.guardrail
                    db.add(guardrail)
                    # message_body = {
                    #     "url": guardrail.guardrail_text,
                    #     "org_id": chatbot.organization_id,
                    #     "chatbot_id": chatbot.id,
                    #     "domain_sweep": guardrail.id,
                    #     "content_source": "guardrails",
                    #     "request_type": "Update"
                    # }
                    # response = sqs_client.send_message(
                    #     QueueUrl=envs.SQS_QUEUE_URL,
                    #     MessageBody=json.dumps(message_body),
                    #     MessageGroupId=f"{str(chatbot.id)}-gr-{guardrail.id}",
                    #     MessageDeduplicationId=str(uuid.uuid4())
                    # )
                    # print(f"Sent to SQS: {response['MessageId']}")
            else:
                
                guardrail = ChatbotGuardrail(
                    guardrail_text=rail.guardrail,
                    chatbot_id=chatbot.id
                )
                db.add(guardrail)
                await db.commit()
                await db.refresh(guardrail)
                newly_added_rails.append(GetGuardrails(
                    guardrail = guardrail.guardrail_text,
                    id = guardrail.id
                ))
                # message_body = {
                #     "url": guardrail.guardrail_text,
                #     "org_id": chatbot.organization_id,
                #     "chatbot_id": guardrail.chatbot_id,
                #     "domain_sweep": guardrail.id,
                #     "content_source": "guardrails"
                # }
                # # Send message to SQS
                # response = sqs_client.send_message(
                #     QueueUrl=envs.SQS_QUEUE_URL,
                #     MessageBody=json.dumps(message_body),
                #     MessageGroupId=f"{str(chatbot.id)}-gr-{guardrail.id}",
                #     MessageDeduplicationId=str(uuid.uuid4())
                # )
                # print(f"Sent to SQS: {response['MessageId']}")
    
    try:
        await db.commit()
        return newly_added_rails
    except Exception as e:
        await db.rollback()
        raise HTTPException(
            status_code=500,
            detail=f"An error occurred while updating chatbot guardrails: {str(e)}"
        )

async def fetch_chatbot_llm_model(
    db: AsyncSession, 
    organization_id: int,
    chatbot_id: int,
    current_user: User
):
    try:
        await _role_based_checks(current_user, organization_id)  # Check permissions
        
        query = select(ChatbotConfig).filter(
            ChatbotConfig.id == chatbot_id,
            ChatbotConfig.organization_id == organization_id
        )
        
        result = await db.execute(query)
        chatbot = result.scalar_one_or_none()
        
        if not chatbot:
            raise HTTPException(status_code=404, detail="Chatbot not found")
        
        return chatbot
    
    except HTTPException as http_err:
        raise http_err
    except SQLAlchemyError as db_err:
        raise HTTPException(status_code=500, detail=f"Database error: {str(db_err)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"An unexpected error occurred: {str(e)}")

async def remove_chatbot_from_organization_service(db, organization_id, chatbot_id, current_user):
    
    await _role_based_checks(current_user, organization_id)
    # Get the chatbot by its ID and organization ID
    query = select(ChatbotConfig).filter(
        ChatbotConfig.id == chatbot_id,
        ChatbotConfig.organization_id == organization_id
    )
    chatbot = await db.execute(query)
    chatbot = chatbot.scalars().first()
    
    if not chatbot:
        raise HTTPException(
            status_code=404,
            detail="Chatbot not found for the given organization."
        )
    
    # Remove all documents associated with the chatbot
    query_documents = select(ChatbotDocument).filter(
        ChatbotDocument.chatbot_id == chatbot_id
    )
    documents = await db.execute(query_documents)
    documents = documents.scalars().all()
    # Extract S3 keys and delete documents
    from urllib.parse import urlparse
    s3_keys = []
    for document in documents:
        if document.document_name:
            parsed_url = urlparse(document.document_name)
            s3_key = parsed_url.path.lstrip("/")  # Extract S3 key
            s3_keys.append(s3_key)
        
        await db.delete(document)
    # for document in documents:
    #     await db.delete(document)
    # Delete associated S3 objects
    for key in s3_keys:
        try:
            s3_client.delete_object(Bucket=envs.BUCKET_NAME, Key=key)
            print(f"🗑️ Deleted S3 object: {key}")
        except Exception as e:
            print(f"⚠️ Failed to delete S3 object {key}: {str(e)}")
                  
    # remove all qa template to the chatbot
    qa_templates = select(QATemplate).filter(
        QATemplate.chatbot_id == chatbot_id
    )
    qa_templates = await db.execute(qa_templates)
    qa_templates_to_delete = qa_templates.scalars().all()
    for templates in qa_templates_to_delete:
        await db.delete(templates)

    await db.delete(chatbot)

    sqs_messages = []
    # Send scrapped content deletion message
    for document in documents:
        if document.content_type == "url":
            sqs_messages.append({
                "org_id": organization_id,
                "chatbot_id": chatbot_id,
                "database_id": document.document_name,  # Assume `source` contains URL
                "content_source": "url"
            })
        elif document.content_type == "application/pdf":
            sqs_messages.append({
                "org_id": organization_id,
                "chatbot_id": chatbot_id,
                "database_id": document.document_name,
                "content_source": "file"
            })
    
    # Send training text deletion message
    sqs_messages.append({
        "org_id": organization_id,
        "chatbot_id": chatbot_id,
        "database_id": "",
        "content_source": "prompt"
    })

    # Send QA pair deletion messages
    for qa in qa_templates_to_delete:
        sqs_messages.append({
            "org_id": organization_id,
            "chatbot_id": chatbot_id,
            "database_id": qa.id,  
            "content_source": "qa_pair"
        })

    # Step 7: Send each message to SQS FIFO queue
    for idx, message in enumerate(sqs_messages):
        sqs_client.send_message(
            QueueUrl=envs.DELETE_SQS_URL,
            MessageBody=json.dumps(message),
            MessageGroupId=f"{idx}-delete_operations",
            MessageDeduplicationId=f"{chatbot_id}-{message['content_source']}-{idx}"  # Ensures unique messages
        )

    query_threads = select(Threads).filter(Threads.chatbot_id == chatbot_id)
    threads = await db.execute(query_threads)
    threads = threads.scalars().all()
    from app.services.user_chat import delete_session
    
    for thread in threads:
        await delete_session(thread.thread_id, current_user, db, chatbot_flag = True)
    
    #remove the chatbot from groups.
    all_groups = await get_rbac_groups_by_org_id(db, organization_id)
    for group in all_groups:
        print(f'group attr == {group.attributes}')
        if group.attributes:
            attributes=[]
            for attr in group.attributes:
                print(f'attr == {attr}')
                if attr["chatbot_id"] == chatbot_id:
                    flag_modified(group, "attributes")
                    print('--mached--')
                else:
                    attributes.append(attr)
            group.attributes = attributes
        db.add(group)
            # user_group_ids.append(group_id)
    # user_data.group_ids.clear()
    # user_data.group_ids = user_group_ids

    await db.commit()
    return {"message": "Chatbot and associated documents removed successfully."}

async def bubble_settings_customization(
        db, 
        organization_id,
        chatbot_id,
        customization_data,
        current_user
    ):
    await _role_based_checks(current_user, organization_id)
    # Get the chatbot by its ID and organization ID
    query = select(ChatbotConfig).filter(
        ChatbotConfig.id == chatbot_id,
        ChatbotConfig.organization_id == organization_id
    )
    chatbot = await db.execute(query)
    chatbot = chatbot.scalars().first()
    
    if not chatbot:
        raise HTTPException(
            status_code=404,
            detail="Chatbot not found for the given organization."
        )
    
    existing_query = select(BubbleSettings).filter(BubbleSettings.chatbot_id == chatbot.id)
    existing_setting = (await db.execute(existing_query)).scalars().first()

    if existing_setting:
        if customization_data.bubble_bgColor:
            existing_setting.bubble_bgColor = customization_data.bubble_bgColor
        if customization_data.bubble_icon:
            existing_setting.bubble_icon = customization_data.bubble_icon

        # existing_setting.bubble_size = customization_data.bubble_size
        # existing_setting.bubble_icon_color = customization_data.bubble_icon_color
        await db.commit()
        await db.refresh(existing_setting)
        return existing_setting

    new_customization = BubbleSettings(
        bubble_bgColor = customization_data.bubble_bgColor,
        bubble_icon = customization_data.bubble_icon,
        # bubble_size = customization_data.bubble_size,
        # bubble_icon_color = customization_data.bubble_icon_color,
        chatbot_id = chatbot.id
    )
    
    db.add(new_customization)
    await db.commit()
    await db.refresh(new_customization)
    return new_customization

async def chatbot_settings_customization(
        db, 
        organization_id,
        chatbot_id,
        customization_data,
        current_user
    ):
    await _role_based_checks(current_user, organization_id)
    # Get the chatbot by its ID and organization ID
    query = select(ChatbotConfig).filter(
        ChatbotConfig.id == chatbot_id,
        ChatbotConfig.organization_id == organization_id
    )
    chatbot = await db.execute(query)
    chatbot = chatbot.scalars().first()
    
    if not chatbot:
        raise HTTPException(
            status_code=404,
            detail="Chatbot not found for the given organization."
        )
    
    existing_query = select(ChatbotSettings).filter(
        ChatbotSettings.chatbot_id == chatbot.id
    )
    existing_setting = (await db.execute(existing_query)).scalars().first()

    if existing_setting:
        existing_setting.primary_color = customization_data.primary_color
        existing_setting.chat_header = customization_data.chat_header
        existing_setting.sender_bubble_color = customization_data.sender_bubble_color
        existing_setting.receiver_bubble_color = customization_data.receiver_bubble_color
        existing_setting.receiverTextColor = customization_data.receiverTextColor
        existing_setting.senderTextColor = customization_data.senderTextColor
        await db.commit()
        await db.refresh(existing_setting)
        return existing_setting
    
    bot_customization = ChatbotSettings(
        primary_color = customization_data.primary_color,
        chat_header = customization_data.chat_header,
        sender_bubble_color = customization_data.sender_bubble_color,
        receiver_bubble_color = customization_data.receiver_bubble_color,
        chatbot_id = chatbot.id,
        receiverTextColor = customization_data.receiverTextColor,
        senderTextColor = customization_data.senderTextColor
    )
    
    db.add(bot_customization)
    await db.commit()
    await db.refresh(bot_customization)
    return bot_customization

async def get_bubble_settings_customization(
        db, 
        chatbot_id,
        is_public = False
    ):
    # await _role_based_checks(current_user, organization_id)
    if is_public:
        chatbot_id = int(_decrypt_chatbot_id(chatbot_id, fernet))
    query = select(ChatbotConfig).filter(
        ChatbotConfig.id == chatbot_id
    )
    chatbot = await db.execute(query)
    chatbot = chatbot.scalars().first()
    
    if not chatbot:
        raise HTTPException(
            status_code=404,
            detail="Chatbot not found for the given organization."
        )
    
    print(f'Chatbot type == {chatbot.chatbot_type}')
    # if chatbot.chatbot_type != "External":
    #     raise HTTPException(
    #         status_code=404,
    #         detail="This is not an external chatbot."
    #     )
    
    query = select(BubbleSettings).filter(
        BubbleSettings.chatbot_id == chatbot_id
    )
    customizations = await db.execute(query)
    chatbot_customize = customizations.scalars().first()
    print(f'chatbot customization feched == {chatbot_customize}')
    if not chatbot_customize:
        # raise HTTPException(
        #     status_code=404,
        #     detail="No bubble customizations found"
        # )
        from app.schemas.request.chatbot_config import BubbleCutomize
        response =  BubbleCutomize(bubble_bgColor = "#F9F9F9", bubble_icon = "https://marti-file-upload-bucket.s3.us-east-2.amazonaws.com/logo/marti-logo-nobg.png")
        return response
    return chatbot_customize

async def get_public_chatbot_settings_customization(
        db, 
        chatbot_id,
    ):
    # await _role_based_checks(current_user, organization_id)
    bot_id = int(_decrypt_chatbot_id(chatbot_id, fernet))
    print(f'chabot id == {bot_id} and then type = {type(bot_id)}')
    query = select(ChatbotConfig).filter(
        ChatbotConfig.id == bot_id
    )
    chatbot = await db.execute(query)
    chatbot = chatbot.scalars().first()
    
    if not chatbot:
        raise HTTPException(
            status_code=404,
            detail="Chatbot not found for the given organization."
        )
    
    if chatbot.chatbot_type != "External":
        raise HTTPException(
            status_code=404,
            detail="This is not an external chatbot."
        )
    
    query = select(ChatbotSettings).filter(
        ChatbotSettings.chatbot_id == bot_id
    )
    customizations = await db.execute(query)
    chatbot_customize = customizations.scalars().first()
    print(f'chatbot customization feched == {chatbot_customize}')
    if not chatbot_customize:
        from app.schemas.request.chatbot_config import ChatbotCutomize
        response =  ChatbotCutomize(primary_color = "#14b8a6", chat_header = "#FFFFFF", sender_bubble_color = "#D3D3D3", receiver_bubble_color = "#efefef", senderTextColor = "#000000", receiverTextColor = "#000000")
        return response
    return chatbot_customize

async def get_chatbot_settings_customization(
        db, 
        organization_id,
        chatbot_id,
        current_user
    ):
    # await _role_based_checks(current_user, organization_id)
    query = select(ChatbotSettings).filter(
        ChatbotSettings.chatbot_id == chatbot_id
    )
    customizations = await db.execute(query)
    chatbot_customize = customizations.scalars().first()
    print(f'chatbot customization feched == {chatbot_customize}')
    if not chatbot_customize:
        from app.schemas.request.chatbot_config import ChatbotCutomize
        response =  ChatbotCutomize(primary_color = "#059669", chat_header = "#FFFFFF", sender_bubble_color = "#D3D3D3", receiver_bubble_color = "#efefef", senderTextColor= "#000000", receiverTextColor="#000000" )
        return response
    return chatbot_customize
    
async def create_organization_chatbot_memory(
        db, 
        organization_id,
        chatbot_id,
        chatbot_memory,
        current_user
    ):
    if current_user.role == UserRole.USER:
        if not current_user.group_ids:
            raise HTTPException(status_code=404, detail="You don't have permissions.")
        user_groups, _ = await format_user_chatbot_permissions(db, organization_id, current_user.group_ids)
    
        bot_details = None
        for bot_info in user_groups:
            if bot_info['chatbot_id'] == chatbot_id:
                bot_details = bot_info
                break
        print(f'bot info == {bot_details}')
        if not bot_details:
            raise HTTPException(status_code=404, detail="Unautherized operation")
        await _fine_grain_role_checks(current_user, organization_id, chatbot_id, bot_details, "can_edit_memory")
        
        # for group_id in current_user.group_ids:
        #     #Extract gropu information and extract ids from groups.
        #     group_details = await get_rbac_groups_by_id(db, current_user.organization_id, group_id)
        #     await _fine_grain_role_checks(current_user, organization_id, chatbot_id, group_details, "can_edit_memory")
       
    else:
        await _fine_grain_role_checks(current_user, organization_id)
    # await _role_based_checks(current_user, organization_id)
    total_roals = await get_total_memory_for_chatbot(db,chatbot_id)
    if (total_roals + 1) > envs.TOTAL_NO_OF_MEMORY:
        raise HTTPException(
                    status_code=442,
                    detail=f"Maximum limit reached."
                )
    
    new_memory = ChatbotMemory(
        chatbot_id=chatbot_id,
        memory_text=chatbot_memory.text,
        creator=current_user.name,
        status = "Uploaded"
    )
    db.add(new_memory)
    # Commit the transaction
    await db.commit()
    # Refresh to load any new data (like autogenerated id)
    await db.refresh(new_memory)
    message_body = {
                "url": chatbot_memory.text,
                "org_id": organization_id,
                "chatbot_id": chatbot_id,
                "domain_sweep": new_memory.id,
                "content_source": "memory"
            }
                # Send message to SQS
    print(f'envs sqs = {envs.SQS_QUEUE_URL}')
    response = sqs_client.send_message(
        QueueUrl=envs.SQS_QUEUE_URL,
        MessageBody=json.dumps(message_body),
        MessageGroupId=f"{str(chatbot_id)}-memory-{new_memory.id}",
        MessageDeduplicationId=str(uuid.uuid4())
    )
    return {"memory_id":new_memory.id,"creator": new_memory.creator, "text": new_memory.memory_text}

async def update_organization_chatbot_memory(
        db, 
        organization_id,
        chatbot_id,
        chatbot_memory,
        current_user
    ):
    if current_user.role == UserRole.USER:
        if not current_user.group_ids:
            raise HTTPException(status_code=404, detail="You don't have permissions.")
        user_groups, _ = await format_user_chatbot_permissions(db, organization_id, current_user.group_ids)
    
        bot_details = None
        for bot_info in user_groups:
            if bot_info['chatbot_id'] == chatbot_id:
                bot_details = bot_info
                break
        print(f'bot info == {bot_details}')
        if not bot_details:
            raise HTTPException(status_code=404, detail="Unautherized operation")
        await _fine_grain_role_checks(current_user, organization_id, chatbot_id, bot_details, "can_edit_memory")
        
        # for group_id in current_user.group_ids:
        #     #Extract gropu information and extract ids from groups.
        #     group_details = await get_rbac_groups_by_id(db, current_user.organization_id, group_id)
        #     await _fine_grain_role_checks(current_user, organization_id, chatbot_id, group_details, "can_edit_memory")
       
    else:
        await _fine_grain_role_checks(current_user, organization_id)
    # await _role_based_checks(current_user, organization_id)
    if chatbot_memory.memory_id:
        query  = select(ChatbotMemory).where(
                        ChatbotMemory.chatbot_id == chatbot_id,
                        ChatbotMemory.id == chatbot_memory.memory_id
                    )
        result = await db.execute(query)
        existing_memory = result.scalar_one_or_none()
        if not existing_memory:
            raise HTTPException(status_code=404, detail="Chatbot memory not found")
        if existing_memory.chatbot_id != chatbot_id:
            raise HTTPException(status_code=404, detail="You can't update.")
        
        existing_memory.memory_text = chatbot_memory.text
        existing_memory.creator = current_user.name
        existing_memory.status = "Uploaded"
        await db.commit()
        await db.refresh(existing_memory)

        message_body = {
            "url": chatbot_memory.text,
            "org_id": organization_id,
            "chatbot_id": chatbot_id,
            "domain_sweep": chatbot_memory.memory_id,
            "content_source": "memory",
            "request_type": "Update"
        }
        response = sqs_client.send_message(
            QueueUrl=envs.SQS_QUEUE_URL,
            MessageBody=json.dumps(message_body),
            MessageGroupId=f"{str(chatbot_id)}-memory-{chatbot_memory.memory_id}",
            MessageDeduplicationId=str(uuid.uuid4())
        )

        return {
            "memory_id": existing_memory.id,
            "creator": existing_memory.creator,
            "text": existing_memory.memory_text,
            "status":existing_memory.status
        }
    else:
        raise HTTPException(status_code=404, detail="Provide complete infromation.")

async def get_organization_chatbot_memory(
    db: AsyncSession, 
    organization_id: int,
    chatbot_id: int,
    current_user: User
):
    # Optionally perform role-based checks if needed
    # await _role_based_checks(current_user, organization_id)
    if current_user.role == UserRole.USER:
        if not current_user.group_ids:
            raise HTTPException(status_code=404, detail="You don't have permissions.")
        user_groups, _ = await format_user_chatbot_permissions(db, organization_id, current_user.group_ids)
    
        bot_details = None
        for bot_info in user_groups:
            if bot_info['chatbot_id'] == chatbot_id:
                bot_details = bot_info
                break
        print(f'bot info == {bot_details}')
        if not bot_details:
            raise HTTPException(status_code=404, detail="Unautherized operation")
        await _fine_grain_role_checks(current_user, organization_id, chatbot_id, bot_details, "can_edit_memory")
        
        # for group_id in current_user.group_ids:
        #     #Extract gropu information and extract ids from groups.
        #     group_details = await get_rbac_groups_by_id(db, current_user.organization_id, group_id)
        #     await _fine_grain_role_checks(current_user, organization_id, chatbot_id, group_details, "can_edit_memory")
       
    else:
        await _fine_grain_role_checks(current_user, organization_id)
    query = select(ChatbotConfig).filter(ChatbotConfig.id == chatbot_id)
    result = await db.execute(query)
    chatbot =  result.scalar_one_or_none()
    if not chatbot:
        raise HTTPException(status_code=403, detail="Chatbot not found.")
    result = await db.execute(
        select(ChatbotMemory).where(ChatbotMemory.chatbot_id == chatbot_id)
    )
    memories = result.scalars().all()
    from app.schemas.response.chatbot_config import GetChatbotMemoryResponse, ChatbotMemoryResponse
    memory_data = []
    for memory in memories:
        memory_data.append(ChatbotMemoryResponse(
            text = memory.memory_text,
            creator = memory.creator,
            memory_id = memory.id,
            status = memory.status
        ))
    # [
    #     {"memory_id": memory.id, "creator": memory.creator, "text": memory.memory_text, "status":memory.status}
    #     for memory in memories
    # ]
    

    # Return the list of memory entries in the format: creator and text
    return GetChatbotMemoryResponse(chatbot_data = memory_data, bot_memory_status = chatbot.memory_status)

async def delete_organization_chatbot_memory(
    db: AsyncSession, 
    organization_id: int,
    chatbot_id: int,
    memory_id: int,
    current_user
):
    if current_user.role == UserRole.USER:
        if not current_user.group_ids:
            raise HTTPException(status_code=404, detail="You don't have permissions.")
        user_groups, _ = await format_user_chatbot_permissions(db, organization_id, current_user.group_ids)
    
        bot_details = None
        for bot_info in user_groups:
            if bot_info['chatbot_id'] == chatbot_id:
                bot_details = bot_info
                break
        print(f'bot info == {bot_details}')
        if not bot_details:
            raise HTTPException(status_code=404, detail="Unautherized operation")
        await _fine_grain_role_checks(current_user, organization_id, chatbot_id, bot_details, "can_edit_memory")
        
        # for group_id in current_user.group_ids:
        #     #Extract gropu information and extract ids from groups.
        #     group_details = await get_rbac_groups_by_id(db, current_user.organization_id, group_id)
        #     await _fine_grain_role_checks(current_user, organization_id, chatbot_id, group_details, "can_edit_memory")
       
    else:
        await _fine_grain_role_checks(current_user, organization_id)
    # Verify permissions for the current user
    # await _role_based_checks(current_user, organization_id)
    
    # Query for the memory record matching both chatbot_id and memory_id
    query = select(ChatbotMemory).where(
        ChatbotMemory.chatbot_id == chatbot_id,
        ChatbotMemory.id == memory_id
    )
    result = await db.execute(query)
    memory = result.scalar_one_or_none()
    
    if not memory:
        raise HTTPException(status_code=404, detail="Chatbot memory not found")
    if memory.chatbot_id != chatbot_id:
        raise HTTPException(status_code=404, detail="You can't delete.")
    
    # Delete the memory record and commit the changes
    await db.delete(memory)
    await db.commit()
    data_to_delete = {
            "org_id": organization_id,
            "chatbot_id": chatbot_id,
            "database_id": memory.id,  
            "content_source": "memory"
        }
    response = sqs_client.send_message(
        QueueUrl=envs.DELETE_SQS_URL,
        MessageBody=json.dumps(data_to_delete),
        MessageGroupId=f"{str(chatbot_id)}-memory",
        MessageDeduplicationId=str(uuid.uuid4())
    )
    
    return 


async def create_organization_chatbot_suggestion(
        db, 
        organization_id,
        chatbot_id,
        chatbot_suggestion,
        current_user
    ):
    await _role_based_checks(current_user, organization_id)
    new_suggestion = ChatSuggestion(
        chatbot_id=chatbot_id,
        suggestion_text = chatbot_suggestion.suggestion_text
    )
    db.add(new_suggestion)
    # Commit the transaction
    await db.commit()
    # Refresh to load any new data (like autogenerated id)
    await db.refresh(new_suggestion)
    return {"suggestion_id":new_suggestion.id, "suggestion_text": new_suggestion.suggestion_text}

async def update_organization_chatbot_suggestion(
        db, 
        organization_id,
        chatbot_id,
        chatbot_suggestion,
        current_user
    ):
    await _role_based_checks(current_user, organization_id)
    if chatbot_suggestion.suggestion_id:
        query  = select(ChatSuggestion).where(
                        ChatSuggestion.chatbot_id == chatbot_id,
                        ChatSuggestion.id == chatbot_suggestion.suggestion_id
                    )
        result = await db.execute(query)
        existing_suggestion = result.scalar_one_or_none()
        if not existing_suggestion:
            raise HTTPException(status_code=404, detail="Chatbot memory not found")
        if existing_suggestion.chatbot_id != chatbot_id:
            raise HTTPException(status_code=404, detail="You can't delete.")
        
        existing_suggestion.suggestion_text = chatbot_suggestion.suggestion_text
        await db.commit()
        await db.refresh(existing_suggestion)
        return {
            "suggestion_id": existing_suggestion.id,
            "suggestion_text": existing_suggestion.suggestion_text
        }
    else:
        raise HTTPException(status_code=404, detail="Provide complete infromation.")

async def get_organization_chatbot_suggestion(
    db: AsyncSession, 
    organization_id: int,
    chatbot_id: int,
    current_user: User
):
    # Optionally perform role-based checks if needed
    # await _role_based_checks(current_user, organization_id)
    
    result = await db.execute(
        select(ChatSuggestion).where(ChatSuggestion.chatbot_id == chatbot_id)
    )
    suggestions = result.scalars().all()

    # Return the list of memory entries in the format: creator and text
    return [
        {"suggestion_id": suggestion.id, "suggestion_text": suggestion.suggestion_text}
        for suggestion in suggestions
    ]

async def delete_organization_chatbot_suggestion(
    db: AsyncSession, 
    organization_id: int,
    chatbot_id: int,
    suggestion_id: int,
    current_user
):
    # Verify permissions for the current user
    await _role_based_checks(current_user, organization_id)
    
    # Query for the memory record matching both chatbot_id and memory_id
    query = select(ChatSuggestion).where(
        ChatSuggestion.chatbot_id == chatbot_id,
        ChatSuggestion.id == suggestion_id
    )
    result = await db.execute(query)
    suggestion = result.scalar_one_or_none()
    
    if not suggestion:
        raise HTTPException(status_code=404, detail="Chatbot memory not found")
    if suggestion.chatbot_id != chatbot_id:
        raise HTTPException(status_code=404, detail="You can't delete.")
    
    # Delete the memory record and commit the changes
    await db.delete(suggestion)
    await db.commit()
    
    return 


from app.services.user_chat import _decrypt_chatbot_id, fernet
async def get_public_chatbot_suggestion(
    db: AsyncSession, 
    chatbot_id: str,
):
    # Optionally perform role-based checks if needed
    # await _role_based_checks(current_user, organization_id)
    bot_id = int(_decrypt_chatbot_id(chatbot_id, fernet))
    print(f'bot id = {bot_id}')
    result = await db.execute(
        select(ChatSuggestion).where(ChatSuggestion.chatbot_id == bot_id)
    )
    suggestions = result.scalars().all()

    # Return the list of memory entries in the format: creator and text
    return [
        {"suggestion_id": suggestion.id, "suggestion_text": suggestion.suggestion_text}
        for suggestion in suggestions
    ]



############################################### [Helpers] ##################################
async def _role_based_checks(current_user: User, organization_id):
    if current_user.role not in [UserRole.ADMIN, UserRole.SUPER_ADMIN]:
        raise HTTPException(status_code=403, detail="Regular user can't update organization related detials.")
    if current_user.role == UserRole.ADMIN and organization_id != current_user.organization_id:
        raise HTTPException(status_code=403, detail="You can only modify details of your organization.")
    
async def _fine_grain_role_checks(current_user: User, organization_id, chatbot_id = None, user_chatbot_config = None, operation = None): #user_group -- detailsed description of the group of user
    if current_user.role == UserRole.ADMIN and organization_id != current_user.organization_id:
        raise HTTPException(status_code=403, detail="You can only modify details of your organization.")                         #operation -- Operation is what user want to do with chatbot
    
    if current_user.role == UserRole.USER:
        if organization_id != current_user.organization_id:
            raise HTTPException(status_code=403, detail="You can only modify details of your organization.")
        # for bot_info in user_groups.attributes:
        if user_chatbot_config["chatbot_id"] == chatbot_id:
            if not operation: #in case of getting only the records
                return True
            if user_chatbot_config[f'{operation}']:
                return True
        raise HTTPException(status_code=403, detail="Unautherized operation.")

    # define user role with permssions.


# async def normalize_url(url: str) -> str:
#     """Normalize a URL for comparison (removing scheme and 'www.')"""
#     parsed_url = urlparse(url)
#     netloc = parsed_url.netloc.lstrip("www.")
#     normalized = urlunparse(parsed_url._replace(netloc=netloc, scheme="https"))
#     return normalized

async def create_organization(db: AsyncSession, org_data: OrganizationCreate) -> Organization:
    """Create a new organization."""
    db_org = Organization(name=org_data.name, logo = org_data.logo, website_link = org_data.website_link)
    db.add(db_org)
    await db.commit()
    await db.refresh(db_org)
    return db_org

async def get_organization(db: AsyncSession, organization_id: int) -> Organization:
    """Get an organization by ID."""
    query = select(Organization).filter(Organization.id == organization_id)
    result = await db.execute(query)
    organization = result.scalar_one_or_none()
    if not organization:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Organization not found")
    return organization

async def update_organization(db: AsyncSession, organization_id: int, organization_data: OrganizationUpdate) -> Organization:
    """Update an organization."""
    organization = await get_organization(db, organization_id)
    for key, value in organization_data.dict(exclude_unset=True).items():
        setattr(organization, key, value)
    await db.commit()
    await db.refresh(organization)
    return organization

async def delete_organization(db: AsyncSession, organization_id: int) -> None:
    """Delete an organization."""
    organization = await get_organization(db, organization_id)
    await db.delete(organization)
    await db.commit()

async def add_organization_user(db: AsyncSession, organization_id: int, user_data: OrganizationUserAdd):
    """Add a user to an organization."""
    # First verify the organization exists
    organization = await get_organization(db, organization_id)
    
    # Get the user
    query = select(User).filter(User.id == user_data.user_id)
    result = await db.execute(query)
    user = result.scalar_one_or_none()
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )
    
    # Check if user is already in this organization
    if user.organization_id == organization_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User is already in this organization"
        )
    
    # Check if user is in another organization
    if user.organization_id is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User is already in another organization"
        )
    
    # Update user's organization
    user.organization_id = organization_id
    user.role = user_data.role
    
    await db.commit()
    await db.refresh(user)
    return user

async def get_organization_users(db: AsyncSession, organization_id: int,  role_filter: Optional[UserRole] = None, skip: int = 0, limit: int = 5) -> List[User]:
    """Get all users in an organization."""
    # Verify organization exists
    await get_organization(db, organization_id)
    
    # Get users
    query = select(User).filter(User.organization_id == organization_id)
    if role_filter:
        query = query.filter(User.role == role_filter)
    query = query.offset(skip).limit(limit)
    result = await db.execute(query)
    users = result.scalars().all()
    return users

async def update_organization_user(
    db: AsyncSession,
    organization_id: int,
    user_id: int,
    user_data: OrganizationUserUpdate
) -> User:
    """Update a user's role in an organization."""
    # Verify organization exists
    await get_organization(db, organization_id)
    
    # Get the user
    query = select(User).filter(
        User.id == user_id,
        User.organization_id == organization_id
    )
    result = await db.execute(query)
    user = result.scalar_one_or_none()
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found in organization"
        )
    
    # Update user's role
    user.role = user_data.role
    
    await db.commit()
    await db.refresh(user)
    return user

async def delete_organization_user(db: AsyncSession, organization_id: int, user_id: int) -> None:
    """Remove a user from an organization."""
    # Verify organization exists
    await get_organization(db, organization_id)
    
    # Get the user
    query = select(User).filter(
        User.id == user_id,
        User.organization_id == organization_id
    )
    result = await db.execute(query)
    user = result.scalar_one_or_none()
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found in organization"
        )
    # Remove user from organization
    query_threads = select(Threads).filter(Threads.user_id == user.id)
    threads = await db.execute(query_threads)
    threads = threads.scalars().all()
    from app.services.user_chat import delete_session
    
    for thread in threads:
        await delete_session(thread.thread_id, user, db)


    await db.delete(user)
    await db.commit()
    return

async def create_new_organization_service(
    db: AsyncSession,
    organization_data: OrganizationCreate,
    current_user: User
) -> Organization:
    """Create a new organization (Admin only)"""
    if current_user.role not in [UserRole.SUPER_ADMIN]:
        raise HTTPException(status_code=403, detail="Not authorized")
    return await create_organization(db, organization_data)

async def check_organization_admin_access(db: AsyncSession, organization_id: int, current_user: User) -> bool:
    """Check if user is the admin of the organization or a super admin."""
    if current_user.role == UserRole.SUPER_ADMIN:
        return True
        
    # Check if user is admin of this specific organization
    if current_user.role == UserRole.ADMIN and current_user.organization_id == organization_id:
        return True
        
    return False

async def read_organization_service(
    db: AsyncSession,
    organization_id: int,
    current_user: User
) -> Organization:
    """Get organization by ID with permission check."""
    # Super admin can view any organization
    if current_user.role == UserRole.SUPER_ADMIN:
        return await get_organization(db, organization_id)
    
    # Regular admin can only view their own organization
    if current_user.role == UserRole.ADMIN:
        if current_user.organization_id != organization_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You can only view your own organization"
            )
        return await get_organization(db, organization_id)
    
    # Regular users can only view their own organization
    if current_user.organization_id != organization_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only view your own organization"
        )
    return await get_organization(db, organization_id)

async def update_existing_organization_service(
    db: AsyncSession,
    organization_id: int,
    organization_data: OrganizationUpdate,
    current_user: User
) -> Organization:
    """Update an organization with permission check."""
    if not await check_organization_admin_access(db, organization_id, current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only organization admin or super admin can update organization"
        )
    return await update_organization(db, organization_id, organization_data)

async def delete_existing_organization_service(
    db: AsyncSession,
    organization_id: int,
    current_user: User
) -> None:
    """Delete an organization with permission check."""
    if not await check_organization_admin_access(db, organization_id, current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only organization admin or super admin can delete organization"
        )
    await delete_organization(db, organization_id)

async def add_user_to_organization_service(
    db: AsyncSession,
    organization_id: int,
    user_data: OrganizationUserAdd,
    current_user: User
):
    """Add a user to an organization (Admin/Super Admin only)"""
    if current_user.role not in [UserRole.ADMIN, UserRole.SUPER_ADMIN]:
        raise HTTPException(status_code=403, detail="Not authorized")
    return await add_organization_user(db, organization_id, user_data)


    





# async def update_organization_chatbot_service(
#     db: AsyncSession,
#     chatbot_data: UpdateChatbotConfigRequest,
#     current_user: User,
#     website_links, 
#     document_files
# ) -> ChatbotConfig:
#     """Update chatbot with permission check"""
#     if current_user.role not in [UserRole.SUPER_ADMIN, UserRole.ADMIN]:
#         raise HTTPException(
#             status_code=403,
#             detail=f"Regular users cannot update chatbot."
#         )
#     # Get the chatbot first
#     query = select(ChatbotConfig).filter(
#         ChatbotConfig.id == chatbot_data.chatbot_id,
#         ChatbotConfig.organization_id == chatbot_data.organization_id
#     )
    
#     result = await db.execute(query)
#     chatbot = result.scalar_one_or_none()
    
#     if not chatbot:
#         raise HTTPException(status_code=404, detail="Chatbot not found")
    
#     # Check if user belongs to the organization
#     if current_user.organization_id != chatbot_data.organization_id:
#         raise HTTPException(
#             status_code=403,
#             detail="You can only update chatbots from your organization"
#         )
    
#     # Update chatbot fields
   
#     chatbot.llm_prompt = chatbot_data.llm_prompt
#     chatbot.guardrails = chatbot_data.guardrails

#     db.add(chatbot)
#     await db.commit()
#     await db.refresh(chatbot)
    
#     if document_files:
#         for document_file in document_files:
#             document_filename = f"{chatbot_data.organization_id}_{chatbot.id}_{document_file.filename}"
#             document_path = f"documents/{document_filename}"
#             os.makedirs(os.path.dirname(document_path), exist_ok=True)
#             # Save the document file
#             content = await document_file.read()
#             with open(document_path, "wb") as f:
#                 f.write(content)
            
#             await insert_document_entry(chatbot.id, document_file.filename, document_file.content_type,'Uploaded',db)
        
#     if website_links:
#         for website_link in website_links:
#             if website_link.sweep_domian:
#                 await insert_webscrap_entry(chatbot.id, website_link.website_link, website_link.sweep_domian, 'url','Uploaded',db)
#             else:
#                 await insert_webscrap_entry(chatbot.id, website_link.website_link, website_link.sweep_url, 'url','Uploaded',db)

#     if chatbot_data.qa_templates:
#         for qa in chatbot_data.qa_templates:
#             qa_template = QATemplate(
#                 chatbot_id=chatbot.id,
#                 question=qa.question,
#                 answer=qa.answer,
#             )
#             db.add(qa_template)
    
#     await db.commit()
#     return chatbot

    