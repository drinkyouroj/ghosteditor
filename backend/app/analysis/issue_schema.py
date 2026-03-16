"""Pydantic models for chapter analysis validation.

Follows the same pattern as bible_schema.py: strict schema validation
of Claude's JSON output with sensible defaults for optional fields.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class Issue(BaseModel):
    type: str = ""  # consistency, pacing, character, plot, voice, worldbuilding, genre_convention
    severity: str = "note"  # critical, warning, note
    chapter_location: str = "middle"  # beginning, middle, end
    description: str = ""
    original_text: str | None = None
    suggestion: str = ""


class PacingAnalysis(BaseModel):
    scene_count: int = 0
    scene_types: list[str] = Field(default_factory=list)
    tension_arc: str = "mixed"  # rising, falling, flat, mixed
    characters_present: list[str] = Field(default_factory=list)
    chapter_summary: str = ""


class GenreNotes(BaseModel):
    conventions_met: list[str] = Field(default_factory=list)
    conventions_missed: list[str] = Field(default_factory=list)
    genre_fit_score: str = "moderate"  # strong, moderate, weak


class ChapterAnalysisResult(BaseModel):
    issues: list[Issue] = Field(default_factory=list)
    pacing: PacingAnalysis = Field(default_factory=PacingAnalysis)
    genre_notes: GenreNotes = Field(default_factory=GenreNotes)


# --- Severity ordering for sorting ---

_SEVERITY_ORDER = {"critical": 0, "warning": 1, "note": 2}

MAX_ISSUES_PER_CHAPTER = 15


def validate_and_filter(result: ChapterAnalysisResult) -> ChapterAnalysisResult:
    """Post-validation filtering of chapter analysis results.

    - Filters out issues with empty descriptions
    - Caps issues at MAX_ISSUES_PER_CHAPTER, keeping highest severity first
    - Normalizes severity and type values to valid enums
    """
    valid_types = {
        "consistency", "pacing", "character", "plot",
        "voice", "worldbuilding", "genre_convention",
    }
    valid_severities = {"critical", "warning", "note"}
    valid_locations = {"beginning", "middle", "end"}

    filtered_issues = []
    for issue in result.issues:
        # Skip empty descriptions
        if not issue.description or not issue.description.strip():
            continue

        # Normalize type
        if issue.type not in valid_types:
            issue.type = "note"

        # Normalize severity
        if issue.severity not in valid_severities:
            issue.severity = "note"

        # Normalize location
        if issue.chapter_location not in valid_locations:
            issue.chapter_location = "middle"

        filtered_issues.append(issue)

    # Sort by severity (critical first) then cap
    filtered_issues.sort(key=lambda i: _SEVERITY_ORDER.get(i.severity, 2))
    result.issues = filtered_issues[:MAX_ISSUES_PER_CHAPTER]

    return result
