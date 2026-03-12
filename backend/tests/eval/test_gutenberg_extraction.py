"""Eval tests: chapter detection and text extraction on Gutenberg samples.

Tests the extraction pipeline against 5 real manuscripts spanning
romance, fantasy, literary fiction, thriller, and mystery genres.
"""

import pytest
from pathlib import Path

from app.manuscripts.extraction import detect_chapters

SAMPLES_DIR = Path(__file__).parent / "samples"

START_MARKER = "*** START OF THE PROJECT GUTENBERG EBOOK"
END_MARKER = "*** END OF THE PROJECT GUTENBERG EBOOK"


def _load_and_strip(filename: str) -> str:
    path = SAMPLES_DIR / filename
    if not path.exists():
        pytest.skip(f"Sample file {filename} not found — run extract_samples.py first")
    text = path.read_text(encoding="utf-8-sig")
    start = text.find(START_MARKER)
    if start != -1:
        newline = text.find("\n", start)
        text = text[newline + 1:]
    end = text.find(END_MARKER)
    if end != -1:
        text = text[:end]
    return text.strip()


class TestPrideAndPrejudice:
    """Romance: simple cast, linear timeline, standard chapter headers."""

    @pytest.fixture
    def text(self):
        return _load_and_strip("pride_and_prejudice_full.txt")

    def test_detects_chapters(self, text):
        chapters = detect_chapters(text)
        # P&P has 61 chapters in this edition (pre-header text + Chapters II-LXI)
        assert len(chapters) >= 55
        assert len(chapters) <= 65

    def test_pre_header_text_captured(self, text):
        """Chapter 1 content before the first CHAPTER II header should be captured."""
        chapters = detect_chapters(text)
        # The first chapter should contain the famous opening line
        assert "truth universally acknowledged" in chapters[0]["text"].lower() or \
               any("truth universally acknowledged" in ch["text"].lower() for ch in chapters[:2])

    def test_no_tiny_chapters(self, text):
        chapters = detect_chapters(text)
        for ch in chapters:
            assert ch["word_count"] >= 200, f"Chapter {ch['chapter_number']} has only {ch['word_count']} words"


class TestPrincessOfMars:
    """Fantasy: large cast, world-building, invented proper nouns."""

    @pytest.fixture
    def text(self):
        return _load_and_strip("princess_of_mars_full.txt")

    def test_detects_chapters(self, text):
        chapters = detect_chapters(text)
        assert len(chapters) >= 25
        assert len(chapters) <= 35

    def test_reasonable_word_counts(self, text):
        chapters = detect_chapters(text)
        total = sum(ch["word_count"] for ch in chapters)
        assert 60_000 <= total <= 75_000


class TestMobyDick:
    """Literary fiction: 135+ chapters, has a table of contents that must be filtered."""

    @pytest.fixture
    def text(self):
        return _load_and_strip("moby_dick_full.txt")

    def test_detects_many_chapters(self, text):
        chapters = detect_chapters(text)
        # Moby Dick has 135 real chapters + some from Cetology references
        # Should detect >100 now that cap is raised to 150
        assert len(chapters) >= 100

    def test_toc_not_included_as_chapters(self, text):
        """TOC entries (< 50 words) should be filtered out."""
        chapters = detect_chapters(text)
        for ch in chapters:
            # No chapter should be just a TOC line
            assert ch["word_count"] >= 50, (
                f"Chapter {ch['chapter_number']} has only {ch['word_count']} words — "
                "likely a TOC entry that wasn't filtered"
            )

    def test_first_chapter_has_content(self, text):
        chapters = detect_chapters(text)
        # First real chapter (Loomings) starts with "Call me Ishmael"
        found = any("call me ishmael" in ch["text"].lower() for ch in chapters[:5])
        assert found, "Expected 'Call me Ishmael' in early chapters"


class TestThirtyNineSteps:
    """Thriller: first person, fast pacing, chapter-level suspense."""

    @pytest.fixture
    def text(self):
        return _load_and_strip("thirty_nine_steps_full.txt")

    def test_detects_chapters(self, text):
        chapters = detect_chapters(text)
        # The Thirty-Nine Steps has ~10 chapters
        assert len(chapters) >= 5
        assert len(chapters) <= 15

    def test_no_empty_chapters(self, text):
        chapters = detect_chapters(text)
        for ch in chapters:
            assert ch["word_count"] >= 200


class TestHoundOfBaskervilles:
    """Mystery: information withheld, multiple investigations, clean chapter structure."""

    @pytest.fixture
    def text(self):
        return _load_and_strip("hound_of_baskervilles_full.txt")

    def test_detects_chapters(self, text):
        chapters = detect_chapters(text)
        # Hound has 15 chapters
        assert len(chapters) >= 13
        assert len(chapters) <= 17

    def test_first_chapter_has_holmes(self, text):
        chapters = detect_chapters(text)
        # Chapter 1 should mention Holmes or Watson
        first_text = chapters[0]["text"].lower()
        assert "holmes" in first_text or "watson" in first_text

    def test_total_word_count(self, text):
        chapters = detect_chapters(text)
        total = sum(ch["word_count"] for ch in chapters)
        assert 55_000 <= total <= 65_000
