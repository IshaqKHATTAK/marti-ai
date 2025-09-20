
from fastapi import APIRouter, Depends, status , HTTPException
import time
from app.schemas.request.landing import LandingRequest,LandingEmailRequest, FaqsUpdationRequest,FaqsCreationRequest, LandingUpdateRequest, DeleteRequest
from app.common.database_config import get_async_db

from fastapi import APIRouter, Depends, status , HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from app.services import email
from app.common.database_config import get_async_db
from app.schemas.response.landing import LandingCreateResponse, LandingGetResponse, FaqsCreationResponse
from app.services.landing import faqs_delete_content, faqs_update_content,get_faqs_content,faqs_create_content, create_landing_content, delete_landing_content, get_landing_content, update_landing_content, get_all_landing_items
from app.services.auth import get_current_user
from typing import List

landing_router = APIRouter(
    prefix="/api/v1/landing",
    tags=["landing"],
    # dependencies=[Depends(get_current_user)]
)

faqs_router = APIRouter(
    prefix="/api/v1/landing",
    tags=["landing"],
    dependencies=[Depends(get_current_user)]
)

@landing_router.post("/{identifier}/",  response_model=LandingCreateResponse)
async def landing_content_creation(
    identifier: int,
    landing_request: LandingRequest,
    db: AsyncSession = Depends(get_async_db),
):
    if not landing_request.title or not landing_request.description or not landing_request.link:
        raise HTTPException(status_code=404, detail="Provide complete infromation.")
    created_announcement =  await create_landing_content(
        identifier,
        landing_request,
        db, 
    )
    response = LandingCreateResponse(
        id=created_announcement.id,
        title = created_announcement.title,
        description=created_announcement.description,
        link=created_announcement.link,
        identifier = identifier
    )
    # retun teh created chatbot emeory in format creator, text
    return response


@landing_router.get("/{identifier}/",  response_model=LandingGetResponse)
async def landing_content_getting(
    identifier: int,
    db: AsyncSession = Depends(get_async_db),
):
    get_landing_items =  await get_landing_content(
        identifier,
        db, 
    )
    response = LandingGetResponse(
        items=get_landing_items,
        identifier = identifier
    )
    # retun teh created chatbot emeory in format creator, text
    return response


@landing_router.patch("/{identifier}", response_model=LandingCreateResponse)
async def landing_content_updating(
    identifier: int,
    update_request: LandingUpdateRequest,
    db: AsyncSession = Depends(get_async_db),
):
    created_announcement =  await update_landing_content(
        identifier,
        update_request,
        db, 
    )
    response = LandingCreateResponse(
        id=created_announcement.id,
        title = created_announcement.title,
        description=created_announcement.description,
        link=created_announcement.link,
        identifier = identifier
    )
    # retun teh created chatbot emeory in format creator, text
    return response

@landing_router.delete("/{identifier}", status_code=status.HTTP_200_OK)
async def landing_content_deleting(
    identifier: int,
    id: DeleteRequest,
    db: AsyncSession = Depends(get_async_db),
):
    response =  await delete_landing_content(
        identifier,
        id,
        db, 
    )
    return {"message": "Record deleted successfully"}

from app.models.user import User, UserRole
@faqs_router.post("/FAQ",  response_model=FaqsCreationResponse)
async def FAQ_content_creation(
    faqs_data: FaqsCreationRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    if current_user.role != UserRole.SUPER_ADMIN:
        raise HTTPException(status_code=404, detail="You are not autherized to create FAQs.")
    if not faqs_data.question or not faqs_data.answer:
        raise HTTPException(status_code=404, detail="Provide complete infromation.")
    created_faqs =  await faqs_create_content(
        faqs_data,
        db
    )
    response = FaqsCreationResponse(
        question=created_faqs.question,
        answer = created_faqs.answer,  
        id = created_faqs.id 
    )
    # retun teh created chatbot emeory in format creator, text
    return response


@faqs_router.get("/FAQ",  response_model=List[FaqsCreationResponse])
async def get_all_faqs(db: AsyncSession = Depends(get_async_db)):
    results = await get_faqs_content(db=db)
    return [FaqsCreationResponse(id=faq.id, question=faq.question, answer=faq.answer) for faq in results]
    
@faqs_router.patch("/FAQ", response_model=FaqsCreationResponse)
async def update_faqs(
    update_request: FaqsUpdationRequest,
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(get_current_user),
):
    if current_user.role != UserRole.SUPER_ADMIN:
        raise HTTPException(status_code=404, detail="You are not autherized to update FAQs.")
    
    updated_faqs =  await faqs_update_content(
        update_request,
        db, 
    )
    return FaqsCreationResponse(
        id=updated_faqs.id,
        question=updated_faqs.question,
        answer=updated_faqs.answer
    )

@faqs_router.delete("/FAQ", status_code=status.HTTP_200_OK)
async def delete_faqs(
    id: DeleteRequest,
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(get_current_user),
):
    if current_user.role != UserRole.SUPER_ADMIN:
        raise HTTPException(status_code=404, detail="You are not autherized to delete FAQs.")
    
    response =  await faqs_delete_content(
        id.id,
        db, 
    )
    return {"message": "Record deleted successfully"}


@landing_router.get("/all",  response_model=list[LandingGetResponse])
async def all_landing_content_getting(
    db: AsyncSession = Depends(get_async_db),
):
    get_all_items = await get_all_landing_items(db)
    
    # retun teh created chatbot emeory in format creator, text
    return get_all_items


@landing_router.post("/send-landing-email", status_code=status.HTTP_200_OK)
async def send_incomplete_content_email(
    email_data:LandingEmailRequest
):
    await email.send_landing_emails(
        email = "awayne@askmarti.com",
        name = email_data.name,
        email_message = email_data.message,
        person_email = email_data.email,
        cc_email = "ndae08@gmail.com"
    )
    return {"details":"Email has been sent successfully"}
