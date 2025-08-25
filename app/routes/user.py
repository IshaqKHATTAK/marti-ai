from fastapi import APIRouter, Depends, HTTPException, status, Response, Request, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from app.common.database_config import get_async_db
from app.models.user import User
from app.utils.database_helper import get_rbac_groups_by_org_id
from fastapi import Query
from app.schemas.request.user import UserCreate, GroupCreate, GroupIdSchema, GroupPatch, UserUpdate, PasswordChange,VerifyUser,LoginUser, UserOnboardRequest, AdminSignupRequest, PasswordResetRequest, ForgetData
from app.schemas.response.user import UserResponse, LoginResponse, PasswordResetResponse,ALLGroupOut, PaginatedResponseGroup, GroupOut, UpdateGroupOut
from app.models.user import UserRole
from app.services.user import (
    create_user,
    login_user,
    onboard_user,
    get_user_list,
    get_org_groups,
    delete_org_group,
    update_organization_group,
    get_chatbot,
    get_user_by_id_with_auth,
    update_user_details,
    create_group,
    toggle_user_active_status_service,
    toggle_user_paid_status_service,
    create_user,
    async_logout_user
)
from typing import Optional
from fastapi.responses import JSONResponse
from typing import List
from app.services.auth import get_current_user
from fastapi.security import OAuth2PasswordRequestForm
from app.common.security import SecurityManager
from app.services import email
from app.utils.db_helpers import get_user_by_email, get_user_organization_admin,get_organization, insert_logs
from datetime import timedelta
from app.utils.database_helper import format_user_chatbot_permissions

router = APIRouter(
    prefix="/api/v1/users",
    tags=["users"],
    dependencies=[Depends(get_current_user)]
)

public_router = APIRouter(
    prefix="/api/v1/users", #
    tags=["users"]
)
from app.common.env_config import get_envs_setting

envs = get_envs_setting()

@public_router.get("/refresh", response_model=LoginResponse)
async def refresh_token(
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_async_db)
):
    """Generate new access token using refresh token from cookie"""
    # Get refresh token from cookie
    print(f'starts')
    security_manager = SecurityManager()
    refresh_token = request.cookies.get(security_manager.refresh_cookie_name)
    print(f'refresh token == {refresh_token}')
    if not refresh_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token missing"
        )
    
    # Use security manager to refresh token
    tokens = await security_manager.refresh_access_token(refresh_token, db)
    print(f'token = {tokens}')
    # Set new refresh token cookie
    security_manager.set_session_cookies(response, refresh_token)
    print(f'fnsihed')
    return tokens


@public_router.post('/forget-password', status_code = status.HTTP_200_OK)
async def forget_password(forget_data: ForgetData, backgound_task:BackgroundTasks, db: AsyncSession = Depends(get_async_db)):
    '''
    reset your password using the old password and email.

        Parameters:
            forget_data (ForgetData): an instance of ForgetData schema for forget password containing uername,and email.
            backgound_task (BackgroundTasks): fastapi background utility.
            session (Session): database session utility with seperate database connection as dependency.
        return:
            json response
    '''
    if not forget_data.email and forget_data.first_name and forget_data.last_name:
        raise HTTPException(status_code=400, detail="Enter first name last name and email")
    from app.utils.db_helpers import get_user_by_email
    userdata = await get_user_by_email(db=db, email=forget_data.email)
    if not userdata:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No such user exist")
    if not userdata.verified_at:
        raise HTTPException(status_code=400, detail="Your account is not verified. Please check your email inbox to verify your account.")
    
    if not userdata.is_active:
        raise HTTPException(status_code=400, detail="Your account has been dactivated. Please contact support.")
   
    verification_token = SecurityManager.create_verification_token(userdata)
    # await email.get_forget_password(forget_data,backgound_task, db)
    backgound_task.add_task(
        email.send_forgot_password_email,
        forget_data.email,
        forget_data.name,
        verification_token
    )
    return JSONResponse({"message": "Password reset link has been send by your email."})

@public_router.post("/verify-forget-password", response_model=PasswordResetResponse)
async def reset_password_endpoint(
    request: PasswordResetRequest, 
    db: AsyncSession = Depends(get_async_db)
):
    return await email.reset_password(request.token, request.useremail, request.new_password, db)


@public_router.post("/signup", response_model=UserResponse)
async def signup(
    signup_data: AdminSignupRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_async_db)
):
    """Sign up new admin with their organization and send verification email"""
    # Create user with is_verified=False
    user = await create_user(db, signup_data)
    
    # Generate verification token
    verification_token = SecurityManager.create_verification_token(user)
    #Send verification email in background
    background_tasks.add_task(
        email.send_verification_email,
        user.email,
        user.name,
        verification_token
    )
    if not user.group_ids:
        user.group_ids = []
    
    new_group = await create_group(
        db=db,
        name="Event Reviewers",
        form_submission = True,
        attributes=[],
        organization_id=user.organization_id,
        current_user = user
    )
    await insert_logs(user.organization_id, "A new organization has been successfully registered within the system.", f'{user.name}', "Organization", db)
    
    return UserResponse.model_validate(user)

from app.utils import database_helper
from collections import defaultdict

@router.get("/me", response_model=UserResponse)
async def get_current_user_details(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db)
):
    """Get current user details"""
    org_data = await get_organization(db, current_user.organization_id)
    image_generation = False
    # image_generation = current_user.add_on_features.get("image_generation", False)
    
    if current_user.add_on_features:
        if "image_generation" in current_user.add_on_features:
            image_generation = True
    
    user_groups, form_submission = await format_user_chatbot_permissions(db,current_user.organization_id, current_user.group_ids)
    
    print(f'user group == {user_groups}')
    org_plan = None
    if current_user.role in [UserRole.USER]:
        #get admin of organization
        admin_user = await get_user_organization_admin(db, current_user.organization_id)
        if admin_user.add_on_features:
            if "image_generation" in admin_user.add_on_features:
                image_generation = True
        org_plan = admin_user.current_plan
    if current_user.role == UserRole.ADMIN:
        from app.services.payment import get_current_user_seats
        total_users_paid = await get_current_user_seats(stripe_customer_id = current_user.stripeId)
        print(f'total users paid == {total_users_paid}')
    return UserResponse(
        id = current_user.id,
        name = current_user.name, 
        email = current_user.email,
        role = current_user.role,
        total_messages = current_user.total_messages,
        organization_id = current_user.organization_id,
        organization_name = org_data.name if org_data else "",
        # chatbots  = [],
        groups = user_groups,
        form_submission = form_submission,
        is_active = current_user.is_active,
        is_paid = current_user.is_paid,
        current_plan = current_user.current_plan if current_user.role == UserRole.ADMIN else org_plan,
        created_at = current_user.created_at,
        updated_at = current_user.updated_at,
        is_verified  =current_user.is_verified,
        verified_at = current_user.verified_at,
        avatar_url  = current_user.avatar_url,
        image_generation = image_generation,
        is_walkthrough_completed = current_user.is_walkthrough_completed,
        trail_expirey =  current_user.created_at + timedelta(days=envs.FREE_TRAIL_DAYS),
        is_user_consent_given = current_user.is_user_consent_given,
        total_users_paid = total_users_paid if current_user.role == UserRole.ADMIN else 0
    )

@public_router.post("/login", response_model=LoginResponse)
async def login(
    response: Response,
    request: Request,
    login_data:  OAuth2PasswordRequestForm = Depends(),
    db: AsyncSession = Depends(get_async_db)
):
    """Login user if email is verified"""
    # Check if user is verified before allowing login
    user = await get_user_by_email(db, login_data.username)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Account not found"
        )
    if not user.is_verified:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Please verify your email before logging in"
        )
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Your account has been deactivted."
        )
    if user.role == UserRole.USER:
        admin = await get_user_organization_admin(db=db, organization_id=user.organization_id)
        if not admin.is_active:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Your organization has been deactivted. Please refer to your organization admin."
            )   
    return await login_user(login_data, response, request, db)

# @public_router.post("/onboard", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
# async def onboard(user_data: UserOnboardRequest, db: AsyncSession = Depends(get_async_db)):
#     return await onboard_user(db, user_data)

@router.patch("/{user_id}/toggle-active")
async def toggle_user_active_status(
    user_id: int,
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(get_current_user)
) -> UserResponse:
    user = await toggle_user_active_status_service(user_id, current_user, db)
    return UserResponse.model_validate(user)

@router.patch("/{user_id}/toggle-paid")
async def toggle_user_paid_status(
    user_id: int,
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(get_current_user)
) -> UserResponse:
    user = await toggle_user_paid_status_service(user_id, current_user, db)
    return UserResponse.model_validate(user)


@router.patch("/walkthrough-completed")
async def walthrough_user(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db)
):
    current_user.is_walkthrough_completed = True
    db.add(current_user)
    await db.commit()
    return JSONResponse({"message": "walkthrough completed."})

@router.patch("/user-consent")
async def walthrough_user(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db)
):
    current_user.is_user_consent_given = True
    db.add(current_user)
    await db.commit()
    return JSONResponse({"message": "User has given consent to access his document."})


# @router.get("/{user_id}", response_model=UserResponse)
# async def get_user(
#     user_id: int,
#     db: AsyncSession = Depends(get_async_db),
#     current_user: User = Depends(get_current_user)
# ) -> UserResponse:
#     user = await get_user_by_id_with_auth(user_id, current_user, db)
#     return UserResponse.model_validate(user)

# @router.get("/", response_model=List[UserResponse])
# async def get_users(
#     skip: int = 0,
#     limit: int = 10,
#     db: AsyncSession = Depends(get_async_db),
#     current_user: User = Depends(get_current_user)
# ) -> List[UserResponse]:
#     users = await get_user_list(skip, limit, current_user, db)
#     return [UserResponse.model_validate(user) for user in users]

# @router.patch("/{user_id}", response_model=UserResponse)
# async def update_user(
#     user_id: int,
#     user_data: UserUpdate,
#     db: AsyncSession = Depends(get_async_db),
#     current_user: User = Depends(get_current_user)
# ):
#     """Update user details"""
#     return await update_user_details(user_id, user_data, current_user, db)


@public_router.get("/verify/{useremail}/{token}")
async def verify_email(
    token: str,
    useremail: str,
    db: AsyncSession = Depends(get_async_db)
):
    if not useremail and token:
        raise HTTPException(status_code=400, detail="Enter email and token both.")
    return await email.verify_email(token, useremail, db)

@router.post("/change-password", status_code = status.HTTP_200_OK)
async def get_current_user_details(
    request: PasswordChange, 
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(get_current_user)
):
    """Reset the current user password."""
    if request.new_password != request.confirm_password:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="New passwords do not match"
        )
    await insert_logs(current_user.organization_id, f"{current_user.organization.name} has been successfully changed password.", f'{current_user.name}', "Organization", db)
    
    return await email.change_user_password(current_user, request.current_password, request.new_password, db)

@router.post('/logout', status_code = status.HTTP_200_OK)
async def logout_user(response: Response,session: AsyncSession = Depends(get_async_db), current_user: User = Depends(get_current_user)):
    try:
        response_data = await async_logout_user(session, current_user)
        response.delete_cookie(key="refresh_token", domain=envs.COOKIE_DOMAIN)
        response.delete_cookie(key="access_token", domain=envs.COOKIE_DOMAIN)
    
        return JSONResponse({"message": response})
    except HTTPException as e:
        raise e
    
@router.post("/organization/{organization_id}/group", response_model=GroupOut)
async def create_organization_group(
    organization_id: int,
    payload: GroupCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db)
):
    if not current_user.organization_id:
        raise HTTPException(status_code=400, detail="User must be part of an organization to create groups.")
    if payload.form_submission:
        raise HTTPException(status_code=400, detail="You can not created group with form review access.")
    
    new_group = await create_group(
        db=db,
        name=payload.name,
        form_submission = False,
        attributes=[attr.dict() for attr in payload.attributes],
        organization_id=organization_id,
        current_user = current_user
    )
    await insert_logs(organization_id, "A new role has been defined and added to the Role-Based Access Control system.", f'{current_user.name}', "RBAC", db)
    
    return new_group

@router.patch("/organization/{organization_id}/group", response_model=UpdateGroupOut)
async def update_group(
    organization_id: int,
    payload: GroupPatch,
    form_submission: Optional[bool] = None,
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(get_current_user)
):
    print(f'form submission value == {form_submission}')
    
    new_group = await update_organization_group(
        db=db,
        form_submission = form_submission,
        name = payload.name,
        attributes=[attr.dict() for attr in payload.attributes],
        organization_id=organization_id,
        group_id = payload.id,
        current_user = current_user
    )
    await insert_logs(organization_id, "The details or permissions associated with a role have been modified.", f'{current_user.name}', "RBAC", db)
    
    return new_group

@router.get("/organization/{organization_id}/group", response_model=PaginatedResponseGroup)
async def get_organization_groups(
    organization_id: int,
    skip: int = 0,
    limit: int = 10,
    group_name: str = Query(None), 
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db)
):
    # group_ids = current_user.group_ids or []
    # if not group_ids:
    #     return []
    response =  await get_org_groups(organization_id, skip, limit, db, current_user, group_name)
    total_groups = await get_rbac_groups_by_org_id(db, organization_id, group_name)
    groups_formatted = []
    for group in response:
        attributes = []
        for attr in group["attributes"]:
            
            chatbot_data = await get_chatbot(attr["chatbot_id"], group["organization_id"], db)
            attr["chatbot_name"] = chatbot_data.chatbot_name
            attributes.append(attr)
            
        group["attributes"] = attributes
        print(f'group == {group}')
        # groups_formatted.append(ALLGroupOut(
        #         id = group["id"],
        #         name =  group["name"],
        #         form_submission = group["form_submission"],
        #         attributes = group["attributes"],
        #         organization_id = group["organization_id"]
        #     ))
    return PaginatedResponseGroup(list_of_all_groups = response,
                                  total_groups = len(total_groups))

@router.delete("/organization/{organization_id}/group", status_code=status.HTTP_200_OK)
async def delete_organization_group(
    organization_id: int,
    payload: GroupIdSchema,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db)
):
    # group_ids = current_user.group_ids or []
    # if not group_ids:
    #     raise HTTPException(status_code=400, detail="You dont have any group to delete.")
    response =  await delete_org_group(payload.id, db, organization_id, current_user)
    await insert_logs(organization_id, "An existing role has been removed from the Role-Based Access Control system.", f'{current_user.name}', "RBAC", db)
    
    return JSONResponse({"message": "You have succefully deleted the group."})
