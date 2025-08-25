from app.schemas.request.events import CreateEventRequest, UpdateEventRequest, EventFeedback
from app.schemas.response.events import CreateEventResponse
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.organization import Event,EventDocument,EventFeedBack #EventCase,CaseStatus,CaseMessage
from fastapi import HTTPException, status
from sqlalchemy import select
from botocore.exceptions import ClientError
from sqlalchemy.orm import selectinload
from urllib.parse import urlparse
import os
from app.utils.database_helper import get_rbac_groups_by_org_id,get_rbac_form_submission_by_ids
from app.models.user import User, UserRole
from app.common.env_config import get_envs_setting
from app.services.user_chat import s3_client, fernet

async def _encrypt_custom(id_to_ecript: str, fernet) -> str:
    """Encrypts the chatbot ID."""
    try:
        return fernet.encrypt(str(id_to_ecript).encode()).decode()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Encryption failed: {str(e)}")

async def _decrypt_custom(encrypted_id: str, fernet) -> str:
    """Decrypts the chatbot ID."""
    try:
        return fernet.decrypt(encrypted_id.encode()).decode()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid chatbot ID")
        

envs = get_envs_setting()
async def event_checks_admin_super_admin(current_user, organization_id):
    if current_user.role == UserRole.ADMIN and organization_id != current_user.organization_id:
        raise HTTPException(status_code=403, detail="You can only modify details of your organization.")                         #operation -- Operation is what user want to do with chatbot
    return

async def create_feedback(feedback_data:EventFeedback, db:AsyncSession, current_user, organization_id):
    # if current_user.role == UserRole.USER:
    #     falg_for_form = await get_rbac_form_submission_by_ids(db, organization_id, current_user.group_ids)
    #     if not falg_for_form:
    #         raise HTTPException(
    #             status_code=status.HTTP_203_NON_AUTHORITATIVE_INFORMATION,
    #             detail="Unautherized operation"
    #         )
    # else:
    #     await event_checks_admin_super_admin(current_user, organization_id=organization_id)
    
    feedback_data = await add_feedback(feedback_data=feedback_data, db=db)
    return feedback_data


async def mark_event_seen_status(
    event_id: int,
    organization_id: int,
    is_seen: bool,
    db: AsyncSession,
    current_user
):
    if current_user.role == UserRole.USER:
            falg_for_form = await get_rbac_form_submission_by_ids(db, organization_id, current_user.group_ids)
            if not falg_for_form:
                raise HTTPException(
                    status_code=status.HTTP_203_NON_AUTHORITATIVE_INFORMATION,
                    detail="Unautherized operation"
                )
    else:
        await event_checks_admin_super_admin(current_user, organization_id=organization_id)
        
    result = await db.execute(
        select(Event)
        .options(selectinload(Event.documents))
        .where(Event.id == event_id)
        .where(Event.organization_id == organization_id)
    )
    event = result.scalar_one_or_none()

    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    event.is_seen = is_seen
    db.add(event)
    await db.commit()
    await db.refresh(event)

    return event


async def create_evnet(event_info:CreateEventRequest, db:AsyncSession, current_user: User,organization_id: int):
    # if current_user.role == UserRole.USER:
    #     falg_for_form = await get_rbac_form_submission_by_ids(db, organization_id, current_user.group_ids)
    #     if not falg_for_form:
    #         raise HTTPException(
    #             status_code=status.HTTP_203_NON_AUTHORITATIVE_INFORMATION,
    #             detail="Unautherized operation"
    #         )
    # else:
    #     await event_checks_admin_super_admin(current_user, organization_id=organization_id)
    
    response = await add_event(event_info, db, organization_id)
    return response


async def remove_event(event_id, db, organization_id, current_user):
    # if current_user.role == UserRole.USER:
    #     falg_for_form = await get_rbac_form_submission_by_ids(db, organization_id, current_user.group_ids)
    #     if not falg_for_form:
    #         raise HTTPException(
    #             status_code=status.HTTP_203_NON_AUTHORITATIVE_INFORMATION,
    #             detail="Unautherized operation"
    #         )
    # else:
    #     await event_checks_admin_super_admin(current_user, organization_id=organization_id)
    
    await  pop_event(event_id=event_id, db=db,organization_id=organization_id)
    return

async def review_for_marti_agent(event_id: int, db: AsyncSession,current_user,organization_id):
    if current_user.role == UserRole.USER:
        falg_for_form = await get_rbac_form_submission_by_ids(db, organization_id, current_user.group_ids)
        if not falg_for_form:
            raise HTTPException(
                status_code=status.HTTP_203_NON_AUTHORITATIVE_INFORMATION,
                detail="Unautherized operation"
            )
    else:
        await event_checks_admin_super_admin(current_user, organization_id=organization_id)
    
    result = await db.execute(select(Event).where(Event.id == event_id).where(Event.organization_id == organization_id))
    event = result.scalar_one_or_none()
    if not event:
        raise HTTPException(status_code=404, detail=f"Event not found")
    event.marti_agent_review = not event.marti_agent_review

    # Prepare S3 keys
    # org_id = event.organization_id
    # if event.documents:
    #     for doc in event.documents:
    #         # Prepare S3 keys
    #         org_id = event.organization_id
    #         bot_id = event.organization.bot_id if hasattr(event.organization, "bot_id") else "unknown_bot"  # Adjust as needed
    #         file_name = doc.document_name
    #         old_key = f"pending_events/{org_id}/external/{file_name}"
    #         new_key = f"uploaded_file_doc/{org_id}/{bot_id}/{file_name}"
    #         try:
    #             copy_source = {'Bucket': envs.BUCKET_NAME, 'Key': old_key}
    #             s3_client.copy_object(
    #                 Bucket=envs.BUCKET_NAME,
    #                 CopySource=copy_source,
    #                 Key=new_key
    #             )
    #             s3_client.delete_object(Bucket=envs.BUCKET_NAME, Key=old_key)
    #         except ClientError as e:
    #             raise HTTPException(status_code=500, detail=f"Failed to move file in S3: {str(e)}")
    #         doc.document_name = new_key
    #         doc.status = "Completed"
    
    await db.commit()
    await db.refresh(event)
    return event

async def review_for_marti_website(event_id: int, db: AsyncSession, current_user, organization_id):
    if current_user.role == UserRole.USER:
        falg_for_form = await get_rbac_form_submission_by_ids(db, organization_id, current_user.group_ids)
        if not falg_for_form:
            raise HTTPException(
                status_code=status.HTTP_203_NON_AUTHORITATIVE_INFORMATION,
                detail="Unautherized operation"
            )
    else:
        await event_checks_admin_super_admin(current_user, organization_id=organization_id)
    
    result = await db.execute(select(Event).where(Event.id == event_id).where(Event.organization_id == organization_id))
    event = result.scalar_one_or_none()
    if not event:
        raise HTTPException(status_code=404, detail=f"Event not found")
    event.marti_website_review = not event.marti_website_review
    await db.commit()
    await db.refresh(event)
    return event
    
async def update_event(event_id: int, data: UpdateEventRequest, db: AsyncSession, current_user, organization_id):
    # Eagerly load the event along with its documents before accessing them
    # if current_user.role == UserRole.USER:
    #     falg_for_form = await get_rbac_form_submission_by_ids(db, organization_id, current_user.group_ids)
    #     if not falg_for_form:
    #         raise HTTPException(
    #             status_code=status.HTTP_203_NON_AUTHORITATIVE_INFORMATION,
    #             detail="Unautherized operation"
    #         )
    # else:
    #     await event_checks_admin_super_admin(current_user, organization_id=organization_id)
    
    
    result = await db.execute(
        select(Event)
        .options(selectinload(Event.documents))
        .where(Event.id == event_id)
        .where(Event.organization_id == organization_id)
    )
    event = result.scalar_one_or_none()
    if not event:
        raise HTTPException(status_code=404, detail=f"Event not found")
    # Update only fields provided
    if data.Name is not None:
        event.name = data.Name
    if data.Building is not None:
        event.building = data.Building
    if data.Department is not None:
        event.department = data.Department
    if data.Title is not None:
        event.title = data.Title
    if data.should_live_on_marti_agent is not None:
        event.should_live_on_marti_agent = data.should_live_on_marti_agent
    if data.should_live_on_marti_page is not None:
        event.should_live_on_marti_page = data.should_live_on_marti_agent
    if data.additional is not None:
        event.additional = data.additional
    if data.should_live_on_marti_agent is not None:
        event.should_live_on_marti_agent = data.should_live_on_marti_agent
    if data.should_live_on_marti_page is not None:
        event.should_live_on_marti_page = data.should_live_on_marti_page
    
    event.is_rejected_marti_agent = False
    event.is_rejected_marti_website = False
    s3_keys = []
    if data.delete_document_files is not None:
        for doc in event.documents:
            if doc.id in data.delete_document_files:
                if doc.document_name:
                    parsed_url = urlparse(doc.document_name)
                    s3_key = parsed_url.path.lstrip("/")  # Extract S3 key
                    s3_keys.append(s3_key)

                await db.delete(doc)
        for key in s3_keys:
            try:
                s3_client.delete_object(Bucket=envs.BUCKET_NAME, Key=key)
                print(f"🗑️ Deleted S3 object: {key}")
            except Exception as e:
                print(f"⚠️ Failed to delete S3 object {key}: {str(e)}")
            
    # Replace documents if provided
    if data.document_files is not None:

        for file_name in data.document_files:
            # doc = EventDocument(
            #     event_id=event.id,
            #     document_name=file_name,
            #     content_type="application/octet-stream",
            #     status="Updated"
            # )
            file_extension = os.path.splitext(file_name)[1][1:]
            #https://marti-dev-file-upload-bucket.s3.us-east-2.amazonaws.com/uploaded_file_doc/1/0/RobertMarshallProfile.pdf
            
            doc = EventDocument(
                event_id=event.id,
                document_name=file_name,
                content_type=file_extension,  # You can update this
                status="Uploaded"
            )
            db.add(doc)
    if event.user_response_to_review:
        from app.services.email import send_event_update_notification_to_admin
        from app.utils.db_helpers import get_user_organization_admin
        event.admin_event_review = True
        event.user_response_to_review = False
        db.add(event)
        
        user_data = await get_user_organization_admin(db = db, organization_id = organization_id)
        await send_event_update_notification_to_admin(
            admin_email = user_data.email,
            user_name = event.name,
            user_email = event.email,
            evnet_title = event.title,
            user_message = "."
        )
    await db.commit()
    await db.refresh(event)

    # Eager load the documents
    result = await db.execute(
        select(Event).options(selectinload(Event.documents)).where(Event.id == event.id)
    )
    event = result.scalar_one()
    return event

async def pop_event(event_id, db,organization_id):
    result = await db.execute(
        select(Event)
        .options(selectinload(Event.documents))
        .where(Event.id == event_id)
        .where(Event.organization_id == organization_id)
    )
    event = result.scalar_one_or_none()
    if not event:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Event not found"
        )
    
    # result = await db.execute(select(Event).where(Event.id == event_id).where(Event.organization_id == organization_id))
    # event = result.scalar_one_or_none()
    # if not event:
    #     raise HTTPException(
    #         status_code=status.HTTP_404_NOT_FOUND,
    #         detail=f"Event not found"
    #     )
    
    s3_keys = []
    for doc in event.documents:
        if doc.document_name:
            parsed_url = urlparse(doc.document_name)
            s3_key = parsed_url.path.lstrip("/")  # Extract S3 key
            s3_keys.append(s3_key)

    await db.delete(event)
    await db.commit()

    for key in s3_keys:
        try:
            s3_client.delete_object(Bucket=envs.BUCKET_NAME, Key=key)
            print(f"🗑️ Deleted S3 object: {key}")
        except Exception as e:
            print(f"⚠️ Failed to delete S3 object {key}: {str(e)}")
        
    # await db.delete(event)
    # await db.commit()
    return 


async def list_all_user_events(user_id, skip, limit, db: AsyncSession, current_user, organization_id):
    # Eagerly load the event along with its documents before accessing them
    if current_user.id != user_id and current_user.role != UserRole.USER:
        raise HTTPException(
            status_code=status.HTTP_203_NON_AUTHORITATIVE_INFORMATION,
            detail="Unautherized operation"
        )
    if current_user.id != user_id and current_user.role == UserRole.USER:
        raise HTTPException(
            status_code=status.HTTP_203_NON_AUTHORITATIVE_INFORMATION,
            detail="Unautherized operation"
        )
    result = await db.execute(select(Event).where(Event.organization_id == organization_id).where(Event.email == current_user.email))
    total_event = result.scalars().all()

    # Load paginated events with eager loading of documents
    page_result = await db.execute(
        select(Event)
        .options(selectinload(Event.documents))
        .where(Event.organization_id == organization_id)
        .where(Event.email == current_user.email)
        .offset(skip)
        .limit(limit)
    )
    events = page_result.scalars().all()

    return events, len(total_event)



async def list_all_events(reviewed, skip, limit, db: AsyncSession, current_user, organization_id):
    # Eagerly load the event along with its documents before accessing them
    if current_user.role != UserRole.ADMIN:  
        falg_for_form = await get_rbac_form_submission_by_ids(db, organization_id, current_user.group_ids)
        if not falg_for_form:
            raise HTTPException(
                status_code=status.HTTP_203_NON_AUTHORITATIVE_INFORMATION,
                detail="Unautherized operation"
            )
    else:
        await event_checks_admin_super_admin(current_user, organization_id=organization_id)
    # result = await db.execute(select(Event).where(Event.organization_id == organization_id))
    
    # Build base query
    query = select(Event).where(Event.organization_id == organization_id)
    if reviewed is not None:
        query = query.where(Event.is_seen == reviewed).where(Event.is_seen == reviewed)
   
    # Get total count
    result = await db.execute(query)
    total_event = result.scalars().all()

    # Load paginated events with eager loading of documents
    # Start building the query
    query = select(Event).options(selectinload(Event.documents)).where(
        Event.organization_id == organization_id
    )

    # if is_website_reviewed is not None:
    #     query = query.where(Event.marti_website_review == is_website_reviewed)
    # if is_agent_reviewed is not None:
    #     query = query.where(Event.marti_agent_review == is_agent_reviewed)
    if reviewed is not None:
        query = query.where(Event.is_seen == reviewed).where(Event.is_seen == reviewed)

    # Apply pagination
    query = query.offset(skip).limit(limit)

    # Execute the final query
    page_result = await db.execute(query)
    events = page_result.scalars().all()

    # page_result = await db.execute(query)

    # if is_website_reviewed is not None:
    #     page_result = page_result.where(Event.marti_website_review == is_website_reviewed)
    # if is_agent_reviewed is not None:
    #     page_result = page_result.where(Event.marti_agent_review == is_agent_reviewed)
    # events = page_result.scalars().all()

    return events, len(total_event)

async def add_feedback(feedback_data:EventFeedback, db: AsyncSession):
    feedback = EventFeedBack(
        event_id = feedback_data.event_id,
        user_feedback = feedback_data.feedback
    )
    db.add(feedback)
    await db.flush()
    await db.commit()
    await db.refresh(feedback)
    return feedback

async def fech_public_event_by_id(event_id: str, db: AsyncSession, organization_id:str):
    # decrypted_event_id = await _decrypt_custom(event_id, fernet)
    # decrypted_org_id = await _decrypt_custom(event_id, fernet)

    result = await db.execute(select(Event).where(Event.id == event_id).where(Event.organization_id == organization_id ))
    event = result.scalar_one_or_none()
    if not event:
        raise HTTPException(status_code=404, detail=f"Event not found")
    # Eager load the documents
    result = await db.execute(
        select(Event).options(selectinload(Event.documents)).where(Event.id == event.id).where(Event.organization_id == organization_id)
    )
    event_with_docs = result.scalar_one()
    return event_with_docs

async def create_shared_url(db,current_user, organization_id, event_id):
    if current_user.role not in [UserRole.ADMIN, UserRole.SUPER_ADMIN]:
        raise HTTPException(
            status_code=status.HTTP_203_NON_AUTHORITATIVE_INFORMATION,
            detail='Unautherize operation'
        )
    if current_user.role == UserRole.ADMIN:
        if current_user.organization_id != organization_id:
            raise HTTPException(
                status_code=status.HTTP_203_NON_AUTHORITATIVE_INFORMATION,
                detail='Unautherize operation'
            )
        
    result = await db.execute(select(Event).where(Event.id == event_id).where(Event.organization_id == organization_id ))
    event = result.scalar_one_or_none()
    if not event:
        raise HTTPException(status_code=404, detail=f"Event not found")
    # Secret key for encryption (should be stored securely)
    encrypted_event_id = await _encrypt_custom(event.id, fernet)
    encrypted_org_id = await _encrypt_custom(organization_id, fernet)
    return {"public_url": f"{envs.FRONTEND_HOST}organization/{encrypted_org_id}/event/{encrypted_event_id}"}
    

async def fech_event_by_id(event_id: int, db: AsyncSession, current_user, organization_id):
    # if current_user.role == UserRole.USER:
    #     falg_for_form = await get_rbac_form_submission_by_ids(db, organization_id, current_user.group_ids)
    #     if not falg_for_form:
    #         raise HTTPException(
    #             status_code=status.HTTP_203_NON_AUTHORITATIVE_INFORMATION,
    #             detail="Unautherized operation"
    #         )
    # else:
    #     await event_checks_admin_super_admin(current_user, organization_id=organization_id)
    
        
    result = await db.execute(select(Event).where(Event.id == event_id).where(Event.organization_id == organization_id ))
    event = result.scalar_one_or_none()
    
    if not event:
        raise HTTPException(status_code=404, detail=f"Event not found")
    # Eager load the documents
    result = await db.execute(
        select(Event).options(selectinload(Event.documents)).where(Event.id == event.id).where(Event.organization_id == organization_id)
    )
    event_with_docs = result.scalar_one()
    return event_with_docs

async def add_event(request: CreateEventRequest, db: AsyncSession, organization_id:int):
    # Create the Event object
    event = Event(
        organization_id = organization_id,
        email=request.Email,
        name=request.Name,
        building=request.Building,
        department=request.Department,
        title=request.Title,
        should_live_on_marti_page=request.should_live_on_marti_page,
        should_live_on_marti_agent = request.should_live_on_marti_agent,
        additional=request.additional,
        marti_agent_review = False,
        marti_website_review= False,
        is_rejected_marti_website = False,
        is_rejected_marti_agent = False
    )

    db.add(event)
    await db.flush()  # Flush to get event.id

    # If there are documents, add them
    if request.document_files:
        for file_info in request.document_files:
            
            file_extension = os.path.splitext(file_info)[1][1:]
            #https://marti-dev-file-upload-bucket.s3.us-east-2.amazonaws.com/uploaded_file_doc/1/0/RobertMarshallProfile.pdf
            doc = EventDocument(
                event_id=event.id,
                document_name=file_info,
                content_type=file_extension,  # You can update this
                status="Uploaded"
            )
            db.add(doc)

    await db.commit()
    await db.refresh(event)
    # Eager load the documents
    result = await db.execute(
        select(Event).options(selectinload(Event.documents)).where(Event.id == event.id)
    )
    event_with_docs = result.scalar_one()

    return event_with_docs


# async def create_case_for_event(db: AsyncSession, current_user, organization_id, event_id, case_title, case_message):
#     case = EventCase(
#         event_id=event_id,
#         created_email=current_user.email,
#         organization_id=organization_id,
#         status=CaseStatus.OPEN,
#         case_title=case_title
#     )
#     db.add(case)
#     await db.flush()

#     is_user_message_flag = True if current_user.role == UserRole.USER else False
#     new_case_message = CaseMessage(
#         case_id=case.id, # New case's ID will be available after add() and flush/commit
#         message=case_message,
#         is_user_message=is_user_message_flag
#     )
#     db.add(new_case_message)
#     try:
#         await db.commit()
#         # await db.refresh(case) # Refresh to load relationships like 'messages'
#         result = await db.execute(
#             select(EventCase)
#             .options(selectinload(EventCase.messages))
#             .where(EventCase.id == case.id)
#         )
#         case = result.scalar_one()
#         print(f'Case and initial message added to database. Case ID: {case.id}')
#     except Exception as e:
#         await db.rollback() # Rollback on error
#         raise HTTPException(
#             status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
#             detail=f"Failed to create case: {e}"
#         )
    
#     print(f'case added to db')
#     # await db.refresh(case)
#     return case

# async def add_case_message(db: AsyncSession, case_id, message, is_user_message):
#     case_message = CaseMessage(
#         case_id=case_id,
#         message=message,
#         is_user_message=is_user_message
#     )
#     db.add(case_message)
#     # await db.flush()
#     await db.commit()
#     await db.refresh(case_message)
#     return case_message

# async def get_case_by_id(db: AsyncSession, organization_id,case_id):
#     result = await db.execute(select(EventCase).where(EventCase.id == case_id).where(EventCase.organization_id == organization_id))
#     case = result.scalar_one_or_none()
#     if not case:
#         raise HTTPException(status_code=404, detail=f"Case not found")
#     result = await db.execute(
#         select(EventCase)
#         .options(selectinload(EventCase.messages))
#         .where(EventCase.id == case_id)
#         .where(EventCase.organization_id == organization_id)
#     )
#     case = result.scalar_one_or_none()
#     if not case:
#         raise HTTPException(status_code=404, detail="Case not found")
#     return case

# async def get_case_by_event_id(db: AsyncSession, organization_id,event_id):
#     result = await db.execute(select(EventCase).where(EventCase.event_id == event_id).where(EventCase.organization_id == organization_id))
#     case = result.scalar_one_or_none()
#     if not case:
#         raise HTTPException(status_code=404, detail=f"Case not found")
#     result = await db.execute(select(EventCase).options(selectinload(EventCase.messages)).where(EventCase.event_id == event_id).where(EventCase.organization_id == organization_id))
#     case_with_messages = result.scalar_one()
#     return case_with_messages

# async def get_all_cases(db: AsyncSession, organization_id):
#     result = await db.execute(select(EventCase).where(EventCase.organization_id == organization_id))
#     cases = result.scalars().all()
#     return cases

# async def delete_case(db: AsyncSession, organization_id,case_id):
#     result = await db.execute(select(EventCase).where(EventCase.id == case_id).where(EventCase.organization_id == organization_id))
#     case = result.scalar_one_or_none()
#     if not case:
#         raise HTTPException(status_code=404, detail=f"Case not found")
#     await db.delete(case)
#     await db.commit()
#     return

# async def delete_case(db: AsyncSession, organization_id, case_id):
    # Load the case with its messages to ensure cascade works
    result = await db.execute(
        select(EventCase)
        .options(selectinload(EventCase.messages))
        .where(EventCase.id == case_id)
        .where(EventCase.organization_id == organization_id)
    )
    case = result.scalar_one_or_none()
    if not case:
        raise HTTPException(status_code=404, detail=f"Case not found")
    
    await db.delete(case)
    await db.commit()
    return