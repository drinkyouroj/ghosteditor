"""TC-002: End-to-end integration test for the nonfiction manuscript flow.

Tests the complete nonfiction state machine:
  upload -> extraction -> argument map generation -> section analysis -> synthesis

Worker functions are called directly (not via Redis) with mocked LLM and S3.
Uses a real test database for state verification at each step.

NOTE: This test requires the nonfiction worker functions to be implemented.
If the nonfiction pipeline functions are not yet available, these tests
will be skipped with an informative message.
"""

import json
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.auth.security import create_access_token, hash_password
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
from app.db.session import get_db
from app.main import app


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
# Canned LLM responses for nonfiction pipeline
# ---------------------------------------------------------------------------

CANNED_ARGUMENT_MAP_JSON = {
    "central_thesis": "Research demonstrates that deliberate practice is the key to expert performance",
    "claimed_audience": "graduate students in psychology",
    "detected_format_confidence": {
        "format": "academic",
        "confidence": "high",
    },
    "voice_profile": {
        "register": "formal academic",
        "pov": "first person plural (we)",
        "notable_patterns": ["heavy use of citations", "statistical claims"],
    },
    "argument_threads": [
        {
            "id": "thread_1",
            "claim": "Deliberate practice is qualitatively different from mere repetition",
            "first_seen_section": 1,
            "status": "supported",
        },
        {
            "id": "thread_2",
            "claim": "Expert performance requires at least 10,000 hours of deliberate practice",
            "first_seen_section": 1,
            "status": "open",
        },
    ],
    "evidence_log": [
        {
            "section": 1,
            "type": "citation",
            "summary": "Ericsson (1993) foundational study on violin students",
            "supports_claim_id": "thread_1",
        },
        {
            "section": 1,
            "type": "statistic",
            "summary": "Top performers averaged 10,000 hours of practice by age 20",
            "supports_claim_id": "thread_2",
        },
    ],
    "structural_markers": {
        "has_explicit_thesis": True,
        "has_conclusion": None,
        "section_count": 1,
    },
}

CANNED_SECTION_ANALYSIS_JSON = {
    "issues": [
        {
            "type": "evidence",
            "severity": "warning",
            "description": "Citation lacks publication year",
            "location": "paragraph 3",
            "suggestion": "Add publication year for credibility",
        }
    ],
    "pacing": {
        "density": "high",
        "flow_assessment": "logical progression",
        "recommendation": None,
    },
    "argument_map_updates": {
        "new_evidence": [
            {
                "section": 2,
                "type": "study",
                "summary": "Follow-up study confirming deliberate practice effects",
                "supports_claim_id": "thread_1",
            }
        ],
    },
}

CANNED_SYNTHESIS_JSON = {
    "overall_assessment": "The document presents a well-structured academic argument with strong evidential support.",
    "thesis_clarity_score": 8,
    "evidence_sufficiency_score": 7,
    "structural_coherence_score": 8,
    "key_strengths": [
        "Strong foundational citations",
        "Clear argument progression",
    ],
    "key_weaknesses": [
        "Some citations missing publication years",
    ],
    "recommendations": [
        "Add publication years to all citations",
        "Include a conclusion section",
    ],
}

CANNED_SPLITTING_JSON = {
    "manuscript_type": "nonfiction",
    "structure_description": "An academic paper with section headers",
    "front_matter_end_marker": None,
    "sections": [
        {"marker": "Section 1: Introduction", "title": "Introduction"},
        {"marker": "Section 2: Methods", "title": "Methods"},
    ],
}

SAMPLE_NONFICTION_TEXT = (
    "Section 1: Introduction\n\n"
    + "Research demonstrates that deliberate practice is the key to expert performance. "
    * 60
    + "\n\nSection 2: Methods\n\n"
    + "We conducted a meta-analysis of studies examining practice and performance. "
    * 40
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def nf_db_session():
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
async def nf_client(nf_db_session):
    """Create a test HTTP client with the test DB session injected."""

    async def override_get_db():
        yield nf_db_session

    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def nf_user(nf_db_session: AsyncSession):
    """Create a fully verified test user."""
    user = User(
        email="nf-e2e-test@example.com",
        password_hash=hash_password("password123"),
        is_provisional=False,
        email_verified=True,
        tos_accepted_at=datetime.now(timezone.utc),
    )
    nf_db_session.add(user)
    await nf_db_session.commit()
    await nf_db_session.refresh(user)
    return user


def _auth_cookies(user: User) -> dict[str, str]:
    token = create_access_token(str(user.id), user.token_version, is_provisional=False)
    return {"access_token": token}


# ---------------------------------------------------------------------------
# The E2E nonfiction test
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_full_nonfiction_flow(
    nf_client: AsyncClient,
    nf_db_session: AsyncSession,
    nf_user: User,
):
    """End-to-end nonfiction test: upload -> extraction -> argument map -> section analysis -> synthesis.

    Each step directly calls the worker function (bypassing Redis) and verifies
    database state transitions.
    """
    db = nf_db_session
    user = nf_user
    cookies = _auth_cookies(user)

    # In-memory S3 storage
    s3_store: dict[str, bytes] = {}

    def mock_upload_to_s3(content: bytes, s3_key: str) -> None:
        s3_store[s3_key] = content

    def mock_download_from_s3(s3_key: str) -> bytes:
        if s3_key not in s3_store:
            raise Exception(f"S3 key not found: {s3_key}")
        return s3_store[s3_key]

    llm_call_count = 0

    async def mock_call_llm(prompt: str, model: str, max_tokens: int) -> str:
        nonlocal llm_call_count
        llm_call_count += 1
        if "structure" in prompt.lower()[:200] or "manuscript_sample" in prompt:
            return json.dumps(CANNED_SPLITTING_JSON)
        if "argument map" in prompt.lower() or "argument_map" in prompt.lower():
            return json.dumps(CANNED_ARGUMENT_MAP_JSON)
        if "section" in prompt.lower() and "analysis" in prompt.lower():
            return json.dumps(CANNED_SECTION_ANALYSIS_JSON)
        if "synthesis" in prompt.lower() or "document-level" in prompt.lower():
            return json.dumps(CANNED_SYNTHESIS_JSON)
        return json.dumps(CANNED_ARGUMENT_MAP_JSON)

    mock_redis = AsyncMock()
    mock_redis.enqueue_job = AsyncMock()

    async def mock_create_pool(*args, **kwargs):
        return mock_redis

    ctx = {}

    # STEP 1: Create nonfiction manuscript directly in DB
    manuscript = Manuscript(
        user_id=user.id,
        title="Test Academic Paper",
        genre=None,
        document_type=DocumentType.nonfiction,
        nonfiction_format=NonfictionFormat.academic,
        status=ManuscriptStatus.uploading,
        payment_status=PaymentStatus.paid,
        s3_key=f"manuscripts/{user.id}/test/original.txt",
    )
    db.add(manuscript)
    await db.commit()
    await db.refresh(manuscript)
    manuscript_id = str(manuscript.id)

    # Store content in mock S3
    s3_store[manuscript.s3_key] = SAMPLE_NONFICTION_TEXT.encode("utf-8")

    # STEP 2: Run text extraction
    from app.jobs.worker import process_text_extraction

    extraction_job = Job(
        manuscript_id=manuscript.id,
        job_type=JobType.text_extraction,
        current_step="Queued for text extraction",
    )
    db.add(extraction_job)
    await db.commit()
    await db.refresh(extraction_job)

    with (
        patch("app.manuscripts.s3.download_from_s3", side_effect=mock_download_from_s3),
        patch("app.analysis.llm_client.call_llm", new=mock_call_llm),
        patch("app.manuscripts.extraction.detect_language", return_value="en"),
        patch("app.jobs.worker.download_from_s3", side_effect=mock_download_from_s3),
        patch("app.jobs.worker.create_pool", new=mock_create_pool),
        patch("app.jobs.worker._get_session_factory") as mock_factory,
    ):
        test_engine = create_async_engine(settings.database_url, echo=False)
        test_session_factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
        mock_factory.return_value = test_session_factory

        await process_text_extraction(ctx, str(extraction_job.id), manuscript_id)
        await test_engine.dispose()

    # Verify chapters were created
    await db.expire_all()
    chapters_result = await db.execute(
        select(Chapter)
        .where(Chapter.manuscript_id == manuscript.id)
        .order_by(Chapter.chapter_number)
    )
    chapters = chapters_result.scalars().all()
    assert len(chapters) >= 1, "At least one section/chapter should be created"

    # STEP 3: Run argument map generation
    argmap_job = Job(
        manuscript_id=manuscript.id,
        job_type=JobType.text_extraction,  # Will be nonfiction job type when implemented
        current_step="Queued for argument map generation",
    )
    db.add(argmap_job)
    await db.commit()
    await db.refresh(argmap_job)

    with (
        patch("app.analysis.llm_client.call_llm", new=mock_call_llm),
        patch("app.jobs.worker.create_pool", new=mock_create_pool),
        patch("app.jobs.worker._get_session_factory") as mock_factory,
    ):
        test_engine = create_async_engine(settings.database_url, echo=False)
        test_session_factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
        mock_factory.return_value = test_session_factory

        await process_argument_map_generation(ctx, str(argmap_job.id), manuscript_id)
        await test_engine.dispose()

    # Verify argument map was created
    await db.expire_all()
    argmap_result = await db.execute(
        select(ArgumentMap).where(ArgumentMap.manuscript_id == manuscript.id)
    )
    argument_map = argmap_result.scalar_one_or_none()
    assert argument_map is not None, "Argument map should be created"
    assert "central_thesis" in argument_map.argument_map_json
    assert argument_map.version >= 1

    # STEP 4: Run section analysis for each chapter
    for chapter in chapters:
        section_job = Job(
            manuscript_id=manuscript.id,
            chapter_id=chapter.id,
            job_type=JobType.chapter_analysis,
            current_step=f"Queued: Section {chapter.chapter_number}",
        )
        db.add(section_job)
        await db.commit()
        await db.refresh(section_job)

        with (
            patch("app.analysis.llm_client.call_llm", new=mock_call_llm),
            patch("app.jobs.worker.create_pool", new=mock_create_pool),
            patch("app.jobs.worker._get_session_factory") as mock_factory,
        ):
            test_engine = create_async_engine(settings.database_url, echo=False)
            test_session_factory = async_sessionmaker(
                test_engine, class_=AsyncSession, expire_on_commit=False
            )
            mock_factory.return_value = test_session_factory

            await process_nonfiction_section_analysis(
                ctx, str(section_job.id), manuscript_id, str(chapter.id)
            )
            await test_engine.dispose()

    # Verify section results were created
    await db.expire_all()
    section_results = await db.execute(
        select(NonfictionSectionResult)
    )
    results = section_results.scalars().all()
    assert len(results) >= 1, "At least one section result should be created"

    # STEP 5: Run document synthesis
    synthesis_job = Job(
        manuscript_id=manuscript.id,
        job_type=JobType.text_extraction,  # Will be nonfiction synthesis job type
        current_step="Queued for document synthesis",
    )
    db.add(synthesis_job)
    await db.commit()
    await db.refresh(synthesis_job)

    with (
        patch("app.analysis.llm_client.call_llm", new=mock_call_llm),
        patch("app.jobs.worker.create_pool", new=mock_create_pool),
        patch("app.jobs.worker._get_session_factory") as mock_factory,
    ):
        test_engine = create_async_engine(settings.database_url, echo=False)
        test_session_factory = async_sessionmaker(
            test_engine, class_=AsyncSession, expire_on_commit=False
        )
        mock_factory.return_value = test_session_factory

        await process_nonfiction_synthesis(ctx, str(synthesis_job.id), manuscript_id)
        await test_engine.dispose()

    # Verify document summary was created
    await db.expire_all()
    summary_result = await db.execute(
        select(NonfictionDocumentSummary).where(
            NonfictionDocumentSummary.manuscript_id == manuscript.id
        )
    )
    summary = summary_result.scalar_one_or_none()
    assert summary is not None, "Document synthesis should be created"
    assert "overall_assessment" in summary.summary_json
