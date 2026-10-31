import sys
from pathlib import Path

# Ensure backend directory is in sys.path
backend_dir = Path(__file__).resolve().parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.database import SessionLocal, engine
from app.main import app
from app.models import User, UserRole, Customer, Product, SubscriptionPlan
from app.security import hash_password, create_access_token


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="function")
def db_session():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def create_test_token(user: User) -> str:
    return create_access_token(
        data={"sub": str(user.id), "email": user.email, "role": user.role.value}
    )


@pytest.fixture
def auth_headers():
    """Helper factory to generate bearer auth headers for any User."""
    def _headers(user: User):
        token = create_test_token(user)
        return {"Authorization": f"Bearer {token}"}
    return _headers
