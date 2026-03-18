"""TC-001: Auth-scoping tests for all user-scoped endpoints.

Verifies that user A cannot access user B's data. All endpoints should
return 404 (not 403) to avoid confirming resource existence.
"""

from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.security import create_access_token
from app.db.models import (
    ArgumentMap,
    Chapter,
    ChapterAnalysis,
    ChapterStatus,
    Job,
    JobStatus,
    JobType,
    Manuscript,
    ManuscriptStatus,
    NonfictionDocumentSummary,
    NonfictionSectionResult,
    PaymentStatus,
    StoryBible,
    User,
)


async def _create_user(db_session: AsyncSession, email: str) -> User:
    """Create a full (non-provisional) user."""
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
    """Build an access_token cookie for authenticated requests."""
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
    status: ManuscriptStatus = ManuscriptStatus.bible_complete,
    payment_status: PaymentStatus = PaymentStatus.paid,
) -> Manuscript:
    """Create a manuscript owned by the given user."""
    manuscript = Manuscript(
        user_id=user.id,
        title="Test Manuscript",
        status=status,
        payment_status=payment_status,
    )
    db_session.add(manuscript)
    await db_session.flush()
    await db_session.refresh(manuscript)
    return manuscript


async def _create_chapter(
    db_session: AsyncSession,
    manuscript: Manuscript,
) -> Chapter:
    """Create a chapter for the given manuscript."""
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
    await db_session.refresh(chapter)
    return chapter


async def _create_story_bible(
    db_session: AsyncSession,
    manuscript: Manuscript,
) -> StoryBible:
    """Create a story bible for the given manuscript."""
    bible = StoryBible(
        manuscript_id=manuscript.id,
        bible_json={"characters": [], "timeline": [], "settings": []},
        version=1,
    )
    db_session.add(bible)
    await db_session.flush()
    await db_session.refresh(bible)
    return bible


async def _create_job(
    db_session: AsyncSession,
    manuscript: Manuscript,
) -> Job:
    """Create a job for the given manuscript."""
    job = Job(
        manuscript_id=manuscript.id,
        job_type=JobType.text_extraction,
        status=JobStatus.pending,
        current_step="Queued for text extraction",
    )
    db_session.add(job)
    await db_session.flush()
    await db_session.refresh(job)
    return job


# --- TC-001.1: GET /manuscripts/{id} ---


@pytest.mark.asyncio
async def test_get_manuscript_wrong_user_returns_404(
    client: AsyncClient, db_session: AsyncSession
):
    """User B cannot see user A's manuscript (returns 404)."""
    user_a = await _create_user(db_session, "owner-get@example.com")
    user_b = await _create_user(db_session, "intruder-get@example.com")
    manuscript = await _create_manuscript(db_session, user_a)
    await db_session.commit()

    resp = await client.get(
        f"/manuscripts/{manuscript.id}",
        cookies=_auth_cookie(user_b),
    )
    assert resp.status_code == 404


# --- TC-001.2: DELETE /manuscripts/{id} ---


@pytest.mark.asyncio
async def test_delete_manuscript_wrong_user_returns_404(
    client: AsyncClient, db_session: AsyncSession
):
    """User B cannot delete user A's manuscript (returns 404)."""
    user_a = await _create_user(db_session, "owner-del@example.com")
    user_b = await _create_user(db_session, "intruder-del@example.com")
    manuscript = await _create_manuscript(db_session, user_a)
    await db_session.commit()

    resp = await client.delete(
        f"/manuscripts/{manuscript.id}",
        cookies=_auth_cookie(user_b),
    )
    assert resp.status_code == 404


# --- TC-001.3: POST /manuscripts/{id}/analyze ---


@pytest.mark.asyncio
async def test_analyze_manuscript_wrong_user_returns_404(
    client: AsyncClient, db_session: AsyncSession
):
    """User B cannot trigger analysis on user A's manuscript (returns 404)."""
    user_a = await _create_user(db_session, "owner-analyze@example.com")
    user_b = await _create_user(db_session, "intruder-analyze@example.com")
    manuscript = await _create_manuscript(
        db_session,
        user_a,
        status=ManuscriptStatus.bible_complete,
        payment_status=PaymentStatus.paid,
    )
    await db_session.commit()

    resp = await client.post(
        f"/manuscripts/{manuscript.id}/analyze",
        cookies=_auth_cookie(user_b),
    )
    assert resp.status_code == 404


# --- TC-001.4: GET /bible/{id} ---


@pytest.mark.asyncio
async def test_get_bible_wrong_user_returns_404(
    client: AsyncClient, db_session: AsyncSession
):
    """User B cannot see user A's story bible (returns 404)."""
    user_a = await _create_user(db_session, "owner-bible@example.com")
    user_b = await _create_user(db_session, "intruder-bible@example.com")
    manuscript = await _create_manuscript(db_session, user_a)
    await _create_story_bible(db_session, manuscript)
    await db_session.commit()

    resp = await client.get(
        f"/bible/{manuscript.id}",
        cookies=_auth_cookie(user_b),
    )
    assert resp.status_code == 404


# --- TC-001.5: GET /bible/{id}/feedback ---


@pytest.mark.asyncio
async def test_get_feedback_wrong_user_returns_404(
    client: AsyncClient, db_session: AsyncSession
):
    """User B cannot see user A's feedback (returns 404)."""
    user_a = await _create_user(db_session, "owner-fb@example.com")
    user_b = await _create_user(db_session, "intruder-fb@example.com")
    manuscript = await _create_manuscript(db_session, user_a)
    await db_session.commit()

    resp = await client.get(
        f"/bible/{manuscript.id}/feedback",
        cookies=_auth_cookie(user_b),
    )
    assert resp.status_code == 404


# --- TC-001.6: GET /manuscripts/jobs/{id} ---


@pytest.mark.asyncio
async def test_get_job_status_wrong_user_returns_404(
    client: AsyncClient, db_session: AsyncSession
):
    """User B cannot see user A's job status (returns 404)."""
    user_a = await _create_user(db_session, "owner-job@example.com")
    user_b = await _create_user(db_session, "intruder-job@example.com")
    manuscript = await _create_manuscript(db_session, user_a)
    job = await _create_job(db_session, manuscript)
    await db_session.commit()

    resp = await client.get(
        f"/manuscripts/jobs/{job.id}",
        cookies=_auth_cookie(user_b),
    )
    assert resp.status_code == 404


# --- TC-001 additional: unauthenticated access ---


@pytest.mark.asyncio
async def test_get_manuscript_unauthenticated_returns_401(
    client: AsyncClient, db_session: AsyncSession
):
    """Unauthenticated request to GET /manuscripts/{id} returns 401."""
    user = await _create_user(db_session, "owner-noauth@example.com")
    manuscript = await _create_manuscript(db_session, user)
    await db_session.commit()

    resp = await client.get(f"/manuscripts/{manuscript.id}")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_delete_manuscript_unauthenticated_returns_401(
    client: AsyncClient, db_session: AsyncSession
):
    """Unauthenticated request to DELETE /manuscripts/{id} returns 401."""
    user = await _create_user(db_session, "owner-noauth-del@example.com")
    manuscript = await _create_manuscript(db_session, user)
    await db_session.commit()

    resp = await client.delete(f"/manuscripts/{manuscript.id}")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_analyze_unauthenticated_returns_401(
    client: AsyncClient, db_session: AsyncSession
):
    """Unauthenticated request to POST /manuscripts/{id}/analyze returns 401."""
    user = await _create_user(db_session, "owner-noauth-az@example.com")
    manuscript = await _create_manuscript(db_session, user)
    await db_session.commit()

    resp = await client.post(f"/manuscripts/{manuscript.id}/analyze")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_get_feedback_unauthenticated_returns_401(
    client: AsyncClient, db_session: AsyncSession
):
    """Unauthenticated request to GET /bible/{id}/feedback returns 401."""
    user = await _create_user(db_session, "owner-noauth-fb@example.com")
    manuscript = await _create_manuscript(db_session, user)
    await db_session.commit()

    resp = await client.get(f"/bible/{manuscript.id}/feedback")
    assert resp.status_code == 401
