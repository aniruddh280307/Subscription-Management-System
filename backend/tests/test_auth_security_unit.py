import pytest
from jose import JWTError
from pydantic import ValidationError

from app.schemas.auth import UserRegisterRequest
from app.security import (
    create_password_reset_token,
    decode_password_reset_token,
    hash_password,
)
from app.services.email_service import build_password_reset_url


@pytest.mark.parametrize(
    "password",
    [
        "short!A",
        "nouppercase!",
        "NOLOWERCASE!",
        "NoSpecialCharacter1",
        "ValidPassword123!" + "é" * 40,
    ],
)
def test_registration_rejects_passwords_outside_policy(password):
    with pytest.raises(ValidationError):
        UserRegisterRequest(
            email="customer@example.com",
            password=password,
            full_name="Customer",
        )


def test_registration_accepts_required_password_policy():
    request = UserRegisterRequest(
        email="customer@example.com",
        password="ValidPassword!",
        full_name="Customer",
    )
    assert request.password == "ValidPassword!"


def test_registration_rejects_public_role_selection():
    with pytest.raises(ValidationError):
        UserRegisterRequest(
            email="customer@example.com",
            password="ValidPassword!",
            full_name="Customer",
            role="admin",
        )


def test_password_reset_token_is_invalid_after_password_change():
    original_hash = hash_password("OriginalPassword!")
    token = create_password_reset_token("00000000-0000-0000-0000-000000000001", original_hash)

    payload = decode_password_reset_token(token, original_hash)
    assert payload["purpose"] == "password_reset"

    changed_hash = hash_password("ChangedPassword!")
    with pytest.raises(JWTError):
        decode_password_reset_token(token, changed_hash)


def test_reset_url_encodes_token(monkeypatch):
    from app.services import email_service

    monkeypatch.setattr(
        email_service.settings,
        "frontend_reset_url",
        "https://example.com/reset-password",
    )
    url = build_password_reset_url("header.payload+/signature=")
    assert url.startswith("https://example.com/reset-password?token=")
    assert "+" not in url.split("token=", 1)[1]
