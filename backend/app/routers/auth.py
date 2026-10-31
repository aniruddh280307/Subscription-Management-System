import smtplib
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from jose import JWTError
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import User, UserRole
from app.schemas.auth import (
    UserRegisterRequest,
    StaffCreateRequest,
    UserLoginRequest,
    ForgotPasswordRequest,
    ResetPasswordRequest,
    MessageResponse,
    UserResponse,
    TokenResponse,
)
from app.security import (
    hash_password,
    verify_password,
    create_access_token,
    create_password_reset_token,
    decode_password_reset_token,
)
from app.dependencies import get_current_user, require_admin
from app.services.email_service import (
    EmailConfigurationError,
    send_password_reset_email,
)

router = APIRouter(prefix="/api/auth", tags=["Authentication"])


@router.post(
    "/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED
)
def register(request: UserRegisterRequest, db: Session = Depends(get_db)):
    """Self-register a Customer account. Privileged roles are never accepted."""
    existing_user = db.query(User).filter(User.email == request.email).first()
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email is already registered",
        )

    user = User(
        email=request.email,
        hashed_password=hash_password(request.password),
        full_name=request.full_name,
        role=UserRole.CUSTOMER,
        is_active=True,
        is_verified=False,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@router.post(
    "/staff",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_staff_user(
    request: StaffCreateRequest,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    """Create a Staff account. This operation is restricted to Admin users."""
    existing_user = db.query(User).filter(User.email == request.email).first()
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email is already registered",
        )

    user = User(
        email=request.email,
        hashed_password=hash_password(request.password),
        full_name=request.full_name,
        role=UserRole.STAFF,
        is_active=True,
        is_verified=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@router.post("/login", response_model=TokenResponse)
def login(request: UserLoginRequest, db: Session = Depends(get_db)):
    """Authenticate user with email/password and issue a JWT access token."""
    user = db.query(User).filter(User.email == request.email).first()
    if not user or not verify_password(request.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Inactive user account",
        )

    token = create_access_token(
        data={"sub": str(user.id), "email": user.email, "role": user.role.value}
    )
    return TokenResponse(access_token=token, token_type="bearer", user=user)


@router.post(
    "/forgot-password",
    response_model=MessageResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def forgot_password(
    request: ForgotPasswordRequest,
    db: Session = Depends(get_db),
):
    """Email a reset link without revealing whether an account exists."""
    generic_message = "If the account exists, a password reset link has been sent."
    user = db.query(User).filter(User.email == request.email).first()
    if not user or not user.is_active:
        return MessageResponse(message=generic_message)

    token = create_password_reset_token(str(user.id), user.hashed_password)
    try:
        send_password_reset_email(user.email, user.full_name, token)
    except (EmailConfigurationError, OSError, smtplib.SMTPException) as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Password reset email is temporarily unavailable",
        ) from exc
    return MessageResponse(message=generic_message)


@router.post("/reset-password", response_model=MessageResponse)
def reset_password(
    request: ResetPasswordRequest,
    db: Session = Depends(get_db),
):
    """Set a new password using a valid, unused password-reset token."""
    try:
        unverified_payload = decode_access_token(request.token)
        user_id = uuid.UUID(unverified_payload.get("sub", ""))
    except (JWTError, ValueError, TypeError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired password reset token",
        )

    user = db.query(User).filter(User.id == user_id).first()
    if not user or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired password reset token",
        )

    try:
        decode_password_reset_token(request.token, user.hashed_password)
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired password reset token",
        )

    user.hashed_password = hash_password(request.password)
    db.commit()
    return MessageResponse(message="Password has been reset successfully.")


@router.get("/me", response_model=UserResponse)
def get_me(current_user: User = Depends(get_current_user)):
    """Retrieve details for the currently authenticated user."""
    return current_user
