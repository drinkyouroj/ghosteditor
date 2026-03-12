"""JSON repair pipeline for Claude API responses.

Per DECISION_004 JUDGE amendment #2:
1. Try json.loads() first
2. Strip markdown code fences
3. Fix trailing commas
4. Return None if all repairs fail (caller should retry)
"""

import json
import re


def parse_json_response(text: str) -> dict | None:
    """Attempt to parse JSON from Claude's response, with repair steps."""
    # Step 1: direct parse
    result = _try_parse(text)
    if result is not None:
        return result

    # Step 2: strip markdown code fences
    stripped = _strip_code_fences(text)
    if stripped != text:
        result = _try_parse(stripped)
        if result is not None:
            return result
        text = stripped

    # Step 3: fix trailing commas
    fixed = _fix_trailing_commas(text)
    if fixed != text:
        result = _try_parse(fixed)
        if result is not None:
            return result

    return None


def is_truncated(text: str) -> bool:
    """Check if a JSON response appears truncated (doesn't end with } or ])."""
    stripped = text.rstrip()
    return len(stripped) > 0 and stripped[-1] not in ("}", "]")


def _try_parse(text: str) -> dict | None:
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None


def _strip_code_fences(text: str) -> str:
    """Remove ```json ... ``` or ``` ... ``` wrappers."""
    text = text.strip()
    pattern = r"^```(?:json)?\s*\n?(.*?)\n?\s*```$"
    match = re.match(pattern, text, re.DOTALL)
    if match:
        return match.group(1).strip()
    return text


def _fix_trailing_commas(text: str) -> str:
    """Remove trailing commas before } or ]."""
    return re.sub(r",\s*([}\]])", r"\1", text)
