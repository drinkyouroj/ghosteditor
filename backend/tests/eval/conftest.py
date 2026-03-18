"""Eval test configuration.

Provides an autouse fixture that skips @pytest.mark.api tests when no LLM API
key is available in the environment. The 'api' mark itself is registered in
backend/pytest.ini to avoid unknown-marker warnings.
"""

import os

import pytest


@pytest.fixture(autouse=True)
def skip_without_api_key(request):
    """Skip tests marked with @pytest.mark.api if no API key is set.

    Checks for ANTHROPIC_API_KEY and GROQ_API_KEY. If neither is present,
    the test is skipped with an informative message.
    """
    if request.node.get_closest_marker("api") is None:
        return
    has_anthropic = bool(os.environ.get("ANTHROPIC_API_KEY"))
    has_groq = bool(os.environ.get("GROQ_API_KEY"))
    if not has_anthropic and not has_groq:
        pytest.skip(
            "Skipped: no ANTHROPIC_API_KEY or GROQ_API_KEY set in environment"
        )
