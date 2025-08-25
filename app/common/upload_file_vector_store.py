from itertools import islice
from openai import RateLimitError, OpenAI
import logging, tiktoken, backoff
from typing import List
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain_openai import OpenAIEmbeddings
from app.common import env_config
from app.utils.document_helper import _chunk_text, _chunk_markdown, _chunk_csv
from pinecone import Pinecone
import os, base64, mimetypes
import tempfile
import io
from PIL import Image
from PyPDF2 import PdfReader
from docx import Document as DocxDocument
import pandas as pd
from app.models.chatbot_model import QATemplate
from fastapi import HTTPException,status
import uuid

logger = logging.getLogger(__name__)

# Declare global variables
SETTINGS = None
PINECONE_CLIENT = None
PINECONE_KB_INDEX_CLIENT = None

def _setup_dependencies():
    global SETTINGS, PINECONE_CLIENT, PINECONE_KB_INDEX_CLIENT

    if SETTINGS:
        return
    # Get App Settings
    SETTINGS = env_config.get_envs_setting()
    # Initialise Pinecone
    PINECONE_CLIENT = Pinecone(api_key=SETTINGS.PINECONE_API_KEY)
    PINECONE_KB_INDEX_CLIENT = PINECONE_CLIENT.Index(name=SETTINGS.PINECONE_KNOWLEDGE_BASE_INDEX)
    

    logger.info("Pinecone client initialised")

# _setup_dependencies()

# Add new file type handling functions
def get_file_type(file_name: str) -> str:
    """Determine file type from filename or mime type."""
    mime_type, _ = mimetypes.guess_type(file_name)
    if mime_type:
        if mime_type.startswith('image/'): return 'image'
        elif mime_type == 'application/pdf': return 'pdf'
        elif mime_type == 'application/vnd.openxmlformats-officedocument.wordprocessingml.document': return 'docx'
        elif mime_type == 'text/csv': return 'csv'
        elif mime_type in ['application/vnd.ms-excel', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet']: return 'excel'
    return 'text'

def process_image(image_content: bytes) -> str:
    """Process image bytes and convert to base64."""
    try:
        img = Image.open(io.BytesIO(image_content))
        if img.mode != 'RGB':
            img = img.convert('RGB')
        img_byte_arr = io.BytesIO()
        img.save(img_byte_arr, format='JPEG')
        return base64.b64encode(img_byte_arr.getvalue()).decode('utf-8')
    except Exception as e:
        raise Exception(f"Error processing image: {str(e)}")

@backoff.on_exception(backoff.expo, Exception, max_time=10, jitter=backoff.random_jitter, logger=logger)
async def delete_from_pinecone(file_name: str, file_type:str, pc_index: Pinecone.Index):
    """file_name should always follow the pattern -> id:file_name:
    Incase of web site the pattern should be id:url:"""
    try:
        logger.warn(f"Trying to delete file from Pinecone | File Name: {file_name} | Index: {pc_index}")
        to_delete = []
        all_ids = pc_index.list(prefix=file_name)
        
        for ids in all_ids:
            to_delete.extend(ids)
        print(f'ids to delete from pinecone {to_delete}')
        if len(to_delete) > 0:
            if file_type == 'url':
                batches = _text_chunk_into_batches(to_delete, 20)
            elif file_type == 'application/octet-stream':
                
                for batch in batches:
                    res = pc_index.delete(ids=batch)
                    logger.info(f"Deleted file chunks from Pinecone | Vector Ids: {batch} | Index: {pc_index} | Response: {res}")
    except Exception as ex:
        raise Exception(f"Unable to delete file from Pinecone | File Name: {file_name} | Index: {pc_index} | Error: {ex}")

async def upload_markdown_file(file_name: str, file_content: str, pc_index: Pinecone.Index, embeddings_model_name: str):
    """file_name should always follow the pattern -> id:file_name:
    Incase of web site scraping the pattern should be id:url:"""
    logger.info(f"Uploading file to Pinecone | File Name: {file_name} | Index: {pc_index}")
    try:
        chunks = _chunk_markdown(file_content)
        logger.info(f"File content split into {len(chunks)} chunks | File Name: {file_name} | Total Tokens: {_num_tokens_from_string(file_content)}")
        # Initialise list for Pinecone vector insertion
        vectors_to_insert = []

    except Exception as ex:
        raise Exception(f"Unable to upload file on Pinecone | File Name: {file_name} | Index: {pc_index} | Error: {ex}")

async def _upload_csv_file(file_name: str, file, pc_index: Pinecone.Index, embeddings_model_name: str):
    with tempfile.NamedTemporaryFile(delete=False, suffix=".csv") as temp_file:
                contents = await file.read()
                temp_file.write(contents)
                temp_file_path = temp_file.name
    chunks = _chunk_csv(temp_file_path)
    
    os.remove(temp_file_path)
    vectors_to_insert = []
    chunk_num = 1
    for batch in _text_chunk_into_batches(chunks, 20):
        response = await _get_embeddings(embeddings_model_name, [chunk.page_content for chunk in batch])
        for vector_data, chunk in zip(response, batch):
            vector_id = f"{file_name}:chunk{chunk_num}"
            vector = vector_data
            vectors_to_insert.append((
                vector_id,
                vector,
                {
                    "file_name": file_name,
                    "chunk_num": chunk_num,
                    "text": chunk.page_content
                }
            ))
            # Increment the chunk_num for each processed chunk
            chunk_num += 1

    for batch in _text_chunk_into_batches(vectors_to_insert, 100):
        response = _upsert_to_pinecone(pc_index=pc_index, vectors=batch)
        logger.info(f"File uploaded successfully to Pinecone | File Name: {file_name} | Index: {pc_index} | Upsert Response: {response}")
    
    return

def extract_text_from_pdf(file_content: bytes) -> str:
    """Extract text from PDF content."""
    pdf = PdfReader(io.BytesIO(file_content))
    text = ''
    for page in pdf.pages:
        text += page.extract_text() + '\n'
    return text

def extract_text_from_docx(file_content: bytes) -> str:
    """Extract text from DOCX content."""
    doc = DocxDocument(io.BytesIO(file_content))
    return '\n'.join(paragraph.text for paragraph in doc.paragraphs)

def extract_text_from_excel(file_content: bytes) -> str:
    """Extract text from Excel content."""
    df = pd.read_excel(io.BytesIO(file_content))
    return df.to_string()

def extract_text_from_csv(file_content: bytes) -> str:
    """Extract text from CSV content."""
    df = pd.read_csv(io.BytesIO(file_content))
    return df.to_string()

@backoff.on_exception(backoff.expo, Exception, max_time=10, jitter=backoff.random_jitter, logger=logger)
async def generate_image_summary(image_content: bytes) -> str:
    """Generate image description using OpenAI Vision API."""
    try:
        client = OpenAI(api_key=SETTINGS.OPENAI_API_KEY)
        base64_image = process_image(image_content)
        
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{
                "role": "user",
                "content": [
                    {"type": "text", "text": "Please describe this image in detail."},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}}
                ]
            }],
            max_tokens=300
        )
        return response.choices[0].message.content
    except Exception as e:
        logger.error(f"Error generating image summary: {str(e)}")
        raise

async def upload_text_file(file_name: str, file_content: str, pc_index: Pinecone.Index, embeddings_model_name: str, file_type: str = None, file = None):
    """file_name should always follow the pattern -> id:file_name:
    Incase of web site scraping the pattern should be id:url:"""
    try:
        logger.info(f"Uploading file to Pinecone | File Name: {file_name} | Index: {pc_index}")
        
        # Handle different file types
        if file_type == 'image':
            summary = await generate_image_summary(file_content)
            chunks = [summary]
            content_type = "image"
        elif file_type == 'application/pdf':
            chunks = _chunk_text(extract_text_from_pdf(file_content))
            content_type = "pdf"
        elif file_type == 'application/vnd.openxmlformats-officedocument.wordprocessingml.document':
            chunks = _chunk_text(extract_text_from_docx(file_content))
            content_type = "docx"
        elif file_type == 'markdown':
            chunks = upload_markdown_file(file_name, file_content, pc_index, embeddings_model_name)
            content_type = "markdown"
        elif file_type == 'text/csv':
            await _upload_csv_file(file_name, file, pc_index, embeddings_model_name)
            return
        else:
            chunks = _chunk_text(file_content)
            content_type = "text"

        logger.info(f"File content split into {len(chunks)} chunks | File Name: {file_name}")
        
        vectors_to_insert = []
        chunk_num = 1
        
        for batch in _text_chunk_into_batches(chunks, 20):
            response = await _get_embeddings(embeddings_model_name, batch if isinstance(batch, list) else [batch])
            
            for vector_data, text in zip(response, batch if isinstance(batch, list) else [batch]):
                vector_id = f"{file_name}:chunk{chunk_num}"
                metadata = {
                    "file_name": file_name,
                    "type": content_type,
                    "chunk_num": chunk_num,
                    "total_chunks": len(chunks),
                    "text": text
                }
                
                if content_type == "image":
                    metadata["summary"] = text
                
                vectors_to_insert.append((vector_id, vector_data, metadata))
                chunk_num += 1

        for batch in _text_chunk_into_batches(vectors_to_insert, 100):
            response = _upsert_to_pinecone(pc_index=pc_index, vectors=batch, chatbot_id=chatbot_id)
            logger.info(f"File uploaded successfully | File Name: {file_name} | Index: {pc_index} | Response: {response}")

    except Exception as ex:
        raise Exception(f"Unable to upload file | File Name: {file_name} | Error: {ex}")

@backoff.on_exception(backoff.expo, Exception, max_time=10, jitter=backoff.random_jitter, logger=logger)
def _upsert_to_pinecone(pc_index: Pinecone.Index, vectors: List[tuple], chatbot_id: int):
    return pc_index.upsert(vectors=vectors, namespace=f"{chatbot_id}-kb")

@backoff.on_exception(backoff.expo, RateLimitError, max_time=90, jitter=backoff.random_jitter, logger=logger)
async def _get_embeddings(embeddings_model_name: str, text_list: List[str]):
    embeddings_model  = OpenAIEmbeddings(model=embeddings_model_name)
    return embeddings_model.embed_documents(text_list)

def _text_chunk_into_batches(seq: List[str], size: int):
    it = iter(seq)
    while batch := list(islice(it, size)):
        yield batch


def _num_tokens_from_string(string: str) -> int:
    """Returns the number of tokens in a text string."""
    encoding = tiktoken.get_encoding("cl100k_base")
    num_tokens = len(encoding.encode(string))
    return num_tokens

# async def get_download_file_link(username: str, file_name: str):
#     base_path = "static"
#     file_path = os.path.join(base_path, str(username), file_name)
#     if not os.path.exists(file_path):
#         return None  
#     return file_path


# async def get_kb_download_file_link( file_name: str):
#     base_path = "static"
#     file_path = os.path.join(base_path, 'knowledge_base', file_name)
#     if not os.path.exists(file_path):
#         return None  
#     return file_path
