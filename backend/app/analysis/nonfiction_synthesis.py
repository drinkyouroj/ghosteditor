"""Nonfiction document synthesis generator.

Generates a document-level summary by combining all section analysis results
and the argument map into a cohesive synthesis. This module will be fully
implemented by Agent 2. The interface contract is defined here so Agent 3's
worker integration can import it.
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

PROMPT_PATH = Path(__file__).parent / "prompts" / "nonfiction_synthesis_v1.txt"


class SynthesisError(Exception):
    """Raised when document synthesis fails."""
    pass


class DocumentSynthesis(BaseModel):
    """Result of document-level nonfiction synthesis."""
    overall_assessment: str = Field(description="High-level assessment of the document")
    argument_strength: dict | None = Field(default=None, description="Overall argument strength analysis")
    evidence_quality: dict | None = Field(default=None, description="Overall evidence quality")
    structural_coherence: dict | None = Field(default=None, description="Document structure assessment")
    key_recommendations: list[str] = Field(default_factory=list, description="Top editorial recommendations")
    cross_section_issues: list[dict] = Field(default_factory=list, description="Issues spanning multiple sections")


async def generate_document_synthesis(
    argument_map_json: dict | None,
    section_results: list[dict],
    nonfiction_format: str | None = None,
) -> tuple[DocumentSynthesis, list[str]]:
    """Generate document-level synthesis from section results and argument map.

    Args:
        argument_map_json: The full argument map for this manuscript.
        section_results: List of section analysis result dicts.
        nonfiction_format: Optional format hint.

    Returns:
        Tuple of (DocumentSynthesis, list of warning strings).

    Raises:
        SynthesisError: If synthesis fails.
    """
    warnings: list[str] = []

    try:
        prompt_template = PROMPT_PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise SynthesisError(
            "Nonfiction synthesis prompt not found. "
            "Ensure prompts/nonfiction_synthesis_v1.txt exists."
        )

    import json
    format_label = nonfiction_format or "general nonfiction"
    prompt = prompt_template.replace("{nonfiction_format}", format_label)

    arg_map_str = json.dumps(argument_map_json) if argument_map_json else "{}"
    sections_str = json.dumps(section_results, indent=2)

    prompt = prompt.replace("{argument_map}", arg_map_str)
    prompt = prompt.replace("{section_results}", sections_str)

    try:
        raw_response = await call_llm(
            prompt=prompt,
            model=settings.llm_model_analysis,
            max_tokens=4096,
        )
    except Exception as e:
        raise SynthesisError(f"LLM call failed: {e}")

    try:
        parsed = parse_json_response(raw_response)
        result = DocumentSynthesis.model_validate(parsed)
    except Exception as e:
        raise SynthesisError(f"Failed to parse synthesis response: {e}")

    return result, warnings
