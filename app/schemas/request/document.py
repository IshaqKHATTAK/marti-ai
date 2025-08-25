from typing import Optional
from pydantic import BaseModel

class WebsiteLink(BaseModel):
    url: str
    id: int