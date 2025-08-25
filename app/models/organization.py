from sqlalchemy import Column, Integer, String, TIMESTAMP
from app.common.database_config import Base
from datetime import datetime, UTC
from sqlalchemy.orm import relationship
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy import ForeignKey, func, Boolean, Text


class Organization(Base):
    __tablename__ = "organizations"

    #Primary Key
    id = Column(Integer, primary_key=True, index=True)

    #Basic Details
    name = Column(String, index=True)
    logo = Column(String(1000), nullable=True)
    website_link = Column(String(1000), nullable=True)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, default=lambda: datetime.now(UTC))
    updated_at = Column(TIMESTAMP(timezone=True), nullable=False, default=lambda: datetime.now(UTC))
    current_plan = Column(String(50), nullable=True)

    # Relationships
    org_events = relationship("Event", back_populates="organization", cascade="all, delete-orphan")
    rbac_groups = relationship("RBAC", back_populates="organization", cascade="all, delete-orphan")
    users = relationship("User", back_populates="organization", cascade="all, delete-orphan")
    chatbots = relationship("ChatbotConfig", back_populates="organization", cascade="all, delete-orphan")
    securityandlogs = relationship("SecurityAndLogs", back_populates="organization", cascade="all, delete-orphan")
    def __repr__(self):
        return f"<Organization {self.name}>"

class RBAC(Base):
    __tablename__ = "rbac"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(1000))
    attributes = Column(JSONB, nullable=True) 
    form_submission = Column(Boolean, nullable=False)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False)
    organization = relationship("Organization", back_populates="rbac_groups")

class Event(Base):
    __tablename__ = 'events'

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, nullable=False)
    name = Column(String, nullable=False)
    building = Column(String, nullable=False)
    department = Column(String, nullable=False)
    title = Column(String, nullable=False)
    should_live_on_marti_page = Column(Boolean, nullable=False)
    should_live_on_marti_agent = Column(Boolean, nullable=False)
    marti_agent_review = Column(Boolean, nullable=False)
    marti_website_review = Column(Boolean, nullable=False)
    is_rejected_marti_website = Column(Boolean, nullable=True, default=False)
    is_rejected_marti_agent = Column(Boolean, nullable=True, default=True)

    admin_event_review = Column(Boolean, nullable=True, default=False)
    user_response_to_review = Column(Boolean, nullable=True, default=False)
    is_seen = Column(Boolean, nullable=True, default=False)
    additional = Column(Text, nullable=True)
    
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False)
    organization = relationship("Organization", back_populates="org_events")

    # Relationship
    documents = relationship("EventDocument", back_populates="event", cascade="all, delete-orphan")
    event_feedback = relationship("EventFeedBack", back_populates="event", cascade="all, delete-orphan")
    # cases = relationship("EventCase", back_populates="event", cascade="all, delete-orphan")


class EventDocument(Base):
    __tablename__ = 'event_documents'

    id = Column(Integer, primary_key=True, index=True)
    event_id = Column(Integer, ForeignKey('events.id'), nullable=False)
    document_name = Column(String, nullable=False)
    content_type = Column(String, nullable=False)
    status = Column(String, nullable=False, default='pending')  # e.g., 'pending', 'approved'
    created_at = Column(TIMESTAMP, nullable=False, default=func.now())
    updated_at = Column(TIMESTAMP, nullable=True, default=None, onupdate=func.now())

    # Correct relationship
    event = relationship("Event", back_populates="documents")


class EventFeedBack(Base):
    __tablename__ = 'event_feedback'
    id = Column(Integer, primary_key=True, autoincrement=True)
    event_id = Column(Integer, ForeignKey('events.id'), nullable=False)
    user_feedback = Column(Text, nullable=True)  

    # Correct relationship
    event = relationship("Event", back_populates="event_feedback")


# from sqlalchemy import Enum
# from sqlalchemy.sql import func
# import enum
# class CaseStatus(enum.Enum):
#     OPEN = "open"
#     IN_PROGRESS = "in_progress"
#     RESOLVED = "resolved"
#     CLOSED = "closed"


# class EventCase(Base):
#     __tablename__ = "event_cases"

#     id = Column(Integer, primary_key=True, index=True)
#     event_id = Column(Integer, ForeignKey("events.id"), nullable=False)
#     created_email = Column(String, nullable=False)  # User email
#     status = Column(Enum(CaseStatus), default=CaseStatus.CLOSED)
#     created_at = Column(TIMESTAMP(timezone=True), nullable=False, default=lambda: datetime.now(UTC))
#     organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False)
#     case_title = Column(String)
#     # Relationships
#     event = relationship("Event", back_populates="cases")
#     messages = relationship("CaseMessage", back_populates="case", cascade="all, delete-orphan")
    
# class CaseMessage(Base):
#     __tablename__ = "case_messages"

#     id = Column(Integer, primary_key=True, index=True)
#     case_id = Column(Integer, ForeignKey("event_cases.id"), nullable=False)
#     message = Column(Text, nullable=False)
#     is_user_message = Column(Boolean, nullable=False, default=False)
#     timestamp = Column(TIMESTAMP(timezone=True), nullable=False, default=lambda: datetime.now(UTC))

#     # Relationships
#     case = relationship("EventCase", back_populates="messages")
    

# [
#     {
#         chatbot_id: int,
#         can_edit_webscrap: bool,
#         can_edit_fileupload: bool,
#         can_edit_qa:bool,
#         can_edit_traning_text:bool,
#         can_edit_memory:bool,
#         can_view_feedback: bool,
#         can_view_chat_logs: bool,
#         can_view_insigh: bool
#     },
#     {
#         chatbot_id: int,
#         can_edit_webscrap: bool,
#         can_edit_fileupload: bool,
#         can_edit_qa:bool,
#         can_edit_traning_text:bool,
#         can_edit_memory:bool,
#         can_view_feedback: bool,
#         can_view_chat_logs: bool,
#         can_view_insigh: bool
#     }
# ]