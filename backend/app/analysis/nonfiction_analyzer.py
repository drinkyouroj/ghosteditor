"""Nonfiction section analysis engine for developmental editing feedback.

Handles LLM API calls, JSON repair, schema validation, and retry logic.
Mirrors the patterns in chapter_analyzer.py for nonfiction manuscripts.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from pydantic import ValidationError

from app.analysis.json_repair import is_truncated, parse_json_response
from app.analysis.llm_client import LLMError, call_llm
from app.analysis.nonfiction_analysis_schema import (
    SectionAnalysisResult,
    validate_and_filter_section,
)
from app.analysis.nonfiction_conventions import get_nonfiction_conventions
from app.analysis.utils import sanitize_manuscript_text as _sanitize_manuscript_text
from app.config import settings

logger = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).parent / "prompts"
PROMPT_VERSION = "nonfiction_section_analysis_v1"
MAX_TOKENS = 16384
MIN_SECTION_WORDS = 300


def _load_prompt(name: str) -> str:
    path = PROMPTS_DIR / f"{name}.txt"
    return path.read_text()


def _format_argument_map_section(argument_map_json: dict | None, key: str) -> str:
    """Format an argument map section for the prompt, or 'None available' if missing."""
    if argument_map_json is None:
        return "None available"
    value = argument_map_json.get(key)
    if value is None:
        return "None available"
    if isinstance(value, list) and len(value) == 0:
        return "None available"
    if isinstance(value, dict) and not any(value.values()):
        return "None available"
    return json.dumps(value, indent=2)


def _build_section_one_instruction(section_number: int) -> str:
    """For section 1 (no prior argument map), skip consistency checks."""
    if section_number == 1:
        return (
            "NOTE: This is Section 1. There is no prior argument map to check "
            "consistency against. Focus on identifying the thesis, initial argument "
            "threads, evidence quality, clarity, and structure."
        )
    return (
        "Check this section against ALL argument map data above. Flag any "
        "contradictions with previously stated claims, abandoned argument threads, "
        "or evidence that conflicts with earlier evidence."
    )


async def analyze_nonfiction_section(
    section_text: str,
    section_number: int,
    nonfiction_format: str | None = None,
    argument_map_json: dict | None = None,
    section_detection_method: str = "header",
    total_sections: int = 1,
) -> tuple[SectionAnalysisResult, list[str]]:
    """Analyze a nonfiction section for developmental editing issues.

    Args:
        section_text: The raw text of the section.
        section_number: 1-based section number.
        nonfiction_format: One of the nonfiction_format enum values.
        argument_map_json: Current argument map state as dict.
        section_detection_method: "header" or "chunked".
        total_sections: Total number of sections in the document.

    Returns:
        (validated_result, warnings_list).

    Raises:
        NonfictionAnalysisError on unrecoverable failure.
    """
    warnings: list[str] = []

    # Infer format from argument map if not specified by user
    if not nonfiction_format and argument_map_json:
        detected = argument_map_json.get("detected_format_confidence")
        if detected and isinstance(detected, dict):
            inferred = detected.get("format")
            confidence = detected.get("confidence", "low")
            if inferred and inferred != "other" and confidence in ("high", "medium"):
                nonfiction_format = inferred
                logger.info(
                    f"Inferred nonfiction format '{inferred}' from argument map "
                    f"(confidence: {confidence})"
                )

    format_str = nonfiction_format or "Not specified"

    # Check minimum section length
    word_count = len(section_text.split())
    if word_count < MIN_SECTION_WORDS:
        warnings.append(
            f"Section has only {word_count} words (minimum {MIN_SECTION_WORDS}). "
            "Analysis skipped — section may be too short for meaningful feedback."
        )
        return SectionAnalysisResult(section_number=section_number, word_count=word_count), warnings

    sanitized_text = _sanitize_manuscript_text(section_text)

    # Load format-specific conventions
    conventions = get_nonfiction_conventions(format_str)
    if not conventions:
        conventions = (
            "No specific format conventions available. "
            "Evaluate based on general nonfiction writing principles."
        )

    # Build prompt — use .replace() instead of .format() to avoid
    # IndexError from curly braces in manuscript text or JSON data
    prompt_template = _load_prompt(PROMPT_VERSION)
    prompt = prompt_template.replace("{nonfiction_format}", format_str)
    prompt = prompt.replace("{section_number}", str(section_number))
    prompt = prompt.replace("{section_detection_method}", section_detection_method)
    prompt = prompt.replace("{total_sections}", str(total_sections))
    prompt = prompt.replace("{argument_map_thesis}", _format_argument_map_section(argument_map_json, "central_thesis"))
    prompt = prompt.replace("{argument_map_threads}", _format_argument_map_section(argument_map_json, "argument_threads"))
    prompt = prompt.replace("{argument_map_evidence}", _format_argument_map_section(argument_map_json, "evidence_log"))
    prompt = prompt.replace("{argument_map_voice}", _format_argument_map_section(argument_map_json, "voice_profile"))
    prompt = prompt.replace("{format_conventions}", conventions)
    prompt = prompt.replace("{section_one_instruction}", _build_section_one_instruction(section_number))
    prompt = prompt.replace("{chapter_text}", sanitized_text)

    # Call LLM API
    try:
        raw_response = await call_llm(prompt, settings.llm_model_analysis, MAX_TOKENS)
    except LLMError as e:
        raise NonfictionAnalysisError(str(e))

    if is_truncated(raw_response):
        logger.error(f"LLM response appears truncated (len={len(raw_response)})")
        raise NonfictionAnalysisError(
            "AI response was cut off. This usually means the section produced "
            "too much output. Please try again."
        )

    # JSON repair pipeline
    parsed = parse_json_response(raw_response)

    if parsed is None:
        logger.warning(
            f"JSON parse failed for section {section_number}. "
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
            raise NonfictionAnalysisError(str(e))
        if is_truncated(raw_response):
            logger.error(f"LLM response appears truncated (len={len(raw_response)})")
            raise NonfictionAnalysisError(
                "AI response was cut off. This usually means the section produced "
                "too much output. Please try again."
            )
        parsed = parse_json_response(raw_response)

    if parsed is None:
        logger.error(
            f"All JSON parse attempts failed for section {section_number}. "
            f"Final response starts with: {raw_response[:500]!r}"
        )
        raise NonfictionAnalysisError(
            "Failed to get valid JSON after retries. "
            "The section may contain content that causes formatting issues."
        )

    # Schema validation
    try:
        validated = SectionAnalysisResult.model_validate(parsed)
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
            raise NonfictionAnalysisError(str(e))
        if is_truncated(raw_response):
            logger.error(f"LLM response appears truncated (len={len(raw_response)})")
            raise NonfictionAnalysisError(
                "AI response was cut off. This usually means the section produced "
                "too much output. Please try again."
            )
        parsed = parse_json_response(raw_response)
        if parsed is None:
            raise NonfictionAnalysisError(f"Schema validation failed after retry: {error_details}")
        try:
            validated = SectionAnalysisResult.model_validate(parsed)
        except ValidationError as e2:
            raise NonfictionAnalysisError(f"Schema validation failed after retry: {e2}")

    # Post-validation filtering
    validated = validate_and_filter_section(validated)

    return validated, warnings


class NonfictionAnalysisError(Exception):
    """Raised when nonfiction section analysis fails in a user-facing way."""
    pass
