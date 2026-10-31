import uuid
import pytest
from app.models import User, UserRole
from app.security import hash_password, create_access_token


def test_auth_registration_success(client, db_session):
    unique_email = f"test_reg_{uuid.uuid4().hex[:8]}@example.com"
    payload = {
        "email": unique_email,
        "password": "SecurePassword123!",
        "full_name": "Test Register User",
    }
    response = client.post("/api/auth/register", json=payload)
    assert response.status_code == 201
    data = response.json()
    assert data["email"] == unique_email
    assert data["full_name"] == "Test Register User"
    assert data["role"] == "customer"
    assert "hashed_password" not in data
    assert "password" not in data

    # Cleanup
    db_session.query(User).filter(User.email == unique_email).delete()
    db_session.commit()


def test_public_registration_rejects_role_selection(client):
    payload = {
        "email": f"role_escalation_{uuid.uuid4().hex[:8]}@example.com",
        "password": "SecurePassword123!",
        "full_name": "Role Escalation Attempt",
        "role": "admin",
    }
    response = client.post("/api/auth/register", json=payload)
    assert response.status_code == 422


def test_only_admin_can_create_staff(client, db_session, auth_headers):
    admin = User(
        email=f"admin_staff_{uuid.uuid4().hex[:8]}@example.com",
        hashed_password=hash_password("AdminPassword123!"),
        full_name="Admin Creator",
        role=UserRole.ADMIN,
        is_active=True,
    )
    customer = User(
        email=f"customer_staff_{uuid.uuid4().hex[:8]}@example.com",
        hashed_password=hash_password("CustomerPassword123!"),
        full_name="Customer Creator",
        role=UserRole.CUSTOMER,
        is_active=True,
    )
    staff_email = f"new_staff_{uuid.uuid4().hex[:8]}@example.com"
    db_session.add_all([admin, customer])
    db_session.commit()

    try:
        payload = {
            "email": staff_email,
            "password": "StaffPassword123!",
            "full_name": "New Staff Member",
        }
        forbidden = client.post(
            "/api/auth/staff", json=payload, headers=auth_headers(customer)
        )
        assert forbidden.status_code == 403

        created = client.post(
            "/api/auth/staff", json=payload, headers=auth_headers(admin)
        )
        assert created.status_code == 201
        assert created.json()["role"] == "staff"
    finally:
        db_session.query(User).filter(
            User.email.in_([admin.email, customer.email, staff_email])
        ).delete(synchronize_session=False)
        db_session.commit()


def test_auth_duplicate_registration_rejection(client, db_session):
    unique_email = f"test_dup_{uuid.uuid4().hex[:8]}@example.com"
    user = User(
        email=unique_email,
        hashed_password=hash_password("password123"),
        full_name="Duplicate User",
        role=UserRole.CUSTOMER,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()

    try:
        payload = {
            "email": unique_email,
            "password": "AnotherPassword123!",
            "full_name": "Duplicate User Attempt",
        }
        response = client.post("/api/auth/register", json=payload)
        assert response.status_code == 400
        assert "already registered" in response.json()["detail"].lower()
    finally:
        db_session.query(User).filter(User.email == unique_email).delete()
        db_session.commit()


def test_auth_login_success(client, db_session):
    unique_email = f"test_login_{uuid.uuid4().hex[:8]}@example.com"
    password = "CorrectPassword123!"
    user = User(
        email=unique_email,
        hashed_password=hash_password(password),
        full_name="Login Success User",
        role=UserRole.CUSTOMER,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()

    try:
        login_res = client.post(
            "/api/auth/login",
            json={"email": unique_email, "password": password},
        )
        assert login_res.status_code == 200
        data = login_res.json()
        assert "access_token" in data
        assert data["token_type"] == "bearer"
        assert data["user"]["email"] == unique_email
    finally:
        db_session.query(User).filter(User.email == unique_email).delete()
        db_session.commit()


def test_auth_invalid_password_rejection(client, db_session):
    unique_email = f"test_badpw_{uuid.uuid4().hex[:8]}@example.com"
    user = User(
        email=unique_email,
        hashed_password=hash_password("RightPassword123!"),
        full_name="Wrong PW User",
        role=UserRole.CUSTOMER,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()

    try:
        login_res = client.post(
            "/api/auth/login",
            json={"email": unique_email, "password": "WrongPassword!"},
        )
        assert login_res.status_code == 401
        assert "invalid email or password" in login_res.json()["detail"].lower()
    finally:
        db_session.query(User).filter(User.email == unique_email).delete()
        db_session.commit()


def test_auth_me_jwt_verification(client, db_session, auth_headers):
    unique_email = f"test_me_{uuid.uuid4().hex[:8]}@example.com"
    user = User(
        email=unique_email,
        hashed_password=hash_password("pw"),
        full_name="Current User Test",
        role=UserRole.STAFF,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()

    try:
        headers = auth_headers(user)
        res = client.get("/api/auth/me", headers=headers)
        assert res.status_code == 200
        data = res.json()
        assert data["email"] == unique_email
        assert data["role"] == "staff"
    finally:
        db_session.query(User).filter(User.email == unique_email).delete()
        db_session.commit()


def test_auth_inactive_user_rejection(client, db_session, auth_headers):
    unique_email = f"test_inactive_{uuid.uuid4().hex[:8]}@example.com"
    user = User(
        email=unique_email,
        hashed_password=hash_password("pw"),
        full_name="Inactive User",
        role=UserRole.CUSTOMER,
        is_active=False,
    )
    db_session.add(user)
    db_session.commit()

    try:
        # Test inactive during login
        login_res = client.post(
            "/api/auth/login",
            json={"email": unique_email, "password": "pw"},
        )
        assert login_res.status_code == 403
        assert "inactive" in login_res.json()["detail"].lower()

        # Test inactive with valid token
        headers = auth_headers(user)
        me_res = client.get("/api/auth/me", headers=headers)
        assert me_res.status_code == 403
    finally:
        db_session.query(User).filter(User.email == unique_email).delete()
        db_session.commit()


def test_role_restrictions(client, db_session, auth_headers):
    """Customer role is forbidden from performing Staff/Admin write operations."""
    cust_user = User(
        email=f"cust_role_{uuid.uuid4().hex[:8]}@example.com",
        hashed_password=hash_password("pw"),
        full_name="Customer User",
        role=UserRole.CUSTOMER,
        is_active=True,
    )
    db_session.add(cust_user)
    db_session.commit()

    try:
        headers = auth_headers(cust_user)
        # Attempt to create product as customer
        res = client.post(
            "/api/products",
            json={"name": "Forbidden Prod", "code": "FORBID-1"},
            headers=headers,
        )
        assert res.status_code == 403
        assert "not permitted" in res.json()["detail"].lower()
    finally:
        db_session.query(User).filter(User.id == cust_user.id).delete()
        db_session.commit()
