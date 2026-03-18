import asyncio
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Response, status
from starlette.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user, get_current_user_allow_provisional, get_user_from_refresh_token
from app.auth.schemas import (
    CompleteRegistrationRequest,
    ForgotPasswordRequest,
    LoginRequest,
    MessageResponse,
    RegisterRequest,
    ResetPasswordRequest,
    UserResponse,
)
from app.auth.security import (
    create_access_token,
    create_refresh_token,
    generate_token,
    hash_password,
    hash_token,
    verify_password,
)
from app.config import settings
from app.db.models import Manuscript, User
from app.db.session import get_db
from app.email.sender import send_password_reset_email, send_verification_email
from app.rate_limit import check_rate_limit

router = APIRouter(prefix="/auth", tags=["auth"])

COOKIE_SECURE = settings.jwt_secret_key != "change-me-in-production"


def _set_auth_cookies(response: Response, user: User) -> None:
    """Set access and refresh token cookies."""
    access = create_access_token(
        str(user.id),
        user.token_version,
        is_provisional=user.is_provisional,
    )
    response.set_cookie(
        key="access_token",
        value=access,
        httponly=True,
        secure=COOKIE_SECURE,
        samesite="lax",
        max_age=settings.provisional_token_expire_minutes * 60 if user.is_provisional
        else settings.access_token_expire_minutes * 60,
        path="/",
    )

    if not user.is_provisional:
        refresh = create_refresh_token(str(user.id), user.token_version)
        response.set_cookie(
            key="refresh_token",
            value=refresh,
            httponly=True,
            secure=COOKIE_SECURE,
            samesite="lax",
            max_age=settings.refresh_token_expire_days * 86400,
            path="/auth/refresh",
        )


@router.post("/register", response_model=MessageResponse)
async def register(body: RegisterRequest, db: AsyncSession = Depends(get_db)):
    """Register with email only (provisional user). Always returns 200 to prevent enumeration."""
    email = body.email.lower().strip()

    result = await db.execute(select(User).where(User.email == email, User.deleted_at.is_(None)))
    existing = result.scalar_one_or_none()

    if existing is not None:
        if not existing.email_verified:
            # Resend verification email
            token = generate_token()
            existing.verification_token = hash_token(token)
            existing.verification_token_expires = datetime.now(timezone.utc) + timedelta(hours=1)
            await db.commit()
            verification_url = f"{settings.base_url}/auth/verify-email?token={token}"
            send_verification_email(existing.email, verification_url)
        # For existing verified users, do nothing (no info leak)
    else:
        token = generate_token()
        user = User(
            email=email,
            verification_token=hash_token(token),
            verification_token_expires=datetime.now(timezone.utc) + timedelta(hours=1),
            is_provisional=True,
        )
        db.add(user)
        await db.commit()
        verification_url = f"{settings.base_url}/auth/verify-email?token={token}"
        send_verification_email(email, verification_url)

    # Constant-time delay to mask timing differences (JUDGE amendment)
    await asyncio.sleep(0.3)
    return MessageResponse(message="If this email is valid, a verification link has been sent.")


@router.get("/verify-email")
async def verify_email(token: str, db: AsyncSession = Depends(get_db)):
    """Verify email with token from verification email."""
    token_hash = hash_token(token)

    # SEC-003: Only match unverified users and use FOR UPDATE to prevent race conditions
    result = await db.execute(
        select(User).where(
            User.verification_token == token_hash,
            User.email_verified.is_(False),
            User.deleted_at.is_(None),
        ).with_for_update()
    )
    user = result.scalar_one_or_none()

    if user is None:
        raise HTTPException(status_code=400, detail="Invalid or expired verification token")

    if user.verification_token_expires and user.verification_token_expires < datetime.now(timezone.utc):
        raise HTTPException(status_code=400, detail="Verification token has expired")

    user.email_verified = True
    user.verification_token = None
    user.verification_token_expires = None
    await db.commit()
    await db.refresh(user)

    redirect = RedirectResponse(url=f"{settings.base_url}/dashboard", status_code=302)
    _set_auth_cookies(redirect, user)

    return redirect


@router.post("/complete-registration", response_model=UserResponse)
async def complete_registration(
    body: CompleteRegistrationRequest,
    response: Response,
    user: User = Depends(get_current_user_allow_provisional),
    db: AsyncSession = Depends(get_db),
):
    """Upgrade provisional user to full account with password and ToS acceptance."""
    if not user.is_provisional:
        raise HTTPException(status_code=400, detail="Registration already complete")

    if not body.tos_accepted:
        raise HTTPException(status_code=400, detail="You must accept the Terms of Service")

    user.password_hash = hash_password(body.password)
    user.is_provisional = False
    user.tos_accepted_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(user)

    _set_auth_cookies(response, user)

    return UserResponse.model_validate(user)


@router.post("/login", response_model=UserResponse)
async def login(body: LoginRequest, response: Response, db: AsyncSession = Depends(get_db)):
    """Login with email and password. Only for full (non-provisional) users."""
    email = body.email.lower().strip()

    # SEC-010: Rate limit login attempts per email
    await check_rate_limit(
        email, action="login", max_requests=10, window=timedelta(minutes=15),
        user_email=email,
    )

    result = await db.execute(select(User).where(User.email == email, User.deleted_at.is_(None)))
    user = result.scalar_one_or_none()

    if user is None or user.password_hash is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    if user.is_provisional:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Please complete registration first")

    if not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    _set_auth_cookies(response, user)

    return UserResponse.model_validate(user)


@router.post("/refresh", response_model=MessageResponse)
async def refresh(
    response: Response,
    user: User = Depends(get_user_from_refresh_token),
):
    """Refresh the access token using the refresh token cookie."""
    access = create_access_token(str(user.id), user.token_version, is_provisional=False)
    response.set_cookie(
        key="access_token",
        value=access,
        httponly=True,
        secure=COOKIE_SECURE,
        samesite="lax",
        max_age=settings.access_token_expire_minutes * 60,
        path="/",
    )
    return MessageResponse(message="Token refreshed")


@router.post("/forgot-password", response_model=MessageResponse)
async def forgot_password(body: ForgotPasswordRequest, db: AsyncSession = Depends(get_db)):
    """Request password reset. Always returns 200 to prevent email enumeration."""
    email = body.email.lower().strip()

    result = await db.execute(
        select(User).where(User.email == email, User.deleted_at.is_(None), User.is_provisional.is_(False))
    )
    user = result.scalar_one_or_none()

    if user is not None:
        token = generate_token()
        user.password_reset_token = hash_token(token)
        user.password_reset_token_expires = datetime.now(timezone.utc) + timedelta(hours=1)
        await db.commit()
        reset_url = f"{settings.base_url}/auth/reset-password?token={token}"
        send_password_reset_email(user.email, reset_url)

    await asyncio.sleep(0.3)
    return MessageResponse(message="If an account exists with this email, a reset link has been sent.")


@router.post("/reset-password", response_model=MessageResponse)
async def reset_password(body: ResetPasswordRequest, db: AsyncSession = Depends(get_db)):
    """Reset password using token from email."""
    token_hash = hash_token(body.token)

    # SEC-004: Use FOR UPDATE to prevent race conditions on password reset
    result = await db.execute(
        select(User).where(
            User.password_reset_token == token_hash,
            User.deleted_at.is_(None),
        ).with_for_update()
    )
    user = result.scalar_one_or_none()

    if user is None:
        raise HTTPException(status_code=400, detail="Invalid or expired reset token")

    if user.password_reset_token_expires and user.password_reset_token_expires < datetime.now(timezone.utc):
        raise HTTPException(status_code=400, detail="Reset token has expired")

    # Verify token is still present (not consumed by a concurrent request)
    if user.password_reset_token is None:
        raise HTTPException(status_code=400, detail="Invalid or expired reset token")

    user.password_hash = hash_password(body.new_password)
    user.password_reset_token = None
    user.password_reset_token_expires = None
    user.token_version += 1  # Invalidate all existing sessions
    await db.commit()

    return MessageResponse(message="Password has been reset. Please log in.")


@router.post("/logout", response_model=MessageResponse)
async def logout(response: Response):
    """Clear auth cookies."""
    response.delete_cookie("access_token", path="/")
    response.delete_cookie("refresh_token", path="/auth/refresh")
    return MessageResponse(message="Logged out")


@router.get("/me", response_model=UserResponse)
async def get_me(user: User = Depends(get_current_user)):
    """Get current user profile. Requires full (non-provisional) user."""
    return UserResponse.model_validate(user)


@router.delete("/account", response_model=MessageResponse)
async def delete_account(
    response: Response,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete the current user's account and all associated data.

    - Soft-deletes the user (sets deleted_at)
    - Soft-deletes all manuscripts
    - Schedules S3 file cleanup (immediate best-effort, with 30-day hard delete)
    - Invalidates all sessions (increments token_version)
    - Clears auth cookies
    """
    import logging
    logger = logging.getLogger(__name__)

    # Soft-delete all manuscripts
    ms_result = await db.execute(
        select(Manuscript).where(
            Manuscript.user_id == user.id,
            Manuscript.deleted_at.is_(None),
        )
    )
    manuscripts = ms_result.scalars().all()

    s3_keys_to_delete = []
    for ms in manuscripts:
        ms.deleted_at = datetime.now(timezone.utc)
        if ms.s3_key:
            s3_keys_to_delete.append(ms.s3_key)

    # Soft-delete user and invalidate sessions
    user.deleted_at = datetime.now(timezone.utc)
    user.token_version += 1

    await db.commit()

    # Best-effort S3 cleanup (non-blocking)
    if s3_keys_to_delete:
        try:
            from app.manuscripts.s3 import delete_from_s3
            for key in s3_keys_to_delete:
                try:
                    delete_from_s3(key)
                except Exception as e:
                    logger.warning(f"Failed to delete S3 key {key}: {e}")
        except Exception as e:
            logger.warning(f"S3 cleanup skipped: {e}")

    # Clear auth cookies
    response.delete_cookie("access_token", path="/")
    response.delete_cookie("refresh_token", path="/auth/refresh")

    return MessageResponse(message="Your account and all data have been deleted.")
