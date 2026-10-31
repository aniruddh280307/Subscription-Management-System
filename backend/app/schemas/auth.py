import uuid
from datetime import datetime
import re
from pydantic import BaseModel, EmailStr, Field, ConfigDict, field_validator

from app.models import UserRole


class StrongPasswordMixin(BaseModel):
    password: str = Field(..., min_length=9, max_length=128)

    @field_validator("password")
    @classmethod
    def validate_password_strength(cls, value: str) -> str:
        if len(value.encode("utf-8")) > 72:
            raise ValueError("Password must not exceed 72 UTF-8 bytes")
        if not re.search(r"[A-Z]", value):
            raise ValueError("Password must include an uppercase letter")
        if not re.search(r"[a-z]", value):
            raise ValueError("Password must include a lowercase letter")
        if not re.search(r"[^A-Za-z0-9]", value):
            raise ValueError("Password must include a special character")
        return value


class UserRegisterRequest(StrongPasswordMixin):
    email: EmailStr
    full_name: str = Field(..., min_length=1, max_length=255)

    model_config = ConfigDict(extra="forbid")


class StaffCreateRequest(StrongPasswordMixin):
    email: EmailStr
    full_name: str = Field(..., min_length=1, max_length=255)

    model_config = ConfigDict(extra="forbid")


class UserLoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=1)


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(StrongPasswordMixin):
    token: str = Field(..., min_length=1)


class MessageResponse(BaseModel):
    message: str


class UserResponse(BaseModel):
    id: uuid.UUID
    email: EmailStr
    full_name: str
    role: UserRole
    is_active: bool
    is_verified: bool
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserResponse
