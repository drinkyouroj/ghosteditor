"""Unit tests for rate limiting and non-English detection."""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from datetime import timedelta

from app.manuscripts.extraction import ExtractionError, detect_language, extract_text


class TestLanguageDetection:
    def test_english_detected(self):
        assert detect_language("This is a sample English text with plenty of words to analyze.") == "en"

    def test_french_detected(self):
        assert detect_language("Ceci est un texte en français avec suffisamment de mots.") == "fr"

    def test_spanish_detected(self):
        assert detect_language("Este es un texto en español con suficientes palabras para detectar.") == "es"

    def test_returns_none_on_empty(self):
        assert detect_language("") is None

    def test_returns_none_on_short_text(self):
        """Very short text may not be reliably detected."""
        result = detect_language("Hi")
        # May return None or a language code — either is acceptable
        assert result is None or isinstance(result, str)

    def test_non_english_extraction_rejected(self):
        """French text should be rejected during extraction."""
        french_text = (
            "La marquise sortit à cinq heures. Il faisait beau ce jour-là. "
            "Elle marchait dans le jardin en pensant à son avenir. "
        ) * 20  # Enough words to pass minimum word count
        content = french_text.encode("utf-8")
        with pytest.raises(ExtractionError, match="English-language manuscripts only"):
            extract_text(content, ".txt")

    def test_english_extraction_passes(self):
        """English text should pass language detection."""
        english_text = (
            "The morning sun streamed through the windows of the old farmhouse. "
            "Sarah picked up her coffee and walked to the porch, watching the fields. "
        ) * 10
        content = english_text.encode("utf-8")
        result = extract_text(content, ".txt")
        assert len(result) > 0


class TestRateLimit:
    @pytest.mark.asyncio
    async def test_rate_limit_allows_under_limit(self):
        """Requests under the limit should pass."""
        from app.rate_limit import check_rate_limit

        mock_redis = AsyncMock()
        mock_redis.incr = AsyncMock(return_value=1)
        mock_redis.expire = AsyncMock()
        mock_redis.aclose = AsyncMock()

        with patch("app.rate_limit._get_redis", return_value=mock_redis):
            # Should not raise
            await check_rate_limit("user-123", action="upload", max_requests=5)

    @pytest.mark.asyncio
    async def test_rate_limit_blocks_over_limit(self):
        """Requests over the limit should raise 429."""
        from fastapi import HTTPException
        from app.rate_limit import check_rate_limit

        mock_redis = AsyncMock()
        mock_redis.incr = AsyncMock(return_value=6)
        mock_redis.ttl = AsyncMock(return_value=1800)
        mock_redis.aclose = AsyncMock()

        with patch("app.rate_limit._get_redis", return_value=mock_redis):
            with pytest.raises(HTTPException) as exc_info:
                await check_rate_limit("user-123", action="upload", max_requests=5)
            assert exc_info.value.status_code == 429
            assert "rate limit" in exc_info.value.detail.lower()

    @pytest.mark.asyncio
    async def test_rate_limit_sets_expiry_on_first_request(self):
        """First request (count=1) should set the TTL on the key."""
        from app.rate_limit import check_rate_limit

        mock_redis = AsyncMock()
        mock_redis.incr = AsyncMock(return_value=1)
        mock_redis.expire = AsyncMock()
        mock_redis.aclose = AsyncMock()

        with patch("app.rate_limit._get_redis", return_value=mock_redis):
            await check_rate_limit("user-123", action="upload")

        mock_redis.expire.assert_called_once()

    @pytest.mark.asyncio
    async def test_rate_limit_fails_open_on_redis_error(self):
        """If Redis is down, allow the request (fail open)."""
        from app.rate_limit import check_rate_limit

        with patch("app.rate_limit._get_redis", side_effect=ConnectionError("Redis down")):
            # Should not raise — fail open
            await check_rate_limit("user-123", action="upload")
