from typing import Optional
from pydantic import BaseModel, EmailStr
from typing import List


class CreateEventRequest(BaseModel):
    Email: EmailStr
    Name: str
    Building: str
    Department: str
    Title: str
    document_files: Optional[List[str]] = None
    should_live_on_marti_page: bool= False
    should_live_on_marti_agent: bool = False
    additional: str

class EventFeedback(BaseModel):
    feedback: str
    event_id: int

class UpdateEventRequest(BaseModel):
    Name: Optional[str] = None
    Building: Optional[str] = None
    Department: Optional[str] = None
    Title: Optional[str] = None
    document_files: Optional[List[str]] = None
    delete_document_files: Optional[List[int]] = None
    should_live_on_marti_page: Optional[bool] = None
    should_live_on_marti_agent: Optional[bool]  = None
    additional: Optional[str] = None

class EmailRequest(BaseModel):
    email_message: str
    email: EmailStr
