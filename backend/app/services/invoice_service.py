"""
Business logic for the Invoices and Payments module.

Key design rules (from PROJECT_CONTEXT.md):
- All monetary values use Decimal (Numeric in DB).
- Use DB transactions for financial operations.
- Preserve financial history — no hard deletes of paid invoices.
- Invoice overdue status is computed (is_overdue property on model) not stored directly.
- Idempotency: duplicate payment attempts with the same idempotency_key return the
  existing payment record without re-processing.
- No real payment gateway integration — records external/manual payment events only.
- amount_paid and amount_due on Invoice stay consistent with succeeded payments + refunds.
- Partial payments update invoice to PARTIALLY_PAID; full payment sets PAID + paid_at.
- Refunds decrease amount_paid and increase amount_due, potentially reopening invoice.
- Voiding or marking uncollectible is only allowed on non-paid invoices.
"""

import uuid
import re
from datetime import datetime, timezone, date
from decimal import Decimal
from typing import Optional

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.models import (
    Invoice,
    InvoiceItem,
    InvoiceStatus,
    Payment,
    PaymentStatus,
    Customer,
    Subscription,
    SubscriptionPlan,
    Product,
    User,
    UserRole,
    AuditLog,
)


# ---------------------------------------------------------------------------
# Sequence helpers
# ---------------------------------------------------------------------------

def _next_invoice_number(db: Session) -> str:
    """
    Generate a sequential invoice number of the form INV-YYYYMM-NNNNNN.
    Counts existing invoices to determine the next sequence number.
    This is safe for low-concurrency scenarios; a proper DB sequence should
    be used for very high-throughput production deployments.
    """
    today = datetime.now(timezone.utc)
    prefix = f"INV-{today.strftime('%Y%m')}-"
    # Count invoices created this month with this prefix
    count = (
        db.query(Invoice)
        .filter(Invoice.invoice_number.like(f"{prefix}%"))
        .count()
    )
    return f"{prefix}{count + 1:06d}"


def _next_payment_number(db: Session) -> str:
    """Generate a sequential payment number PAY-YYYYMM-NNNNNN."""
    today = datetime.now(timezone.utc)
    prefix = f"PAY-{today.strftime('%Y%m')}-"
    count = (
        db.query(Payment)
        .filter(Payment.payment_number.like(f"{prefix}%"))
        .count()
    )
    return f"{prefix}{count + 1:06d}"


# ---------------------------------------------------------------------------
# Audit helper
# ---------------------------------------------------------------------------

def _audit(
    db: Session,
    user: Optional[User],
    action: str,
    entity_type: str,
    entity_id: uuid.UUID,
    old_values: Optional[dict] = None,
    new_values: Optional[dict] = None,
) -> None:
    log = AuditLog(
        user_id=user.id if user else None,
        action=action,
        entity_type=entity_type,
        entity_id=str(entity_id),
        old_values=old_values,
        new_values=new_values,
    )
    db.add(log)


# ---------------------------------------------------------------------------
# Invoice totals computation
# ---------------------------------------------------------------------------

def _compute_totals(
    items: list,
    tax_amount: Decimal,
    discount_amount: Decimal,
) -> tuple[Decimal, Decimal]:
    """Return (subtotal, total_amount) from a list of (quantity, unit_price) tuples or InvoiceItem objects."""
    subtotal = Decimal("0.00")
    for item in items:
        if isinstance(item, dict):
            subtotal += Decimal(str(item["unit_price"])) * item["quantity"]
        else:
            subtotal += item.unit_price * item.quantity
    total = max(subtotal + tax_amount - discount_amount, Decimal("0.00"))
    return subtotal, total


# ---------------------------------------------------------------------------
# Invoice CRUD
# ---------------------------------------------------------------------------

def create_invoice(
    db: Session,
    current_user: User,
    customer_id: uuid.UUID,
    subscription_id: Optional[uuid.UUID],
    issue_date: date,
    due_date: date,
    currency: str,
    tax_amount: Decimal,
    discount_amount: Decimal,
    items_data: list,
) -> Invoice:
    """Create a DRAFT invoice with line items. Only admin/staff may create invoices."""
    # Verify customer exists and is active
    customer = db.query(Customer).filter(Customer.id == customer_id).first()
    if not customer:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Customer '{customer_id}' not found",
        )
    if customer.is_archived or not customer.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot create invoice for an inactive or archived customer",
        )

    # Optionally verify subscription belongs to customer
    if subscription_id:
        sub = db.query(Subscription).filter(Subscription.id == subscription_id).first()
        if not sub:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Subscription '{subscription_id}' not found",
            )
        if sub.customer_id != customer_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Subscription does not belong to the specified customer",
            )

    # Build line items (price snapshot captured at creation time)
    invoice_items = []
    for item_data in items_data:
        unit_price = Decimal(str(item_data.unit_price))
        qty = item_data.quantity
        amount = (unit_price * qty).quantize(Decimal("0.01"))
        invoice_items.append(
            InvoiceItem(
                product_id=item_data.product_id,
                plan_id=item_data.plan_id,
                description=item_data.description,
                quantity=qty,
                unit_price=unit_price,
                amount=amount,
            )
        )

    subtotal, total_amount = _compute_totals(invoice_items, tax_amount, discount_amount)

    invoice_number = _next_invoice_number(db)
    invoice = Invoice(
        invoice_number=invoice_number,
        customer_id=customer_id,
        subscription_id=subscription_id,
        status=InvoiceStatus.DRAFT,
        subtotal=subtotal.quantize(Decimal("0.01")),
        tax_amount=tax_amount.quantize(Decimal("0.01")),
        discount_amount=discount_amount.quantize(Decimal("0.01")),
        total_amount=total_amount.quantize(Decimal("0.01")),
        amount_paid=Decimal("0.00"),
        amount_due=total_amount.quantize(Decimal("0.01")),
        currency=currency.upper(),
        issue_date=issue_date,
        due_date=due_date,
    )
    db.add(invoice)
    db.flush()  # Get invoice.id before adding items

    for item in invoice_items:
        item.invoice_id = invoice.id
        db.add(item)

    _audit(
        db, current_user, "INVOICE_CREATE", "invoice", invoice.id,
        new_values={
            "invoice_number": invoice_number,
            "customer_id": str(customer_id),
            "total_amount": str(total_amount),
            "status": "draft",
        },
    )

    db.commit()
    db.refresh(invoice)
    return invoice


def finalize_invoice(db: Session, current_user: User, invoice: Invoice) -> Invoice:
    """Transition a DRAFT invoice to OPEN."""
    if invoice.status != InvoiceStatus.DRAFT:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Only DRAFT invoices can be finalized (current: {invoice.status.value})",
        )
    old_status = invoice.status.value
    invoice.status = InvoiceStatus.OPEN
    _audit(
        db, current_user, "INVOICE_FINALIZE", "invoice", invoice.id,
        old_values={"status": old_status},
        new_values={"status": "open"},
    )
    db.commit()
    db.refresh(invoice)
    return invoice


def update_invoice(
    db: Session,
    current_user: User,
    invoice: Invoice,
    due_date: Optional[date],
    tax_amount: Optional[Decimal],
    discount_amount: Optional[Decimal],
) -> Invoice:
    """Update a DRAFT invoice's due date, tax or discount and recompute totals."""
    if invoice.status != InvoiceStatus.DRAFT:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only DRAFT invoices can be edited",
        )

    old = {
        "due_date": str(invoice.due_date),
        "tax_amount": str(invoice.tax_amount),
        "discount_amount": str(invoice.discount_amount),
    }

    if due_date is not None:
        if due_date < invoice.issue_date:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="due_date must not be before issue_date",
            )
        invoice.due_date = due_date

    if tax_amount is not None:
        invoice.tax_amount = tax_amount.quantize(Decimal("0.01"))

    if discount_amount is not None:
        invoice.discount_amount = discount_amount.quantize(Decimal("0.01"))

    # Recompute totals
    new_total = max(
        invoice.subtotal + invoice.tax_amount - invoice.discount_amount,
        Decimal("0.00"),
    ).quantize(Decimal("0.01"))
    invoice.total_amount = new_total
    invoice.amount_due = max(new_total - invoice.amount_paid, Decimal("0.00")).quantize(
        Decimal("0.01")
    )

    _audit(
        db, current_user, "INVOICE_UPDATE", "invoice", invoice.id,
        old_values=old,
        new_values={
            "due_date": str(invoice.due_date),
            "tax_amount": str(invoice.tax_amount),
            "discount_amount": str(invoice.discount_amount),
            "total_amount": str(invoice.total_amount),
        },
    )
    db.commit()
    db.refresh(invoice)
    return invoice


def void_invoice(
    db: Session, current_user: User, invoice: Invoice, reason: Optional[str]
) -> Invoice:
    """Void a non-paid invoice. PAID invoices cannot be voided."""
    if invoice.status == InvoiceStatus.PAID:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Paid invoices cannot be voided. Issue a refund instead.",
        )
    if invoice.status in (InvoiceStatus.VOID, InvoiceStatus.UNCOLLECTIBLE):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invoice is already {invoice.status.value}",
        )
    old_status = invoice.status.value
    invoice.status = InvoiceStatus.VOID
    invoice.voided_at = datetime.now(timezone.utc)
    _audit(
        db, current_user, "INVOICE_VOID", "invoice", invoice.id,
        old_values={"status": old_status},
        new_values={"status": "void", "reason": reason},
    )
    db.commit()
    db.refresh(invoice)
    return invoice


def mark_uncollectible(
    db: Session, current_user: User, invoice: Invoice, reason: Optional[str]
) -> Invoice:
    """Mark an OPEN or OVERDUE invoice as uncollectible."""
    if invoice.status not in (InvoiceStatus.OPEN, InvoiceStatus.OVERDUE, InvoiceStatus.PARTIALLY_PAID):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot mark '{invoice.status.value}' invoice as uncollectible",
        )
    old_status = invoice.status.value
    invoice.status = InvoiceStatus.UNCOLLECTIBLE
    _audit(
        db, current_user, "INVOICE_UNCOLLECTIBLE", "invoice", invoice.id,
        old_values={"status": old_status},
        new_values={"status": "uncollectible", "reason": reason},
    )
    db.commit()
    db.refresh(invoice)
    return invoice


def refresh_invoice_overdue_status(db: Session, invoice: Invoice) -> Invoice:
    """
    Check whether the invoice has become overdue and update status in DB.
    Called lazily when an invoice is retrieved or listed.
    Overdue = status is OPEN or PARTIALLY_PAID, amount_due > 0, today > due_date.
    """
    if invoice.is_overdue and invoice.status in (
        InvoiceStatus.OPEN, InvoiceStatus.PARTIALLY_PAID
    ):
        invoice.status = InvoiceStatus.OVERDUE
        db.commit()
        db.refresh(invoice)
    return invoice


# ---------------------------------------------------------------------------
# Payment recording
# ---------------------------------------------------------------------------

def record_payment(
    db: Session,
    current_user: User,
    invoice: Invoice,
    amount: Decimal,
    currency: str,
    payment_method: str,
    transaction_reference: Optional[str],
    idempotency_key: Optional[str],
    payment_status: PaymentStatus,
    failure_reason: Optional[str],
    payment_date: Optional[date] = None,
    note: Optional[str] = None,
) -> Payment:
    """
    Record a payment against an invoice.

    Idempotency: if idempotency_key is supplied and an existing payment with
    that key already exists, return it immediately without creating a duplicate.

    Only SUCCEEDED payments update invoice financial totals.
    FAILED payments are recorded for audit purposes only.
    """
    # Idempotency check
    if idempotency_key:
        existing = (
            db.query(Payment)
            .filter(Payment.idempotency_key == idempotency_key)
            .first()
        )
        if existing:
            return existing

    # Invoice must be payable
    if invoice.status in (InvoiceStatus.PAID, InvoiceStatus.VOID, InvoiceStatus.UNCOLLECTIBLE):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot record payment against a '{invoice.status.value}' invoice",
        )
    if invoice.status == InvoiceStatus.DRAFT:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot record payment against a DRAFT invoice. Finalize it first.",
        )

    # Amount cannot exceed remaining balance for succeeded payments
    if payment_status == PaymentStatus.SUCCEEDED:
        if amount > invoice.amount_due:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Payment amount {amount} exceeds outstanding balance "
                    f"{invoice.amount_due} on invoice {invoice.invoice_number}"
                ),
            )

    payment_number = _next_payment_number(db)

    payment = Payment(
        payment_number=payment_number,
        invoice_id=invoice.id,
        customer_id=invoice.customer_id,
        amount=amount.quantize(Decimal("0.01")),
        currency=currency.upper(),
        status=payment_status,
        payment_method=payment_method,
        transaction_reference=transaction_reference,
        idempotency_key=idempotency_key,
        failure_reason=failure_reason,
        payment_date=payment_date or date.today(),
        note=note,
        refunded_amount=Decimal("0.00"),
    )
    db.add(payment)
    db.flush()

    # Update invoice financials only for succeeded payments
    if payment_status == PaymentStatus.SUCCEEDED:
        invoice.amount_paid = (invoice.amount_paid + amount).quantize(Decimal("0.01"))
        invoice.amount_due = (invoice.total_amount - invoice.amount_paid).quantize(
            Decimal("0.01")
        )
        now = datetime.now(timezone.utc)
        if invoice.amount_due <= Decimal("0.00"):
            invoice.amount_due = Decimal("0.00")
            invoice.status = InvoiceStatus.PAID
            invoice.paid_at = now
        elif invoice.amount_paid > Decimal("0.00"):
            invoice.status = InvoiceStatus.PARTIALLY_PAID

    _audit(
        db, current_user, "PAYMENT_RECORD", "payment", payment.id,
        new_values={
            "payment_number": payment_number,
            "invoice_id": str(invoice.id),
            "invoice_number": invoice.invoice_number,
            "amount": str(amount),
            "status": payment_status.value,
        },
    )

    db.commit()
    db.refresh(payment)
    return payment


# ---------------------------------------------------------------------------
# Refunds
# ---------------------------------------------------------------------------

def refund_payment(
    db: Session,
    current_user: User,
    payment: Payment,
    refund_amount: Decimal,
    reason: Optional[str],
) -> Payment:
    """
    Apply a partial or full refund to a succeeded payment.
    Updates invoice amount_paid / amount_due and invoice status accordingly.
    """
    if payment.status not in (PaymentStatus.SUCCEEDED, PaymentStatus.PARTIALLY_REFUNDED):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Only succeeded or partially-refunded payments can be refunded (current: {payment.status.value})",
        )

    already_refunded = payment.refunded_amount
    max_refundable = payment.amount - already_refunded

    if refund_amount > max_refundable:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Refund amount {refund_amount} exceeds refundable balance "
                f"{max_refundable} on payment {payment.payment_number}"
            ),
        )

    old_refunded = payment.refunded_amount
    payment.refunded_amount = (already_refunded + refund_amount).quantize(
        Decimal("0.01")
    )

    if payment.refunded_amount >= payment.amount:
        payment.status = PaymentStatus.REFUNDED
    else:
        payment.status = PaymentStatus.PARTIALLY_REFUNDED

    # Update invoice financials
    invoice = db.query(Invoice).filter(Invoice.id == payment.invoice_id).first()
    if invoice:
        invoice.amount_paid = max(
            invoice.amount_paid - refund_amount, Decimal("0.00")
        ).quantize(Decimal("0.01"))
        invoice.amount_due = (invoice.total_amount - invoice.amount_paid).quantize(
            Decimal("0.01")
        )
        # Downgrade invoice status if it was PAID
        if invoice.status == InvoiceStatus.PAID and invoice.amount_due > Decimal("0.00"):
            invoice.status = (
                InvoiceStatus.PARTIALLY_PAID
                if invoice.amount_paid > Decimal("0.00")
                else InvoiceStatus.OPEN
            )
            invoice.paid_at = None

    _audit(
        db, current_user, "PAYMENT_REFUND", "payment", payment.id,
        old_values={"refunded_amount": str(old_refunded), "status": payment.status.value},
        new_values={
            "refunded_amount": str(payment.refunded_amount),
            "refund_applied": str(refund_amount),
            "reason": reason,
        },
    )

    db.commit()
    db.refresh(payment)
    return payment
