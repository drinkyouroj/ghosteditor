from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth.dependencies import get_current_user, get_current_user_allow_provisional
from app.db.models import (
    ArgumentMap,
    Chapter,
    Manuscript,
    NonfictionDocumentSummary,
    NonfictionSectionResult,
    User,
)
from app.db.session import get_db

router = APIRouter(prefix="/argument-map", tags=["argument-map"])


@router.get("/{manuscript_id}")
async def get_argument_map(
    manuscript_id: uuid.UUID,
    user: User = Depends(get_current_user_allow_provisional),
    db: AsyncSession = Depends(get_db),
):
    """Get the argument map for a nonfiction manuscript. Scoped to current user.
    Provisional users can access this (free preview).
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

    arg_map_result = await db.execute(
        select(ArgumentMap).where(ArgumentMap.manuscript_id == manuscript_id)
    )
    arg_map = arg_map_result.scalar_one_or_none()
    if arg_map is None:
        raise HTTPException(status_code=404, detail="Argument map not yet generated")

    arg_map_data = arg_map.argument_map_json or {}
    warnings = arg_map_data.get("_warnings", []) if isinstance(arg_map_data, dict) else []
    # Return argument map without the internal _warnings key
    clean_map = {k: v for k, v in arg_map_data.items() if k != "_warnings"} if isinstance(arg_map_data, dict) else arg_map_data

    return {
        "manuscript_id": str(manuscript_id),
        "version": arg_map.version,
        "argument_map": clean_map,
        "warnings": warnings,
        "updated_at": arg_map.updated_at.isoformat(),
    }


@router.get("/{manuscript_id}/feedback")
async def get_nonfiction_feedback(
    manuscript_id: uuid.UUID,
    dimension: str | None = Query(
        default=None,
        description="Filter by dimension: argument, evidence, clarity, structure, tone",
    ),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get all section analysis feedback for a nonfiction manuscript.

    Returns sections with their analysis results and document summary.
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

    # Load chapters with their nonfiction results
    chapters_result = await db.execute(
        select(Chapter)
        .options(selectinload(Chapter.nonfiction_results))
        .where(Chapter.manuscript_id == manuscript_id)
        .order_by(Chapter.chapter_number)
    )
    chapters = chapters_result.scalars().all()

    # Get document summary if available
    summary_result = await db.execute(
        select(NonfictionDocumentSummary).where(
            NonfictionDocumentSummary.manuscript_id == manuscript_id
        )
    )
    doc_summary_row = summary_result.scalar_one_or_none()
    document_summary = doc_summary_row.summary_json if doc_summary_row else None

    # Build response
    severity_order = {"critical": 0, "warning": 1, "note": 2}
    section_feedback = []

    for ch in chapters:
        # Get the latest nonfiction result for this section
        nf_result = None
        if ch.nonfiction_results:
            # Filter by dimension if specified
            results = ch.nonfiction_results
            if dimension:
                results = [r for r in results if r.dimension.value == dimension]
            if results:
                nf_result = max(results, key=lambda r: r.created_at)

        issues = []
        section_data = {}

        if nf_result:
            result_json = nf_result.section_results_json or {}
            raw_issues = result_json.get("issues", [])

            # Sort by severity
            for issue in raw_issues:
                issues.append(issue)
            issues.sort(key=lambda i: severity_order.get(i.get("severity", "note"), 2))

            section_data = {
                "evidence_assessment": result_json.get("evidence_assessment"),
                "argument_coherence": result_json.get("argument_coherence"),
                "clarity_score": result_json.get("clarity_score"),
                "structure_notes": result_json.get("structure_notes"),
                "tone_analysis": result_json.get("tone_analysis"),
            }

        section_feedback.append({
            "section_id": str(ch.id),
            "section_number": ch.chapter_number,
            "title": ch.title,
            "word_count": ch.word_count,
            "status": ch.status.value,
            "section_detection_method": nf_result.section_detection_method.value if nf_result else None,
            "dimension": nf_result.dimension.value if nf_result else None,
            "issues": issues,
            "issue_counts": {
                "critical": sum(1 for i in issues if i.get("severity") == "critical"),
                "warning": sum(1 for i in issues if i.get("severity") == "warning"),
                "note": sum(1 for i in issues if i.get("severity") == "note"),
            },
            **section_data,
        })

    # Summary counts across all sections
    total_issues = sum(len(s["issues"]) for s in section_feedback)
    total_critical = sum(s["issue_counts"]["critical"] for s in section_feedback)
    total_warning = sum(s["issue_counts"]["warning"] for s in section_feedback)
    total_note = sum(s["issue_counts"]["note"] for s in section_feedback)
    analyzed_count = sum(1 for s in section_feedback if s["status"] == "analyzed")

    return {
        "manuscript_id": str(manuscript_id),
        "title": manuscript.title,
        "document_type": manuscript.document_type.value,
        "nonfiction_format": manuscript.nonfiction_format.value if manuscript.nonfiction_format else None,
        "status": manuscript.status.value,
        "document_summary": document_summary,
        "summary": {
            "total_issues": total_issues,
            "critical": total_critical,
            "warning": total_warning,
            "note": total_note,
            "sections_analyzed": analyzed_count,
            "sections_total": len(section_feedback),
        },
        "sections": section_feedback,
    }
