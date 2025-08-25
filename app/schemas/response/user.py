from pydantic import BaseModel, Field
from datetime import datetime
from typing import Optional, List
from app.models.user import UserRole
from typing import List, Dict, Any


class ChatbotInfo(BaseModel):
    chatbot_id: int
    chatbot_name: str


class ExistingUser(BaseModel):
    name: str
    email: str

class BulkUploadResponse(BaseModel):
    created_users: List[dict]
    existing_users: List[dict]
    total_created: int
    total_existing: int

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
    can_view_insight: Optional[bool] = False

class UserResponse(BaseModel):
    id: int
    name: str
    email: str
    role: str
    total_messages: int
    organization_id: Optional[int] = None
    organization_name: Optional[str] = None
    form_submission: Optional[bool]= False
    # chatbots: Optional[List[ChatbotInfo]] = []
    groups: List[AttributeItem] = []
    group_ids: List[int] = []
    is_active: bool = True
    is_paid: bool 
    current_plan: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    is_verified: bool = False
    verified_at: Optional[datetime] = None
    avatar_url: Optional[str] = None
    trail_expirey: Optional[datetime] = None
    is_walkthrough_completed: Optional[bool] = True
    image_generation: bool = False
    is_user_consent_given: Optional[bool] = False
    total_users_paid: Optional[int] = None
    class Config:
        from_attributes = True

class UserResponseCreate(BaseModel):
    id: int
    name: str
    email: str
    role: str
    total_messages: int
    organization_id: Optional[int] = None
    organization_name: Optional[str] = None
    # chatbots: Optional[List[ChatbotInfo]] = []
    groups: List[AttributeItem] = []
    group_ids: Optional[List[dict]] = []
    is_active: bool = True
    is_paid: bool 
    current_plan: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    is_verified: bool = False
    verified_at: Optional[datetime] = None
    avatar_url: Optional[str] = None
    trail_expirey: Optional[datetime] = None
    image_generation: bool = False
    class Config:
        from_attributes = True


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "Bearer"

class TokenData(BaseModel):
    user_id: int
    email: str
    role: UserRole


class PasswordResetResponse(BaseModel):
    message: str
    status: str
    
# class CreatedUser(BaseModel):
#     id: int
#     name: str
#     email: str
#     user: UserResponse

class GroupAttribute(BaseModel):
    chatbot_id: int
    chatbot_name: str
    can_edit_webscrap: bool
    can_edit_fileupload: bool
    can_edit_qa: bool
    can_edit_traning_text: bool
    can_edit_memory: bool 
    can_edit_guardrails: bool
    can_view_feedback: bool
    can_view_chat_logs: bool
    can_view_insight: Optional[bool] = False
    
    
class GroupOut(BaseModel):
    id: int
    name: str
    form_submission: bool
    attributes: List[GroupAttribute]

    class Config:
        orm_mode = True


class UpdateGroupOut(BaseModel):
    id: int
    name: str
    form_submission: bool
    attributes: List[GroupAttribute]
    organization_id: int
    class Config:
        orm_mode = True

class ALLGroupOut(BaseModel):
    id: int
    name: str
    form_submission: bool
    attributes: List[GroupAttribute] = []
    organization_id: int
    class Config:
        orm_mode = True

class PaginatedResponseGroup(BaseModel):
    list_of_all_groups: Optional[List[ALLGroupOut]] = []
    total_groups: int = 0