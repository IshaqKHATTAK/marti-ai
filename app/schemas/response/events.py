from typing import Optional
from pydantic import BaseModel, EmailStr
from typing import List

class DocumentFileResponse(BaseModel):
    id: int
    doc_name: str
    doc_status: str

class SeenResponse(BaseModel):
    is_seen: Optional[bool] = None

class CreateEventResponse(BaseModel):
    id: int
    organization_id: int
    Email: EmailStr
    Name: str
    Building: str
    Department: str
    Title: str
    document_files: Optional[List[DocumentFileResponse]] = None
    should_live_on_marti_page: bool
    should_live_on_marti_agent: bool 
    additional: str
    marti_agent_review: bool
    marti_website_review: bool
    is_rejected_marti_website: Optional[bool] = None
    is_rejected_marti_agent: Optional[bool] = None
    user_response_to_review: Optional[bool] = None
    admin_event_review: Optional[bool] = None
    is_seen: Optional[bool] = None
    total_size_in_kb: Optional[int] = 0,
    allowed_size: Optional[int] = 0


class EventFeedbackRespose(BaseModel):
    feedback_id: int
    feedback: str
    event_id: int

class ReviewResponse(BaseModel):
    agent_review: bool | None = None
    website_review: bool | None = None
    is_rejected_marti_website: bool | None = None
    is_rejected_marti_agent: bool | None = None

class GetPaginatedRespose(BaseModel):
    total_event_count: int 
    events: Optional[List[CreateEventResponse]] = []

class CaseMessageResponse(BaseModel):
    message: str
    timestamp: str
    is_user_message: bool

class CreateCaseResponse(BaseModel):
    case_id: int
    case_status: str
    case_title: str
    organization_id: int
    event_id: int
    created_email: str
    case_messages: Optional[List[CaseMessageResponse]] = []

