from pydantic import BaseModel, EmailStr
from typing import Optional, List
from enum import Enum

class UserRole(str, Enum):
    ADMIN = "admin"
    USER = "user"
    SUPER_ADMIN = "super_admin"

class AdminSignupRequest(BaseModel):
    name: str
    email: EmailStr
    password: str
    organization_name: str
    country: str
    logo: Optional[str] = None
    website_link: Optional[str] = None
    
class LoginUser(BaseModel):
    email: EmailStr
    password: str

class UserCreate(BaseModel):
    """For admin to create organization users"""
    name: str
    email: EmailStr
    password: str
    confirm_password: str
    active: bool = False
    group_ids: Optional[List[int]] = None
    # chatbot_ids: Optional[List[int]] = None 
    role: str = "USER"

class SuperAdminCreateUser(UserCreate):
    organization_id: int

class UserUpdate(BaseModel):
    name: Optional[str] = None
    role: Optional[str] = None
    is_active: Optional[bool] = None

class UserOnboardRequest(BaseModel):
    email: EmailStr
    name: str
    organization_name: Optional[str] = None

class VerifyUser(BaseModel):
    email: EmailStr
    token: str

class PasswordResetRequest(BaseModel):
    token: str
    useremail: str
    new_password: str

class ForgetData(BaseModel):
    name: str
    email: str

class PasswordChange(BaseModel):
    current_password: str
    new_password: str
    confirm_password: str

class AttributeItem(BaseModel):
    chatbot_id: int
    can_edit_webscrap: bool
    can_edit_fileupload: bool
    can_edit_qa: bool
    can_edit_traning_text: bool
    can_edit_memory: bool
    can_edit_guardrails: bool
    can_view_feedback: bool
    can_view_chat_logs: bool
    can_view_insight: bool
    

class GroupCreate(BaseModel):
    name: str
    form_submission: Optional[bool] = False
    attributes: List[AttributeItem]

class GroupIdSchema(BaseModel):
    id: int

class GroupPatch(BaseModel):
    id: int
    name: str
    attributes: List[AttributeItem]