from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime

class UserData(BaseModel):
    name: str
    email: str
    group_ids: List[int] = []

class SuperAdminOrganizations(BaseModel):
    admin_id: int
    organization_id: int
    admin_name: str
    admin_email: str
    organization_name: str
    is_active: bool
    is_paid: bool 
    current_plan: Optional[str] = None

class AllSuperAdminOrganizations(BaseModel):
    organizations: List[SuperAdminOrganizations] = []
    total_organizations: int = 0

class ChatbotInfo(BaseModel):
    chatbot_id: int
    chatbot_name: str

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
 
class OrganizationResponse(BaseModel):
    id: int
    name: str
    email: str
    total_messages : Optional[int] = None
    is_active:bool
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    role: Optional[str] = None
    # chatbots: Optional[List[ChatbotInfo]] = []
    groups: List[AttributeItem] = []
    group_ids: List[int] = []
    class Config:
        from_attributes = True
        json_encoders = {
            datetime: lambda v: v.isoformat() if v else None
        }

class OrganizationListResponse(BaseModel):
    id: int
    name: str
    email: str
    total_messages : Optional[int] = None
    is_active:bool
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    role: Optional[str] = None
    # chatbots: Optional[List[ChatbotInfo]] = []
    groups: List[AttributeItem] = []
    group_ids: Optional[List[dict]] = []
    class Config:
        from_attributes = True
        json_encoders = {
            datetime: lambda v: v.isoformat() if v else None
        }


class OrganizationUsersResponse(BaseModel):
    organization_name: str
    organization_id: int
    users_data: List[OrganizationListResponse]
    total_users: int

    class Config:
        from_attributes = True

class UpdateOrganization(BaseModel):
    website_url: Optional[str] = None
    logo : Optional[str] = None
    name: Optional[str] = None

class UpdateUserProfile(BaseModel):
    name : Optional[str] = None
    avatar_url : Optional[str] = None