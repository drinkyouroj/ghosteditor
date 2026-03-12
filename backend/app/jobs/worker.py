"""Async job worker using arq (Redis-backed).

Processes text extraction and story bible generation jobs.
Each job updates its status in PostgreSQL for frontend polling.
"""

import json
import logging
import uuid
from datetime import datetime, timezone

from arq import create_pool
from arq.connections import RedisSettings
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.analysis.story_bible import StoryBibleError, generate_story_bible
from app.config import settings
from app.db.models import (
    Chapter,
    ChapterStatus,
    Job,
    JobStatus,
    JobType,
    Manuscript,
    ManuscriptStatus,
    StoryBible,
    StoryBibleVersion,
)
from app.manuscripts.extraction import ExtractionError, check_word_count, detect_chapters, extract_text
from app.manuscripts.s3 import download_from_s3

logger = logging.getLogger(__name__)

MAX_BIBLE_VERSIONS = 50


def _get_session_factory():
    engine = create_async_engine(settings.database_url, echo=False)
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def _update_job(session: AsyncSession, job_id: uuid.UUID, **kwargs):
    result = await session.execute(select(Job).where(Job.id == job_id))
    job = result.scalar_one()
    for key, value in kwargs.items():
        setattr(job, key, value)
    await session.commit()


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
            async with session_factory() as err_session:
                await _update_job(
                    err_session, job_uuid,
                    status=JobStatus.failed,
                    error_message=str(e),
                    current_step="Extraction failed",
                    completed_at=datetime.now(timezone.utc),
                )
                result = await err_session.execute(select(Manuscript).where(Manuscript.id == ms_uuid))
                ms = result.scalar_one()
                ms.status = ManuscriptStatus.error
                await err_session.commit()
        except Exception as e:
            logger.exception(f"Unexpected error in text extraction for {manuscript_id}")
            async with session_factory() as err_session:
                await _update_job(
                    err_session, job_uuid,
                    status=JobStatus.failed,
                    error_message=f"Internal error: {type(e).__name__}",
                    current_step="Extraction failed",
                    completed_at=datetime.now(timezone.utc),
                )
                result = await err_session.execute(select(Manuscript).where(Manuscript.id == ms_uuid))
                ms = result.scalar_one()
                ms.status = ManuscriptStatus.error
                await err_session.commit()


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

        except StoryBibleError as e:
            logger.error(f"Bible generation error for manuscript {manuscript_id}: {e}")
            async with session_factory() as err_session:
                await _update_job(
                    err_session, job_uuid,
                    status=JobStatus.failed,
                    error_message=str(e),
                    current_step="Bible generation failed",
                    completed_at=datetime.now(timezone.utc),
                )
                result = await err_session.execute(select(Manuscript).where(Manuscript.id == ms_uuid))
                ms = result.scalar_one()
                ms.status = ManuscriptStatus.error
                await err_session.commit()
        except Exception as e:
            logger.exception(f"Unexpected error in bible generation for {manuscript_id}")
            async with session_factory() as err_session:
                await _update_job(
                    err_session, job_uuid,
                    status=JobStatus.failed,
                    error_message=f"Internal error: {type(e).__name__}",
                    current_step="Bible generation failed",
                    completed_at=datetime.now(timezone.utc),
                )
                result = await err_session.execute(select(Manuscript).where(Manuscript.id == ms_uuid))
                ms = result.scalar_one()
                ms.status = ManuscriptStatus.error
                await err_session.commit()


class WorkerSettings:
    """arq worker settings."""
    functions = [process_text_extraction, process_bible_generation]
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
    max_jobs = 5
    job_timeout = 300  # 5 minutes per job
