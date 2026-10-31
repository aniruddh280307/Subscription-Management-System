import uuid
from datetime import datetime
from decimal import Decimal
from typing import List, Optional
from pydantic import BaseModel, Field, ConfigDict


class ProductVariantCreateRequest(BaseModel):
    attribute_name: str = Field(..., min_length=1, max_length=100)
    value: str = Field(..., min_length=1, max_length=100)
    sku: Optional[str] = Field(default=None, min_length=1, max_length=80)
    price_modifier: Decimal = Field(default=Decimal("0.00"))
    is_active: bool = True


class ProductVariantUpdateRequest(BaseModel):
    attribute_name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    value: Optional[str] = Field(default=None, min_length=1, max_length=100)
    sku: Optional[str] = Field(default=None, min_length=1, max_length=80)
    price_modifier: Optional[Decimal] = None
    is_active: Optional[bool] = None


class ProductVariantResponse(BaseModel):
    id: uuid.UUID
    product_id: uuid.UUID
    attribute_name: str
    value: str
    sku: Optional[str]
    price_modifier: Decimal
    is_active: bool
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ProductCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    code: str = Field(..., min_length=1, max_length=50)
    description: Optional[str] = None
    product_type: str = Field(default="service", min_length=1, max_length=50)
    sales_price: Decimal = Field(default=Decimal("0.00"), ge=0)
    cost_price: Decimal = Field(default=Decimal("0.00"), ge=0)
    notes: Optional[str] = None
    variants: List[ProductVariantCreateRequest] = Field(default_factory=list)
    is_active: bool = True


class ProductUpdateRequest(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    code: Optional[str] = Field(None, min_length=1, max_length=50)
    description: Optional[str] = None
    product_type: Optional[str] = Field(default=None, min_length=1, max_length=50)
    sales_price: Optional[Decimal] = Field(default=None, ge=0)
    cost_price: Optional[Decimal] = Field(default=None, ge=0)
    notes: Optional[str] = None
    is_active: Optional[bool] = None
    is_archived: Optional[bool] = None


class ProductResponse(BaseModel):
    id: uuid.UUID
    name: str
    code: str
    description: Optional[str]
    product_type: str
    sales_price: Decimal
    cost_price: Decimal
    notes: Optional[str]
    variants: List[ProductVariantResponse] = Field(default_factory=list)
    is_active: bool
    is_archived: bool
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
