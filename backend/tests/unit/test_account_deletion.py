"""Unit tests for account deletion endpoint (DELETE /auth/account).

Tests GDPR data purge: soft-delete user + manuscripts, S3 cleanup,
session invalidation, and cookie clearing.
"""

import uuid
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from unittest.mock import patch, MagicMock

from app.auth.security import hash_password, create_access_token
from app.db.models import Manuscript, ManuscriptStatus, PaymentStatus, User


def _make_full_user(email: str = "delete@example.com") -> User:
    return User(
        email=email,
        password_hash=hash_password("password123"),
        is_provisional=False,
        email_verified=True,
    )


async def _login(client: AsyncClient, email: str, password: str = "password123"):
    from unittest.mock import patch
    with patch("app.rate_limit._get_redis", side_effect=ConnectionError("Redis unavailable in test")):
        resp = await client.post("/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200
    return resp


@pytest.mark.asyncio
async def test_delete_account_soft_deletes_user(client: AsyncClient, db_session: AsyncSession):
    """Deleting account sets deleted_at on the user."""
    user = _make_full_user()
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)

    await _login(client, "delete@example.com")

    resp = await client.delete("/auth/account")
    assert resp.status_code == 200
    assert "deleted" in resp.json()["message"].lower()

    await db_session.refresh(user)
    assert user.deleted_at is not None


@pytest.mark.asyncio
async def test_delete_account_soft_deletes_manuscripts(client: AsyncClient, db_session: AsyncSession):
    """All user manuscripts are soft-deleted."""
    user = _make_full_user("msdel@example.com")
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)

    # Create two manuscripts
    for i in range(2):
        ms = Manuscript(
            user_id=user.id,
            title=f"Test Manuscript {i}",
            status=ManuscriptStatus.bible_complete,
            payment_status=PaymentStatus.unpaid,
            s3_key=f"manuscripts/{user.id}/ms{i}/original.docx",
        )
        db_session.add(ms)
    await db_session.commit()

    await _login(client, "msdel@example.com")

    with patch("app.manuscripts.s3.delete_from_s3"):
        resp = await client.delete("/auth/account")

    assert resp.status_code == 200

    result = await db_session.execute(
        select(Manuscript).where(Manuscript.user_id == user.id)
    )
    manuscripts = result.scalars().all()
    assert all(ms.deleted_at is not None for ms in manuscripts)


@pytest.mark.asyncio
async def test_delete_account_increments_token_version(client: AsyncClient, db_session: AsyncSession):
    """Session invalidation via token_version increment."""
    user = _make_full_user("token@example.com")
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)

    original_version = user.token_version

    await _login(client, "token@example.com")
    resp = await client.delete("/auth/account")
    assert resp.status_code == 200

    await db_session.refresh(user)
    assert user.token_version == original_version + 1


@pytest.mark.asyncio
async def test_delete_account_clears_cookies(client: AsyncClient, db_session: AsyncSession):
    """Auth cookies are cleared after deletion."""
    user = _make_full_user("cookies@example.com")
    db_session.add(user)
    await db_session.commit()

    await _login(client, "cookies@example.com")
    resp = await client.delete("/auth/account")
    assert resp.status_code == 200

    set_cookie = resp.headers.get("set-cookie", "")
    assert "access_token" in set_cookie


@pytest.mark.asyncio
async def test_delete_account_s3_cleanup(client: AsyncClient, db_session: AsyncSession):
    """S3 files are cleaned up (best-effort)."""
    user = _make_full_user("s3del@example.com")
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)

    ms = Manuscript(
        user_id=user.id,
        title="S3 Test",
        status=ManuscriptStatus.bible_complete,
        payment_status=PaymentStatus.unpaid,
        s3_key="manuscripts/test/original.docx",
    )
    db_session.add(ms)
    await db_session.commit()

    await _login(client, "s3del@example.com")

    with patch("app.manuscripts.s3.delete_from_s3", return_value=None) as mock_delete:
        resp = await client.delete("/auth/account")

    assert resp.status_code == 200
    mock_delete.assert_called_once_with("manuscripts/test/original.docx")


@pytest.mark.asyncio
async def test_deleted_user_cannot_login(client: AsyncClient, db_session: AsyncSession):
    """After deletion, login should fail."""
    user = _make_full_user("nologin@example.com")
    db_session.add(user)
    await db_session.commit()

    await _login(client, "nologin@example.com")
    await client.delete("/auth/account")

    from unittest.mock import patch as mock_patch
    with mock_patch("app.rate_limit._get_redis", side_effect=ConnectionError("Redis unavailable in test")):
        resp = await client.post("/auth/login", json={"email": "nologin@example.com", "password": "password123"})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_delete_account_requires_auth(client: AsyncClient):
    """Unauthenticated deletion should fail."""
    resp = await client.delete("/auth/account")
    assert resp.status_code == 401
