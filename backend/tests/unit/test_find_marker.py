"""Unit tests for _find_marker_position in extraction.py.

Tests marker matching with various text formatting edge cases
observed in production with Project Gutenberg books.
"""

import pytest

from app.manuscripts.extraction import _find_marker_position


class TestFindMarkerPosition:
    """Tests for _find_marker_position."""

    def test_standard_marker_match(self):
        """Standard exact marker found at expected position."""
        text = "Some preamble text.\n\nChapter 1: The Beginning\n\nOnce upon a time..."
        pos = _find_marker_position(text, "Chapter 1: The Beginning")
        assert pos != -1
        assert text[pos:].startswith("Chapter 1: The Beginning")

    def test_standard_marker_not_found(self):
        """Returns -1 when marker does not exist in text."""
        text = "No chapters here, just a wall of text."
        pos = _find_marker_position(text, "Chapter 99: Missing")
        assert pos == -1

    def test_gutenberg_underscore_markers(self):
        """Markers wrapped in Gutenberg-style underscores (_Title_) match."""
        text = "Table of Contents\n\n_1. The Dawn_\n\nIt was a dark morning..."
        # The marker without underscores should still match the underscored text
        pos = _find_marker_position(text, "1. The Dawn")
        assert pos != -1
        # The underscored version should also match
        pos2 = _find_marker_position(text, "_1. The Dawn_")
        assert pos2 != -1

    def test_smart_quotes_normalized_to_ascii(self):
        """Smart quotes in marker or text are normalized for matching."""
        # Text has smart quotes, marker has ASCII quotes
        text = "Front matter\n\n\u201cThe Hero\u2019s Journey\u201d\n\nOnce upon..."
        pos = _find_marker_position(text, '"The Hero\'s Journey"')
        assert pos != -1

        # Reverse: marker has smart quotes, text has ASCII
        text2 = 'Front matter\n\n"The Hero\'s Journey"\n\nOnce upon...'
        pos2 = _find_marker_position(text2, "\u201cThe Hero\u2019s Journey\u201d")
        assert pos2 != -1

    def test_number_stripped_variant_matching(self):
        """Number prefix stripped: '1. Title' matches 'Title' in text."""
        text = "Preamble\n\nThe Dawn of Time\n\nIn the beginning there was..."
        # Marker has number prefix, text does not
        pos = _find_marker_position(text, "1. The Dawn of Time")
        assert pos != -1
        assert "The Dawn of Time" in text[pos : pos + 30]

    def test_search_start_offset(self):
        """search_start parameter skips earlier occurrences."""
        text = "Chapter 1: Intro\n\nSome text...\n\nChapter 1: Intro\n\nMore text..."
        first_pos = _find_marker_position(text, "Chapter 1: Intro", 0)
        assert first_pos != -1
        second_pos = _find_marker_position(text, "Chapter 1: Intro", first_pos + 1)
        assert second_pos != -1
        assert second_pos > first_pos

    def test_case_insensitive_fallback(self):
        """Case-insensitive matching catches differently-cased markers."""
        text = "Preamble\n\nCHAPTER ONE: THE BEGINNING\n\nIt was a dark night..."
        pos = _find_marker_position(text, "Chapter One: The Beginning")
        assert pos != -1

    def test_word_boundary_prevents_partial_match(self):
        """'ACT I' should not match inside 'ACT II' or 'ACTING'."""
        text = "Front\n\nACT II\n\nScene text..."
        pos = _find_marker_position(text, "ACT I")
        # ACT I should NOT match ACT II (word boundary check)
        # The regex uses (?!\\w) at the end to prevent this
        assert pos == -1

    def test_whitespace_normalized(self):
        """Extra whitespace in marker or text is collapsed for matching."""
        text = "Preamble\n\nChapter   1:   The   Beginning\n\nOnce upon..."
        pos = _find_marker_position(text, "Chapter 1: The Beginning")
        assert pos != -1
