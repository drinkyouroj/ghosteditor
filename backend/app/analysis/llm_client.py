"""Unified LLM client supporting Anthropic, OpenAI-compatible, and Groq APIs.

Configured via LLM_BACKEND env var: "anthropic", "openai", or "groq".
OpenAI backend works with Ollama, LMStudio, vLLM, or any OpenAI-compatible server.
Groq backend uses Groq's OpenAI-compatible API for fast inference.
"""

import asyncio
import logging
import time

import anthropic
import openai

from app.config import settings

logger = logging.getLogger(__name__)

TIMEOUT_DEFAULT = 1800.0  # 30 minutes — local models can be slow
TIMEOUT_GROQ = 120.0  # 2 minutes — Groq is fast

GROQ_BASE_URL = "https://api.groq.com/openai/v1"
GROQ_MAX_TOKENS = 32768  # Groq's max output token limit


class LLMError(Exception):
    """Raised when an LLM API call fails in a user-facing way."""
    pass


def _is_retryable(exc: Exception) -> bool:
    """Return True if the exception is transient and should be retried."""
    # Anthropic retryable errors
    if isinstance(exc, (anthropic.RateLimitError, anthropic.APITimeoutError, anthropic.APIConnectionError)):
        return True
    if isinstance(exc, anthropic.APIStatusError) and exc.status_code in (429, 529):
        return True

    # OpenAI / Groq retryable errors (Groq uses the openai SDK)
    if isinstance(exc, (openai.RateLimitError, openai.APITimeoutError, openai.APIConnectionError)):
        return True

    return False


def _is_auth_error(exc: Exception) -> bool:
    """Return True if the exception is an authentication error (never retry)."""
    return isinstance(exc, (anthropic.AuthenticationError, openai.AuthenticationError))


def _to_llm_error(exc: Exception) -> LLMError:
    """Convert a raw SDK exception into a user-friendly LLMError."""
    # Auth errors
    if isinstance(exc, anthropic.AuthenticationError):
        logger.error("Anthropic API authentication failed — check ANTHROPIC_API_KEY")
        return LLMError("AI service configuration error. Please contact support.")
    if isinstance(exc, openai.AuthenticationError):
        logger.error("OpenAI/Groq API authentication failed — check API key")
        return LLMError("AI service configuration error. Please contact support.")

    # Rate limit
    if isinstance(exc, anthropic.RateLimitError):
        return LLMError("Our AI service is temporarily busy. Please try again in a few minutes.")
    if isinstance(exc, openai.RateLimitError):
        return LLMError("Our AI service is temporarily busy. Please try again in a few minutes.")

    # Timeout
    if isinstance(exc, anthropic.APITimeoutError):
        return LLMError(
            "AI service timed out while analyzing your chapter. "
            "This can happen with very long chapters — please try again."
        )
    if isinstance(exc, openai.APITimeoutError):
        return LLMError(
            "AI service timed out while analyzing your chapter. "
            "This can happen with very long chapters — please try again."
        )

    # Connection
    if isinstance(exc, anthropic.APIConnectionError):
        return LLMError("Could not connect to AI service. Please check your connection and try again.")
    if isinstance(exc, openai.APIConnectionError):
        return LLMError("Could not connect to AI service. Please check your connection and try again.")

    # Anthropic overloaded (529)
    if isinstance(exc, anthropic.APIStatusError) and exc.status_code == 529:
        return LLMError("Our AI service is temporarily overloaded. Please try again in a few minutes.")

    # Generic API status errors
    if isinstance(exc, anthropic.APIStatusError):
        logger.error("Anthropic API error %d: %s", exc.status_code, exc.message)
        return LLMError("AI service encountered an error. Please try again.")
    if isinstance(exc, openai.APIStatusError):
        logger.error("OpenAI/Groq API error %d: %s", exc.status_code, exc.message)
        return LLMError("AI service encountered an error. Please try again.")

    # Fallback
    return LLMError(f"Unexpected AI service error: {exc}")


async def call_llm(prompt: str, model: str, max_tokens: int) -> str:
    """Call the configured LLM backend and return the text response.

    Args:
        prompt: The user message to send.
        model: Model name (e.g. "claude-haiku-4-5-20251001" or "qwen2.5:14b").
        max_tokens: Maximum tokens in the response.

    Returns:
        The model's text response.

    Raises:
        LLMError: On any API failure, with a user-friendly message.
    """
    backend = settings.llm_backend
    max_attempts = settings.llm_retry_count
    base_delay = settings.llm_retry_base_delay

    logger.info(
        "LLM call started (backend=%s, model=%s, max_tokens=%d, prompt_len=%d chars)",
        backend, model, max_tokens, len(prompt),
    )
    start = time.monotonic()
    last_exc: Exception | None = None

    for attempt in range(1, max_attempts + 1):
        try:
            if backend == "groq":
                result = await _call_groq(prompt, model, max_tokens)
            elif backend == "openai":
                result = await _call_openai(prompt, model, max_tokens)
            else:
                result = await _call_anthropic(prompt, model, max_tokens)

            elapsed = time.monotonic() - start
            logger.info(
                "LLM call completed in %.1fs (response_len=%d chars)", elapsed, len(result)
            )
            return result

        except Exception as exc:
            last_exc = exc
            elapsed = time.monotonic() - start

            # Never retry auth errors
            if _is_auth_error(exc):
                logger.error("LLM auth error after %.1fs, not retrying", elapsed)
                raise _to_llm_error(exc) from exc

            # Retry transient errors
            if _is_retryable(exc) and attempt < max_attempts:
                delay = base_delay * (2 ** (attempt - 1))
                logger.warning(
                    "LLM call attempt %d/%d failed (%.1fs elapsed): %s. "
                    "Retrying in %.1fs...",
                    attempt, max_attempts, elapsed, exc, delay,
                )
                await asyncio.sleep(delay)
                continue

            # Non-retryable or final attempt
            logger.error(
                "LLM call failed after %d attempt(s) (%.1fs elapsed): %s",
                attempt, elapsed, exc,
            )
            raise _to_llm_error(exc) from exc

    # Should not reach here, but just in case
    raise _to_llm_error(last_exc) from last_exc  # type: ignore[arg-type]


async def _call_anthropic(prompt: str, model: str, max_tokens: int) -> str:
    client_kwargs = {
        "api_key": settings.anthropic_api_key,
        "timeout": TIMEOUT_DEFAULT,
    }
    if settings.anthropic_base_url:
        client_kwargs["base_url"] = settings.anthropic_base_url
    client = anthropic.AsyncAnthropic(**client_kwargs)
    message = await client.messages.create(
        model=model,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    return message.content[0].text


async def _call_openai(prompt: str, model: str, max_tokens: int) -> str:
    client_kwargs = {
        "api_key": settings.openai_api_key or "none",
        "timeout": TIMEOUT_DEFAULT,
    }
    if settings.openai_base_url:
        client_kwargs["base_url"] = settings.openai_base_url
    client = openai.AsyncOpenAI(**client_kwargs)
    response = await client.chat.completions.create(
        model=model,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.choices[0].message.content


async def _call_groq(prompt: str, model: str, max_tokens: int) -> str:
    capped_tokens = min(max_tokens, GROQ_MAX_TOKENS)
    client = openai.AsyncOpenAI(
        api_key=settings.groq_api_key,
        base_url=GROQ_BASE_URL,
        timeout=TIMEOUT_GROQ,
    )
    response = await client.chat.completions.create(
        model=model,
        max_tokens=capped_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.choices[0].message.content
