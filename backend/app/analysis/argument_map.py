"""Argument map generator for nonfiction manuscripts.

Generates a structured argument map (thesis, argument threads, evidence patterns)
from a nonfiction manuscript, serving the same role as a Story Bible for fiction.
"""
from __future__ import annotations

import logging
from pathlib import Path

from pydantic import ValidationError

from app.analysis.argument_map_schema import ArgumentMapSchema
from app.analysis.json_repair import is_truncated, parse_json_response
from app.analysis.llm_client import LLMError, call_llm
from app.analysis.utils import sanitize_manuscript_text as _sanitize_manuscript_text
from app.config import settings

logger = logging.getLogger(__name__)

PROMPT_PATH = Path(__file__).parent / "prompts" / "argument_map_v1.txt"
MAX_TOKENS = 8192


class ArgumentMapError(Exception):
    """Raised when argument map generation fails."""
    pass


async def generate_argument_map(
    manuscript_text: str,
    nonfiction_format: str | None = None,
) -> tuple[ArgumentMapSchema, list[str]]:
    """Generate an argument map from nonfiction manuscript text.

    Args:
        manuscript_text: Full manuscript text (or concatenated sections).
        nonfiction_format: Optional format hint (academic, journalism, etc.).

    Returns:
        Tuple of (ArgumentMapSchema, list of warning strings).

    Raises:
        ArgumentMapError: If generation fails after retries.
    """
    warnings: list[str] = []

    try:
        prompt_template = PROMPT_PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise ArgumentMapError(
            "Argument map prompt template not found. "
            "Ensure prompts/argument_map_v1.txt exists."
        )

    format_label = nonfiction_format or "general nonfiction"

    # Truncate very long texts to avoid context window overflow
    max_chars = 100_000
    if len(manuscript_text) > max_chars:
        manuscript_text = manuscript_text[:max_chars]
        warnings.append(
            f"Manuscript text truncated to {max_chars} characters for argument map generation"
        )

    sanitized_text = _sanitize_manuscript_text(manuscript_text)

    # Prompt template contains {nonfiction_format} and {chapter_text} placeholders
    # plus <manuscript_text> wrapping — substitute both via .replace()
    prompt = prompt_template.replace("{nonfiction_format}", format_label)
    prompt = prompt.replace("{chapter_text}", sanitized_text)

    # Call LLM API
    try:
        raw_response = await call_llm(prompt, settings.llm_model_bible, MAX_TOKENS)
    except LLMError as e:
        raise ArgumentMapError(str(e))

    if is_truncated(raw_response):
        logger.error(f"LLM response appears truncated (len={len(raw_response)})")
        raise ArgumentMapError(
            "AI response was cut off. This usually means the manuscript produced "
            "too much output. Please try again."
        )

    # JSON repair pipeline
    parsed = parse_json_response(raw_response)

    if parsed is None:
        logger.warning(
            f"JSON parse failed for argument map. "
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
            raise ArgumentMapError(
                "AI response was cut off on retry. Please try again."
            )
        parsed = parse_json_response(raw_response)

    if parsed is None:
        logger.error(
            f"All JSON parse attempts failed for argument map. "
            f"Final response starts with: {raw_response[:500]!r}"
        )
        raise ArgumentMapError(
            "Failed to get valid JSON after retries. "
            "The manuscript may contain content that causes formatting issues."
        )

    # Schema validation
    try:
        schema = ArgumentMapSchema.model_validate(parsed)
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
            raise ArgumentMapError(
                "AI response was cut off on retry. Please try again."
            )
        parsed = parse_json_response(raw_response)
        if parsed is None:
            raise ArgumentMapError(f"Schema validation failed after retry: {error_details}")
        try:
            schema = ArgumentMapSchema.model_validate(parsed)
        except ValidationError as e2:
            raise ArgumentMapError(f"Schema validation failed after retry: {e2}")

    return schema, warnings
