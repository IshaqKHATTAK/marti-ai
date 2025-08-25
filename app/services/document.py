from crawl4ai import AsyncWebCrawler
from pathlib import Path
from app.utils import document_helper
from app.models.organization import  Organization
from app.models.chatbot_model import ChatbotDocument
from sqlalchemy.future import select
from app.utils.database_helper import get_document, update_document_status, delete_document, insert_document_entry
from app.common.upload_file_vector_store import upload_text_file, PINECONE_KB_INDEX_CLIENT
from app.common.env_config import get_envs_setting
from sqlalchemy import case, desc
from app.schemas.response.user_document import FileInfo, DocumentsListResponse
from fastapi import HTTPException, status 
from sqlalchemy import select, case, delete

setting = get_envs_setting()

async def scrap_website(url: str, session, output_path: str = None):
    organization_id = 1
    async with AsyncWebCrawler(verbose=True) as crawler:
        result = await crawler.arun(
            url=url,
        )
        if result.success:
            print(f"Website scrapped succesfully!")
            docs = await get_document(str(url), organization_id, session)
            if docs:
                _ = await update_document_status(str(url),'Uploaded',organization_id,session)
            # Delete from pinecone
            else:
                await insert_document_entry(organization_id,str(url), 'url','Uploaded',session)
            # Chunk, vectorise and upload to Pinecone (consumer vector id pattern -> user_name:file_name:chunk_num)
            await upload_text_file(f"{organization_id}:{url}:", result.markdown, PINECONE_KB_INDEX_CLIENT, setting.EMBEDDINGS_MODEL_NAME, file_type = 'application/octet-stream')
            await update_document_status(url,'Completed',organization_id, session)
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"The website ({url}) has some issues while extracting content."
            )
    return


async def manage_upload_file(files, session):
    organization_id = 1 #This should be replaced with chatbot id
    for file in files:
        file_extension = Path(file.filename).suffix.lower()
        # if file_extension == '.csv' or file_extension == '.xlsx' or file_extension == 'xml':
        #     return
        
        # with open(file.filename, "wb") as f:
        #     contents = await file.read()
        #     f.write(contents)
        #     print(f"File '{file.filename}'.")

        extracted_text = await document_helper.extract_text(file.filename, file, file.content_type)
        
        # If there exist document with same name
        docs = await get_document(file.filename, organization_id, session)
        if docs:
            _ = await update_document_status(file.filename,'Uploaded',organization_id,session)
            # delete from pinecone
        else:
            await insert_document_entry(organization_id,file.filename, file.content_type,'Uploaded',session)
        
        print('file content while upserting')
        # Chunk, vectorise and upload to Pinecone (consumer vector id pattern -> user_name:file_name:chunk_num)
        await upload_text_file(f"{organization_id}:{file.filename}:", extracted_text, PINECONE_KB_INDEX_CLIENT, setting.EMBEDDINGS_MODEL_NAME, file_type = file.content_type, file=file)
        await update_document_status(file.filename,'Completed',organization_id, session)
        # if len(extracted_text) > settings.CONSUMER_FILE_CHARACTERS_LIMIT:
        #     raise Exception(f"File character limit exceeded | File Character Count: {len(extracted_text)}")
        # user_doc_db = database_helper.get_file_from_user_db(id, file.filename, session)
        
    return 
    

async def get_documents_list(organization_id, session):
    stmt = (
        select(
            ChatbotDocument.document_name,
            ChatbotDocument.content_type,
            ChatbotDocument.status,
        )
        .where(
            ChatbotDocument.organization_id == organization_id,
            ChatbotDocument.status == "Completed",
        )
        .order_by(desc(ChatbotDocument.created_at))
    )
    result = await session.execute(stmt)
    uploaded_user_docs = result.all()
    
    stmt = (
        select(
            ChatbotDocument.document_name,
            ChatbotDocument.content_type,
            case(
                (ChatbotDocument.status == 'upload_failed', 'Upload Failed'),
                else_=ChatbotDocument.status,
            ).label("status"),
        )
        .where(
            ChatbotDocument.organization_id == organization_id,
            ChatbotDocument.status == "upload_failed",
        )
        .order_by(desc(ChatbotDocument.created_at))
    )
    result = await session.execute(stmt)
    failed_user_docs = result.all()

    stmt = (
        select(
            ChatbotDocument.document_name,
            ChatbotDocument.content_type,
            case(
                (ChatbotDocument.status == 'to_delete', 'Deleting'),
                else_=ChatbotDocument.status,
            ).label("status"),
        )
        .where(
            ChatbotDocument.organization_id == organization_id,
            ChatbotDocument.status != "upload_failed",
            ChatbotDocument.status != "Completed",
            ChatbotDocument.status != "del_failed",
        )
        .order_by(desc(ChatbotDocument.created_at))
    )
    result = await session.execute(stmt)
    processing_user_docs = result.all()

    # Clean up upload_failed docs after sending them
    stmt = (
        delete(ChatbotDocument)
        .where(
            ChatbotDocument.organization_id == organization_id,
            ChatbotDocument.status == "upload_failed",
        )
    )
    await session.execute(stmt)
    await session.commit()

    # Map query results to FileInfo
    uploaded_files = [FileInfo(filename=doc[0], content_type=doc[1], status=doc[2]) for doc in uploaded_user_docs]
    failed_files = [FileInfo(filename=doc[0], content_type=doc[1], status=doc[2]) for doc in failed_user_docs]
    processing_files = [FileInfo(filename=doc[0], content_type=doc[1], status=doc[2]) for doc in processing_user_docs]

    return DocumentsListResponse(uploaded_files=uploaded_files, processing_files=processing_files, failed_files=failed_files)
