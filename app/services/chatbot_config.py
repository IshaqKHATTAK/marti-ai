from app.models.chatbot_model import ChatbotConfig, SecurityAndLogs
from fastapi import HTTPException,status
from datetime import datetime, timezone
from sqlalchemy.future import select
from sqlalchemy.exc import SQLAlchemyError
from app.services.organization import _role_based_checks
from sqlalchemy import desc


async def _get_chatbot_config(data,session):
    result = await session.execute(select(ChatbotConfig).filter(ChatbotConfig.id == data.id)) 
    return result.scalars().first()

async def create_chatbot_config(data, session):
    try:    
        chatbot_config = ChatbotConfig(
            llm_model_name=data.llm_model_name,
            llm_temperature=data.llm_temperature,
            llm_prompt=data.llm_prompt,
            llm_role=data.llm_role,
            llm_streaming=data.llm_streaming,  
        )
        session.add(chatbot_config)
        await session.commit()
        return chatbot_config
    except SQLAlchemyError as e:
        await session.rollback()  
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Database error occurred: {str(e)}"
        )
    except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"An unexpected error occurred: {str(e)}"
            )

async def get_chatbot_config(data,session):
    chatbot_config = await _get_chatbot_config(data, session)
    if chatbot_config is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No chatbot config found in database")
    
    return chatbot_config

async def get_all_logs(organization_id, skip, limit,  db, logs_type = None):
    stmt = select(SecurityAndLogs).filter(SecurityAndLogs.organization_id == organization_id)
    # if chatbot_ids:
    #     stmt = stmt.filter(SecurityAndLogs.chatbot_id.in_(chatbot_ids))
    if logs_type:
        stmt = stmt.filter(SecurityAndLogs.logs_type.ilike(f"%{logs_type}%"))    
    stmt = stmt.order_by(desc(SecurityAndLogs.dated_at))                                
    stmt = stmt.offset(skip).limit(limit)

    result = await db.execute(stmt)
    return result.scalars().all()

from sqlalchemy import func
async def get_total_logs_count(organization_id, db, logs_type):
    stmt = select(func.count()).select_from(SecurityAndLogs).filter(
        SecurityAndLogs.organization_id == organization_id
    )
    # if chatbot_ids:
    #     stmt = stmt.filter(SecurityAndLogs.chatbot_id.in_(chatbot_ids))
    if logs_type:
        stmt = stmt.filter(SecurityAndLogs.logs_type.ilike(f"%{logs_type}%"))
    result = await db.execute(stmt)
    count = result.scalar_one()
    return count

async def update_chatbot_config(data, session):
    chatbot_setup = await _get_chatbot_config(data, session)
    if chatbot_setup is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No chatbot config found in database")
    
    update_dict = {k: v for k, v in data.dict().items() if v is not None}
    if len(update_dict) == 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No valid data found in request")

    for key, value in update_dict.items():
        setattr(chatbot_setup, key, value)
    
    chatbot_setup.updated_at = datetime.now(timezone.utc)
    await session.commit()
    return chatbot_setup

from app.schemas.response.chatbot_config import logs, SecurityAndLogsResponse

async def security_and_logs_service(organization_id, current_user, skip, limit, db, logs_type = None):
    await _role_based_checks(current_user=current_user, organization_id=organization_id)
    records = await get_all_logs(organization_id=organization_id, skip = skip, limit=limit, db=db, logs_type = logs_type)
    total_formatted_records = []
    for record in records:
        total_formatted_records.append(logs(
            name=record.name,
            description=record.description,
            logs_type = record.logs_type,
            date=record.dated_at.strftime("%Y-%m-%d %H:%M:%S")
        )
        )
    
    total_records = await get_total_logs_count(organization_id=organization_id, db=db, logs_type = logs_type)
    
    return SecurityAndLogsResponse(
        organization_logs=total_formatted_records,
        total_logs=total_records
    )