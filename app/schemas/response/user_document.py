from typing import List, Optional
from pydantic import BaseModel


class FileInfo(BaseModel):
    filename: str
    content_type: Optional[str] = None
    status: Optional[str] = None
    error: Optional[str] = None


class DocumentsListResponse(BaseModel):
    uploaded_files: List[FileInfo]
    processing_files: List[FileInfo]
    failed_files: List[FileInfo]