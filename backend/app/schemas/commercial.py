import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models import AdjustmentType, GuardianFindingStatus, QuotationStatus


class RuleBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    code: str = Field(..., min_length=1, max_length=50)
    adjustment_type: AdjustmentType
    value: Decimal = Field(..., ge=0)
    product_id: Optional[uuid.UUID] = None
    valid_from: Optional[date] = None
    valid_until: Optional[date] = None
    is_active: bool = True

    @model_validator(mode="after")
    def validate_dates(self):
        if self.valid_from and self.valid_until and self.valid_until < self.valid_from:
            raise ValueError("valid_until must be on or after valid_from")
        return self


class TaxRuleCreate(RuleBase):
    pass


class TaxRuleResponse(RuleBase):
    id: uuid.UUID
    created_at: datetime
    updated_at: datetime
    model_config = ConfigDict(from_attributes=True)


class DiscountRuleCreate(RuleBase):
    subscription_id: Optional[uuid.UUID] = None
    minimum_purchase: Decimal = Field(default=Decimal("0.00"), ge=0)
    minimum_quantity: int = Field(default=1, ge=1)
    usage_limit: Optional[int] = Field(default=None, ge=1)


class DiscountRuleResponse(DiscountRuleCreate):
    id: uuid.UUID
    times_redeemed: int
    created_at: datetime
    updated_at: datetime
    model_config = ConfigDict(from_attributes=True)


class QuoteLineInput(BaseModel):
    product_id: uuid.UUID
    plan_id: uuid.UUID
    description: str = Field(..., min_length=1, max_length=255)
    quantity: int = Field(default=1, ge=1)
    unit_price: Decimal = Field(..., ge=0)


class QuoteLineResponse(QuoteLineInput):
    id: uuid.UUID
    amount: Optional[Decimal] = None
    model_config = ConfigDict(from_attributes=True)


class TemplateCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=150)
    code: str = Field(..., min_length=1, max_length=50)
    valid_days: int = Field(default=30, ge=1)
    payment_terms_days: int = Field(default=0, ge=0)
    notes: Optional[str] = None
    lines: List[QuoteLineInput] = Field(..., min_length=1)


class TemplateResponse(BaseModel):
    id: uuid.UUID
    name: str
    code: str
    valid_days: int
    payment_terms_days: int
    notes: Optional[str]
    is_active: bool
    lines: List[QuoteLineResponse]
    created_at: datetime
    updated_at: datetime
    model_config = ConfigDict(from_attributes=True)


class QuotationCreate(BaseModel):
    customer_id: uuid.UUID
    template_id: Optional[uuid.UUID] = None
    valid_days: int = Field(default=30, ge=1)
    payment_terms_days: int = Field(default=0, ge=0)
    currency: str = Field(default="USD", min_length=3, max_length=3)
    notes: Optional[str] = None
    lines: List[QuoteLineInput] = Field(default_factory=list)


class QuotationResponse(BaseModel):
    id: uuid.UUID
    quotation_number: str
    customer_id: uuid.UUID
    template_id: Optional[uuid.UUID]
    status: QuotationStatus
    valid_until: date
    payment_terms_days: int
    currency: str
    subtotal: Decimal
    tax_amount: Decimal
    discount_amount: Decimal
    total_amount: Decimal
    notes: Optional[str]
    lines: List[QuoteLineResponse]
    confirmed_at: Optional[datetime]
    closed_at: Optional[datetime]
    created_at: datetime
    updated_at: datetime
    model_config = ConfigDict(from_attributes=True)


class BillingRunRequest(BaseModel):
    as_of: date = Field(default_factory=date.today)


class GuardianStatusUpdate(BaseModel):
    status: GuardianFindingStatus
