from typing import Optional
from pydantic import BaseModel
from typing import Optional, List

class CreateAnnouncement(BaseModel):
    title: str
    description: str
    criticality: str

