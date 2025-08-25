from fastapi import APIRouter, Depends, status , HTTPException, BackgroundTasks
import time
from app.schemas.request.events import CreateEventRequest, UpdateEventRequest, EventFeedback, EmailRequest
from app.schemas.response.events import CreateEventResponse, SeenResponse, GetPaginatedRespose, EventFeedbackRespose, ReviewResponse, CaseMessageResponse
from app.services.envets import create_evnet, remove_event, mark_event_seen_status,  create_shared_url ,create_feedback, update_event, list_all_events,review_for_marti_website, review_for_marti_agent, fech_public_event_by_id, fech_event_by_id, list_all_user_events #get_case_by_event_id, add_case_message, get_case_by_id, delete_case, get_all_cases
from app.common.database_config import get_async_db
from sqlalchemy.ext.asyncio import AsyncSession
from app.common.database_config import get_async_db
from app.services.auth import get_current_user
from typing import List
from app.services import email
from app.services import notifications
from app.services.organization import get_s3_file_size
from urllib.parse import urlparse
from app.models.user import User, UserRole
from app.utils.database_helper import get_rbac_form_submission_by_ids
from app.utils.db_helpers import get_user_organization_admin
from app.services.envets import event_checks_admin_super_admin
from typing import Optional
# from app.models.organization import EventCase
from sqlalchemy import select
from datetime import datetime

events_router = APIRouter(
    prefix="/api/v1/events",
    tags=["Events"],
    # dependencies=[Depends(get_current_user)] 
)


@events_router.post('/{organization_id}', response_model=CreateEventResponse)
async def post_event(
        organization_id: int,
        event_info:CreateEventRequest,
        background_tasks: BackgroundTasks,
        db: AsyncSession = Depends(get_async_db),
        current_user: User = Depends(get_current_user)
        ):
    if current_user.email != event_info.Email:
        raise HTTPException(
            status_code=status.HTTP_203_NON_AUTHORITATIVE_INFORMATION,
            detail="You are not allowed to create event for other user"
        )
    if len(event_info.additional) > 600:
        raise HTTPException(
            status_code=status.HTTP_203_NON_AUTHORITATIVE_INFORMATION,
            detail="Your additional note exceeds the 600-character limit."
        )
    if not event_info.should_live_on_marti_agent and not event_info.should_live_on_marti_page:
        raise HTTPException(
            status_code=status.HTTP_203_NON_AUTHORITATIVE_INFORMATION,
            detail="You must select at least one option: either the MARTI agent or the MARTI website to host your event."
        )
    
    response_data_returned = await create_evnet(event_info, db, current_user,organization_id)
    # docs = []
    docs = [
        {"id": doc.id, "doc_name": doc.document_name, "doc_status": doc.status}
        for doc in response_data_returned.documents
    ]

    # for documents in response_data_returned.documents:
    #     for doc in documents:
    #         docs.append(
    #             {"id":doc.id, "doc_name":doc.document_name, "doc_status":doc.status}
    #         )
        
    response_created = CreateEventResponse(
        id = response_data_returned.id,
        organization_id=response_data_returned.organization_id,
        Email = response_data_returned.email,
        Name = response_data_returned.name,
        Building = response_data_returned.building,
        Department = response_data_returned.department,
        Title = response_data_returned.title,
        document_files = docs,
        should_live_on_marti_page = response_data_returned.should_live_on_marti_page,
        should_live_on_marti_agent=response_data_returned.should_live_on_marti_agent,
        additional = response_data_returned.additional,
        marti_website_review = response_data_returned.marti_website_review,
        marti_agent_review = response_data_returned.marti_agent_review,
        is_seen = response_data_returned.is_seen
    )

    #Send confirmation email to the person submitting an events
    background_tasks.add_task(
        email.send_confirmation_email_on_event_submission,
        response_data_returned.email,
        response_data_returned.name,
        response_data_returned.id
    )

    # create event announcements
    await notifications.create_event_announcement(response_data_returned.title, current_user)
    return response_created

@events_router.patch('/{organization_id}/{event_id}', response_model=CreateEventResponse)
async def patch_event(
    event_id: int,
    organization_id:int,
    update_data: UpdateEventRequest,
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(get_current_user)
):
    updated_event = await update_event(event_id, update_data, db,current_user,organization_id)

    docs = [
        {"id":doc.id, "doc_name": doc.document_name, "doc_status": doc.status}
        for doc in updated_event.documents
    ]
    
    return CreateEventResponse(
        organization_id=updated_event.organization_id,
        id=updated_event.id,
        Email=updated_event.email,
        Name=updated_event.name,
        Building=updated_event.building,
        Department=updated_event.department,
        Title=updated_event.title,
        document_files=docs,
        should_live_on_marti_page = updated_event.should_live_on_marti_page,
        should_live_on_marti_agent = updated_event.should_live_on_marti_agent,
        additional=updated_event.additional,
        marti_website_review = updated_event.marti_website_review,
        marti_agent_review = updated_event.marti_agent_review,
        user_response_to_review = updated_event.user_response_to_review,
        admin_event_review = updated_event.admin_event_review,
        is_seen = updated_event.is_seen
    )

#add date filter to fech by date.
@events_router.get('/{organization_id}/{user_id}/user-events', response_model=GetPaginatedRespose)
async def get_all_events(organization_id:int, user_id:int, skip: int = 0, limit: int = 10, db: AsyncSession = Depends(get_async_db), current_user: User = Depends(get_current_user)):
    events, events_count = await list_all_user_events(user_id, skip, limit, db, current_user,organization_id)
    event_responses = []
    for event in events:
        docs = [
            {"id":doc.id, "doc_name": doc.document_name, "doc_status": doc.status}
            for doc in event.documents
        ]
        total_size_in_kb = 0
        for doc in docs:
            # parsed_url = urlparse(doc["doc_name"])
            # file_name = parsed_url.path.split("/")[-1]
            
            file_size_kb = get_s3_file_size(doc["doc_name"])
            total_size_in_kb += file_size_kb
        event_responses.append(CreateEventResponse(
            organization_id=event.organization_id,
            id=event.id,
            Email=event.email,
            Name=event.name,
            Building=event.building,
            Department=event.department,
            Title=event.title,
            document_files=docs,
            should_live_on_marti_agent=event.should_live_on_marti_agent,
            should_live_on_marti_page=event.should_live_on_marti_page,
            additional=event.additional,
            marti_website_review = event.marti_website_review,
            marti_agent_review = event.marti_agent_review,
            is_rejected_marti_website = event.is_rejected_marti_website,
            is_rejected_marti_agent = event.is_rejected_marti_agent,
            user_response_to_review = event.user_response_to_review,
            admin_event_review = event.admin_event_review,
            is_seen = event.is_seen,
            total_size_in_kb = int(total_size_in_kb),
            allowed_size = 5000
        ))
    
    return GetPaginatedRespose(
        total_event_count=events_count,
        events=event_responses
    )
    
#add date filter to fech by date.
@events_router.get('/{organization_id}', response_model=GetPaginatedRespose)
async def get_all_events( organization_id:int, reviewed: Optional[bool] = None, skip: int = 0, limit: int = 10, db: AsyncSession = Depends(get_async_db), current_user: User = Depends(get_current_user)):
    events, events_count = await list_all_events(reviewed, skip, limit, db, current_user,organization_id)
    event_responses = []
    for event in events:
        docs = [
            {"id":doc.id, "doc_name": doc.document_name, "doc_status": doc.status}
            for doc in event.documents
        ]
        event_responses.append(CreateEventResponse(
            organization_id=event.organization_id,
            id=event.id,
            Email=event.email,
            Name=event.name,
            Building=event.building,
            Department=event.department,
            Title=event.title,
            document_files=docs,
            should_live_on_marti_agent=event.should_live_on_marti_agent,
            should_live_on_marti_page=event.should_live_on_marti_page,
            additional=event.additional,
            marti_website_review = event.marti_website_review,
            marti_agent_review = event.marti_agent_review,
            is_rejected_marti_website = event.is_rejected_marti_website,
            is_rejected_marti_agent = event.is_rejected_marti_agent,
            user_response_to_review = event.user_response_to_review,
            admin_event_review = event.admin_event_review,
            is_seen = event.is_seen
        ))
    
    return GetPaginatedRespose(
        total_event_count=events_count,
        events=event_responses
    )
    
@events_router.delete('/{organization_id}/{event_id}', status_code=200)
async def delete_event(organization_id:int, event_id: int, db: AsyncSession = Depends(get_async_db), current_user: User = Depends(get_current_user)):
    # Fetch the event
    await remove_event(event_id, db, organization_id, current_user)
    
    return {"details":"Deleted successful."}

#add date filter to fech by date.
@events_router.get('/{organization_id}/{event_id}/enent-view', response_model=CreateEventResponse)
async def get_all_events(
    organization_id:int,
    event_id: int, 
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(get_current_user)
    ):

    event = await fech_event_by_id(event_id=event_id, db=db,current_user = current_user,organization_id=organization_id)
    # event_responses = []
    docs = [
        {"id": doc.id, "doc_name": doc.document_name, "doc_status": doc.status}
        for doc in event.documents
    ]

    return CreateEventResponse(
            id=event.id,
            organization_id=event.organization_id,
            Email=event.email,
            Name=event.name,
            Building=event.building,
            Department=event.department,
            Title=event.title,
            document_files=docs,
            should_live_on_marti_agent=event.should_live_on_marti_agent,
            should_live_on_marti_page=event.should_live_on_marti_page, 
            additional=event.additional,
            marti_website_review = event.marti_website_review,
            marti_agent_review = event.marti_agent_review,
            is_rejected_marti_website = event.is_rejected_marti_website,
            is_rejected_marti_agent = event.is_rejected_marti_agent,
            user_response_to_review = event.user_response_to_review,
            admin_event_review = event.admin_event_review,
            is_seen = event.is_seen
        )
    
    # return event_responses

@events_router.patch('/{organization_id}/{event_id}/review', status_code=status.HTTP_200_OK, response_model=List[ReviewResponse])
async def patch_event_review(
    organization_id:int,
    event_id: int,
    background_tasks: BackgroundTasks,
    agent_review: Optional[bool] = None,
    website_review: Optional[bool] = None,
    is_rejected_marti_website: Optional[bool] = None,
    is_rejected_marti_agent: Optional[bool] = None,
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(get_current_user)
):
    # if agent_review == website_review:
    #     HTTPException(
    #         status_code=status.HTTP_203_NON_AUTHORITATIVE_INFORMATION,
    #         detail="Operation not allowed"
    #     )
    
    event = await fech_event_by_id(event_id=event_id, db=db,organization_id = organization_id, current_user = current_user)
    if is_rejected_marti_website and website_review and event.is_rejected_marti_website:
        raise HTTPException(
            status_code=status.HTTP_203_NON_AUTHORITATIVE_INFORMATION,
            detail="This event is rejected for marti website"
        )
    if is_rejected_marti_agent and agent_review and event.is_rejected_marti_agent:
        raise HTTPException(
            status_code=status.HTTP_203_NON_AUTHORITATIVE_INFORMATION,
            detail="This event is rejected for marti agent"
        )
    if event.user_response_to_review:
        raise HTTPException(
            status_code=status.HTTP_203_NON_AUTHORITATIVE_INFORMATION,
            detail="User has not responded to the review."
        )
    page_flag = None
    agent_flag = None
    response = []
    is_rejected_marti_website_flag = event.is_rejected_marti_website
    is_rejected_marti_agent_flag = event.is_rejected_marti_agent
    if is_rejected_marti_website:
        event.is_rejected_marti_website = True
        db.add(event)
        is_rejected_marti_website_flag = True
        background_tasks.add_task(
            email.event_rejection_email,
            event.email,
            event.name,
            marti_page = False,
            marti_agent = True
        )
        
        # await db.refresh(event)
    if is_rejected_marti_agent:
        event.is_rejected_marti_agent = True
        db.add(event)
        is_rejected_marti_agent_flag = True
        background_tasks.add_task(
            email.event_rejection_email,
            event.email,
            event.name,
            marti_page = False,
            marti_agent = True
        )
        # await db.commit()
        # await db.refresh(event)
    if event.admin_event_review:
        event.admin_event_review = False
        db.add(event)
    await db.commit()
    if agent_review is not None:
        if not event.should_live_on_marti_agent:
            raise HTTPException(
                status_code=status.HTTP_203_NON_AUTHORITATIVE_INFORMATION,
                detail='This event is not allowed to publish on external agent.'
            )
        if event.marti_agent_review:
            raise HTTPException(
                status_code=status.HTTP_203_NON_AUTHORITATIVE_INFORMATION,
                detail="Already published on marti system"
            )
        updated_event = await review_for_marti_agent(event_id=event_id,db=db,current_user=current_user,organization_id=organization_id)
        #Send confirmation email to the person submitting an events
        background_tasks.add_task(
            email.send_event_live_notification,
            updated_event.email,
            updated_event.name,
            marti_page = False,
            marti_agent = True
        )
        agent_flag = updated_event.marti_agent_review
    if website_review is not None:
        if not event.should_live_on_marti_page:
            raise HTTPException(
                status_code=status.HTTP_203_NON_AUTHORITATIVE_INFORMATION,
                detail='This event is not allowed to publish on public website.'
            )
        if event.marti_website_review:
            raise HTTPException(
                status_code=status.HTTP_203_NON_AUTHORITATIVE_INFORMATION,
                detail="Already published on marti page"
            )
        updated_event = await review_for_marti_website(event_id=event_id,db=db,current_user=current_user,organization_id=organization_id)
        #Send confirmation email to the person submitting an events
        background_tasks.add_task(
            email.send_event_live_notification,
            updated_event.email,
            updated_event.name,
            marti_page = True,
            marti_agent = False
        )
        page_flag = updated_event.marti_website_review
    
    response.append(
        ReviewResponse(
            agent_review = agent_flag,
            website_review = page_flag,
            is_rejected_marti_website = is_rejected_marti_website_flag,
            is_rejected_marti_agent = is_rejected_marti_agent_flag
        ))
    return response

@events_router.post('/{organization_id}/feedback', response_model=EventFeedbackRespose)
async def post_event(
        organization_id:int,
        event_feedback:EventFeedback,
        db: AsyncSession = Depends(get_async_db),
        current_user: User = Depends(get_current_user)
        ):
    response_data_returned = await create_feedback(event_feedback, db,current_user, organization_id)
    # docs = []
    
    response_created = EventFeedbackRespose(
        feedback_id = response_data_returned.id,
        feedback = response_data_returned.user_feedback,
        event_id = response_data_returned.event_id
    )
    return response_created

@events_router.post("/{organization_id}/send-email/{event_id}", status_code=status.HTTP_200_OK)
async def send_incomplete_content_email(
    organization_id:int,
    event_id: int,
    email_data: EmailRequest,
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(get_current_user)
):
    event_data = await fech_event_by_id(
        event_id=event_id,
        db = db,
        current_user = current_user,
        organization_id=organization_id
    )
    if current_user.role == UserRole.USER:
        from app.utils.database_helper import format_user_chatbot_permissions
        _, form_submission = await format_user_chatbot_permissions(db,current_user.organization_id, current_user.group_ids)
        if not form_submission:
            HTTPException(
            status_code=status.HTTP_203_NON_AUTHORITATIVE_INFORMATION,
            detail="Operation not allowed"
        )

        event_data.user_response_to_review = True
        event_data.admin_event_review = False
        db.add(event_data)
        user_data = await get_user_organization_admin(db = db, organization_id = organization_id)
        await email.send_incomplete_envent_info(
            email = event_data.email,
            name = event_data.name,
            email_message = email_data.email_message,
            evnet_title = event_data.title
        )
        
    else:
        event_data.user_response_to_review = True
        event_data.admin_event_review = False
        await email.send_incomplete_envent_info(
            email = event_data.email,
            name = event_data.name,
            email_message = email_data.email_message,
            evnet_title = event_data.title
        )
        db.add(event_data)
    
    # event_data.user_response_to_review = True
    # db.add(event_data)
    await db.commit()
    _ = await mark_event_seen_status(event_id, organization_id, False, db, current_user)

    return {"details":"Email send succefully"}

@events_router.patch('/{organization_id}/{event_id}/seen-status', response_model=SeenResponse)
async def update_seen_status(
    event_id: int,
    organization_id: int,
    is_seen: bool,
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(get_current_user)
):
    updated_event = await mark_event_seen_status(event_id, organization_id, is_seen, db, current_user)

    # docs = [
    #     {"id": doc.id, "doc_name": doc.document_name, "doc_status": doc.status}
    #     for doc in updated_event.documents
    # ]

    return SeenResponse(
        is_seen=updated_event.is_seen
    )

# admin_event_review --> final settig will be either be delete or approve.

# @events_router.post("/{organization_id}/{event_id}/generate-event-link", status_code=status.HTTP_200_OK)
# async def generate_public_event_link(
#     organization_id: int,  
#     event_id: int,
#     db: AsyncSession = Depends(get_async_db),
#     current_user: User = Depends(get_current_user)
# ):
#     return await create_shared_url(db,current_user,organization_id,event_id)


# from app.services.envets import create_case_for_event
# @events_router.get('/{organization_id}/{event_id}/enent-user-view', response_model=CreateEventResponse)
# async def get_all_events(
#     organization_id:int,
#     event_id: int, 
#     db: AsyncSession = Depends(get_async_db),
#     current_user: User = Depends(get_current_user)
#     ):

#     event = await fech_public_event_by_id(event_id=event_id, db=db, organization_id=organization_id)
#     # event_responses = []
#     docs = [
#         {"id": doc.id, "doc_name": doc.document_name, "doc_status": doc.status}
#         for doc in event.documents
#     ]

#     return CreateEventResponse(
#             id=event.id,
#             organization_id=event.organization_id,
#             Email=event.email,
#             Name=event.name,
#             Building=event.building,
#             Department=event.department,
#             Title=event.title,
#             document_files=docs,
#             should_live_on_marti_agent=event.should_live_on_marti_agent,
#             should_live_on_marti_page=event.should_live_on_marti_page, 
#             additional=event.additional,
#             marti_website_review = event.marti_website_review,
#             marti_agent_review = event.marti_agent_review,
#             is_rejected_marti_website = event.is_rejected_marti_website,
#             is_rejected_marti_agent = event.is_rejected_marti_agent
#         )

# @events_router.post("/{organization_id}/{event_id}/create-case", status_code=status.HTTP_200_OK, response_model=CreateCaseResponse)
# async def create_case(
#     organization_id:int,
#     event_id: int,
#     case_title: str,
#     case_message: str,
#     db: AsyncSession = Depends(get_async_db),
#     current_user: User = Depends(get_current_user)
# ):
#     if current_user.role == UserRole.USER:  
#         falg_for_form = await get_rbac_form_submission_by_ids(db, organization_id, current_user.group_ids)
#         if not falg_for_form:
#             raise HTTPException(
#                 status_code=status.HTTP_203_NON_AUTHORITATIVE_INFORMATION,
#                 detail="Unautherized operation"
#             )
#     else:
#         await event_checks_admin_super_admin(current_user, organization_id=organization_id)
#     event = await fech_event_by_id(event_id=event_id, db=db, organization_id=organization_id, current_user=current_user)
#     # if user do not want to publish on marti agent or marti page then he/admin can not create case

#     if not event.should_live_on_marti_agent or not event.should_live_on_marti_page:
#         raise HTTPException(
#             status_code=status.HTTP_203_NON_AUTHORITATIVE_INFORMATION,
#             detail="This event is not allowed to create case for"
#         )
#     # already_case_exists = await db.execute(select(EventCase).where(EventCase.event_id == event_id))
#     result = await db.execute(select(EventCase).where(EventCase.event_id == event_id).where(EventCase.organization_id == organization_id))
#     case = result.scalar_one_or_none()
#     if case:
#         raise HTTPException(
#             status_code=status.HTTP_203_NON_AUTHORITATIVE_INFORMATION,
#             detail="Case already exists"
#         )
#     case = await create_case_for_event(db,current_user,organization_id,event_id,case_title, case_message=case_message)
    
#     case_messages = []
#     # print(f'case messages{case.messages}')
#     for message in case.messages:
#         case_messages.append(CaseMessageResponse(
#             message=message.message,
#             timestamp=str(message.timestamp),
#             is_user_message=message.is_user_message
#         ))
#     await email.send_incomplete_envent_info(
#         email = event.email,
#         name = event.name,
#         email_message = case_message,
#         case_title = case.case_title
#     )
#     return CreateCaseResponse(
#         case_id=case.id,
#         case_status=case.status,
#         organization_id=case.organization_id,
#         event_id=case.event_id,
#         created_email=case.created_email,
#         case_title=case.case_title,
#         case_messages=case_messages
#     )

# @events_router.get("/{organization_id}/{event_id}/get-case", status_code=status.HTTP_200_OK, response_model=CreateCaseResponse)
# async def get_case(
#     organization_id:int,
#     event_id: int,
#     db: AsyncSession = Depends(get_async_db),
#     current_user: User = Depends(get_current_user)
# ):
#     case = await get_case_by_event_id(db,organization_id,event_id)
#     case_messages = []
#     for message in case.messages:
#         case_messages.append(CaseMessageResponse(
#             message=message.message,
#             timestamp=str(message.timestamp),
#             is_user_message=message.is_user_message
#         ))
#     return CreateCaseResponse(
#         case_id=case.id,
#         case_status=case.status,
#         organization_id=case.organization_id,
#         case_title=case.case_title,
#         event_id=case.event_id,
#         created_email=case.created_email,
#         case_messages=case_messages
#     )

# @events_router.post("/{organization_id}/{case_id}/{event_id}/add-case-message", status_code=status.HTTP_200_OK, response_model=CreateCaseResponse)
# async def add_case_message_to_case(
#     organization_id:int,
#     case_id: int,
#     event_id: int,
#     case_message: str,
#     db: AsyncSession = Depends(get_async_db),
#     current_user: User = Depends(get_current_user)
# ):
#     case = await get_case_by_id(db,organization_id,case_id)
#     if case.event_id != event_id:
#         raise HTTPException(
#             status_code=status.HTTP_203_NON_AUTHORITATIVE_INFORMATION,
#             detail="Case not found"
#         )
#     if current_user.role == UserRole.USER:
#         _ = await add_case_message(db,case.id,case_message,True)
#     else:
#         _ = await add_case_message(db,case.id,case_message,False)
#     updated_case = await get_case_by_event_id(db,organization_id,event_id)
#     case_messages = []
#     for message in updated_case.messages:
#         case_messages.append(CaseMessageResponse(
#             message=message.message,
#             timestamp=str(message.timestamp),
#             is_user_message=message.is_user_message
#         ))
#     case_messages.append(CaseMessageResponse(
#         message=case_message,
#         timestamp=str(datetime.now()),
#         is_user_message=False
#     ))
#     return CreateCaseResponse(
#         case_id=updated_case.id,
#         case_status=updated_case.status,
#         organization_id=updated_case.organization_id,
#         event_id=updated_case.event_id,
#         case_title=updated_case.case_title,
#         created_email=updated_case.created_email,
#         case_messages=case_messages
#     )

# @events_router.delete("/{organization_id}/{case_id}/{event_id}/delete-case", status_code=status.HTTP_200_OK)
# async def can_delete_case(
#     organization_id:int,
#     case_id: int,
#     event_id: int,
#     db: AsyncSession = Depends(get_async_db),
#     current_user: User = Depends(get_current_user)
# ):
#     case = await get_case_by_id(db,organization_id,case_id)
#     if not case:
#         raise HTTPException(
#             status_code=status.HTTP_203_NON_AUTHORITATIVE_INFORMATION,
#             detail="Case not found"
#         )
#     if case.event_id != event_id:
#         raise HTTPException(
#             status_code=status.HTTP_203_NON_AUTHORITATIVE_INFORMATION,
#             detail="Case not found"
#         )
#     await delete_case(db,organization_id,case_id)
#     return {"details":"Case deleted successfully"}

# @events_router.get("/{organization_id}/get-all-cases", status_code=status.HTTP_200_OK, response_model=List[CreateCaseResponse])
# async def can_get_all_cases(
#     organization_id:int,
#     db: AsyncSession = Depends(get_async_db),
#     current_user: User = Depends(get_current_user)
# ):
#     # get all cases for an organization
#     cases = await get_all_cases(db,organization_id)
#     case_responses = []
#     for case in cases:
#         case_responses.append(CreateCaseResponse(
#             case_id=case.id,
#             case_status=case.status,
#             organization_id=case.organization_id,
#             event_id=case.event_id,
#             created_email=case.created_email,
#             case_title=case.case_title,
#             case_messages=[]
#         ))
#     return case_responses

