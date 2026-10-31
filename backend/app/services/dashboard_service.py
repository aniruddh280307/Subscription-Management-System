"""
Dashboard and Reporting service.

All metrics are computed from real database aggregations — no hardcoded or
random values. MRR/ARR assumptions are documented inline.

MRR Methodology:
  - Only ACTIVE and TRIALING subscriptions contribute to MRR.
  - Each subscription contributes a normalized monthly amount based on its plan:
      daily   → price × 365 / (12 × interval_count)
      weekly  → price × 52 / (12 × interval_count)
      monthly → price / interval_count
      quarterly → price / (3 × interval_count)
      yearly  → price / (12 × interval_count)
  - ARR = MRR × 12.
  - Assumption: price on the plan is the per-cycle charge. This does not account
    for prorations, discounts, or mid-cycle changes.

Upcoming Renewals:
  - Subscriptions whose current_period_end falls within the next N days
    (default 30) and are ACTIVE or TRIALING.

Revenue Trend:
  - Monthly sum of succeeded payment amounts for the last N months.
  - Refunded amounts are NOT subtracted here (they appear as separate refund payments).
    Net revenue = sum(SUCCEEDED) - sum(REFUNDED payments).
"""

from datetime import datetime, timezone, date, timedelta
from decimal import Decimal
from typing import List, Optional

from sqlalchemy import func, extract, case, and_
from sqlalchemy.orm import Session

from app.models import (
    Customer,
    Subscription,
    SubscriptionPlan,
    SubscriptionStatus,
    BillingInterval,
    Invoice,
    InvoiceStatus,
    Payment,
    PaymentStatus,
    User,
    UserRole,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _normalize_to_monthly(price: Decimal, interval: BillingInterval, interval_count: int) -> Decimal:
    """Convert a plan's price to its Monthly Recurring Revenue contribution."""
    count = Decimal(interval_count)
    if interval == BillingInterval.DAILY:
        return price * Decimal("365") / (Decimal("12") * count)
    elif interval == BillingInterval.WEEKLY:
        return price * Decimal("52") / (Decimal("12") * count)
    elif interval == BillingInterval.MONTHLY:
        return price / count
    elif interval == BillingInterval.QUARTERLY:
        return price / (Decimal("3") * count)
    elif interval == BillingInterval.YEARLY:
        return price / (Decimal("12") * count)
    return Decimal("0")


# ---------------------------------------------------------------------------
# Summary metrics
# ---------------------------------------------------------------------------

def get_summary_metrics(db: Session) -> dict:
    """
    Return high-level counts and financial summaries:
    - total_customers (active)
    - total_subscriptions broken down by status
    - mrr (Monthly Recurring Revenue) and arr
    - total_invoiced (sum of all non-void/non-draft invoice totals)
    - total_collected (sum of succeeded payment amounts)
    - total_outstanding (sum of amount_due on open/overdue/partially_paid invoices)
    - overdue_invoices_count
    - overdue_invoices_amount
    """
    # Customer count
    total_customers = db.query(func.count(Customer.id)).filter(
        Customer.is_active == True, Customer.is_archived == False
    ).scalar() or 0

    # Subscription counts by status
    sub_counts_rows = (
        db.query(Subscription.status, func.count(Subscription.id))
        .group_by(Subscription.status)
        .all()
    )
    sub_counts = {row[0].value: row[1] for row in sub_counts_rows}

    # MRR — sum over active and trialing subscriptions
    active_subs = (
        db.query(Subscription, SubscriptionPlan)
        .join(SubscriptionPlan, Subscription.plan_id == SubscriptionPlan.id)
        .filter(
            Subscription.status.in_(
                [SubscriptionStatus.ACTIVE, SubscriptionStatus.TRIALING]
            )
        )
        .all()
    )
    mrr = sum(
        (
            _normalize_to_monthly(plan.price, plan.billing_interval, plan.interval_count)
            for _, plan in active_subs
        ),
        Decimal("0"),
    )
    mrr = mrr.quantize(Decimal("0.01"))
    arr = (mrr * Decimal("12")).quantize(Decimal("0.01"))

    # Total invoiced (non-void, non-draft)
    total_invoiced = db.query(func.coalesce(func.sum(Invoice.total_amount), 0)).filter(
        Invoice.status.notin_([InvoiceStatus.VOID, InvoiceStatus.DRAFT])
    ).scalar()
    total_invoiced = Decimal(str(total_invoiced)).quantize(Decimal("0.01"))

    # Total collected (succeeded payments)
    total_collected = db.query(func.coalesce(func.sum(Payment.amount), 0)).filter(
        Payment.status == PaymentStatus.SUCCEEDED
    ).scalar()
    total_collected = Decimal(str(total_collected)).quantize(Decimal("0.01"))

    # Total outstanding
    total_outstanding = db.query(func.coalesce(func.sum(Invoice.amount_due), 0)).filter(
        Invoice.status.in_(
            [InvoiceStatus.OPEN, InvoiceStatus.OVERDUE, InvoiceStatus.PARTIALLY_PAID]
        )
    ).scalar()
    total_outstanding = Decimal(str(total_outstanding)).quantize(Decimal("0.01"))

    # Overdue invoices (status = overdue OR is_overdue computed property where possible)
    overdue_count = db.query(func.count(Invoice.id)).filter(
        Invoice.status == InvoiceStatus.OVERDUE
    ).scalar() or 0

    overdue_amount = db.query(func.coalesce(func.sum(Invoice.amount_due), 0)).filter(
        Invoice.status == InvoiceStatus.OVERDUE
    ).scalar()
    overdue_amount = Decimal(str(overdue_amount)).quantize(Decimal("0.01"))

    return {
        "total_active_customers": total_customers,
        "subscriptions_by_status": sub_counts,
        "mrr": str(mrr),
        "arr": str(arr),
        "total_invoiced": str(total_invoiced),
        "total_collected": str(total_collected),
        "total_outstanding": str(total_outstanding),
        "overdue_invoices_count": overdue_count,
        "overdue_invoices_amount": str(overdue_amount),
        "mrr_assumption": (
            "MRR includes only ACTIVE and TRIALING subscriptions. "
            "Amounts are normalized: weekly×(52/12), monthly×1, quarterly÷3, yearly÷12. "
            "Prorations, one-time charges and discounts are not accounted for."
        ),
    }


# ---------------------------------------------------------------------------
# Revenue trend (monthly breakdown)
# ---------------------------------------------------------------------------

def get_revenue_trend(db: Session, months: int = 12) -> List[dict]:
    """
    Return monthly revenue for the last N months.
    Each entry: { year, month, gross_revenue, refunded_amount, net_revenue }.
    Gross = sum of SUCCEEDED payment amounts.
    Refunded = sum of refunded_amount on REFUNDED/PARTIALLY_REFUNDED payments.
    Net = Gross - Refunded.
    """
    now = datetime.now(timezone.utc)
    results = []

    for i in range(months - 1, -1, -1):
        # Compute target year/month
        total_months = now.month - 1 - i
        year = now.year + total_months // 12
        month = total_months % 12 + 1
        if total_months < 0:
            year = now.year - (-total_months + 11) // 12
            month = 12 - (-total_months - 1) % 12

        gross = db.query(func.coalesce(func.sum(Payment.amount), 0)).filter(
            Payment.status == PaymentStatus.SUCCEEDED,
            extract("year", Payment.created_at) == year,
            extract("month", Payment.created_at) == month,
        ).scalar()

        refunded = db.query(func.coalesce(func.sum(Payment.refunded_amount), 0)).filter(
            Payment.status.in_([PaymentStatus.REFUNDED, PaymentStatus.PARTIALLY_REFUNDED]),
            extract("year", Payment.created_at) == year,
            extract("month", Payment.created_at) == month,
        ).scalar()

        gross = Decimal(str(gross)).quantize(Decimal("0.01"))
        refunded = Decimal(str(refunded)).quantize(Decimal("0.01"))

        results.append({
            "year": year,
            "month": month,
            "gross_revenue": str(gross),
            "refunded_amount": str(refunded),
            "net_revenue": str((gross - refunded).quantize(Decimal("0.01"))),
        })

    return results


# ---------------------------------------------------------------------------
# Upcoming renewals
# ---------------------------------------------------------------------------

def get_upcoming_renewals(db: Session, days: int = 30) -> List[dict]:
    """
    Return subscriptions whose current_period_end falls within the next N days
    and are in ACTIVE or TRIALING status.
    Ordered by current_period_end ascending.
    """
    now = datetime.now(timezone.utc)
    cutoff = now + timedelta(days=days)

    rows = (
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
        .order_by(Subscription.current_period_end.asc())
        .limit(100)
        .all()
    )

    return [
        {
            "subscription_id": str(sub.id),
            "customer_id": str(customer.id),
            "customer_name": customer.name,
            "customer_email": customer.email,
            "plan_name": plan.name,
            "plan_code": plan.code,
            "billing_interval": plan.billing_interval.value,
            "price": str(plan.price),
            "currency": plan.currency,
            "current_period_end": sub.current_period_end.isoformat(),
            "status": sub.status.value,
        }
        for sub, plan, customer in rows
    ]


# ---------------------------------------------------------------------------
# Invoice status breakdown
# ---------------------------------------------------------------------------

def get_invoice_status_breakdown(db: Session) -> List[dict]:
    """Count and sum total_amount for invoices grouped by status."""
    rows = (
        db.query(
            Invoice.status,
            func.count(Invoice.id).label("count"),
            func.coalesce(func.sum(Invoice.total_amount), 0).label("total_amount"),
            func.coalesce(func.sum(Invoice.amount_due), 0).label("amount_due"),
        )
        .group_by(Invoice.status)
        .all()
    )
    return [
        {
            "status": row.status.value,
            "count": row.count,
            "total_amount": str(Decimal(str(row.total_amount)).quantize(Decimal("0.01"))),
            "amount_due": str(Decimal(str(row.amount_due)).quantize(Decimal("0.01"))),
        }
        for row in rows
    ]


# ---------------------------------------------------------------------------
# Payment outcome breakdown
# ---------------------------------------------------------------------------

def get_payment_outcomes(db: Session) -> List[dict]:
    """Count and sum payment amounts grouped by status."""
    rows = (
        db.query(
            Payment.status,
            func.count(Payment.id).label("count"),
            func.coalesce(func.sum(Payment.amount), 0).label("total_amount"),
        )
        .group_by(Payment.status)
        .all()
    )
    return [
        {
            "status": row.status.value,
            "count": row.count,
            "total_amount": str(Decimal(str(row.total_amount)).quantize(Decimal("0.01"))),
        }
        for row in rows
    ]
