"""Tests for ToC detection and marker matching in _split_by_markers.

Covers:
- ToC re-search: short ToC entries followed by long body chapters
- Number-stripped variant matching (e.g., "3. My Chapter Title" matches body text)
- Smart quote normalization in marker matching
"""

import pytest

from app.manuscripts.extraction import _split_by_markers, _find_marker_position


class TestToCDetectionAndReSearch:
    """When markers first match in a ToC (producing very short chapters),
    _split_by_markers should detect this and re-search sequentially in the body."""

    def test_toc_research_produces_correct_chapter_sizes(self):
        """Build a fake text with a ToC followed by body chapters.
        The ToC has short entries; the body has long content under each heading."""
        # Build ToC block: short references to each chapter
        toc = (
            "TABLE OF CONTENTS\n\n"
            "Chapter I\n"
            "Chapter II\n"
            "Chapter III\n"
            "Chapter IV\n"
            "Chapter V\n\n"
        )

        # Build body with substantial content under each chapter heading
        body_chapters = []
        for i, numeral in enumerate(["I", "II", "III", "IV", "V"], 1):
            heading = f"Chapter {numeral}"
            content = f"This is the content of chapter {i}. " * 200  # ~1400 words
            body_chapters.append(f"{heading}\n\n{content}\n\n")
        body = "\n".join(body_chapters)

        full_text = toc + body

        # Sections as LLM would return them
        sections = [
            {"marker": "Chapter I", "title": "Chapter I"},
            {"marker": "Chapter II", "title": "Chapter II"},
            {"marker": "Chapter III", "title": "Chapter III"},
            {"marker": "Chapter IV", "title": "Chapter IV"},
            {"marker": "Chapter V", "title": "Chapter V"},
        ]

        result = _split_by_markers(full_text, sections, front_matter_end_marker=None)

        # Should have 5 chapters, each with substantial word count
        assert len(result) == 5, f"Expected 5 chapters, got {len(result)}"
        for ch in result:
            assert ch["word_count"] > 100, (
                f"Chapter '{ch['title']}' has only {ch['word_count']} words — "
                f"likely matched in ToC instead of body"
            )

    def test_front_matter_skips_preface(self):
        """When front_matter_end_marker is set, markers before it should be skipped."""
        # Place "Chapter Alpha" in the preface as a false match, and use
        # a distinct front_matter_end_marker that isn't a chapter title
        front_matter = (
            "FOREWORD\n\n"
            "Chapter Alpha is mentioned here in the foreword.\n\n"
            "END_OF_FRONT_MATTER\n\n"
        )
        body = (
            "Chapter Alpha\n\n" + "Body text for alpha. " * 200 + "\n\n"
            "Chapter Beta\n\n" + "Body text for beta. " * 200 + "\n\n"
        )
        full_text = front_matter + body

        sections = [
            {"marker": "Chapter Alpha", "title": "Chapter Alpha"},
            {"marker": "Chapter Beta", "title": "Chapter Beta"},
        ]

        result = _split_by_markers(
            full_text, sections, front_matter_end_marker="END_OF_FRONT_MATTER"
        )

        # Should find the body chapters, not the foreword mention
        assert len(result) == 2
        for ch in result:
            assert ch["word_count"] > 100


class TestNumberStrippedVariantMatching:
    """Test that markers like '3. My Chapter Title' match body text that contains
    only 'My Chapter Title' (without the numbered prefix)."""

    def test_numbered_marker_finds_unnumbered_body(self):
        """The LLM returns '3. My Chapter Title' but the text only has
        'My Chapter Title' (without the number)."""
        text = (
            "Introduction\n\n" + "Intro text. " * 200 + "\n\n"
            "My Chapter Title\n\n" + "Chapter body content here. " * 200 + "\n\n"
            "Another Chapter Title\n\n" + "More body content here. " * 200
        )

        # Marker has number prefix that text doesn't
        pos = _find_marker_position(text, "3. My Chapter Title")
        assert pos != -1, "Number-stripped variant should match"

        # Should find the position of 'My Chapter Title' in the body
        expected_pos = text.find("My Chapter Title")
        assert pos == expected_pos

    def test_numbered_marker_in_full_split(self):
        """End-to-end: LLM returns numbered markers, body text has no numbers."""
        text = (
            "_The Opening_\n\n" + "Opening content goes here. " * 200 + "\n\n"
            "_The Middle Part_\n\n" + "Middle content goes here. " * 200 + "\n\n"
            "_The Final Act_\n\n" + "Final content goes here. " * 200
        )

        sections = [
            {"marker": "1. The Opening", "title": "The Opening"},
            {"marker": "2. The Middle Part", "title": "The Middle Part"},
            {"marker": "3. The Final Act", "title": "The Final Act"},
        ]

        result = _split_by_markers(text, sections, front_matter_end_marker=None)
        assert len(result) == 3
        for ch in result:
            assert ch["word_count"] > 100


class TestSmartQuoteNormalization:
    """Test that smart quotes in markers or text don't prevent matching."""

    def test_smart_quotes_in_marker_match_ascii_in_text(self):
        """LLM returns marker with smart quotes, text has ASCII quotes."""
        text = 'Chapter: "The Beginning"\n\n' + "Content here. " * 200
        # Smart double quotes in marker
        pos = _find_marker_position(text, 'Chapter: \u201cThe Beginning\u201d')
        assert pos != -1, "Smart-quote marker should match ASCII-quote text"

    def test_ascii_quotes_in_marker_match_smart_in_text(self):
        """Text has smart quotes, marker has ASCII."""
        text = 'Chapter: \u201cThe Beginning\u201d\n\n' + "Content here. " * 200
        pos = _find_marker_position(text, 'Chapter: "The Beginning"')
        assert pos != -1, "ASCII-quote marker should match smart-quote text"

    def test_smart_apostrophe_normalization(self):
        """Smart apostrophes should match regular apostrophes."""
        text = "The Hero's Journey\n\n" + "Content here. " * 200
        # Smart apostrophe in marker
        pos = _find_marker_position(text, "The Hero\u2019s Journey")
        assert pos != -1, "Smart apostrophe marker should match ASCII apostrophe text"

    def test_smart_quotes_in_full_split(self):
        """End-to-end: smart quotes in markers, ASCII in body."""
        text = (
            "Alice's Adventure\n\n" + "Alice went to wonderland. " * 200 + "\n\n"
            "Bob's Story\n\n" + "Bob did some things. " * 200
        )

        sections = [
            {"marker": "Alice\u2019s Adventure", "title": "Alice's Adventure"},
            {"marker": "Bob\u2019s Story", "title": "Bob's Story"},
        ]

        result = _split_by_markers(text, sections, front_matter_end_marker=None)
        assert len(result) == 2
        for ch in result:
            assert ch["word_count"] > 100


class TestMarkerMatchThreshold:
    """Test that <50% marker match rate forces fallback."""

    def test_low_match_rate_returns_empty(self):
        """If less than 50% of markers match, return empty list."""
        text = "Chapter One\n\n" + "Content. " * 200

        sections = [
            {"marker": "Chapter One", "title": "Chapter One"},
            {"marker": "Hallucinated Chapter Two", "title": "Chapter Two"},
            {"marker": "Hallucinated Chapter Three", "title": "Chapter Three"},
            {"marker": "Hallucinated Chapter Four", "title": "Chapter Four"},
            {"marker": "Hallucinated Chapter Five", "title": "Chapter Five"},
        ]

        result = _split_by_markers(text, sections, front_matter_end_marker=None)
        # Only 1 out of 5 markers matches = 20%, should return empty
        assert result == []

    def test_high_match_rate_proceeds(self):
        """If >= 50% of markers match, proceed with split."""
        text = (
            "Chapter One\n\n" + "Content one. " * 200 + "\n\n"
            "Chapter Two\n\n" + "Content two. " * 200 + "\n\n"
            "Chapter Three\n\n" + "Content three. " * 200
        )

        sections = [
            {"marker": "Chapter One", "title": "Chapter One"},
            {"marker": "Chapter Two", "title": "Chapter Two"},
            {"marker": "Chapter Three", "title": "Chapter Three"},
            {"marker": "Hallucinated Chapter Four", "title": "Chapter Four"},
        ]

        result = _split_by_markers(text, sections, front_matter_end_marker=None)
        # 3 out of 4 markers match = 75%, should proceed
        assert len(result) == 3
