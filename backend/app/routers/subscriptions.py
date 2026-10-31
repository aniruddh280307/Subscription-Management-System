import math
import uuid
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session, joinedload

from app.database import get_db
from app.models import Subscription, Customer, User, UserRole, SubscriptionStatus
from app.schemas.subscription import (
    SubscriptionCreateRequest,
    SubscriptionUpdateRequest,
    SubscriptionCancelRequest,
    SubscriptionPlanChangeRequest,
    SubscriptionResponse,
)
from app.schemas.common import PaginatedResponse
from app.dependencies import get_current_user
from app.services.subscription_service import (
    create_subscription as svc_create_subscription,
    cancel_subscription as svc_cancel_subscription,
    pause_subscription as svc_pause_subscription,
    resume_subscription as svc_resume_subscription,
    change_subscription_plan as svc_change_plan,
)

router = APIRouter(prefix="/api/subscriptions", tags=["Subscriptions"])


def get_subscription_or_404(
    subscription_id: uuid.UUID, db: Session, current_user: User
) -> Subscription:
    """Helper to retrieve subscription with customer ownership check."""
    subscription = (
        db.query(Subscription)
        .options(joinedload(Subscription.plan), joinedload(Subscription.customer))
        .filter(Subscription.id == subscription_id)
        .first()
    )
    if not subscription:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Subscription not found",
        )

    if current_user.role == UserRole.CUSTOMER:
        if subscription.customer.user_id != current_user.id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access forbidden: you can only access your own subscriptions",
            )
    return subscription


@router.post(
    "", response_model=SubscriptionResponse, status_code=status.HTTP_201_CREATED
)
def create_subscription_endpoint(
    request: SubscriptionCreateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Create a subscription for a customer under an active plan."""
    return svc_create_subscription(
        db=db,
        current_user=current_user,
        customer_id=request.customer_id,
        plan_id=request.plan_id,
        custom_start_date=request.start_date,
        quantity=request.quantity,
        payment_terms_days=request.payment_terms_days,
        expiry_date=request.expiry_date,
    )


@router.get("", response_model=PaginatedResponse[SubscriptionResponse])
def list_subscriptions(
    customer_id: Optional[uuid.UUID] = Query(None, description="Filter by customer"),
    plan_id: Optional[uuid.UUID] = Query(None, description="Filter by plan"),
    subscription_status: Optional[SubscriptionStatus] = Query(
        None, alias="status", description="Filter by status"
    ),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List subscriptions with pagination and status/plan/customer filters. Customers see only their own."""
    query = db.query(Subscription).options(
        joinedload(Subscription.plan), joinedload(Subscription.customer)
    )

    if current_user.role == UserRole.CUSTOMER:
        query = query.join(Customer).filter(Customer.user_id == current_user.id)
    elif customer_id:
        query = query.filter(Subscription.customer_id == customer_id)

    if plan_id:
        query = query.filter(Subscription.plan_id == plan_id)

    if subscription_status:
        query = query.filter(Subscription.status == subscription_status)

    total = query.count()
    items = (
        query.order_by(Subscription.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    total_pages = math.ceil(total / page_size) if page_size > 0 else 0

    return PaginatedResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )


@router.get("/{id}", response_model=SubscriptionResponse)
def get_subscription(
    id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Retrieve an individual subscription. Customers only see their own."""
    return get_subscription_or_404(id, db, current_user)


@router.patch("/{id}", response_model=SubscriptionResponse)
def update_subscription(
    id: uuid.UUID,
    request: SubscriptionUpdateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Update permitted subscription details (e.g. cancel_at_period_end). Rejects arbitrary status changes."""
    subscription = get_subscription_or_404(id, db, current_user)

    if request.cancel_at_period_end is not None:
        subscription.cancel_at_period_end = request.cancel_at_period_end

    db.commit()
    db.refresh(subscription)
    return subscription


@router.post("/{id}/cancel", response_model=SubscriptionResponse)
def cancel_subscription_endpoint(
    id: uuid.UUID,
    request: SubscriptionCancelRequest = SubscriptionCancelRequest(),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Cancel subscription immediately or flag for cancellation at period end."""
    subscription = get_subscription_or_404(id, db, current_user)
    return svc_cancel_subscription(
        db=db,
        current_user=current_user,
        subscription=subscription,
        immediately=request.immediately,
    )


@router.post("/{id}/pause", response_model=SubscriptionResponse)
def pause_subscription_endpoint(
    id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Pause an active or trialing subscription."""
    subscription = get_subscription_or_404(id, db, current_user)
    return svc_pause_subscription(
        db=db, current_user=current_user, subscription=subscription
    )


@router.post("/{id}/resume", response_model=SubscriptionResponse)
def resume_subscription_endpoint(
    id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Resume a paused subscription."""
    subscription = get_subscription_or_404(id, db, current_user)
    return svc_resume_subscription(
        db=db, current_user=current_user, subscription=subscription
    )


@router.post("/{id}/change-plan", response_model=SubscriptionResponse)
def change_plan_endpoint(
    id: uuid.UUID,
    request: SubscriptionPlanChangeRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Upgrade or downgrade plan safely while preserving history and audit logs."""
    subscription = get_subscription_or_404(id, db, current_user)
    return svc_change_plan(
        db=db,
        current_user=current_user,
        subscription=subscription,
        new_plan_id=request.new_plan_id,
        immediately=request.immediately,
    )
