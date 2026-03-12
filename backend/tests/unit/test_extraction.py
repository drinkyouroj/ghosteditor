import pytest

from app.manuscripts.extraction import (
    ExtractionError,
    check_word_count,
    detect_chapters,
    extract_text_from_txt,
)


class TestChapterDetection:
    def test_no_chapters_returns_single(self):
        text = "This is a story with no chapter headers. " * 100
        chapters = detect_chapters(text)
        assert len(chapters) == 1
        assert chapters[0]["chapter_number"] == 1

    def test_standard_chapter_headers(self):
        text = "Chapter 1\n\n" + ("Word " * 300) + "\n\nChapter 2\n\n" + ("Word " * 300)
        chapters = detect_chapters(text)
        assert len(chapters) == 2
        assert chapters[0]["chapter_number"] == 1
        assert chapters[1]["chapter_number"] == 2

    def test_case_insensitive_chapters(self):
        text = "CHAPTER ONE\n\n" + ("Word " * 300) + "\n\nCHAPTER TWO\n\n" + ("Word " * 300)
        chapters = detect_chapters(text)
        assert len(chapters) == 2

    def test_short_chapters_merged(self):
        """Chapters < 200 words should be merged with the next chapter."""
        text = "Chapter 1\n\nShort.\n\nChapter 2\n\n" + ("Word " * 300)
        chapters = detect_chapters(text)
        assert len(chapters) == 1  # Short Chapter 1 merged into Chapter 2

    def test_max_chapters_cap(self):
        """More than 150 detected chapters should fall back to single chapter."""
        parts = []
        for i in range(160):
            parts.append(f"Chapter {i + 1}\n\n" + ("Word " * 250))
        text = "\n\n".join(parts)
        chapters = detect_chapters(text)
        assert len(chapters) == 1  # Fell back to single chapter

    def test_word_count_calculated(self):
        text = "Chapter 1\n\n" + ("Word " * 500)
        chapters = detect_chapters(text)
        assert chapters[0]["word_count"] >= 500


class TestWordCountLimit:
    def test_under_limit_passes(self):
        chapters = [{"word_count": 50_000}]
        total = check_word_count(chapters)
        assert total == 50_000

    def test_over_limit_raises(self):
        chapters = [{"word_count": 130_000}]
        with pytest.raises(ExtractionError, match="120,000 word limit"):
            check_word_count(chapters)


class TestTxtExtraction:
    def test_valid_utf8(self):
        content = "Hello, world! This is a test.".encode("utf-8")
        result = extract_text_from_txt(content)
        assert "Hello" in result

    def test_unicode_content(self):
        content = "Héllo, wörld! Ñoño.".encode("utf-8")
        result = extract_text_from_txt(content)
        assert "Héllo" in result
