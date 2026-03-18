"""Eval tests: story bible generation via Claude API on Gutenberg samples.

Runs generate_story_bible() against the first chapter of 5 genre samples.
Validates JSON structure, schema compliance, and content-level expectations.

These tests make real Claude API calls and cost real money. They are marked
with @pytest.mark.api so they can be run selectively:
    pytest tests/eval/test_story_bible_generation.py -v -m api
"""

import asyncio
import json
import pytest
from pathlib import Path

from app.analysis.story_bible import generate_story_bible
from app.analysis.bible_schema import StoryBibleSchema
from app.manuscripts.extraction import detect_chapters_sync as detect_chapters

from tests.eval.conftest import get_backend_name

SAMPLES_DIR = Path(__file__).parent / "samples"
RESULTS_DIR_BASE = Path(__file__).parent / "bible_results"


def _results_dir() -> Path:
    """Return the backend-scoped results directory."""
    return RESULTS_DIR_BASE / get_backend_name()

START_MARKER = "*** START OF THE PROJECT GUTENBERG EBOOK"
END_MARKER = "*** END OF THE PROJECT GUTENBERG EBOOK"

GENRE_MAP = {
    "pride_and_prejudice_full.txt": ("Romance", "romance"),
    "princess_of_mars_full.txt": ("Fantasy / Science Fiction", "fantasy"),
    "moby_dick_full.txt": ("Literary Fiction", "literary"),
    "thirty_nine_steps_full.txt": ("Thriller", "thriller"),
    "hound_of_baskervilles_full.txt": ("Mystery", "mystery"),
}

# Expected characters per book (at minimum, these should be found)
EXPECTED_CHARACTERS = {
    "romance": ["bennet"],
    "fantasy": ["carter"],
    "literary": ["ishmael"],
    "mystery": ["holmes", "watson"],
    "thriller": ["hannay", "scudder", "richard"],
}

# Expected voice profiles
EXPECTED_VOICE = {
    "romance": {"pov_contains": "third", "tense_contains": "past"},
    "fantasy": {"pov_contains": "first", "tense_contains": "past"},
    "literary": {"pov_contains": "first", "tense_contains": "past"},  # Moby Dick is predominantly past tense
    "mystery": {"pov_contains": "first", "tense_contains": "past"},
    "thriller": {"pov_contains": "first", "tense_contains": "past"},
}


def _load_first_chapter(filename: str) -> str:
    """Load a full Gutenberg text, strip headers, extract first narrative chapter.

    For books with extensive front matter (TOC, etymology sections), finds the
    first chapter that contains actual narrative content.
    """
    path = SAMPLES_DIR / filename
    if not path.exists():
        pytest.skip(f"Sample file {filename} not found")
    text = path.read_text(encoding="utf-8-sig")
    start = text.find(START_MARKER)
    if start != -1:
        newline = text.find("\n", start)
        text = text[newline + 1:]
    end = text.find(END_MARKER)
    if end != -1:
        text = text[:end]
    text = text.strip()
    chapters = detect_chapters(text)

    # The first detected chapter may be front matter (TOC, title page).
    # Use the first chapter with a title matching "CHAPTER 1" or similar,
    # or fall back to the first chapter with >1000 words of narrative.
    for ch in chapters:
        title = (ch.get("title") or "").strip().upper()
        if title in ("CHAPTER 1", "CHAPTER I", "CHAPTER 1."):
            return ch["text"]
        # Match "CHAPTER 1. Loomings" etc.
        if title.startswith("CHAPTER 1.") or title.startswith("CHAPTER 1 "):
            return ch["text"]
        if title.startswith("CHAPTER I.") or title.startswith("CHAPTER I "):
            return ch["text"]

    # No explicit "Chapter 1" found — use the first chapter with substantial text.
    # Pre-header text (untitled) is fine if it has real narrative (>1000 words).
    return chapters[0]["text"]


def _save_result(genre_key: str, bible: StoryBibleSchema):
    """Save bible result for manual review (scoped by LLM backend)."""
    results_dir = _results_dir()
    results_dir.mkdir(parents=True, exist_ok=True)
    path = results_dir / f"{genre_key}_bible.json"
    path.write_text(json.dumps(bible.model_dump(), indent=2))


# Generate all bibles once at module level using asyncio.run
_bibles_cache = None


def _get_bibles():
    global _bibles_cache
    if _bibles_cache is not None:
        return _bibles_cache

    async def _generate_all():
        results = {}
        for filename, (genre, key) in GENRE_MAP.items():
            chapter_text = _load_first_chapter(filename)
            bible, warnings = await generate_story_bible(
                chapter_text=chapter_text,
                chapter_number=1,
                genre=genre,
            )
            results[key] = {"bible": bible, "warnings": warnings}
            _save_result(key, bible)
            print(f"  Generated {key} bible: {len(bible.characters)} characters, "
                  f"{len(bible.timeline)} events, {len(bible.settings)} settings")
        return results

    _bibles_cache = asyncio.run(_generate_all())
    return _bibles_cache


@pytest.fixture(scope="module")
def bibles():
    return _get_bibles()


# --- Structure tests (all genres) ---

@pytest.mark.api
def test_all_produce_valid_schema(bibles):
    for key, result in bibles.items():
        assert isinstance(result["bible"], StoryBibleSchema), f"{key}: not a valid StoryBibleSchema"


@pytest.mark.api
def test_all_have_characters(bibles):
    for key, result in bibles.items():
        bible = result["bible"]
        assert len(bible.characters) >= 1, f"{key}: no characters extracted"


@pytest.mark.api
def test_all_have_timeline(bibles):
    for key, result in bibles.items():
        bible = result["bible"]
        assert len(bible.timeline) >= 1, f"{key}: no timeline events extracted"


@pytest.mark.api
def test_all_have_settings(bibles):
    for key, result in bibles.items():
        bible = result["bible"]
        assert len(bible.settings) >= 1, f"{key}: no settings extracted"


@pytest.mark.api
def test_all_have_voice_profile(bibles):
    for key, result in bibles.items():
        bible = result["bible"]
        assert bible.voice_profile.pov, f"{key}: empty POV"
        assert bible.voice_profile.tense, f"{key}: empty tense"
        assert bible.voice_profile.tone, f"{key}: empty tone"


@pytest.mark.api
def test_all_have_plot_threads(bibles):
    for key, result in bibles.items():
        bible = result["bible"]
        assert len(bible.plot_threads) >= 1, f"{key}: no plot threads extracted"


# --- Content tests (genre-specific) ---

@pytest.mark.api
def test_romance_characters(bibles):
    """Pride & Prejudice should mention the Bennet family."""
    bible = bibles["romance"]["bible"]
    names = " ".join(c.name.lower() for c in bible.characters)
    assert any(exp in names for exp in EXPECTED_CHARACTERS["romance"]), (
        f"Expected Bennet family in: {[c.name for c in bible.characters]}"
    )


@pytest.mark.api
def test_mystery_characters(bibles):
    """Hound of the Baskervilles should mention Holmes and Watson."""
    bible = bibles["mystery"]["bible"]
    names = " ".join(c.name.lower() for c in bible.characters)
    assert "holmes" in names, f"Holmes not found in: {[c.name for c in bible.characters]}"
    assert "watson" in names, f"Watson not found in: {[c.name for c in bible.characters]}"


@pytest.mark.api
def test_literary_narrator(bibles):
    """Moby Dick should identify Ishmael as narrator."""
    bible = bibles["literary"]["bible"]
    names = " ".join(c.name.lower() for c in bible.characters)
    assert "ishmael" in names, f"Ishmael not found in: {[c.name for c in bible.characters]}"


@pytest.mark.api
def test_thriller_has_characters(bibles):
    """The Thirty-Nine Steps should find key characters."""
    bible = bibles["thriller"]["bible"]
    names = " ".join(c.name.lower() for c in bible.characters)
    assert any(exp in names for exp in EXPECTED_CHARACTERS["thriller"]), (
        f"Expected key thriller characters in: {[c.name for c in bible.characters]}"
    )


@pytest.mark.api
def test_fantasy_characters(bibles):
    """Princess of Mars should find John Carter."""
    bible = bibles["fantasy"]["bible"]
    names = " ".join(c.name.lower() for c in bible.characters)
    assert any(exp in names for exp in EXPECTED_CHARACTERS["fantasy"]), (
        f"Expected Carter in: {[c.name for c in bible.characters]}"
    )


# --- Voice profile tests ---

@pytest.mark.api
def test_voice_profiles_correct(bibles):
    """Check POV and tense detection for each genre."""
    for key, expected in EXPECTED_VOICE.items():
        bible = bibles[key]["bible"]
        pov = bible.voice_profile.pov.lower()
        tense = bible.voice_profile.tense.lower()
        assert expected["pov_contains"] in pov, (
            f"{key}: expected POV containing '{expected['pov_contains']}', got '{pov}'"
        )
        assert expected["tense_contains"] in tense, (
            f"{key}: expected tense containing '{expected['tense_contains']}', got '{tense}'"
        )


# --- Character quality tests ---

@pytest.mark.api
def test_no_hallucinated_protagonists(bibles):
    """Characters marked as protagonist should be reasonable in count.
    Some ensemble chapters (e.g. P&P Ch1) may not have a clear protagonist,
    so we only check that there aren't too many.
    """
    for key, result in bibles.items():
        bible = result["bible"]
        protagonists = [c for c in bible.characters if c.role == "protagonist"]
        assert len(protagonists) <= 3, (
            f"{key}: too many protagonists ({len(protagonists)}): "
            f"{[p.name for p in protagonists]}"
        )
        # At least one character should have a significant role
        significant = [c for c in bible.characters
                       if c.role in ("protagonist", "supporting")]
        assert len(significant) >= 1, (
            f"{key}: no protagonist or supporting characters identified"
        )


@pytest.mark.api
def test_characters_have_descriptions(bibles):
    """Major characters should have descriptions."""
    for key, result in bibles.items():
        bible = result["bible"]
        major = [c for c in bible.characters if c.role in ("protagonist", "antagonist", "supporting")]
        for char in major:
            assert char.description, (
                f"{key}: {char.name} ({char.role}) has no description"
            )


# --- JSON round-trip test ---

@pytest.mark.api
def test_bibles_json_roundtrip(bibles):
    """All bibles should survive JSON serialization/deserialization."""
    for key, result in bibles.items():
        bible = result["bible"]
        dumped = json.dumps(bible.model_dump())
        loaded = json.loads(dumped)
        reconstructed = StoryBibleSchema.model_validate(loaded)
        assert len(reconstructed.characters) == len(bible.characters), (
            f"{key}: character count mismatch after roundtrip"
        )
