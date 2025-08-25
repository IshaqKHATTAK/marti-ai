from typing import Optional
from pydantic import BaseModel

class LandingRequest(BaseModel):
    title: str
    link: str
    description: str

class FaqsCreationRequest(BaseModel):
    question: str
    answer: str
    
class FaqsUpdationRequest(BaseModel):
    question: str
    answer: str
    id: int


class LandingUpdateRequest(BaseModel):
    title: Optional[str] = None
    link: Optional[str] = None
    description: Optional[str] = None
    id: int

class DeleteRequest(BaseModel):
    id: int

class LandingEmailRequest(BaseModel):
    name: str
    email: str
    message: str