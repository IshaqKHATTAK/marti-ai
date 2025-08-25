from typing import Optional
from pydantic import BaseModel
from fastapi import Form
from typing import Optional, List, Union
from enum import Enum

class InputData(BaseModel):
    id: int

class QATemplateData(BaseModel):
    question: str
    answer: str

# class BotDetials(BaseModel):
#     chatbot_name: str
#     llm_prompt: str
#     llm_role: str
#     llm_streaming: Optional[bool] = None,
#     llm_model_name: Optional[str] = None,
#     avatar: Optional[str] = None,
#     llm_temperature: Optional[float]

class BotType(str, Enum):
    internal = "internal"
    teacher = "teacher"
    external = "external"

class BotDetails(BaseModel):
    chatbot_name: str
    llm_prompt: Optional[str] = ''
    llm_role: str
    llm_streaming: Optional[bool] = True
    llm_model_name: Optional[str] = 'gpt-4o-mini'  
    avatar: Optional[str] = '' 
    llm_temperature: Optional[float] = 0.2
    bot_type: BotType = BotType.internal


class CreateData(BaseModel):
    llm_temperature: Optional[float] = 0.2
    organization_id: int
    llm_role: str
    qa_templates: Optional[List[QATemplateData]] = []
    llm_streaming: Optional[bool] = True
    llm_model_name: Optional[str] = "gpt-4o-mini"
    llm_prompt: str
    chatbot_name: str

class UpdateChatbotConfigRequest(BaseModel):
    organization_id: int
    chatbot_id: int
    llm_prompt: Optional[str] = None
    qa_templates: Optional[List[QATemplateData]] = []
    guardrails: Optional[List[str]] = []
    
class ChatbotDetails(BaseModel):
    chatbot_name: str
    chatbot_role: str

    @classmethod
    def as_form(cls, 
                chatbot_name: str = Form(...), 
                chatbot_role: str = Form(...)) -> "ChatbotDetails":
        return cls(chatbot_name=chatbot_name, chatbot_role=chatbot_role)
    
class QAsRemove(BaseModel):
    question: str
    answer: str
    id:int
    # qa_db_id: Optional[str] = None
    # qa_thrd_id: Optional[int] = None

class GuardrailsAdded(BaseModel):
    guard_rail: str

class GuardrailsRemoved(BaseModel):
    guardrail: str
    id: int

class GuardrailsUpdated(BaseModel):
    guardrail: str
    id: Optional[int] = None

class ChatbotCutomize(BaseModel):
    primary_color : str 
    chat_header: str 
    sender_bubble_color: str
    receiver_bubble_color: str
    senderTextColor: str
    receiverTextColor: str


class BubbleCutomize(BaseModel):
    bubble_bgColor : Optional[str] = None
    bubble_icon: Optional[str] = None
    # bubble_size: str
    # bubble_icon_color: str

class SecurityAndLogs(BaseModel):
    chatbots_ids: Optional[List[int]] = []