from app.services.auth import get_current_user
from sqlalchemy.orm import Session
from fastapi.responses import JSONResponse
from fastapi import APIRouter, Depends, status , HTTPException
from app.schemas.request.notifications import CreateAnnouncement
from sqlalchemy.ext.asyncio import AsyncSession
from app.common.database_config import get_async_db
from app.models.user import User
from app.services.notifications import create_gloabl_announcement


announcement_router = APIRouter(
    prefix="/api/v1/notifications",
    tags=["notifications"],
    dependencies=[Depends(get_current_user)]
)


@announcement_router.post("/create/global/announcement", status_code=status.HTTP_200_OK)
async def create_gloabl_announcements(
    create_announcement: CreateAnnouncement,
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(get_current_user) 
):
    if not create_announcement.title or not create_announcement.description or not create_announcement.criticality:
        raise HTTPException(status_code=404, detail="Provide complete infromation.")
    created_announcement =  await create_gloabl_announcement(
        db, 
        create_announcement,
        current_user,
    )
    # retun teh created chatbot emeory in format creator, text
    return JSONResponse({'message':'Notification has been sent succefully!'})
