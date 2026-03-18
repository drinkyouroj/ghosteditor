"""Argument map generator for nonfiction manuscripts.

Generates a structured argument map (thesis, argument threads, evidence patterns)
from a nonfiction manuscript, serving the same role as a Story Bible for fiction.

This module will be fully implemented by Agent 1. The interface contract is
defined here so Agent 3's worker integration can import it.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from app.analysis.argument_map_schema import ArgumentMapSchema
from app.analysis.llm_client import call_llm
from app.analysis.json_repair import parse_json_response
from app.config import settings

logger = logging.getLogger(__name__)

PROMPT_PATH = Path(__file__).parent / "prompts" / "argument_map_v1.txt"


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
    prompt = prompt_template.replace("{nonfiction_format}", format_label)

    # Truncate very long texts to avoid context window overflow
    max_chars = 100_000
    if len(manuscript_text) > max_chars:
        manuscript_text = manuscript_text[:max_chars]
        warnings.append(
            f"Manuscript text truncated to {max_chars} characters for argument map generation"
        )

    full_prompt = f"{prompt}\n\n<manuscript_text>\n{manuscript_text}\n</manuscript_text>"

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
