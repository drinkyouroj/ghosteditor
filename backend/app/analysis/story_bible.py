"""Story bible generation and incremental update service.

Handles Claude API calls, JSON repair, schema validation, drift detection,
prompt injection sanitization, and voice profile update logic.

Per DECISION_004 JUDGE amendments:
- #1: Drift detection — warn if entity counts decrease
- #2: JSON repair pipeline
- #3: Sanitize </manuscript_text> tags in input
- #4: Voice profile update window (allow update on Chapter 2 only)
- #5: Pydantic schema validation
"""

import json
import logging
from pathlib import Path

import anthropic
from pydantic import ValidationError

from app.analysis.bible_schema import StoryBibleSchema
from app.analysis.json_repair import is_truncated, parse_json_response
from app.config import settings

logger = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).parent / "prompts"
PROMPT_VERSION = "story_bible_v1"
MAX_TOKENS = 16384


def _load_prompt(name: str) -> str:
    path = PROMPTS_DIR / f"{name}.txt"
    return path.read_text()


def _sanitize_manuscript_text(text: str) -> str:
    """Escape closing manuscript_text tags to prevent prompt injection.
    Per DECISION_004 JUDGE amendment #3.
    """
    return text.replace("</manuscript_text>", "&lt;/manuscript_text&gt;")


def _check_drift(old_bible: dict, new_bible: dict) -> list[str]:
    """Compare entity counts between old and new bible. Returns warnings.
    Per DECISION_004 JUDGE amendment #1.
    """
    warnings = []
    checks = [
        ("characters", "character"),
        ("timeline", "timeline event"),
        ("settings", "setting"),
        ("plot_threads", "plot thread"),
        ("world_rules", "world rule"),
    ]
    for key, label in checks:
        old_count = len(old_bible.get(key, []))
        new_count = len(new_bible.get(key, []))
        if new_count < old_count:
            warnings.append(
                f"{label} count decreased: {old_count} → {new_count}. "
                f"Possible drift — entries may have been dropped."
            )
    return warnings


def _compare_voice_profiles(profile1: dict, profile2: dict) -> bool:
    """Check if two voice profiles differ significantly (different POV or tense)."""
    return (
        profile1.get("pov", "").lower() != profile2.get("pov", "").lower()
        or profile1.get("tense", "").lower() != profile2.get("tense", "").lower()
    )


async def generate_story_bible(
    chapter_text: str,
    chapter_number: int,
    genre: str | None = None,
    existing_bible: dict | None = None,
) -> tuple[StoryBibleSchema, list[str]]:
    """Generate or update a story bible from chapter text.

    Returns (validated_bible, warnings).
    Raises StoryBibleError on unrecoverable failure.
    """
    sanitized_text = _sanitize_manuscript_text(chapter_text)
    genre_str = genre or "Not specified"
    warnings = []

    if existing_bible is None or chapter_number == 1:
        prompt_template = _load_prompt("story_bible_v1")
        prompt = prompt_template.format(
            genre=genre_str,
            chapter_number=chapter_number,
            chapter_text=sanitized_text,
        )
    else:
        prompt_template = _load_prompt("story_bible_update_v1")
        prompt = prompt_template.format(
            genre=genre_str,
            chapter_number=chapter_number,
            existing_bible_json=json.dumps(existing_bible, indent=2),
            chapter_text=sanitized_text,
        )

    # Call Claude API
    raw_response = await _call_claude(prompt)

    # JSON repair pipeline (JUDGE amendment #2)
    parsed = parse_json_response(raw_response)

    if parsed is None and is_truncated(raw_response):
        logger.warning("Response appears truncated, retrying with higher max_tokens")
        raw_response = await _call_claude(prompt, max_tokens=MAX_TOKENS * 2)
        parsed = parse_json_response(raw_response)

    if parsed is None:
        logger.warning(
            f"JSON parse failed for bible (chapter {chapter_number}). "
            f"Response starts with: {raw_response[:200]!r}"
        )
        # Retry once with explicit JSON instruction
        retry_prompt = prompt + (
            "\n\nIMPORTANT: Your previous response was not valid JSON. "
            "Respond with ONLY valid JSON. No text before or after the JSON object."
        )
        raw_response = await _call_claude(retry_prompt)
        parsed = parse_json_response(raw_response)

    if parsed is None:
        logger.error(
            f"All JSON parse attempts failed for bible (chapter {chapter_number}). "
            f"Final response starts with: {raw_response[:500]!r}"
        )
        raise StoryBibleError(
            "Failed to get valid JSON from Claude after retries. "
            "The chapter may contain content that causes formatting issues."
        )

    # Schema validation (JUDGE amendment #5)
    try:
        validated = StoryBibleSchema.model_validate(parsed)
    except ValidationError as e:
        # Retry with validation error context
        error_details = str(e)
        retry_prompt = prompt + (
            f"\n\nIMPORTANT: Your previous response had schema errors:\n{error_details}\n"
            "Please fix these errors and respond with valid JSON matching the schema exactly."
        )
        raw_response = await _call_claude(retry_prompt)
        parsed = parse_json_response(raw_response)
        if parsed is None:
            raise StoryBibleError(f"Schema validation failed after retry: {error_details}")
        try:
            validated = StoryBibleSchema.model_validate(parsed)
        except ValidationError as e2:
            raise StoryBibleError(f"Schema validation failed after retry: {e2}")

    # Drift detection (JUDGE amendment #1)
    if existing_bible is not None and chapter_number > 1:
        drift_warnings = _check_drift(existing_bible, validated.model_dump())
        if drift_warnings:
            for w in drift_warnings:
                logger.warning(f"Bible drift detected (Chapter {chapter_number}): {w}")
            warnings.extend(drift_warnings)

    # Voice profile update window (JUDGE amendment #4)
    if chapter_number == 2 and existing_bible is not None:
        old_voice = existing_bible.get("voice_profile", {})
        new_voice = validated.voice_profile.model_dump()
        if _compare_voice_profiles(old_voice, new_voice):
            warnings.append(
                "Voice profile updated — Chapter 1 appears to use a different voice "
                "(possible prologue). Using Chapter 2's voice profile."
            )
            # Keep the new voice profile (Chapter 2's)
        else:
            # Restore Chapter 1's voice profile as locked
            validated.voice_profile = StoryBibleSchema.model_validate(
                {"voice_profile": old_voice}
            ).voice_profile

    return validated, warnings


async def _call_claude(prompt: str, max_tokens: int = MAX_TOKENS) -> str:
    """Call Claude API and return the text response.

    Translates Anthropic API errors into StoryBibleError with user-friendly messages.
    """
    try:
        client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
        message = await client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        return message.content[0].text
    except anthropic.RateLimitError:
        raise StoryBibleError(
            "Our AI service is temporarily busy. Please try again in a few minutes."
        )
    except anthropic.AuthenticationError:
        logger.error("Anthropic API authentication failed — check ANTHROPIC_API_KEY")
        raise StoryBibleError(
            "AI service configuration error. Please contact support."
        )
    except anthropic.APIStatusError as e:
        logger.error(f"Anthropic API error {e.status_code}: {e.message}")
        if e.status_code == 529:  # Overloaded
            raise StoryBibleError(
                "Our AI service is temporarily overloaded. Please try again in a few minutes."
            )
        raise StoryBibleError(
            "AI service encountered an error. Please try again."
        )
    except anthropic.APITimeoutError:
        raise StoryBibleError(
            "AI service timed out while analyzing your chapter. "
            "This can happen with very long chapters — please try again."
        )
    except anthropic.APIConnectionError:
        raise StoryBibleError(
            "Could not connect to AI service. Please check your connection and try again."
        )


class StoryBibleError(Exception):
    """Raised when story bible generation fails in a user-facing way."""
    pass
