import uuid
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, EmailStr, Field, ConfigDict


class CustomerCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    email: EmailStr
    phone: Optional[str] = Field(None, max_length=50)
    billing_address: Optional[str] = None
    currency: str = Field(default="USD", min_length=3, max_length=3)
    user_id: Optional[uuid.UUID] = None


class CustomerUpdateRequest(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    email: Optional[EmailStr] = None
    phone: Optional[str] = Field(None, max_length=50)
    billing_address: Optional[str] = None
    currency: Optional[str] = Field(None, min_length=3, max_length=3)
    is_active: Optional[bool] = None
    is_archived: Optional[bool] = None


class CustomerResponse(BaseModel):
    id: uuid.UUID
    user_id: Optional[uuid.UUID]
    name: str
    email: EmailStr
    phone: Optional[str]
    billing_address: Optional[str]
    currency: str
    is_active: bool
    is_archived: bool
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
