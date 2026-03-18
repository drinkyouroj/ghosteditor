"""Async job worker using arq (Redis-backed).

Processes text extraction, story bible generation, chapter analysis,
and nonfiction pipeline jobs (argument map, section analysis, synthesis).
Each job updates its status in PostgreSQL for frontend polling.

Error handling strategy:
- Known errors (ExtractionError, StoryBibleError, ChapterAnalysisError) are user-facing
  and stored in job.error_message for frontend display.
- Transient errors (API rate limits, connection errors) trigger automatic retry
  up to job.max_attempts (default 3).
- Unexpected errors are logged with full traceback and stored as generic messages.
- Stalled jobs (stuck in "running" beyond timeout) are recovered via on_startup cleanup.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timedelta, timezone

from arq import create_pool, cron
from arq.connections import RedisSettings
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.analysis.argument_map import ArgumentMapError, generate_argument_map
from app.analysis.chapter_analyzer import ChapterAnalysisError, analyze_chapter
from app.analysis.nonfiction_analyzer import NonfictionAnalysisError, analyze_nonfiction_section
from app.analysis.nonfiction_synthesis import SynthesisError, generate_document_synthesis
from app.analysis.story_bible import StoryBibleError, generate_story_bible
from app.config import settings
from app.db.models import (
    ArgumentMap,
    Chapter,
    ChapterAnalysis,
    ChapterStatus,
    DocumentType,
    EmailEvent,
    Job,
    JobStatus,
    JobType,
    Manuscript,
    ManuscriptStatus,
    NonfictionDocumentSummary,
    NonfictionSectionResult,
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
TRANSIENT_ERROR_KEYWORDS = ["temporarily busy", "temporarily overloaded", "timed out", "connection", "connect"]


_worker_engine = create_async_engine(settings.database_url, echo=False)
_worker_session_factory = async_sessionmaker(_worker_engine, class_=AsyncSession, expire_on_commit=False)


def _get_session_factory():
    return _worker_session_factory


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
    chapter_uuid: uuid.UUID | None = None,
):
    """Fail a job, or re-enqueue it if the error is transient and retries remain.

    If chapter_uuid is provided, reverts the chapter status to 'extracted' on
    permanent failure so the chapter is eligible for retry via /analyze.
    """
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
            args = [str(job_uuid), str(ms_uuid)]
            if chapter_uuid is not None:
                args.append(str(chapter_uuid))
            await redis.enqueue_job(job_func, *args, _defer_by=30)
            return

        # Permanent failure or retries exhausted
        job.status = JobStatus.failed
        job.error_message = error_msg
        job.current_step = step_label
        job.completed_at = datetime.now(timezone.utc)

        # Revert chapter status so it's eligible for retry
        if chapter_uuid is not None:
            ch_result = await session.execute(select(Chapter).where(Chapter.id == chapter_uuid))
            ch = ch_result.scalar_one_or_none()
            if ch and ch.status == ChapterStatus.analyzing:
                ch.status = ChapterStatus.extracted
                logger.info(f"Reverted chapter {chapter_uuid} status to 'extracted' for retry eligibility")

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

            # Revert chapter status for chapter analysis jobs
            if job.chapter_id:
                ch_result = await session.execute(
                    select(Chapter).where(Chapter.id == job.chapter_id)
                )
                ch = ch_result.scalar_one_or_none()
                if ch and ch.status == ChapterStatus.analyzing:
                    ch.status = ChapterStatus.extracted
                    logger.info(f"Reverted stalled chapter {job.chapter_id} to 'extracted'")

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
            # Guard: skip if job row doesn't exist (orphaned Redis job)
            guard_result = await session.execute(select(Job).where(Job.id == job_uuid))
            if guard_result.scalar_one_or_none() is None:
                logger.warning(f"Job {job_id} not found in database — orphaned Redis job, skipping")
                return

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

            # Detect chapters — nonfiction uses header detection (DECISION_008),
            # fiction uses LLM-assisted splitting (DECISION_007)
            doc_type = manuscript.document_type.value if manuscript.document_type else None
            chapters_data, split_warnings = await detect_chapters(full_text, document_type=doc_type)
            if split_warnings:
                for w in split_warnings:
                    logger.warning(f"Splitting warning for manuscript {manuscript_id}: {w}")
            total_words = check_word_count(chapters_data)

            logger.info(
                f"Manuscript {manuscript_id}: {len(chapters_data)} chapters detected, "
                f"{total_words} total words"
            )
            for ch_data in chapters_data:
                logger.info(
                    f"  Ch {ch_data['chapter_number']}: {ch_data.get('title', 'untitled')!r} "
                    f"({ch_data['word_count']} words, method={ch_data.get('split_method', '?')})"
                )

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

            # Branch based on document_type: nonfiction -> argument map, fiction -> story bible
            is_nonfiction = manuscript.document_type == DocumentType.nonfiction

            if is_nonfiction:
                next_job = Job(
                    manuscript_id=ms_uuid,
                    chapter_id=None,
                    job_type=JobType.story_bible_generation,  # Reuse type for argument map
                    current_step="Queued for argument map generation",
                )
                session.add(next_job)
                await session.flush()
                await session.refresh(next_job)

                try:
                    redis = await create_pool(RedisSettings.from_dsn(settings.redis_url))
                    await redis.enqueue_job(
                        "process_argument_map_generation",
                        str(next_job.id),
                        manuscript_id,
                    )
                except Exception as enqueue_err:
                    logger.error(f"Failed to enqueue argument map generation for manuscript {manuscript_id}: {enqueue_err}")
                    await session.rollback()
                    async with session_factory() as err_session:
                        ms_result = await err_session.execute(select(Manuscript).where(Manuscript.id == ms_uuid))
                        ms = ms_result.scalar_one()
                        ms.status = ManuscriptStatus.error
                        await err_session.commit()
                    return

                await session.commit()
            else:
                # Enqueue bible generation for Chapter 1
                bible_job = Job(
                    manuscript_id=ms_uuid,
                    chapter_id=None,
                    job_type=JobType.story_bible_generation,
                    current_step="Queued for story bible generation",
                )
                session.add(bible_job)

                # Flush to get IDs without committing
                await session.flush()
                await session.refresh(bible_job)

                # Enqueue to Redis — if this fails, log error and mark manuscript as error
                try:
                    redis = await create_pool(RedisSettings.from_dsn(settings.redis_url))
                    await redis.enqueue_job(
                        "process_bible_generation",
                        str(bible_job.id),
                        manuscript_id,
                    )
                except Exception as enqueue_err:
                    logger.error(f"Failed to enqueue bible generation for manuscript {manuscript_id}: {enqueue_err}")
                    await session.rollback()
                    async with session_factory() as err_session:
                        ms_result = await err_session.execute(select(Manuscript).where(Manuscript.id == ms_uuid))
                        ms = ms_result.scalar_one()
                        ms.status = ManuscriptStatus.error
                        await err_session.commit()
                    return

                # Only commit after successful enqueue
                await session.commit()

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
            # Guard: skip if job row doesn't exist (orphaned Redis job)
            guard_result = await session.execute(select(Job).where(Job.id == job_uuid))
            if guard_result.scalar_one_or_none() is None:
                logger.warning(f"Job {job_id} not found in database — orphaned Redis job, skipping")
                return

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

            # Store drift warnings in bible JSON so they're surfaced via the API
            if warnings:
                bible_dict["_warnings"] = warnings

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
                        bible_url = f"{settings.base_url}/manuscripts/{manuscript_id}/bible"
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
            # Guard: skip if job row doesn't exist (orphaned Redis job)
            guard_result = await session.execute(select(Job).where(Job.id == job_uuid))
            if guard_result.scalar_one_or_none() is None:
                logger.warning(f"Job {job_id} not found in database — orphaned Redis job, skipping")
                return

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

                # Store drift warnings in bible JSON so they're surfaced via the API
                if bible_warnings:
                    bible_dict["_warnings"] = bible_warnings

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
            analysis_dump = analysis_result.model_dump()
            issues_data = analysis_dump.get("issues", [])
            # Store issues as a dict so we can include metadata like issues_capped and skip_reason
            issues_json_value = {
                "issues": issues_data,
                "issues_capped": analysis_dump.get("issues_capped", False),
            }

            # Flag chapters that were too short for meaningful analysis
            if not issues_data and chapter.word_count is not None and chapter.word_count < 500:
                issues_json_value["skip_reason"] = (
                    "Chapter has fewer than 500 words — too short for meaningful analysis"
                )
            chapter_analysis = ChapterAnalysis(
                chapter_id=ch_uuid,
                issues_json=issues_json_value,
                pacing_json=analysis_dump.get("pacing"),
                genre_notes=analysis_dump.get("genre_notes"),
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

                # Flush to get IDs without committing
                await session.flush()
                await session.refresh(next_job)

                # Enqueue to Redis — if this fails, log error and mark manuscript as error
                try:
                    redis = await create_pool(RedisSettings.from_dsn(settings.redis_url))
                    await redis.enqueue_job(
                        "process_chapter_analysis",
                        str(next_job.id), manuscript_id, str(next_chapter.id),
                    )
                except Exception as enqueue_err:
                    logger.error(f"Failed to enqueue next chapter analysis for manuscript {manuscript_id}: {enqueue_err}")
                    await session.rollback()
                    async with session_factory() as err_session:
                        ms_result = await err_session.execute(select(Manuscript).where(Manuscript.id == ms_uuid))
                        ms = ms_result.scalar_one()
                        ms.status = ManuscriptStatus.error
                        await err_session.commit()
                    return

                # Only commit after successful enqueue
                await session.commit()
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
                chapter_uuid=ch_uuid,
            )
        except Exception as e:
            logger.exception(f"Unexpected error in chapter analysis for {chapter_id}")
            await _fail_job_with_retry(
                session_factory, job_uuid, ms_uuid,
                "An unexpected error occurred while analyzing your chapter. Please try again.",
                "Chapter analysis failed", "process_chapter_analysis",
                chapter_uuid=ch_uuid,
            )


async def process_argument_map_generation(ctx, job_id: str, manuscript_id: str):
    """Generate argument map from all sections of a nonfiction manuscript.

    Mirrors process_bible_generation() but for nonfiction documents.
    Saves to the ArgumentMap table and sets manuscript status to bible_complete
    (reuses same status to share downstream flow).
    """
    session_factory = _get_session_factory()
    job_uuid = uuid.UUID(job_id)
    ms_uuid = uuid.UUID(manuscript_id)

    async with session_factory() as session:
        try:
            # Guard: skip if job row doesn't exist (orphaned Redis job)
            guard_result = await session.execute(select(Job).where(Job.id == job_uuid))
            if guard_result.scalar_one_or_none() is None:
                logger.warning(f"Job {job_id} not found in database — orphaned Redis job, skipping")
                return

            await _update_job(
                session, job_uuid,
                status=JobStatus.running,
                started_at=datetime.now(timezone.utc),
                current_step="Preparing argument map generation",
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
                raise ArgumentMapError("No sections found for this manuscript")

            # Combine all section texts for argument map generation
            full_text = "\n\n".join(ch.raw_text for ch in chapters if ch.raw_text)

            await _update_job(
                session, job_uuid,
                current_step="Generating argument map",
                progress_pct=30,
            )

            # Call LLM
            nf_format = manuscript.nonfiction_format.value if manuscript.nonfiction_format else None
            argument_map_schema, warnings = await generate_argument_map(
                manuscript_text=full_text,
                nonfiction_format=nf_format,
            )

            arg_map_dict = argument_map_schema.model_dump()

            # Store warnings in JSON
            if warnings:
                arg_map_dict["_warnings"] = warnings

            await _update_job(session, job_uuid, current_step="Saving argument map", progress_pct=80)

            # Check for existing argument map
            existing_result = await session.execute(
                select(ArgumentMap).where(ArgumentMap.manuscript_id == ms_uuid)
            )
            existing_row = existing_result.scalar_one_or_none()

            if existing_row is None:
                arg_map = ArgumentMap(
                    manuscript_id=ms_uuid,
                    argument_map_json=arg_map_dict,
                    version=1,
                )
                session.add(arg_map)
            else:
                existing_row.argument_map_json = arg_map_dict
                existing_row.version = existing_row.version + 1

            # Update manuscript status — reuse bible_complete for downstream compatibility
            manuscript.status = ManuscriptStatus.bible_complete
            await session.commit()

            # Log warnings
            for w in warnings:
                logger.warning(f"Argument map warning (manuscript {manuscript_id}): {w}")

            await _update_job(
                session, job_uuid,
                status=JobStatus.completed,
                completed_at=datetime.now(timezone.utc),
                current_step="Argument map generated",
                progress_pct=100,
            )

            # Schedule drip emails for unpaid manuscripts
            if manuscript.payment_status == PaymentStatus.unpaid:
                try:
                    from app.email.drip import schedule_drip_emails
                    from app.email.sender import send_bible_ready_email

                    user_result = await session.execute(
                        select(User).where(User.id == manuscript.user_id)
                    )
                    user = user_result.scalar_one_or_none()
                    if user:
                        arg_map_url = f"{settings.base_url}/manuscripts/{manuscript_id}/argument-map"
                        send_bible_ready_email(user.email, manuscript.title, arg_map_url)

                    await schedule_drip_emails(
                        session, manuscript.user_id, ms_uuid,
                        datetime.now(timezone.utc),
                    )
                except Exception as e:
                    logger.warning(f"Failed to schedule drip emails: {e}")

        except ArgumentMapError as e:
            logger.error(f"Argument map generation error for manuscript {manuscript_id}: {e}")
            await _fail_job_with_retry(
                session_factory, job_uuid, ms_uuid,
                str(e), "Argument map generation failed", "process_argument_map_generation",
            )
        except Exception as e:
            logger.exception(f"Unexpected error in argument map generation for {manuscript_id}")
            await _fail_job_with_retry(
                session_factory, job_uuid, ms_uuid,
                "An unexpected error occurred while generating your argument map. Please try again.",
                "Argument map generation failed", "process_argument_map_generation",
            )


async def process_nonfiction_section_analysis(ctx, job_id: str, manuscript_id: str, chapter_id: str):
    """Analyze a single nonfiction section.

    Mirrors process_chapter_analysis() but for nonfiction documents.
    Uses the argument map as context (same role as story bible in fiction).
    Saves results to NonfictionSectionResult table.
    After all sections complete, enqueues nonfiction synthesis.
    """
    session_factory = _get_session_factory()
    job_uuid = uuid.UUID(job_id)
    ms_uuid = uuid.UUID(manuscript_id)
    ch_uuid = uuid.UUID(chapter_id)

    async with session_factory() as session:
        try:
            # Guard: skip if job row doesn't exist (orphaned Redis job)
            guard_result = await session.execute(select(Job).where(Job.id == job_uuid))
            if guard_result.scalar_one_or_none() is None:
                logger.warning(f"Job {job_id} not found in database — orphaned Redis job, skipping")
                return

            await _update_job(
                session, job_uuid,
                status=JobStatus.running,
                started_at=datetime.now(timezone.utc),
                current_step="Preparing section analysis",
                progress_pct=5,
                attempts=Job.attempts + 1,
            )

            # Get manuscript, chapter, and argument map
            result = await session.execute(select(Manuscript).where(Manuscript.id == ms_uuid))
            manuscript = result.scalar_one()

            ch_result = await session.execute(select(Chapter).where(Chapter.id == ch_uuid))
            chapter = ch_result.scalar_one()
            chapter.status = ChapterStatus.analyzing
            await session.commit()

            arg_map_result = await session.execute(
                select(ArgumentMap).where(ArgumentMap.manuscript_id == ms_uuid)
            )
            arg_map_row = arg_map_result.scalar_one_or_none()
            argument_map_json = arg_map_row.argument_map_json if arg_map_row else None

            # --- Analyze section ---
            await _update_job(
                session, job_uuid,
                current_step=f"Analyzing section {chapter.chapter_number}",
                progress_pct=30,
            )

            nf_format = manuscript.nonfiction_format.value if manuscript.nonfiction_format else None
            analysis_result, warnings = await analyze_nonfiction_section(
                section_text=chapter.raw_text,
                section_number=chapter.chapter_number,
                nonfiction_format=nf_format,
                argument_map_json=argument_map_json,
            )

            await _update_job(
                session, job_uuid,
                current_step="Saving section analysis",
                progress_pct=70,
            )

            # Save section result
            analysis_dump = analysis_result.model_dump()
            section_result = NonfictionSectionResult(
                chapter_id=ch_uuid,
                section_results_json=analysis_dump,
                dimension=analysis_dump.get("dimension", "argument"),
                section_detection_method=analysis_dump.get("section_detection_method", "header"),
                prompt_version="nonfiction_section_analysis_v1",
            )
            session.add(section_result)

            chapter.status = ChapterStatus.analyzed
            await session.commit()

            for w in warnings:
                logger.warning(f"Section analysis warning (section {chapter_id}): {w}")

            await _update_job(
                session, job_uuid,
                status=JobStatus.completed,
                completed_at=datetime.now(timezone.utc),
                current_step="Section analysis complete",
                progress_pct=100,
            )

            # --- Chain to next section or enqueue synthesis ---
            next_ch_result = await session.execute(
                select(Chapter).where(
                    Chapter.manuscript_id == ms_uuid,
                    Chapter.status == ChapterStatus.extracted,
                ).order_by(Chapter.chapter_number).limit(1)
            )
            next_chapter = next_ch_result.scalar_one_or_none()

            if next_chapter is not None:
                # Enqueue next section
                next_job = Job(
                    manuscript_id=ms_uuid,
                    chapter_id=next_chapter.id,
                    job_type=JobType.chapter_analysis,  # Reuse type
                    current_step=f"Queued: Section {next_chapter.chapter_number}",
                )
                session.add(next_job)
                await session.flush()
                await session.refresh(next_job)

                try:
                    redis = await create_pool(RedisSettings.from_dsn(settings.redis_url))
                    await redis.enqueue_job(
                        "process_nonfiction_section_analysis",
                        str(next_job.id), manuscript_id, str(next_chapter.id),
                    )
                except Exception as enqueue_err:
                    logger.error(f"Failed to enqueue next section analysis for manuscript {manuscript_id}: {enqueue_err}")
                    await session.rollback()
                    async with session_factory() as err_session:
                        ms_result = await err_session.execute(select(Manuscript).where(Manuscript.id == ms_uuid))
                        ms = ms_result.scalar_one()
                        ms.status = ManuscriptStatus.error
                        await err_session.commit()
                    return

                await session.commit()
                logger.info(
                    f"Chained to Section {next_chapter.chapter_number} "
                    f"for manuscript {manuscript_id}"
                )
            else:
                # All sections analyzed — enqueue synthesis
                await _update_job(
                    session, job_uuid,
                    current_step="All sections analyzed, queuing synthesis",
                    progress_pct=100,
                )

                synthesis_job = Job(
                    manuscript_id=ms_uuid,
                    chapter_id=None,
                    job_type=JobType.pacing_analysis,  # Reuse type for synthesis
                    current_step="Queued for document synthesis",
                )
                session.add(synthesis_job)
                await session.flush()
                await session.refresh(synthesis_job)

                try:
                    redis = await create_pool(RedisSettings.from_dsn(settings.redis_url))
                    await redis.enqueue_job(
                        "process_nonfiction_synthesis",
                        str(synthesis_job.id), manuscript_id,
                    )
                except Exception as enqueue_err:
                    logger.error(f"Failed to enqueue synthesis for manuscript {manuscript_id}: {enqueue_err}")
                    await session.rollback()
                    async with session_factory() as err_session:
                        ms_result = await err_session.execute(select(Manuscript).where(Manuscript.id == ms_uuid))
                        ms = ms_result.scalar_one()
                        ms.status = ManuscriptStatus.error
                        await err_session.commit()
                    return

                await session.commit()
                logger.info(f"All sections analyzed for manuscript {manuscript_id}, synthesis enqueued")

        except NonfictionAnalysisError as e:
            logger.error(f"Section analysis error for section {chapter_id}: {e}")
            await _fail_job_with_retry(
                session_factory, job_uuid, ms_uuid,
                str(e), "Section analysis failed", "process_nonfiction_section_analysis",
                chapter_uuid=ch_uuid,
            )
        except Exception as e:
            logger.exception(f"Unexpected error in section analysis for {chapter_id}")
            await _fail_job_with_retry(
                session_factory, job_uuid, ms_uuid,
                "An unexpected error occurred while analyzing your section. Please try again.",
                "Section analysis failed", "process_nonfiction_section_analysis",
                chapter_uuid=ch_uuid,
            )


async def process_nonfiction_synthesis(ctx, job_id: str, manuscript_id: str):
    """Generate document-level synthesis after all sections are analyzed.

    Combines all section analysis results into a cohesive document summary.
    Saves to NonfictionDocumentSummary table and marks manuscript as complete.
    """
    session_factory = _get_session_factory()
    job_uuid = uuid.UUID(job_id)
    ms_uuid = uuid.UUID(manuscript_id)

    async with session_factory() as session:
        try:
            # Guard: skip if job row doesn't exist (orphaned Redis job)
            guard_result = await session.execute(select(Job).where(Job.id == job_uuid))
            if guard_result.scalar_one_or_none() is None:
                logger.warning(f"Job {job_id} not found in database — orphaned Redis job, skipping")
                return

            await _update_job(
                session, job_uuid,
                status=JobStatus.running,
                started_at=datetime.now(timezone.utc),
                current_step="Generating document synthesis",
                progress_pct=10,
                attempts=Job.attempts + 1,
            )

            # Get manuscript
            result = await session.execute(select(Manuscript).where(Manuscript.id == ms_uuid))
            manuscript = result.scalar_one()

            # Get argument map
            arg_map_result = await session.execute(
                select(ArgumentMap).where(ArgumentMap.manuscript_id == ms_uuid)
            )
            arg_map_row = arg_map_result.scalar_one_or_none()
            argument_map_json = arg_map_row.argument_map_json if arg_map_row else None

            # Get all section results
            chapters_result = await session.execute(
                select(Chapter)
                .where(Chapter.manuscript_id == ms_uuid)
                .order_by(Chapter.chapter_number)
            )
            chapters = chapters_result.scalars().all()

            section_results = []
            for ch in chapters:
                sr_result = await session.execute(
                    select(NonfictionSectionResult)
                    .where(NonfictionSectionResult.chapter_id == ch.id)
                )
                for sr in sr_result.scalars().all():
                    section_results.append(sr.section_results_json)

            await _update_job(
                session, job_uuid,
                current_step="Generating document summary",
                progress_pct=40,
            )

            # Call LLM
            nf_format = manuscript.nonfiction_format.value if manuscript.nonfiction_format else None
            synthesis_result, warnings = await generate_document_synthesis(
                argument_map_json=argument_map_json,
                section_results=section_results,
                nonfiction_format=nf_format,
            )

            synthesis_dict = synthesis_result.model_dump()

            await _update_job(
                session, job_uuid,
                current_step="Saving document summary",
                progress_pct=80,
            )

            # Save or update document summary
            existing_summary_result = await session.execute(
                select(NonfictionDocumentSummary).where(
                    NonfictionDocumentSummary.manuscript_id == ms_uuid
                )
            )
            existing_summary = existing_summary_result.scalar_one_or_none()

            if existing_summary is None:
                doc_summary = NonfictionDocumentSummary(
                    manuscript_id=ms_uuid,
                    summary_json=synthesis_dict,
                )
                session.add(doc_summary)
            else:
                existing_summary.summary_json = synthesis_dict

            # Mark manuscript as complete
            manuscript.status = ManuscriptStatus.complete
            await session.commit()

            for w in warnings:
                logger.warning(f"Synthesis warning (manuscript {manuscript_id}): {w}")

            await _update_job(
                session, job_uuid,
                status=JobStatus.completed,
                completed_at=datetime.now(timezone.utc),
                current_step="Document synthesis complete",
                progress_pct=100,
            )

            logger.info(f"Nonfiction synthesis complete for manuscript {manuscript_id}")

        except SynthesisError as e:
            logger.error(f"Synthesis error for manuscript {manuscript_id}: {e}")
            await _fail_job_with_retry(
                session_factory, job_uuid, ms_uuid,
                str(e), "Synthesis failed", "process_nonfiction_synthesis",
            )
        except Exception as e:
            logger.exception(f"Unexpected error in synthesis for {manuscript_id}")
            await _fail_job_with_retry(
                session_factory, job_uuid, ms_uuid,
                "An unexpected error occurred while generating your document summary. Please try again.",
                "Synthesis failed", "process_nonfiction_synthesis",
            )


async def _purge_deleted_data(ctx):
    """Hard-delete data for users soft-deleted more than 30 days ago (SEC-008).

    GDPR compliance: after the 30-day grace period, permanently remove all
    user data including manuscripts, analyses, jobs, and email events.
    Deletion order respects foreign key constraints.
    """
    session_factory = _get_session_factory()
    cutoff = datetime.now(timezone.utc) - timedelta(days=30)

    async with session_factory() as session:
        # Find users eligible for hard deletion
        result = await session.execute(
            select(User).where(
                User.deleted_at.isnot(None),
                User.deleted_at < cutoff,
            )
        )
        users = result.scalars().all()

        if not users:
            return

        for user in users:
            user_id = user.id
            logger.info(f"Hard-purging data for user {user_id} (deleted_at={user.deleted_at})")

            # Get all manuscript IDs for this user
            ms_result = await session.execute(
                select(Manuscript.id).where(Manuscript.user_id == user_id)
            )
            manuscript_ids = [row[0] for row in ms_result.all()]

            if manuscript_ids:
                # Get all chapter IDs for these manuscripts
                ch_result = await session.execute(
                    select(Chapter.id).where(Chapter.manuscript_id.in_(manuscript_ids))
                )
                chapter_ids = [row[0] for row in ch_result.all()]

                if chapter_ids:
                    # Delete nonfiction section results (FK -> chapters)
                    await session.execute(
                        delete(NonfictionSectionResult).where(
                            NonfictionSectionResult.chapter_id.in_(chapter_ids)
                        )
                    )
                    # Delete chapter analyses (FK -> chapters)
                    await session.execute(
                        delete(ChapterAnalysis).where(
                            ChapterAnalysis.chapter_id.in_(chapter_ids)
                        )
                    )

                # Get story bible IDs for version cleanup
                sb_result = await session.execute(
                    select(StoryBible.id).where(StoryBible.manuscript_id.in_(manuscript_ids))
                )
                story_bible_ids = [row[0] for row in sb_result.all()]

                if story_bible_ids:
                    # Delete story bible versions (FK -> story_bibles)
                    await session.execute(
                        delete(StoryBibleVersion).where(
                            StoryBibleVersion.story_bible_id.in_(story_bible_ids)
                        )
                    )

                # Delete story bibles (FK -> manuscripts)
                await session.execute(
                    delete(StoryBible).where(StoryBible.manuscript_id.in_(manuscript_ids))
                )

                # Delete argument maps (FK -> manuscripts)
                await session.execute(
                    delete(ArgumentMap).where(ArgumentMap.manuscript_id.in_(manuscript_ids))
                )

                # Delete nonfiction document summaries (FK -> manuscripts)
                await session.execute(
                    delete(NonfictionDocumentSummary).where(
                        NonfictionDocumentSummary.manuscript_id.in_(manuscript_ids)
                    )
                )

                # Delete chapters (FK -> manuscripts)
                await session.execute(
                    delete(Chapter).where(Chapter.manuscript_id.in_(manuscript_ids))
                )

                # Delete jobs (FK -> manuscripts)
                await session.execute(
                    delete(Job).where(Job.manuscript_id.in_(manuscript_ids))
                )

                # Delete manuscripts
                await session.execute(
                    delete(Manuscript).where(Manuscript.user_id == user_id)
                )

            # Delete email events (FK -> users)
            await session.execute(
                delete(EmailEvent).where(EmailEvent.user_id == user_id)
            )

            # Delete the user
            await session.delete(user)

            logger.info(
                f"Hard-purged user {user_id}: "
                f"{len(manuscript_ids)} manuscripts removed"
            )

        await session.commit()
        logger.info(f"Hard-purge complete: {len(users)} users permanently deleted")


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
    functions = [
        process_text_extraction,
        process_bible_generation,
        process_chapter_analysis,
        process_argument_map_generation,
        process_nonfiction_section_analysis,
        process_nonfiction_synthesis,
    ]
    cron_jobs = [
        # Run drip email dispatch every hour at :00
        cron(process_drip_emails, minute=0),
        # Run GDPR hard purge daily at 03:00 UTC (SEC-008)
        cron(_purge_deleted_data, hour=3, minute=0),
    ]
    on_startup = _recover_stalled_jobs
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
    max_jobs = 5
    job_timeout = settings.arq_job_timeout  # Configurable via ARQ_JOB_TIMEOUT env var (default 3600s)
