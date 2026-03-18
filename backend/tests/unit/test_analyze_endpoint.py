"""Tests for the POST /manuscripts/{id}/analyze payment guard and access control."""

from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.security import create_access_token
from app.db.models import (
    Chapter,
    ChapterStatus,
    Manuscript,
    ManuscriptStatus,
    PaymentStatus,
    User,
)


async def _create_user(db_session: AsyncSession, email: str) -> User:
    """Helper: create a full (non-provisional) user and return it."""
    user = User(
        email=email,
        is_provisional=False,
        email_verified=True,
    )
    db_session.add(user)
    await db_session.flush()
    await db_session.refresh(user)
    return user


def _auth_cookie(user: User) -> dict[str, str]:
    """Helper: build an access_token cookie for authenticated requests."""
    token = create_access_token(
        user_id=str(user.id),
        token_version=user.token_version,
        is_provisional=False,
    )
    return {"access_token": token}


async def _create_manuscript(
    db_session: AsyncSession,
    user: User,
    *,
    payment_status: PaymentStatus = PaymentStatus.unpaid,
    status: ManuscriptStatus = ManuscriptStatus.bible_complete,
    add_chapter: bool = False,
) -> Manuscript:
    """Helper: create a manuscript with the given status flags."""
    manuscript = Manuscript(
        user_id=user.id,
        title="Test Manuscript",
        status=status,
        payment_status=payment_status,
    )
    db_session.add(manuscript)
    await db_session.flush()
    await db_session.refresh(manuscript)

    if add_chapter:
        chapter = Chapter(
            manuscript_id=manuscript.id,
            chapter_number=1,
            title="Chapter 1",
            raw_text="Some text for testing.",
            word_count=5,
            status=ChapterStatus.extracted,
        )
        db_session.add(chapter)
        await db_session.flush()

    return manuscript


@pytest.mark.asyncio
async def test_analyze_unpaid_returns_402(client: AsyncClient, db_session: AsyncSession):
    """Unpaid manuscript should be rejected with 402 Payment Required."""
    user = await _create_user(db_session, "unpaid@example.com")
    manuscript = await _create_manuscript(
        db_session,
        user,
        payment_status=PaymentStatus.unpaid,
        status=ManuscriptStatus.bible_complete,
    )
    await db_session.commit()

    resp = await client.post(
        f"/manuscripts/{manuscript.id}/analyze",
        cookies=_auth_cookie(user),
    )
    assert resp.status_code == 402
    assert "payment required" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_analyze_paid_proceeds(client: AsyncClient, db_session: AsyncSession):
    """Paid manuscript with bible_complete status should be accepted (202)."""
    user = await _create_user(db_session, "paid@example.com")
    manuscript = await _create_manuscript(
        db_session,
        user,
        payment_status=PaymentStatus.paid,
        status=ManuscriptStatus.bible_complete,
        add_chapter=True,
    )
    await db_session.commit()

    mock_redis = AsyncMock()
    mock_redis.enqueue_job = AsyncMock()

    with patch("app.manuscripts.router.create_pool", return_value=mock_redis):
        resp = await client.post(
            f"/manuscripts/{manuscript.id}/analyze",
            cookies=_auth_cookie(user),
        )
    assert resp.status_code == 202


@pytest.mark.asyncio
async def test_analyze_wrong_user_returns_404(client: AsyncClient, db_session: AsyncSession):
    """Manuscript owned by user A should not be visible to user B (404)."""
    user_a = await _create_user(db_session, "owner@example.com")
    user_b = await _create_user(db_session, "intruder@example.com")
    manuscript = await _create_manuscript(
        db_session,
        user_a,
        payment_status=PaymentStatus.paid,
        status=ManuscriptStatus.bible_complete,
    )
    await db_session.commit()

    resp = await client.post(
        f"/manuscripts/{manuscript.id}/analyze",
        cookies=_auth_cookie(user_b),
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_analyze_not_ready_returns_409(client: AsyncClient, db_session: AsyncSession):
    """Manuscript still in 'uploading' status should be rejected with 409."""
    user = await _create_user(db_session, "notready@example.com")
    manuscript = await _create_manuscript(
        db_session,
        user,
        payment_status=PaymentStatus.paid,
        status=ManuscriptStatus.uploading,
    )
    await db_session.commit()

    resp = await client.post(
        f"/manuscripts/{manuscript.id}/analyze",
        cookies=_auth_cookie(user),
    )
    assert resp.status_code == 409
