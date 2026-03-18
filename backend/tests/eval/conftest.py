# Multi-backend eval configuration for GhostEditor.
#
# Run evals against default backend (from .env):
#   pytest tests/eval/ -v -m api
#
# Run evals against Groq:
#   pytest tests/eval/ -v -m api --llm-backend=groq --llm-model=llama-3.3-70b-versatile
#
# Run evals against Anthropic:
#   pytest tests/eval/ -v -m api --llm-backend=anthropic --llm-model=claude-haiku-4-5-20251001
#
# Results are cached separately per backend in:
#   tests/eval/bible_results/{backend}/
#   tests/eval/analysis_results/{backend}/

import pytest


def pytest_addoption(parser):
    parser.addoption(
        "--llm-backend",
        action="store",
        default=None,  # None = use whatever .env says
        choices=["anthropic", "groq"],
        help="LLM backend to use for eval tests",
    )
    parser.addoption(
        "--llm-model",
        action="store",
        default=None,  # None = use whatever .env says
        help="LLM model to use for eval tests (e.g. llama-3.3-70b-versatile)",
    )


@pytest.fixture(autouse=True)
def configure_llm_backend(request):
    """Override LLM settings for eval tests if CLI flags are provided."""
    backend = request.config.getoption("--llm-backend")
    model = request.config.getoption("--llm-model")
    if backend:
        from app.config import settings
        settings.llm_backend = backend
    if model:
        from app.config import settings
        settings.llm_model_bible = model
        settings.llm_model_analysis = model


def get_backend_name() -> str:
    """Return the current LLM backend name for cache directory scoping.

    This reads from the live settings object, so it reflects any overrides
    applied by the configure_llm_backend fixture or --llm-backend CLI flag.
    """
    from app.config import settings
    return settings.llm_backend
