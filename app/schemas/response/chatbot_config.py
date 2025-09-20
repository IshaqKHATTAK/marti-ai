from pydantic import BaseModel
from datetime import datetime
from typing import Any, Optional, List
from pydantic import BaseModel, HttpUrl
from enum import Enum

class WebscrapUrl(BaseModel):
    website_link: str
    website_url_id: int
    sweep_domain: bool
    sweep_url: bool
    status: str

class DocumentInfo(BaseModel):
    document_name: str
    document_id: int
    document_status: Optional[str] = None
    document_size: Optional[float] = 0.0 

class QATemplateData(BaseModel):
    question: str
    answer: str
    status: Optional[str] = None
    id:int
    # qa_db_id:Optional[str] = None
    # qa_thrd_id: Optional[int] = None

class QATemplateDataPaginated(BaseModel):
    total_qas: int
    qa_data: Optional[List[QATemplateData]] = []

class UpdateQATemplateData(BaseModel):
    question: str
    answer: str
    id: Optional[int] = None
    qa_db_id:Optional[str] = None
    qa_thrd_id: Optional[int] = None

class ChatbotMemory(BaseModel):
    text: str
    memory_id: Optional[int] = None

class ChatbotSuggestion(BaseModel):
    suggestion_text: str

class ChatbotSuggestionUpdate(BaseModel):
    suggestion_text: str
    suggestion_id: Optional[int] = None

class ChatbotMemoryResponse(BaseModel):
    text: str
    creator: str
    memory_id: int
    status: str

class GetChatbotMemoryResponse(BaseModel):
    chatbot_data: list[ChatbotMemoryResponse]
    bot_memory_status: bool

class ChatbotSuggestionResponse(BaseModel):
    suggestion_id: int
    suggestion_text: str

class GetGuardrails(BaseModel):
    guardrail: str
    id: int

class ChatbotFileUpdateResponse(BaseModel):
    website_url: Optional[List[WebscrapUrl]] = []
    bot_documents: Optional[List[DocumentInfo]] = []
    consumed_webscrap_size: Optional[Any] = 0
    consumed_file_size: Optional[Any] = 0

class UrlValidationResponse(BaseModel):
    valid: bool
    message: Optional[str] = None

class ChatbotConfigResponse(BaseModel):
    id: int
    llm_model_name: str
    llm_temperature: float
    llm_prompt: str
    llm_role: str
    status: Optional[str] = None
    llm_streaming: bool = True
    chatbot_type: str
    chatbot_name: str
    website_url: Optional[List[WebscrapUrl]] = []
    bot_documents: Optional[List[DocumentInfo]] = []
    qa_templates: Optional[List[QATemplateData]] = []
    guardrails: Optional[List[GetGuardrails]] = []
    avatar: Optional[str] = None
    scaffolding_level: Optional[str] = None
    consumed_webscrap_size: Optional[Any] = 0
    consumed_file_size: Optional[Any] = 0
    class Config:
        from_attributes = True

class WebsiteUrlPagination(BaseModel):
    website_url: Optional[List[WebscrapUrl]] = []
    total_webiste: Optional[int] = 0
    consumed_webscrap_size: Optional[Any] = 0

class DocumentPagination(BaseModel):
    bot_documents: Optional[List[DocumentInfo]] = []
    total_documents: Optional[int] = 0
    consumed_file_size: Optional[Any] = 0


class KnowledgeBaseDoc(BaseModel):
    websites: WebsiteUrlPagination
    documents: DocumentPagination

    

class WebsiteUrl(BaseModel):
    website_link: str
    sweep_domain: bool
    sweep_url: bool
    website_url_id: Optional[int] = None
    
class WebsiteRemoved(BaseModel):
    website_link: str
    website_url_id: int

class DocumentRemoved(BaseModel):
    document_name: str
    document_id: int

class ChatbotPrompt(BaseModel):
    trianing_text: str

class ScaffoldingLevel(str, Enum):
    low = "low"
    medium = "medium"
    high = "high"

class DetailsRequest(BaseModel):
    chatbot_name: str
    chatbot_role: str
    avatar: Optional[str] = None
    scaffolding_level: Optional[ScaffoldingLevel] = None
from enum import Enum

class LlmModelEnum(str, Enum):
    gpt_41 = "gpt-4.1"
    gpt_41_mini = "gpt-4.1-mini"
    gpt_5 = "gpt-5"

class ImageGenerationLlmModelEnum(str, Enum):
    dall_e_2 = "dall-e-2"
    dall_e_3 = "dall-e-3"

class BotLlmRequest(BaseModel):
    model_name: LlmModelEnum

class ImageGenerationBotLlmRequest(BaseModel):
    model_name: ImageGenerationLlmModelEnum

class AppModels(BaseModel):
    chat_model_name: BotLlmRequest
    image_model_name: ImageGenerationBotLlmRequest

class ChatBotCreation(BaseModel):
    id: Optional[int] = None
    chatbot_type: str
    chatbot_name: str
    avatar: str
    specilized_type: Optional[str] = None
    # llm_model_name: str

class AllPreExistingChatbotResponse(BaseModel):
    org_avatar: Optional[str] = None
    chatbots: List[ChatBotCreation]

class ListBots(ChatBotCreation):
    used_messages: Optional[int] = None
    allowed_messages: Optional[int]= None
    
class ListBotsForPlan(BaseModel):
    chatbot_type: str
    chatbot_name: str
    specilized_type: Optional[str] = None
    used_messages: Optional[int] = None
    allowed_messages: Optional[int]= None
    
class AllChatbotResponse(BaseModel):
    org_avatar: Optional[str] = None
    chatbots: List[ListBots]


class Guardrails(BaseModel):
    guardrails: str

class SweepOption(str, Enum):
    domain = "domain"
    website_page = "website_page"
    

class UrlValidationRequest(BaseModel):
    chatbot_id: int
    url: HttpUrl
    sweep_option: SweepOption

class logs(BaseModel):
    name: str
    description: Optional[str] = None
    logs_type: Optional[str] = None
    date: Optional[datetime] = None

class SecurityAndLogsResponse(BaseModel):
    organization_logs: List[logs] = []
    total_logs: int = 0

