import uuid
from decimal import Decimal
from app.models import User, UserRole, Product, SubscriptionPlan
from app.security import hash_password


def test_subscription_plan_lifecycle(client, db_session, auth_headers):
    staff_user = User(
        email=f"plan_staff_{uuid.uuid4().hex[:8]}@example.com",
        hashed_password=hash_password("pw"),
        full_name="Plan Staff",
        role=UserRole.STAFF,
        is_active=True,
    )
    cust_user = User(
        email=f"plan_cust_{uuid.uuid4().hex[:8]}@example.com",
        hashed_password=hash_password("pw"),
        full_name="Plan Cust",
        role=UserRole.CUSTOMER,
        is_active=True,
    )
    db_session.add_all([staff_user, cust_user])
    db_session.commit()

    code_prefix = f"PLN-{uuid.uuid4().hex[:6].upper()}"
    prod_code = f"PROD-{code_prefix}"
    plan_code = f"PLAN-{code_prefix}-M"

    product = Product(
        name="Billing Platform Suite",
        code=prod_code,
        description="Core billing suite",
        is_active=True,
        is_archived=False,
    )
    db_session.add(product)
    db_session.commit()
    db_session.refresh(product)

    try:
        staff_headers = auth_headers(staff_user)
        cust_headers = auth_headers(cust_user)

        # 1. Reject plan under non-existent product
        bad_prod_res = client.post(
            "/api/plans",
            json={
                "product_id": str(uuid.uuid4()),
                "name": "Ghost Plan",
                "code": f"GHOST-{code_prefix}",
                "billing_interval": "monthly",
                "price": 29.99,
            },
            headers=staff_headers,
        )
        assert bad_prod_res.status_code == 404

        # 2. Reject negative price
        neg_price_res = client.post(
            "/api/plans",
            json={
                "product_id": str(product.id),
                "name": "Invalid Price Plan",
                "code": f"NEG-{code_prefix}",
                "billing_interval": "monthly",
                "price": -10.00,
            },
            headers=staff_headers,
        )
        assert neg_price_res.status_code in (400, 422)

        # 3. Create valid plan
        plan_res = client.post(
            "/api/plans",
            json={
                "product_id": str(product.id),
                "name": "Professional Daily",
                "code": plan_code,
                "description": "Flexible professional plan",
                "billing_interval": "daily",
                "interval_count": 2,
                "price": 49.99,
                "currency": "USD",
                "trial_period_days": 14,
                "minimum_quantity": 3,
                "available_from": "2026-01-01",
                "available_until": "2027-12-31",
                "payment_terms_days": 7,
                "auto_close_on_end": True,
                "allow_early_close": False,
                "allow_pause": False,
                "allow_renewal": True,
                "is_active": True,
            },
            headers=staff_headers,
        )
        assert plan_res.status_code == 201
        plan_data = plan_res.json()
        assert plan_data["code"] == plan_code
        assert Decimal(str(plan_data["price"])) == Decimal("49.99")
        assert plan_data["trial_period_days"] == 14
        assert plan_data["billing_interval"] == "daily"
        assert plan_data["interval_count"] == 2
        assert plan_data["minimum_quantity"] == 3
        assert plan_data["payment_terms_days"] == 7
        assert plan_data["auto_close_on_end"] is True
        assert plan_data["allow_early_close"] is False
        assert plan_data["allow_pause"] is False
        assert plan_data["allow_renewal"] is True
        plan_id = plan_data["id"]

        # 4. Duplicate plan code rejection
        dup_plan_res = client.post(
            "/api/plans",
            json={
                "product_id": str(product.id),
                "name": "Duplicate Code Plan",
                "code": plan_code,
                "billing_interval": "monthly",
                "price": 99.00,
            },
            headers=staff_headers,
        )
        assert dup_plan_res.status_code == 400
        assert "already exists" in dup_plan_res.json()["detail"].lower()

        # 5. Customer retrieves active plan
        get_res = client.get(f"/api/plans/{plan_id}", headers=cust_headers)
        assert get_res.status_code == 200
        assert get_res.json()["name"] == "Professional Daily"

        # 6. Staff updates plan price
        update_res = client.patch(
            f"/api/plans/{plan_id}",
            json={"price": 59.99},
            headers=staff_headers,
        )
        assert update_res.status_code == 200
        assert Decimal(str(update_res.json()["price"])) == Decimal("59.99")

        # 7. Staff archives plan on delete
        del_res = client.delete(f"/api/plans/{plan_id}", headers=staff_headers)
        assert del_res.status_code == 200
        assert del_res.json()["is_archived"] is True
        assert del_res.json()["is_active"] is False

        # 8. Customer no longer sees archived plan
        not_avail = client.get(f"/api/plans/{plan_id}", headers=cust_headers)
        assert not_avail.status_code == 404

    finally:
        db_session.query(SubscriptionPlan).filter(
            SubscriptionPlan.product_id == product.id
        ).delete()
        db_session.query(Product).filter(Product.id == product.id).delete()
        db_session.query(User).filter(
            User.id.in_([staff_user.id, cust_user.id])
        ).delete()
        db_session.commit()
