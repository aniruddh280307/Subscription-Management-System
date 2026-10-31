import uuid
from datetime import date, datetime
from typing import Optional
from pydantic import BaseModel, Field, ConfigDict

from app.models import SubscriptionStatus
from app.schemas.customer import CustomerResponse
from app.schemas.plan import PlanResponse


class SubscriptionCreateRequest(BaseModel):
    customer_id: uuid.UUID
    plan_id: uuid.UUID
    start_date: Optional[datetime] = None
    quantity: int = Field(default=1, ge=1)
    payment_terms_days: Optional[int] = Field(default=None, ge=0)
    expiry_date: Optional[date] = None


class SubscriptionUpdateRequest(BaseModel):
    cancel_at_period_end: Optional[bool] = None


class SubscriptionCancelRequest(BaseModel):
    immediately: bool = Field(
        default=False,
        description="If True, cancel immediately. If False, set cancel_at_period_end=True.",
    )


class SubscriptionPlanChangeRequest(BaseModel):
    new_plan_id: uuid.UUID
    immediately: bool = Field(
        default=True,
        description="If True, apply new plan immediately and recalculate period end. If False, defer to next cycle.",
    )


class SubscriptionResponse(BaseModel):
    id: uuid.UUID
    subscription_number: Optional[str]
    customer_id: uuid.UUID
    plan_id: uuid.UUID
    quotation_id: Optional[uuid.UUID]
    quantity: int
    payment_terms_days: int
    expiry_date: Optional[date]
    status: SubscriptionStatus
    start_date: datetime
    current_period_start: datetime
    current_period_end: datetime
    trial_start: Optional[datetime]
    trial_end: Optional[datetime]
    canceled_at: Optional[datetime]
    ended_at: Optional[datetime]
    cancel_at_period_end: bool
    created_at: datetime
    updated_at: datetime

    plan: Optional[PlanResponse] = None
    customer: Optional[CustomerResponse] = None

    model_config = ConfigDict(from_attributes=True)
