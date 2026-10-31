"""
Revenue Guardian — explainable, database-backed monitoring service.

Each detection rule returns a list of finding dictionaries. Findings are:
  - Persisted with a stable fingerprint for acknowledgement and resolution
  - Clearly labelled SUSPECTED vs CONFIRMED
  - Accompanied by evidence, severity, affected record IDs, and recommended action

Detection rules implemented:
  1. OVERDUE_INVOICE       — invoices with status=OVERDUE or past due_date with amount_due > 0
  2. FAILED_PAYMENT        — payments with status=FAILED in the last N days
  3. DUPLICATE_PAYMENT     — multiple succeeded payments against the same invoice on the same day
  4. DUPLICATE_INVOICE     — multiple non-void invoices for one subscription/date
  5. EXPIRED_DISCOUNT_ACTIVE — active discount rules after date/usage limits
  6. INVOICE_TOTAL_MISMATCH— invoice total_amount != subtotal + tax - discount
  7. AMOUNT_PAID_MISMATCH  — invoice amount_paid + amount_due != total_amount
  8. UPCOMING_RENEWAL      — active subscriptions renewing within N days (alert, not an issue)
  9. MISSING_INVOICE       — active subscriptions with no invoice in the current billing period
 10. PAYMENT_INVOICE_MISMATCH — payment.customer_id != invoice.customer_id (data integrity)

All rules query real database tables only. No hardcoded data.
"""

from datetime import datetime, timezone, date, timedelta
from decimal import Decimal
from typing import List, Dict, Any, Optional
import hashlib
import json

from sqlalchemy import func, and_, text, cast, Date
from sqlalchemy.orm import Session

from app.models import (
    Customer,
    Subscription,
    SubscriptionPlan,
    SubscriptionStatus,
    Invoice,
    InvoiceStatus,
    InvoiceItem,
    Payment,
    PaymentStatus,
    GuardianFinding,
    GuardianFindingStatus,
    DiscountRule,
)


SEVERITY_LOW = "low"
SEVERITY_MEDIUM = "medium"
SEVERITY_HIGH = "high"
SEVERITY_CRITICAL = "critical"

CERTAINTY_SUSPECTED = "suspected"
CERTAINTY_CONFIRMED = "confirmed"


def _finding(
    rule: str,
    title: str,
    severity: str,
    certainty: str,
    affected_ids: Dict[str, Any],
    evidence: str,
    recommended_action: str,
) -> Dict[str, Any]:
    return {
        "rule": rule,
        "title": title,
        "severity": severity,
        "certainty": certainty,
        "affected_ids": affected_ids,
        "evidence": evidence,
        "recommended_action": recommended_action,
        "detected_at": datetime.now(timezone.utc).isoformat(),
        "status": "open",
    }


# ---------------------------------------------------------------------------
# Rule 1: Overdue invoices
# ---------------------------------------------------------------------------

def detect_overdue_invoices(db: Session) -> List[Dict]:
    findings = []
    today = date.today()

    # Confirmed: status is already OVERDUE
    confirmed = (
        db.query(Invoice)
        .filter(Invoice.status == InvoiceStatus.OVERDUE)
        .all()
    )
    for inv in confirmed:
        findings.append(_finding(
            rule="OVERDUE_INVOICE",
            title=f"Invoice {inv.invoice_number} is overdue",
            severity=SEVERITY_HIGH,
            certainty=CERTAINTY_CONFIRMED,
            affected_ids={"invoice_id": str(inv.id), "customer_id": str(inv.customer_id)},
            evidence=(
                f"Invoice {inv.invoice_number} status=overdue, "
                f"due_date={inv.due_date}, amount_due={inv.amount_due}"
            ),
            recommended_action="Contact customer for payment or mark as uncollectible.",
        ))

    # Suspected: OPEN/PARTIALLY_PAID invoices past their due_date (not yet status-updated)
    suspected = (
        db.query(Invoice)
        .filter(
            Invoice.status.in_([InvoiceStatus.OPEN, InvoiceStatus.PARTIALLY_PAID]),
            Invoice.due_date < today,
            Invoice.amount_due > 0,
        )
        .all()
    )
    for inv in suspected:
        findings.append(_finding(
            rule="OVERDUE_INVOICE",
            title=f"Invoice {inv.invoice_number} appears overdue (status not yet updated)",
            severity=SEVERITY_MEDIUM,
            certainty=CERTAINTY_SUSPECTED,
            affected_ids={"invoice_id": str(inv.id), "customer_id": str(inv.customer_id)},
            evidence=(
                f"Invoice {inv.invoice_number} status={inv.status.value}, "
                f"due_date={inv.due_date} (past), amount_due={inv.amount_due}"
            ),
            recommended_action="Run overdue-status refresh or update status to overdue.",
        ))

    return findings


# ---------------------------------------------------------------------------
# Rule 2: Failed payments
# ---------------------------------------------------------------------------

def detect_failed_payments(db: Session, days: int = 30) -> List[Dict]:
    since = datetime.now(timezone.utc) - timedelta(days=days)
    failed = (
        db.query(Payment)
        .filter(
            Payment.status == PaymentStatus.FAILED,
            Payment.created_at >= since,
        )
        .order_by(Payment.created_at.desc())
        .all()
    )
    findings = []
    for pay in failed:
        findings.append(_finding(
            rule="FAILED_PAYMENT",
            title=f"Payment {pay.payment_number} failed",
            severity=SEVERITY_MEDIUM,
            certainty=CERTAINTY_CONFIRMED,
            affected_ids={
                "payment_id": str(pay.id),
                "invoice_id": str(pay.invoice_id),
                "customer_id": str(pay.customer_id),
            },
            evidence=(
                f"Payment {pay.payment_number} status=failed, "
                f"amount={pay.amount}, "
                f"reason={pay.failure_reason or 'not recorded'}, "
                f"created_at={pay.created_at.isoformat()}"
            ),
            recommended_action="Retry payment or contact customer to update payment method.",
        ))
    return findings


# ---------------------------------------------------------------------------
# Rule 3: Duplicate payments (same invoice, same day, multiple succeeded)
# ---------------------------------------------------------------------------

def detect_duplicate_payments(db: Session) -> List[Dict]:
    rows = (
        db.query(
            Payment.invoice_id,
            cast(Payment.created_at, Date).label("pay_date"),
            func.count(Payment.id).label("cnt"),
        )
        .filter(Payment.status == PaymentStatus.SUCCEEDED)
        .group_by(Payment.invoice_id, cast(Payment.created_at, Date))
        .having(func.count(Payment.id) > 1)
        .all()
    )
    findings = []
    for row in rows:
        findings.append(_finding(
            rule="DUPLICATE_PAYMENT",
            title=f"Multiple succeeded payments on invoice {row.invoice_id} on {row.pay_date}",
            severity=SEVERITY_HIGH,
            certainty=CERTAINTY_SUSPECTED,
            affected_ids={"invoice_id": str(row.invoice_id)},
            evidence=(
                f"{row.cnt} succeeded payments recorded against invoice "
                f"{row.invoice_id} on {row.pay_date}. "
                "This may indicate a duplicate charge."
            ),
            recommended_action=(
                "Review payment records for this invoice and date. "
                "If duplicate, issue a refund for the extra payment."
            ),
        ))
    return findings


def detect_duplicate_invoices(db: Session) -> List[Dict]:
    """Flag multiple non-void invoices for one subscription and issue date."""
    rows = (
        db.query(
            Invoice.subscription_id,
            Invoice.issue_date,
            func.count(Invoice.id).label("cnt"),
        )
        .filter(Invoice.subscription_id.isnot(None), Invoice.status != InvoiceStatus.VOID)
        .group_by(Invoice.subscription_id, Invoice.issue_date)
        .having(func.count(Invoice.id) > 1)
        .all()
    )
    return [
        _finding(
            rule="DUPLICATE_INVOICE",
            title=f"Multiple invoices issued for subscription {row.subscription_id} on {row.issue_date}",
            severity=SEVERITY_HIGH,
            certainty=CERTAINTY_SUSPECTED,
            affected_ids={"subscription_id": str(row.subscription_id)},
            evidence=f"{row.cnt} non-void invoices share the same subscription and issue date {row.issue_date}.",
            recommended_action="Compare billing periods and void any invoice created in error.",
        )
        for row in rows
    ]


def detect_expired_discounts(db: Session) -> List[Dict]:
    """Flag discount rules that remain active after expiry or usage exhaustion."""
    today = date.today()
    rules = db.query(DiscountRule).filter(DiscountRule.is_active.is_(True)).all()
    findings = []
    for rule in rules:
        reasons = []
        if rule.valid_until and rule.valid_until < today:
            reasons.append(f"valid_until={rule.valid_until}")
        if rule.usage_limit is not None and rule.times_redeemed >= rule.usage_limit:
            reasons.append(f"times_redeemed={rule.times_redeemed}, usage_limit={rule.usage_limit}")
        if reasons:
            findings.append(_finding(
                rule="EXPIRED_DISCOUNT_ACTIVE",
                title=f"Discount {rule.code} is still active after its limit",
                severity=SEVERITY_MEDIUM,
                certainty=CERTAINTY_CONFIRMED,
                affected_ids={"discount_rule_id": str(rule.id)},
                evidence="; ".join(reasons) + "; is_active=true",
                recommended_action="Archive the discount rule and review invoices created after it expired.",
            ))
    return findings


# ---------------------------------------------------------------------------
# Rule 4: Invoice total mismatch (subtotal + tax - discount != total_amount)
# ---------------------------------------------------------------------------

def detect_invoice_total_mismatches(db: Session) -> List[Dict]:
    invoices = (
        db.query(Invoice)
        .filter(Invoice.status.notin_([InvoiceStatus.VOID]))
        .all()
    )
    findings = []
    for inv in invoices:
        expected = max(
            inv.subtotal + inv.tax_amount - inv.discount_amount, Decimal("0")
        ).quantize(Decimal("0.01"))
        actual = Decimal(str(inv.total_amount)).quantize(Decimal("0.01"))
        if abs(expected - actual) > Decimal("0.01"):
            findings.append(_finding(
                rule="INVOICE_TOTAL_MISMATCH",
                title=f"Invoice {inv.invoice_number} total_amount does not match line items",
                severity=SEVERITY_CRITICAL,
                certainty=CERTAINTY_CONFIRMED,
                affected_ids={"invoice_id": str(inv.id), "customer_id": str(inv.customer_id)},
                evidence=(
                    f"subtotal={inv.subtotal}, tax={inv.tax_amount}, "
                    f"discount={inv.discount_amount}, "
                    f"expected_total={expected}, actual_total={actual}"
                ),
                recommended_action=(
                    "Recalculate and correct total_amount. "
                    "Investigate how the discrepancy occurred."
                ),
            ))
    return findings


# ---------------------------------------------------------------------------
# Rule 5: amount_paid + amount_due != total_amount
# ---------------------------------------------------------------------------

def detect_amount_paid_due_mismatch(db: Session) -> List[Dict]:
    invoices = (
        db.query(Invoice)
        .filter(Invoice.status.notin_([InvoiceStatus.VOID, InvoiceStatus.DRAFT]))
        .all()
    )
    findings = []
    for inv in invoices:
        paid = Decimal(str(inv.amount_paid)).quantize(Decimal("0.01"))
        due = Decimal(str(inv.amount_due)).quantize(Decimal("0.01"))
        total = Decimal(str(inv.total_amount)).quantize(Decimal("0.01"))
        if abs((paid + due) - total) > Decimal("0.01"):
            findings.append(_finding(
                rule="AMOUNT_PAID_DUE_MISMATCH",
                title=f"Invoice {inv.invoice_number} amount_paid + amount_due != total_amount",
                severity=SEVERITY_CRITICAL,
                certainty=CERTAINTY_CONFIRMED,
                affected_ids={"invoice_id": str(inv.id), "customer_id": str(inv.customer_id)},
                evidence=(
                    f"amount_paid={paid}, amount_due={due}, sum={paid + due}, "
                    f"total_amount={total}, diff={abs((paid + due) - total)}"
                ),
                recommended_action=(
                    "Reconcile payment records against invoice. "
                    "Correct amount_paid/amount_due values."
                ),
            ))
    return findings


# ---------------------------------------------------------------------------
# Rule 6: Upcoming renewals (alert, not an issue)
# ---------------------------------------------------------------------------

def detect_upcoming_renewals(db: Session, days: int = 7) -> List[Dict]:
    now = datetime.now(timezone.utc)
    cutoff = now + timedelta(days=days)
    subs = (
        db.query(Subscription, SubscriptionPlan, Customer)
        .join(SubscriptionPlan, Subscription.plan_id == SubscriptionPlan.id)
        .join(Customer, Subscription.customer_id == Customer.id)
        .filter(
            Subscription.status.in_(
                [SubscriptionStatus.ACTIVE, SubscriptionStatus.TRIALING]
            ),
            Subscription.current_period_end >= now,
            Subscription.current_period_end <= cutoff,
        )
        .all()
    )
    findings = []
    for sub, plan, customer in subs:
        findings.append(_finding(
            rule="UPCOMING_RENEWAL",
            title=f"Subscription {sub.id} renews within {days} days",
            severity=SEVERITY_LOW,
            certainty=CERTAINTY_CONFIRMED,
            affected_ids={
                "subscription_id": str(sub.id),
                "customer_id": str(customer.id),
                "plan_id": str(plan.id),
            },
            evidence=(
                f"Subscription renews on {sub.current_period_end.isoformat()}, "
                f"plan={plan.name}, price={plan.price}, "
                f"customer={customer.email}"
            ),
            recommended_action=(
                "Ensure invoice is generated before the renewal date "
                "and customer is notified."
            ),
        ))
    return findings


# ---------------------------------------------------------------------------
# Rule 7: Active subscriptions with no invoice in current billing period
# ---------------------------------------------------------------------------

def detect_missing_invoices(db: Session) -> List[Dict]:
    """
    Flag ACTIVE subscriptions where no invoice exists with an issue_date
    falling within the current billing period [current_period_start, current_period_end].
    This is SUSPECTED because invoice generation may not yet be automated.
    """
    active_subs = (
        db.query(Subscription, SubscriptionPlan, Customer)
        .join(SubscriptionPlan, Subscription.plan_id == SubscriptionPlan.id)
        .join(Customer, Subscription.customer_id == Customer.id)
        .filter(Subscription.status == SubscriptionStatus.ACTIVE)
        .all()
    )

    findings = []
    for sub, plan, customer in active_subs:
        period_start = sub.current_period_start.date() if sub.current_period_start else None
        period_end = sub.current_period_end.date() if sub.current_period_end else None
        if not period_start or not period_end:
            continue

        invoice_count = (
            db.query(func.count(Invoice.id))
            .filter(
                Invoice.customer_id == sub.customer_id,
                Invoice.subscription_id == sub.id,
                Invoice.issue_date >= period_start,
                Invoice.issue_date <= period_end,
                Invoice.status.notin_([InvoiceStatus.VOID]),
            )
            .scalar()
        )
        if invoice_count == 0:
            findings.append(_finding(
                rule="MISSING_INVOICE",
                title=f"No invoice found for subscription {sub.id} in current period",
                severity=SEVERITY_MEDIUM,
                certainty=CERTAINTY_SUSPECTED,
                affected_ids={
                    "subscription_id": str(sub.id),
                    "customer_id": str(sub.customer_id),
                    "plan_id": str(plan.id),
                },
                evidence=(
                    f"Subscription {sub.id} (status=active) has no invoice linked "
                    f"for period {period_start} to {period_end}. "
                    f"Plan: {plan.name}, price: {plan.price}."
                ),
                recommended_action=(
                    "Generate an invoice for this subscription period "
                    "or verify if billing is handled externally."
                ),
            ))
    return findings


# ---------------------------------------------------------------------------
# Rule 8: Payment/invoice customer mismatch (data integrity)
# ---------------------------------------------------------------------------

def detect_payment_invoice_customer_mismatch(db: Session) -> List[Dict]:
    """
    Payment.customer_id should always equal Invoice.customer_id.
    The DB enforces this via ForeignKeyConstraint, but check for legacy data.
    """
    rows = (
        db.query(Payment, Invoice)
        .join(Invoice, Payment.invoice_id == Invoice.id)
        .filter(Payment.customer_id != Invoice.customer_id)
        .all()
    )
    findings = []
    for pay, inv in rows:
        findings.append(_finding(
            rule="PAYMENT_INVOICE_CUSTOMER_MISMATCH",
            title=f"Payment {pay.payment_number} customer does not match invoice customer",
            severity=SEVERITY_CRITICAL,
            certainty=CERTAINTY_CONFIRMED,
            affected_ids={
                "payment_id": str(pay.id),
                "invoice_id": str(inv.id),
                "payment_customer_id": str(pay.customer_id),
                "invoice_customer_id": str(inv.customer_id),
            },
            evidence=(
                f"Payment {pay.payment_number} has customer_id={pay.customer_id} "
                f"but invoice {inv.invoice_number} has customer_id={inv.customer_id}."
            ),
            recommended_action=(
                "Investigate how this record was created. "
                "Correct the data mismatch and audit related records."
            ),
        ))
    return findings


# ---------------------------------------------------------------------------
# Run all rules
# ---------------------------------------------------------------------------

def run_all_rules(db: Session, days_failed: int = 30, days_renewal: int = 7) -> Dict[str, Any]:
    all_findings = []
    all_findings.extend(detect_overdue_invoices(db))
    all_findings.extend(detect_failed_payments(db, days=days_failed))
    all_findings.extend(detect_duplicate_payments(db))
    all_findings.extend(detect_duplicate_invoices(db))
    all_findings.extend(detect_expired_discounts(db))
    all_findings.extend(detect_invoice_total_mismatches(db))
    all_findings.extend(detect_amount_paid_due_mismatch(db))
    all_findings.extend(detect_upcoming_renewals(db, days=days_renewal))
    all_findings.extend(detect_missing_invoices(db))
    all_findings.extend(detect_payment_invoice_customer_mismatch(db))

    by_severity = {s: 0 for s in (SEVERITY_LOW, SEVERITY_MEDIUM, SEVERITY_HIGH, SEVERITY_CRITICAL)}
    for f in all_findings:
        by_severity[f["severity"]] = by_severity.get(f["severity"], 0) + 1

    return {
        "total_findings": len(all_findings),
        "by_severity": by_severity,
        "findings": all_findings,
    }


def sync_guardian_findings(db: Session, days_failed: int = 30, days_renewal: int = 7):
    result = run_all_rules(db, days_failed, days_renewal)
    persisted = []
    for item in result["findings"]:
        fingerprint_source = f"{item['rule']}:{json.dumps(item['affected_ids'], sort_keys=True)}"
        fingerprint = hashlib.sha256(fingerprint_source.encode()).hexdigest()
        finding = db.query(GuardianFinding).filter(GuardianFinding.fingerprint == fingerprint).first()
        if not finding:
            finding = GuardianFinding(
                fingerprint=fingerprint, rule=item["rule"], title=item["title"],
                severity=item["severity"], certainty=item["certainty"],
                affected_ids=item["affected_ids"], evidence=item["evidence"],
                recommended_action=item["recommended_action"], status=GuardianFindingStatus.OPEN,
            )
            db.add(finding)
        else:
            finding.title = item["title"]; finding.severity = item["severity"]
            finding.certainty = item["certainty"]; finding.evidence = item["evidence"]
            finding.recommended_action = item["recommended_action"]
            if finding.status == GuardianFindingStatus.RESOLVED:
                finding.status = GuardianFindingStatus.OPEN; finding.resolved_at = None
        persisted.append(finding)
    db.commit()
    for finding in persisted: db.refresh(finding)
    return persisted
