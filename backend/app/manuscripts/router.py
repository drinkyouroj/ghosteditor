from __future__ import annotations

import uuid
from datetime import datetime, timezone

from arq import create_pool
from arq.connections import RedisSettings
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user, get_current_user_allow_provisional
from app.config import settings
from app.db.models import Chapter, ChapterStatus, DocumentType, Job, JobType, JobStatus, Manuscript, ManuscriptStatus, NonfictionFormat, PaymentStatus, SubscriptionStatus, User
from app.db.session import get_db

FREE_TIER_MANUSCRIPT_LIMIT = 3  # Per DECISION_006 Amendment 4
from app.manuscripts.schemas import (
    ChapterSummary,
    JobResponse,
    ManuscriptDetailResponse,
    ManuscriptResponse,
    UploadResponse,
)
from app.manuscripts.s3 import upload_to_s3
from app.manuscripts.validation import validate_file
from app.rate_limit import check_rate_limit

router = APIRouter(prefix="/manuscripts", tags=["manuscripts"])


@router.post("/upload", response_model=UploadResponse, status_code=201)
async def upload_manuscript(
    file: UploadFile = File(...),
    title: str = Form(...),
    genre: str | None = Form(default=None),
    document_type: str = Form(default="fiction"),
    nonfiction_format: str | None = Form(default=None),
    user: User = Depends(get_current_user_allow_provisional),
    db: AsyncSession = Depends(get_db),
):
    """Upload a manuscript file for analysis.

    Provisional users can upload (free Chapter 1 / story bible preview).
    Validates file, stores in S3, creates manuscript row, enqueues extraction job.

    Per DECISION_006 Amendment 4: free-tier users limited to 3 manuscripts.
    """
    # Per-user rate limit: 5 uploads per hour
    await check_rate_limit(str(user.id), action="upload", user_email=user.email)

    # Validate document_type
    try:
        doc_type = DocumentType(document_type)
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid document_type: {document_type}. Must be 'fiction' or 'nonfiction'.",
        )

    # Validate nonfiction_format
    nf_format = None
    if nonfiction_format is not None:
        if doc_type != DocumentType.nonfiction:
            raise HTTPException(
                status_code=422,
                detail="nonfiction_format can only be set when document_type is 'nonfiction'.",
            )
        try:
            nf_format = NonfictionFormat(nonfiction_format)
        except ValueError:
            valid = ", ".join(f.value for f in NonfictionFormat)
            raise HTTPException(
                status_code=422,
                detail=f"Invalid nonfiction_format: {nonfiction_format}. Must be one of: {valid}.",
            )

    # Free-tier upload limit
    if user.subscription_status == SubscriptionStatus.free:
        count_result = await db.execute(
            select(func.count(Manuscript.id)).where(
                Manuscript.user_id == user.id,
                Manuscript.deleted_at.is_(None),
            )
        )
        manuscript_count = count_result.scalar()
        if manuscript_count >= FREE_TIER_MANUSCRIPT_LIMIT:
            raise HTTPException(
                status_code=402,
                detail=f"Free tier allows up to {FREE_TIER_MANUSCRIPT_LIMIT} manuscripts. "
                "Upgrade to a subscription for unlimited manuscripts.",
            )

    content, ext = await validate_file(file)

    manuscript_id = uuid.uuid4()
    s3_key = f"manuscripts/{user.id}/{manuscript_id}/original{ext}"

    # Upload to S3
    try:
        upload_to_s3(content, s3_key)
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to store file. Please try again.")

    # Create manuscript row — subscribers and auto-paid users get paid status
    auto_paid = False
    if settings.auto_paid_emails:
        exempt = {e.strip().lower() for e in settings.auto_paid_emails.split(",") if e.strip()}
        auto_paid = user.email.lower() in exempt
    payment = PaymentStatus.paid if (user.subscription_status == SubscriptionStatus.subscribed or auto_paid) else PaymentStatus.unpaid
    manuscript = Manuscript(
        id=manuscript_id,
        user_id=user.id,
        title=title,
        genre=genre,
        document_type=doc_type,
        nonfiction_format=nf_format,
        s3_key=s3_key,
        status=ManuscriptStatus.uploading,
        payment_status=payment,
    )
    db.add(manuscript)

    # Create extraction job
    job = Job(
        manuscript_id=manuscript_id,
        job_type=JobType.text_extraction,
        current_step="Queued for text extraction",
    )
    db.add(job)

    # Flush to get IDs without committing
    await db.flush()
    await db.refresh(job)
    await db.refresh(manuscript)

    # Enqueue to Redis — if this fails, rollback DB
    try:
        redis = await create_pool(RedisSettings.from_dsn(settings.redis_url))
        await redis.enqueue_job("process_text_extraction", str(job.id), str(manuscript_id))
    except Exception:
        await db.rollback()
        raise HTTPException(status_code=503, detail="Job queue unavailable. Please try again.")

    # Only commit after successful enqueue
    await db.commit()

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
        document_type=manuscript.document_type.value,
        nonfiction_format=manuscript.nonfiction_format.value if manuscript.nonfiction_format else None,
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
    """Soft-delete a manuscript and clean up S3 files.

    Per GDPR requirements: files are removed from S3 immediately (best-effort).
    Database rows are soft-deleted (deleted_at timestamp) for 30-day retention.
    """
    import logging
    logger = logging.getLogger(__name__)

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

    s3_key = manuscript.s3_key
    manuscript.deleted_at = datetime.now(timezone.utc)
    await db.commit()

    # Best-effort S3 cleanup
    if s3_key:
        try:
            from app.manuscripts.s3 import delete_from_s3
            delete_from_s3(s3_key)
        except Exception as e:
            logger.warning(f"Failed to delete S3 key {s3_key}: {e}")


@router.post("/{manuscript_id}/analyze", status_code=202)
async def start_chapter_analysis(
    manuscript_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Enqueue chapter analysis jobs for all extracted chapters.

    Requires manuscript to be paid and bible_complete.
    Skips chapters that are already analyzed or currently analyzing.
    """
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

    if manuscript.payment_status != PaymentStatus.paid:
        raise HTTPException(status_code=402, detail="Payment required before analysis")

    is_nonfiction = manuscript.document_type == DocumentType.nonfiction
    bible_or_map_label = "argument map" if is_nonfiction else "story bible"

    if manuscript.status not in (ManuscriptStatus.bible_complete, ManuscriptStatus.complete, ManuscriptStatus.error, ManuscriptStatus.analyzing):
        raise HTTPException(
            status_code=409,
            detail=f"Manuscript must have a completed {bible_or_map_label} first (current: {manuscript.status.value})",
        )

    # Find chapters eligible for analysis (ordered)
    chapters_result = await db.execute(
        select(Chapter).where(
            Chapter.manuscript_id == manuscript_id,
            Chapter.status.in_([ChapterStatus.extracted]),
        ).order_by(Chapter.chapter_number)
    )
    chapters = chapters_result.scalars().all()

    section_label = "sections" if is_nonfiction else "chapters"
    if not chapters:
        raise HTTPException(status_code=409, detail=f"No {section_label} available for analysis")

    # Update manuscript status
    manuscript.status = ManuscriptStatus.analyzing
    await db.flush()

    # Only enqueue the first chapter/section — the worker chains to the next
    first_chapter = chapters[0]
    worker_func = "process_nonfiction_section_analysis" if is_nonfiction else "process_chapter_analysis"
    step_label = "Section" if is_nonfiction else "Chapter"
    job = Job(
        manuscript_id=manuscript_id,
        chapter_id=first_chapter.id,
        job_type=JobType.chapter_analysis,
        current_step=f"Queued: {step_label} {first_chapter.chapter_number}",
    )
    db.add(job)

    # Flush to get IDs without committing
    await db.flush()
    await db.refresh(job)

    # Enqueue to Redis — if this fails, rollback DB
    try:
        redis = await create_pool(RedisSettings.from_dsn(settings.redis_url))
        await redis.enqueue_job(
            worker_func, str(job.id), str(manuscript_id), str(first_chapter.id),
        )
    except Exception:
        await db.rollback()
        raise HTTPException(status_code=503, detail="Job queue unavailable. Please try again.")

    # Only commit after successful enqueue
    await db.commit()

    return {"message": f"Analysis started for {len(chapters)} {section_label}", f"{section_label}_queued": len(chapters)}


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
