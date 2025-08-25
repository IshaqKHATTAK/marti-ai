from sqlalchemy import TIMESTAMP
from datetime import datetime
from sqlalchemy import Boolean, DateTime, Column, Integer, Numeric, String, ForeignKey, Text, func, Index, Enum as SQLAEnum
from app.common.database_config import Base
from sqlalchemy.orm import relationship
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.dialects.postgresql import ENUM
import enum
from sqlalchemy import JSON
from datetime import datetime, timezone

class UrlSweep(str, enum.Enum):
    Domain = "domain"
    website_page = "website_page"

class BotType(str, enum.Enum):
    internal = "internal"
    teacher = "teacher"
    external = "external"

class FeedbackStatus(str, enum.Enum):
    Reviewed = "Reviewed"
    Unreviewed = "Unreviewed"

class SuperAdminModel(Base):
    __tablename__ = 'super_admin'
    id = Column(Integer, primary_key=True, autoincrement=True)
    llm_model_name = Column(String(250), nullable=False, index=True, default="gpt-4o-mini")

class ChatbotConfig(Base):
    __tablename__ = 'chatbot'
    id = Column(Integer, primary_key=True, autoincrement=True)
    chatbot_name = Column(String(250), nullable=False, index=True, default=None)
    chatbot_type = Column(String(250), nullable=False, index=True, default=None)
    specialized_type = Column(
        SQLAEnum(BotType, native_enum=False),
        nullable=True
    )
    llm_model_name = Column(String(250), nullable=False, index=True, default=None)
    llm_temperature = Column(Numeric(2,2), nullable=False, default=None)
    llm_prompt = Column(Text, nullable=False, default=None)
    llm_role = Column(String(100), nullable=False, default=None)
    llm_streaming = Column(Boolean, nullable=False, default=True)
    avatar_url = Column(String(500), nullable=True)
    prompt_status = Column(String(100), nullable=True, index=True, default=None)
    memory_status = Column(Boolean, default=True)
    total_chatbot_messages_count = Column(Integer, nullable=False,  default=0)
    admin_per_days_messages_count = Column(Integer, nullable=True,  default=0)
    per_day_messages = Column(Integer, nullable=True,  default=0)
    public_per_day_messages_count = Column(Integer, nullable=True,  default=0)
    # public_last_7_days_messages_count = Column(Integer, nullable=True,  default=0)
    public_last_7_days_messages = Column(JSON, nullable=True, default={})  
    # Organization Relationship
    organization_id = Column(Integer, ForeignKey("organizations.id"))
    organization = relationship("Organization", back_populates="chatbots")

    # Chatbot Documents Relationship
    documents = relationship("ChatbotDocument", back_populates="chatbot", cascade="all, delete-orphan")

    # Add this relationship
    qa_templates = relationship("QATemplate", back_populates="chatbot", cascade="all, delete-orphan")

    # Add this relationship
    bot_threads = relationship("Threads", back_populates="chatbot", cascade="all, delete-orphan")
    guardrails = relationship(
        "ChatbotGuardrail", back_populates="chatbot", cascade="all, delete-orphan"
    )

    # Add relationship to settings
    settings = relationship("ChatbotSettings", back_populates="chatbot", cascade="all, delete-orphan")
    bubble_settings = relationship("BubbleSettings", back_populates="chatbot", cascade="all, delete-orphan")
    bot_memory = relationship("ChatbotMemory", back_populates="chatbot", cascade="all, delete-orphan")
    bot_suggestion = relationship("ChatSuggestion", back_populates="chatbot", cascade="all, delete-orphan")
    feedbacks = relationship("MessagesFeedbacks", back_populates="chatbot", cascade="all, delete-orphan")
    #guardrails = Column(ARRAY(String), nullable=True, default=[])
    
class ChatbotDocument(Base):
    __tablename__ = "chatbot_documents"
    id = Column(Integer, primary_key=True, autoincrement=True)
    chatbot_id = Column(Integer, ForeignKey("chatbot.id"), nullable=False)
    #Only for website.
    url_sweep_option = Column(
        SQLAEnum(UrlSweep, native_enum=False),
        nullable=False,
        default=UrlSweep.website_page.value
    )
    document_name = Column(String(length=2000), nullable=True, index=True, default=None)
    content_type = Column(String(250), nullable=True, index=True, default=None)
    status = Column(String(100), nullable=True, index=True, default=None)
    updated_at = Column(TIMESTAMP, nullable=True, default=None, onupdate=func.now())
    created_at = Column(TIMESTAMP, nullable=False, default=func.now())
    
    __table_args__ = (
        Index("idx_chatbot_id_document_name", "chatbot_id", "document_name"),
        Index("idx_chatbot_id_document_name_status", "chatbot_id", "document_name", "status"),
    )

    # Relationships
    chatbot = relationship("ChatbotConfig", back_populates="documents")

class Threads(Base):
    __tablename__ = "threads"
    thread_id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=False)
    chatbot_id = Column(Integer, ForeignKey("chatbot.id"), nullable=False)
    title = Column(String(250), nullable=True)
    title_manual_update = Column(Boolean, default=False)
    setup_project = Column(Boolean, default=False)
    created_timestamp = Column(TIMESTAMP, nullable=False, default=func.now())
    updated_timestamp = Column(TIMESTAMP, nullable=False, default=func.now(), onupdate=datetime.now)
    agent_generated_prompt =Column(Text, nullable=True)
    questions_counter = Column(Integer, nullable=True, default=0)
    chatbot = relationship("ChatbotConfig", back_populates="bot_threads")
    messages = relationship("Messages", back_populates="thread", cascade="all, delete-orphan")

class MessageRole(str, enum.Enum):
    ASSISTANT = "Assistant"
    USER = "User"
    TOOL = "Tool"

class Messages(Base):
    __tablename__ = "chat_messages"
    message_id = Column(Integer, primary_key=True, autoincrement=True)
    thread_id = Column(Integer, ForeignKey("threads.thread_id"), nullable=False)
    organization_admin_id = Column(Integer, nullable=False)
    # Role using SQLAlchemy Enum
    role = Column(SQLAEnum(MessageRole, native_enum=False), nullable=False, default=MessageRole.USER.value)
    message_content = Column(Text, nullable=True)
    is_image = Column(Boolean,  default=False)
    is_revised = Column(Boolean,  default=False)
    # image_description = Column(Text, nullable=True)
    images_urls = Column(ARRAY(String), nullable=True, default=[])
    created_timestamp = Column(TIMESTAMP, nullable=False, default=func.now())
    message_uuid = Column(String, unique=True, nullable=False)
    thread = relationship("Threads", back_populates="messages")

    feedbacks = relationship("MessagesFeedbacks", back_populates="message", cascade="all, delete-orphan")

class QATemplate(Base):
    __tablename__ = "qa_templates"
    
    id = Column(Integer, primary_key=True, index=True)
    chatbot_id = Column(Integer, ForeignKey("chatbot.id"))
    question = Column(String)
    answer = Column(String)
    status = Column(String(100), nullable=True, index=True, default=None)
    message_id = Column(String, unique=True, nullable=True)
    thread_id = Column(Integer, unique=False, nullable=True)
    # status = Column(String)
    # embedding_id = Column(String, unique=True)

    # Relationship back to ChatbotConfig
    chatbot = relationship("ChatbotConfig", back_populates="qa_templates")

class ChatbotGuardrail(Base):
    __tablename__ = "chatbot_guardrails"

    id = Column(Integer, primary_key=True, autoincrement=True)
    guardrail_text = Column(String, nullable=False)  # Guardrail description
    chatbot_id = Column(Integer, ForeignKey("chatbot.id"), nullable=False)
    chatbot = relationship("ChatbotConfig", back_populates="guardrails")

class ChatbotSettings(Base):
    __tablename__ = 'chatbot_settings'
    id = Column(Integer, primary_key=True, autoincrement=True)
    chatbot_id = Column(Integer, ForeignKey("chatbot.id"), nullable=False, index=True)

    # Appearance fields
    primary_color = Column(String(250), nullable=True)  
    chat_header = Column(String(250), nullable=True)  
    sender_bubble_color = Column(String(250), nullable=True) 
    receiver_bubble_color = Column(String(250), nullable=True)
    receiverTextColor = Column(String(250), nullable=True) 
    senderTextColor = Column(String(250), nullable=True)
    # Relationship with ChatbotConfig
    chatbot = relationship("ChatbotConfig", back_populates="settings")

class MessagesFeedbacks(Base):
    __tablename__ = 'messages_feedbacks'
    org_admin_id = Column(Integer, nullable=True, index=True)
    id = Column(Integer, primary_key=True, autoincrement=True)
    chatbot_id = Column(Integer, ForeignKey("chatbot.id"), nullable=False, index=True)
    message_id = Column(String, ForeignKey("chat_messages.message_uuid"), nullable=True, index=True)
    chatbot_name = Column(String(250), nullable=False, index=True, default=None)
    message_text = Column(Text, nullable=True)
    # Appearance fields
    user_name = Column(String(250), nullable=True, index=True, default=None)
    feedback = Column(Text, nullable=False, default=None)
    #status = Column(String(100), nullable=True, index=True, default=None)
    status = Column(
        SQLAEnum(FeedbackStatus, native_enum=False),
        nullable=False,
        default=FeedbackStatus.Unreviewed.value
    )
    chatbot_type = Column(String(250), nullable=False, index=True, default=None)
    
    # Relationship with ChatbotConfig
    chatbot = relationship("ChatbotConfig", back_populates="feedbacks")
    message = relationship("Messages", back_populates="feedbacks")

class ChatbotMemory(Base):
    __tablename__ = 'chatbot_memory'
    id = Column(Integer, primary_key=True, autoincrement=True)
    chatbot_id = Column(Integer, ForeignKey("chatbot.id"), nullable=False, index=True)

    # Appearance fields
    creator = Column(String(250), nullable=True)  
    memory_text = Column(Text, nullable=False, default=None)
    status = Column(String(100), nullable=True, index=True, default=None)
    # Relationship with ChatbotConfig
    chatbot = relationship("ChatbotConfig", back_populates="bot_memory")

class ChatSuggestion(Base):
    __tablename__ = 'chatbot_suggestion'
    id = Column(Integer, primary_key=True, autoincrement=True)
    chatbot_id = Column(Integer, ForeignKey("chatbot.id"), nullable=False, index=True)

    # Appearance fields
    suggestion_text = Column(Text, nullable=False, default=None)

    # Relationship with ChatbotConfig
    chatbot = relationship("ChatbotConfig", back_populates="bot_suggestion")

class BubbleSettings(Base):
    __tablename__ = 'bubble_settings'
    id = Column(Integer, primary_key=True, autoincrement=True)
    chatbot_id = Column(Integer, ForeignKey("chatbot.id"), nullable=False, index=True)

    # Appearance fields
    # bubble_bgColor = Column(String(250), nullable=True)  
    # bubble_icon = Column(String(250), nullable=True)  
    
    bubble_bgColor = Column(String(250), nullable=True)  
    bubble_icon = Column(String(250), nullable=True)  
    # bubble_size = Column(String(250), nullable=True) 
    # bubble_icon_color = Column(String(250), nullable=True)

    # Relationship with ChatbotConfig
    chatbot = relationship("ChatbotConfig", back_populates="bubble_settings")


def get_utc_now():
    return datetime.now(timezone.utc)


class SecurityAndLogs(Base):
    __tablename__ = 'securityandlogs'
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(500), nullable=True) 
    logs_type = Column(String(1000), nullable=True) 
    
    # Appearance fields
    description = Column(Text, nullable=False, default=None)
    # chatbot_id = Column(Integer, nullable=False)
    dated_at = Column(DateTime(timezone=True), default=get_utc_now, onupdate=get_utc_now)
    
    organization_id = Column(Integer, ForeignKey("organizations.id"))
    organization = relationship("Organization", back_populates="securityandlogs")

