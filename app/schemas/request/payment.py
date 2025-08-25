from typing import Optional
from pydantic import BaseModel
from typing import Optional, Dict


class SubscriptionRequest(BaseModel):
    priceId: str
    add_on:  Optional[Dict[str, bool]] = {}
    coupon: Optional[str] = None
    no_of_users_apart_from_admin: Optional[int] = 0
    user_usage_priceId: Optional[str] = None

class UpdateSubscriptionRequest(BaseModel):
    priceId: Optional[str] = None
    add_on:  Optional[Dict[str, bool]] = {}
    coupon: Optional[str] = None
    no_of_users_apart_from_admin: Optional[int] = None # The *new total* number of users for the preview
    user_usage_priceId: Optional[str] = None # The metered price ID for users (e.g., $6/yearly, $8/monthly)
    

class ValidatePromoCodeRequest(BaseModel):
    code: str


class UpdateCardRequest(BaseModel):
    payment_method_id: str