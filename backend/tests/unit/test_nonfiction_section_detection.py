"""Unit tests for nonfiction section detection (DECISION_008).

Tests header detection (markdown, ALL-CAPS, numbered), chunked fallback,
single header edge case, empty document handling, and mid-paragraph
false positive rejection.
"""

from __future__ import annotations

import pytest

from app.manuscripts.extraction import (
    MIN_NONFICTION_SECTION_WORDS,
    _detect_nonfiction_headers,
    _detect_nonfiction_sections,
    _is_valid_nonfiction_header,
    _nonfiction_chunk_at_paragraphs,
)


# ---------------------------------------------------------------------------
# Header detection tests
# ---------------------------------------------------------------------------


class TestDetectNonfictionHeaders:
    """Test that header patterns are correctly identified."""

    def test_markdown_headers(self):
        text = (
            "\n\n# Introduction\n\n"
            "Some body text here that goes on for a while.\n\n"
            "## Background\n\n"
            "More body text follows this heading.\n\n"
            "### Methodology\n\n"
            "Even more text here.\n"
        )
        headers = _detect_nonfiction_headers(text)
        titles = [h[1] for h in headers]
        assert "# Introduction" in titles
        assert "## Background" in titles
        assert "### Methodology" in titles

    def test_allcaps_headers(self):
        text = (
            "\n\nINTRODUCTION\n\n"
            "This is the opening section with plenty of body text.\n\n"
            "LITERATURE REVIEW\n\n"
            "Here we review the existing literature on the topic.\n\n"
            "METHODOLOGY\n\n"
            "Our research methodology is described below.\n"
        )
        headers = _detect_nonfiction_headers(text)
        titles = [h[1] for h in headers]
        assert "INTRODUCTION" in titles
        assert "LITERATURE REVIEW" in titles
        assert "METHODOLOGY" in titles

    def test_numbered_section_headers(self):
        text = (
            "\n\n1. Introduction\n\n"
            "Body text for the introduction section.\n\n"
            "2. Methods\n\n"
            "Body text for the methods section.\n\n"
            "Section 3: Results\n\n"
            "Body text for the results section.\n"
        )
        headers = _detect_nonfiction_headers(text)
        titles = [h[1] for h in headers]
        assert "1. Introduction" in titles
        assert "2. Methods" in titles
        assert "Section 3: Results" in titles

    def test_rejects_long_lines(self):
        """Lines over 120 chars should not be treated as headers."""
        long_line = "A" * 121
        text = f"\n\n{long_line}\n\nSome body text follows.\n"
        headers = _detect_nonfiction_headers(text)
        assert len(headers) == 0

    def test_deduplicates_overlapping_matches(self):
        """Headers matched by multiple patterns should be deduped."""
        # "1. INTRODUCTION" could match both numbered and ALL-CAPS patterns
        text = "\n\nINTRODUCTION\n\nBody text.\n"
        headers = _detect_nonfiction_headers(text)
        # Should only appear once
        positions = [h[0] for h in headers]
        assert len(positions) == len(set(positions))


# ---------------------------------------------------------------------------
# Mid-paragraph false positive rejection (JUDGE amendment #2)
# ---------------------------------------------------------------------------


class TestMidParagraphRejection:
    """Test that mid-paragraph false positives from PDF line wrapping are rejected."""

    def test_rejects_header_followed_by_lowercase(self):
        """A line that looks like a header but is followed by lowercase text
        on the next line is a mid-paragraph fragment, not a header."""
        text = (
            "\n\nThe results confirm our hypothesis.\n"
            "IMPLICATIONS FOR POLICY\n"
            "are discussed in the following section, where we examine\n"
            "the broader impact.\n"
        )
        headers = _detect_nonfiction_headers(text)
        # "IMPLICATIONS FOR POLICY" should be rejected because next line
        # starts with lowercase "are"
        titles = [h[1] for h in headers]
        assert "IMPLICATIONS FOR POLICY" not in titles

    def test_rejects_header_followed_by_comma(self):
        text = (
            "\n\nSome preceding text.\n\n"
            "IMPORTANT FINDINGS\n"
            ", which we discuss below.\n"
        )
        headers = _detect_nonfiction_headers(text)
        titles = [h[1] for h in headers]
        assert "IMPORTANT FINDINGS" not in titles

    def test_accepts_header_followed_by_uppercase_body(self):
        """A real header followed by normal body text should be accepted."""
        text = (
            "\n\nINTRODUCTION\n\n"
            "This section introduces the main arguments of the paper.\n"
        )
        headers = _detect_nonfiction_headers(text)
        titles = [h[1] for h in headers]
        assert "INTRODUCTION" in titles


# ---------------------------------------------------------------------------
# Chunked fallback tests
# ---------------------------------------------------------------------------


class TestNonfictionChunkedFallback:
    """Test the 1,500-word chunked fallback."""

    def test_short_text_single_chunk(self):
        """Text under 1,800 words should be a single chunk."""
        text = " ".join(["word"] * 500)
        sections = _nonfiction_chunk_at_paragraphs(text)
        assert len(sections) == 1
        assert sections[0]["section_detection_method"] == "chunked"
        assert sections[0]["split_method"] == "nonfiction_chunked"

    def test_long_text_multiple_chunks(self):
        """Text over 1,800 words should be split into multiple chunks."""
        # Create ~4500 words with paragraph breaks
        paragraphs = []
        for i in range(30):
            paragraphs.append(" ".join(["word"] * 150))
        text = "\n\n".join(paragraphs)

        sections = _nonfiction_chunk_at_paragraphs(text)
        assert len(sections) >= 2
        for sec in sections:
            assert sec["section_detection_method"] == "chunked"

    def test_chunks_respect_word_window(self):
        """Each chunk should be roughly within the 1200-1800 word window."""
        paragraphs = []
        for i in range(40):
            paragraphs.append(" ".join(["word"] * 150))
        text = "\n\n".join(paragraphs)

        sections = _nonfiction_chunk_at_paragraphs(text)
        # All sections except possibly the last should be in the window
        for sec in sections[:-1]:
            assert sec["word_count"] >= 900, (
                f"Section too short: {sec['word_count']} words"
            )


# ---------------------------------------------------------------------------
# Full nonfiction section detection tests
# ---------------------------------------------------------------------------


class TestDetectNonfictionSections:
    """Integration tests for the full nonfiction detection pipeline."""

    def test_header_detection_splits_at_headers(self):
        """When 2+ headers are found, split at header positions."""
        text = (
            "\n\n# Introduction\n\n"
            + " ".join(["word"] * 300) + "\n\n"
            + "## Methods\n\n"
            + " ".join(["word"] * 300) + "\n\n"
            + "## Results\n\n"
            + " ".join(["word"] * 300) + "\n"
        )
        sections, warnings = _detect_nonfiction_sections(text)
        assert len(sections) == 3
        assert sections[0]["section_detection_method"] == "header"
        assert sections[0]["title"] == "# Introduction"

    def test_chunked_fallback_when_no_headers(self):
        """When no headers detected, fall back to chunking."""
        # Plain text with no headers, long enough to chunk
        paragraphs = []
        for i in range(30):
            paragraphs.append(" ".join(["word"] * 150))
        text = "\n\n".join(paragraphs)

        sections, warnings = _detect_nonfiction_sections(text)
        assert len(sections) >= 2
        for sec in sections:
            assert sec["section_detection_method"] == "chunked"

    def test_single_header_content_start(self):
        """Single header should be treated as content-start marker."""
        front_matter = "Copyright 2024 by Jane Smith.\nAll rights reserved.\nFor my mother.\n\n"
        body = " ".join(["word"] * 3000)
        text = front_matter + "\n\nINTRODUCTION\n\n" + body

        sections, warnings = _detect_nonfiction_sections(text)
        # Front matter should be stripped
        for sec in sections:
            assert "Copyright 2024" not in sec["text"]
            assert sec["section_detection_method"] == "chunked"
        # Should have a warning about single header
        assert any("Single header" in w for w in warnings)

    def test_empty_document(self):
        """Empty or near-empty text should produce a single section."""
        sections, warnings = _detect_nonfiction_sections("")
        assert len(sections) == 1
        assert sections[0]["word_count"] == 0

    def test_whitespace_collapse_warning(self):
        """When no headers AND no blank lines, warn about whitespace collapse."""
        # Text with no blank lines at all
        text = " ".join(["word"] * 500)
        sections, warnings = _detect_nonfiction_sections(text)
        assert any("lost formatting" in w for w in warnings)

    def test_short_sections_merged(self):
        """Sections under 200 words should be merged with the next."""
        text = (
            "\n\n# Very Short\n\n"
            "Just a few words here.\n\n"
            "# Longer Section\n\n"
            + " ".join(["word"] * 300) + "\n\n"
            "# Another Section\n\n"
            + " ".join(["word"] * 300) + "\n"
        )
        sections, warnings = _detect_nonfiction_sections(text)
        # The short section should be merged into the next
        for sec in sections:
            assert sec["word_count"] >= MIN_NONFICTION_SECTION_WORDS or sec is sections[-1]

    def test_section_numbering_sequential(self):
        """Section chapter_numbers should be sequential starting at 1."""
        text = (
            "\n\n# Section One\n\n"
            + " ".join(["word"] * 300) + "\n\n"
            + "# Section Two\n\n"
            + " ".join(["word"] * 300) + "\n\n"
            + "# Section Three\n\n"
            + " ".join(["word"] * 300) + "\n"
        )
        sections, warnings = _detect_nonfiction_sections(text)
        for i, sec in enumerate(sections):
            assert sec["chapter_number"] == i + 1
