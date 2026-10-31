import math
import uuid
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Product, ProductVariant, User, UserRole
from app.schemas.product import (
    ProductCreateRequest,
    ProductUpdateRequest,
    ProductResponse,
    ProductVariantCreateRequest,
    ProductVariantUpdateRequest,
    ProductVariantResponse,
)
from app.schemas.common import PaginatedResponse
from app.dependencies import get_current_user, require_staff_or_admin

router = APIRouter(prefix="/api/products", tags=["Products"])


@router.post(
    "", response_model=ProductResponse, status_code=status.HTTP_201_CREATED
)
def create_product(
    request: ProductCreateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_staff_or_admin),
):
    """Create a new product. Staff and Admin only. Prevents duplicate product codes."""
    code_normalized = request.code.strip().upper()
    existing = db.query(Product).filter(Product.code == code_normalized).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Product with code '{code_normalized}' already exists",
        )

    product = Product(
        name=request.name.strip(),
        code=code_normalized,
        description=request.description,
        product_type=request.product_type.strip().lower(),
        sales_price=request.sales_price,
        cost_price=request.cost_price,
        notes=request.notes,
        is_active=request.is_active,
        is_archived=False,
    )
    seen_variants = set()
    seen_skus = set()
    for variant_request in request.variants:
        variant_key = (
            variant_request.attribute_name.strip().casefold(),
            variant_request.value.strip().casefold(),
        )
        if variant_key in seen_variants:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Duplicate product variant attribute and value",
            )
        seen_variants.add(variant_key)

        sku = variant_request.sku.strip().upper() if variant_request.sku else None
        if sku and sku in seen_skus:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Duplicate variant SKU '{sku}'",
            )
        if sku and db.query(ProductVariant).filter(ProductVariant.sku == sku).first():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Variant SKU '{sku}' already exists",
            )
        if sku:
            seen_skus.add(sku)

        product.variants.append(
            ProductVariant(
                attribute_name=variant_request.attribute_name.strip(),
                value=variant_request.value.strip(),
                sku=sku,
                price_modifier=variant_request.price_modifier,
                is_active=variant_request.is_active,
            )
        )
    db.add(product)
    db.commit()
    db.refresh(product)
    return product


@router.get("", response_model=PaginatedResponse[ProductResponse])
def list_products(
    search: Optional[str] = Query(None, description="Search by name or code"),
    is_active: Optional[bool] = Query(None),
    is_archived: bool = Query(False, description="Include archived products"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List products. Customers only see active, non-archived products. Staff/Admin can view all with filters."""
    query = db.query(Product)

    if current_user.role == UserRole.CUSTOMER:
        query = query.filter(Product.is_active == True, Product.is_archived == False)
    else:
        if not is_archived:
            query = query.filter(Product.is_archived == False)
        if is_active is not None:
            query = query.filter(Product.is_active == is_active)

    if search:
        search_fmt = f"%{search.strip()}%"
        query = query.filter(
            or_(Product.name.ilike(search_fmt), Product.code.ilike(search_fmt))
        )

    total = query.count()
    items = query.order_by(Product.created_at.desc()).offset((page - 1) * page_size).limit(page_size).all()
    total_pages = math.ceil(total / page_size) if page_size > 0 else 0

    return PaginatedResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )


@router.get("/{product_id}", response_model=ProductResponse)
def get_product(
    product_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Retrieve an individual product."""
    product = db.query(Product).filter(Product.id == product_id).first()
    if not product:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Product not found",
        )

    if current_user.role == UserRole.CUSTOMER and (not product.is_active or product.is_archived):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Product not found or unavailable",
        )

    return product


@router.patch("/{product_id}", response_model=ProductResponse)
def update_product(
    product_id: uuid.UUID,
    request: ProductUpdateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_staff_or_admin),
):
    """Update product details. Staff and Admin only. Enforces code uniqueness."""
    product = db.query(Product).filter(Product.id == product_id).first()
    if not product:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Product not found",
        )

    update_data = request.model_dump(exclude_unset=True)
    if "code" in update_data and update_data["code"]:
        new_code = update_data["code"].strip().upper()
        if new_code != product.code:
            existing = db.query(Product).filter(Product.code == new_code).first()
            if existing:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Product with code '{new_code}' already exists",
                )
            update_data["code"] = new_code
    if "product_type" in update_data and update_data["product_type"]:
        update_data["product_type"] = update_data["product_type"].strip().lower()

    for field, value in update_data.items():
        setattr(product, field, value)

    db.commit()
    db.refresh(product)
    return product


@router.post(
    "/{product_id}/variants",
    response_model=ProductVariantResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_product_variant(
    product_id: uuid.UUID,
    request: ProductVariantCreateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_staff_or_admin),
):
    product = db.query(Product).filter(Product.id == product_id).first()
    if not product or product.is_archived:
        raise HTTPException(status_code=404, detail="Product not found or archived")

    attribute_name = request.attribute_name.strip()
    value = request.value.strip()
    duplicate = db.query(ProductVariant).filter(
        ProductVariant.product_id == product_id,
        ProductVariant.attribute_name == attribute_name,
        ProductVariant.value == value,
    ).first()
    if duplicate:
        raise HTTPException(status_code=400, detail="Product variant already exists")

    sku = request.sku.strip().upper() if request.sku else None
    if sku and db.query(ProductVariant).filter(ProductVariant.sku == sku).first():
        raise HTTPException(status_code=400, detail=f"Variant SKU '{sku}' already exists")

    variant = ProductVariant(
        product_id=product_id,
        attribute_name=attribute_name,
        value=value,
        sku=sku,
        price_modifier=request.price_modifier,
        is_active=request.is_active,
    )
    db.add(variant)
    db.commit()
    db.refresh(variant)
    return variant


@router.patch(
    "/{product_id}/variants/{variant_id}",
    response_model=ProductVariantResponse,
)
def update_product_variant(
    product_id: uuid.UUID,
    variant_id: uuid.UUID,
    request: ProductVariantUpdateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_staff_or_admin),
):
    variant = db.query(ProductVariant).filter(
        ProductVariant.id == variant_id,
        ProductVariant.product_id == product_id,
    ).first()
    if not variant:
        raise HTTPException(status_code=404, detail="Product variant not found")

    update_data = request.model_dump(exclude_unset=True)
    if "attribute_name" in update_data and update_data["attribute_name"]:
        update_data["attribute_name"] = update_data["attribute_name"].strip()
    if "value" in update_data and update_data["value"]:
        update_data["value"] = update_data["value"].strip()
    if "sku" in update_data:
        new_sku = update_data["sku"].strip().upper() if update_data["sku"] else None
        if new_sku and new_sku != variant.sku:
            duplicate_sku = db.query(ProductVariant).filter(ProductVariant.sku == new_sku).first()
            if duplicate_sku:
                raise HTTPException(status_code=400, detail=f"Variant SKU '{new_sku}' already exists")
        update_data["sku"] = new_sku

    next_attribute = update_data.get("attribute_name", variant.attribute_name)
    next_value = update_data.get("value", variant.value)
    duplicate = db.query(ProductVariant).filter(
        ProductVariant.product_id == product_id,
        ProductVariant.attribute_name == next_attribute,
        ProductVariant.value == next_value,
        ProductVariant.id != variant_id,
    ).first()
    if duplicate:
        raise HTTPException(status_code=400, detail="Product variant already exists")

    for field, value in update_data.items():
        setattr(variant, field, value)
    db.commit()
    db.refresh(variant)
    return variant


@router.delete(
    "/{product_id}/variants/{variant_id}",
    response_model=ProductVariantResponse,
)
def deactivate_product_variant(
    product_id: uuid.UUID,
    variant_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_staff_or_admin),
):
    variant = db.query(ProductVariant).filter(
        ProductVariant.id == variant_id,
        ProductVariant.product_id == product_id,
    ).first()
    if not variant:
        raise HTTPException(status_code=404, detail="Product variant not found")
    variant.is_active = False
    db.commit()
    db.refresh(variant)
    return variant


@router.delete("/{product_id}", response_model=ProductResponse)
def delete_or_archive_product(
    product_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_staff_or_admin),
):
    """Archive product. Historical references in plans or invoice items are preserved via archiving."""
    product = db.query(Product).filter(Product.id == product_id).first()
    if not product:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Product not found",
        )

    # Archive instead of deleting to preserve catalog & financial integrity
    product.is_archived = True
    product.is_active = False
    db.commit()
    db.refresh(product)
    return product
