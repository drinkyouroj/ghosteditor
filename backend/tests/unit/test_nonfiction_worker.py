"""TC-003: Nonfiction worker unit tests.

Tests for process_argument_map_generation, process_nonfiction_section_analysis,
and process_nonfiction_synthesis worker functions.

NOTE: These tests require the nonfiction worker functions to be implemented
in app/jobs/worker.py. If not yet available, tests are skipped.
"""

import json
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.db.models import (
    ArgumentMap,
    Base,
    Chapter,
    ChapterStatus,
    DocumentType,
    Job,
    JobStatus,
    JobType,
    Manuscript,
    ManuscriptStatus,
    NonfictionDocumentSummary,
    NonfictionFormat,
    NonfictionSectionResult,
    PaymentStatus,
    User,
)


# ---------------------------------------------------------------------------
# Check if nonfiction worker functions exist
# ---------------------------------------------------------------------------

_NONFICTION_WORKER_AVAILABLE = False
try:
    from app.jobs.worker import (
        process_argument_map_generation,
        process_nonfiction_section_analysis,
        process_nonfiction_synthesis,
    )
    _NONFICTION_WORKER_AVAILABLE = True
except ImportError:
    pass

pytestmark = pytest.mark.skipif(
    not _NONFICTION_WORKER_AVAILABLE,
    reason="Nonfiction worker functions not yet implemented",
)


# ---------------------------------------------------------------------------
# Canned responses
# ---------------------------------------------------------------------------

CANNED_ARGUMENT_MAP = {
    "central_thesis": "Research shows deliberate practice drives expert performance",
    "claimed_audience": "researchers",
    "detected_format_confidence": {"format": "academic", "confidence": "high"},
    "voice_profile": {
        "register": "formal academic",
        "pov": "first person plural (we)",
        "notable_patterns": [],
    },
    "argument_threads": [
        {
            "id": "thread_1",
            "claim": "Deliberate practice is key",
            "first_seen_section": 1,
            "status": "supported",
        },
    ],
    "evidence_log": [
        {
            "section": 1,
            "type": "citation",
            "summary": "Ericsson (1993) study",
            "supports_claim_id": "thread_1",
        },
    ],
    "structural_markers": {
        "has_explicit_thesis": True,
        "has_conclusion": None,
        "section_count": 1,
    },
}

CANNED_SECTION_ANALYSIS = {
    "issues": [
        {
            "type": "evidence",
            "severity": "warning",
            "description": "Citation lacks year",
            "location": "paragraph 3",
            "suggestion": "Add publication year",
        }
    ],
    "pacing": {
        "density": "high",
        "flow_assessment": "logical",
        "recommendation": None,
    },
    "argument_map_updates": {},
}

CANNED_SYNTHESIS = {
    "overall_assessment": "Well-structured academic argument.",
    "thesis_clarity_score": 8,
    "evidence_sufficiency_score": 7,
    "structural_coherence_score": 8,
    "key_strengths": ["Strong citations"],
    "key_weaknesses": ["Missing conclusion"],
    "recommendations": ["Add conclusion"],
}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def nf_worker_db():
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


@pytest_asyncio.fixture
async def nf_manuscript(nf_worker_db: AsyncSession):
    """Create a nonfiction manuscript with chapters for testing."""
    db = nf_worker_db

    user = User(
        email="nf-worker@example.com",
        is_provisional=False,
        email_verified=True,
    )
    db.add(user)
    await db.flush()
    await db.refresh(user)

    manuscript = Manuscript(
        user_id=user.id,
        title="Test Academic Paper",
        document_type=DocumentType.nonfiction,
        nonfiction_format=NonfictionFormat.academic,
        status=ManuscriptStatus.bible_generating,
        payment_status=PaymentStatus.paid,
    )
    db.add(manuscript)
    await db.flush()
    await db.refresh(manuscript)

    chapter = Chapter(
        manuscript_id=manuscript.id,
        chapter_number=1,
        title="Introduction",
        raw_text="Research demonstrates deliberate practice. " * 100,
        word_count=500,
        status=ChapterStatus.extracted,
    )
    db.add(chapter)
    await db.flush()
    await db.refresh(chapter)

    manuscript.chapter_count = 1
    await db.commit()

    return manuscript, chapter, user


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _make_mock_session_factory(db_url: str):
    """Create a session factory for worker tests."""
    engine = create_async_engine(db_url, echo=False)
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def _create_job(db: AsyncSession, manuscript_id, job_type=JobType.text_extraction, chapter_id=None) -> Job:
    job = Job(
        manuscript_id=manuscript_id,
        chapter_id=chapter_id,
        job_type=job_type,
        current_step="Queued",
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)
    return job


# ---------------------------------------------------------------------------
# TC-003.1: process_argument_map_generation — happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_argument_map_generation_happy_path(nf_worker_db, nf_manuscript):
    """Argument map generation should create an ArgumentMap record."""
    db = nf_worker_db
    manuscript, chapter, user = nf_manuscript

    job = await _create_job(db, manuscript.id)

    async def mock_call_llm(prompt, model, max_tokens):
        return json.dumps(CANNED_ARGUMENT_MAP)

    mock_redis = AsyncMock()
    mock_redis.enqueue_job = AsyncMock()

    with (
        patch("app.analysis.llm_client.call_llm", new=mock_call_llm),
        patch("app.jobs.worker.create_pool", return_value=mock_redis),
        patch("app.jobs.worker._get_session_factory") as mock_factory,
    ):
        mock_factory.return_value = _make_mock_session_factory(settings.database_url)
        await process_argument_map_generation({}, str(job.id), str(manuscript.id))

    await db.expire_all()
    result = await db.execute(
        select(ArgumentMap).where(ArgumentMap.manuscript_id == manuscript.id)
    )
    argmap = result.scalar_one_or_none()
    assert argmap is not None, "ArgumentMap should be created"
    assert "central_thesis" in argmap.argument_map_json


# ---------------------------------------------------------------------------
# TC-003.2: process_argument_map_generation — LLM failure
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_argument_map_generation_llm_failure(nf_worker_db, nf_manuscript):
    """Argument map generation should handle LLM errors gracefully."""
    db = nf_worker_db
    manuscript, chapter, user = nf_manuscript

    job = await _create_job(db, manuscript.id)

    async def mock_call_llm_fail(prompt, model, max_tokens):
        raise Exception("LLM service temporarily overloaded")

    with (
        patch("app.analysis.llm_client.call_llm", new=mock_call_llm_fail),
        patch("app.jobs.worker.create_pool", return_value=AsyncMock(enqueue_job=AsyncMock())),
        patch("app.jobs.worker._get_session_factory") as mock_factory,
    ):
        mock_factory.return_value = _make_mock_session_factory(settings.database_url)
        await process_argument_map_generation({}, str(job.id), str(manuscript.id))

    await db.expire_all()
    result = await db.execute(select(Job).where(Job.id == job.id))
    updated_job = result.scalar_one()
    # Should be either failed or re-queued for retry
    assert updated_job.status in (JobStatus.failed, JobStatus.pending)


# ---------------------------------------------------------------------------
# TC-003.3: process_nonfiction_section_analysis — happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_section_analysis_happy_path(nf_worker_db, nf_manuscript):
    """Section analysis should create NonfictionSectionResult records."""
    db = nf_worker_db
    manuscript, chapter, user = nf_manuscript

    # Create argument map first (needed for section analysis)
    argmap = ArgumentMap(
        manuscript_id=manuscript.id,
        argument_map_json=CANNED_ARGUMENT_MAP,
        version=1,
    )
    db.add(argmap)
    await db.commit()

    job = await _create_job(db, manuscript.id, chapter_id=chapter.id)

    async def mock_call_llm(prompt, model, max_tokens):
        return json.dumps(CANNED_SECTION_ANALYSIS)

    with (
        patch("app.analysis.llm_client.call_llm", new=mock_call_llm),
        patch("app.jobs.worker.create_pool", return_value=AsyncMock(enqueue_job=AsyncMock())),
        patch("app.jobs.worker._get_session_factory") as mock_factory,
    ):
        mock_factory.return_value = _make_mock_session_factory(settings.database_url)
        await process_nonfiction_section_analysis(
            {}, str(job.id), str(manuscript.id), str(chapter.id)
        )

    await db.expire_all()
    results = await db.execute(select(NonfictionSectionResult))
    section_results = results.scalars().all()
    assert len(section_results) >= 1, "At least one section result should be created"


# ---------------------------------------------------------------------------
# TC-003.4: process_nonfiction_section_analysis — LLM failure
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_section_analysis_llm_failure(nf_worker_db, nf_manuscript):
    """Section analysis should handle LLM errors gracefully."""
    db = nf_worker_db
    manuscript, chapter, user = nf_manuscript

    argmap = ArgumentMap(
        manuscript_id=manuscript.id,
        argument_map_json=CANNED_ARGUMENT_MAP,
        version=1,
    )
    db.add(argmap)
    await db.commit()

    job = await _create_job(db, manuscript.id, chapter_id=chapter.id)

    async def mock_call_llm_fail(prompt, model, max_tokens):
        raise Exception("Connection timed out")

    with (
        patch("app.analysis.llm_client.call_llm", new=mock_call_llm_fail),
        patch("app.jobs.worker.create_pool", return_value=AsyncMock(enqueue_job=AsyncMock())),
        patch("app.jobs.worker._get_session_factory") as mock_factory,
    ):
        mock_factory.return_value = _make_mock_session_factory(settings.database_url)
        await process_nonfiction_section_analysis(
            {}, str(job.id), str(manuscript.id), str(chapter.id)
        )

    await db.expire_all()
    result = await db.execute(select(Job).where(Job.id == job.id))
    updated_job = result.scalar_one()
    assert updated_job.status in (JobStatus.failed, JobStatus.pending)


# ---------------------------------------------------------------------------
# TC-003.5: process_nonfiction_synthesis — happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_synthesis_happy_path(nf_worker_db, nf_manuscript):
    """Document synthesis should create NonfictionDocumentSummary."""
    db = nf_worker_db
    manuscript, chapter, user = nf_manuscript

    argmap = ArgumentMap(
        manuscript_id=manuscript.id,
        argument_map_json=CANNED_ARGUMENT_MAP,
        version=1,
    )
    db.add(argmap)
    await db.commit()

    job = await _create_job(db, manuscript.id)

    async def mock_call_llm(prompt, model, max_tokens):
        return json.dumps(CANNED_SYNTHESIS)

    with (
        patch("app.analysis.llm_client.call_llm", new=mock_call_llm),
        patch("app.jobs.worker.create_pool", return_value=AsyncMock(enqueue_job=AsyncMock())),
        patch("app.jobs.worker._get_session_factory") as mock_factory,
    ):
        mock_factory.return_value = _make_mock_session_factory(settings.database_url)
        await process_nonfiction_synthesis({}, str(job.id), str(manuscript.id))

    await db.expire_all()
    result = await db.execute(
        select(NonfictionDocumentSummary).where(
            NonfictionDocumentSummary.manuscript_id == manuscript.id
        )
    )
    summary = result.scalar_one_or_none()
    assert summary is not None, "Document summary should be created"
    assert "overall_assessment" in summary.summary_json


# ---------------------------------------------------------------------------
# TC-003.6: process_nonfiction_synthesis — LLM failure
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_synthesis_llm_failure(nf_worker_db, nf_manuscript):
    """Synthesis should handle LLM errors gracefully."""
    db = nf_worker_db
    manuscript, chapter, user = nf_manuscript

    argmap = ArgumentMap(
        manuscript_id=manuscript.id,
        argument_map_json=CANNED_ARGUMENT_MAP,
        version=1,
    )
    db.add(argmap)
    await db.commit()

    job = await _create_job(db, manuscript.id)

    async def mock_call_llm_fail(prompt, model, max_tokens):
        raise Exception("API rate limit exceeded")

    with (
        patch("app.analysis.llm_client.call_llm", new=mock_call_llm_fail),
        patch("app.jobs.worker.create_pool", return_value=AsyncMock(enqueue_job=AsyncMock())),
        patch("app.jobs.worker._get_session_factory") as mock_factory,
    ):
        mock_factory.return_value = _make_mock_session_factory(settings.database_url)
        await process_nonfiction_synthesis({}, str(job.id), str(manuscript.id))

    await db.expire_all()
    result = await db.execute(select(Job).where(Job.id == job.id))
    updated_job = result.scalar_one()
    assert updated_job.status in (JobStatus.failed, JobStatus.pending)
