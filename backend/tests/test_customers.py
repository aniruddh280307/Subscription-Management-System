import uuid
from app.models import User, UserRole, Customer
from app.security import hash_password


def test_customer_crud_and_isolation(client, db_session, auth_headers):
    # Setup 1 staff user, 2 customer users
    staff_user = User(
        email=f"staff_{uuid.uuid4().hex[:8]}@example.com",
        hashed_password=hash_password("pw"),
        full_name="Staff Operator",
        role=UserRole.STAFF,
        is_active=True,
    )
    cust_user1 = User(
        email=f"cust1_{uuid.uuid4().hex[:8]}@example.com",
        hashed_password=hash_password("pw"),
        full_name="Customer One",
        role=UserRole.CUSTOMER,
        is_active=True,
    )
    cust_user2 = User(
        email=f"cust2_{uuid.uuid4().hex[:8]}@example.com",
        hashed_password=hash_password("pw"),
        full_name="Customer Two",
        role=UserRole.CUSTOMER,
        is_active=True,
    )
    db_session.add_all([staff_user, cust_user1, cust_user2])
    db_session.commit()

    try:
        staff_headers = auth_headers(staff_user)
        cust1_headers = auth_headers(cust_user1)
        cust2_headers = auth_headers(cust_user2)

        # 1. Customer 1 self-onboards
        c1_res = client.post(
            "/api/customers",
            json={"name": "Cust One Inc", "email": cust_user1.email, "currency": "USD"},
            headers=cust1_headers,
        )
        assert c1_res.status_code == 201
        c1_data = c1_res.json()
        assert c1_data["user_id"] == str(cust_user1.id)
        c1_id = c1_data["id"]

        # 2. Duplicate onboarding by customer 1 rejected
        dup_res = client.post(
            "/api/customers",
            json={"name": "Cust One Clone", "email": cust_user1.email},
            headers=cust1_headers,
        )
        assert dup_res.status_code == 400

        # 3. Staff creates customer 2
        c2_res = client.post(
            "/api/customers",
            json={
                "name": "Cust Two Corp",
                "email": cust_user2.email,
                "user_id": str(cust_user2.id),
                "currency": "EUR",
            },
            headers=staff_headers,
        )
        assert c2_res.status_code == 201
        c2_data = c2_res.json()
        c2_id = c2_data["id"]

        # 4. Customer isolation check: Cust 1 tries to view Cust 2 -> 403 Forbidden
        view_res = client.get(f"/api/customers/{c2_id}", headers=cust1_headers)
        assert view_res.status_code == 403

        # Cust 1 tries to update Cust 2 -> 403 Forbidden
        update_res = client.patch(
            f"/api/customers/{c2_id}",
            json={"name": "Hacked Corp"},
            headers=cust1_headers,
        )
        assert update_res.status_code == 403

        # 5. Customer 1 lists customers -> only sees self
        list_cust1 = client.get("/api/customers", headers=cust1_headers).json()
        assert list_cust1["total"] == 1
        assert list_cust1["items"][0]["id"] == c1_id

        # 6. Staff lists customers -> sees both
        list_staff = client.get(
            f"/api/customers?search={cust_user1.email}", headers=staff_headers
        ).json()
        assert list_staff["total"] >= 1
        assert any(c["id"] == c1_id for c in list_staff["items"])

        # 7. Staff archives customer 1
        del_res = client.delete(f"/api/customers/{c1_id}", headers=staff_headers)
        assert del_res.status_code == 200
        del_data = del_res.json()
        assert del_data["is_archived"] is True
        assert del_data["is_active"] is False

        # 8. 404 for invalid customer ID
        not_found = client.get(
            f"/api/customers/{uuid.uuid4()}", headers=staff_headers
        )
        assert not_found.status_code == 404

    finally:
        # Cleanup
        db_session.query(Customer).filter(
            Customer.email.in_([cust_user1.email, cust_user2.email])
        ).delete()
        db_session.query(User).filter(
            User.id.in_([staff_user.id, cust_user1.id, cust_user2.id])
        ).delete()
        db_session.commit()
