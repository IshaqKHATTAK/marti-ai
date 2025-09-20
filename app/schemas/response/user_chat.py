from pydantic import BaseModel
from typing import List
from datetime import datetime
from typing import Optional


class S3UploadItem(BaseModel):
    file_name: str
    file_type: str
    file_size: Optional[int] = 0
    

class S3PublicUpload(BaseModel):
    upload_files: List[S3UploadItem]
    bot_id: str
    fingerprint: str
    thread_id: str

class S3UploadRequest(BaseModel):
    upload_type: str
    upload_files: List[S3UploadItem]
    bot_id: Optional[int] = 0
    thread_id: Optional[int] = 0
    org_id: Optional[int] = 0
    

class S3ResponseItem(BaseModel):
    upload_url: str
    s3_key: str
    filename: str
    thread_id: int
    
    
class S3Response(BaseModel):
    files: List[S3ResponseItem]
    is_user_consent_given: Optional[bool] = None


class PublicS3ResponseItem(BaseModel):
    upload_url: str
    s3_key: str
    filename: str
    thread_id: str

class PublicS3Response(BaseModel):
    files: List[PublicS3ResponseItem]
    thread_id: Optional[str] = None



class ChatbotResponse(BaseModel):
    id: int
    answer: Optional[str] = None
    url_to_image: Optional[str] = None
    image_generation_flag: bool
    created_at: datetime
    updated_at: datetime
    message_id: str
    is_simplify: Optional[bool] = False
    new_chat: bool
    
  
class ExternalChatbotResponse(BaseModel):
    id: str
    answer: Optional[str] = None
    # image_generation_flag: bool
   

class Chats(BaseModel):
    role: str
    message: Optional[str] = None
    images_urls: Optional[List[str]] = None
    message_id: str
    is_revise: bool = False

class GetMessagesResponse(BaseModel):
    id: int
    image_generation: bool = False
    chat_messages: List[Chats]
    total_messages: int

class GetMessagesResponseInternal(BaseModel):
    id: int
    image_generation: bool = False
    chat_messages: List[Chats]
    offset: int

class ThreadResponse(BaseModel):
    thread_id: int
    title: str
    created_timestamp: datetime
    updated_timestamp: datetime | None

class AllSessionsResponse(BaseModel):
    sessions: List[ThreadResponse] = []
    total_session: int

class AllThreadResponseItem(BaseModel):
    thread_id: int
    title: str
    user_name: str
    user_id: int
    chatbot_name: str
    chatbot_type: str
    chatbot_id: int
    created_timestamp: datetime
    updated_timestamp: datetime | None

class AllThreadResponse(BaseModel):
    thread_sessions: List[AllThreadResponseItem] = []
    total_thread_session: int


class ShareChatbotResponse(BaseModel):
    chatbot_id: int
    share_url: str
    iframe: str
    script: str


class MessageFeedbackResponse(BaseModel):
    bot_id: int
    user_name: Optional[str] = None
    message_id: Optional[str] = None
    feedback: str
    chatbot_type: str
    status: str
    feedback_id: int
    chatbot_name: str
    message_text: str

class AllMessageFeedbackResponse(BaseModel):
    feedbacks: List[MessageFeedbackResponse] = []
    total_feedback: Optional[int] = 0


class MessageReviewResponse(BaseModel):
    status: str

class MessageViewResponse(BaseModel):
    feedback: str




class ChatbotMessageCount(BaseModel):
    chatbot_id: int
    chatbot_name: str
    message_count: int

class DailyMessageCount(BaseModel):
    start_date: str
    end_date: str
    total_messages: int

class AnalyticsResponse(BaseModel):
    total_messages: int
    chatbot_messages: List[ChatbotMessageCount] 
    daily_totals: List[DailyMessageCount] 