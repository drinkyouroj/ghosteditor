"""Unit tests for error handling in extraction, story bible, and chapter analysis.

Tests the new error handling added for:
- Empty/tiny text after extraction
- Corrupt DOCX/PDF files
- Claude API error translation (mocked)
- Transient error detection for retry logic
"""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from app.manuscripts.extraction import ExtractionError, extract_text


class TestExtractionErrorHandling:
    def test_empty_txt_raises(self):
        """Empty .txt file should raise ExtractionError."""
        content = b"   \n\n   "
        with pytest.raises(ExtractionError, match="No text could be extracted"):
            extract_text(content, ".txt")

    def test_tiny_txt_raises(self):
        """TXT with fewer than 50 words should raise."""
        content = b"Hello world. This is short."
        with pytest.raises(ExtractionError, match="nearly empty"):
            extract_text(content, ".txt")

    def test_valid_txt_passes(self):
        """TXT with enough words should pass."""
        content = ("Word " * 100).encode("utf-8")
        result = extract_text(content, ".txt")
        assert len(result.split()) >= 50

    def test_unsupported_extension_raises(self):
        with pytest.raises(ExtractionError, match="Unsupported file type"):
            extract_text(b"content", ".rtf")

    def test_non_utf8_txt_raises(self):
        """Non-UTF-8 .txt should raise."""
        content = b"\xff\xfe" + "Hello".encode("utf-16-le")
        with pytest.raises(Exception):
            extract_text(content, ".txt")

    def test_corrupt_docx_raises(self):
        """Non-ZIP bytes labeled as .docx should raise."""
        content = b"This is not a zip file at all"
        with pytest.raises(ExtractionError, match="corrupt|valid"):
            extract_text(content, ".docx")

    def test_invalid_pdf_raises(self):
        """Bytes without %PDF- header should raise."""
        content = b"Not a PDF file content here"
        with pytest.raises(ExtractionError, match="valid PDF|read this PDF"):
            extract_text(content, ".pdf")


class TestTransientErrorDetection:
    """Test the transient error detection logic used by the worker retry system.

    Tests the logic directly (same keywords as worker.TRANSIENT_ERROR_KEYWORDS)
    to avoid importing the worker module which pulls in boto3, arq, and the
    full app stack.
    """

    TRANSIENT_KEYWORDS = ["temporarily busy", "temporarily overloaded", "timed out", "connection"]

    def _is_transient(self, msg: str) -> bool:
        return any(kw in msg.lower() for kw in self.TRANSIENT_KEYWORDS)

    def test_rate_limit_is_transient(self):
        assert self._is_transient("Our AI service is temporarily busy. Please try again.")

    def test_overloaded_is_transient(self):
        assert self._is_transient("Our AI service is temporarily overloaded.")

    def test_timeout_is_transient(self):
        assert self._is_transient("AI service timed out while analyzing your chapter.")

    def test_connection_is_transient(self):
        assert self._is_transient("Could not connect to AI service. Please check your connection.")

    def test_schema_error_is_not_transient(self):
        assert not self._is_transient("Schema validation failed after retry")

    def test_empty_file_is_not_transient(self):
        assert not self._is_transient("No text could be extracted from this file.")

    def test_json_error_is_not_transient(self):
        assert not self._is_transient("Failed to get valid JSON from Claude after retries.")


class TestClaudeApiErrorTranslation:
    """Test that Anthropic API exceptions are caught and translated."""

    @pytest.mark.asyncio
    async def test_rate_limit_translated_bible(self):
        """RateLimitError should become StoryBibleError with user-friendly message."""
        import anthropic
        from app.analysis.story_bible import StoryBibleError, _call_claude

        mock_client = MagicMock()
        mock_client.messages.create = AsyncMock(
            side_effect=anthropic.RateLimitError(
                message="rate limit exceeded",
                response=MagicMock(status_code=429, headers={}),
                body=None,
            )
        )
        with patch("app.analysis.story_bible.anthropic.AsyncAnthropic", return_value=mock_client):
            with pytest.raises(StoryBibleError, match="temporarily busy"):
                await _call_claude("test prompt")

    @pytest.mark.asyncio
    async def test_rate_limit_translated_analysis(self):
        """RateLimitError should become ChapterAnalysisError."""
        import anthropic
        from app.analysis.chapter_analyzer import ChapterAnalysisError, _call_claude

        mock_client = MagicMock()
        mock_client.messages.create = AsyncMock(
            side_effect=anthropic.RateLimitError(
                message="rate limit exceeded",
                response=MagicMock(status_code=429, headers={}),
                body=None,
            )
        )
        with patch("app.analysis.chapter_analyzer.anthropic.AsyncAnthropic", return_value=mock_client):
            with pytest.raises(ChapterAnalysisError, match="temporarily busy"):
                await _call_claude("test prompt")

    @pytest.mark.asyncio
    async def test_auth_error_translated(self):
        """AuthenticationError should become StoryBibleError about config."""
        import anthropic
        from app.analysis.story_bible import StoryBibleError, _call_claude

        mock_client = MagicMock()
        mock_client.messages.create = AsyncMock(
            side_effect=anthropic.AuthenticationError(
                message="invalid api key",
                response=MagicMock(status_code=401, headers={}),
                body=None,
            )
        )
        with patch("app.analysis.story_bible.anthropic.AsyncAnthropic", return_value=mock_client):
            with pytest.raises(StoryBibleError, match="configuration error"):
                await _call_claude("test prompt")

    @pytest.mark.asyncio
    async def test_timeout_error_translated(self):
        """APITimeoutError should become StoryBibleError about timeout."""
        import anthropic
        from app.analysis.story_bible import StoryBibleError, _call_claude

        mock_client = MagicMock()
        mock_client.messages.create = AsyncMock(
            side_effect=anthropic.APITimeoutError(request=MagicMock())
        )
        with patch("app.analysis.story_bible.anthropic.AsyncAnthropic", return_value=mock_client):
            with pytest.raises(StoryBibleError, match="timed out"):
                await _call_claude("test prompt")

    @pytest.mark.asyncio
    async def test_overloaded_error_translated(self):
        """529 Overloaded should become StoryBibleError about overloaded."""
        import anthropic
        from app.analysis.story_bible import StoryBibleError, _call_claude

        mock_client = MagicMock()
        mock_client.messages.create = AsyncMock(
            side_effect=anthropic.APIStatusError(
                message="overloaded",
                response=MagicMock(status_code=529, headers={}),
                body=None,
            )
        )
        with patch("app.analysis.story_bible.anthropic.AsyncAnthropic", return_value=mock_client):
            with pytest.raises(StoryBibleError, match="overloaded"):
                await _call_claude("test prompt")
