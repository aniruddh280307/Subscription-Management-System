"""
API routers for Invoices and Payments.

Authorization:
- Admin and Staff: full access to all invoices and payments.
- Customers: read-only access to their own invoices and payments.
- Invoice creation, finalization, voiding and payment recording: admin/staff only.
"""

import math
import uuid
import html
import smtplib
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session, joinedload

from app.database import get_db
from app.models import (
    Invoice,
    InvoiceStatus,
    Payment,
    PaymentStatus,
    Customer,
    User,
    UserRole,
)
from app.schemas.invoice import (
    InvoiceCreateRequest,
    InvoiceUpdateRequest,
    InvoiceVoidRequest,
    InvoiceMarkUncollectibleRequest,
    InvoiceResponse,
    PaymentRecordRequest,
    PaymentRefundRequest,
    PaymentResponse,
)
from app.schemas.common import PaginatedResponse
from app.dependencies import get_current_user, require_staff_or_admin
from app.services.invoice_service import (
    create_invoice as svc_create_invoice,
    finalize_invoice as svc_finalize_invoice,
    update_invoice as svc_update_invoice,
    void_invoice as svc_void_invoice,
    mark_uncollectible as svc_mark_uncollectible,
    record_payment as svc_record_payment,
    refund_payment as svc_refund_payment,
    refresh_invoice_overdue_status,
)
from app.services.email_service import EmailConfigurationError, send_invoice_email

router = APIRouter(tags=["Invoices & Payments"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_invoice_or_404(
    invoice_id: uuid.UUID, db: Session, current_user: User
) -> Invoice:
    invoice = (
        db.query(Invoice)
        .options(joinedload(Invoice.items))
        .filter(Invoice.id == invoice_id)
        .first()
    )
    if not invoice:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Invoice not found"
        )
    # Customer isolation
    if current_user.role == UserRole.CUSTOMER:
        cust = db.query(Customer).filter(Customer.user_id == current_user.id).first()
        if not cust or invoice.customer_id != cust.id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access forbidden: you can only access your own invoices",
            )
    return invoice


def _get_payment_or_404(
    payment_id: uuid.UUID, db: Session, current_user: User
) -> Payment:
    payment = db.query(Payment).filter(Payment.id == payment_id).first()
    if not payment:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Payment not found"
        )
    if current_user.role == UserRole.CUSTOMER:
        cust = db.query(Customer).filter(Customer.user_id == current_user.id).first()
        if not cust or payment.customer_id != cust.id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access forbidden: you can only access your own payments",
            )
    return payment


# ---------------------------------------------------------------------------
# Invoice routes
# ---------------------------------------------------------------------------


@router.post(
    "/api/invoices",
    response_model=InvoiceResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a DRAFT invoice",
)
def create_invoice(
    request: InvoiceCreateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_staff_or_admin),
):
    """Create a DRAFT invoice with line items. Admin/Staff only."""
    return svc_create_invoice(
        db=db,
        current_user=current_user,
        customer_id=request.customer_id,
        subscription_id=request.subscription_id,
        issue_date=request.issue_date,
        due_date=request.due_date,
        currency=request.currency,
        tax_amount=request.tax_amount,
        discount_amount=request.discount_amount,
        items_data=request.items,
    )


@router.get(
    "/api/invoices",
    response_model=PaginatedResponse[InvoiceResponse],
    summary="List invoices",
)
def list_invoices(
    customer_id: Optional[uuid.UUID] = Query(None),
    invoice_status: Optional[InvoiceStatus] = Query(None, alias="status"),
    subscription_id: Optional[uuid.UUID] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List invoices with pagination. Customers see only their own invoices."""
    query = db.query(Invoice).options(joinedload(Invoice.items))

    if current_user.role == UserRole.CUSTOMER:
        cust = db.query(Customer).filter(Customer.user_id == current_user.id).first()
        if not cust:
            return PaginatedResponse(items=[], total=0, page=page, page_size=page_size, total_pages=0)
        query = query.filter(Invoice.customer_id == cust.id)
    elif customer_id:
        query = query.filter(Invoice.customer_id == customer_id)

    if subscription_id:
        query = query.filter(Invoice.subscription_id == subscription_id)

    if invoice_status:
        query = query.filter(Invoice.status == invoice_status)

    total = query.count()
    invoices = (
        query.order_by(Invoice.issue_date.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    # Lazily refresh overdue status
    for inv in invoices:
        refresh_invoice_overdue_status(db, inv)

    total_pages = math.ceil(total / page_size) if page_size > 0 else 0
    return PaginatedResponse(
        items=invoices, total=total, page=page, page_size=page_size, total_pages=total_pages
    )


@router.get(
    "/api/invoices/{invoice_id}",
    response_model=InvoiceResponse,
    summary="Get an invoice",
)
def get_invoice(
    invoice_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    invoice = _get_invoice_or_404(invoice_id, db, current_user)
    return refresh_invoice_overdue_status(db, invoice)


@router.patch(
    "/api/invoices/{invoice_id}",
    response_model=InvoiceResponse,
    summary="Update a DRAFT invoice",
)
def update_invoice(
    invoice_id: uuid.UUID,
    request: InvoiceUpdateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_staff_or_admin),
):
    """Update due date, tax or discount on a DRAFT invoice."""
    invoice = _get_invoice_or_404(invoice_id, db, current_user)
    return svc_update_invoice(
        db=db,
        current_user=current_user,
        invoice=invoice,
        due_date=request.due_date,
        tax_amount=request.tax_amount,
        discount_amount=request.discount_amount,
    )


@router.post(
    "/api/invoices/{invoice_id}/finalize",
    response_model=InvoiceResponse,
    summary="Finalize a DRAFT invoice (DRAFT → OPEN)",
)
def finalize_invoice(
    invoice_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_staff_or_admin),
):
    invoice = _get_invoice_or_404(invoice_id, db, current_user)
    return svc_finalize_invoice(db=db, current_user=current_user, invoice=invoice)


@router.post("/api/invoices/{invoice_id}/send", response_model=InvoiceResponse)
def send_invoice(
    invoice_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_staff_or_admin),
):
    invoice = _get_invoice_or_404(invoice_id, db, current_user)
    if invoice.status == InvoiceStatus.DRAFT:
        raise HTTPException(status_code=400, detail="Finalize the invoice before sending")
    customer = db.query(Customer).filter(Customer.id == invoice.customer_id).first()
    try:
        send_invoice_email(customer.email, customer.name, invoice)
    except (EmailConfigurationError, OSError, smtplib.SMTPException) as exc:
        raise HTTPException(status_code=503, detail="Invoice email is temporarily unavailable") from exc
    invoice.sent_at = datetime.now(timezone.utc)
    db.commit(); db.refresh(invoice)
    return invoice


@router.get("/api/invoices/{invoice_id}/print", response_class=HTMLResponse)
def print_invoice(
    invoice_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    invoice = _get_invoice_or_404(invoice_id, db, current_user)
    customer = db.query(Customer).filter(Customer.id == invoice.customer_id).first()
    rows = "".join(
        f"<tr><td>{html.escape(item.description)}</td><td>{item.quantity}</td>"
        f"<td>{invoice.currency} {item.unit_price}</td><td>{invoice.currency} {item.amount}</td></tr>"
        for item in invoice.items
    )
    return HTMLResponse(f"""<!doctype html><html><head><title>{html.escape(invoice.invoice_number)}</title>
    <style>body{{font-family:Arial;margin:48px;color:#17231f}}header{{display:flex;justify-content:space-between}}table{{width:100%;border-collapse:collapse;margin-top:35px}}th,td{{padding:12px;border-bottom:1px solid #ddd;text-align:left}}.totals{{margin:30px 0 0 auto;width:320px}}@media print{{button{{display:none}}}}</style></head>
    <body><button onclick="window.print()">Print invoice</button><header><div><h1>SubscriptHQ</h1><p>{html.escape(customer.name)}</p></div><div><h2>{html.escape(invoice.invoice_number)}</h2><p>Issued {invoice.issue_date}<br>Due {invoice.due_date}</p></div></header>
    <table><thead><tr><th>Description</th><th>Qty</th><th>Unit price</th><th>Amount</th></tr></thead><tbody>{rows}</tbody></table>
    <div class="totals"><p>Subtotal: {invoice.currency} {invoice.subtotal}</p><p>Tax: {invoice.currency} {invoice.tax_amount}</p><p>Discount: {invoice.currency} {invoice.discount_amount}</p><h3>Total: {invoice.currency} {invoice.total_amount}</h3><p>Amount due: {invoice.currency} {invoice.amount_due}</p></div></body></html>""")


@router.post(
    "/api/invoices/{invoice_id}/void",
    response_model=InvoiceResponse,
    summary="Void an invoice",
)
def void_invoice(
    invoice_id: uuid.UUID,
    request: InvoiceVoidRequest = InvoiceVoidRequest(),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_staff_or_admin),
):
    invoice = _get_invoice_or_404(invoice_id, db, current_user)
    return svc_void_invoice(db=db, current_user=current_user, invoice=invoice, reason=request.reason)


@router.post(
    "/api/invoices/{invoice_id}/mark-uncollectible",
    response_model=InvoiceResponse,
    summary="Mark an invoice as uncollectible",
)
def mark_uncollectible(
    invoice_id: uuid.UUID,
    request: InvoiceMarkUncollectibleRequest = InvoiceMarkUncollectibleRequest(),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_staff_or_admin),
):
    invoice = _get_invoice_or_404(invoice_id, db, current_user)
    return svc_mark_uncollectible(
        db=db, current_user=current_user, invoice=invoice, reason=request.reason
    )


# ---------------------------------------------------------------------------
# Payment routes
# ---------------------------------------------------------------------------


@router.post(
    "/api/invoices/{invoice_id}/payments",
    response_model=PaymentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Record a payment against an invoice",
)
def record_payment(
    invoice_id: uuid.UUID,
    request: PaymentRecordRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_staff_or_admin),
):
    """
    Record an external/manual payment against an invoice.
    Idempotency: supply idempotency_key to safely retry without creating duplicates.
    Does NOT integrate a real payment gateway.
    """
    if request.invoice_id != invoice_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="invoice_id in request body must match the URL path parameter",
        )
    invoice = _get_invoice_or_404(invoice_id, db, current_user)
    return svc_record_payment(
        db=db,
        current_user=current_user,
        invoice=invoice,
        amount=request.amount,
        currency=request.currency,
        payment_method=request.payment_method,
        transaction_reference=request.transaction_reference,
        idempotency_key=request.idempotency_key,
        payment_status=request.payment_status,
        failure_reason=request.failure_reason,
        payment_date=request.payment_date,
        note=request.note,
    )


@router.get(
    "/api/invoices/{invoice_id}/payments",
    response_model=PaginatedResponse[PaymentResponse],
    summary="List payments for an invoice",
)
def list_payments_for_invoice(
    invoice_id: uuid.UUID,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    invoice = _get_invoice_or_404(invoice_id, db, current_user)
    query = db.query(Payment).filter(Payment.invoice_id == invoice_id)
    total = query.count()
    items = (
        query.order_by(Payment.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    total_pages = math.ceil(total / page_size) if page_size > 0 else 0
    return PaginatedResponse(
        items=items, total=total, page=page, page_size=page_size, total_pages=total_pages
    )


@router.get(
    "/api/payments",
    response_model=PaginatedResponse[PaymentResponse],
    summary="List all payments",
)
def list_payments(
    customer_id: Optional[uuid.UUID] = Query(None),
    payment_status: Optional[PaymentStatus] = Query(None, alias="status"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List payments. Customers see only their own."""
    query = db.query(Payment)

    if current_user.role == UserRole.CUSTOMER:
        cust = db.query(Customer).filter(Customer.user_id == current_user.id).first()
        if not cust:
            return PaginatedResponse(items=[], total=0, page=page, page_size=page_size, total_pages=0)
        query = query.filter(Payment.customer_id == cust.id)
    elif customer_id:
        query = query.filter(Payment.customer_id == customer_id)

    if payment_status:
        query = query.filter(Payment.status == payment_status)

    total = query.count()
    items = (
        query.order_by(Payment.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    total_pages = math.ceil(total / page_size) if page_size > 0 else 0
    return PaginatedResponse(
        items=items, total=total, page=page, page_size=page_size, total_pages=total_pages
    )


@router.get(
    "/api/payments/{payment_id}",
    response_model=PaymentResponse,
    summary="Get a payment",
)
def get_payment(
    payment_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return _get_payment_or_404(payment_id, db, current_user)


@router.post(
    "/api/payments/{payment_id}/refund",
    response_model=PaymentResponse,
    summary="Refund a payment (partial or full)",
)
def refund_payment(
    payment_id: uuid.UUID,
    request: PaymentRefundRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_staff_or_admin),
):
    """Apply a partial or full refund to a succeeded payment, updating invoice totals."""
    payment = _get_payment_or_404(payment_id, db, current_user)
    return svc_refund_payment(
        db=db,
        current_user=current_user,
        payment=payment,
        refund_amount=request.refund_amount,
        reason=request.reason,
    )
