from fastapi import APIRouter, Depends, status, Form, Body, HTTPException, UploadFile, File
import json
from sqlalchemy.ext.asyncio import AsyncSession
from app.common.database_config import get_async_db
from app.schemas.request.organization import (
    OrganizationUserUpdate,
)

from app.utils.db_helpers import get_organization, insert_logs
from urllib.parse import urlparse
from typing import Optional
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from app.schemas.request.user import UserCreate
from app.schemas.response.organization import OrganizationListResponse,OrganizationResponse,UpdateUserProfile,UpdateOrganization, OrganizationUsersResponse, UserData, AllSuperAdminOrganizations, SuperAdminOrganizations
from app.schemas.response.user import UserResponse, BulkUploadResponse, UserResponseCreate
from app.services.organization import (
    create_bulk_user_service,
    list_organization_users_service,
    list_chatbot_docs,
    list_chatbot_urls,
    get_total_chatbot_filesize,
    get_file_and_webscrap_sizes_for_chatbot,
    super_admin_list_organization_users_service,
    update_organization_profile_data,
    list_platform_pre_existing_agents,
    fetch_organization_profile,
    update_user_profile,
    create_organization_chatbot_service,
    update_organization_chatbot_traning_QAs,
    list_organization_chatbots_service,
    fech_allowed_usage_per_admin,
    update_bot_streaming,
    get_organization_chatbot_service,
    update_organization_chatbot_files,
    check_if_file_exist,
    # update_organization_chatbot_service,
    update_organization_chatbot_traning_text,
    update_organization_chatbot_model_name,
    update_image_generation_organization_chatbot_model_name,
    get_organization_chabot_name,
    get_organization_image_generation_chabot_name,
    update_organization_chatbot_traning_guardrails,
    fetch_chatbot_llm_model,
    update_organization_chatbot_details,
    update_organization_user_status_service,
    get_user_name_email_by_id,
    remove_user_from_organization_service,
    list_all_organizations_service,
    create_organization_user_service,
    remove_chatbot_from_organization_service,
    bubble_settings_customization,
    chatbot_settings_customization,
    get_qa_templates_service,
    get_chatbot_settings_customization,
    get_public_chatbot_settings_customization,
    get_total_chatbot_webscrap_size,
    get_bubble_settings_customization,
    create_organization_chatbot_memory,
    update_organization_chatbot_memory,
    get_organization_chatbot_memory,
    delete_organization_chatbot_memory,
    create_organization_chatbot_suggestion,
    update_organization_chatbot_suggestion,
    get_organization_chatbot_suggestion,
    get_public_chatbot_suggestion,
    delete_organization_chatbot_suggestion,
    normalize_url
)
from app.services.user import (
    toggle_user_active_status_service,
    toggle_chatbot_memory
)
from fastapi import Query
from app.services.auth import get_current_user, check_roles
from app.models.user import User, UserRole
from app.models.chatbot_model import BotType
from typing import List
from app.schemas.request.chatbot_config import CreateData, QATemplateData, BotDetails, UpdateChatbotConfigRequest, ChatbotDetails, QAsRemove, GuardrailsAdded, GuardrailsRemoved, GuardrailsUpdated, ChatbotCutomize, BubbleCutomize
from app.schemas.response.chatbot_config import ChatbotFileUpdateResponse, UrlValidationResponse, WebsiteUrlPagination, DocumentPagination,ChatbotConfigResponse, WebsiteUrl,AllPreExistingChatbotResponse, AllChatbotResponse,ListBots,ChatBotCreation, WebscrapUrl, DocumentInfo, ImageGenerationLlmModelEnum,LlmModelEnum, AppModels, BotLlmRequest,UrlValidationRequest, WebsiteRemoved, DetailsRequest, ChatbotPrompt, DocumentRemoved, UpdateQATemplateData, ChatbotMemory, GetChatbotMemoryResponse,ChatbotMemoryResponse, ChatbotSuggestion,ChatbotSuggestionUpdate, ChatbotSuggestionResponse
from app.schemas.response import chatbot_config
from app.common.env_config import get_envs_setting

envs = get_envs_setting()
router = APIRouter()
###########################################SuperAdmin routes####################################
# Super admin route
@router.get("/list", response_model=AllSuperAdminOrganizations)
async def list_all_organizations(
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(get_current_user),
    limit: int = Query(10, ge=1),
    skip: int = Query(0, ge=0),
):
    """List all organizations (Super Admin only)"""
    return await list_all_organizations_service(db, current_user, limit,skip)


####################################### Admin and superadmin combined users routes ###########################################
@router.patch("/user/profile", response_model=UpdateUserProfile)
async def update_organization_profile(
    updated_data: UpdateUserProfile,
    db: AsyncSession = Depends(get_async_db),
    current_user=Depends(get_current_user)
):
    return await update_user_profile(db, current_user, updated_data)

@router.get("/{organization_id}/profile", response_model=UpdateOrganization)
async def get_organization_profile(
    organization_id: int,
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(get_current_user)
):
    return await fetch_organization_profile(db, organization_id, current_user)

@router.patch("/{organization_id}/profile", response_model=UpdateOrganization)
async def update_organization_profile(
    organization_id: int,
    updated_data: UpdateOrganization,
    db: AsyncSession = Depends(get_async_db),
    current_user=Depends(get_current_user)
):
    return await update_organization_profile_data(db, organization_id, current_user,updated_data)

@router.get("/{organization_id}/admin/list/users", response_model=OrganizationUsersResponse)
async def list_admin_organization_users(
    organization_id: int,
    # skip: int = 0, 
    # limit: int = 5,
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(get_current_user)
):
    return await super_admin_list_organization_users_service(db, organization_id, current_user)
    #return await list_organization_users_service(db, organization_id, current_user, skip, limit, super_admin_api = True)
    
import stripe

@router.post("/{organization_id}/create/user", response_model=UserResponseCreate)
async def create_organization_user(
    organization_id: int,
    user_data: UserCreate,
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(get_current_user)
):
    """Create a new user in the organization"""
    
    response = await create_organization_user_service(db, organization_id, user_data, current_user)
    await insert_logs(organization_id, f"A new user account has been created and added to the system.", f'{current_user.name}', "User", db)
    return response

@router.post("/{organization_id}/bulk-upload", status_code=status.HTTP_200_OK)
async def bulk_upload_users(
    organization_id: int,
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(get_current_user),
    file: UploadFile = File(...),
):
    
    await create_bulk_user_service(organization_id, db, current_user, file)
    # await create_bulk_user_service(db, organization_id, file, current_user)
    await insert_logs(organization_id, f"A new user account has been created and added to the system.", f'{current_user.name}', "User", db)
    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={"message": "File has been uploaded successfully for processing"}
    ) 

@router.get("/{organization_id}/list/users", response_model=OrganizationUsersResponse)
async def list_organization_users(
    organization_id: int,
    skip: int = 0, 
    limit: int = 5,
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(get_current_user)
):
    return await list_organization_users_service(db, organization_id, current_user, skip, limit)
    # Format the response
    # response = OrganizationUsersResponse(
    #     organization_name=organization_name,
    #     organization_id=organization_id,
    #     users_data=users
    # )
    # return response

@router.get("/{organization_id}/get/user/{user_id}", response_model=UserData)
async def get_organization_user(
    organization_id: int,
    user_id: int,
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(get_current_user)
):
    return await get_user_name_email_by_id(db, organization_id, user_id, current_user)

@router.patch("/{organization_id}/update/user/{user_id}", response_model=OrganizationListResponse)
async def update_organization_user_status(
    organization_id: int,
    user_id: int,
    user_data: OrganizationUserUpdate,
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(get_current_user)
):
    response =  await update_organization_user_status_service(
        db, organization_id, user_id, user_data, current_user
    )
    await insert_logs(organization_id, f"The profile information (e.g., name, email, rbac) of a user has been updated..", f'{current_user.name}', "User", db)
    return response

@router.patch("/{organization_id}/toggle-active/user/{user_id}")
async def toggle_user_active_status(
    organization_id: int,
    user_id: int,
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(get_current_user)
) -> UserResponse:
    user = await toggle_user_active_status_service(organization_id, user_id, current_user, db)
    toggle = "on" if user.is_active else "off"
    await insert_logs(user.organization_id, f"{user.name} has been successfully toggle {toggle}.", f'{current_user.name}', "Organization", db)
    
    return UserResponse.model_validate(user)

@router.delete("/{organization_id}/delete/user/{user_id}", status_code=status.HTTP_200_OK)
async def remove_user_from_organization(
    organization_id: int,
    user_id: int,
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(get_current_user)
):
    await remove_user_from_organization_service(db, organization_id, user_id, current_user)
    await insert_logs(organization_id, f"A user account has been removed from the system.", f'{current_user.name}', "User", db)
    
    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={"message": "User successfully removed from the organization"}
    ) 

####################################### Admin and superadmin combined chatbot routes ###########################################

@router.post("/{organization_id}/create/chatbot", response_model=ChatBotCreation)
async def create_organization_chatbot(
    organization_id: int,
    chatbot_details: BotDetails,
    qa_templates: Optional[list[QATemplateData]] = None, 
    guardrails: Optional[list[str]] = None,
    # website_links: Optional[str] = None,
    website_links: Optional[List[WebsiteUrl]] = None,
    document_files: Optional[List[str]] = None,
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(get_current_user),
):
    """Create a chatbot for an organization"""
    guardrails_list = []
    try:
        if len(chatbot_details.llm_prompt) > envs.TRANING_TEXT_CHAR_LENGTH:
            raise HTTPException(status_code=422, detail=f"Maximum allowed character limit are 500.")
        if len(qa_templates) >= envs.TOTAL_NO_OF_QAS:
            raise HTTPException(status_code=422, detail=f"Maximum allowed limit reached.")
        for qa in qa_templates:
            if len(qa.question) + len(qa.answer) > envs.PER_QA_CHAR_LEN:
                raise HTTPException(status_code=422, detail=f"Maximum allowed character limit are 100.")
        if len(guardrails) > envs.TOTAL_NO_OF_GUARDRAILS:
            raise HTTPException(status_code=422, detail=f"Maximum allowed guardrails limit are 5.")
        for guardrail in guardrails:  
            if len(guardrail) > envs.PER_GUARDRAILS_CHAR_LEN:
                raise HTTPException(status_code=422, detail=f"Maximum allowed character limit are 300.")
        if len(website_links) > envs.TOTAL_NO_OF_ALLOWED_URLS:
            raise HTTPException(status_code=422, detail=f"Maximum allowed website limit are 50.")
        # if len(document_files) > envs.TOTAL_NO_OF_ALLOWED_DOCS:
        #     raise HTTPException(status_code=422, detail=f"Maximum allowed document file limit are 5.")
        
        # Validate scaffolding level for student chatbots
        if chatbot_details.bot_type == BotType.student and chatbot_details.scaffolding_level is None:
            raise HTTPException(status_code=422, detail="Scaffolding level is required for student type chatbots.")
        if chatbot_details.bot_type != BotType.student and chatbot_details.scaffolding_level is not None:
            raise HTTPException(status_code=422, detail="Scaffolding level should not be provided.")
        
        # if guardrails:
        #     guardrails_list = json.loads(guardrails)
        #     if not isinstance(guardrails_list, list):
        #         raise ValueError("guardrails not in expected format.")
        
        # Parse JSON strings
        # parsed_qa_templates = json.loads(qa_templates) if qa_templates else []
        # parsed_website_links = [WebsiteUrl(**link) for link in json.loads(website_links)] if website_links else []
        
        normalized_website_links = set()
        for link in website_links:
            normalized_link =  normalize_url(link.website_link)
            if normalized_link in normalized_website_links:
                raise HTTPException(
                    status_code=400,
                    detail=f"Duplicate website URL found: {link.website_link}"
                )
            normalized_website_links.add(normalized_link)
    
        # parsed_guardrails = json.loads(guardrails) if guardrails else []
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=422, detail=f"Invalid JSON format: {e}")
    # except ValidationError as e:
    #     raise HTTPException(status_code=422, detail=f"Validation error for website_links: {e}")

    # Assemble chatbot_data manually
    chatbot_data = {
        "llm_temperature": chatbot_details.llm_temperature,
        "organization_id": organization_id,
        "llm_role": chatbot_details.llm_role,
        "llm_streaming": chatbot_details.llm_streaming,
        "llm_model_name": chatbot_details.llm_model_name,
        "llm_prompt": chatbot_details.llm_prompt,
        "chatbot_name": chatbot_details.chatbot_name,
        "scaffolding_level": chatbot_details.scaffolding_level,
    }
    converted_data = CreateData(**chatbot_data)
    chatbot = await create_organization_chatbot_service(
        db, 
        organization_id, 
        converted_data, 
        current_user, 
        chatbot_details.avatar, 
        document_files,
        website_links,
        guardrails,
        qa_templates,
        chatbot_details.bot_type.value
    )

    # Eagerly load relationships and serialize response
    response_data = ChatBotCreation(
        id=chatbot.id,
        chatbot_type=chatbot.chatbot_type,
        chatbot_name=chatbot.chatbot_name,
        avatar = chatbot.avatar_url if chatbot.avatar_url else "",
        # llm_model_name = chatbot.llm_model_name
    )
    await insert_logs(organization_id, f"{current_user.name} has successfully created chatbot.", f'{current_user.name}', "Chatbot", db)
    
    return response_data


@router.get("/{organization_id}/list/pre-existing", response_model= AllPreExistingChatbotResponse)
async def list_organization_chatbots(
    organization_id: int,
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(get_current_user)
):
    """List all chatbots for an organization"""
    # chatbots =  await list_organization_chatbots_service(db, organization_id, current_user, cateogry_indicator)
    
    response = []
    organization_data = await get_organization(db = db, org_id = organization_id)
    
    chatbots =  await list_platform_pre_existing_agents(current_user)
    for chatbot in chatbots:
            response.append(ChatBotCreation(
            chatbot_type=chatbot["chatbot_type"],
            chatbot_name=chatbot["chatbot_name"],
            avatar = chatbot["avatar_url"] if chatbot["avatar_url"] else "",
            specilized_type=chatbot["specilized_type"]
            # llm_model_name=chatbot["llm_model_name"]
        ))
    
    return AllPreExistingChatbotResponse(org_avatar = organization_data.logo, chatbots = response)


@router.get("/{organization_id}/list/chatbots", response_model= AllChatbotResponse) # AllChatbotResponse
async def list_organization_chatbots(
    organization_id: int,
    cateogry_indicator:int,
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(get_current_user)
):
    """List all chatbots for an organization"""
    chatbots =  await list_organization_chatbots_service(db, organization_id, current_user, cateogry_indicator)
    
    response = []
    organization_data = await get_organization(db = db, org_id = organization_id)
    chatbots =  await list_organization_chatbots_service(db, organization_id, current_user, cateogry_indicator)
    
    for chatbot in chatbots:
            from app.utils.database_helper import check_and_refresh_chat_cycle
            is_expire = await check_and_refresh_chat_cycle(current_user, chatbot, session=db)
            
            allowed_messages = 0
            # if chatbot.chatbot_type == "External":
            allowed_messages = await fech_allowed_usage_per_admin(chatbot, session=db)
            response.append(ListBots(
            id=chatbot.id,
            chatbot_type=chatbot.chatbot_type,
            chatbot_name=chatbot.chatbot_name,
            avatar = chatbot.avatar_url if chatbot.avatar_url else "",
            specilized_type = chatbot.specialized_type,
            used_messages= 0 if is_expire else chatbot.monthly_messages_count // 2 if chatbot.monthly_messages_count else 0,
            allowed_messages=allowed_messages // 2
            # llm_model_name=chatbot.llm_model_name
        ))
                
    return AllChatbotResponse(org_avatar = organization_data.logo, chatbots = response)


@router.patch("/{organization_id}/streaming/chatbots/{chatbot_id}")
async def update_chatbot_streaming(
    organization_id: int,
    chatbot_id: int,
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(get_current_user)
):
    """Get a specific chatbot"""
    return await update_bot_streaming(db, organization_id, chatbot_id, current_user)



@router.get("/{organization_id}/get/chatbot/{chatbot_id}", response_model=ChatbotConfigResponse)
async def get_organization_chatbot(
    organization_id: int,
    chatbot_id: int,
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(get_current_user)
):
    """Get a specific chatbot"""
    return await get_organization_chatbot_service(db, organization_id, chatbot_id, current_user)

@router.get("/{organization_id}/get/chatbot/{chatbot_id}/qa-templates", response_model=chatbot_config.QATemplateDataPaginated)
async def get_chatbot_qa_templates(
    organization_id: int,
    chatbot_id: int,
    skip: int = 0,
    limit: int = 10,
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(get_current_user),
):
    qas,total_qas =  await get_qa_templates_service(db, organization_id, chatbot_id, current_user, skip, limit)
    return chatbot_config.QATemplateDataPaginated(
        total_qas = total_qas,
        qa_data = qas
    )

@router.get("/allowed/models", status_code=status.HTTP_200_OK)
async def get_allowed_llm_models():
    """
    API to fetch the allowed LLM models.
    """
    
    return {"allowed_models": [model.value for model in LlmModelEnum]}

@router.patch("/{organization_id}/update/chatbot/{chatbot_id}/details", status_code=status.HTTP_200_OK)
async def update_organization_chatbot_detials(
    organization_id: int,
    chatbot_id: int,
    detials_request: DetailsRequest,
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(get_current_user) 
):
    chatbot_update =  await update_organization_chatbot_details(
        db, 
        organization_id,
        chatbot_id,
        detials_request.chatbot_name,
        detials_request.chatbot_role,
        current_user,
        detials_request.avatar,
        detials_request.scaffolding_level
    )
    await insert_logs(organization_id, f"The configuration or settings of a chatbot instance have been updated.", f'{current_user.name}', "Chatbot", db)
    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={"message": "Chatbot has been updated successfully."}
    ) 

@router.patch("/{organization_id}/update/chatbot/{chatbot_id}/text", status_code=status.HTTP_200_OK)
async def update_organization_chatbot_text(
    organization_id: int,
    chatbot_id: int,
    training_text: ChatbotPrompt,
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(get_current_user) 
):
    if len(training_text.trianing_text) > envs.TRANING_TEXT_CHAR_LENGTH:
            raise HTTPException(status_code=422, detail=f"Maximum allowed character limit 600 has exceeded.")
    
    chatbot_update =  await update_organization_chatbot_traning_text(
        db, 
        organization_id,
        chatbot_id,
        training_text.trianing_text,
        current_user
    )
    await insert_logs(organization_id, f"The configuration or settings of a chatbot instance have been updated.", f'{current_user.name}', "Chatbot", db)
    
    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={"message": "Chatbot has been updated successfully."}
    ) 


@router.get("/files-webscrap-sizes")
async def chatbot_webscrap_and_file_sizes(
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(get_current_user)
):
    return await get_file_and_webscrap_sizes_for_chatbot()


@router.get("/{organization_id}/chatbot/{chatbot_id}/url-knowledge-base", response_model=WebsiteUrlPagination)
async def list_organization_users(
    organization_id: int,
    chatbot_id: int,
    skip_urls: int = 0,
    limit_urls: int = 5,
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(get_current_user)
):
    return await list_chatbot_urls(db, organization_id, chatbot_id, current_user, skip_urls, limit_urls)

@router.get("/{organization_id}/chatbot/{chatbot_id}/doc-knowledge-base", response_model=DocumentPagination)
async def list_organization_users(
    organization_id: int,
    chatbot_id: int,
    skip_docs: int = 0,
    limit_docs: int = 5,
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(get_current_user)
):
    return await list_chatbot_docs(db, organization_id, chatbot_id, current_user, skip_docs, limit_docs)
    # return await list_organization_users_service(db, organization_id, current_user, skip, limit)

@router.post("/{organization_id}/validate-url/chatbot")#, response_model=UrlValidationResponse)
async def validate_url_for_chatbot(
    organization_id: int,
    request: UrlValidationRequest,
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(get_current_user)
):
    # try:
    # def is_valid_url(url: str) -> bool:
    #     parsed = urlparse(url)
    #     return all([parsed.scheme, parsed.netloc])
    # if not is_valid_url(request.url):
    #     raise HTTPException(status_code=422, detail="Invalid URL format")
    response =  await check_if_file_exist(
        db, 
        organization_id,
        request,
        current_user
    )
    return response
    # except ValidationError as ve:
    #     raise HTTPException(status_code=422, detail="Invalid URL format")

    # except Exception as e:
    #     raise HTTPException(status_code=400, detail=f"URL validation failed")

@router.patch("/{organization_id}/update/chatbot/{chatbot_id}/files", response_model=ChatbotFileUpdateResponse)
async def update_organization_chatbot_files_route(
    organization_id: int,
    chatbot_id: int,
    document_added: Optional[List[str]] = None,
    document_removed: Optional[List[DocumentRemoved]] = None, # empty list incasee no delete is required
    website_added:Optional[List[WebsiteUrl]] = None, 
    website_removed:Optional[List[WebsiteRemoved]] = None, #empty in case no delete of website req
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(get_current_user) 
):
    try:
        chatbot_update =  await update_organization_chatbot_files(
        db, 
        organization_id,
        chatbot_id,
        document_added,
        document_removed,
        website_added,
        website_removed,
        current_user
        )
        await insert_logs(organization_id, f"The configuration or settings of a chatbot instance have been updated.", f'{current_user.name}', "Chatbot", db)
        
        return chatbot_update

    except json.JSONDecodeError as e:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid JSON format: {str(e)}"
        )
    except ValidationError as e:
        raise HTTPException(
            status_code=400,
            detail=f"Validation error: {str(e)}"
        )
        
    

@router.patch("/{organization_id}/update/chatbot/{chatbot_id}/QAs",  response_model=List[chatbot_config.QATemplateData])
async def update_organization_chatbot_qa(
    organization_id: int,
    chatbot_id: int,
    QAs_added: Optional[List[UpdateQATemplateData]] = None,
    QAs_removed: Optional[List[QAsRemove]] = None,
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(get_current_user) 
):
    for qa in QAs_added:
        if len(qa.question) + len(qa.answer) > envs.PER_QA_CHAR_LEN:
            raise HTTPException(status_code=422, detail=f"Maximum allowed character limit are 100.")

    chatbot_update =  await update_organization_chatbot_traning_QAs(
        db,
        organization_id,
        chatbot_id,
        QAs_added,
        QAs_removed,
        current_user
    )
    await insert_logs(organization_id, f"The configuration or settings of a chatbot instance have been updated.", f'{current_user.name}', "Chatbot", db)
    
    return chatbot_update

@router.patch("/{organization_id}/update/chatbot/{chatbot_id}/guardrails", response_model=List[chatbot_config.GetGuardrails])
async def update_organization_chatbot_guardrails(
    organization_id: int,
    chatbot_id: int,
    guardrails_added: Optional[List[GuardrailsUpdated]] = None,
    guardrails_removed: Optional[List[GuardrailsRemoved]] = None,
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(get_current_user) 
):
    for guardrail in guardrails_added:  
            if len(guardrail.guardrail) > envs.PER_GUARDRAILS_CHAR_LEN:
                raise HTTPException(status_code=422, detail=f"Maximum allowed character limit are 300.")
        
    chatbot_update =  await update_organization_chatbot_traning_guardrails(
        db, 
        organization_id,
        chatbot_id,
        guardrails_added,
        guardrails_removed,
        current_user
    )
    await insert_logs(organization_id, f"The configuration or settings of a chatbot instance have been updated.", f'{current_user.name}', "Chatbot", db)
    
    return chatbot_update

@router.delete("/{organization_id}/delete/chatbot/{chatbot_id}", status_code=status.HTTP_200_OK)
async def remove_chatbot_from_organization(
    organization_id: int,
    chatbot_id: int,
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(get_current_user)
):
    await remove_chatbot_from_organization_service(db, organization_id, chatbot_id, current_user)
    await insert_logs(organization_id, f"An existing chatbot instance has been removed from the system.", f'{current_user.name}', "Chatbot", db)
    
    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={"message": "Chatbot has deleted successfully from the organization."}
    ) 


@router.post("/{organization_id}/setting/customize/chatbot/{chatbot_id}", response_model=ChatbotCutomize)
async def cutomize_organization_chatbot(
    organization_id: int,
    chatbot_id: int,
    customization_data:ChatbotCutomize,
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(get_current_user)
):
    chatbot_setting_customize =  await chatbot_settings_customization(
        db, 
        organization_id,
        chatbot_id,
        customization_data,
        current_user
    )

    return chatbot_setting_customize

@router.post("/{organization_id}/setting/customize/bubble/{chatbot_id}", response_model=BubbleCutomize)
async def cutomize_organization_chatbot(
    organization_id: int,
    chatbot_id: int,
    customization_data:BubbleCutomize,
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(get_current_user)
):
    bubble_setting_customize =  await bubble_settings_customization(
        db, 
        organization_id,
        chatbot_id,
        customization_data,
        current_user
    )

    return bubble_setting_customize

@router.get("/{organization_id}/get/setting/customize/chatbot/{chatbot_id}", response_model=ChatbotCutomize)
async def cutomize_organization_chatbot(
    organization_id: int,
    chatbot_id: int,
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(get_current_user)
):
    chatbot_setting_customize =  await get_chatbot_settings_customization(
        db, 
        organization_id,
        chatbot_id,
        current_user
    )
    return chatbot_setting_customize

@router.get("/get/setting/customize/chatbot/public/{chatbot_id}", response_model=ChatbotCutomize)
async def cutomize_organization_chatbot(
    chatbot_id: str,
    db: AsyncSession = Depends(get_async_db),
):
    chatbot_setting_customize =  await get_public_chatbot_settings_customization(
        db, 
        chatbot_id
    )
    return chatbot_setting_customize

@router.get("/get/setting/customize/bubble/{chatbot_id}", response_model=BubbleCutomize)
async def cutomize_organization_chatbot(
    chatbot_id: int,
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(get_current_user)

):
    bubble_setting_customize =  await get_bubble_settings_customization(
        db, 
        chatbot_id
    )
    return bubble_setting_customize

@router.get("/get/setting/customize/public/bubble/{chatbot_id}", response_model=BubbleCutomize)
async def cutomize_organization_chatbot(
    chatbot_id: str,
    db: AsyncSession = Depends(get_async_db),
):
    bubble_setting_customize =  await get_bubble_settings_customization(
        db, 
        chatbot_id,
        is_public = True
    )
    return bubble_setting_customize

@router.patch("/{organization_id}/setting/toggle-memory/chatbot/{chatbot_id}", status_code=status.HTTP_200_OK)
async def update_organization_chatbot_memory_status(
    organization_id: int,
    chatbot_id: int,
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(get_current_user) 
):
    chatbot = await toggle_chatbot_memory(
        db = db,
        organization_id = organization_id,
        chatbot_id = chatbot_id,
        current_user = current_user
    )
    return {
        "memory_status": chatbot.memory_status
    } 

@router.post("/{organization_id}/setting/memory/chatbot/{chatbot_id}", status_code=status.HTTP_200_OK)
async def cutomize_organization_chatbot_memeory(
    organization_id: int,
    chatbot_id: int,
    chatbot_memory: ChatbotMemory,
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(get_current_user) 
):
    if not chatbot_memory.text:
        raise HTTPException(status_code=422, detail=f"You must write something in your memory text to update")
    
    if len(chatbot_memory.text) > envs.PER_MEMORY_CHAR_LEN:
            raise HTTPException(status_code=422, detail=f"Maximum allowed character limit 300 has exceeded.")
    
    chatbot_update =  await create_organization_chatbot_memory(
        db, 
        organization_id,
        chatbot_id,
        chatbot_memory,
        current_user
    )
    # retun teh created chatbot emeory in format creator, text
    return chatbot_update

@router.patch("/{organization_id}/setting/update/memory/chatbot/{chatbot_id}", status_code=status.HTTP_200_OK)
async def update_organization_chatbot_memeory(
    organization_id: int,
    chatbot_id: int,
    chatbot_memory: ChatbotMemory,
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(get_current_user) 
):
    if not chatbot_memory.memory_id:
        raise HTTPException(status_code=422, detail=f"Complete information not provided")
    
    if len(chatbot_memory.text) > 310:
            raise HTTPException(status_code=422, detail=f"Maximum allowed character limit 300 has exceeded.")
    
    chatbot_update =  await update_organization_chatbot_memory(
        db,
        organization_id,
        chatbot_id,
        chatbot_memory,
        current_user
    )
    # retun teh created chatbot emeory in format creator, text
    return chatbot_update

@router.get("/{organization_id}/setting/memory/chatbot/{chatbot_id}", response_model=GetChatbotMemoryResponse)
async def get_organization_chatbot_memeory(
    organization_id: int,
    chatbot_id: int,
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(get_current_user) 
):
    chatbot_update =  await get_organization_chatbot_memory(
        db, 
        organization_id,
        chatbot_id,
        current_user
    )
    # retun teh created chatbot emeory in format creator, text
    return chatbot_update

@router.delete("/{organization_id}/setting/memory/{memory_id}/chatbot/{chatbot_id}", status_code=status.HTTP_200_OK)
async def get_organization_chatbot_memeory(
    organization_id: int,
    chatbot_id: int,
    memory_id: int,
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(get_current_user) 
):
    chatbot_update =  await delete_organization_chatbot_memory(
        db, 
        organization_id,
        chatbot_id,
        memory_id,
        current_user
    )
    # retun teh created chatbot emeory in format creator, text
    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={"message": "Chatbot memory has been deleted successfully."}
    ) 

@router.post("/{organization_id}/setting/suggestion/chatbot/{chatbot_id}", status_code=status.HTTP_200_OK)
async def cutomize_organization_chatbot_memeory(
    organization_id: int,
    chatbot_id: int,
    chatbot_suggestion: ChatbotSuggestion,
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(get_current_user) 
):
    chatbot_update =  await create_organization_chatbot_suggestion(
        db, 
        organization_id,
        chatbot_id,
        chatbot_suggestion,
        current_user
    )
    # retun teh created chatbot emeory in format creator, text
    return chatbot_update

@router.patch("/{organization_id}/setting/update/suggestion/chatbot/{chatbot_id}", status_code=status.HTTP_200_OK)
async def update_organization_chatbot_memeory(
    organization_id: int,
    chatbot_id: int,
    chatbot_suggestion: ChatbotSuggestionUpdate,
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(get_current_user) 
):
    chatbot_update =  await update_organization_chatbot_suggestion(
        db, 
        organization_id,
        chatbot_id,
        chatbot_suggestion,
        current_user
    )
    # retun teh created chatbot emeory in format creator, text
    return chatbot_update

@router.get("/setting/suggestion/public-chatbot/{chatbot_id}", response_model=List[ChatbotSuggestionResponse])
async def get_organization_chatbot_memeory(
    chatbot_id: str,
    db: AsyncSession = Depends(get_async_db),
):
    chatbot_update =  await get_public_chatbot_suggestion(
        db, 
        chatbot_id
    )
    # retun teh created chatbot emeory in format creator, text
    return chatbot_update

@router.get("/{organization_id}/setting/suggestion/chatbot/{chatbot_id}", response_model=List[ChatbotSuggestionResponse])
async def get_organization_chatbot_memeory(
    organization_id: int,
    chatbot_id: int,
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(get_current_user) 
):
    chatbot_update =  await get_organization_chatbot_suggestion(
        db, 
        organization_id,
        chatbot_id,
        current_user
    )
    # retun teh created chatbot emeory in format creator, text
    return chatbot_update

@router.delete("/{organization_id}/setting/suggestion/{suggestion_id}/chatbot/{chatbot_id}", status_code=status.HTTP_200_OK)
async def get_organization_chatbot_memeory(
    organization_id: int,
    chatbot_id: int,
    suggestion_id: int,
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(get_current_user) 
):
    chatbot_update =  await delete_organization_chatbot_suggestion(
        db, 
        organization_id,
        chatbot_id,
        suggestion_id,
        current_user
    )
    # retun teh created chatbot emeory in format creator, text
    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={"message": "Chatbot suggestion has been deleted successfully."}
    ) 

@router.get("/app/config", status_code=status.HTTP_200_OK)
async def get_organization_chatbot(
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(get_current_user)
):
    if current_user.role != UserRole.SUPER_ADMIN:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You are not authorized to perform this functionality."
        )
    image_llm_model = await get_organization_image_generation_chabot_name( db = db)
    chat_llm_model = await get_organization_chabot_name( db = db)
    return {
        "chat_model_name": chat_llm_model,
        "image_model_name":image_llm_model
    } 

@router.patch("/app/config", status_code=status.HTTP_200_OK)
async def update_organization_chatbot_text(
    llm_models: AppModels,
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(get_current_user) 
):
    # Manual Enum Validation
    if llm_models.image_model_name.model_name not in ImageGenerationLlmModelEnum.__members__.values():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid model name '{llm_models.image_model_name.model_name}'. Allowed values: {list(ImageGenerationLlmModelEnum.__members__.values())}"
        )
    if llm_models.chat_model_name.model_name not in LlmModelEnum.__members__.values():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid model name '{llm_models.chat_model_name.model_name}'. Allowed values: {list(LlmModelEnum.__members__.values())}"
        )
    if current_user.role != UserRole.SUPER_ADMIN:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"You are not autherized to perform this functionality."
        )
    if llm_models.image_model_name:
        image_chatbot_update =  await update_image_generation_organization_chatbot_model_name(
            db = db, 
            model_name = llm_models.image_model_name.model_name.value,
        )
    if llm_models.chat_model_name:
        chat_chatbot_update =  await update_organization_chatbot_model_name(
            db = db, 
            model_name = llm_models.chat_model_name.model_name.value,
        )

    return {
        "chat_model_name": llm_models.chat_model_name.model_name.value,
        "image_model_name":llm_models.image_model_name.model_name.value
    } 

@router.get("/app/allowed/models", status_code=status.HTTP_200_OK)
async def get_allowed_image_generation_llm_models():
    """
    API to fetch the allowed LLM models.
    """
    
    return {
        "allowed_image_models": [model.value for model in ImageGenerationLlmModelEnum],
        "allowed_chat_models": [model.value for model in LlmModelEnum]
        }

