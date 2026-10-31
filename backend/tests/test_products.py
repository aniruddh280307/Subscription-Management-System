import uuid
from app.models import User, UserRole, Product
from app.security import hash_password


def test_product_crud_and_archiving(client, db_session, auth_headers):
    staff_user = User(
        email=f"prod_staff_{uuid.uuid4().hex[:8]}@example.com",
        hashed_password=hash_password("pw"),
        full_name="Product Staff",
        role=UserRole.STAFF,
        is_active=True,
    )
    cust_user = User(
        email=f"prod_cust_{uuid.uuid4().hex[:8]}@example.com",
        hashed_password=hash_password("pw"),
        full_name="Product Cust",
        role=UserRole.CUSTOMER,
        is_active=True,
    )
    db_session.add_all([staff_user, cust_user])
    db_session.commit()

    code_prefix = f"TST-{uuid.uuid4().hex[:6].upper()}"
    p1_code = f"{code_prefix}-1"

    try:
        staff_headers = auth_headers(staff_user)
        cust_headers = auth_headers(cust_user)

        # 1. Staff creates product
        p1_res = client.post(
            "/api/products",
            json={
                "name": "Cloud Pro SaaS",
                "code": p1_code,
                "description": "Enterprise cloud tier",
                "product_type": "software",
                "sales_price": "1200.00",
                "cost_price": "500.00",
                "notes": "Recurring enterprise product",
                "variants": [
                    {
                        "attribute_name": "Brand",
                        "value": "Odoo",
                        "sku": f"{code_prefix}-ODOO",
                        "price_modifier": "560.00",
                    }
                ],
                "is_active": True,
            },
            headers=staff_headers,
        )
        assert p1_res.status_code == 201
        p1_data = p1_res.json()
        assert p1_data["code"] == p1_code
        assert p1_data["product_type"] == "software"
        assert p1_data["sales_price"] == "1200.00"
        assert p1_data["cost_price"] == "500.00"
        assert p1_data["variants"][0]["value"] == "Odoo"
        assert p1_data["variants"][0]["price_modifier"] == "560.00"
        p1_id = p1_data["id"]

        # Create, update, and deactivate another generic variant
        variant_res = client.post(
            f"/api/products/{p1_id}/variants",
            json={
                "attribute_name": "Edition",
                "value": "Enterprise",
                "sku": f"{code_prefix}-ENT",
                "price_modifier": "250.00",
            },
            headers=staff_headers,
        )
        assert variant_res.status_code == 201
        variant_id = variant_res.json()["id"]
        variant_update = client.patch(
            f"/api/products/{p1_id}/variants/{variant_id}",
            json={"price_modifier": "300.00"},
            headers=staff_headers,
        )
        assert variant_update.status_code == 200
        assert variant_update.json()["price_modifier"] == "300.00"
        variant_delete = client.delete(
            f"/api/products/{p1_id}/variants/{variant_id}",
            headers=staff_headers,
        )
        assert variant_delete.status_code == 200
        assert variant_delete.json()["is_active"] is False

        # 2. Duplicate product code rejection
        dup_res = client.post(
            "/api/products",
            json={
                "name": "Cloud Pro SaaS Duplicate",
                "code": p1_code,
            },
            headers=staff_headers,
        )
        assert dup_res.status_code == 400
        assert "already exists" in dup_res.json()["detail"].lower()

        # 3. Customer views active products
        cust_list = client.get(
            f"/api/products?search={code_prefix}", headers=cust_headers
        ).json()
        assert any(p["id"] == p1_id for p in cust_list["items"])

        # 4. Staff updates product
        update_res = client.patch(
            f"/api/products/{p1_id}",
            json={"description": "Updated cloud description"},
            headers=staff_headers,
        )
        assert update_res.status_code == 200
        assert update_res.json()["description"] == "Updated cloud description"

        # 5. Archive product on delete
        del_res = client.delete(f"/api/products/{p1_id}", headers=staff_headers)
        assert del_res.status_code == 200
        assert del_res.json()["is_archived"] is True
        assert del_res.json()["is_active"] is False

        # 6. Customer can no longer see archived product
        cust_list_after = client.get(
            f"/api/products?search={code_prefix}", headers=cust_headers
        ).json()
        assert not any(p["id"] == p1_id for p in cust_list_after["items"])

    finally:
        db_session.query(Product).filter(Product.code.like(f"{code_prefix}%")).delete()
        db_session.query(User).filter(
            User.id.in_([staff_user.id, cust_user.id])
        ).delete()
        db_session.commit()
