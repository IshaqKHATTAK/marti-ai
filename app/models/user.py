from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey, Index, Enum as SQLAEnum
from sqlalchemy.orm import relationship
from app.common.database_config import Base
from datetime import datetime, timezone
from app.models.organization import Organization
import enum
from sqlalchemy import Enum
from sqlalchemy.dialects.postgresql import ARRAY

class UserRole(str, enum.Enum):
    ADMIN = "admin"
    USER = "user"
    SUPER_ADMIN = "super_admin"

class Plan(enum.Enum):
    free = "Free"
    starter = "Starter"
    enterprise = "Enterprise"

    @classmethod
    def from_string(cls, string_value: str):
        for member in cls:
            if member.value.lower() == string_value.lower():
                return member
        raise ValueError(f"'{string_value}' is not a valid {cls.__name__}")
    

def get_utc_now():
    return datetime.now(timezone.utc)

class User(Base):
    __tablename__ = "users"

    #Primary Key
    id = Column(Integer, primary_key=True, index=True)

    #Basic Details
    name = Column(String)
    email = Column(String, unique=True, index=True)
    avatar_url = Column(String(1000), nullable=True)
    hashed_password = Column(String, nullable=True)

    #Role
    role = Column(
        SQLAEnum(UserRole, native_enum=False),
        nullable=False,
        default=UserRole.USER.value
    )
    
    #Status
    is_active = Column(Boolean, default=True)
    is_paid = Column(Boolean, default=False)
    is_onboarded = Column(Boolean, default=False)
    is_verified = Column(Boolean, default=False)
    is_walkthrough_completed = Column(Boolean, default=False)
    verified_at = Column(DateTime(timezone=True), nullable=True)

    group_ids = Column(ARRAY(Integer), nullable=True)
    # chatbot_ids = Column(ARRAY(Integer), nullable=True)
    total_messages = Column(Integer,default=0)

    #Organization Relationship
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=True)
    organization = relationship("Organization", back_populates="users")

    #Timestamps
    created_at = Column(DateTime(timezone=True), default=get_utc_now)
    updated_at = Column(DateTime(timezone=True), default=get_utc_now, onupdate=get_utc_now)

    # Add this relationship
    sessions = relationship("UserSession", back_populates="user", cascade="all, delete-orphan")

    current_plan = Column(
        SQLAEnum(Plan, native_enum=False),
        nullable=True,
        default=Plan.free
    )
    add_on_features = Column(ARRAY(String), nullable=True)
    country = Column(String, nullable=True)
    stripeId = Column(String(200), nullable=True)
    is_user_consent_given = Column(Boolean, default=False, nullable=True)
    def get_context_string(self, context: str):
        return f"{context}{self.hashed_password[-6:]}{self.updated_at.strftime('%m%d%Y%H%M%S')}".strip()
    
    def __repr__(self):
        return f"<User {self.name}>"
    

class UserSession(Base):
    __tablename__ = "user_sessions"

    # Primary Key
    id = Column(Integer, primary_key=True, index=True)
    
    # Session Data
    refresh_token = Column(String(255), unique=True, nullable=False, index=True)
    
    # User Relationship
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    user = relationship("User", back_populates="sessions")
    
    # Session Info
    user_agent = Column(String(255))
    ip_address = Column(String(45))
    
    # Status
    is_valid = Column(Boolean, default=True)
    
    # Timestamps
    created_at = Column(DateTime(timezone=True), default=get_utc_now)
    expires_at = Column(DateTime(timezone=True))
    last_activity = Column(DateTime(timezone=True), default=get_utc_now)
    invalidated_at = Column(DateTime(timezone=True), nullable=True)

    def __repr__(self):
        return f"<UserSession {self.refresh_token[:8]}... for user {self.user_id}>"
