"""
Revenue Guardian API endpoints.

All findings are computed on demand from real database queries.
Access: Admin and Staff only.
"""

from datetime import datetime, timezone
from typing import Any, Dict, Optional
import uuid
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import GuardianFinding, GuardianFindingStatus, User
from app.schemas.commercial import GuardianStatusUpdate
from app.dependencies import require_staff_or_admin
from app.services.guardian_service import (
    run_all_rules,
    detect_overdue_invoices,
    detect_failed_payments,
    detect_duplicate_payments,
    detect_duplicate_invoices,
    detect_expired_discounts,
    detect_invoice_total_mismatches,
    detect_amount_paid_due_mismatch,
    detect_upcoming_renewals,
    detect_missing_invoices,
    detect_payment_invoice_customer_mismatch,
    sync_guardian_findings,
)

router = APIRouter(prefix="/api/guardian", tags=["Revenue Guardian"])


@router.get("/run", summary="Run all Revenue Guardian detection rules")
def run_guardian(
    days_failed: int = Query(
        default=30, ge=1, le=365,
        description="Look-back window for failed payment detection (days)"
    ),
    days_renewal: int = Query(
        default=7, ge=1, le=90,
        description="Look-ahead window for upcoming renewal alerts (days)"
    ),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_staff_or_admin),
) -> Dict[str, Any]:
    """
    Run all Revenue Guardian rules and return findings grouped by severity.
    Findings clearly label SUSPECTED vs CONFIRMED issues.
    Each finding includes evidence, affected record IDs, and recommended action.
    """
    findings = sync_guardian_findings(db, days_failed=days_failed, days_renewal=days_renewal)
    by_severity = {"low": 0, "medium": 0, "high": 0, "critical": 0}
    for finding in findings:
        by_severity[finding.severity] = by_severity.get(finding.severity, 0) + 1
    return {
        "total_findings": len(findings),
        "by_severity": by_severity,
        "findings": [
            {
                "id": str(f.id), "rule": f.rule, "title": f.title,
                "severity": f.severity, "certainty": f.certainty,
                "affected_ids": f.affected_ids, "evidence": f.evidence,
                "recommended_action": f.recommended_action,
                "status": f.status.value, "detected_at": f.detected_at.isoformat(),
            } for f in findings
        ],
    }


@router.get("/findings")
def list_guardian_findings(
    finding_status: Optional[GuardianFindingStatus] = Query(default=None, alias="status"),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_staff_or_admin),
):
    query = db.query(GuardianFinding)
    if finding_status: query = query.filter(GuardianFinding.status == finding_status)
    return query.order_by(GuardianFinding.detected_at.desc()).all()


@router.patch("/findings/{finding_id}")
def update_guardian_finding(
    finding_id: uuid.UUID,
    request: GuardianStatusUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_staff_or_admin),
):
    finding = db.query(GuardianFinding).filter(GuardianFinding.id == finding_id).first()
    if not finding:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Guardian finding not found")
    finding.status = request.status
    finding.resolved_at = datetime.now(timezone.utc) if request.status == GuardianFindingStatus.RESOLVED else None
    db.commit(); db.refresh(finding); return finding


@router.get("/overdue-invoices", summary="Overdue invoice findings")
def guardian_overdue(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_staff_or_admin),
):
    return {"findings": detect_overdue_invoices(db)}


@router.get("/failed-payments", summary="Failed payment findings")
def guardian_failed_payments(
    days: int = Query(default=30, ge=1, le=365),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_staff_or_admin),
):
    return {"findings": detect_failed_payments(db, days=days)}


@router.get("/duplicate-payments", summary="Potential duplicate payment findings")
def guardian_duplicates(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_staff_or_admin),
):
    return {"findings": detect_duplicate_payments(db)}


@router.get("/duplicate-invoices", summary="Potential duplicate invoice findings")
def guardian_duplicate_invoices(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_staff_or_admin),
):
    return {"findings": detect_duplicate_invoices(db)}


@router.get("/expired-discounts", summary="Active expired discount findings")
def guardian_expired_discounts(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_staff_or_admin),
):
    return {"findings": detect_expired_discounts(db)}


@router.get("/invoice-total-mismatches", summary="Invoice total calculation mismatches")
def guardian_total_mismatches(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_staff_or_admin),
):
    return {"findings": detect_invoice_total_mismatches(db)}


@router.get("/amount-paid-due-mismatches", summary="Invoice amount_paid + amount_due mismatch")
def guardian_amount_mismatches(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_staff_or_admin),
):
    return {"findings": detect_amount_paid_due_mismatch(db)}


@router.get("/upcoming-renewals", summary="Upcoming renewal alerts")
def guardian_renewals(
    days: int = Query(default=7, ge=1, le=90),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_staff_or_admin),
):
    return {"findings": detect_upcoming_renewals(db, days=days)}


@router.get("/missing-invoices", summary="Active subscriptions missing invoices")
def guardian_missing_invoices(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_staff_or_admin),
):
    return {"findings": detect_missing_invoices(db)}


@router.get("/payment-invoice-mismatches", summary="Payment/invoice customer ID mismatches")
def guardian_pay_inv_mismatches(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_staff_or_admin),
):
    return {"findings": detect_payment_invoice_customer_mismatch(db)}
