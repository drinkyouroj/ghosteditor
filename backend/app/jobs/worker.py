"""Async job worker using arq (Redis-backed).

Processes text extraction and story bible generation jobs.
Each job updates its status in PostgreSQL for frontend polling.

Error handling strategy:
- Known errors (ExtractionError, StoryBibleError, ChapterAnalysisError) are user-facing
  and stored in job.error_message for frontend display.
- Transient errors (API rate limits, connection errors) trigger automatic retry
  up to job.max_attempts (default 3).
- Unexpected errors are logged with full traceback and stored as generic messages.
- Stalled jobs (stuck in "running" beyond timeout) are recovered via on_startup cleanup.
"""

import json
import logging
import uuid
from datetime import datetime, timedelta, timezone

from arq import create_pool, cron
from arq.connections import RedisSettings
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.analysis.chapter_analyzer import ChapterAnalysisError, analyze_chapter
from app.analysis.story_bible import StoryBibleError, generate_story_bible
from app.config import settings
from app.db.models import (
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
    StoryBibleVersion,
    User,
)
from app.manuscripts.extraction import ExtractionError, check_word_count, detect_chapters, extract_text
from app.manuscripts.s3 import download_from_s3

logger = logging.getLogger(__name__)

MAX_BIBLE_VERSIONS = 50
STALE_JOB_TIMEOUT_MINUTES = 15  # Jobs running longer than this are considered stalled

# Transient error messages that should trigger automatic retry
TRANSIENT_ERROR_KEYWORDS = ["temporarily busy", "temporarily overloaded", "timed out", "connection"]


def _get_session_factory():
    engine = create_async_engine(settings.database_url, echo=False)
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def _update_job(session: AsyncSession, job_id: uuid.UUID, **kwargs):
    result = await session.execute(select(Job).where(Job.id == job_id))
    job = result.scalar_one()
    for key, value in kwargs.items():
        setattr(job, key, value)
    await session.commit()


def _is_transient_error(error_msg: str) -> bool:
    """Check if an error message indicates a transient failure worth retrying."""
    return any(kw in error_msg.lower() for kw in TRANSIENT_ERROR_KEYWORDS)


async def _fail_job_with_retry(
    session_factory,
    job_uuid: uuid.UUID,
    ms_uuid: uuid.UUID,
    error_msg: str,
    step_label: str,
    job_func: str,
):
    """Fail a job, or re-enqueue it if the error is transient and retries remain."""
    async with session_factory() as session:
        result = await session.execute(select(Job).where(Job.id == job_uuid))
        job = result.scalar_one()

        if _is_transient_error(error_msg) and job.attempts < job.max_attempts:
            # Transient error with retries remaining — re-enqueue
            logger.info(
                f"Transient error on job {job_uuid} (attempt {job.attempts}/{job.max_attempts}), "
                f"re-enqueueing: {error_msg}"
            )
            job.status = JobStatus.pending
            job.current_step = f"Retrying ({job.attempts}/{job.max_attempts})..."
            job.error_message = None
            await session.commit()

            redis = await create_pool(RedisSettings.from_dsn(settings.redis_url))
            await redis.enqueue_job(job_func, str(job_uuid), str(ms_uuid), _defer_by=30)
            return

        # Permanent failure or retries exhausted
        job.status = JobStatus.failed
        job.error_message = error_msg
        job.current_step = step_label
        job.completed_at = datetime.now(timezone.utc)
        await session.commit()

        # Update manuscript status
        ms_result = await session.execute(select(Manuscript).where(Manuscript.id == ms_uuid))
        ms = ms_result.scalar_one()
        ms.status = ManuscriptStatus.error
        await session.commit()


async def _recover_stalled_jobs(ctx):
    """On worker startup, find and fail jobs stuck in 'running' state.

    This handles cases where the worker crashed or arq killed a job at timeout
    without proper cleanup.
    """
    session_factory = _get_session_factory()
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=STALE_JOB_TIMEOUT_MINUTES)

    async with session_factory() as session:
        result = await session.execute(
            select(Job).where(
                Job.status == JobStatus.running,
                Job.started_at < cutoff,
            )
        )
        stalled_jobs = result.scalars().all()

        for job in stalled_jobs:
            logger.warning(f"Recovering stalled job {job.id} (started {job.started_at})")
            job.status = JobStatus.failed
            job.error_message = (
                "Processing was interrupted. Please try uploading again."
            )
            job.current_step = "Recovery: job timed out"
            job.completed_at = datetime.now(timezone.utc)

            # Reset manuscript status from stuck intermediate state
            ms_result = await session.execute(
                select(Manuscript).where(Manuscript.id == job.manuscript_id)
            )
            ms = ms_result.scalar_one_or_none()
            if ms and ms.status in (
                ManuscriptStatus.extracting,
                ManuscriptStatus.bible_generating,
                ManuscriptStatus.analyzing,
            ):
                ms.status = ManuscriptStatus.error

        if stalled_jobs:
            await session.commit()
            logger.info(f"Recovered {len(stalled_jobs)} stalled jobs")


async def process_text_extraction(ctx, job_id: str, manuscript_id: str):
    """Extract text from uploaded manuscript and detect chapters."""
    session_factory = _get_session_factory()
    job_uuid = uuid.UUID(job_id)
    ms_uuid = uuid.UUID(manuscript_id)

    async with session_factory() as session:
        try:
            await _update_job(
                session, job_uuid,
                status=JobStatus.running,
                started_at=datetime.now(timezone.utc),
                current_step="Downloading file",
                progress_pct=10,
                attempts=Job.attempts + 1,
            )

            # Get manuscript
            result = await session.execute(select(Manuscript).where(Manuscript.id == ms_uuid))
            manuscript = result.scalar_one()
            manuscript.status = ManuscriptStatus.extracting
            await session.commit()

            # Download from S3
            content = download_from_s3(manuscript.s3_key)

            await _update_job(session, job_uuid, current_step="Extracting text", progress_pct=30)

            # Extract text
            ext = "." + manuscript.s3_key.rsplit(".", 1)[-1]
            full_text = extract_text(content, ext)

            await _update_job(session, job_uuid, current_step="Detecting chapters", progress_pct=50)

            # Detect chapters
            chapters_data = detect_chapters(full_text)
            total_words = check_word_count(chapters_data)

            await _update_job(session, job_uuid, current_step="Saving chapters", progress_pct=70)

            # Create chapter rows
            for ch_data in chapters_data:
                chapter = Chapter(
                    manuscript_id=ms_uuid,
                    chapter_number=ch_data["chapter_number"],
                    title=ch_data.get("title"),
                    raw_text=ch_data["text"],
                    word_count=ch_data["word_count"],
                    status=ChapterStatus.extracted,
                )
                session.add(chapter)

            manuscript.status = ManuscriptStatus.bible_generating
            manuscript.chapter_count = len(chapters_data)
            manuscript.word_count_est = total_words
            await session.commit()

            await _update_job(
                session, job_uuid,
                status=JobStatus.completed,
                completed_at=datetime.now(timezone.utc),
                current_step="Text extraction complete",
                progress_pct=100,
            )

            # Enqueue bible generation for Chapter 1
            bible_job = Job(
                manuscript_id=ms_uuid,
                chapter_id=None,
                job_type=JobType.story_bible_generation,
                current_step="Queued for story bible generation",
            )
            session.add(bible_job)
            await session.commit()
            await session.refresh(bible_job)

            # Enqueue the bible generation job in arq
            redis = await create_pool(RedisSettings.from_dsn(settings.redis_url))
            await redis.enqueue_job(
                "process_bible_generation",
                str(bible_job.id),
                manuscript_id,
            )

        except ExtractionError as e:
            logger.error(f"Extraction error for manuscript {manuscript_id}: {e}")
            await _fail_job_with_retry(
                session_factory, job_uuid, ms_uuid,
                str(e), "Extraction failed", "process_text_extraction",
            )
        except Exception as e:
            logger.exception(f"Unexpected error in text extraction for {manuscript_id}")
            await _fail_job_with_retry(
                session_factory, job_uuid, ms_uuid,
                f"An unexpected error occurred while processing your file. Please try again.",
                "Extraction failed", "process_text_extraction",
            )


async def process_bible_generation(ctx, job_id: str, manuscript_id: str):
    """Generate story bible from Chapter 1 (or update from subsequent chapters)."""
    session_factory = _get_session_factory()
    job_uuid = uuid.UUID(job_id)
    ms_uuid = uuid.UUID(manuscript_id)

    async with session_factory() as session:
        try:
            await _update_job(
                session, job_uuid,
                status=JobStatus.running,
                started_at=datetime.now(timezone.utc),
                current_step="Preparing bible generation",
                progress_pct=10,
                attempts=Job.attempts + 1,
            )

            # Get manuscript and chapters
            result = await session.execute(select(Manuscript).where(Manuscript.id == ms_uuid))
            manuscript = result.scalar_one()

            chapters_result = await session.execute(
                select(Chapter)
                .where(Chapter.manuscript_id == ms_uuid)
                .order_by(Chapter.chapter_number)
            )
            chapters = chapters_result.scalars().all()

            if not chapters:
                raise StoryBibleError("No chapters found for this manuscript")

            # Check for existing bible
            bible_result = await session.execute(
                select(StoryBible).where(StoryBible.manuscript_id == ms_uuid)
            )
            existing_bible_row = bible_result.scalar_one_or_none()
            existing_bible = existing_bible_row.bible_json if existing_bible_row else None

            # Process Chapter 1 first (or next unprocessed chapter)
            chapter_to_process = chapters[0]
            for ch in chapters:
                if ch.status == ChapterStatus.extracted:
                    chapter_to_process = ch
                    break

            chapter_num = chapter_to_process.chapter_number
            await _update_job(
                session, job_uuid,
                current_step=f"Generating bible from Chapter {chapter_num}",
                progress_pct=30,
            )

            # Call Claude
            bible_schema, warnings = await generate_story_bible(
                chapter_text=chapter_to_process.raw_text,
                chapter_number=chapter_num,
                genre=manuscript.genre,
                existing_bible=existing_bible,
            )

            bible_dict = bible_schema.model_dump()

            await _update_job(session, job_uuid, current_step="Saving story bible", progress_pct=80)

            # Save or update bible
            if existing_bible_row is None:
                story_bible = StoryBible(
                    manuscript_id=ms_uuid,
                    bible_json=bible_dict,
                    version=1,
                )
                session.add(story_bible)
                await session.flush()

                # Save version snapshot
                version = StoryBibleVersion(
                    story_bible_id=story_bible.id,
                    bible_json=bible_dict,
                    version=1,
                    created_by_chapter_id=chapter_to_process.id,
                )
                session.add(version)
            else:
                new_version = existing_bible_row.version + 1
                existing_bible_row.bible_json = bible_dict
                existing_bible_row.version = new_version

                # Save version snapshot (with cap enforcement)
                version = StoryBibleVersion(
                    story_bible_id=existing_bible_row.id,
                    bible_json=bible_dict,
                    version=new_version,
                    created_by_chapter_id=chapter_to_process.id,
                )
                session.add(version)

                # Enforce version cap (max 50, per DECISION_001 JUDGE)
                if new_version > MAX_BIBLE_VERSIONS:
                    delete_version = new_version - MAX_BIBLE_VERSIONS
                    old_result = await session.execute(
                        select(StoryBibleVersion).where(
                            StoryBibleVersion.story_bible_id == existing_bible_row.id,
                            StoryBibleVersion.version == delete_version,
                        )
                    )
                    old_version = old_result.scalar_one_or_none()
                    if old_version:
                        await session.delete(old_version)

            # Update chapter status
            chapter_to_process.status = ChapterStatus.analyzed

            # Update manuscript status
            manuscript.status = ManuscriptStatus.bible_complete
            await session.commit()

            # Log warnings
            for w in warnings:
                logger.warning(f"Bible generation warning (manuscript {manuscript_id}): {w}")

            await _update_job(
                session, job_uuid,
                status=JobStatus.completed,
                completed_at=datetime.now(timezone.utc),
                current_step="Story bible generated",
                progress_pct=100,
            )

            # Schedule drip emails for unpaid manuscripts + send bible-ready notification
            if manuscript.payment_status == PaymentStatus.unpaid:
                try:
                    from app.email.drip import schedule_drip_emails
                    from app.email.sender import send_bible_ready_email

                    user_result = await session.execute(
                        select(User).where(User.id == manuscript.user_id)
                    )
                    user = user_result.scalar_one_or_none()
                    if user:
                        bible_url = f"http://localhost:5173/manuscripts/{manuscript_id}/bible"
                        send_bible_ready_email(user.email, manuscript.title, bible_url)

                    await schedule_drip_emails(
                        session, manuscript.user_id, ms_uuid,
                        datetime.now(timezone.utc),
                    )
                except Exception as e:
                    logger.warning(f"Failed to schedule drip emails: {e}")

        except StoryBibleError as e:
            logger.error(f"Bible generation error for manuscript {manuscript_id}: {e}")
            await _fail_job_with_retry(
                session_factory, job_uuid, ms_uuid,
                str(e), "Bible generation failed", "process_bible_generation",
            )
        except Exception as e:
            logger.exception(f"Unexpected error in bible generation for {manuscript_id}")
            await _fail_job_with_retry(
                session_factory, job_uuid, ms_uuid,
                "An unexpected error occurred while generating your story bible. Please try again.",
                "Bible generation failed", "process_bible_generation",
            )


async def process_chapter_analysis(ctx, job_id: str, manuscript_id: str, chapter_id: str):
    """Analyze a single chapter using Claude API.

    For each chapter:
    1. Update the story bible with new information from this chapter
    2. Analyze the chapter against the updated bible
    3. Chain to the next unanalyzed chapter

    Chapters are processed sequentially so the bible accumulates
    characters, timeline, and plot threads as the manuscript progresses.
    """
    session_factory = _get_session_factory()
    job_uuid = uuid.UUID(job_id)
    ms_uuid = uuid.UUID(manuscript_id)
    ch_uuid = uuid.UUID(chapter_id)

    async with session_factory() as session:
        try:
            await _update_job(
                session, job_uuid,
                status=JobStatus.running,
                started_at=datetime.now(timezone.utc),
                current_step="Preparing chapter analysis",
                progress_pct=5,
                attempts=Job.attempts + 1,
            )

            # Get manuscript, chapter, and story bible
            result = await session.execute(select(Manuscript).where(Manuscript.id == ms_uuid))
            manuscript = result.scalar_one()

            ch_result = await session.execute(select(Chapter).where(Chapter.id == ch_uuid))
            chapter = ch_result.scalar_one()
            chapter.status = ChapterStatus.analyzing
            await session.commit()

            bible_result = await session.execute(
                select(StoryBible).where(StoryBible.manuscript_id == ms_uuid)
            )
            bible_row = bible_result.scalar_one_or_none()
            existing_bible = bible_row.bible_json if bible_row else None

            # --- Step 1: Update story bible with this chapter ---
            # Skip bible update for chapter 1 (already generated during initial bible pass)
            if chapter.chapter_number > 1 and bible_row is not None:
                await _update_job(
                    session, job_uuid,
                    current_step=f"Updating bible with Chapter {chapter.chapter_number}",
                    progress_pct=15,
                )

                bible_schema, bible_warnings = await generate_story_bible(
                    chapter_text=chapter.raw_text,
                    chapter_number=chapter.chapter_number,
                    genre=manuscript.genre,
                    existing_bible=existing_bible,
                )

                bible_dict = bible_schema.model_dump()

                # Update bible row
                new_version = bible_row.version + 1
                bible_row.bible_json = bible_dict
                bible_row.version = new_version

                # Save version snapshot
                version = StoryBibleVersion(
                    story_bible_id=bible_row.id,
                    bible_json=bible_dict,
                    version=new_version,
                    created_by_chapter_id=chapter.id,
                )
                session.add(version)

                # Enforce version cap
                if new_version > MAX_BIBLE_VERSIONS:
                    delete_version = new_version - MAX_BIBLE_VERSIONS
                    old_result = await session.execute(
                        select(StoryBibleVersion).where(
                            StoryBibleVersion.story_bible_id == bible_row.id,
                            StoryBibleVersion.version == delete_version,
                        )
                    )
                    old_version = old_result.scalar_one_or_none()
                    if old_version:
                        await session.delete(old_version)

                await session.commit()

                for w in bible_warnings:
                    logger.warning(f"Bible update warning (Chapter {chapter.chapter_number}): {w}")

                # Use the updated bible for analysis
                existing_bible = bible_dict

                logger.info(
                    f"Bible updated for manuscript {manuscript_id} "
                    f"(Chapter {chapter.chapter_number}, v{new_version})"
                )

            # --- Step 2: Analyze chapter against current bible ---
            await _update_job(
                session, job_uuid,
                current_step=f"Analyzing Chapter {chapter.chapter_number}",
                progress_pct=50,
            )

            analysis_result, warnings = await analyze_chapter(
                chapter_text=chapter.raw_text,
                chapter_number=chapter.chapter_number,
                genre=manuscript.genre,
                bible_json=existing_bible,
            )

            await _update_job(session, job_uuid, current_step="Saving analysis", progress_pct=80)

            # Save analysis
            chapter_analysis = ChapterAnalysis(
                chapter_id=ch_uuid,
                issues_json=analysis_result.model_dump().get("issues", []),
                pacing_json=analysis_result.model_dump().get("pacing"),
                genre_notes=analysis_result.model_dump().get("genre_notes"),
                prompt_version="chapter_analysis_v1",
            )
            session.add(chapter_analysis)

            chapter.status = ChapterStatus.analyzed
            await session.commit()

            for w in warnings:
                logger.warning(f"Analysis warning (chapter {chapter_id}): {w}")

            await _update_job(
                session, job_uuid,
                status=JobStatus.completed,
                completed_at=datetime.now(timezone.utc),
                current_step="Chapter analysis complete",
                progress_pct=100,
            )

            # --- Step 3: Chain to next chapter or mark complete ---
            next_ch_result = await session.execute(
                select(Chapter).where(
                    Chapter.manuscript_id == ms_uuid,
                    Chapter.status == ChapterStatus.extracted,
                ).order_by(Chapter.chapter_number).limit(1)
            )
            next_chapter = next_ch_result.scalar_one_or_none()

            if next_chapter is not None:
                # Enqueue next chapter
                next_job = Job(
                    manuscript_id=ms_uuid,
                    chapter_id=next_chapter.id,
                    job_type=JobType.chapter_analysis,
                    current_step=f"Queued: Chapter {next_chapter.chapter_number}",
                )
                session.add(next_job)
                await session.commit()
                await session.refresh(next_job)

                redis = await create_pool(RedisSettings.from_dsn(settings.redis_url))
                await redis.enqueue_job(
                    "process_chapter_analysis",
                    str(next_job.id), manuscript_id, str(next_chapter.id),
                )
                logger.info(
                    f"Chained to Chapter {next_chapter.chapter_number} "
                    f"for manuscript {manuscript_id}"
                )
            else:
                # All chapters analyzed
                manuscript.status = ManuscriptStatus.complete
                await session.commit()
                logger.info(f"All chapters analyzed for manuscript {manuscript_id}")

        except (ChapterAnalysisError, StoryBibleError) as e:
            logger.error(f"Chapter analysis error for chapter {chapter_id}: {e}")
            await _fail_job_with_retry(
                session_factory, job_uuid, ms_uuid,
                str(e), "Chapter analysis failed", "process_chapter_analysis",
            )
        except Exception as e:
            logger.exception(f"Unexpected error in chapter analysis for {chapter_id}")
            await _fail_job_with_retry(
                session_factory, job_uuid, ms_uuid,
                "An unexpected error occurred while analyzing your chapter. Please try again.",
                "Chapter analysis failed", "process_chapter_analysis",
            )


async def process_drip_emails(ctx):
    """Process pending drip emails. Run periodically via arq cron."""
    session_factory = _get_session_factory()
    async with session_factory() as session:
        from app.email.drip import process_pending_emails
        count = await process_pending_emails(session)
        if count > 0:
            logger.info(f"Processed {count} pending email events")


class WorkerSettings:
    """arq worker settings."""
    functions = [process_text_extraction, process_bible_generation, process_chapter_analysis]
    cron_jobs = [
        # Run drip email dispatch every hour at :00
        cron(process_drip_emails, minute=0),
    ]
    on_startup = _recover_stalled_jobs
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
    max_jobs = 5
    job_timeout = 300  # 5 minutes per job
