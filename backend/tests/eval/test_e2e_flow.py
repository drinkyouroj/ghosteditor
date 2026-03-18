"""End-to-end integration test for the full manuscript flow.

Tests the complete state machine:
  upload -> extraction -> bible generation -> payment -> analysis -> feedback

Worker functions are called directly (not via Redis) with mocked LLM and S3.
Uses a real test database for state verification at each step.
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
    Base,
    Chapter,
    ChapterAnalysis,
    ChapterStatus,
    Job,
    JobStatus,
    JobType,
    Manuscript,
    ManuscriptStatus,
    PaymentStatus,
    StoryBible,
    User,
)
from app.db.session import get_db
from app.main import app


# ---------------------------------------------------------------------------
# Canned LLM responses
# ---------------------------------------------------------------------------

CANNED_BIBLE_JSON = {
    "characters": [
        {
            "name": "Test Character",
            "aliases": [],
            "description": "A test character who drives the plot",
            "role": "protagonist",
            "first_appearance": "Chapter 1",
            "traits": ["brave", "curious"],
            "physical": {"age": "30s", "gender": "female", "appearance": "tall"},
            "relationships": [],
        }
    ],
    "timeline": [
        {
            "event": "Story begins",
            "chapter": 1,
            "date_in_story": None,
            "characters_involved": ["Test Character"],
        }
    ],
    "settings": [
        {
            "name": "The Village",
            "description": "A quiet village at the edge of the forest",
            "chapter_introduced": 1,
        }
    ],
    "world_rules": ["Magic exists but is rare"],
    "voice_profile": {
        "pov": "third person",
        "tense": "past",
        "tone": "neutral",
        "style_notes": "Clean prose with occasional metaphor",
    },
    "plot_threads": [
        {
            "thread": "The quest begins",
            "status": "open",
            "introduced_chapter": 1,
            "last_updated_chapter": 1,
        }
    ],
}

CANNED_ANALYSIS_JSON = {
    "issues": [],
    "pacing": {
        "scene_count": 3,
        "scene_types": ["action", "dialogue", "reflection"],
        "tension_arc": "rising",
        "characters_present": ["Test Character"],
        "chapter_summary": "The protagonist arrives at the village and discovers a mystery.",
    },
    "genre_notes": {
        "conventions_met": ["strong opening hook"],
        "conventions_missed": [],
        "genre_fit_score": "strong",
    },
}

# LLM response for chapter splitting (detect_chapters calls call_llm)
CANNED_SPLITTING_JSON = {
    "manuscript_type": "novel",
    "structure_description": "A novel with chapter headers",
    "front_matter_end_marker": None,
    "sections": [
        {"marker": "Chapter 1", "title": "The Beginning"},
    ],
}

# Sample manuscript text (>500 words so analysis doesn't skip it)
SAMPLE_MANUSCRIPT_TEXT = (
    "Chapter 1\n\n"
    + "Test Character walked through the village, her boots crunching on gravel. "
    * 60
    + "\n\nThe sun was setting behind the mountains, casting long shadows. "
    * 40
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def e2e_db_session():
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
async def e2e_client(e2e_db_session):
    """Create a test HTTP client with the test DB session injected."""

    async def override_get_db():
        yield e2e_db_session

    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def e2e_user(e2e_db_session: AsyncSession):
    """Create a fully verified, non-provisional test user."""
    user = User(
        email="e2e-test@example.com",
        password_hash=hash_password("password123"),
        is_provisional=False,
        email_verified=True,
        tos_accepted_at=datetime.now(timezone.utc),
    )
    e2e_db_session.add(user)
    await e2e_db_session.commit()
    await e2e_db_session.refresh(user)
    return user


def _auth_cookies(user: User) -> dict[str, str]:
    """Generate auth cookies for a test user."""
    token = create_access_token(str(user.id), user.token_version, is_provisional=False)
    return {"access_token": token}


# ---------------------------------------------------------------------------
# The E2E test
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_full_manuscript_flow(
    e2e_client: AsyncClient,
    e2e_db_session: AsyncSession,
    e2e_user: User,
):
    """End-to-end test: upload -> extraction -> bible -> payment -> analysis -> feedback.

    Each step directly calls the worker function (bypassing Redis) and verifies
    database state transitions match the expected state machine.
    """
    from app.jobs.worker import (
        process_bible_generation,
        process_chapter_analysis,
        process_text_extraction,
    )

    db = e2e_db_session
    user = e2e_user
    cookies = _auth_cookies(user)

    # In-memory S3 storage for this test
    s3_store: dict[str, bytes] = {}

    def mock_upload_to_s3(content: bytes, s3_key: str) -> None:
        s3_store[s3_key] = content

    def mock_download_from_s3(s3_key: str) -> bytes:
        if s3_key not in s3_store:
            raise Exception(f"S3 key not found: {s3_key}")
        return s3_store[s3_key]

    # Track which LLM call we're on to return appropriate responses
    llm_call_count = 0

    async def mock_call_llm(prompt: str, model: str, max_tokens: int) -> str:
        nonlocal llm_call_count
        llm_call_count += 1
        # The splitting call happens first during extraction
        if "manuscript_sample" in prompt or "structure" in prompt.lower()[:200]:
            return json.dumps(CANNED_SPLITTING_JSON)
        # Bible generation
        if "story bible" in prompt.lower() or "bible" in prompt.lower():
            return json.dumps(CANNED_BIBLE_JSON)
        # Chapter analysis
        if "analysis" in prompt.lower() or "issue" in prompt.lower():
            return json.dumps(CANNED_ANALYSIS_JSON)
        # Fallback: return bible JSON (safe default)
        return json.dumps(CANNED_BIBLE_JSON)

    # Mock Redis pool for arq enqueue calls inside worker functions
    mock_redis = AsyncMock()
    mock_redis.enqueue_job = AsyncMock()

    async def mock_create_pool(*args, **kwargs):
        return mock_redis

    # Worker ctx (the workers receive a ctx dict from arq)
    ctx = {}

    # -----------------------------------------------------------------------
    # STEP 1: Upload manuscript via POST /manuscripts/upload
    # -----------------------------------------------------------------------
    file_content = SAMPLE_MANUSCRIPT_TEXT.encode("utf-8")

    with (
        patch("app.manuscripts.router.upload_to_s3", side_effect=mock_upload_to_s3),
        patch("app.manuscripts.router.create_pool", new=mock_create_pool),
        patch("app.rate_limit.aioredis.from_url", return_value=AsyncMock(
            incr=AsyncMock(return_value=1),
            expire=AsyncMock(),
            aclose=AsyncMock(),
        )),
    ):
        resp = await e2e_client.post(
            "/manuscripts/upload",
            data={"title": "Test Novel", "genre": "fantasy"},
            files={"file": ("test_novel.txt", file_content, "text/plain")},
            cookies=cookies,
        )

    assert resp.status_code == 201, f"Upload failed: {resp.text}"
    upload_data = resp.json()
    manuscript_id = upload_data["manuscript_id"]
    job_id = upload_data["job_id"]

    # Verify manuscript was created with correct initial state
    ms_result = await db.execute(select(Manuscript).where(Manuscript.id == uuid.UUID(manuscript_id)))
    manuscript = ms_result.scalar_one()
    assert manuscript.status == ManuscriptStatus.uploading
    assert manuscript.title == "Test Novel"
    assert manuscript.genre == "fantasy"
    assert manuscript.user_id == user.id

    # Verify S3 received the file
    assert len(s3_store) == 1
    stored_key = list(s3_store.keys())[0]
    assert stored_key == manuscript.s3_key

    # Verify extraction job was created
    job_result = await db.execute(select(Job).where(Job.id == uuid.UUID(job_id)))
    job = job_result.scalar_one()
    assert job.job_type == JobType.text_extraction
    assert job.status == JobStatus.pending

    # -----------------------------------------------------------------------
    # STEP 2: Run text extraction worker directly
    # -----------------------------------------------------------------------
    with (
        patch("app.manuscripts.s3.download_from_s3", side_effect=mock_download_from_s3),
        patch("app.analysis.llm_client.call_llm", new=mock_call_llm),
        patch("app.manuscripts.extraction.detect_language", return_value="en"),
        patch("app.jobs.worker.download_from_s3", side_effect=mock_download_from_s3),
        patch("app.jobs.worker.create_pool", new=mock_create_pool),
        patch("app.jobs.worker._get_session_factory") as mock_factory,
    ):
        # The worker creates its own session factory; we need to provide our test DB
        test_engine = create_async_engine(settings.database_url, echo=False)
        test_session_factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
        mock_factory.return_value = test_session_factory

        await process_text_extraction(ctx, job_id, manuscript_id)

        await test_engine.dispose()

    # Refresh manuscript state from DB
    await db.expire_all()
    ms_result = await db.execute(select(Manuscript).where(Manuscript.id == uuid.UUID(manuscript_id)))
    manuscript = ms_result.scalar_one()

    # After extraction: status should be bible_generating, chapters should exist
    assert manuscript.status == ManuscriptStatus.bible_generating, (
        f"Expected bible_generating, got {manuscript.status}"
    )
    assert manuscript.chapter_count is not None and manuscript.chapter_count >= 1

    # Verify chapters were created
    chapters_result = await db.execute(
        select(Chapter)
        .where(Chapter.manuscript_id == uuid.UUID(manuscript_id))
        .order_by(Chapter.chapter_number)
    )
    chapters = chapters_result.scalars().all()
    assert len(chapters) >= 1
    for ch in chapters:
        assert ch.status == ChapterStatus.extracted
        assert ch.word_count is not None and ch.word_count > 0

    # Verify bible generation job was created
    bible_job_result = await db.execute(
        select(Job).where(
            Job.manuscript_id == uuid.UUID(manuscript_id),
            Job.job_type == JobType.story_bible_generation,
        )
    )
    bible_job = bible_job_result.scalar_one()
    bible_job_id = str(bible_job.id)

    # -----------------------------------------------------------------------
    # STEP 3: Run bible generation worker directly
    # -----------------------------------------------------------------------
    with (
        patch("app.analysis.story_bible.call_llm", new=mock_call_llm),
        patch("app.jobs.worker.create_pool", new=mock_create_pool),
        patch("app.jobs.worker._get_session_factory") as mock_factory,
        patch("app.email.drip.schedule_drip_emails", new_callable=AsyncMock),
        patch("app.email.sender.send_bible_ready_email", new_callable=MagicMock),
    ):
        test_engine = create_async_engine(settings.database_url, echo=False)
        test_session_factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
        mock_factory.return_value = test_session_factory

        await process_bible_generation(ctx, bible_job_id, manuscript_id)

        await test_engine.dispose()

    # Refresh and verify bible state
    await db.expire_all()
    ms_result = await db.execute(select(Manuscript).where(Manuscript.id == uuid.UUID(manuscript_id)))
    manuscript = ms_result.scalar_one()
    assert manuscript.status == ManuscriptStatus.bible_complete, (
        f"Expected bible_complete, got {manuscript.status}"
    )

    # Verify story bible exists
    bible_result = await db.execute(
        select(StoryBible).where(StoryBible.manuscript_id == uuid.UUID(manuscript_id))
    )
    story_bible = bible_result.scalar_one()
    assert story_bible.version == 1
    assert "characters" in story_bible.bible_json
    assert len(story_bible.bible_json["characters"]) >= 1
    assert story_bible.bible_json["characters"][0]["name"] == "Test Character"

    # Verify bible job completed
    await db.expire_all()
    bible_job_result = await db.execute(select(Job).where(Job.id == uuid.UUID(bible_job_id)))
    bible_job = bible_job_result.scalar_one()
    assert bible_job.status == JobStatus.completed

    # -----------------------------------------------------------------------
    # STEP 4: Simulate payment (set payment_status = paid)
    # -----------------------------------------------------------------------
    ms_result = await db.execute(select(Manuscript).where(Manuscript.id == uuid.UUID(manuscript_id)))
    manuscript = ms_result.scalar_one()
    assert manuscript.payment_status == PaymentStatus.unpaid

    manuscript.payment_status = PaymentStatus.paid
    await db.commit()

    # -----------------------------------------------------------------------
    # STEP 5: Start chapter analysis via POST /manuscripts/{id}/analyze
    # -----------------------------------------------------------------------
    # First, we need to ensure there are extracted chapters ready for analysis.
    # Chapter 1 was marked as "analyzed" by bible generation, so we need at least
    # one chapter still in "extracted" status. Our sample text may have produced
    # only one chapter. Let's check and handle both cases.
    await db.expire_all()
    chapters_result = await db.execute(
        select(Chapter).where(
            Chapter.manuscript_id == uuid.UUID(manuscript_id),
            Chapter.status == ChapterStatus.extracted,
        ).order_by(Chapter.chapter_number)
    )
    extracted_chapters = chapters_result.scalars().all()

    if not extracted_chapters:
        # Bible generation marked Chapter 1 as analyzed. Reset it for analysis flow testing.
        # In production, /analyze would skip already-analyzed chapters.
        # For this test, we reset one chapter to exercise the analysis path.
        all_chapters_result = await db.execute(
            select(Chapter).where(
                Chapter.manuscript_id == uuid.UUID(manuscript_id),
            ).order_by(Chapter.chapter_number)
        )
        all_chapters = all_chapters_result.scalars().all()
        first_chapter = all_chapters[0]
        first_chapter.status = ChapterStatus.extracted
        await db.commit()
        extracted_chapters = [first_chapter]

    target_chapter = extracted_chapters[0]
    target_chapter_id = str(target_chapter.id)

    with patch("app.manuscripts.router.create_pool", new=mock_create_pool):
        resp = await e2e_client.post(
            f"/manuscripts/{manuscript_id}/analyze",
            cookies=cookies,
        )

    assert resp.status_code == 202, f"Analyze failed: {resp.text}"
    analyze_data = resp.json()
    assert analyze_data["chapters_queued"] >= 1

    # Find the analysis job
    await db.expire_all()
    analysis_job_result = await db.execute(
        select(Job).where(
            Job.manuscript_id == uuid.UUID(manuscript_id),
            Job.job_type == JobType.chapter_analysis,
        ).order_by(Job.created_at.desc())
    )
    analysis_job = analysis_job_result.scalars().first()
    assert analysis_job is not None
    analysis_job_id = str(analysis_job.id)

    # Verify manuscript status changed to analyzing
    ms_result = await db.execute(select(Manuscript).where(Manuscript.id == uuid.UUID(manuscript_id)))
    manuscript = ms_result.scalar_one()
    assert manuscript.status == ManuscriptStatus.analyzing

    # -----------------------------------------------------------------------
    # STEP 6: Run chapter analysis worker directly
    # -----------------------------------------------------------------------
    with (
        patch("app.analysis.story_bible.call_llm", new=mock_call_llm),
        patch("app.analysis.chapter_analyzer.call_llm", new=mock_call_llm),
        patch("app.jobs.worker.create_pool", new=mock_create_pool),
        patch("app.jobs.worker._get_session_factory") as mock_factory,
    ):
        test_engine = create_async_engine(settings.database_url, echo=False)
        test_session_factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
        mock_factory.return_value = test_session_factory

        await process_chapter_analysis(
            ctx, analysis_job_id, manuscript_id, target_chapter_id,
        )

        await test_engine.dispose()

    # Refresh and verify analysis state
    await db.expire_all()
    ms_result = await db.execute(select(Manuscript).where(Manuscript.id == uuid.UUID(manuscript_id)))
    manuscript = ms_result.scalar_one()
    # If this was the only extracted chapter, manuscript should be complete
    assert manuscript.status in (ManuscriptStatus.complete, ManuscriptStatus.analyzing), (
        f"Expected complete or analyzing, got {manuscript.status}"
    )

    # Verify chapter analysis was saved
    ch_result = await db.execute(select(Chapter).where(Chapter.id == uuid.UUID(target_chapter_id)))
    analyzed_chapter = ch_result.scalar_one()
    assert analyzed_chapter.status == ChapterStatus.analyzed

    analysis_result = await db.execute(
        select(ChapterAnalysis).where(ChapterAnalysis.chapter_id == uuid.UUID(target_chapter_id))
    )
    chapter_analysis = analysis_result.scalar_one()
    assert chapter_analysis.prompt_version == "chapter_analysis_v1"
    assert isinstance(chapter_analysis.pacing_json, dict)
    assert isinstance(chapter_analysis.genre_notes, dict)

    # Verify analysis job completed
    analysis_job_result = await db.execute(select(Job).where(Job.id == uuid.UUID(analysis_job_id)))
    analysis_job = analysis_job_result.scalar_one()
    assert analysis_job.status == JobStatus.completed

    # -----------------------------------------------------------------------
    # STEP 7: Get feedback via GET /bible/{id}/feedback
    # -----------------------------------------------------------------------
    resp = await e2e_client.get(
        f"/bible/{manuscript_id}/feedback",
        cookies=cookies,
    )

    assert resp.status_code == 200, f"Feedback failed: {resp.text}"
    feedback = resp.json()

    # Verify feedback response structure
    assert feedback["manuscript_id"] == manuscript_id
    assert feedback["title"] == "Test Novel"
    assert feedback["genre"] == "fantasy"
    assert "summary" in feedback
    assert "chapters" in feedback
    assert feedback["summary"]["chapters_analyzed"] >= 1
    assert feedback["summary"]["chapters_total"] >= 1
    assert isinstance(feedback["chapters"], list)
    assert len(feedback["chapters"]) >= 1

    # Verify individual chapter feedback structure
    ch_feedback = feedback["chapters"][0]
    assert "chapter_id" in ch_feedback
    assert "chapter_number" in ch_feedback
    assert "issues" in ch_feedback
    assert "issue_counts" in ch_feedback
    assert "pacing" in ch_feedback
    assert "genre_notes" in ch_feedback
    assert ch_feedback["status"] == "analyzed"

    # -----------------------------------------------------------------------
    # STEP 8: Verify story bible endpoint works
    # -----------------------------------------------------------------------
    resp = await e2e_client.get(
        f"/bible/{manuscript_id}",
        cookies=cookies,
    )

    assert resp.status_code == 200, f"Bible fetch failed: {resp.text}"
    bible_data = resp.json()
    assert bible_data["manuscript_id"] == manuscript_id
    assert bible_data["version"] >= 1
    assert "characters" in bible_data["bible"]
    assert bible_data["bible"]["characters"][0]["name"] == "Test Character"

    # -----------------------------------------------------------------------
    # STEP 9: Verify manuscript detail endpoint
    # -----------------------------------------------------------------------
    resp = await e2e_client.get(
        f"/manuscripts/{manuscript_id}",
        cookies=cookies,
    )

    assert resp.status_code == 200, f"Manuscript detail failed: {resp.text}"
    detail = resp.json()
    assert detail["id"] == manuscript_id
    assert detail["title"] == "Test Novel"
    assert detail["payment_status"] == "paid"
    assert detail["chapter_count"] >= 1
    assert len(detail["chapters"]) >= 1
