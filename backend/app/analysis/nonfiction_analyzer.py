"""Nonfiction section analyzer.

Analyzes individual nonfiction sections against the argument map context.
This module will be fully implemented by Agent 2. The interface contract
is defined here so Agent 3's worker integration can import it.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from app.analysis.llm_client import call_llm
from app.analysis.json_repair import parse_json_response
from app.config import settings

logger = logging.getLogger(__name__)

PROMPT_PATH = Path(__file__).parent / "prompts" / "nonfiction_section_analysis_v1.txt"


class NonfictionAnalysisError(Exception):
    """Raised when nonfiction section analysis fails."""
    pass


class NonfictionSectionAnalysis(BaseModel):
    """Result of analyzing a single nonfiction section."""
    dimension: str = Field(default="argument", description="Primary analysis dimension")
    section_detection_method: str = Field(default="header", description="How the section was detected")
    issues: list[dict] = Field(default_factory=list, description="Issues found in this section")
    evidence_assessment: dict | None = Field(default=None, description="Assessment of evidence quality")
    argument_coherence: dict | None = Field(default=None, description="Argument coherence analysis")
    clarity_score: float | None = Field(default=None, description="Clarity score 0-1")
    structure_notes: str | None = Field(default=None, description="Notes on section structure")
    tone_analysis: dict | None = Field(default=None, description="Tone consistency analysis")


async def analyze_nonfiction_section(
    section_text: str,
    section_number: int,
    nonfiction_format: str | None = None,
    argument_map_json: dict | None = None,
) -> tuple[NonfictionSectionAnalysis, list[str]]:
    """Analyze a single nonfiction section.

    Args:
        section_text: Raw text of the section.
        section_number: Section number in the manuscript.
        nonfiction_format: Optional format hint.
        argument_map_json: The argument map context (like bible_json for fiction).

    Returns:
        Tuple of (NonfictionSectionAnalysis, list of warning strings).

    Raises:
        NonfictionAnalysisError: If analysis fails after retries.
    """
    warnings: list[str] = []

    try:
        prompt_template = PROMPT_PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise NonfictionAnalysisError(
            "Nonfiction section analysis prompt not found. "
            "Ensure prompts/nonfiction_section_analysis_v1.txt exists."
        )

    format_label = nonfiction_format or "general nonfiction"
    prompt = prompt_template.replace("{nonfiction_format}", format_label)
    prompt = prompt.replace("{section_number}", str(section_number))

    import json
    arg_map_str = json.dumps(argument_map_json) if argument_map_json else "{}"
    prompt = prompt.replace("{argument_map}", arg_map_str)

    full_prompt = f"{prompt}\n\n<manuscript_text>\n{section_text}\n</manuscript_text>"

    try:
        raw_response = await call_llm(
            prompt=full_prompt,
            model=settings.llm_model_analysis,
            max_tokens=4096,
        )
    except Exception as e:
        raise NonfictionAnalysisError(f"LLM call failed: {e}")

    try:
        parsed = parse_json_response(raw_response)
        result = NonfictionSectionAnalysis.model_validate(parsed)
    except Exception as e:
        raise NonfictionAnalysisError(f"Failed to parse section analysis response: {e}")

    return result, warnings
