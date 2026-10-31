import enum
import uuid
from datetime import datetime, date
from decimal import Decimal
from typing import Optional, List

from sqlalchemy import (
    Column,
    String,
    Boolean,
    Integer,
    Numeric,
    DateTime,
    Date,
    ForeignKey,
    ForeignKeyConstraint,
    UniqueConstraint,
    CheckConstraint,
    Index,
    Text,
    Enum as SAEnum,
    func,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship

from app.database import Base


# ============================================================================
# Enums
# ============================================================================

class UserRole(str, enum.Enum):
    ADMIN = "admin"
    STAFF = "staff"
    CUSTOMER = "customer"


class BillingInterval(str, enum.Enum):
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    YEARLY = "yearly"


class SubscriptionStatus(str, enum.Enum):
    TRIALING = "trialing"
    ACTIVE = "active"
    PAST_DUE = "past_due"
    PAUSED = "paused"
    CANCELED = "canceled"
    EXPIRED = "expired"


class InvoiceStatus(str, enum.Enum):
    DRAFT = "draft"
    OPEN = "open"
    PARTIALLY_PAID = "partially_paid"
    PAID = "paid"
    OVERDUE = "overdue"
    VOID = "void"
    UNCOLLECTIBLE = "uncollectible"


class PaymentStatus(str, enum.Enum):
    PENDING = "pending"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    REFUNDED = "refunded"
    PARTIALLY_REFUNDED = "partially_refunded"


class QuotationStatus(str, enum.Enum):
    DRAFT = "draft"
    QUOTATION = "quotation"
    CONFIRMED = "confirmed"
    ACTIVE = "active"
    CLOSED = "closed"
    CANCELED = "canceled"
    EXPIRED = "expired"


class AdjustmentType(str, enum.Enum):
    PERCENTAGE = "percentage"
    FIXED = "fixed"


class GuardianFindingStatus(str, enum.Enum):
    OPEN = "open"
    ACKNOWLEDGED = "acknowledged"
    RESOLVED = "resolved"


# ============================================================================
# Models
# ============================================================================

class User(Base):
    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email = Column(String(255), unique=True, nullable=False, index=True)
    hashed_password = Column(String(255), nullable=False)
    full_name = Column(String(255), nullable=False)
    role = Column(
        SAEnum(
            UserRole,
            name="user_role_enum",
            native_enum=True,
            values_callable=lambda x: [e.value for e in x],
        ),
        nullable=False,
        default=UserRole.CUSTOMER,
        index=True,
    )
    is_active = Column(Boolean, default=True, nullable=False)
    is_verified = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    # Relationships
    customer = relationship("Customer", back_populates="user", uselist=False)
    audit_logs = relationship("AuditLog", back_populates="user")


class Customer(Base):
    __tablename__ = "customers"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        unique=True,
        nullable=True,
        index=True,
    )
    name = Column(String(255), nullable=False, index=True)
    email = Column(String(255), nullable=False, index=True)
    phone = Column(String(50), nullable=True)
    billing_address = Column(Text, nullable=True)
    currency = Column(String(3), default="USD", nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    is_archived = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    # Relationships
    user = relationship("User", back_populates="customer")
    subscriptions = relationship("Subscription", back_populates="customer")
    invoices = relationship("Invoice", back_populates="customer")
    payments = relationship("Payment", back_populates="customer")


class Product(Base):
    __tablename__ = "products"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(255), nullable=False, index=True)
    code = Column(String(50), unique=True, nullable=False, index=True)
    description = Column(Text, nullable=True)
    product_type = Column(String(50), nullable=False, default="service")
    sales_price = Column(Numeric(12, 2), nullable=False, default=Decimal("0.00"))
    cost_price = Column(Numeric(12, 2), nullable=False, default=Decimal("0.00"))
    notes = Column(Text, nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)
    is_archived = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    # Relationships
    plans = relationship(
        "SubscriptionPlan", back_populates="product", cascade="all, delete-orphan"
    )
    variants = relationship(
        "ProductVariant", back_populates="product", cascade="all, delete-orphan"
    )
    invoice_items = relationship("InvoiceItem", back_populates="product")

    __table_args__ = (
        CheckConstraint("sales_price >= 0", name="ck_product_sales_price_non_negative"),
        CheckConstraint("cost_price >= 0", name="ck_product_cost_price_non_negative"),
    )


class ProductVariant(Base):
    __tablename__ = "product_variants"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    product_id = Column(
        UUID(as_uuid=True),
        ForeignKey("products.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    attribute_name = Column(String(100), nullable=False)
    value = Column(String(100), nullable=False)
    sku = Column(String(80), unique=True, nullable=True, index=True)
    price_modifier = Column(
        Numeric(12, 2), nullable=False, default=Decimal("0.00")
    )
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint(
            "product_id",
            "attribute_name",
            "value",
            name="uq_product_variant_attribute_value",
        ),
    )

    product = relationship("Product", back_populates="variants")


class SubscriptionPlan(Base):
    __tablename__ = "subscription_plans"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    product_id = Column(
        UUID(as_uuid=True),
        ForeignKey("products.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    name = Column(String(255), nullable=False)
    code = Column(String(50), unique=True, nullable=False, index=True)
    description = Column(Text, nullable=True)
    billing_interval = Column(
        SAEnum(
            BillingInterval,
            name="billing_interval_enum",
            native_enum=True,
            values_callable=lambda x: [e.value for e in x],
        ),
        nullable=False,
    )
    interval_count = Column(Integer, default=1, nullable=False)
    price = Column(Numeric(12, 2), nullable=False)
    currency = Column(String(3), default="USD", nullable=False)
    trial_period_days = Column(Integer, default=0, nullable=False)
    minimum_quantity = Column(Integer, default=1, nullable=False)
    available_from = Column(Date, nullable=True, index=True)
    available_until = Column(Date, nullable=True, index=True)
    payment_terms_days = Column(Integer, default=0, nullable=False)
    auto_close_on_end = Column(Boolean, default=False, nullable=False)
    allow_early_close = Column(Boolean, default=True, nullable=False)
    allow_pause = Column(Boolean, default=True, nullable=False)
    allow_renewal = Column(Boolean, default=True, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    is_archived = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    __table_args__ = (
        CheckConstraint("price >= 0", name="ck_plan_price_non_negative"),
        CheckConstraint("interval_count >= 1", name="ck_plan_interval_count_positive"),
        CheckConstraint("trial_period_days >= 0", name="ck_plan_trial_days_non_negative"),
        CheckConstraint("minimum_quantity >= 1", name="ck_plan_minimum_quantity_positive"),
        CheckConstraint("payment_terms_days >= 0", name="ck_plan_payment_terms_non_negative"),
        CheckConstraint(
            "available_until IS NULL OR available_from IS NULL OR available_until >= available_from",
            name="ck_plan_availability_dates_valid",
        ),
    )

    # Relationships
    product = relationship("Product", back_populates="plans")
    subscriptions = relationship("Subscription", back_populates="plan")


class Subscription(Base):
    __tablename__ = "subscriptions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    subscription_number = Column(String(50), unique=True, nullable=True, index=True)
    customer_id = Column(
        UUID(as_uuid=True),
        ForeignKey("customers.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    plan_id = Column(
        UUID(as_uuid=True),
        ForeignKey("subscription_plans.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    quotation_id = Column(
        UUID(as_uuid=True), ForeignKey("quotations.id", ondelete="SET NULL"), nullable=True, index=True
    )
    quantity = Column(Integer, default=1, nullable=False)
    payment_terms_days = Column(Integer, default=0, nullable=False)
    expiry_date = Column(Date, nullable=True)
    status = Column(
        SAEnum(
            SubscriptionStatus,
            name="subscription_status_enum",
            native_enum=True,
            values_callable=lambda x: [e.value for e in x],
        ),
        nullable=False,
        default=SubscriptionStatus.ACTIVE,
        index=True,
    )
    start_date = Column(DateTime(timezone=True), nullable=False)
    current_period_start = Column(DateTime(timezone=True), nullable=False)
    current_period_end = Column(DateTime(timezone=True), nullable=False, index=True)
    trial_start = Column(DateTime(timezone=True), nullable=True)
    trial_end = Column(DateTime(timezone=True), nullable=True)
    canceled_at = Column(DateTime(timezone=True), nullable=True)
    ended_at = Column(DateTime(timezone=True), nullable=True)
    cancel_at_period_end = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    # Relationships
    customer = relationship("Customer", back_populates="subscriptions")
    plan = relationship("SubscriptionPlan", back_populates="subscriptions")
    invoices = relationship("Invoice", back_populates="subscription")

    __table_args__ = (
        CheckConstraint("quantity >= 1", name="ck_subscription_quantity_positive"),
        CheckConstraint("payment_terms_days >= 0", name="ck_subscription_payment_terms_non_negative"),
    )


class Invoice(Base):
    __tablename__ = "invoices"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    invoice_number = Column(String(50), unique=True, nullable=False, index=True)
    customer_id = Column(
        UUID(as_uuid=True),
        ForeignKey("customers.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    subscription_id = Column(
        UUID(as_uuid=True),
        ForeignKey("subscriptions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    billing_key = Column(String(150), unique=True, nullable=True, index=True)
    status = Column(
        SAEnum(
            InvoiceStatus,
            name="invoice_status_enum",
            native_enum=True,
            values_callable=lambda x: [e.value for e in x],
        ),
        nullable=False,
        default=InvoiceStatus.DRAFT,
        index=True,
    )
    subtotal = Column(Numeric(12, 2), nullable=False)
    tax_amount = Column(Numeric(12, 2), default=Decimal("0.00"), nullable=False)
    discount_amount = Column(Numeric(12, 2), default=Decimal("0.00"), nullable=False)
    total_amount = Column(Numeric(12, 2), nullable=False)
    amount_paid = Column(Numeric(12, 2), default=Decimal("0.00"), nullable=False)
    amount_due = Column(Numeric(12, 2), nullable=False)
    currency = Column(String(3), default="USD", nullable=False)
    issue_date = Column(Date, nullable=False, index=True)
    due_date = Column(Date, nullable=False, index=True)
    paid_at = Column(DateTime(timezone=True), nullable=True)
    voided_at = Column(DateTime(timezone=True), nullable=True)
    sent_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint("id", "customer_id", name="uq_invoices_id_customer_id"),
        CheckConstraint("subtotal >= 0", name="ck_invoice_subtotal_non_negative"),
        CheckConstraint("tax_amount >= 0", name="ck_invoice_tax_non_negative"),
        CheckConstraint("discount_amount >= 0", name="ck_invoice_discount_non_negative"),
        CheckConstraint("total_amount >= 0", name="ck_invoice_total_non_negative"),
        CheckConstraint("amount_paid >= 0", name="ck_invoice_amount_paid_non_negative"),
        CheckConstraint("amount_due >= 0", name="ck_invoice_amount_due_non_negative"),
        CheckConstraint("amount_paid <= total_amount", name="ck_invoice_amount_paid_lte_total"),
    )

    # Relationships
    customer = relationship("Customer", back_populates="invoices")
    subscription = relationship("Subscription", back_populates="invoices")
    items = relationship(
        "InvoiceItem", back_populates="invoice", cascade="all, delete-orphan"
    )
    payments = relationship("Payment", back_populates="invoice", overlaps="customer,payments")

    @property
    def is_overdue(self) -> bool:
        """Calculates if the invoice is overdue based on current date, due date, amount due, and status."""
        if self.status in (InvoiceStatus.PAID, InvoiceStatus.VOID, InvoiceStatus.UNCOLLECTIBLE):
            return False
        if self.amount_due <= 0:
            return False
        return date.today() > self.due_date


class InvoiceItem(Base):
    __tablename__ = "invoice_items"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    invoice_id = Column(
        UUID(as_uuid=True),
        ForeignKey("invoices.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    product_id = Column(
        UUID(as_uuid=True),
        ForeignKey("products.id", ondelete="SET NULL"),
        nullable=True,
    )
    plan_id = Column(
        UUID(as_uuid=True),
        ForeignKey("subscription_plans.id", ondelete="SET NULL"),
        nullable=True,
    )
    description = Column(String(255), nullable=False)
    quantity = Column(Integer, default=1, nullable=False)
    unit_price = Column(Numeric(12, 2), nullable=False)
    amount = Column(Numeric(12, 2), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        CheckConstraint("quantity > 0", name="ck_invoice_item_quantity_positive"),
        CheckConstraint("unit_price >= 0", name="ck_invoice_item_unit_price_non_negative"),
        CheckConstraint("amount >= 0", name="ck_invoice_item_amount_non_negative"),
    )

    # Relationships
    invoice = relationship("Invoice", back_populates="items")
    product = relationship("Product", back_populates="invoice_items")
    plan = relationship("SubscriptionPlan")


class Payment(Base):
    __tablename__ = "payments"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    payment_number = Column(String(50), unique=True, nullable=False, index=True)
    invoice_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    customer_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    amount = Column(Numeric(12, 2), nullable=False)
    currency = Column(String(3), default="USD", nullable=False)
    status = Column(
        SAEnum(
            PaymentStatus,
            name="payment_status_enum",
            native_enum=True,
            values_callable=lambda x: [e.value for e in x],
        ),
        nullable=False,
        default=PaymentStatus.PENDING,
        index=True,
    )
    payment_method = Column(String(50), nullable=False)
    transaction_reference = Column(String(255), nullable=True, index=True)
    idempotency_key = Column(String(255), unique=True, nullable=True, index=True)
    failure_reason = Column(Text, nullable=True)
    payment_date = Column(Date, nullable=True)
    note = Column(Text, nullable=True)
    refunded_amount = Column(Numeric(12, 2), default=Decimal("0.00"), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    __table_args__ = (
        # Enforces customer_id consistency between payment and invoice at the database level
        ForeignKeyConstraint(
            ["invoice_id", "customer_id"],
            ["invoices.id", "invoices.customer_id"],
            name="fk_payments_invoice_customer",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["customer_id"],
            ["customers.id"],
            name="fk_payments_customer_id",
            ondelete="RESTRICT",
        ),
        CheckConstraint("amount > 0", name="ck_payment_amount_positive"),
        CheckConstraint("refunded_amount >= 0", name="ck_payment_refunded_non_negative"),
        CheckConstraint("refunded_amount <= amount", name="ck_payment_refunded_lte_amount"),
    )

    # Relationships
    invoice = relationship(
        "Invoice",
        back_populates="payments",
        foreign_keys=[invoice_id],
        overlaps="customer,payments",
    )
    customer = relationship(
        "Customer",
        back_populates="payments",
        foreign_keys=[customer_id],
        overlaps="invoice,payments",
    )


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    action = Column(String(50), nullable=False, index=True)
    entity_type = Column(String(50), nullable=False, index=True)
    entity_id = Column(String(100), nullable=False, index=True)
    old_values = Column(JSONB, nullable=True)
    new_values = Column(JSONB, nullable=True)
    ip_address = Column(String(45), nullable=True)
    user_agent = Column(String(255), nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        index=True,
    )

    # Relationships
    user = relationship("User", back_populates="audit_logs")


class TaxRule(Base):
    __tablename__ = "tax_rules"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(120), nullable=False)
    code = Column(String(50), unique=True, nullable=False, index=True)
    adjustment_type = Column(SAEnum(AdjustmentType, name="adjustment_type_enum", values_callable=lambda x: [e.value for e in x]), nullable=False)
    value = Column(Numeric(12, 4), nullable=False)
    product_id = Column(UUID(as_uuid=True), ForeignKey("products.id", ondelete="SET NULL"), nullable=True, index=True)
    valid_from = Column(Date, nullable=True)
    valid_until = Column(Date, nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    __table_args__ = (
        CheckConstraint("value >= 0", name="ck_tax_rule_value_non_negative"),
        CheckConstraint("valid_until IS NULL OR valid_from IS NULL OR valid_until >= valid_from", name="ck_tax_rule_dates_valid"),
    )


class DiscountRule(Base):
    __tablename__ = "discount_rules"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(120), nullable=False)
    code = Column(String(50), unique=True, nullable=False, index=True)
    adjustment_type = Column(SAEnum(AdjustmentType, name="adjustment_type_enum", values_callable=lambda x: [e.value for e in x], create_type=False), nullable=False)
    value = Column(Numeric(12, 4), nullable=False)
    product_id = Column(UUID(as_uuid=True), ForeignKey("products.id", ondelete="SET NULL"), nullable=True, index=True)
    subscription_id = Column(UUID(as_uuid=True), ForeignKey("subscriptions.id", ondelete="CASCADE"), nullable=True, index=True)
    minimum_purchase = Column(Numeric(12, 2), nullable=False, default=Decimal("0.00"))
    minimum_quantity = Column(Integer, nullable=False, default=1)
    valid_from = Column(Date, nullable=True)
    valid_until = Column(Date, nullable=True)
    usage_limit = Column(Integer, nullable=True)
    times_redeemed = Column(Integer, nullable=False, default=0)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    __table_args__ = (
        CheckConstraint("value >= 0", name="ck_discount_rule_value_non_negative"),
        CheckConstraint("minimum_purchase >= 0", name="ck_discount_minimum_purchase_non_negative"),
        CheckConstraint("minimum_quantity >= 1", name="ck_discount_minimum_quantity_positive"),
        CheckConstraint("usage_limit IS NULL OR usage_limit >= 1", name="ck_discount_usage_limit_positive"),
        CheckConstraint("times_redeemed >= 0", name="ck_discount_times_redeemed_non_negative"),
        CheckConstraint("valid_until IS NULL OR valid_from IS NULL OR valid_until >= valid_from", name="ck_discount_rule_dates_valid"),
    )


class QuotationTemplate(Base):
    __tablename__ = "quotation_templates"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(150), nullable=False)
    code = Column(String(50), unique=True, nullable=False, index=True)
    valid_days = Column(Integer, nullable=False, default=30)
    payment_terms_days = Column(Integer, nullable=False, default=0)
    notes = Column(Text, nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    lines = relationship("QuotationTemplateLine", back_populates="template", cascade="all, delete-orphan")
    __table_args__ = (
        CheckConstraint("valid_days >= 1", name="ck_quote_template_valid_days_positive"),
        CheckConstraint("payment_terms_days >= 0", name="ck_quote_template_payment_terms_non_negative"),
    )


class QuotationTemplateLine(Base):
    __tablename__ = "quotation_template_lines"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    template_id = Column(UUID(as_uuid=True), ForeignKey("quotation_templates.id", ondelete="CASCADE"), nullable=False, index=True)
    product_id = Column(UUID(as_uuid=True), ForeignKey("products.id", ondelete="RESTRICT"), nullable=False)
    plan_id = Column(UUID(as_uuid=True), ForeignKey("subscription_plans.id", ondelete="RESTRICT"), nullable=False)
    description = Column(String(255), nullable=False)
    quantity = Column(Integer, nullable=False, default=1)
    unit_price = Column(Numeric(12, 2), nullable=False)
    template = relationship("QuotationTemplate", back_populates="lines")
    __table_args__ = (CheckConstraint("quantity >= 1", name="ck_quote_template_line_quantity_positive"),)


class Quotation(Base):
    __tablename__ = "quotations"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    quotation_number = Column(String(50), unique=True, nullable=False, index=True)
    customer_id = Column(UUID(as_uuid=True), ForeignKey("customers.id", ondelete="RESTRICT"), nullable=False, index=True)
    template_id = Column(UUID(as_uuid=True), ForeignKey("quotation_templates.id", ondelete="SET NULL"), nullable=True)
    status = Column(SAEnum(QuotationStatus, name="quotation_status_enum", values_callable=lambda x: [e.value for e in x]), nullable=False, default=QuotationStatus.DRAFT, index=True)
    valid_until = Column(Date, nullable=False)
    payment_terms_days = Column(Integer, nullable=False, default=0)
    currency = Column(String(3), nullable=False, default="USD")
    subtotal = Column(Numeric(12, 2), nullable=False)
    tax_amount = Column(Numeric(12, 2), nullable=False, default=Decimal("0.00"))
    discount_amount = Column(Numeric(12, 2), nullable=False, default=Decimal("0.00"))
    total_amount = Column(Numeric(12, 2), nullable=False)
    notes = Column(Text, nullable=True)
    confirmed_at = Column(DateTime(timezone=True), nullable=True)
    closed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    lines = relationship("QuotationLine", back_populates="quotation", cascade="all, delete-orphan")
    __table_args__ = (
        CheckConstraint("payment_terms_days >= 0", name="ck_quotation_payment_terms_non_negative"),
        CheckConstraint("total_amount >= 0", name="ck_quotation_total_non_negative"),
    )


class QuotationLine(Base):
    __tablename__ = "quotation_lines"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    quotation_id = Column(UUID(as_uuid=True), ForeignKey("quotations.id", ondelete="CASCADE"), nullable=False, index=True)
    product_id = Column(UUID(as_uuid=True), ForeignKey("products.id", ondelete="RESTRICT"), nullable=False)
    plan_id = Column(UUID(as_uuid=True), ForeignKey("subscription_plans.id", ondelete="RESTRICT"), nullable=False)
    description = Column(String(255), nullable=False)
    quantity = Column(Integer, nullable=False)
    unit_price = Column(Numeric(12, 2), nullable=False)
    amount = Column(Numeric(12, 2), nullable=False)
    quotation = relationship("Quotation", back_populates="lines")
    __table_args__ = (CheckConstraint("quantity >= 1", name="ck_quotation_line_quantity_positive"),)


class GuardianFinding(Base):
    __tablename__ = "guardian_findings"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    fingerprint = Column(String(64), unique=True, nullable=False, index=True)
    rule = Column(String(80), nullable=False, index=True)
    title = Column(String(255), nullable=False)
    severity = Column(String(20), nullable=False, index=True)
    certainty = Column(String(20), nullable=False)
    affected_ids = Column(JSONB, nullable=False)
    evidence = Column(Text, nullable=False)
    recommended_action = Column(Text, nullable=False)
    status = Column(SAEnum(GuardianFindingStatus, name="guardian_finding_status_enum", values_callable=lambda x: [e.value for e in x]), nullable=False, default=GuardianFindingStatus.OPEN, index=True)
    detected_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    resolved_at = Column(DateTime(timezone=True), nullable=True)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
