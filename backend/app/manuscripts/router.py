import uuid
from datetime import datetime, timezone

from arq import create_pool
from arq.connections import RedisSettings
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user, get_current_user_allow_provisional
from app.config import settings
from app.db.models import Chapter, Job, JobType, Manuscript, ManuscriptStatus, User
from app.db.session import get_db
from app.manuscripts.schemas import (
    ChapterSummary,
    JobResponse,
    ManuscriptDetailResponse,
    ManuscriptResponse,
    UploadResponse,
)
from app.manuscripts.s3 import upload_to_s3
from app.manuscripts.validation import validate_file

router = APIRouter(prefix="/manuscripts", tags=["manuscripts"])


@router.post("/upload", response_model=UploadResponse, status_code=201)
async def upload_manuscript(
    file: UploadFile = File(...),
    title: str = Form(...),
    genre: str | None = Form(default=None),
    user: User = Depends(get_current_user_allow_provisional),
    db: AsyncSession = Depends(get_db),
):
    """Upload a manuscript file for analysis.

    Provisional users can upload (free Chapter 1 / story bible preview).
    Validates file, stores in S3, creates manuscript row, enqueues extraction job.
    """
    content, ext = await validate_file(file)

    manuscript_id = uuid.uuid4()
    s3_key = f"manuscripts/{user.id}/{manuscript_id}/original{ext}"

    # Upload to S3
    try:
        upload_to_s3(content, s3_key)
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to store file. Please try again.")

    # Create manuscript row
    manuscript = Manuscript(
        id=manuscript_id,
        user_id=user.id,
        title=title,
        genre=genre,
        s3_key=s3_key,
        status=ManuscriptStatus.uploading,
    )
    db.add(manuscript)

    # Create extraction job
    job = Job(
        manuscript_id=manuscript_id,
        job_type=JobType.text_extraction,
        current_step="Queued for text extraction",
    )
    db.add(job)

    await db.commit()
    await db.refresh(job)

    # Enqueue extraction job in arq
    redis = await create_pool(RedisSettings.from_dsn(settings.redis_url))
    await redis.enqueue_job("process_text_extraction", str(job.id), str(manuscript_id))

    return UploadResponse(manuscript_id=manuscript_id, status="uploading", job_id=job.id)


@router.get("", response_model=list[ManuscriptResponse])
async def list_manuscripts(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List all manuscripts for the current user."""
    result = await db.execute(
        select(Manuscript)
        .where(Manuscript.user_id == user.id, Manuscript.deleted_at.is_(None))
        .order_by(Manuscript.created_at.desc())
    )
    manuscripts = result.scalars().all()
    return [ManuscriptResponse.model_validate(m) for m in manuscripts]


@router.get("/{manuscript_id}", response_model=ManuscriptDetailResponse)
async def get_manuscript(
    manuscript_id: uuid.UUID,
    user: User = Depends(get_current_user_allow_provisional),
    db: AsyncSession = Depends(get_db),
):
    """Get manuscript details with chapter list. Scoped to current user."""
    result = await db.execute(
        select(Manuscript).where(
            Manuscript.id == manuscript_id,
            Manuscript.user_id == user.id,
            Manuscript.deleted_at.is_(None),
        )
    )
    manuscript = result.scalar_one_or_none()
    if manuscript is None:
        raise HTTPException(status_code=404, detail="Manuscript not found")

    chapters_result = await db.execute(
        select(Chapter)
        .where(Chapter.manuscript_id == manuscript_id)
        .order_by(Chapter.chapter_number)
    )
    chapters = chapters_result.scalars().all()

    return ManuscriptDetailResponse(
        id=manuscript.id,
        title=manuscript.title,
        genre=manuscript.genre,
        status=manuscript.status.value,
        payment_status=manuscript.payment_status.value,
        chapter_count=manuscript.chapter_count,
        word_count_est=manuscript.word_count_est,
        created_at=manuscript.created_at,
        chapters=[ChapterSummary.model_validate(c) for c in chapters],
    )


@router.delete("/{manuscript_id}", status_code=204)
async def delete_manuscript(
    manuscript_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Soft-delete a manuscript. Purge job handles S3 and child row cleanup."""
    result = await db.execute(
        select(Manuscript).where(
            Manuscript.id == manuscript_id,
            Manuscript.user_id == user.id,
            Manuscript.deleted_at.is_(None),
        )
    )
    manuscript = result.scalar_one_or_none()
    if manuscript is None:
        raise HTTPException(status_code=404, detail="Manuscript not found")

    manuscript.deleted_at = datetime.now(timezone.utc)
    await db.commit()


@router.get("/jobs/{job_id}", response_model=JobResponse)
async def get_job_status(
    job_id: uuid.UUID,
    user: User = Depends(get_current_user_allow_provisional),
    db: AsyncSession = Depends(get_db),
):
    """Get job status. Job must belong to a manuscript owned by the requesting user."""
    result = await db.execute(
        select(Job)
        .join(Manuscript, Job.manuscript_id == Manuscript.id)
        .where(
            Job.id == job_id,
            Manuscript.user_id == user.id,
            Manuscript.deleted_at.is_(None),
        )
    )
    job = result.scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    return JobResponse.model_validate(job)
