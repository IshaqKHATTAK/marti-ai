from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.models.user import User
from app.models.chatbot_model import ChatbotConfig, SecurityAndLogs
from fastapi import HTTPException
from app.models.user import UserRole
from datetime import datetime, timezone

async def get_user_organization_admin(db: AsyncSession, organization_id:int):
    query = (
        select(User)
        .filter(
            User.organization_id == organization_id,  # Match organization_id
            User.role == UserRole.ADMIN  # Must have ADMIN role
        )
    )
    result = await db.execute(query)
    return result.scalar_one_or_none()
    
async def get_user_by_email(db: AsyncSession, email: str) -> User | None:
    """Get user by email from database."""
    query = select(User).filter(User.email == email)
    result = await db.execute(query)
    return result.scalar_one_or_none()
 
from app.models.organization import Organization

async def get_organization(db: AsyncSession, org_id: int):
    query = select(Organization).filter(Organization.id == org_id)
    result = await db.execute(query)
    return result.scalar_one_or_none()

async def get_user_by_id(db: AsyncSession, user_id: int) -> User:
    """Get user by ID from database."""
    query = select(User).filter(User.id == user_id)
    result = await db.execute(query)
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user

async def get_chatbot_by_id(db: AsyncSession, bot_id: int) -> User:
    """Get user by ID from database."""
    query = select(ChatbotConfig).filter(ChatbotConfig.id == bot_id)
    result = await db.execute(query)
    chatbot = result.scalar_one_or_none()
    if not chatbot:
        raise HTTPException(status_code=404, detail="User not found")
    return chatbot

async def insert_logs(organization_id, description, name, logs_type, db):
    logs = SecurityAndLogs(
        organization_id = organization_id,
        description = description,
        name = name,
        logs_type = logs_type,
        dated_at = datetime.utcnow()
    )

    db.add(logs)
    # await db.commit()
    await db.flush()   # Use flush before commit with AsyncSession
    await db.commit()
    return 