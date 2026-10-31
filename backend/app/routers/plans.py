import math
import uuid
from datetime import date
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Product, SubscriptionPlan, Subscription, User, UserRole
from app.schemas.plan import (
    PlanCreateRequest,
    PlanUpdateRequest,
    PlanResponse,
)
from app.schemas.common import PaginatedResponse
from app.dependencies import get_current_user, require_staff_or_admin

router = APIRouter(prefix="/api/plans", tags=["Subscription Plans"])


@router.post(
    "", response_model=PlanResponse, status_code=status.HTTP_201_CREATED
)
def create_plan(
    request: PlanCreateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_staff_or_admin),
):
    """Create a subscription plan under an existing product. Staff and Admin only."""
    product = db.query(Product).filter(Product.id == request.product_id).first()
    if not product:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Product '{request.product_id}' does not exist",
        )
    if product.is_archived:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot add plans to an archived product",
        )

    code_normalized = request.code.strip().upper()
    existing = db.query(SubscriptionPlan).filter(SubscriptionPlan.code == code_normalized).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Subscription plan with code '{code_normalized}' already exists",
        )

    if request.price < 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Price cannot be negative",
        )
    if request.trial_period_days < 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Trial period days cannot be negative",
        )
    if request.interval_count < 1:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Interval count must be at least 1",
        )

    plan = SubscriptionPlan(
        product_id=request.product_id,
        name=request.name.strip(),
        code=code_normalized,
        description=request.description,
        billing_interval=request.billing_interval,
        interval_count=request.interval_count,
        price=request.price,
        currency=request.currency.upper(),
        trial_period_days=request.trial_period_days,
        minimum_quantity=request.minimum_quantity,
        available_from=request.available_from,
        available_until=request.available_until,
        payment_terms_days=request.payment_terms_days,
        auto_close_on_end=request.auto_close_on_end,
        allow_early_close=request.allow_early_close,
        allow_pause=request.allow_pause,
        allow_renewal=request.allow_renewal,
        is_active=request.is_active,
        is_archived=False,
    )
    db.add(plan)
    db.commit()
    db.refresh(plan)
    return plan


@router.get("", response_model=PaginatedResponse[PlanResponse])
def list_plans(
    product_id: Optional[uuid.UUID] = Query(None, description="Filter by parent product"),
    search: Optional[str] = Query(None, description="Search by name or code"),
    is_active: Optional[bool] = Query(None),
    is_archived: bool = Query(False, description="Include archived plans"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List subscription plans. Customers see active, non-archived plans. Staff/Admin can view all with filters."""
    query = db.query(SubscriptionPlan)

    if product_id:
        query = query.filter(SubscriptionPlan.product_id == product_id)

    if current_user.role == UserRole.CUSTOMER:
        today = date.today()
        query = query.filter(
            SubscriptionPlan.is_active == True,
            SubscriptionPlan.is_archived == False,
            (SubscriptionPlan.available_from.is_(None) | (SubscriptionPlan.available_from <= today)),
            (SubscriptionPlan.available_until.is_(None) | (SubscriptionPlan.available_until >= today)),
        )
    else:
        if not is_archived:
            query = query.filter(SubscriptionPlan.is_archived == False)
        if is_active is not None:
            query = query.filter(SubscriptionPlan.is_active == is_active)

    if search:
        search_fmt = f"%{search.strip()}%"
        query = query.filter(
            or_(
                SubscriptionPlan.name.ilike(search_fmt),
                SubscriptionPlan.code.ilike(search_fmt),
            )
        )

    total = query.count()
    items = query.order_by(SubscriptionPlan.created_at.desc()).offset((page - 1) * page_size).limit(page_size).all()
    total_pages = math.ceil(total / page_size) if page_size > 0 else 0

    return PaginatedResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )


@router.get("/{plan_id}", response_model=PlanResponse)
def get_plan(
    plan_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Retrieve an individual plan."""
    plan = db.query(SubscriptionPlan).filter(SubscriptionPlan.id == plan_id).first()
    if not plan:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Subscription plan not found",
        )

    if current_user.role == UserRole.CUSTOMER:
        today = date.today()
        unavailable = (
            not plan.is_active
            or plan.is_archived
            or (plan.available_from is not None and plan.available_from > today)
            or (plan.available_until is not None and plan.available_until < today)
        )
        if unavailable:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Subscription plan not found or unavailable",
            )

    return plan


@router.patch("/{plan_id}", response_model=PlanResponse)
def update_plan(
    plan_id: uuid.UUID,
    request: PlanUpdateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_staff_or_admin),
):
    """Update subscription plan details. Staff and Admin only. Enforces code uniqueness."""
    plan = db.query(SubscriptionPlan).filter(SubscriptionPlan.id == plan_id).first()
    if not plan:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Subscription plan not found",
        )

    update_data = request.model_dump(exclude_unset=True)
    if "code" in update_data and update_data["code"]:
        new_code = update_data["code"].strip().upper()
        if new_code != plan.code:
            existing = (
                db.query(SubscriptionPlan)
                .filter(SubscriptionPlan.code == new_code)
                .first()
            )
            if existing:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Subscription plan with code '{new_code}' already exists",
                )
            update_data["code"] = new_code

    if "price" in update_data and update_data["price"] is not None and update_data["price"] < 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Price cannot be negative",
        )
    if (
        "trial_period_days" in update_data
        and update_data["trial_period_days"] is not None
        and update_data["trial_period_days"] < 0
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Trial period days cannot be negative",
        )
    if (
        "interval_count" in update_data
        and update_data["interval_count"] is not None
        and update_data["interval_count"] < 1
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Interval count must be at least 1",
        )
    if "currency" in update_data and update_data["currency"]:
        update_data["currency"] = update_data["currency"].upper()

    next_available_from = update_data.get("available_from", plan.available_from)
    next_available_until = update_data.get("available_until", plan.available_until)
    if (
        next_available_from is not None
        and next_available_until is not None
        and next_available_until < next_available_from
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="available_until must be on or after available_from",
        )

    for field, value in update_data.items():
        setattr(plan, field, value)

    db.commit()
    db.refresh(plan)
    return plan


@router.delete("/{plan_id}", response_model=PlanResponse)
def delete_or_archive_plan(
    plan_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_staff_or_admin),
):
    """Archive plan. Historical references in subscriptions are preserved via archiving."""
    plan = db.query(SubscriptionPlan).filter(SubscriptionPlan.id == plan_id).first()
    if not plan:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Subscription plan not found",
        )

    # Archive to preserve historical references
    plan.is_archived = True
    plan.is_active = False
    db.commit()
    db.refresh(plan)
    return plan
