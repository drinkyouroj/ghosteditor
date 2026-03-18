"""Argument map generation and incremental update service.

Handles LLM API calls, JSON repair, schema validation, drift detection,
prompt injection sanitization, and evidence log condensation for nonfiction
manuscripts. Mirrors story_bible.py's architecture.

Per DECISION_008 and DECISION_004 (nonfiction mode):
- Drift detection: warn if argument_threads count decreases
- JSON repair pipeline (reuses json_repair.py)
- Sanitize </manuscript_text> tags in input
- Evidence log cap at 50 entries with condensation
- Detected format confidence divergence warning
- Pydantic schema validation
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from pydantic import ValidationError

from app.analysis.argument_map_schema import ArgumentMapSchema
from app.analysis.json_repair import is_truncated, parse_json_response
from app.analysis.llm_client import LLMError, call_llm
from app.config import settings

logger = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).parent / "prompts"
PROMPT_VERSION = "argument_map_v1"
MAX_TOKENS = 64000
MAX_EVIDENCE_LOG_ENTRIES = 50


def _load_prompt(name: str) -> str:
    path = PROMPTS_DIR / f"{name}.txt"
    return path.read_text()


def _sanitize_manuscript_text(text: str) -> str:
    """Escape closing manuscript_text tags to prevent prompt injection."""
    return text.replace("</manuscript_text>", "&lt;/manuscript_text&gt;")


def _check_drift(old_map: dict, new_map: dict) -> list[str]:
    """Compare entity counts between old and new argument map. Returns warnings.

    Warns if argument_threads count decreases — possible drift where
    threads were dropped during incremental update.
    """
    warnings = []
    old_count = len(old_map.get("argument_threads", []))
    new_count = len(new_map.get("argument_threads", []))
    if new_count < old_count:
        warnings.append(
            f"argument thread count decreased: {old_count} → {new_count}. "
            f"Possible drift — threads may have been dropped."
        )
    return warnings


def _check_format_divergence(
    detected: dict, user_format: str | None
) -> list[str]:
    """Check if detected format diverges from user-selected format.

    Returns warnings if the LLM's detected format differs from what
    the user specified.
    """
    warnings = []
    if user_format is None:
        return warnings

    detected_format = detected.get("format", "other")
    confidence = detected.get("confidence", "low")

    if detected_format != user_format and confidence in ("high", "medium"):
        warnings.append(
            f"Format divergence detected: you selected '{user_format}' but "
            f"the document appears to be '{detected_format}' "
            f"(confidence: {confidence}). Analysis will use the detected format."
        )
    return warnings


async def generate_argument_map(
    section_text: str,
    section_number: int,
    nonfiction_format: str | None = None,
    existing_map: dict | None = None,
) -> tuple[ArgumentMapSchema, list[str]]:
    """Generate or update an argument map from section text.

    Args:
        section_text: The text of the nonfiction section to analyze.
        section_number: The section number (1-indexed).
        nonfiction_format: User-selected nonfiction format (academic, etc.) or None.
        existing_map: Previous argument map dict for incremental update, or None.

    Returns (validated_argument_map, warnings).
    Raises ArgumentMapError on unrecoverable failure.
    """
    sanitized_text = _sanitize_manuscript_text(section_text)
    format_str = nonfiction_format or "Not specified"
    warnings = []

    if existing_map is None or section_number == 1:
        prompt_template = _load_prompt("argument_map_v1")
        prompt = prompt_template.format(
            nonfiction_format=format_str,
            chapter_text=sanitized_text,
        )
    else:
        # Incremental update: include existing map in prompt
        prompt_template = _load_prompt("argument_map_v1")
        prompt = prompt_template.format(
            nonfiction_format=format_str,
            chapter_text=sanitized_text,
        )
        # Append existing map context for incremental updates
        prompt += (
            "\n\nIMPORTANT: You are updating an existing argument map. "
            "Here is the current argument map from previous sections. "
            "Merge new findings into this structure. Do NOT drop existing "
            "argument threads — only add new ones or update statuses.\n\n"
            f"Existing argument map:\n{json.dumps(existing_map, indent=2)}"
        )

    # Call LLM API
    try:
        raw_response = await call_llm(prompt, settings.llm_model_bible, MAX_TOKENS)
    except LLMError as e:
        raise ArgumentMapError(str(e))

    if is_truncated(raw_response):
        logger.error(f"LLM response appears truncated (len={len(raw_response)})")
        raise ArgumentMapError(
            "AI response was cut off. This usually means the section produced "
            "too much output. Please try again."
        )

    # JSON repair pipeline
    parsed = parse_json_response(raw_response)

    if parsed is None:
        logger.warning(
            f"JSON parse failed for argument map (section {section_number}). "
            f"Response starts with: {raw_response[:200]!r}"
        )
        # Retry once with explicit JSON instruction
        retry_prompt = prompt + (
            "\n\nIMPORTANT: Your previous response was not valid JSON. "
            "Respond with ONLY valid JSON. No text before or after the JSON object."
        )
        try:
            raw_response = await call_llm(retry_prompt, settings.llm_model_bible, MAX_TOKENS)
        except LLMError as e:
            raise ArgumentMapError(str(e))
        if is_truncated(raw_response):
            logger.error(f"LLM response appears truncated (len={len(raw_response)})")
            raise ArgumentMapError(
                "AI response was cut off. This usually means the section produced "
                "too much output. Please try again."
            )
        parsed = parse_json_response(raw_response)

    if parsed is None:
        logger.error(
            f"All JSON parse attempts failed for argument map (section {section_number}). "
            f"Final response starts with: {raw_response[:500]!r}"
        )
        raise ArgumentMapError(
            "Failed to get valid JSON after retries. "
            "The section may contain content that causes formatting issues."
        )

    # Track whether evidence log was truncated before schema validation
    raw_evidence_count = len(parsed.get("evidence_log", []) or [])
    evidence_was_truncated = raw_evidence_count > MAX_EVIDENCE_LOG_ENTRIES

    # Schema validation
    try:
        validated = ArgumentMapSchema.model_validate(parsed)
    except ValidationError as e:
        # Retry with validation error context
        error_details = str(e)
        retry_prompt = prompt + (
            f"\n\nIMPORTANT: Your previous response had schema errors:\n{error_details}\n"
            "Please fix these errors and respond with valid JSON matching the schema exactly."
        )
        try:
            raw_response = await call_llm(retry_prompt, settings.llm_model_bible, MAX_TOKENS)
        except LLMError as e:
            raise ArgumentMapError(str(e))
        if is_truncated(raw_response):
            logger.error(f"LLM response appears truncated (len={len(raw_response)})")
            raise ArgumentMapError(
                "AI response was cut off. This usually means the section produced "
                "too much output. Please try again."
            )
        parsed = parse_json_response(raw_response)
        if parsed is None:
            raise ArgumentMapError(f"Schema validation failed after retry: {error_details}")
        try:
            validated = ArgumentMapSchema.model_validate(parsed)
        except ValidationError as e2:
            raise ArgumentMapError(f"Schema validation failed after retry: {e2}")

    # Set evidence_log_truncated flag
    if evidence_was_truncated:
        validated.evidence_log_truncated = True
        logger.info(
            f"Evidence log truncated from {raw_evidence_count} to "
            f"{MAX_EVIDENCE_LOG_ENTRIES} entries"
        )
        warnings.append(
            f"Evidence log was capped at {MAX_EVIDENCE_LOG_ENTRIES} entries "
            f"(original had {raw_evidence_count}). Most significant entries retained."
        )

    # Drift detection
    if existing_map is not None and section_number > 1:
        drift_warnings = _check_drift(existing_map, validated.model_dump())
        if drift_warnings:
            for w in drift_warnings:
                logger.warning(f"Argument map drift detected (Section {section_number}): {w}")
            warnings.extend(drift_warnings)

    # Format divergence check
    format_warnings = _check_format_divergence(
        validated.detected_format_confidence.model_dump(),
        nonfiction_format,
    )
    if format_warnings:
        for w in format_warnings:
            logger.info(f"Format divergence (Section {section_number}): {w}")
        warnings.extend(format_warnings)

    return validated, warnings


class ArgumentMapError(Exception):
    """Raised when argument map generation fails in a user-facing way."""
    pass
