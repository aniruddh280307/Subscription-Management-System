import calendar
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Optional, Tuple
from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.models import (
    Subscription,
    SubscriptionPlan,
    Customer,
    User,
    UserRole,
    SubscriptionStatus,
    BillingInterval,
    AuditLog,
)

# Valid state transition graph for strict lifecycle safety
VALID_TRANSITIONS = {
    SubscriptionStatus.TRIALING: {
        SubscriptionStatus.ACTIVE,
        SubscriptionStatus.CANCELED,
        SubscriptionStatus.PAUSED,
    },
    SubscriptionStatus.ACTIVE: {
        SubscriptionStatus.PAST_DUE,
        SubscriptionStatus.PAUSED,
        SubscriptionStatus.CANCELED,
    },
    SubscriptionStatus.PAST_DUE: {
        SubscriptionStatus.ACTIVE,
        SubscriptionStatus.CANCELED,
        SubscriptionStatus.EXPIRED,
    },
    SubscriptionStatus.PAUSED: {
        SubscriptionStatus.ACTIVE,
        SubscriptionStatus.CANCELED,
    },
    SubscriptionStatus.CANCELED: set(),  # Terminal
    SubscriptionStatus.EXPIRED: set(),   # Terminal
}


def _next_subscription_number(db: Session) -> str:
    prefix = f"SUB-{datetime.now(timezone.utc).strftime('%Y%m')}-"
    count = db.query(Subscription).filter(Subscription.subscription_number.like(f"{prefix}%")).count()
    return f"{prefix}{count + 1:06d}"


def add_calendar_months(dt: datetime, months: int) -> datetime:
    """Add calendar months preserving time and capping to last day of month."""
    month = dt.month - 1 + months
    year = dt.year + month // 12
    month = month % 12 + 1
    max_day = calendar.monthrange(year, month)[1]
    day = min(dt.day, max_day)
    return dt.replace(year=year, month=month, day=day)


def calculate_period_end(
    start_date: datetime, interval: BillingInterval, interval_count: int = 1
) -> datetime:
    """Calculate the next period end date handling leap years and month-end dates safely."""
    if interval == BillingInterval.DAILY:
        return start_date + timedelta(days=interval_count)
    elif interval == BillingInterval.WEEKLY:
        return start_date + timedelta(weeks=interval_count)
    elif interval == BillingInterval.MONTHLY:
        return add_calendar_months(start_date, 1 * interval_count)
    elif interval == BillingInterval.QUARTERLY:
        return add_calendar_months(start_date, 3 * interval_count)
    elif interval == BillingInterval.YEARLY:
        return add_calendar_months(start_date, 12 * interval_count)
    raise ValueError(f"Unsupported billing interval: {interval}")


def record_audit_log(
    db: Session,
    user: Optional[User],
    action: str,
    entity_id: uuid.UUID,
    old_values: Optional[dict] = None,
    new_values: Optional[dict] = None,
) -> AuditLog:
    """Record an explainable audit log entry."""
    audit = AuditLog(
        user_id=user.id if user else None,
        action=action,
        entity_type="subscription",
        entity_id=str(entity_id),
        old_values=old_values,
        new_values=new_values,
    )
    db.add(audit)
    return audit


def create_subscription(
    db: Session,
    current_user: User,
    customer_id: uuid.UUID,
    plan_id: uuid.UUID,
    custom_start_date: Optional[datetime] = None,
    quantity: int = 1,
    payment_terms_days: Optional[int] = None,
    quotation_id: Optional[uuid.UUID] = None,
    expiry_date: Optional[date] = None,
) -> Subscription:
    """Create a subscription with trial support, accurate billing cycle calculation, and customer isolation."""
    customer = db.query(Customer).filter(Customer.id == customer_id).first()
    if not customer:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Customer '{customer_id}' does not exist",
        )
    if customer.is_archived or not customer.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot create subscription for inactive or archived customer",
        )

    # Customer user access restriction
    if current_user.role == UserRole.CUSTOMER and customer.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access forbidden: you can only create subscriptions for your own account",
        )

    plan = db.query(SubscriptionPlan).filter(SubscriptionPlan.id == plan_id).first()
    if not plan:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Subscription plan '{plan_id}' does not exist",
        )
    if plan.is_archived or not plan.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot subscribe to an inactive or archived plan",
        )
    today = date.today()
    if (
        (plan.available_from is not None and plan.available_from > today)
        or (plan.available_until is not None and plan.available_until < today)
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot subscribe to a plan outside its availability period",
        )

    start_date = custom_start_date or datetime.now(timezone.utc)
    now = datetime.now(timezone.utc)

    if plan.trial_period_days > 0:
        trial_start = start_date
        trial_end = start_date + timedelta(days=plan.trial_period_days)
        sub_status = SubscriptionStatus.TRIALING
        period_start = start_date
        period_end = trial_end
    else:
        trial_start = None
        trial_end = None
        sub_status = SubscriptionStatus.ACTIVE
        period_start = start_date
        period_end = calculate_period_end(
            start_date, plan.billing_interval, plan.interval_count
        )

    subscription = Subscription(
        subscription_number=_next_subscription_number(db),
        customer_id=customer_id,
        plan_id=plan_id,
        quotation_id=quotation_id,
        quantity=max(quantity, plan.minimum_quantity),
        payment_terms_days=payment_terms_days if payment_terms_days is not None else plan.payment_terms_days,
        expiry_date=expiry_date,
        status=sub_status,
        start_date=start_date,
        current_period_start=period_start,
        current_period_end=period_end,
        trial_start=trial_start,
        trial_end=trial_end,
        cancel_at_period_end=False,
    )
    db.add(subscription)
    db.flush()

    record_audit_log(
        db=db,
        user=current_user,
        action="SUBSCRIPTION_CREATE",
        entity_id=subscription.id,
        new_values={
            "customer_id": str(customer_id),
            "plan_id": str(plan_id),
            "status": sub_status.value,
            "period_end": period_end.isoformat(),
            "trial_period_days": plan.trial_period_days,
        },
    )

    db.commit()
    db.refresh(subscription)
    return subscription


def cancel_subscription(
    db: Session,
    current_user: User,
    subscription: Subscription,
    immediately: bool = False,
) -> Subscription:
    """Cancel subscription immediately or flag for cancellation at period end."""
    if subscription.status in (SubscriptionStatus.CANCELED, SubscriptionStatus.EXPIRED):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Subscription is already {subscription.status.value}",
        )

    if immediately and not subscription.plan.allow_early_close:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This plan does not allow early closure",
        )

    old_status = subscription.status.value
    now = datetime.now(timezone.utc)

    if immediately:
        if SubscriptionStatus.CANCELED not in VALID_TRANSITIONS.get(
            subscription.status, set()
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Cannot transition status from '{subscription.status.value}' to 'canceled'",
            )
        subscription.status = SubscriptionStatus.CANCELED
        subscription.canceled_at = now
        subscription.ended_at = now
        subscription.cancel_at_period_end = False
    else:
        subscription.cancel_at_period_end = True
        subscription.canceled_at = now

    record_audit_log(
        db=db,
        user=current_user,
        action="SUBSCRIPTION_CANCEL",
        entity_id=subscription.id,
        old_values={"status": old_status, "cancel_at_period_end": False},
        new_values={
            "status": subscription.status.value,
            "cancel_at_period_end": subscription.cancel_at_period_end,
            "immediately": immediately,
            "ended_at": subscription.ended_at.isoformat()
            if subscription.ended_at
            else None,
        },
    )

    db.commit()
    db.refresh(subscription)
    return subscription


def pause_subscription(
    db: Session, current_user: User, subscription: Subscription
) -> Subscription:
    """Pause an active or trialing subscription."""
    if not subscription.plan.allow_pause:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This plan does not allow subscriptions to be paused",
        )
    if subscription.status == SubscriptionStatus.PAUSED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Subscription is already paused",
        )

    if SubscriptionStatus.PAUSED not in VALID_TRANSITIONS.get(
        subscription.status, set()
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot pause subscription in '{subscription.status.value}' state",
        )

    old_status = subscription.status.value
    subscription.status = SubscriptionStatus.PAUSED

    record_audit_log(
        db=db,
        user=current_user,
        action="SUBSCRIPTION_PAUSE",
        entity_id=subscription.id,
        old_values={"status": old_status},
        new_values={"status": subscription.status.value},
    )

    db.commit()
    db.refresh(subscription)
    return subscription


def resume_subscription(
    db: Session, current_user: User, subscription: Subscription
) -> Subscription:
    """Resume a paused subscription."""
    if subscription.status != SubscriptionStatus.PAUSED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot resume subscription with status '{subscription.status.value}' (must be paused)",
        )

    now = datetime.now(timezone.utc)
    if subscription.trial_end and now < subscription.trial_end:
        new_status = SubscriptionStatus.TRIALING
    else:
        new_status = SubscriptionStatus.ACTIVE

    old_status = subscription.status.value
    subscription.status = new_status

    record_audit_log(
        db=db,
        user=current_user,
        action="SUBSCRIPTION_RESUME",
        entity_id=subscription.id,
        old_values={"status": old_status},
        new_values={"status": subscription.status.value},
    )

    db.commit()
    db.refresh(subscription)
    return subscription


def change_subscription_plan(
    db: Session,
    current_user: User,
    subscription: Subscription,
    new_plan_id: uuid.UUID,
    immediately: bool = True,
) -> Subscription:
    """Upgrade or downgrade plan safely while preserving history and audit logs."""
    if subscription.status in (SubscriptionStatus.CANCELED, SubscriptionStatus.EXPIRED):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot change plan of a {subscription.status.value} subscription",
        )

    if subscription.plan_id == new_plan_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Subscription is already on this plan",
        )

    new_plan = (
        db.query(SubscriptionPlan).filter(SubscriptionPlan.id == new_plan_id).first()
    )
    if not new_plan:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Plan '{new_plan_id}' does not exist",
        )
    if new_plan.is_archived or not new_plan.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot change to an inactive or archived plan",
        )
    today = date.today()
    if (
        (new_plan.available_from is not None and new_plan.available_from > today)
        or (new_plan.available_until is not None and new_plan.available_until < today)
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot change to a plan outside its availability period",
        )

    old_plan_id = subscription.plan_id
    now = datetime.now(timezone.utc)

    if immediately:
        subscription.plan_id = new_plan_id
        # Recalculate current period end based on new interval starting from current period start
        subscription.current_period_end = calculate_period_end(
            subscription.current_period_start,
            new_plan.billing_interval,
            new_plan.interval_count,
        )

    record_audit_log(
        db=db,
        user=current_user,
        action="SUBSCRIPTION_PLAN_CHANGE",
        entity_id=subscription.id,
        old_values={"plan_id": str(old_plan_id)},
        new_values={
            "plan_id": str(new_plan_id),
            "immediately": immediately,
            "new_period_end": subscription.current_period_end.isoformat(),
        },
    )

    db.commit()
    db.refresh(subscription)
    return subscription
