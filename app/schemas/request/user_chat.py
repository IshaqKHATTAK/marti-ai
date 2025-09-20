from typing import Optional
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime


class UserChat(BaseModel):
    id: int
    bot_id: int
    question: str
    generate_image: bool
    is_simplify: Optional[bool] = False
    images_urls: Optional[List[str]] = None

class ExternalChat(UserChat):
    generate_image: bool
    

class ChatId(BaseModel):
    id: int

class AdminChatId(BaseModel):
    chatbot_id: int
    thread_id: int
    user_id: int
    
class UserSecureChat(BaseModel):
    id: str
    bot_id: str
    fingerprint: str
    question: str
    generate_image: bool
    images_urls: Optional[List[str]] = None
    simplify: bool = False

class MessageFeedbackRequest(BaseModel):
    bot_id: int
    message_id: str
    feedback: str

class PublicMessageFeedbackRequest(BaseModel):
    bot_id: str
    message_text: str
    feedback: str

class DeleteFeedback(BaseModel):
    feedback_id: int

class S3DeleteRequest(BaseModel):
    s3_key: str

class AdminFilterRequest(BaseModel):
    chatbot_ids:Optional[List[int]] = []
    user_ids:Optional[List[int]] = []
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None



class Analytics(BaseModel):
    chatbot_ids:Optional[List[int]] = []

