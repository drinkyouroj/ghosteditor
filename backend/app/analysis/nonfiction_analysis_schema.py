"""Pydantic models for nonfiction section analysis and document synthesis validation.

Follows the same pattern as bible_schema.py and issue_schema.py: strict schema
validation of LLM JSON output with sensible defaults and null coercion.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


# --- Severity and dimension ordering ---

_SEVERITY_ORDER = {"critical": 0, "warning": 1, "note": 2}

VALID_DIMENSIONS = {"argument", "evidence", "clarity", "structure", "tone"}
VALID_SEVERITIES = {"critical", "warning", "note"}

MAX_ISSUES_PER_SECTION = 15


# --- Section Analysis Models ---


class NonfictionIssue(BaseModel):
    dimension: str = "clarity"  # argument, evidence, clarity, structure, tone
    severity: str = "note"  # critical, warning, note
    location: str = ""
    description: str = ""
    suggestion: str | None = None

    @field_validator("suggestion", mode="before")
    @classmethod
    def coerce_suggestion_null(cls, v):
        if v is not None and isinstance(v, str) and not v.strip():
            return None
        return v


class ArgumentMapUpdate(BaseModel):
    new_threads: list[dict] = Field(default_factory=list)
    thread_status_changes: list[dict] = Field(default_factory=list)
    new_evidence: list[dict] = Field(default_factory=list)

    @field_validator("new_threads", mode="before")
    @classmethod
    def coerce_new_threads_null(cls, v):
        return v if v is not None else []

    @field_validator("thread_status_changes", mode="before")
    @classmethod
    def coerce_thread_status_changes_null(cls, v):
        return v if v is not None else []

    @field_validator("new_evidence", mode="before")
    @classmethod
    def coerce_new_evidence_null(cls, v):
        return v if v is not None else []


class SectionAnalysisResult(BaseModel):
    section_number: int = 0
    section_title: str | None = None
    word_count: int = 0
    issues: list[NonfictionIssue] = Field(default_factory=list)
    section_summary: str = ""
    argument_map_updates: ArgumentMapUpdate = Field(default_factory=ArgumentMapUpdate)
    evidence_capped: bool = False

    @field_validator("issues", mode="before")
    @classmethod
    def coerce_issues_null(cls, v):
        return v if v is not None else []

    @field_validator("argument_map_updates", mode="before")
    @classmethod
    def coerce_argument_map_updates_null(cls, v):
        return v if v is not None else {}

    @field_validator("section_title", mode="before")
    @classmethod
    def coerce_section_title(cls, v):
        if v is not None and isinstance(v, str) and not v.strip():
            return None
        return v


# --- Document Synthesis Models ---


class DocumentSynthesis(BaseModel):
    overall_assessment: str = ""
    thesis_clarity_score: str = "developing"  # weak, developing, clear, strong
    argument_coherence: str = "mostly_coherent"  # fragmented, inconsistent, mostly_coherent, coherent
    evidence_density: str = "adequate"  # sparse, uneven, adequate, strong
    tone_consistency: str = "mostly_consistent"  # inconsistent, mostly_consistent, consistent
    top_strengths: list[str] = Field(default_factory=list)
    top_priorities: list[str] = Field(default_factory=list)
    format_specific_notes: str = ""

    @field_validator("top_strengths", mode="before")
    @classmethod
    def coerce_top_strengths_null(cls, v):
        return v if v is not None else []

    @field_validator("top_priorities", mode="before")
    @classmethod
    def coerce_top_priorities_null(cls, v):
        return v if v is not None else []


# --- Post-validation filtering ---


def validate_and_filter_section(result: SectionAnalysisResult) -> SectionAnalysisResult:
    """Post-validation filtering of section analysis results.

    - Filters out issues with empty descriptions
    - Normalizes dimension and severity to valid values
    - Caps issues at MAX_ISSUES_PER_SECTION, keeping highest severity first
    """
    filtered_issues = []
    for issue in result.issues:
        # Skip empty descriptions
        if not issue.description or not issue.description.strip():
            continue

        # Normalize dimension
        if issue.dimension not in VALID_DIMENSIONS:
            issue.dimension = "clarity"

        # Normalize severity
        if issue.severity not in VALID_SEVERITIES:
            issue.severity = "note"

        filtered_issues.append(issue)

    # Sort by severity (critical first) then cap
    filtered_issues.sort(key=lambda i: _SEVERITY_ORDER.get(i.severity, 2))
    result.issues = filtered_issues[:MAX_ISSUES_PER_SECTION]

    return result
