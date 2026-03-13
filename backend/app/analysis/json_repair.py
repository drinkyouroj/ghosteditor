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

    # Step 4: extract JSON object from surrounding text
    # Claude sometimes adds preamble text before/after the JSON
    extracted = _extract_json_object(text)
    if extracted is not None and extracted != text:
        result = _try_parse(extracted)
        if result is not None:
            return result
        # Try trailing comma fix on extracted text too
        fixed = _fix_trailing_commas(extracted)
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


def _extract_json_object(text: str) -> str | None:
    """Extract the first complete JSON object from text that may contain non-JSON around it.

    Handles cases where Claude adds preamble like 'Here is the analysis:' before the JSON,
    or adds commentary after the closing brace.
    """
    # Find the first { and try to find the matching }
    start = text.find("{")
    if start == -1:
        return None

    # Walk forward counting braces to find the matching close
    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(text)):
        c = text[i]
        if escape:
            escape = False
            continue
        if c == "\\":
            escape = True
            continue
        if c == '"' and not escape:
            in_string = not in_string
            continue
        if in_string:
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]

    # No matching close brace found — return from first { to end (likely truncated)
    return text[start:]
