import math
import uuid
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Customer, User, UserRole, Subscription, Invoice, Payment
from app.schemas.customer import (
    CustomerCreateRequest,
    CustomerUpdateRequest,
    CustomerResponse,
)
from app.schemas.common import PaginatedResponse
from app.dependencies import get_current_user, require_staff_or_admin

router = APIRouter(prefix="/api/customers", tags=["Customers"])


@router.post(
    "", response_model=CustomerResponse, status_code=status.HTTP_201_CREATED
)
def create_customer(
    request: CustomerCreateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Create a customer record. Customers can only link to themselves; Staff/Admin can specify user_id."""
    target_user_id = request.user_id
    if current_user.role == UserRole.CUSTOMER:
        target_user_id = current_user.id
        # Prevent customer user from creating multiple customer profiles
        existing = db.query(Customer).filter(Customer.user_id == current_user.id).first()
        if existing:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="A customer profile already exists for your account",
            )
    elif target_user_id:
        # Verify user exists
        user = db.query(User).filter(User.id == target_user_id).first()
        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"User '{target_user_id}' does not exist",
            )

    customer = Customer(
        user_id=target_user_id,
        name=request.name,
        email=request.email,
        phone=request.phone,
        billing_address=request.billing_address,
        currency=request.currency.upper(),
        is_active=True,
        is_archived=False,
    )
    db.add(customer)
    db.commit()
    db.refresh(customer)
    return customer


@router.get("", response_model=PaginatedResponse[CustomerResponse])
def list_customers(
    search: Optional[str] = Query(None, description="Search by name or email"),
    is_active: Optional[bool] = Query(None),
    is_archived: bool = Query(False, description="Include archived customers"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List customers with search, pagination and filtering. Customers see only their own profile."""
    query = db.query(Customer)

    if current_user.role == UserRole.CUSTOMER:
        query = query.filter(Customer.user_id == current_user.id)
    else:
        if not is_archived:
            query = query.filter(Customer.is_archived == False)

    if is_active is not None:
        query = query.filter(Customer.is_active == is_active)

    if search:
        search_fmt = f"%{search.strip()}%"
        query = query.filter(
            or_(Customer.name.ilike(search_fmt), Customer.email.ilike(search_fmt))
        )

    total = query.count()
    items = query.order_by(Customer.created_at.desc()).offset((page - 1) * page_size).limit(page_size).all()
    total_pages = math.ceil(total / page_size) if page_size > 0 else 0

    return PaginatedResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )


@router.get("/{customer_id}", response_model=CustomerResponse)
def get_customer(
    customer_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Retrieve an individual customer. Customers can only retrieve their own record."""
    customer = db.query(Customer).filter(Customer.id == customer_id).first()
    if not customer:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Customer not found",
        )

    if current_user.role == UserRole.CUSTOMER and customer.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access forbidden: you may only view your own customer record",
        )

    return customer


@router.patch("/{customer_id}", response_model=CustomerResponse)
def update_customer(
    customer_id: uuid.UUID,
    request: CustomerUpdateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Update customer details. Customers can update profile info; Staff/Admin can update all fields."""
    customer = db.query(Customer).filter(Customer.id == customer_id).first()
    if not customer:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Customer not found",
        )

    if current_user.role == UserRole.CUSTOMER:
        if customer.user_id != current_user.id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access forbidden: you may only update your own customer record",
            )
        # Customers cannot archive themselves
        if request.is_archived is not None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Customers cannot change archive status",
            )

    update_data = request.model_dump(exclude_unset=True)
    if "currency" in update_data and update_data["currency"]:
        update_data["currency"] = update_data["currency"].upper()

    for field, value in update_data.items():
        setattr(customer, field, value)

    db.commit()
    db.refresh(customer)
    return customer


@router.delete("/{customer_id}", response_model=CustomerResponse)
def delete_or_archive_customer(
    customer_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_staff_or_admin),
):
    """Archive a customer record. Deactivation/archiving preserves financial and subscription history."""
    customer = db.query(Customer).filter(Customer.id == customer_id).first()
    if not customer:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Customer not found",
        )

    # Check references: subscriptions, invoices, payments
    has_subscriptions = (
        db.query(Subscription).filter(Subscription.customer_id == customer_id).count() > 0
    )
    has_invoices = (
        db.query(Invoice).filter(Invoice.customer_id == customer_id).count() > 0
    )
    has_payments = (
        db.query(Payment).filter(Payment.customer_id == customer_id).count() > 0
    )

    if has_subscriptions or has_invoices or has_payments:
        # Preserve historical reference: archive instead of deleting
        customer.is_archived = True
        customer.is_active = False
        db.commit()
        db.refresh(customer)
        return customer

    # If completely unreferenced, archive as well per requirements
    customer.is_archived = True
    customer.is_active = False
    db.commit()
    db.refresh(customer)
    return customer
