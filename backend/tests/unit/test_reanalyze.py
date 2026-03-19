"""Tests for the POST /manuscripts/{id}/reanalyze endpoint."""

from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.security import create_access_token
from app.db.models import (
    Chapter,
    ChapterAnalysis,
    ChapterStatus,
    DocumentType,
    Manuscript,
    ManuscriptStatus,
    NonfictionDocumentSummary,
    NonfictionDimension,
    NonfictionSectionResult,
    PaymentStatus,
    SectionDetectionMethod,
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
    payment_status: PaymentStatus = PaymentStatus.paid,
    status: ManuscriptStatus = ManuscriptStatus.complete,
    document_type: DocumentType = DocumentType.fiction,
    add_chapters: bool = True,
    add_analyses: bool = False,
) -> Manuscript:
    """Helper: create a manuscript with chapters and optionally analyses."""
    manuscript = Manuscript(
        user_id=user.id,
        title="Test Manuscript",
        status=status,
        payment_status=payment_status,
        document_type=document_type,
    )
    db_session.add(manuscript)
    await db_session.flush()
    await db_session.refresh(manuscript)

    if add_chapters:
        for i in range(1, 4):
            ch_status = ChapterStatus.analyzed if status == ManuscriptStatus.complete else ChapterStatus.extracted
            chapter = Chapter(
                manuscript_id=manuscript.id,
                chapter_number=i,
                title=f"Chapter {i}",
                raw_text=f"Text for chapter {i}.",
                word_count=10,
                status=ch_status,
            )
            db_session.add(chapter)
        await db_session.flush()

    if add_analyses and add_chapters:
        chapters_result = await db_session.execute(
            select(Chapter).where(Chapter.manuscript_id == manuscript.id)
        )
        chapters = chapters_result.scalars().all()
        for ch in chapters:
            if document_type == DocumentType.fiction:
                analysis = ChapterAnalysis(
                    chapter_id=ch.id,
                    issues_json={"issues": []},
                    prompt_version="v1",
                )
                db_session.add(analysis)
            else:
                result = NonfictionSectionResult(
                    chapter_id=ch.id,
                    section_results_json={"issues": []},
                    dimension=NonfictionDimension.argument,
                    section_detection_method=SectionDetectionMethod.header,
                    prompt_version="v1",
                )
                db_session.add(result)
        if document_type == DocumentType.nonfiction:
            summary = NonfictionDocumentSummary(
                manuscript_id=manuscript.id,
                summary_json={"overall_assessment": "test"},
            )
            db_session.add(summary)
        await db_session.flush()

    return manuscript


@pytest.mark.asyncio
async def test_reanalyze_returns_202_for_valid_manuscript(
    client: AsyncClient, db_session: AsyncSession
):
    """Paid + complete manuscript should return 202."""
    user = await _create_user(db_session, "valid@example.com")
    manuscript = await _create_manuscript(
        db_session, user, payment_status=PaymentStatus.paid,
        status=ManuscriptStatus.complete, add_chapters=True,
    )
    await db_session.commit()

    mock_redis = AsyncMock()
    mock_redis.enqueue_job = AsyncMock()

    with patch("app.manuscripts.router._get_arq_pool", return_value=mock_redis):
        with patch("app.manuscripts.router.check_rate_limit", new_callable=AsyncMock):
            resp = await client.post(
                f"/manuscripts/{manuscript.id}/reanalyze",
                cookies=_auth_cookie(user),
            )
    assert resp.status_code == 202
    assert "re-analysis started" in resp.json()["message"].lower()


@pytest.mark.asyncio
async def test_reanalyze_wrong_user_returns_404(
    client: AsyncClient, db_session: AsyncSession
):
    """Manuscript owned by user A should not be accessible to user B."""
    user_a = await _create_user(db_session, "owner@example.com")
    user_b = await _create_user(db_session, "intruder@example.com")
    manuscript = await _create_manuscript(db_session, user_a)
    await db_session.commit()

    with patch("app.manuscripts.router.check_rate_limit", new_callable=AsyncMock):
        resp = await client.post(
            f"/manuscripts/{manuscript.id}/reanalyze",
            cookies=_auth_cookie(user_b),
        )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_reanalyze_unpaid_returns_402(
    client: AsyncClient, db_session: AsyncSession
):
    """Unpaid manuscript should be rejected with 402."""
    user = await _create_user(db_session, "unpaid@example.com")
    manuscript = await _create_manuscript(
        db_session, user, payment_status=PaymentStatus.unpaid,
        status=ManuscriptStatus.complete,
    )
    await db_session.commit()

    with patch("app.manuscripts.router.check_rate_limit", new_callable=AsyncMock):
        resp = await client.post(
            f"/manuscripts/{manuscript.id}/reanalyze",
            cookies=_auth_cookie(user),
        )
    assert resp.status_code == 402


@pytest.mark.asyncio
async def test_reanalyze_wrong_status_returns_409(
    client: AsyncClient, db_session: AsyncSession
):
    """Manuscript not in complete/error status should be rejected with 409."""
    user = await _create_user(db_session, "wrongstatus@example.com")
    manuscript = await _create_manuscript(
        db_session, user, payment_status=PaymentStatus.paid,
        status=ManuscriptStatus.analyzing,
    )
    await db_session.commit()

    with patch("app.manuscripts.router.check_rate_limit", new_callable=AsyncMock):
        resp = await client.post(
            f"/manuscripts/{manuscript.id}/reanalyze",
            cookies=_auth_cookie(user),
        )
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_reanalyze_clears_fiction_analyses(
    client: AsyncClient, db_session: AsyncSession
):
    """Re-analyze should delete ChapterAnalysis rows and reset chapter statuses."""
    user = await _create_user(db_session, "clearfiction@example.com")
    manuscript = await _create_manuscript(
        db_session, user, payment_status=PaymentStatus.paid,
        status=ManuscriptStatus.complete, add_chapters=True, add_analyses=True,
    )
    await db_session.commit()

    # Verify analyses exist before
    result = await db_session.execute(
        select(ChapterAnalysis)
        .join(Chapter)
        .where(Chapter.manuscript_id == manuscript.id)
    )
    assert len(result.scalars().all()) == 3

    mock_redis = AsyncMock()
    mock_redis.enqueue_job = AsyncMock()

    with patch("app.manuscripts.router._get_arq_pool", return_value=mock_redis):
        with patch("app.manuscripts.router.check_rate_limit", new_callable=AsyncMock):
            resp = await client.post(
                f"/manuscripts/{manuscript.id}/reanalyze",
                cookies=_auth_cookie(user),
            )
    assert resp.status_code == 202

    # Verify analyses are deleted
    result = await db_session.execute(
        select(ChapterAnalysis)
        .join(Chapter)
        .where(Chapter.manuscript_id == manuscript.id)
    )
    assert len(result.scalars().all()) == 0

    # Verify chapter statuses reset to extracted
    result = await db_session.execute(
        select(Chapter).where(Chapter.manuscript_id == manuscript.id)
    )
    chapters = result.scalars().all()
    for ch in chapters:
        await db_session.refresh(ch)
        assert ch.status == ChapterStatus.extracted


@pytest.mark.asyncio
async def test_reanalyze_rate_limit_returns_429(
    client: AsyncClient, db_session: AsyncSession
):
    """4th reanalyze in a day should be rejected with 429."""
    from fastapi import HTTPException

    user = await _create_user(db_session, "ratelimit@example.com")
    manuscript = await _create_manuscript(
        db_session, user, payment_status=PaymentStatus.paid,
        status=ManuscriptStatus.complete, add_chapters=True,
    )
    await db_session.commit()

    # Mock rate limit to raise 429
    async def raise_rate_limit(*args, **kwargs):
        raise HTTPException(
            status_code=429,
            detail="Rate limit exceeded. Maximum 3 requests per window. Try again in ~1440 minutes.",
        )

    with patch("app.manuscripts.router.check_rate_limit", side_effect=raise_rate_limit):
        resp = await client.post(
            f"/manuscripts/{manuscript.id}/reanalyze",
            cookies=_auth_cookie(user),
        )
    assert resp.status_code == 429
    assert "rate limit" in resp.json()["detail"].lower()
