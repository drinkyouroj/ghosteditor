import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.security import generate_token, hash_token
from app.db.models import User


@pytest.mark.asyncio
async def test_register_returns_200(client: AsyncClient):
    resp = await client.post("/auth/register", json={"email": "test@example.com"})
    assert resp.status_code == 200
    assert "verification link" in resp.json()["message"].lower()


@pytest.mark.asyncio
async def test_register_creates_provisional_user(client: AsyncClient, db_session: AsyncSession):
    await client.post("/auth/register", json={"email": "new@example.com"})
    result = await db_session.execute(select(User).where(User.email == "new@example.com"))
    user = result.scalar_one()
    assert user.is_provisional is True
    assert user.email_verified is False
    assert user.verification_token is not None


@pytest.mark.asyncio
async def test_register_duplicate_email_returns_200(client: AsyncClient):
    """Same response for duplicate emails to prevent enumeration."""
    await client.post("/auth/register", json={"email": "dup@example.com"})
    resp = await client.post("/auth/register", json={"email": "dup@example.com"})
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_verify_email_sets_cookie(client: AsyncClient, db_session: AsyncSession):
    token = generate_token()
    from datetime import datetime, timedelta, timezone

    user = User(
        email="verify@example.com",
        verification_token=hash_token(token),
        verification_token_expires=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    db_session.add(user)
    await db_session.commit()

    resp = await client.get(f"/auth/verify-email?token={token}", follow_redirects=False)
    assert resp.status_code == 302
    assert "access_token" in resp.cookies


@pytest.mark.asyncio
async def test_verify_email_invalid_token(client: AsyncClient):
    resp = await client.get("/auth/verify-email?token=badtoken")
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_login_requires_full_user(client: AsyncClient, db_session: AsyncSession):
    """Provisional users cannot log in."""
    from app.auth.security import hash_password

    user = User(
        email="prov@example.com",
        password_hash=hash_password("password123"),
        is_provisional=True,
        email_verified=True,
    )
    db_session.add(user)
    await db_session.commit()

    resp = await client.post("/auth/login", json={"email": "prov@example.com", "password": "password123"})
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_full_login_flow(client: AsyncClient, db_session: AsyncSession):
    """Full user can log in and gets cookies."""
    from app.auth.security import hash_password

    user = User(
        email="full@example.com",
        password_hash=hash_password("password123"),
        is_provisional=False,
        email_verified=True,
    )
    db_session.add(user)
    await db_session.commit()

    resp = await client.post("/auth/login", json={"email": "full@example.com", "password": "password123"})
    assert resp.status_code == 200
    assert "access_token" in resp.cookies
    assert resp.json()["email"] == "full@example.com"


@pytest.mark.asyncio
async def test_login_wrong_password(client: AsyncClient, db_session: AsyncSession):
    from app.auth.security import hash_password

    user = User(
        email="wrong@example.com",
        password_hash=hash_password("correct"),
        is_provisional=False,
        email_verified=True,
    )
    db_session.add(user)
    await db_session.commit()

    resp = await client.post("/auth/login", json={"email": "wrong@example.com", "password": "incorrect"})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_me_requires_auth(client: AsyncClient):
    resp = await client.get("/auth/me")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_logout_clears_cookies(client: AsyncClient):
    resp = await client.post("/auth/logout")
    assert resp.status_code == 200
    # Cookies should be set with max_age=0 or deleted
    assert "access_token" in resp.headers.get("set-cookie", "")
