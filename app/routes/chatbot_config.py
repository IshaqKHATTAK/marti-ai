from fastapi import APIRouter, Depends, status 
from app.schemas.request.chatbot_config import UpdateChatbotConfigRequest,InputData,CreateData
from app.schemas.response.chatbot_config import ChatbotConfigResponse, SecurityAndLogsResponse
from app.services import chatbot_config
from sqlalchemy.orm import Session
from fastapi.responses import JSONResponse
from app.services.auth import get_current_user
from app.common.database_config import get_async_db
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.user import User

chatbot_config_routes = APIRouter(
    prefix="/api/v1/security-logs",
    tags=["Admin"],
    responses={404: {"description": "Not found"}},
    # dependencies=[Depends(oauth2_scheme), Depends(validate_access_token), Depends(is_admin)]
)

from typing import List, Optional
from app.schemas.request.chatbot_config import SecurityAndLogs
from fastapi import Query
@chatbot_config_routes.post("/{organization_id}/get", status_code=status.HTTP_200_OK, response_model=SecurityAndLogsResponse)
async def security_and_logs(
    organization_id: int,
    logs_type: str = Query(None),
    skip: int = 0,
    limit: int = 10,
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(get_current_user),
):
    response = await chatbot_config.security_and_logs_service(organization_id, current_user, skip, limit,  db, logs_type)
    return response
    

# @chatbot_config_routes.post("/create", status_code=status.HTTP_200_OK)
# async def update_admin_config(data:CreateData, session: Session = Depends(database_config.get_async_db)): 
#     config_data =  await chatbot_config.create_chatbot_config(data, session)
#     return JSONResponse({"message": "Chatbot config has been created successfully"})

# @chatbot_config_routes.post("/config", status_code=status.HTTP_200_OK, response_model=ChatbotConfigResponse)
# async def update_admin_config(data:InputData, session: Session = Depends(database_config.get_async_db)): #, 
#     config_data =  await chatbot_config.get_chatbot_config(data, session)
#     return ChatbotConfigResponse(id = config_data.id, llm_model_name=config_data.llm_model_name, llm_temperature=config_data.llm_temperature, llm_prompt=config_data.llm_prompt, llm_role=config_data.llm_role, llm_streaming=config_data.llm_streaming)

# @chatbot_config_routes.put("/update", status_code=status.HTTP_200_OK)
# async def update_admin_config(data: UpdateChatbotConfigRequest, session: Session = Depends(database_config.get_async_db)): #, session: Session = Depends(get_session)
#     await chatbot_config.update_chatbot_config(data, session)
#     return JSONResponse({"message": "Chatbot config has been updated successfully"})

