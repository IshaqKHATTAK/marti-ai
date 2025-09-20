from passlib.context import CryptContext
from datetime import datetime, timedelta, UTC, timezone
import secrets
import jwt
from sqlalchemy.orm.attributes import flag_modified
from fastapi import Response, Request, HTTPException
from app.models.user import User, Plan, UserRole
from app.models.user import UserSession
from sqlalchemy.orm import Session
from app.common import env_config
from sqlalchemy.ext.asyncio import AsyncSession
import base64
from sqlalchemy import select
envs = env_config.get_envs_setting()
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

class SecurityManager:
    def __init__(self):
        self.cookie_name = "access_token"
        self.refresh_cookie_name = "refresh_token"
        self.access_token_expire = timedelta(minutes=envs.ACCESS_TOKEN_EXPIRE_MINUTES)
        self.refresh_token_expire = timedelta(days=envs.REFRESH_TOKEN_EXPIRE_DAYS)
    
    def verify_password(self, plain_password: str, hashed_password: str) -> bool:
        return pwd_context.verify(plain_password, hashed_password)

    def get_password_hash(self, password: str) -> str:
        return pwd_context.hash(password)
    
    def create_access_token(self, user_id: int) -> str:
        """Create JWT access token"""
        data = {
            "sub": str(user_id),
            "type": "access",
            "exp": datetime.now(UTC) + self.access_token_expire
        }
        return jwt.encode(data, envs.JWT_SECRET, algorithm=envs.ALGORITHM)

    def create_refresh_token(self, user_id: int) -> str:
        """Create secure refresh token"""
        data = {
            "sub": str(user_id),
            "type": "refresh",
            "exp": datetime.now(UTC) + self.refresh_token_expire
        }
        return jwt.encode(data, envs.JWT_SECRET, algorithm=envs.ALGORITHM)
    
    def set_session_cookies(
        self, 
        response: Response, 
        refresh_token: str
    ) -> None:
        """Set only refresh token cookie"""
        response.set_cookie(
            key=self.refresh_cookie_name,
            value=refresh_token,
            domain=envs.COOKIE_DOMAIN,
            httponly=True,
            secure=True,
            samesite="none",
            max_age=int(self.refresh_token_expire.total_seconds()),
            path="/"
        )
        print(f"Cookie Set: {self.refresh_cookie_name} = {refresh_token}")
    
    async def clear_session_cookies(
        self, 
        response: Response, 
        refresh_token: str,
        db: AsyncSession
    ) -> None:
        """Clear cookies and delete session from database"""
        try:
            # Find and delete session
            session_query = select(UserSession).filter(
                UserSession.refresh_token == refresh_token
            )
            session = await db.execute(session_query).scalar_one_or_none()
            
            if session:
                await db.delete(session)
                await db.commit()
            
            # Clear cookies
            response.delete_cookie(key=self.cookie_name, path="/")
            response.delete_cookie(key=self.refresh_cookie_name, path="/users/refresh")
            
        except Exception as e:
            raise HTTPException(
                status_code=500,
                detail=f"Error clearing session: {str(e)}"
            )

    async def create_session(
        self, 
        user: User, 
        response: Response, 
        request: Request, 
        db: AsyncSession
    ) -> dict:
        # Create tokens
        access_token = self.create_access_token(user.id)
        refresh_token = self.create_refresh_token(user.id)
        
        # Make all datetimes timezone-aware
        now = datetime.now(timezone.utc)
        expires_at = now + self.refresh_token_expire
        
        # Create session
        session = UserSession(
            refresh_token=refresh_token,
            user_id=user.id,
            user_agent=request.headers.get("user-agent"),
            ip_address=request.client.host,
            is_valid=True,
            created_at=now,
            expires_at=expires_at,
            last_activity=now
        )
        
        db.add(session)
        await db.commit()
        await db.refresh(session)
        
        # Set refresh token cookie
        self.set_session_cookies(response, refresh_token)
        
        # Return only access token
        return {
            "access_token": access_token,
            "token_type": "Bearer"
        }

    async def validate_session(
        self,
        access_token: str,
        db: AsyncSession
    ) -> User:
        try:
            # Only decode JWT token for access tokens, no database check needed
            payload = jwt.decode(
                access_token, 
                envs.JWT_SECRET, 
                algorithms=[envs.ALGORITHM]
            )
            user_id = int(payload["sub"])
            
            # Get user using async syntax
            user_query = select(User).filter(
                User.id == user_id,
                User.is_active == True
            )
            user_result = await db.execute(user_query)
            user = user_result.scalar_one_or_none()
            
            if not user:
                raise HTTPException(
                    status_code=401,
                    detail="User not found or inactive"
                )
            if user.current_plan == Plan.free and user.role != UserRole.SUPER_ADMIN:
                seven_days_ago = datetime.utcnow() - timedelta(days=envs.FREE_TRAIL_DAYS)
                if user.created_at.replace(tzinfo=None) < seven_days_ago:
                    user.is_paid = False
                    if "image_generation" in user.add_on_features:
                        user.add_on_features.remove("image_generation")
                        flag_modified(user, "add_on_features")
                    await db.commit()
            return user
                
        except jwt.ExpiredSignatureError:
            raise HTTPException(
                status_code=401,
                detail="Token has expired"
            )
        except jwt.JWTError:
            raise HTTPException(
                status_code=401,
                detail="Invalid token"
            )

    def validate_refresh_token(self, refresh_token: str) -> dict:
        """Validate refresh token and return payload"""
        try:
            # Decode and verify the refresh token
            payload = jwt.decode(
                refresh_token, 
                envs.JWT_SECRET, 
                algorithms=[envs.ALGORITHM]
            )
            
            # Check token type
            if payload.get("type") != "refresh":
                raise HTTPException(
                    status_code=401,
                    detail="Invalid token type"
                )
            
            return payload
            
        except jwt.ExpiredSignatureError:
            raise HTTPException(
                status_code=401,
                detail="Refresh token has expired"
            )
        except jwt.JWTError:
            raise HTTPException(
                status_code=401,
                detail="Invalid refresh token"
            )

    async def refresh_access_token(self, refresh_token: str, db: AsyncSession) -> dict:
        """Generate new access token using existing refresh token"""
        try:
            # First validate the refresh token
            payload = self.validate_refresh_token(refresh_token)
            user_id = int(payload["sub"])
            
            # Find valid session
            session_query = select(UserSession).filter(
                UserSession.refresh_token == refresh_token,
                UserSession.user_id == user_id,  # Additional check
                UserSession.expires_at > datetime.now(timezone.utc)
            )
            session = await db.execute(session_query)
            session = session.scalar_one_or_none()
            
            if not session:
                raise HTTPException(
                    status_code=401, 
                    detail="Invalid or expired session"
                )
            
            # Create new access token
            new_access_token = self.create_access_token(user_id)
            
            # Update last activity
            session.last_activity = datetime.now(timezone.utc)
            await db.commit()
            
            return {
                "access_token": new_access_token,
                "token_type": "Bearer",
                "expires_in": str(datetime.now(UTC) + self.access_token_expire)
            }
            
        except Exception as e:
            raise HTTPException(
                status_code=401,
                detail=f"Could not refresh token: {str(e)}"
            )

    
    @classmethod
    def create_verification_token(cls, user: User):
        """Create email verification token"""
        raw_token = user.get_context_string(context=envs.VERIFICATION_TOKEN_SECRET)
        encoded_token = base64.urlsafe_b64encode(raw_token.encode()).decode()
        return encoded_token
       
    @classmethod
    def verify_token(cls, plan_token: str,hashed_token: str):
        """Verify email verification token and return email"""
        try:
            if plan_token == hashed_token:
                return True
            else:
                return False
        except:
            raise ValueError("Invalid or expired token")
            return False
