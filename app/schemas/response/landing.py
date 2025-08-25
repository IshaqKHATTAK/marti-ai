from typing import Optional
from pydantic import BaseModel

class LandingCreateResponse(BaseModel):
    id: int
    title: str
    link: str
    description: str
    identifier: int
    
class LandingGetItemResponse(BaseModel):
    id: int
    title: str
    link: str
    description: str

class LandingGetResponse(BaseModel):
    items: Optional[list[LandingGetItemResponse]] = []
    identifier: int

class AllLandingGetResponse(BaseModel):
    items: Optional[list[LandingGetResponse]] = []
    identifier: int

class FaqsCreationResponse(BaseModel):
    question: str
    answer: str
    id: int