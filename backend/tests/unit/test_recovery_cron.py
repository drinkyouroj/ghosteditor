"""Unit tests for _recover_stuck_manuscripts cron job.

Tests the recovery cron that detects manuscripts stuck in 'paid but not
analyzing' state (bible_complete + paid) and auto-enqueues analysis jobs.
"""

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.db.models import (
    Base,
    Chapter,
    ChapterStatus,
    DocumentType,
    Job,
    JobStatus,
    JobType,
    Manuscript,
    ManuscriptStatus,
    NonfictionFormat,
    PaymentStatus,
    User,
)
from app.jobs.worker import _recover_stuck_manuscripts


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def recovery_db():
    """Create a test database session with a fresh schema."""
    engine = create_async_engine(settings.database_url, echo=False)

    async with engine.begin() as conn:
        await conn.execute(text("DROP SCHEMA public CASCADE"))
        await conn.execute(text("CREATE SCHEMA public"))
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        yield session

    async with engine.begin() as conn:
        await conn.execute(text("DROP SCHEMA public CASCADE"))
        await conn.execute(text("CREATE SCHEMA public"))
        await conn.run_sync(Base.metadata.create_all)

    await engine.dispose()


async def _create_stuck_manuscript(
    db: AsyncSession,
    *,
    document_type: DocumentType = DocumentType.fiction,
    nonfiction_format: NonfictionFormat | None = None,
    minutes_ago: int = 60,
    payment_status: PaymentStatus = PaymentStatus.paid,
    status: ManuscriptStatus = ManuscriptStatus.bible_complete,
    deleted_at: datetime | None = None,
    chapter_status: ChapterStatus = ChapterStatus.extracted,
):
    """Helper to create a manuscript in a stuck state with a chapter."""
    user = User(
        email=f"recovery-{uuid.uuid4().hex[:8]}@example.com",
        is_provisional=False,
        email_verified=True,
    )
    db.add(user)
    await db.flush()
    await db.refresh(user)

    updated_at = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    manuscript = Manuscript(
        user_id=user.id,
        title="Test Stuck Manuscript",
        document_type=document_type,
        nonfiction_format=nonfiction_format,
        status=status,
        payment_status=payment_status,
        deleted_at=deleted_at,
    )
    db.add(manuscript)
    await db.flush()
    await db.refresh(manuscript)

    # Manually set updated_at to simulate time passage
    from sqlalchemy import update
    await db.execute(
        update(Manuscript)
        .where(Manuscript.id == manuscript.id)
        .values(updated_at=updated_at)
    )
    await db.commit()
    await db.refresh(manuscript)

    chapter = Chapter(
        manuscript_id=manuscript.id,
        chapter_number=1,
        title="Chapter 1",
        raw_text="Some text content for testing.",
        word_count=6,
        status=chapter_status,
    )
    db.add(chapter)
    await db.commit()
    await db.refresh(chapter)

    return manuscript, chapter


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@patch("app.jobs.worker._get_session_factory")
@patch("app.jobs.worker.create_pool")
async def test_recover_stuck_fiction_manuscript(mock_create_pool, mock_factory, recovery_db):
    """A fiction manuscript at bible_complete + paid should be recovered."""
    db = recovery_db
    manuscript, chapter = await _create_stuck_manuscript(db, document_type=DocumentType.fiction)

    # Set up mocks
    engine = create_async_engine(settings.database_url, echo=False)
    real_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    mock_factory.return_value = real_factory

    mock_redis = AsyncMock()
    mock_create_pool.return_value = mock_redis

    await _recover_stuck_manuscripts({})

    # Verify Redis enqueue was called with the correct function
    mock_redis.enqueue_job.assert_called_once()
    call_args = mock_redis.enqueue_job.call_args
    assert call_args[0][0] == "process_chapter_analysis"
    assert call_args[0][2] == str(manuscript.id)
    assert call_args[0][3] == str(chapter.id)

    # Verify manuscript status was updated
    async with real_factory() as check_session:
        result = await check_session.execute(
            select(Manuscript).where(Manuscript.id == manuscript.id)
        )
        ms = result.scalar_one()
        assert ms.status == ManuscriptStatus.analyzing

        # Verify a Job row was created
        job_result = await check_session.execute(
            select(Job).where(Job.manuscript_id == manuscript.id)
        )
        job = job_result.scalar_one()
        assert job.chapter_id == chapter.id
        assert job.job_type == JobType.chapter_analysis

    await engine.dispose()


@pytest.mark.asyncio
@patch("app.jobs.worker._get_session_factory")
@patch("app.jobs.worker.create_pool")
async def test_recover_stuck_nonfiction_manuscript(mock_create_pool, mock_factory, recovery_db):
    """A nonfiction manuscript at bible_complete + paid should use nonfiction analysis."""
    db = recovery_db
    manuscript, chapter = await _create_stuck_manuscript(
        db,
        document_type=DocumentType.nonfiction,
        nonfiction_format=NonfictionFormat.academic,
    )

    engine = create_async_engine(settings.database_url, echo=False)
    real_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    mock_factory.return_value = real_factory

    mock_redis = AsyncMock()
    mock_create_pool.return_value = mock_redis

    await _recover_stuck_manuscripts({})

    # Verify nonfiction-specific job function was enqueued
    mock_redis.enqueue_job.assert_called_once()
    call_args = mock_redis.enqueue_job.call_args
    assert call_args[0][0] == "process_nonfiction_section_analysis"

    await engine.dispose()


@pytest.mark.asyncio
@patch("app.jobs.worker._get_session_factory")
@patch("app.jobs.worker.create_pool")
async def test_no_recovery_for_unpaid_manuscript(mock_create_pool, mock_factory, recovery_db):
    """Unpaid manuscripts should not be recovered."""
    db = recovery_db
    await _create_stuck_manuscript(db, payment_status=PaymentStatus.unpaid)

    engine = create_async_engine(settings.database_url, echo=False)
    real_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    mock_factory.return_value = real_factory

    mock_redis = AsyncMock()
    mock_create_pool.return_value = mock_redis

    await _recover_stuck_manuscripts({})

    mock_redis.enqueue_job.assert_not_called()
    await engine.dispose()


@pytest.mark.asyncio
@patch("app.jobs.worker._get_session_factory")
@patch("app.jobs.worker.create_pool")
async def test_no_recovery_for_recently_updated_manuscript(mock_create_pool, mock_factory, recovery_db):
    """Manuscripts updated less than 30 minutes ago should not be recovered."""
    db = recovery_db
    await _create_stuck_manuscript(db, minutes_ago=10)  # Only 10 minutes old

    engine = create_async_engine(settings.database_url, echo=False)
    real_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    mock_factory.return_value = real_factory

    mock_redis = AsyncMock()
    mock_create_pool.return_value = mock_redis

    await _recover_stuck_manuscripts({})

    mock_redis.enqueue_job.assert_not_called()
    await engine.dispose()


@pytest.mark.asyncio
@patch("app.jobs.worker._get_session_factory")
@patch("app.jobs.worker.create_pool")
async def test_no_recovery_for_deleted_manuscript(mock_create_pool, mock_factory, recovery_db):
    """Soft-deleted manuscripts should not be recovered."""
    db = recovery_db
    await _create_stuck_manuscript(
        db, deleted_at=datetime.now(timezone.utc) - timedelta(days=1)
    )

    engine = create_async_engine(settings.database_url, echo=False)
    real_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    mock_factory.return_value = real_factory

    mock_redis = AsyncMock()
    mock_create_pool.return_value = mock_redis

    await _recover_stuck_manuscripts({})

    mock_redis.enqueue_job.assert_not_called()
    await engine.dispose()


@pytest.mark.asyncio
@patch("app.jobs.worker._get_session_factory")
@patch("app.jobs.worker.create_pool")
async def test_no_recovery_when_no_extracted_chapters(mock_create_pool, mock_factory, recovery_db):
    """Manuscripts with no extracted chapters should be skipped."""
    db = recovery_db
    await _create_stuck_manuscript(db, chapter_status=ChapterStatus.analyzed)

    engine = create_async_engine(settings.database_url, echo=False)
    real_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    mock_factory.return_value = real_factory

    mock_redis = AsyncMock()
    mock_create_pool.return_value = mock_redis

    await _recover_stuck_manuscripts({})

    mock_redis.enqueue_job.assert_not_called()
    await engine.dispose()
