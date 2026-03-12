import uuid

from fastapi import Cookie, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.security import decode_token
from app.db.models import User
from app.db.session import get_db


async def _get_user_from_token(
    token: str | None,
    db: AsyncSession,
    allow_provisional: bool = False,
    allow_refresh: bool = False,
) -> User:
    if token is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

    payload = decode_token(token)
    if payload is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")

    token_type = payload.get("type")

    if allow_refresh:
        if token_type != "refresh":
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token type")
    else:
        if token_type not in ("full", "provisional"):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token type")
        if token_type == "provisional" and not allow_provisional:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Please complete registration to access this resource",
            )

    user_id = payload.get("sub")
    if user_id is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    result = await db.execute(
        select(User).where(User.id == uuid.UUID(user_id), User.deleted_at.is_(None))
    )
    user = result.scalar_one_or_none()

    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")

    # Check token_version — reject tokens issued before password reset
    jwt_token_version = payload.get("token_version")
    if jwt_token_version is not None and jwt_token_version != user.token_version:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token has been revoked")

    return user


async def get_current_user(
    access_token: str | None = Cookie(default=None),
    db: AsyncSession = Depends(get_db),
) -> User:
    """Default dependency — requires full (non-provisional) user.
    Use this on all endpoints unless provisional access is explicitly needed.
    """
    return await _get_user_from_token(access_token, db, allow_provisional=False)


async def get_current_user_allow_provisional(
    access_token: str | None = Cookie(default=None),
    db: AsyncSession = Depends(get_db),
) -> User:
    """Explicit opt-in — allows provisional users.
    Use ONLY on endpoints that provisional users need (upload Chapter 1, view bible, job status).
    """
    return await _get_user_from_token(access_token, db, allow_provisional=True)


async def get_user_from_refresh_token(
    refresh_token: str | None = Cookie(default=None),
    db: AsyncSession = Depends(get_db),
) -> User:
    """Extract user from refresh token cookie."""
    return await _get_user_from_token(refresh_token, db, allow_refresh=True)
