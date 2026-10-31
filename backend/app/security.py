from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any
import bcrypt
import hashlib
import hmac
from jose import jwt, JWTError

from app.database import settings


def hash_password(password: str) -> str:
    """Hash a validated password using bcrypt without silent truncation."""
    pwd_bytes = password.encode("utf-8")
    if len(pwd_bytes) > 72:
        raise ValueError("Password must not exceed 72 UTF-8 bytes")
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(pwd_bytes, salt).decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a plaintext password against a bcrypt hash."""
    try:
        pwd_bytes = plain_password.encode("utf-8")
        if len(pwd_bytes) > 72:
            return False
        hash_bytes = hashed_password.encode("utf-8")
        return bcrypt.checkpw(pwd_bytes, hash_bytes)
    except Exception:
        return False


def create_access_token(
    data: Dict[str, Any], expires_delta: Optional[timedelta] = None
) -> str:
    """Create a signed JWT access token."""
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(
            minutes=settings.access_token_expire_minutes
        )
    to_encode.update({"exp": expire, "iat": datetime.now(timezone.utc)})
    encoded_jwt = jwt.encode(
        to_encode, settings.jwt_secret, algorithm=settings.jwt_algorithm
    )
    return encoded_jwt


def decode_access_token(token: str) -> Dict[str, Any]:
    """Decode and validate a JWT access token."""
    return jwt.decode(
        token, settings.jwt_secret, algorithms=[settings.jwt_algorithm]
    )


def _password_fingerprint(hashed_password: str) -> str:
    return hashlib.sha256(hashed_password.encode("utf-8")).hexdigest()


def create_password_reset_token(user_id: str, hashed_password: str) -> str:
    """Create a short-lived token tied to the user's current password hash."""
    return create_access_token(
        {
            "sub": user_id,
            "purpose": "password_reset",
            "password_fingerprint": _password_fingerprint(hashed_password),
        },
        expires_delta=timedelta(minutes=settings.password_reset_expire_minutes),
    )


def decode_password_reset_token(token: str, hashed_password: str) -> Dict[str, Any]:
    """Validate reset purpose and invalidate tokens after a password change."""
    payload = decode_access_token(token)
    if payload.get("purpose") != "password_reset":
        raise JWTError("Invalid token purpose")

    supplied = payload.get("password_fingerprint", "")
    expected = _password_fingerprint(hashed_password)
    if not hmac.compare_digest(supplied, expected):
        raise JWTError("Password reset token has already been used")
    return payload
