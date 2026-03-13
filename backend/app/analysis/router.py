import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth.dependencies import get_current_user, get_current_user_allow_provisional
from app.db.models import Chapter, ChapterAnalysis, Manuscript, StoryBible, User
from app.db.session import get_db

router = APIRouter(prefix="/bible", tags=["story-bible"])


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

    return {
        "manuscript_id": str(manuscript_id),
        "version": bible.version,
        "bible": bible.bible_json,
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

        chapter_feedback.append({
            "chapter_id": str(ch.id),
            "chapter_number": ch.chapter_number,
            "title": ch.title,
            "word_count": ch.word_count,
            "status": ch.status.value,
            "issues": issues,
            "issue_counts": {
                "critical": sum(1 for i in issues if i.get("severity") == "critical"),
                "warning": sum(1 for i in issues if i.get("severity") == "warning"),
                "note": sum(1 for i in issues if i.get("severity") == "note"),
            },
            "pacing": pacing,
            "genre_notes": genre_notes,
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
