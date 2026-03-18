"""Argument map generator for nonfiction manuscripts.

Generates a structured argument map (thesis, argument threads, evidence patterns)
from a nonfiction manuscript, serving the same role as a Story Bible for fiction.
"""
from __future__ import annotations

import logging
from pathlib import Path

from app.analysis.argument_map_schema import ArgumentMapSchema
from app.analysis.llm_client import call_llm
from app.analysis.json_repair import parse_json_response
from app.config import settings

logger = logging.getLogger(__name__)

PROMPT_PATH = Path(__file__).parent / "prompts" / "argument_map_v1.txt"


class ArgumentMapError(Exception):
    """Raised when argument map generation fails."""
    pass


def _sanitize_manuscript_text(text: str) -> str:
    """Escape closing manuscript_text tags to prevent prompt injection."""
    return text.replace("</manuscript_text>", "&lt;/manuscript_text&gt;")


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
    full_prompt = prompt_template.replace("{nonfiction_format}", format_label)
    full_prompt = full_prompt.replace("{chapter_text}", sanitized_text)

    try:
        raw_response = await call_llm(
            prompt=full_prompt,
            model=settings.llm_model_bible,
            max_tokens=4096,
        )
    except Exception as e:
        raise ArgumentMapError(f"LLM call failed: {e}")

    try:
        parsed = parse_json_response(raw_response)
        schema = ArgumentMapSchema.model_validate(parsed)
    except Exception as e:
        raise ArgumentMapError(f"Failed to parse argument map response: {e}")

    return schema, warnings
