"""
Dashboard and Reporting API endpoints.

All data is computed from real database aggregations.
Access: Admin and Staff only for all dashboard endpoints.
"""

from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import User
from app.dependencies import require_staff_or_admin
from app.services.dashboard_service import (
    get_summary_metrics,
    get_revenue_trend,
    get_upcoming_renewals,
    get_invoice_status_breakdown,
    get_payment_outcomes,
)

router = APIRouter(prefix="/api/dashboard", tags=["Dashboard & Reports"])


@router.get("/summary", summary="Overall platform summary metrics")
def dashboard_summary(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_staff_or_admin),
) -> Dict[str, Any]:
    """
    Returns:
    - total_active_customers
    - subscriptions_by_status (dict)
    - mrr and arr (with documented assumptions)
    - total_invoiced, total_collected, total_outstanding
    - overdue_invoices_count, overdue_invoices_amount
    - mrr_assumption (methodology note)
    """
    return get_summary_metrics(db)


@router.get("/revenue-trend", summary="Monthly revenue trend")
def revenue_trend(
    months: int = Query(default=12, ge=1, le=36, description="Number of past months to include"),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_staff_or_admin),
) -> List[Dict[str, Any]]:
    """
    Returns monthly gross revenue, total refunded amounts and net revenue
    for the last N months (default 12). Based on succeeded payment records.
    """
    return get_revenue_trend(db, months=months)


@router.get("/upcoming-renewals", summary="Subscriptions renewing soon")
def upcoming_renewals(
    days: int = Query(default=30, ge=1, le=90, description="Look-ahead window in days"),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_staff_or_admin),
) -> List[Dict[str, Any]]:
    """
    Returns ACTIVE and TRIALING subscriptions whose billing period ends
    within the next N days (default 30), ordered by renewal date ascending.
    """
    return get_upcoming_renewals(db, days=days)


@router.get("/invoice-breakdown", summary="Invoice counts and amounts by status")
def invoice_breakdown(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_staff_or_admin),
) -> List[Dict[str, Any]]:
    """
    Returns count, total_amount and amount_due for invoices grouped by status.
    """
    return get_invoice_status_breakdown(db)


@router.get("/payment-outcomes", summary="Payment counts and amounts by status")
def payment_outcomes(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_staff_or_admin),
) -> List[Dict[str, Any]]:
    """
    Returns count and total payment amount grouped by payment status
    (succeeded, failed, refunded, partially_refunded, pending).
    """
    return get_payment_outcomes(db)
