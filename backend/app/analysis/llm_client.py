"""Unified LLM client supporting Anthropic, OpenAI-compatible, and Groq APIs.

Configured via LLM_BACKEND env var: "anthropic", "openai", or "groq".
OpenAI backend works with Ollama, LMStudio, vLLM, or any OpenAI-compatible server.
Groq backend uses Groq's OpenAI-compatible API for fast inference.
"""

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
    logger.info(
        "LLM call started (backend=%s, model=%s, max_tokens=%d, prompt_len=%d chars)",
        backend, model, max_tokens, len(prompt),
    )
    start = time.monotonic()
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
    except LLMError:
        elapsed = time.monotonic() - start
        logger.error("LLM call failed after %.1fs", elapsed)
        raise


async def _call_anthropic(prompt: str, model: str, max_tokens: int) -> str:
    try:
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
    except anthropic.RateLimitError:
        raise LLMError(
            "Our AI service is temporarily busy. Please try again in a few minutes."
        )
    except anthropic.AuthenticationError:
        logger.error("Anthropic API authentication failed — check ANTHROPIC_API_KEY")
        raise LLMError("AI service configuration error. Please contact support.")
    except anthropic.APIStatusError as e:
        logger.error(f"Anthropic API error {e.status_code}: {e.message}")
        if e.status_code == 529:
            raise LLMError(
                "Our AI service is temporarily overloaded. Please try again in a few minutes."
            )
        raise LLMError("AI service encountered an error. Please try again.")
    except anthropic.APITimeoutError:
        raise LLMError(
            "AI service timed out while analyzing your chapter. "
            "This can happen with very long chapters — please try again."
        )
    except anthropic.APIConnectionError:
        raise LLMError(
            "Could not connect to AI service. Please check your connection and try again."
        )


async def _call_openai(prompt: str, model: str, max_tokens: int) -> str:
    try:
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
    except openai.RateLimitError:
        raise LLMError(
            "Our AI service is temporarily busy. Please try again in a few minutes."
        )
    except openai.AuthenticationError:
        logger.error("OpenAI API authentication failed — check OPENAI_API_KEY")
        raise LLMError("AI service configuration error. Please contact support.")
    except openai.APIStatusError as e:
        logger.error(f"OpenAI API error {e.status_code}: {e.message}")
        raise LLMError("AI service encountered an error. Please try again.")
    except openai.APITimeoutError:
        raise LLMError(
            "AI service timed out while analyzing your chapter. "
            "This can happen with very long chapters — please try again."
        )
    except openai.APIConnectionError:
        raise LLMError(
            "Could not connect to AI service. Please check your connection and try again."
        )


async def _call_groq(prompt: str, model: str, max_tokens: int) -> str:
    capped_tokens = min(max_tokens, GROQ_MAX_TOKENS)
    try:
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
    except openai.RateLimitError:
        raise LLMError(
            "Groq rate limit reached. Please try again in a few minutes."
        )
    except openai.AuthenticationError:
        logger.error("Groq API authentication failed — check GROQ_API_KEY")
        raise LLMError("AI service configuration error. Please contact support.")
    except openai.APIStatusError as e:
        logger.error(f"Groq API error {e.status_code}: {e.message}")
        raise LLMError("AI service encountered an error. Please try again.")
    except openai.APITimeoutError:
        raise LLMError(
            "Groq API timed out. Please try again."
        )
    except openai.APIConnectionError:
        raise LLMError(
            "Could not connect to Groq. Please check your connection and try again."
        )
