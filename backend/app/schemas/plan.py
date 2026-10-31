import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Optional
from pydantic import BaseModel, Field, ConfigDict, model_validator

from app.models import BillingInterval


class PlanCreateRequest(BaseModel):
    product_id: uuid.UUID
    name: str = Field(..., min_length=1, max_length=255)
    code: str = Field(..., min_length=1, max_length=50)
    description: Optional[str] = None
    billing_interval: BillingInterval
    interval_count: int = Field(default=1, ge=1)
    price: Decimal = Field(..., ge=0)
    currency: str = Field(default="USD", min_length=3, max_length=3)
    trial_period_days: int = Field(default=0, ge=0)
    minimum_quantity: int = Field(default=1, ge=1)
    available_from: Optional[date] = None
    available_until: Optional[date] = None
    payment_terms_days: int = Field(default=0, ge=0)
    auto_close_on_end: bool = False
    allow_early_close: bool = True
    allow_pause: bool = True
    allow_renewal: bool = True
    is_active: bool = True

    @model_validator(mode="after")
    def validate_availability(self):
        if (
            self.available_from
            and self.available_until
            and self.available_until < self.available_from
        ):
            raise ValueError("available_until must be on or after available_from")
        return self


class PlanUpdateRequest(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    code: Optional[str] = Field(None, min_length=1, max_length=50)
    description: Optional[str] = None
    billing_interval: Optional[BillingInterval] = None
    interval_count: Optional[int] = Field(None, ge=1)
    price: Optional[Decimal] = Field(None, ge=0)
    currency: Optional[str] = Field(None, min_length=3, max_length=3)
    trial_period_days: Optional[int] = Field(None, ge=0)
    minimum_quantity: Optional[int] = Field(None, ge=1)
    available_from: Optional[date] = None
    available_until: Optional[date] = None
    payment_terms_days: Optional[int] = Field(None, ge=0)
    auto_close_on_end: Optional[bool] = None
    allow_early_close: Optional[bool] = None
    allow_pause: Optional[bool] = None
    allow_renewal: Optional[bool] = None
    is_active: Optional[bool] = None
    is_archived: Optional[bool] = None


class PlanResponse(BaseModel):
    id: uuid.UUID
    product_id: uuid.UUID
    name: str
    code: str
    description: Optional[str]
    billing_interval: BillingInterval
    interval_count: int
    price: Decimal
    currency: str
    trial_period_days: int
    minimum_quantity: int
    available_from: Optional[date]
    available_until: Optional[date]
    payment_terms_days: int
    auto_close_on_end: bool
    allow_early_close: bool
    allow_pause: bool
    allow_renewal: bool
    is_active: bool
    is_archived: bool
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
