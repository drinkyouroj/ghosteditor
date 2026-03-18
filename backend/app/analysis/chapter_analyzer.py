"""Chapter analysis engine for developmental editing feedback.

Handles Claude API calls, JSON repair, schema validation, and retry logic.
Follows the same patterns as story_bible.py.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from pydantic import ValidationError

from app.analysis.genre_conventions import get_genre_conventions
from app.analysis.issue_schema import ChapterAnalysisResult, validate_and_filter
from app.analysis.json_repair import is_truncated, parse_json_response
from app.analysis.llm_client import LLMError, call_llm
from app.analysis.utils import sanitize_manuscript_text as _sanitize_manuscript_text
from app.config import settings

logger = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).parent / "prompts"
PROMPT_VERSION = "chapter_analysis_v1"
MAX_TOKENS = 16384
MIN_CHAPTER_WORDS = 500


def _load_prompt(name: str) -> str:
    path = PROMPTS_DIR / f"{name}.txt"
    return path.read_text()


def _format_bible_section(bible_json: dict | None, key: str) -> str:
    """Format a story bible section for the prompt, or 'None available' if missing."""
    if bible_json is None:
        return "None available"
    value = bible_json.get(key)
    if value is None:
        return "None available"
    if isinstance(value, list) and len(value) == 0:
        return "None available"
    if isinstance(value, dict) and not any(value.values()):
        return "None available"
    return json.dumps(value, indent=2)


def _build_genre_conventions_section(genre: str) -> str:
    """Build the genre conventions section for the prompt."""
    conventions = get_genre_conventions(genre)
    if not conventions:
        return "Genre conventions: No specific conventions available for this genre. Evaluate based on general storytelling principles."
    lines = ["Genre conventions to check for:"]
    for conv in conventions:
        lines.append(f"- {conv}")
    return "\n".join(lines)


def _build_chapter_one_instruction(chapter_number: int) -> str:
    """For chapter 1 (no prior bible), skip consistency checks."""
    if chapter_number == 1:
        return (
            "NOTE: This is Chapter 1. There is no prior story bible to check consistency against. "
            "Skip consistency-type issues entirely. Focus on character introduction, pacing, "
            "voice establishment, and genre fit."
        )
    return (
        "Check this chapter against ALL story bible sections above. Flag any contradictions "
        "in character details, timeline impossibilities, setting inconsistencies, or "
        "world rule violations as consistency issues."
    )


async def analyze_chapter(
    chapter_text: str,
    chapter_number: int,
    genre: str | None = None,
    bible_json: dict | None = None,
) -> tuple[ChapterAnalysisResult, list[str]]:
    """Analyze a chapter for developmental editing issues.

    Returns (validated_result, warnings_list).
    Raises ChapterAnalysisError on unrecoverable failure.
    """
    warnings: list[str] = []
    genre_str = genre or "Not specified"

    # Check minimum chapter length
    word_count = len(chapter_text.split())
    if word_count < MIN_CHAPTER_WORDS:
        warnings.append(
            f"Chapter has only {word_count} words (minimum {MIN_CHAPTER_WORDS}). "
            "Analysis skipped — chapter may be too short for meaningful feedback."
        )
        return ChapterAnalysisResult(), warnings

    sanitized_text = _sanitize_manuscript_text(chapter_text)

    # Build prompt
    prompt_template = _load_prompt(PROMPT_VERSION)
    prompt = prompt_template.format(
        genre=genre_str,
        chapter_number=chapter_number,
        genre_conventions_section=_build_genre_conventions_section(genre_str),
        bible_characters=_format_bible_section(bible_json, "characters"),
        bible_timeline=_format_bible_section(bible_json, "timeline"),
        bible_settings=_format_bible_section(bible_json, "settings"),
        bible_world_rules=_format_bible_section(bible_json, "world_rules"),
        bible_voice_profile=_format_bible_section(bible_json, "voice_profile"),
        bible_plot_threads=_format_bible_section(bible_json, "plot_threads"),
        chapter_one_instruction=_build_chapter_one_instruction(chapter_number),
        chapter_text=sanitized_text,
    )

    # Call LLM API
    try:
        raw_response = await call_llm(prompt, settings.llm_model_analysis, MAX_TOKENS)
    except LLMError as e:
        raise ChapterAnalysisError(str(e))

    if is_truncated(raw_response):
        logger.error(f"LLM response appears truncated (len={len(raw_response)})")
        raise ChapterAnalysisError(
            "AI response was cut off. This usually means the chapter produced "
            "too much output. Please try again."
        )

    # JSON repair pipeline
    parsed = parse_json_response(raw_response)

    if parsed is None:
        logger.warning(
            f"JSON parse failed for chapter {chapter_number}. "
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
            raise ChapterAnalysisError(str(e))
        if is_truncated(raw_response):
            logger.error(f"LLM response appears truncated (len={len(raw_response)})")
            raise ChapterAnalysisError(
                "AI response was cut off. This usually means the chapter produced "
                "too much output. Please try again."
            )
        parsed = parse_json_response(raw_response)

    if parsed is None:
        logger.error(
            f"All JSON parse attempts failed for chapter {chapter_number}. "
            f"Final response starts with: {raw_response[:500]!r}"
        )
        raise ChapterAnalysisError(
            "Failed to get valid JSON after retries. "
            "The chapter may contain content that causes formatting issues."
        )

    # Schema validation
    try:
        validated = ChapterAnalysisResult.model_validate(parsed)
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
            raise ChapterAnalysisError(str(e))
        if is_truncated(raw_response):
            logger.error(f"LLM response appears truncated (len={len(raw_response)})")
            raise ChapterAnalysisError(
                "AI response was cut off. This usually means the chapter produced "
                "too much output. Please try again."
            )
        parsed = parse_json_response(raw_response)
        if parsed is None:
            raise ChapterAnalysisError(f"Schema validation failed after retry: {error_details}")
        try:
            validated = ChapterAnalysisResult.model_validate(parsed)
        except ValidationError as e2:
            raise ChapterAnalysisError(f"Schema validation failed after retry: {e2}")

    # Post-validation filtering
    validated = validate_and_filter(validated)

    return validated, warnings


class ChapterAnalysisError(Exception):
    """Raised when chapter analysis fails in a user-facing way."""
    pass
