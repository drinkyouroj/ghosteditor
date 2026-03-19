from __future__ import annotations

import uuid
from datetime import timedelta
from enum import Enum as PyEnum

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.analysis.export import generate_feedback_docx, generate_feedback_pdf
from app.auth.dependencies import get_current_user, get_current_user_allow_provisional
from app.db.models import (
    Chapter,
    ChapterAnalysis,
    DocumentType,
    Manuscript,
    NonfictionDocumentSummary,
    NonfictionSectionResult,
    StoryBible,
    User,
)
from app.db.session import get_db
from app.rate_limit import check_rate_limit

router = APIRouter(prefix="/bible", tags=["story-bible"])

# Rate limit for export: 10 per hour per DECISION-010
EXPORT_RATE_LIMIT = 10
EXPORT_RATE_WINDOW = timedelta(hours=1)


class ExportFormat(str, PyEnum):
    pdf = "pdf"
    docx = "docx"


@router.get("/{manuscript_id}")
async def get_story_bible(
    manuscript_id: uuid.UUID,
    user: User = Depends(get_current_user_allow_provisional),
    db: AsyncSession = Depends(get_db),
):
    """Get the story bible for a manuscript. Scoped to current user.
    Provisional users can access this (free bible preview).
    """
    # Verify manuscript ownership
    ms_result = await db.execute(
        select(Manuscript).where(
            Manuscript.id == manuscript_id,
            Manuscript.user_id == user.id,
            Manuscript.deleted_at.is_(None),
        )
    )
    manuscript = ms_result.scalar_one_or_none()
    if manuscript is None:
        raise HTTPException(status_code=404, detail="Manuscript not found")

    bible_result = await db.execute(
        select(StoryBible).where(StoryBible.manuscript_id == manuscript_id)
    )
    bible = bible_result.scalar_one_or_none()
    if bible is None:
        raise HTTPException(status_code=404, detail="Story bible not yet generated")

    bible_data = bible.bible_json or {}
    warnings = bible_data.get("_warnings", []) if isinstance(bible_data, dict) else []
    # Return bible without the internal _warnings key
    clean_bible = {k: v for k, v in bible_data.items() if k != "_warnings"} if isinstance(bible_data, dict) else bible_data

    return {
        "manuscript_id": str(manuscript_id),
        "version": bible.version,
        "bible": clean_bible,
        "warnings": warnings,
        "updated_at": bible.updated_at.isoformat(),
    }


@router.get("/{manuscript_id}/feedback")
async def get_manuscript_feedback(
    manuscript_id: uuid.UUID,
    severity: str | None = Query(default=None, description="Filter by severity: critical, warning, note"),
    issue_type: str | None = Query(default=None, description="Filter by type: consistency, pacing, etc."),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get all chapter analysis feedback for a manuscript.

    Returns chapters with their analysis results (issues, pacing, genre notes).
    Scoped to current user. Requires full auth (not provisional).
    """
    # Verify manuscript ownership
    ms_result = await db.execute(
        select(Manuscript).where(
            Manuscript.id == manuscript_id,
            Manuscript.user_id == user.id,
            Manuscript.deleted_at.is_(None),
        )
    )
    manuscript = ms_result.scalar_one_or_none()
    if manuscript is None:
        raise HTTPException(status_code=404, detail="Manuscript not found")

    # Load chapters with their analyses
    chapters_result = await db.execute(
        select(Chapter)
        .options(selectinload(Chapter.analyses))
        .where(Chapter.manuscript_id == manuscript_id)
        .order_by(Chapter.chapter_number)
    )
    chapters = chapters_result.scalars().all()

    # Build response
    severity_order = {"critical": 0, "warning": 1, "note": 2}
    chapter_feedback = []

    for ch in chapters:
        # Get the latest analysis for this chapter
        analysis = None
        if ch.analyses:
            analysis = max(ch.analyses, key=lambda a: a.created_at)

        issues = []
        pacing = None
        genre_notes = None

        if analysis:
            raw_issues = analysis.issues_json if isinstance(analysis.issues_json, list) else analysis.issues_json.get("issues", [])

            # Apply filters
            for issue in raw_issues:
                if severity and issue.get("severity") != severity:
                    continue
                if issue_type and issue.get("type") != issue_type:
                    continue
                issues.append(issue)

            # Sort by severity
            issues.sort(key=lambda i: severity_order.get(i.get("severity", "note"), 2))

            pacing = analysis.pacing_json
            genre_notes = analysis.genre_notes

        # Check for issues_capped flag or skip_reason in analysis data
        issues_capped = False
        skip_reason = None
        if analysis:
            # issues_capped stored in issues_json if it's a dict
            if isinstance(analysis.issues_json, dict):
                issues_capped = analysis.issues_json.get("issues_capped", False)
                skip_reason = analysis.issues_json.get("skip_reason")

        chapter_feedback.append({
            "chapter_id": str(ch.id),
            "chapter_number": ch.chapter_number,
            "title": ch.title,
            "word_count": ch.word_count,
            "status": ch.status.value,
            "issues": issues,
            "issues_capped": issues_capped,
            "issue_counts": {
                "critical": sum(1 for i in issues if i.get("severity") == "critical"),
                "warning": sum(1 for i in issues if i.get("severity") == "warning"),
                "note": sum(1 for i in issues if i.get("severity") == "note"),
            },
            "pacing": pacing,
            "genre_notes": genre_notes,
            **({"skip_reason": skip_reason} if skip_reason else {}),
        })

    # Summary counts across all chapters
    total_issues = sum(len(ch["issues"]) for ch in chapter_feedback)
    total_critical = sum(ch["issue_counts"]["critical"] for ch in chapter_feedback)
    total_warning = sum(ch["issue_counts"]["warning"] for ch in chapter_feedback)
    total_note = sum(ch["issue_counts"]["note"] for ch in chapter_feedback)
    analyzed_count = sum(1 for ch in chapter_feedback if ch["status"] == "analyzed")

    return {
        "manuscript_id": str(manuscript_id),
        "title": manuscript.title,
        "genre": manuscript.genre,
        "status": manuscript.status.value,
        "summary": {
            "total_issues": total_issues,
            "critical": total_critical,
            "warning": total_warning,
            "note": total_note,
            "chapters_analyzed": analyzed_count,
            "chapters_total": len(chapter_feedback),
        },
        "chapters": chapter_feedback,
    }


@router.get("/{manuscript_id}/feedback/export")
async def export_manuscript_feedback(
    manuscript_id: uuid.UUID,
    format: ExportFormat = Query(
        default=ExportFormat.pdf,
        description="Export format: pdf or docx",
    ),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Export manuscript feedback as a PDF or DOCX file.

    Per DECISION-010: on-demand generation, rate-limited to 10/hour,
    no internal IDs in output. Supports both fiction and nonfiction.
    """
    # Rate limit per DECISION-010 JUDGE verdict
    await check_rate_limit(
        user_id=str(user.id),
        action="export",
        max_requests=EXPORT_RATE_LIMIT,
        window=EXPORT_RATE_WINDOW,
        user_email=user.email,
    )

    # Verify manuscript ownership
    ms_result = await db.execute(
        select(Manuscript).where(
            Manuscript.id == manuscript_id,
            Manuscript.user_id == user.id,
            Manuscript.deleted_at.is_(None),
        )
    )
    manuscript = ms_result.scalar_one_or_none()
    if manuscript is None:
        raise HTTPException(status_code=404, detail="Manuscript not found")

    is_nonfiction = manuscript.document_type == DocumentType.nonfiction

    # Load feedback data (same logic as get_manuscript_feedback / get_nonfiction_feedback)
    if is_nonfiction:
        feedback_data = await _build_nonfiction_feedback(manuscript, db)
    else:
        feedback_data = await _build_fiction_feedback(manuscript, db)

    title = manuscript.title or "Untitled"
    genre = feedback_data.get("genre")
    summary = feedback_data["summary"]
    chapters = feedback_data["chapters"]
    document_summary = feedback_data.get("document_summary")

    # Generate export file
    if format == ExportFormat.pdf:
        content = generate_feedback_pdf(title, genre, summary, chapters, document_summary)
        media_type = "application/pdf"
        extension = "pdf"
    else:
        content = generate_feedback_docx(title, genre, summary, chapters, document_summary)
        media_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        extension = "docx"

    # Sanitize filename (no internal data, just the manuscript title)
    safe_title = "".join(c for c in title if c.isalnum() or c in " _-").strip()[:80]
    if not safe_title:
        safe_title = "feedback"
    filename = f"{safe_title}_feedback.{extension}"

    return StreamingResponse(
        iter([content]),
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


async def _build_fiction_feedback(
    manuscript: Manuscript,
    db: AsyncSession,
) -> dict:
    """Build fiction feedback data dict for export (no internal IDs)."""
    chapters_result = await db.execute(
        select(Chapter)
        .options(selectinload(Chapter.analyses))
        .where(Chapter.manuscript_id == manuscript.id)
        .order_by(Chapter.chapter_number)
    )
    chapters = chapters_result.scalars().all()

    severity_order = {"critical": 0, "warning": 1, "note": 2}
    chapter_feedback = []

    for ch in chapters:
        analysis = None
        if ch.analyses:
            analysis = max(ch.analyses, key=lambda a: a.created_at)

        issues = []
        if analysis:
            raw_issues = (
                analysis.issues_json
                if isinstance(analysis.issues_json, list)
                else analysis.issues_json.get("issues", [])
            )
            for issue in raw_issues:
                issues.append(issue)
            issues.sort(key=lambda i: severity_order.get(i.get("severity", "note"), 2))

        chapter_feedback.append({
            "chapter_number": ch.chapter_number,
            "title": ch.title,
            "word_count": ch.word_count,
            "status": ch.status.value,
            "issues": issues,
        })

    total_issues = sum(len(ch["issues"]) for ch in chapter_feedback)
    analyzed_count = sum(1 for ch in chapter_feedback if ch["status"] == "analyzed")

    return {
        "genre": manuscript.genre,
        "summary": {
            "total_issues": total_issues,
            "critical": sum(
                1 for ch in chapter_feedback for i in ch["issues"] if i.get("severity") == "critical"
            ),
            "warning": sum(
                1 for ch in chapter_feedback for i in ch["issues"] if i.get("severity") == "warning"
            ),
            "note": sum(
                1 for ch in chapter_feedback for i in ch["issues"] if i.get("severity") == "note"
            ),
            "chapters_analyzed": analyzed_count,
            "chapters_total": len(chapter_feedback),
        },
        "chapters": chapter_feedback,
    }


async def _build_nonfiction_feedback(
    manuscript: Manuscript,
    db: AsyncSession,
) -> dict:
    """Build nonfiction feedback data dict for export (no internal IDs)."""
    chapters_result = await db.execute(
        select(Chapter)
        .options(selectinload(Chapter.nonfiction_results))
        .where(Chapter.manuscript_id == manuscript.id)
        .order_by(Chapter.chapter_number)
    )
    chapters = chapters_result.scalars().all()

    # Document summary
    summary_result = await db.execute(
        select(NonfictionDocumentSummary).where(
            NonfictionDocumentSummary.manuscript_id == manuscript.id
        )
    )
    doc_summary_row = summary_result.scalar_one_or_none()
    document_summary = doc_summary_row.summary_json if doc_summary_row else None

    severity_order = {"critical": 0, "warning": 1, "note": 2}
    section_feedback = []

    for ch in chapters:
        issues = []
        if ch.nonfiction_results:
            for nf_result in ch.nonfiction_results:
                result_json = nf_result.section_results_json or {}
                raw_issues = result_json.get("issues", [])
                issues.extend(raw_issues)
            issues.sort(key=lambda i: severity_order.get(i.get("severity", "note"), 2))

        section_feedback.append({
            "chapter_number": ch.chapter_number,
            "title": ch.title,
            "word_count": ch.word_count,
            "status": ch.status.value,
            "issues": issues,
        })

    total_issues = sum(len(s["issues"]) for s in section_feedback)
    analyzed_count = sum(1 for s in section_feedback if s["status"] == "analyzed")

    nf_format = manuscript.nonfiction_format.value if manuscript.nonfiction_format else None

    return {
        "genre": nf_format,
        "summary": {
            "total_issues": total_issues,
            "critical": sum(
                1 for s in section_feedback for i in s["issues"] if i.get("severity") == "critical"
            ),
            "warning": sum(
                1 for s in section_feedback for i in s["issues"] if i.get("severity") == "warning"
            ),
            "note": sum(
                1 for s in section_feedback for i in s["issues"] if i.get("severity") == "note"
            ),
            "chapters_analyzed": analyzed_count,
            "chapters_total": len(section_feedback),
        },
        "chapters": section_feedback,
        "document_summary": document_summary,
    }
