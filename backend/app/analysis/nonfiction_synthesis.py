"""Nonfiction document synthesis generator.

Produces a document-level assessment by synthesizing argument map data and
per-section analysis summaries. Receives structured data only — no raw
manuscript text.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from pydantic import ValidationError

from app.analysis.json_repair import is_truncated, parse_json_response
from app.analysis.llm_client import LLMError, call_llm
from app.analysis.nonfiction_analysis_schema import DocumentSynthesis
from app.config import settings

logger = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).parent / "prompts"
PROMPT_VERSION = "nonfiction_synthesis_v1"
MAX_TOKENS = 8192


def _load_prompt(name: str) -> str:
    path = PROMPTS_DIR / f"{name}.txt"
    return path.read_text()


def _format_field(value: object) -> str:
    """Format a field for the prompt, handling None and empty values."""
    if value is None:
        return "None available"
    if isinstance(value, list) and len(value) == 0:
        return "None available"
    if isinstance(value, dict) and not any(value.values()):
        return "None available"
    if isinstance(value, str):
        return value if value.strip() else "None available"
    return json.dumps(value, indent=2)


async def generate_document_synthesis(
    argument_map_json: dict,
    section_summaries: list[dict],
    nonfiction_format: str | None = None,
) -> tuple[DocumentSynthesis, list[str]]:
    """Generate a document-level synthesis from structured analysis data.

    Args:
        argument_map_json: The complete argument map as a dict.
        section_summaries: List of dicts with keys: section_number,
            issue_count_by_dimension, key_issues.
        nonfiction_format: One of the nonfiction_format enum values.

    Returns:
        (validated_synthesis, warnings_list).

    Raises:
        SynthesisError on unrecoverable failure.
    """
    warnings: list[str] = []
    format_str = nonfiction_format or "Not specified"

    # Extract argument map fields for prompt formatting
    central_thesis = argument_map_json.get("central_thesis", "")
    if isinstance(central_thesis, dict):
        thesis_text = central_thesis.get("statement", "")
        has_explicit = central_thesis.get("is_explicit", False)
    else:
        thesis_text = str(central_thesis) if central_thesis else ""
        has_explicit = False

    has_conclusion = argument_map_json.get("has_conclusion", False)
    detected_format = argument_map_json.get("detected_format", format_str)
    detected_format_confidence = argument_map_json.get("detected_format_confidence", "unknown")

    # Build prompt
    prompt_template = _load_prompt(PROMPT_VERSION)
    prompt = prompt_template.format(
        nonfiction_format=format_str,
        total_sections=len(section_summaries),
        central_thesis=_format_field(thesis_text),
        has_explicit_thesis=str(has_explicit).lower(),
        has_conclusion=str(has_conclusion).lower(),
        argument_threads=_format_field(argument_map_json.get("argument_threads")),
        evidence_log=_format_field(argument_map_json.get("evidence_log")),
        voice_profile=_format_field(argument_map_json.get("voice_profile")),
        detected_format=_format_field(detected_format),
        detected_format_confidence=str(detected_format_confidence),
        section_issue_summary=json.dumps(section_summaries, indent=2),
    )

    # Call LLM API
    try:
        raw_response = await call_llm(prompt, settings.llm_model_analysis, MAX_TOKENS)
    except LLMError as e:
        raise SynthesisError(str(e))

    if is_truncated(raw_response):
        logger.error(f"LLM response appears truncated (len={len(raw_response)})")
        raise SynthesisError(
            "AI response was cut off during document synthesis. Please try again."
        )

    # JSON repair pipeline
    parsed = parse_json_response(raw_response)

    if parsed is None:
        logger.warning(
            f"JSON parse failed for document synthesis. "
            f"Response starts with: {raw_response[:200]!r}"
        )
        # Retry once with explicit JSON instruction
        retry_prompt = prompt + (
            "\n\nIMPORTANT: Your previous response was not valid JSON. "
            "Respond with ONLY valid JSON. No text before or after the JSON object."
        )
        try:
            raw_response = await call_llm(retry_prompt, settings.llm_model_analysis, MAX_TOKENS)
        except LLMError as e:
            raise SynthesisError(str(e))
        if is_truncated(raw_response):
            logger.error(f"LLM response appears truncated (len={len(raw_response)})")
            raise SynthesisError(
                "AI response was cut off during document synthesis. Please try again."
            )
        parsed = parse_json_response(raw_response)

    if parsed is None:
        logger.error(
            f"All JSON parse attempts failed for document synthesis. "
            f"Final response starts with: {raw_response[:500]!r}"
        )
        raise SynthesisError(
            "Failed to get valid JSON after retries. "
            "The synthesis may have encountered formatting issues."
        )

    # Schema validation
    try:
        validated = DocumentSynthesis.model_validate(parsed)
    except ValidationError as e:
        # Retry with validation error context
        error_details = str(e)
        retry_prompt = prompt + (
            f"\n\nIMPORTANT: Your previous response had schema errors:\n{error_details}\n"
            "Please fix these errors and respond with valid JSON matching the schema exactly."
        )
        try:
            raw_response = await call_llm(retry_prompt, settings.llm_model_analysis, MAX_TOKENS)
        except LLMError as e:
            raise SynthesisError(str(e))
        if is_truncated(raw_response):
            logger.error(f"LLM response appears truncated (len={len(raw_response)})")
            raise SynthesisError(
                "AI response was cut off during document synthesis. Please try again."
            )
        parsed = parse_json_response(raw_response)
        if parsed is None:
            raise SynthesisError(f"Schema validation failed after retry: {error_details}")
        try:
            validated = DocumentSynthesis.model_validate(parsed)
        except ValidationError as e2:
            raise SynthesisError(f"Schema validation failed after retry: {e2}")

    return validated, warnings


class SynthesisError(Exception):
    """Raised when document synthesis fails in a user-facing way."""
    pass
