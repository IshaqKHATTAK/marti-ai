from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import HTTPException, status, Response, Request
from fastapi.responses import JSONResponse
from app.models.user import User, UserRole, UserSession, Plan
from app.models.chatbot_model import ChatbotConfig, ChatbotGuardrail
from app.schemas.request.user import UserCreate, LoginUser, UserOnboardRequest, UserUpdate, AdminSignupRequest
from app.common.security import SecurityManager
from datetime import datetime, timezone
from app.schemas.response.organization import  ChatbotInfo
from sqlalchemy import select
from app.schemas.response.user import LoginResponse
from app.utils import database_helper
from app.services.organization import _role_based_checks
from app.services.organization import create_organization
from app.schemas.request.organization import OrganizationCreate
from sqlalchemy.orm.attributes import flag_modified
from typing import List
from app.utils.user_helpers import validate_user_role, toggle_chatbot_status,toggle_user_status, toggle_true_user_status, toggle_false_user_status, toggle_user_paid_status as toggle_paid_status
from app.utils.db_helpers import get_user_by_email, get_user_by_id, get_chatbot_by_id
from app.models.user import Plan
from sqlalchemy import delete
from app.utils.database_helper import format_user_chatbot_permissions
from app.services.organization import _fine_grain_role_checks


security_manager = SecurityManager()

async def toggle_chatbot_memory(
        db, 
        organization_id,
        chatbot_id,
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

    # if current_user.role == UserRole.ADMIN:
    #     chatbot = await get_chatbot_by_id(db, chatbot_id)
        
    #     if chatbot.organization_id != organization_id:
    #         raise HTTPException(status_code=403, detail="Not authorized")
    #     await toggle_chatbot_status(chatbot) 
    #     db.add(chatbot)

    chatbot = await get_chatbot_by_id(db, chatbot_id)
    if chatbot.organization_id != organization_id:
        raise HTTPException(status_code=403, detail="organization not found")
    await toggle_chatbot_status(chatbot) 
    db.add(chatbot)
    
    await db.commit()
    await db.refresh(chatbot)
    chatbot = await get_chatbot_by_id(db, chatbot_id)
    return chatbot



async def toggle_user_active_status_service(
    organization_id : int,
    user_id: int,
    current_user: User,
    db: AsyncSession
) -> User:
    """Toggle user active status with admin authorization."""
    if current_user.role not in [UserRole.ADMIN,UserRole.SUPER_ADMIN]:
        raise HTTPException(status_code=403, detail="Not authorized")
    if current_user.role == UserRole.ADMIN and current_user.organization_id != organization_id:
        raise HTTPException(status_code=403, detail="Not authorized")
    
    if current_user.role == UserRole.ADMIN:
        curr_user = await get_user_by_id(db, user_id)

        if curr_user.organization_id != organization_id:
            raise HTTPException(status_code=403, detail="Not authorized")
        await toggle_user_status(curr_user) 
        db.add(curr_user)
        
    if current_user.role == UserRole.SUPER_ADMIN:
        curr_user = await get_user_by_id(db, user_id)
        if curr_user.organization_id != organization_id:
            raise HTTPException(status_code=403, detail="organization not found")
        # user = await toggle_user_status(user)
        if curr_user.role == UserRole.USER:
            await toggle_user_status(curr_user)
            db.add(curr_user)
        elif curr_user.role == UserRole.ADMIN:
            await toggle_user_status(curr_user)
            db.add(curr_user)
            # if curr_user.is_active:

            #     users = await db.execute(
            #         select(User).filter(User.organization_id == organization_id)
            #     )
            #     users = users.scalars().all()

            #     # Toggle the status for all users
            #     for curr_user in users:
            #         curr_user = await toggle_false_user_status(curr_user) 
            #         db.add(curr_user)
            #         await db.commit()
            #         await db.refresh(curr_user)
            # else:
            #     users = await db.execute(
            #         select(User).filter(User.organization_id == organization_id)
            #     )
            #     users = users.scalars().all()

            #     # Toggle the status for all users
            #     for curr_user in users:
            #         curr_user = await toggle_true_user_status(curr_user) 
            #         db.add(curr_user)
                    # await db.commit()
                    # await db.refresh(curr_user)
    
    await db.commit()
    await db.refresh(curr_user)
    user = await get_user_by_id(db, user_id)
    return user


async def create_user(
    db: AsyncSession, 
    signup_data: AdminSignupRequest
) -> User:
    """Sign up new admin with their organization."""
    async with db.begin() as transaction:
        try:
            # Check if user exists
            if await get_user_by_email(db, signup_data.email):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Email already registered"
                )
            
            # Create organization first
            org_data = OrganizationCreate(name=signup_data.organization_name)
            org = await create_organization(db, org_data)
            
            # Create admin user with organization
            hashed_password = security_manager.get_password_hash(signup_data.password)
            db_user = User(
                name=signup_data.name,
                email=signup_data.email,
                hashed_password=hashed_password,
                role=UserRole.ADMIN,
                is_active=True,
                organization_id=org.id,
                country=signup_data.country
            )
            
            db.add(db_user)
            await db.flush()  # Flush but don't commit yet
            
            # If we get here, everything succeeded
            await transaction.commit()
            return db_user
            
        except Exception as e:
            # If anything fails, rollback the entire transaction
            await transaction.rollback()
            
            # If it's our HTTP exception, re-raise it
            if isinstance(e, HTTPException):
                raise e
                
            # Otherwise, wrap it in a 500 error
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to create user"
            )


async def login_user(
    login_data,
    response: Response,
    request: Request,
    db: AsyncSession
) -> LoginResponse:
    # Get user and verify credentials
    user = await get_user_by_email(db, login_data.username)
    if not user:
        raise HTTPException(status_code=400, detail="Incorrect email or password")
    
    if not security_manager.verify_password(login_data.password, user.hashed_password):
        raise HTTPException(status_code=400, detail="Incorrect email or password")
    
    # Check for existing valid session
    # session_query = select(UserSession).filter(
    #     UserSession.user_id == user.id,
    #     UserSession.expires_at > datetime.now(timezone.utc),
    #     UserSession.is_valid == True
    # )
    # session = await db.execute(session_query)
    # session = session.scalar_one_or_none()
    await db.execute(delete(UserSession).where(UserSession.user_id == user.id))
    await db.commit()

    # if session:
    #     # If valid session exists, just create new access token
    #     access_token = security_manager.create_access_token(user.id)
    #     return LoginResponse(
    #         access_token=access_token,
    #         refresh_token=session.refresh_token,
    #         expires_in=str(session.expires_at),
    #         token_type="Bearer"
    #     )
    return await security_manager.create_session(user, response, request, db)

def get_utc_now():
    return datetime.now(timezone.utc)

async def onboard_user(
    db: AsyncSession, 
    user_data: UserOnboardRequest
) -> User:
    """Onboard a new user with organization."""
    # Get user
    query = select(User).filter(User.email == user_data.email)
    result = await db.execute(query)
    user = result.scalar_one_or_none()
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found. Please sign up first at /users/signup"
        )
    
    # Prevent double onboarding
    if user.organization_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User is already onboarded to an organization"
        )
    
    # Update user's name if provided
    if user_data.name:
        user.name = user_data.name
    
    # If organization name is provided, create and link it
    if user_data.organization_name:
        org_data = OrganizationCreate(name=user_data.organization_name)
        org = await create_organization(db, org_data)
        user.organization_id = org.id
        user.role = UserRole.ADMIN  # First user in org becomes admin
        user.is_onboarded = True  # Track onboarding status
    
    await db.commit()
    await db.refresh(user)
    
    return user

async def get_user_list(
    skip: int,
    limit: int,
    current_user: User,
    db: AsyncSession
) -> List[User]:
    """Get list of users with role-based access control."""
    if current_user.role not in [UserRole.SUPER_ADMIN, UserRole.ADMIN]:
        raise HTTPException(status_code=403, detail="Not authorized")
    if current_user.role == UserRole.ADMIN:
        # Only return users with role USER
        query = select(User).filter(
            User.organization_id == current_user.organization_id,
            User.role == UserRole.USER
        ).offset(skip).limit(limit)
    elif current_user.role == UserRole.SUPER_ADMIN:
        # Return all users in the organization
        query = select(User).filter(User).offset(skip).limit(limit)
    
    result = await db.execute(query)
    return result.scalars().all()

async def get_user_by_id_with_auth(
    user_id: int,
    current_user: User,
    db: AsyncSession
) -> User:
    """Get user by ID with authorization check."""
    user = await get_user_by_id(db, user_id)
    return user

async def update_user_details(
    user_id: int,
    user_data: UserUpdate,
    current_user: User,
    db: AsyncSession
) -> User:
    """Update user details with proper permission checks"""
    # Get user to update
    user = await get_user_by_id(db, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Permission check - allow SUPER_ADMIN or if user is updating their own details
    if current_user.role != UserRole.SUPER_ADMIN and current_user.id != user_id:
        raise HTTPException(
            status_code=403,
            detail="Not authorized to update this user"
        )

    # Update user fields
    update_data = user_data.dict(exclude_unset=True)
    for key, value in update_data.items():
        setattr(user, key, value)

    await db.commit()
    await db.refresh(user)
    return user


async def toggle_user_paid_status_service(
    user_id: int,
    current_user: User,
    db: AsyncSession
) -> User:
    """Toggle user paid status with admin/super-admin authorization."""
    if current_user.role not in [UserRole.ADMIN, UserRole.SUPER_ADMIN]:
        raise HTTPException(status_code=403, detail="Not authorized")
    
    user = await get_user_by_id(db, user_id)
    user = await toggle_paid_status(user)
    await db.commit()
    await db.refresh(user)
    return user

async def create_organization_user(
    db: AsyncSession, 
    user_data: UserCreate,
    current_user: User
) -> User:
    """Create a new user for admin's organization."""
    # Check if user exists
    if await get_user_by_email(db, user_data.email):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already registered"
        )
    
    # Create user with admin's organization
    hashed_password = security_manager.get_password_hash(user_data.password)
    db_user = User(
        name=user_data.name,
        email=user_data.email,
        hashed_password=hashed_password,
        role=UserRole.USER,  # Always USER
        is_active=True,
        organization_id=current_user.organization_id
    )
    
    db.add(db_user)
    await db.commit()
    await db.refresh(db_user)
    
    return db_user

async def create_user_service(
    db: AsyncSession, 
    signup_data: AdminSignupRequest
) -> User:
    """Sign up new admin with their organization."""
    # Check if user already exists using utility function
    if await get_user_by_email(db, signup_data.email):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already registered"
        )
    
    # Create organization first
    org_data = OrganizationCreate(name=signup_data.organization_name, logo=signup_data.logo, website_link=signup_data.website_link)
    org = await create_organization(db, org_data)
    
    # Create admin user with organization
    hashed_password = security_manager.get_password_hash(signup_data.password)
    db_user = User(
        name=signup_data.name,
        email=signup_data.email,
        hashed_password=hashed_password,
        role=UserRole.ADMIN,
        is_active=True,
        organization_id=org.id,
        country=signup_data.country,
        current_plan = Plan.free,
        add_on_features = ["image_generation"]
        # is_verified = True,
        # verified_at = datetime.utcnow()
    )
    
    db.add(db_user)
    await db.commit()
    await db.refresh(db_user)
    external_chatbot = await create_external_chatbot(org.id,db)
    if external_chatbot:
        guardrails_list = ["Avoid answering questions about religions."]
        
        for rails in guardrails_list:
            guardrails = ChatbotGuardrail(
                guardrail_text=rails,
                chatbot_id = external_chatbot.id
            )
            db.add(guardrails)
            await db.commit()
            await db.refresh(guardrails)
    return db_user

async def create_user(
    db: AsyncSession,
    signup_data: AdminSignupRequest
) -> User:
    """Create new user with organization"""
    return await create_user_service(db, signup_data)

async def create_external_chatbot(org_id, db, llm_name = 'gpt-4o-mini',llm_tone = 'friendly'):
    
    chatbot_config_data = ChatbotConfig(
        chatbot_name = 'ASK MARTI',
        organization_id = org_id,
        llm_model_name = llm_name,
        llm_prompt = "",
        llm_role = llm_tone,
        chatbot_type = 'External',
        llm_temperature = 0.3
    )
    db.add(chatbot_config_data)
    await db.commit()
    await db.refresh(chatbot_config_data)
    return chatbot_config_data


async def create_user_in_organization(
    db: AsyncSession,
    user_data: UserCreate,
    organization_id: int
) -> User:
    """Create a new user and assign to organization"""
    # Check if user exists
    
    if await get_user_by_email(db, user_data.email):
        raise HTTPException(
            status_code=400,
            detail="Email already registered"
        )
    # Create user with organization ID
    hashed_password = security_manager.get_password_hash(user_data.password)
    
    db_user = User(
        name=user_data.name,
        email=user_data.email,
        hashed_password=hashed_password,
        role=UserRole.USER,
        is_active=user_data.active,
        is_verified = True,
        verified_at = datetime.utcnow(),
        group_ids = user_data.group_ids,
        # chatbot_ids = user_data.chatbot_ids,
        organization_id=organization_id,
        is_walkthrough_completed = True
    )
    
    db.add(db_user)
    await db.commit()
    await db.refresh(db_user)
    return db_user

async def verify_user_email(db: AsyncSession, email: str) -> User:
    """Mark user email as verified"""
    user = await get_user_by_email(db, email)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )
    
    user.is_verified = True
    user.verified_at = datetime.utcnow()
    user.is_paid = True
    user.current_plan = Plan.free
    await db.commit()
    await db.refresh(user)
    return user

async def update_user_plan(db: AsyncSession, id: int) -> User:
    """Updatet user plan."""
    query = select(User).filter(User.id == id)
    result = await db.execute(query)
    user =  result.scalar_one_or_none()
    user.is_paid = True
    user.current_plan = 'Free'
    
    
async def async_logout_user(session: AsyncSession, user_data):
    # Fetch user tokens asynchronously
    result = await session.execute(select(UserSession).where(UserSession.user_id == user_data.id))
    user_tokens = result.scalars().all()

    if not user_tokens:
        raise HTTPException(status_code=400, detail="You are already logged out.")
    
    # Delete all user tokens asynchronously
    await session.execute(delete(UserSession).where(UserSession.user_id == user_data.id))
    await session.commit()
    return "You are logged out."

async def delete_org_group(group_id, db: AsyncSession, organization_id, current_user):
    #fech the user whcih are connected with the group
    # remove the group from all those users.
    await _role_based_checks(current_user, organization_id)
    users_of_group = await database_helper.get_users_of_group(db, group_id)
    for user in users_of_group:
        if user.group_ids and group_id in user.group_ids:
            user.group_ids.remove(group_id)
            flag_modified(user, "group_ids")
            db.add(user)
    results = await database_helper.delete_rbac_group_by_id(db, group_id)
    
    await db.commit()

    return




async def get_org_groups(organization_id, skip, limit, db: AsyncSession, current_user, group_name):
    await _role_based_checks(current_user, organization_id)
    groups = await database_helper.get_rbac_groups_by_org_id_paginated(skip, limit, db, organization_id, group_name)
    response = [
        {
            "id": group.id,
            "form_submission":group.form_submission,
            "name": group.name,
            "attributes": group.attributes,
            "organization_id": group.organization_id,
        }
        for group in groups
    ]

    return response

async def get_chatbot(chatbot_id, organization_id, db):
    query = (
        select(ChatbotConfig)
        .filter(
            ChatbotConfig.id == chatbot_id,
            ChatbotConfig.organization_id == organization_id
        )
    )
    
    result = await db.execute(query)
    chatbot = result.scalar_one_or_none()
    return chatbot

async def create_group(db: AsyncSession, name: str, form_submission:bool, attributes: List[dict], organization_id: int, current_user):
    await _role_based_checks(current_user, organization_id)
    from app.services.organization import list_organization_chatbots_service
    chatbots =  await list_organization_chatbots_service(db, organization_id, current_user)
    organization_chatbots_ids = [bot.id for bot in chatbots]
    attributes = [user_bot for user_bot in attributes if user_bot["chatbot_id"] in organization_chatbots_ids]
    print(f'attribute after filter == {attributes}')
    # chatbots_ids_added = [bot_id for bot_id in chatbots_ids_added if bot_id in organization_chatbots_ids]
    response = await database_helper.create_rbac_groups(db, name, form_submission,attributes_list=attributes, organization_id=organization_id)
    attributes = []
    for attr in response.attributes:
        print(f'attr == {attr}')
        chatbot_data = await get_chatbot(attr["chatbot_id"], response.organization_id, db)
        attr["chatbot_name"] = chatbot_data.chatbot_name
        attributes.append(attr)
    response.attributes = attributes
    return response

async def update_organization_group(db: AsyncSession, name: str, form_submission, attributes: List[dict], organization_id: int, group_id, current_user):
    await _role_based_checks(current_user, organization_id)
    from app.services.organization import list_organization_chatbots_service
    chatbots =  await list_organization_chatbots_service(db, organization_id, current_user)
    organization_chatbots_ids = [bot.id for bot in chatbots]
    attributes = [user_bot for user_bot in attributes if user_bot["chatbot_id"] in organization_chatbots_ids]
    print(f'attribute after filter == {attributes}')
    
    response = await database_helper.update_rbac_groups(db, name, form_submission, attributes_list=attributes, organization_id=organization_id, group_id = group_id)
    attributes = []
    for attr in response.attributes:
        print(f'attr == {attr}')
        chatbot_data = await get_chatbot(attr["chatbot_id"], response.organization_id, db)
        attr["chatbot_name"] = chatbot_data.chatbot_name
        attributes.append(attr)

    response.attributes = attributes
    return response

async def create_student_agent(org_id, db, llm_name = 'gpt-4o-mini',llm_tone = 'friendly', bot_type = None):
    print(f'specialized type agent == {bot_type}')
    chatbot_config_data = ChatbotConfig(
        chatbot_name = 'Teacher Agent',
        organization_id = org_id,
        llm_model_name = llm_name,
        llm_prompt = "",
        llm_role = llm_tone,
        chatbot_type = 'Internal',
        llm_temperature = 0.2,
        specialized_type = bot_type
    )
    db.add(chatbot_config_data)
    await db.commit()
    await db.refresh(chatbot_config_data)
    return chatbot_config_data
