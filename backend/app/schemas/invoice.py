"""
Pydantic schemas for the Invoices and Payments module.

Design decisions:
- InvoiceCreateRequest supports optional subscription_id (invoices can be standalone).
- Line items carry a price snapshot at creation time (unit_price stored on InvoiceItem).
- All monetary values use Decimal / str round-trip to avoid float precision issues.
- Payment recording does NOT integrate a payment gateway; it records manual/external payments.
- Refunds are partial or full against a specific succeeded payment.
- Idempotency key is optional but when supplied must be unique across the system.
"""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import List, Optional

from pydantic import BaseModel, Field, ConfigDict, field_validator

from app.models import InvoiceStatus, PaymentStatus


# ---------------------------------------------------------------------------
# Invoice line-item schemas
# ---------------------------------------------------------------------------


class InvoiceItemCreateRequest(BaseModel):
    product_id: Optional[uuid.UUID] = None
    plan_id: Optional[uuid.UUID] = None
    description: str = Field(..., min_length=1, max_length=255)
    quantity: int = Field(default=1, ge=1)
    unit_price: Decimal = Field(..., ge=Decimal("0"))

    @field_validator("unit_price", mode="before")
    @classmethod
    def coerce_unit_price(cls, v):
        return Decimal(str(v))


class InvoiceItemResponse(BaseModel):
    id: uuid.UUID
    invoice_id: uuid.UUID
    product_id: Optional[uuid.UUID]
    plan_id: Optional[uuid.UUID]
    description: str
    quantity: int
    unit_price: Decimal
    amount: Decimal
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


# ---------------------------------------------------------------------------
# Invoice schemas
# ---------------------------------------------------------------------------


class InvoiceCreateRequest(BaseModel):
    customer_id: uuid.UUID
    subscription_id: Optional[uuid.UUID] = None
    issue_date: date
    due_date: date
    currency: str = Field(default="USD", min_length=3, max_length=3)
    tax_amount: Decimal = Field(default=Decimal("0.00"), ge=Decimal("0"))
    discount_amount: Decimal = Field(default=Decimal("0.00"), ge=Decimal("0"))
    items: List[InvoiceItemCreateRequest] = Field(..., min_length=1)

    @field_validator("tax_amount", "discount_amount", mode="before")
    @classmethod
    def coerce_decimal(cls, v):
        return Decimal(str(v))

    @field_validator("due_date")
    @classmethod
    def due_date_not_before_issue(cls, v, info):
        issue = info.data.get("issue_date")
        if issue and v < issue:
            raise ValueError("due_date must be on or after issue_date")
        return v


class InvoiceUpdateRequest(BaseModel):
    """Only DRAFT invoices may be updated."""
    due_date: Optional[date] = None
    tax_amount: Optional[Decimal] = Field(default=None, ge=Decimal("0"))
    discount_amount: Optional[Decimal] = Field(default=None, ge=Decimal("0"))

    @field_validator("tax_amount", "discount_amount", mode="before")
    @classmethod
    def coerce_decimal(cls, v):
        if v is None:
            return v
        return Decimal(str(v))


class InvoiceFinalizeRequest(BaseModel):
    """Transition a DRAFT invoice to OPEN."""
    pass


class InvoiceVoidRequest(BaseModel):
    reason: Optional[str] = Field(default=None, max_length=500)


class InvoiceMarkUncollectibleRequest(BaseModel):
    reason: Optional[str] = Field(default=None, max_length=500)


class InvoiceResponse(BaseModel):
    id: uuid.UUID
    invoice_number: str
    customer_id: uuid.UUID
    subscription_id: Optional[uuid.UUID]
    billing_key: Optional[str]
    status: InvoiceStatus
    subtotal: Decimal
    tax_amount: Decimal
    discount_amount: Decimal
    total_amount: Decimal
    amount_paid: Decimal
    amount_due: Decimal
    currency: str
    issue_date: date
    due_date: date
    paid_at: Optional[datetime]
    voided_at: Optional[datetime]
    sent_at: Optional[datetime]
    created_at: datetime
    updated_at: datetime
    items: List[InvoiceItemResponse] = []

    model_config = ConfigDict(from_attributes=True)


# ---------------------------------------------------------------------------
# Payment schemas
# ---------------------------------------------------------------------------


class PaymentRecordRequest(BaseModel):
    """Record an external / manual payment against an invoice."""
    invoice_id: uuid.UUID
    amount: Decimal = Field(..., gt=Decimal("0"))
    currency: str = Field(default="USD", min_length=3, max_length=3)
    payment_method: str = Field(..., min_length=1, max_length=50)
    transaction_reference: Optional[str] = Field(default=None, max_length=255)
    idempotency_key: Optional[str] = Field(default=None, max_length=255)
    failure_reason: Optional[str] = None
    payment_date: Optional[date] = None
    note: Optional[str] = Field(default=None, max_length=1000)
    # status must be succeeded or failed when recorded manually
    payment_status: PaymentStatus = Field(default=PaymentStatus.SUCCEEDED)

    @field_validator("amount", mode="before")
    @classmethod
    def coerce_amount(cls, v):
        return Decimal(str(v))


class PaymentRefundRequest(BaseModel):
    refund_amount: Decimal = Field(..., gt=Decimal("0"))
    reason: Optional[str] = Field(default=None, max_length=500)

    @field_validator("refund_amount", mode="before")
    @classmethod
    def coerce_amount(cls, v):
        return Decimal(str(v))


class PaymentResponse(BaseModel):
    id: uuid.UUID
    payment_number: str
    invoice_id: uuid.UUID
    customer_id: uuid.UUID
    amount: Decimal
    currency: str
    status: PaymentStatus
    payment_method: str
    transaction_reference: Optional[str]
    idempotency_key: Optional[str]
    failure_reason: Optional[str]
    payment_date: Optional[date]
    note: Optional[str]
    refunded_amount: Decimal
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
