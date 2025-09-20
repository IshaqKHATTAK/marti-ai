from fastapi_mail import FastMail, MessageSchema, ConnectionConfig
from fastapi import BackgroundTasks
from typing import List
from pydantic import EmailStr
from app.common.env_config import get_envs_setting
from sqlalchemy.ext.asyncio import AsyncSession
from app.common.database_config import get_async_db
from app.services import payment
from app.utils.db_helpers import get_user_by_email
from app.common.security import SecurityManager
# from app.services.user import verify_user_email
from fastapi import Depends, HTTPException, status
from app.models.user import User
from io import BytesIO


settings = get_envs_setting()

# Email configuration using environment variables
conf = ConnectionConfig(
    MAIL_USERNAME = settings.MAIL_USERNAME,
    MAIL_PASSWORD = settings.MAIL_PASSWORD,
    MAIL_FROM = settings.MAIL_FROM,
    MAIL_PORT = settings.MAIL_PORT,
    MAIL_SERVER = settings.MAIL_SERVER,
    MAIL_STARTTLS = settings.MAIL_STARTTLS,
    MAIL_SSL_TLS = settings.MAIL_SSL_TLS,
    USE_CREDENTIALS = settings.MAIL_USE_CREDENTIALS,
    MAIL_DEBUG=settings.MAIL_DEBUG,
    MAIL_FROM_NAME=settings.MAIL_FROM_NAME
)
    

async def send_event_update_notification_to_admin(
    admin_email: EmailStr,
    user_name: str,
    user_email: str,
    evnet_title: str,
    user_message: str
):
    try:
        # Optional: Link to event or admin panel
        event_admin_url = f"{settings.FRONTEND_HOST}requests"

        # # Email template
        # html = f"""
        #     <div style="font-family: Arial, sans-serif; color: #333; max-width: 600px; margin: auto; padding: 20px; border: 1px solid #ddd; border-radius: 8px;">
                
        #         <h2 style="color: #2c3e50;">Event Updated: {evnet_title}</h2>
        #         <p>Dear Admin,</p>
                
        #         <p><strong>{user_name}</strong> (<a href="mailto:{user_email}">{user_email}</a>) has responded to your feedback and updated the event information for:</p>
                
        #         <p style="margin-left: 20px;"><strong>Project Title:</strong> {evnet_title}</p>

        #         <p style="margin-top: 20px;"><strong>User's Message:</strong></p>
        #         <div style="background-color: #f9f9f9; padding: 10px; border-left: 4px solid #3498db;">
        #             {user_message}
        #         </div>

        #         <p style="margin-top: 30px;">You can review the updated event details using your admin panel.</p>
        #         <div style="text-align: center; margin-top: 20px;">
        #             <a href="{event_admin_url}" style="padding: 10px 20px; background-color: #3498db; color: white; text-decoration: none; border-radius: 4px;">View Event</a>
        #         </div>

        #         <p style="margin-top: 40px;">Best regards, <br><strong>The MARTI AI System</strong></p>
        #         <div style="text-align: center; margin-top: 30px;">
        #             <img src="https://marti-file-upload-bucket.s3.us-east-2.amazonaws.com/logo/marti-logo-nobg.png" alt="MARTI AI Logo" style="max-width: 120px; height: auto; opacity: 0.8;" />
        #         </div>
        #     </div>
        # """
        # Email template
        html = f"""
        <div style="font-family: Arial, sans-serif; color: #333; max-width: 600px; margin: auto; padding: 20px; border: 1px solid #ddd; border-radius: 8px;">
            
            <h2 style="color: #2c3e50;">Event Updated: {evnet_title}</h2>
            <p>Dear Admin,</p>
            
            <p><strong>{user_name}</strong> (<a href="mailto:{user_email}">{user_email}</a>) has made updates to the event in response to your feedback.</p>
            
            <p style="margin-left: 20px;"><strong>Project Title:</strong> {evnet_title}</p>

            <p style="margin-top: 20px;">You can review the updated event details using your admin panel.</p>
            
            <div style="text-align: center; margin-top: 20px;">
                <a href="{event_admin_url}" style="padding: 10px 20px; background-color: #3498db; color: white; text-decoration: none; border-radius: 4px;">View Event</a>
            </div>

            <p style="margin-top: 40px;">Best regards, <br><strong>The MARTI AI System</strong></p>

            <div style="text-align: center; margin-top: 30px;">
                <img src="https://marti-file-upload-bucket.s3.us-east-2.amazonaws.com/logo/marti-logo-nobg.png" alt="MARTI AI Logo" style="max-width: 120px; height: auto; opacity: 0.8;" />
            </div>
        </div>
      """


        # Create message
        message = MessageSchema(
            subject=f"User Response: {user_name} updated event - {evnet_title}",
            recipients=[admin_email],
            body=html,
            subtype="html"
        )

        # Send email
        fm = FastMail(conf)
        await fm.send_message(message)
        print(f"Admin notification sent to {admin_email}")
        
    except Exception as e:
        print(f"Error sending admin notification email: {e}")
        raise


async def send_incomplete_envent_info(email: EmailStr, name: str, email_message: str, evnet_title):
    try:
        # Create verification link
        event_status_url = f"{settings.FRONTEND_HOST}user-submission"
        # Email template
        html = f"""
                <div style="font-family: Arial, sans-serif; color: #333; max-width: 600px; margin: auto; padding: 20px; border: 1px solid #ddd; border-radius: 8px;">
                    
                    <h2 style="color: #2c3e50;">Hello {name}!</h2>
                    <p>We noticed that some required information is still missing for your event: <strong>{evnet_title}</strong>.</p>
                    <p>You are kindly requested to complete your event details. This will help the Ask MARTI team accurately live stream your event. Please refer to the requirements below and make sure to complete them.</p>
                    
                    <p>{email_message}</p>
                    <p><strong>Note:</strong> Thank you for being a part of our event.</p>
                    
                    <p>Warm regards, <br><strong>The MARTI AI Team</strong></p>
                    <div style="text-align: center; margin-top: 30px;">
                        <img src="https://marti-file-upload-bucket.s3.us-east-2.amazonaws.com/logo/marti-logo-nobg.png" alt="MARTI AI Logo" style="max-width: 120px; height: auto; opacity: 0.8;" />
                    </div>
                </div>
                """


        # Create message
        message = MessageSchema(
            subject=f"Complete Your Event Information for {evnet_title}",
            recipients=[email],
            body=html,
            subtype="html"
        )
        
        # Send email
        fm = FastMail(conf)
        await fm.send_message(message)
        print(f"Verification email sent to {email}")
        
    except Exception as e:
        print(f"Error sending email: {e}")
        raise 
    from fastapi import HTTPException, status
from pydantic import EmailStr
from fastapi_mail import FastMail, MessageSchema

async def event_rejection_email(email: EmailStr, name: str, marti_page: bool, marti_agent: bool):
    try:
        # Validate rejection type
        if marti_page == marti_agent:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                                detail="Invalid rejection request. Only one of marti_page or marti_agent must be True.")

        # Set statement based on rejection type
        if marti_page:
            statement = (
                "Thank you for submitting your event to the MARTI Website. "
                "After a thorough review, we regret to inform you that your event did not meet the criteria for publication at this time. "
                "Please don’t be discouraged—your ideas have potential and we welcome future submissions."
            )
        else:
            statement = (
                "Thank you for submitting your event to the MARTI Agent. "
                "After reviewing your submission, we’ve decided not to proceed with publishing it at this time. "
                "This decision isn’t a reflection of your potential, and we encourage you to submit again with refinements."
            )

        # HTML email content
        html = f"""
            <div style="font-family: Arial, sans-serif; color: #333; max-width: 600px; margin: auto; padding: 20px; border: 1px solid #ddd; border-radius: 8px;">
                <h2 style="color: #c0392b;">Hi {name}, your event submission update</h2>
                <p>{statement}</p>

                <p>If you would like to receive feedback or support on improving your submission, we're here to help.</p>

                <p>Thank you for taking the time to engage with MARTI. We truly value your ideas and creativity.</p>

                <p>Warm regards,<br><strong>The MARTI AI Team</strong></p>

                <div style="text-align: center; margin-top: 30px;">
                    <img src="https://marti-file-upload-bucket.s3.us-east-2.amazonaws.com/logo/marti-logo-nobg.png" alt="MARTI AI Logo" style="max-width: 120px; height: auto; opacity: 0.8;" />
                </div>
            </div>
        """

        # Create and send email
        message = MessageSchema(
            subject="Update on your event submission",
            recipients=[email],
            body=html,
            subtype="html"
        )

        fm = FastMail(conf)
        await fm.send_message(message)
        print(f"Rejection email sent to {email}")

    except Exception as e:
        print(f"Error sending rejection email: {e}")
        raise

async def send_event_live_notification(email: EmailStr, name: str, marti_page: bool, marti_agent:bool):
    try:
        # Create verification link
        event_status_url = f"{settings.FRONTEND_HOST}user-submission"
        # Email template
        if marti_page == marti_agent:
            raise HTTPException(status_code=status.HTTP_203_NON_AUTHORITATIVE_INFORMATION,
                                detail="Unautherize operation.")
        statement = ''
        if marti_page:
            statement = 'Congratulations! Your event is now live on the MARTI website. You can visit our site to see it in action.'
        else:
            statement = 'Congratulations! Your event is now live on the MARTI system/agent. Feel free to check it out on the agent interface.'

        html = f"""
            <div style="font-family: Arial, sans-serif; color: #333; max-width: 600px; margin: auto; padding: 20px; border: 1px solid #ddd; border-radius: 8px;">
                <h2 style="color: #2c3e50;">Hi {name}, your event is now live!</h2>
                <p>{statement}</p>

                <p>We're excited to have your event featured and available. If you have any questions or need assistance, feel free to reach out to us.</p>

                <p>Best regards, <br><strong>The MARTI AI Team</strong></p>

                <div style="text-align: center; margin-top: 30px;">
                    <img src="https://marti-file-upload-bucket.s3.us-east-2.amazonaws.com/logo/marti-logo-nobg.png" alt="MARTI AI Logo" style="max-width: 120px; height: auto; opacity: 0.8;" />
                </div>
            </div>
        """

        # Create message
        message = MessageSchema(
            subject="Congratulations! your event is live.",
            recipients=[email],
            body=html,
            subtype="html"
        )
        
        # Send email
        fm = FastMail(conf)
        await fm.send_message(message)
        print(f"Verification email sent to {email}")
        
    except Exception as e:
        print(f"Error sending email: {e}")
        raise 

async def send_confirmation_email_on_event_submission(email: EmailStr, name: str, event_id: str):
    """Send verification email to user"""
    try:
        # Create verification link
        event_status_url = f"{settings.FRONTEND_HOST}user-submission"#requests
        # Email template
        html = f"""
            <div style="font-family: Arial, sans-serif; color: #333; max-width: 600px; margin: auto; padding: 20px; border: 1px solid #ddd; border-radius: 8px;">
                <h2 style="color: #2c3e50;">Hello {name}! Welcome to MARTI AI Events</h2>
                <p>We are pleased to inform you that your event submission has been completed successfully. You can now track your event status at the URL below.</p>
                
                <div style="text-align: center; margin: 20px 0;">
                    <a href="{event_status_url}" 
                    style="background-color: #007bff; color: #fff; text-decoration: none; padding: 12px 20px; border-radius: 5px; font-size: 16px; display: inline-block;">
                        View Your Event
                    </a>
                </div>

                <p>You can view and track your event information.</p>
                <p><strong>Note:</strong> Thank you for being part of our event.</p>
                <p>Your registration reference ID is: {event_id}</p>

                <p>You can use this ID to view or update your registration.</p>
                <p>Welcome aboard, <br><strong>The MARTI AI Team</strong></p>

                <div style="text-align: center; margin-top: 30px;">
                    <img src="https://marti-file-upload-bucket.s3.us-east-2.amazonaws.com/logo/marti-logo-nobg.png" alt="MARTI AI Logo" style="max-width: 120px; height: auto; opacity: 0.8;" />
                </div>
            </div>

        """
        
        # Create message
        message = MessageSchema(
            subject="New event submitted",
            recipients=[email],
            body=html,
            subtype="html"
        )
        
        # Send email
        fm = FastMail(conf)
        await fm.send_message(message)
        print(f"Verification email sent to {email}")
        
    except Exception as e:
        print(f"Error sending email: {e}")
        raise 

    
async def send_verification_email(email: EmailStr, name: str, token: str):
    """Send verification email to user"""
    try:
        # Create verification link
        verification_url = f"{settings.FRONTEND_HOST}users/{email}/verify/{token}"
        # Email template
        html = f"""
            <div style="font-family: Arial, sans-serif; color: #333; max-width: 600px; margin: auto; padding: 20px; border: 1px solid #ddd; border-radius: 8px;">
                <h2 style="color: #2c3e50;">Welcome to MARTI AI, {name}!</h2>
                <p>We are thrilled to have you on board. To get started, please confirm your email address by clicking the button below.</p>
                
                <div style="text-align: center; margin: 20px 0;">
                    <a href="{verification_url}" 
                    style="background-color: #007bff; color: #fff; text-decoration: none; padding: 12px 20px; border-radius: 5px; font-size: 16px; display: inline-block;">
                        Verify Your Email
                    </a>
                </div>

                <p>Verifying your email helps us secure your account and provide you with the best experience.</p>
                <p><strong>Note:</strong> This verification link is valid for 24 hours. If you did not sign up for an account, please ignore this email.</p>
                
                <p>Welcome aboard, <br><strong>The Ask MARTI Team</strong></p>

                <div style="text-align: center; margin-top: 30px;">
                    <img src="https://marti-file-upload-bucket.s3.us-east-2.amazonaws.com/logo/marti-logo-nobg.png" alt="MARTI AI Logo" 
                    style="max-width: 120px; height: 60px; opacity: 0.8;" />
                </div>
            </div>
        """
        
        # Create message
        message = MessageSchema(
            subject="Verify Your Email",
            recipients=[email],
            body=html,
            subtype="html"
        )
        
        # Send email
        fm = FastMail(conf)
        await fm.send_message(message)
        print(f"Verification email sent to {email}")
        
    except Exception as e:
        print(f"Error sending email: {e}")
        raise 


async def send_forgot_password_email(email: EmailStr, name: str, token: str):
    """Send verification email to user"""
    try:
        # Create verification link
        reset_password_url = f"{settings.FRONTEND_HOST}forget-password/{email}/{token}"
        # Email template
        html = f"""
                    <div style="font-family: Arial, sans-serif; color: #333; max-width: 600px; margin: auto; padding: 20px; border: 1px solid #ddd; border-radius: 8px;">
                        <h2 style="color: #2c3e50;">Reset Your Password, {name}</h2>
                        <p>We received a request to reset your password. Click the button below to set a new password.</p>
                        
                        <div style="text-align: center; margin: 20px 0;">
                            <a href="{reset_password_url}" 
                            style="background-color: #007bff; color: #fff; text-decoration: none; padding: 12px 20px; border-radius: 5px; font-size: 16px; display: inline-block;">
                                Reset Password
                            </a>
                        </div>

                        <p>If you did not request this password reset, please ignore this email. Your account remains secure.</p>
                        <p><strong>Note:</strong> This password reset link is valid for 24 hours.</p>
                        
                        <p>Need help? Contact our support team.</p>
                        <p>Best regards, <br><strong>The Ask MARTI Team</strong></p>

                        <div style="text-align: center; margin-top: 30px;">
                            <img src="https://marti-file-upload-bucket.s3.us-east-2.amazonaws.com/logo/marti-logo-nobg.png" alt="MARTI AI Logo" style="max-width: 120px; height: auto; opacity: 0.8;" />
                        </div>
                    </div>
                """
    
        # Create message
        message = MessageSchema(
            subject="Reset Your Password",
            recipients=[email],
            body=html,
            subtype="html"
        )
        
        # Send email
        fm = FastMail(conf)
        await fm.send_message(message)
        print(f"Password reset link sent to {email}")
        
    except Exception as e:
        print(f"Error sending email: {e}")
        raise 
from datetime import datetime, timezone
from datetime import datetime, timezone
from dateutil.relativedelta import relativedelta

async def verify_email(
    token,
    useremail,
    db: AsyncSession = Depends(get_async_db)
):
    """Verify user email with token"""
    try:
        # Get user
        print(f'username == {useremail}')
        user = await get_user_by_email(db, useremail)
        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found"
            )
        # Check if already verified
        if user.is_verified:
            return {
                "message": "Email already verified",
                "status": "info"
            }
        # created_token = user.get_context_string(settings.VERIFICATION_TOKEN_SECRET)
        created_token = SecurityManager.create_verification_token(user)
        # Decode and validate token
        print(f'generated == {created_token} already been == {token}')
        print(f'update at time == {user.updated_at}')
        # try:
        token_valid = SecurityManager.verify_token(created_token, token)
        # except Exception as verify_exec:
        #     token_valid = False

        if not token_valid:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="This link either expired or no more valid")
        # Update user verification status
        from app.services.user import verify_user_email

        user = await verify_user_email(db, useremail)
        
        stripeId = await payment.create_customer(user)
        if not stripeId:
            raise HTTPException(status_code=400, detail="Couldn't create stripe id for customer")
        user.stripeId = stripeId
        user.billing_cycle_start = datetime.now(timezone.utc)
        user.billing_cycle_end = datetime.now(timezone.utc) + relativedelta(months=1)
        db.add(user)  # Ensure the user object is added to the session
        await db.commit()
        await db.refresh(user)
        return {
            "message": "Email verified successfully",
            "status": "success"
        }
    
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired verification token"
        )

async def reset_password(token: str, useremail: str, new_password: str, db: AsyncSession):
    """
    Reset the password for a user after validating the provided token.
    """
    try:
        # Retrieve the user by email
        user = await get_user_by_email(db, useremail)
        if not user:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
        
        # Generate the token based on the current user data (adjust as needed)
        created_token = SecurityManager.create_verification_token(user)
        
        try:
            token_valid = SecurityManager.verify_token(created_token, token)
        except Exception:
            token_valid = False

        if not token_valid:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid or expired token")
        
        # Hash the new password and update the user's password field
        hashed_password = SecurityManager().get_password_hash(new_password)
        user.hashed_password = hashed_password
        
        db.add(user)
        await db.commit()
        await db.refresh(user)
        
        return {"message": "Password reset successfully", "status": "success"}
    
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Password reset failed: {str(e)}")


async def change_user_password(user: User, current_password: str, new_password: str, db: AsyncSession):
    """
    Change the password for a user after verifying the current password.
    """
    # Verify the current password
    if not SecurityManager().verify_password(current_password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Current password is incorrect"
        )
    
    # Hash the new password
    hashed_password = SecurityManager().get_password_hash(new_password)
    
    # Update user password
    user.hashed_password = hashed_password
    db.add(user)
    await db.commit()
    await db.refresh(user)
    
    return {"message": "Password has changed successfully", "status": "success"}


async def send_landing_emails(email: EmailStr, name: str, email_message: str, person_email: str, cc_email: str = None):
    try:
        # Create verification link
        # event_status_url = f"{settings.FRONTEND_HOST}event/confirm/"
        # Email template
        html = f"""
            <div style="font-family: Arial, sans-serif; color: #333; max-width: 600px; margin: auto; padding: 24px; border: 1px solid #ddd; border-radius: 8px; background-color: #f9f9f9;">
                
                <h2 style="color: #2c3e50; margin-bottom: 16px;">📩 New Inquiry from <span style="color: #000;">{name}</span></h2>
                
                <p style="margin-bottom: 12px;">You have received a new message:</p>

                <blockquote style="margin: 0 0 16px 0; padding: 12px 16px; background-color: #fff; border-left: 4px solid #2c3e50; font-style: italic;">
                    {email_message}
                </blockquote>

                <p style="margin-bottom: 24px;">Sender's Email: <strong>{person_email}</strong></p>

                <p style="margin-bottom: 0;">Warm regards,</p>
                <p style="font-weight: bold; color: #2c3e50; margin-top: 4px;">The Ask MARTI Support Team</p>
                <div style="text-align: center; margin-top: 30px;">
                    <img src="https://marti-file-upload-bucket.s3.us-east-2.amazonaws.com/logo/marti-logo-nobg.png" alt="MARTI AI Logo" style="max-width: 120px; height: auto; opacity: 0.8;" />
                </div>
            </div>

        """
        
        # Create message
        message = MessageSchema(
            subject="New MARTI Inquiry",
            recipients=[email],
            cc=[cc_email],
            body=html,
            subtype="html"
        )
        
        # Send email
        conf = ConnectionConfig(
        MAIL_USERNAME = settings.MAIL_USERNAME,
        MAIL_PASSWORD = settings.MAIL_PASSWORD,
        MAIL_FROM = "support@askmarti.com",
        MAIL_PORT = settings.MAIL_PORT,
        MAIL_SERVER = settings.MAIL_SERVER,
        MAIL_STARTTLS = settings.MAIL_STARTTLS,
        MAIL_SSL_TLS = settings.MAIL_SSL_TLS,
        USE_CREDENTIALS = settings.MAIL_USE_CREDENTIALS,
        MAIL_DEBUG=settings.MAIL_DEBUG,
        MAIL_FROM_NAME=settings.MAIL_FROM_NAME
        )
        fm = FastMail(conf)
        await fm.send_message(message)
        print(f"Verification email sent to {email}")
        
    except Exception as e:
        print(f"Error sending email: {e}")
        raise 

# async def send_password_reset_invitation(
#     email: EmailStr, 
#     name: str, 
#     token: str,
#     organization_name: str
# ):
#     """
#     Send password reset invitation email to newly created users
    
#     Args:
#         email: User's email address
#         name: User's name
#         token: Password reset token
#         organization_name: Name of the organization
#     """
#     try:
#         # Create password reset link
#         reset_url = f"http://{settings.FRONTEND_HOST}/reset-password?token={token}"
        
#         # Email template
#         html = f"""
#             <h3>Welcome to {organization_name}, {name}!</h3>
#             <p>Your account has been created. To access the platform, please set your password by clicking the link below:</p>
#             <p>
#                 <a href="{reset_url}">
#                     Set Your Password
#                 </a>
#             </p>
#             <p>This link will expire in 24 hours.</p>
#             <p>If you have any issues, please reach out to support.</p>
#             <p>Thank you,<br>The {organization_name} Team</p>
#         """
        
#         # Create message
#         message = MessageSchema(
#             subject=f"Welcome to {organization_name} - Set Your Password",
#             recipients=[email],
#             body=html,
#             subtype="html"
#         )
        
#         # Send email
#         fm = FastMail(conf)
#         await fm.send_message(message)
#         print(f"Password reset invitation email sent to {email}")
        
#     except Exception as e:
#         print(f"Error sending invitation email: {e}")
#         raise


# async def send_bulk_password_reset_invitations(
#     users: List[dict], 
#     organization_name: str,
#     background_tasks: BackgroundTasks
# ):
#     """
#     Send password reset invitations to multiple users
    
#     Args:
#         users: List of user dictionaries with email, name, and id
#         organization_name: Name of the organization
#         background_tasks: FastAPI background tasks
#     """
#     for user in users:
#         token = SecurityManager.create_verification_token(user["user"])
        
#         # Add email sending task to background
#         background_tasks.add_task(
#             send_password_reset_invitation,
#             email=user["email"],
#             name=user["name"],
#             token=token,
#             organization_name=organization_name
#         )
    
#     return len(users)

