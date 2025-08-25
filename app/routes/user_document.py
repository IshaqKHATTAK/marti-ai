from sqlalchemy.orm import Session
from fastapi.responses import JSONResponse
from fastapi import APIRouter, Depends, status 
from crawl4ai import AsyncWebCrawler
from app.schemas.request.document import WebsiteLink
from app.common import database_config
from app.services import document
from fastapi import UploadFile, HTTPException

document_upload_routes = APIRouter(
    prefix="/api/v1/document",
    tags=["Document"],
    responses={404: {"description": "Not found"}},
    # dependencies=[Depends(oauth2_scheme), Depends(validate_access_token), Depends(is_admin)]
)

# @document_upload_routes.post("/website-link", status_code=status.HTTP_200_OK)
# async def upload_website_link(data: WebsiteLink, session: Session = Depends(database_config.get_async_db)): 
#     await document.scrap_website(url = data.url, session = session)
#     return JSONResponse({'message':'webiste sracped succefully! and stored in vector store.'})

    
# @document_upload_routes.post("/upload-files", status_code=status.HTTP_200_OK)
# async def upload_document(files: list[UploadFile], session: Session = Depends(database_config.get_async_db)): 
#     allowed_extensions = {
#                         ".pdf", ".json", ".xml", ".docx", ".doc", ".txt", ".csv", ".xlsx", 
#                         ".ppt", ".pptx", ".jpg", ".png", ".md", ".epub", ".mbo", ".rtf", ".html"
#                         }
#     for file in files:
#         if not any(file.filename.endswith(ext) for ext in allowed_extensions):
#             raise HTTPException(
#                 status_code=status.HTTP_400_BAD_REQUEST,
#                 detail=f"File '{file.filename}' is not allowed."
#             )
#     await document.manage_upload_file(files, session)

#     return JSONResponse({'message':'File uploaded succefully! and stored in knowledge base.'})


# @document_upload_routes.get("/list", status_code=status.HTTP_200_OK)
# async def get_documents_list(session: Session = Depends(database_config.get_async_db)):
#     # instead of 1 there should be organization id
#     doc_list = await document.get_documents_list(1, session)
#     return doc_list.model_dump(exclude_none=True)

# @document_upload_routes.get("/delete", status_code=status.HTTP_200_OK)
# async def delete_documents(session: Session = Depends(database_config.get_async_db)):
