from sqlalchemy.exc import SQLAlchemyError

from app.common.database_config import AsyncSessionLocal
import backoff
from app.models.organization import  Organization
from app.models.chatbot_model import ChatbotDocument, UrlSweep, ChatbotConfig, Threads
from sqlalchemy.future import select
from sqlalchemy import delete, any_
from sqlalchemy.orm import joinedload
from datetime import date, timedelta, datetime, timezone
from sqlalchemy.orm.attributes import flag_modified
from fastapi import APIRouter, Depends, HTTPException
from app.models.organization import RBAC
from app.models.user import User
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from app.common.database_config import get_async_db
from typing import List
import json
from collections import defaultdict
from app.common.env_config import get_envs_setting
envs = get_envs_setting()
scheduler = AsyncIOScheduler()
# async def get_file_from_user_db(Organization_id, filename, session):
#     result = await session.execute(select(ChatbotConfig).filter(ChatbotConfig.id == data.id)) 
#     return result.scalars().first()


import logging
logger = logging.getLogger(__name__)

async def format_user_chatbot_permissions(db, organization_id, group_ids):
    merged_groups = defaultdict(dict)
    form_submission = False
    if group_ids:
        for group_id in group_ids:
            user_group_details = await get_rbac_groups_by_id(db, organization_id, group_id)
            form_submission = user_group_details.form_submission
            if user_group_details:
                for group in user_group_details.attributes:
                    chatbot_id = group["chatbot_id"]
                    for key, value in group.items():
                        if key == "chatbot_id":
                            merged_groups[chatbot_id]["chatbot_id"] = chatbot_id
                        else:
                            # If key not seen before, or current value is False and new is True, update it
                            merged_groups[chatbot_id][key] = merged_groups[chatbot_id].get(key, False) or value

    return list(merged_groups.values()),  form_submission

# group_ids
async def get_users_of_group(db, group_id):
    stmt = select(User).where(group_id == any_(User.group_ids))
    results = await db.execute(stmt)
    return results.scalars().all()

async def delete_rbac_group_by_id(db, group_id: int):
    stmt = select(RBAC).where(RBAC.id == group_id)
    result = await db.execute(stmt)
    group = result.scalar_one_or_none()

    if not group:
        return False

    await db.delete(group)
    await db.commit()
    return True

async def get_rbac_groups_by_org_id_paginated(skip, limit, db, organization_id, group_name = None):
    stmt = (
        select(RBAC)
        .where(RBAC.organization_id == organization_id)
        .offset(skip)
        .limit(limit)
    )
    if group_name:
        stmt = stmt.filter(RBAC.name.ilike(f"%{group_name}%"))
    result = await db.execute(stmt)
    return result.scalars().all()


async def get_rbac_groups_by_org_id(db, organization_id, group_name=None):
    stmt = select(RBAC).where(RBAC.organization_id == organization_id)
    if group_name:
        stmt = stmt.filter(RBAC.name.ilike(f"%{group_name}%"))
    result = await db.execute(stmt)
    return result.scalars().all()

async def get_rbac_form_submission_by_ids(db, organization_id: int, group_ids):
    for g_id in group_ids:
        stmt = select(RBAC).where(
        (RBAC.organization_id == organization_id) & (RBAC.id == g_id)
        )
        result = await db.execute(stmt)
        data = result.scalar_one_or_none()
        if data.form_submission:
            return True

    return False


async def get_rbac_groups_by_id(db, organization_id: int, group_id: int):
    stmt = select(RBAC).where(
        (RBAC.organization_id == organization_id) & (RBAC.id == group_id)
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none()

async def create_rbac_groups(db, name,form_submission, attributes_list, organization_id):
    new_group = RBAC(   
        name=name,
        form_submission = form_submission,
        attributes=attributes_list,
        organization_id=organization_id
    )
    db.add(new_group)
    await db.commit()
    await db.refresh(new_group)
    return new_group

async def update_rbac_groups(db, name, form_submission, attributes_list, organization_id, group_id):
    stmt = select(RBAC).where(RBAC.id == group_id)
    result = await db.execute(stmt)
    group = result.scalar_one_or_none()

    if not group:
        raise HTTPException(status_code=404, detail="Group not found")
    if group.name == "Event Reviewers": #This is pre-build gropu for form reviews so should not be updatable.
        raise HTTPException(status_code=400, detail="You can not modify the pre-existing access levels")
    
    if name:
        group.name = name
    if form_submission is not None:
        print(f'form submission added')
        group.form_submission = form_submission
    # Convert existing attributes to dict {chatbot_id: attribute_dict}
    existing_attributes = {attr["chatbot_id"]: attr for attr in (group.attributes or [])}
    updated_attributes = []
    for new_attr in attributes_list:
        updated_attributes.append(new_attr)
        # if new_attr["chatbot_id"] in existing_attributes:
        #     #  Update existing chatbot's attributes
        #     existing_attributes[new_attr["chatbot_id"]].update(new_attr)
        # else:
        #     # Add new chatbot and its attributes
        #     existing_attributes[new_attr["chatbot_id"]] = new_attr
    print(f'form submission attribute = {form_submission}')
    print(f'new attr == {updated_attributes}')
    # print(f'list(existing_attributes.values()) == {list(existing_attributes.values())}')
    group.attributes = updated_attributes
    
    flag_modified(group, "attributes")  # important to detect change
    db.add(group)
    await db.commit()
    await db.refresh(group)
    
    return group

async def update_thread_prompt_and_counter(db_session, prompt_generated, counter,thread_id):
    user_thread = await db_session.execute(
        select(Threads).filter((Threads.thread_id == thread_id))
    )
    user_thread_result = user_thread.scalars().first()
    
    if user_thread_result:
        user_thread_result.agent_generated_prompt = prompt_generated
        user_thread_result.questions_counter = counter
        
        await db_session.commit()  
        
    return

async def get_thread_data(db_session,thread_id):
    user_thread = await db_session.execute(
        select(Threads).filter((Threads.thread_id == thread_id))
    )
    return user_thread.scalars().first()

async def get_document(document_name, chatbot_id, session):
    docs = await session.execute(
            select(ChatbotDocument).filter(
                (ChatbotDocument.document_name == document_name) & 
                (ChatbotDocument.chatbot_id == chatbot_id)
            )
        )
    return docs.scalars().first()

async def delete_document(document_name: str, organization_id, session):
    docs = await session.execute(
        select(ChatbotDocument).filter(
        (ChatbotDocument.document_name == document_name) &
        (ChatbotDocument.organization_id == organization_id)
        )
    )
    document = docs.scalars().first()
    if document:
        await session.delete(document)
        await session.commit()  
        return True
    
    return False

async def update_document_status(document_name: str, new_status: str, organization_id, session):
    docs = await session.execute(
        select(ChatbotDocument).filter((ChatbotDocument.document_name == document_name) & (ChatbotDocument.organization_id == organization_id))
    )
    document = docs.scalars().first()
    
    if document:
        document.status = new_status
        await session.commit()  
        return True
    
    return False

async def insert_document_entry(
        chatbot_id: int,
        document_name: str,
        content_type: str,
        status: str,
        session
    ):
    new_document = ChatbotDocument(
        chatbot_id=chatbot_id,
        document_name=document_name,
        content_type=content_type,
        status=status
    )
    
    session.add(new_document)
    await session.commit()  
    await session.refresh(new_document)  

    return new_document

async def delete_document_entry(document_id: int, db):
    query = delete(ChatbotDocument).where(ChatbotDocument.id == document_id)
    await db.execute(query)
    await db.commit()

async def delete_webscrap_entry(website_id: int, db):
    query = delete(ChatbotDocument).where(ChatbotDocument.id == website_id)
    await db.execute(query)
    await db.commit()

async def insert_webscrap_entry(
        chatbot_id: int,
        url: str,
        sweap_domain: str,
        content_type: str,
        status: str,
        session
    ):

    if sweap_domain:
        new_document = ChatbotDocument(
            chatbot_id=chatbot_id,
            document_name=url,
            content_type=content_type,
            url_sweep_option = UrlSweep.Domain.value,
            status=status
        )
    else:
        new_document = ChatbotDocument(
            chatbot_id=chatbot_id,
            document_name=url,
            content_type=content_type,
            url_sweep_option = UrlSweep.website_page.value,
            status=status
        )
    session.add(new_document)
    await session.commit()  
    await session.refresh(new_document)  

    return new_document

async def get_webscrap_entery(id, chatbot_id, session):
    docs = await session.execute(
            select(ChatbotDocument).filter(
                (ChatbotDocument.id == id) & 
                (ChatbotDocument.chatbot_id == chatbot_id)
            )
        )
    return docs.scalars().first()

async def get_organization_and_documents(organization_id: int, session=None):
    
    query = (
        select(Organization)
        .options(joinedload(Organization.documents))
        .filter(Organization.id == organization_id)
    )
    org_result = await session.execute(query)
    organization = org_result.scalars().first()
    if not organization:
        return {"error": "Organization not found"}
    
    return organization


async def increment_chatbot_message_count(chatbot_id, session):
    """
    Increment the chatbot's total_chatbot_messages count by one asynchronously.
    """
    result = await session.execute(
        select(ChatbotConfig).filter(ChatbotConfig.id == chatbot_id)
    )
    chatbot = result.scalars().first()

    print("increment_chatbot_message_count")

    if chatbot:
        chatbot.total_chatbot_messages_count += 2
        await session.commit()
        return chatbot.total_chatbot_messages_count
    
    return None


async def increment_admin_chatbot_message_count(chatbot_id, session):
    """
    Increment the chatbot's total_chatbot_messages count by one asynchronously.
    """
    result = await session.execute(
        select(ChatbotConfig).filter(ChatbotConfig.id == chatbot_id)
    )
    chatbot = result.scalars().first()

    if chatbot:
        chatbot.admin_per_days_messages_count += 2
        await session.commit()
        return chatbot.admin_per_days_messages_count
    
    return None


async def increment_chatbot_per_day_message_count(chatbot_id, session):
    """
    Increment the chatbot's total_chatbot_messages count by one asynchronously.
    """
    result = await session.execute(
        select(ChatbotConfig).filter(ChatbotConfig.id == chatbot_id)
    )
    chatbot = result.scalars().first()

    if chatbot:
        chatbot.per_day_messages = chatbot.per_day_messages or 0
        chatbot.per_day_messages += 2
        await session.commit()
        return chatbot.per_day_messages
    
    return None


async def increment_chatbot_monthly_message_count(chatbot, session):
    today = datetime.utcnow().date().isoformat() # YYYY-MM-DD format
            
    if chatbot.monthly_messages_count is None:
        chatbot.monthly_messages_count = 2
    else:
        chatbot.monthly_messages_count += 2
    
    print(f"Before update: {chatbot.monthly_messages_count}")

    session.add(chatbot) 
    await session.commit()

    return chatbot.monthly_messages_count


async def get_last_seven_days_count(chatbot_id, session):
    """
    Retrieve the last 7 days' message counts for a chatbot.
    """
    result = await session.execute(
        select(ChatbotConfig.public_last_7_days_messages)
        .where(ChatbotConfig.id == chatbot_id)
    )
    row = result.scalar()
    
    return row if row else {}




async def reset_chatbot_message_counts():
    """
    Resets the admin message count and total chatbot messages count for all chatbots daily.
    """
    print(f"scheduler triggtered")
    async for session in get_async_db():    # increment_external_bot_monthly_message_countUse async session for DB operations
        try:
            result = await session.execute(select(ChatbotConfig))
            chatbots = result.scalars().all()

            for chatbot in chatbots:
                chatbot.admin_per_days_messages_count = 0
                chatbot.total_chatbot_messages_count = 0  
            
            await session.commit()
            print(f"Chatbot message counts reset at {datetime.now()}.")
        except Exception as e:
            print(f"Error resetting chatbot messages: {e}")
        finally:
            await session.close()

# Schedule the reset function to run daily at 00:00 UTC
# scheduler.add_job(reset_chatbot_message_counts, "cron", hour=0, minute=0)
from datetime import datetime, timezone, timedelta
from dateutil.relativedelta import relativedelta

async def check_and_refresh_chat_cycle(user: User,bot_config, session) -> bool:

    # Current UTC time
    now = datetime.now(timezone.utc)
    print(f'now and next: {now} >= {user.billing_cycle_end}')
    if now >=  user.billing_cycle_end:
        # Cycle expired → reset usage
        bot_config.monthly_messages_count = 0
        # Move cycle forward by 1 month
        user.billing_cycle_end = now + relativedelta(months=1)

        session.add_all([user, bot_config])
        await session.commit()
        await session.refresh(user)
        print(f'returned treu in side check and refresh')
        return True
    else:
        print(f'returned False in side check and refresh')
        return False