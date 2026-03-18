"""Tests for LLM client retry logic."""

import asyncio
from unittest.mock import AsyncMock, patch

import openai
import pytest

from app.analysis.llm_client import LLMError, call_llm


@pytest.fixture(autouse=True)
def _use_groq_backend(monkeypatch):
    """Force groq backend with minimal retry delays for fast tests."""
    monkeypatch.setattr("app.analysis.llm_client.settings.llm_backend", "groq")
    monkeypatch.setattr("app.analysis.llm_client.settings.llm_retry_count", 3)
    monkeypatch.setattr("app.analysis.llm_client.settings.llm_retry_base_delay", 0.01)


@pytest.mark.asyncio
async def test_retry_succeeds_after_transient_failures():
    """call_llm should retry on transient errors and return result on success."""
    mock_call = AsyncMock(
        side_effect=[
            openai.RateLimitError(
                message="rate limited",
                response=AsyncMock(status_code=429, headers={}),
                body=None,
            ),
            openai.APIConnectionError(request=AsyncMock()),
            "Success response",
        ]
    )
    with patch("app.analysis.llm_client._call_groq", mock_call):
        result = await call_llm("test prompt", "test-model", 100)

    assert result == "Success response"
    assert mock_call.call_count == 3


@pytest.mark.asyncio
async def test_auth_error_not_retried():
    """call_llm should immediately raise LLMError on auth errors without retrying."""
    mock_call = AsyncMock(
        side_effect=openai.AuthenticationError(
            message="bad key",
            response=AsyncMock(status_code=401, headers={}),
            body=None,
        )
    )
    with patch("app.analysis.llm_client._call_groq", mock_call):
        with pytest.raises(LLMError, match="configuration error"):
            await call_llm("test prompt", "test-model", 100)

    assert mock_call.call_count == 1


@pytest.mark.asyncio
async def test_all_retries_exhausted_raises_llm_error():
    """call_llm should raise LLMError after all retry attempts are exhausted."""
    mock_call = AsyncMock(
        side_effect=openai.RateLimitError(
            message="rate limited",
            response=AsyncMock(status_code=429, headers={}),
            body=None,
        )
    )
    with patch("app.analysis.llm_client._call_groq", mock_call):
        with pytest.raises(LLMError, match="temporarily busy"):
            await call_llm("test prompt", "test-model", 100)

    assert mock_call.call_count == 3
