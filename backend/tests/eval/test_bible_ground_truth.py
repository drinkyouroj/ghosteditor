"""Eval tests: story bible generation against manually curated ground truth.

Generates story bibles incrementally across the first 3 chapters of each
Gutenberg sample, then compares against hand-written ground truth JSON files
for character recall, voice profile accuracy, plot thread coverage, setting
recall, and protagonist correctness.

These tests make real Claude API calls and cost real money. They are marked
with @pytest.mark.api so they can be run selectively:
    pytest tests/eval/test_bible_ground_truth.py -v -m api
"""

import asyncio
import json
import re
import pytest
from pathlib import Path

from app.analysis.story_bible import generate_story_bible
from app.analysis.bible_schema import StoryBibleSchema
from app.manuscripts.extraction import detect_chapters_sync as detect_chapters

SAMPLES_DIR = Path(__file__).parent / "samples"
GROUND_TRUTH_DIR = Path(__file__).parent / "ground_truth"
RESULTS_DIR = Path(__file__).parent / "bible_results"

START_MARKER = "*** START OF THE PROJECT GUTENBERG EBOOK"
END_MARKER = "*** END OF THE PROJECT GUTENBERG EBOOK"

GENRE_MAP = {
    "pride_and_prejudice_full.txt": ("Romance", "romance"),
    "time_machine_full.txt": ("Fantasy", "fantasy"),
    "riddle_of_sands_full.txt": ("Thriller", "thriller"),
    "great_gatsby_full.txt": ("Literary Fiction", "literary"),
    "moonstone_full.txt": ("Mystery", "mystery"),
}

GROUND_TRUTH_FILES = {
    "romance": "romance_bible.json",
    "fantasy": "fantasy_bible.json",
    "literary": "literary_bible.json",
    "thriller": "thriller_bible.json",
    "mystery": "mystery_bible.json",
}

NUM_CHAPTERS = 3


def _strip_gutenberg(text: str) -> str:
    """Strip Gutenberg header and footer."""
    start = text.find(START_MARKER)
    if start != -1:
        newline = text.find("\n", start)
        text = text[newline + 1:]
    end = text.find(END_MARKER)
    if end != -1:
        text = text[:end]
    return text.strip()


def _split_time_machine(text: str, count: int) -> list[dict]:
    """Custom chapter splitting for The Time Machine.

    Section markers are formatted as " I." (space + Roman numeral + period)
    at the start of a line.
    """
    pattern = re.compile(r"^ [IVX]+\.\n", re.MULTILINE)
    matches = list(pattern.finditer(text))

    if not matches:
        # Fallback: treat entire text as one chapter
        return [{"chapter_number": 1, "title": None, "text": text,
                 "word_count": len(text.split())}]

    chapters = []
    for i, match in enumerate(matches[:count]):
        start = match.start()
        if i + 1 < len(matches):
            end = matches[i + 1].start()
        else:
            end = len(text)
        ch_text = text[start:end].strip()
        chapters.append({
            "chapter_number": i + 1,
            "title": match.group().strip(),
            "text": ch_text,
            "word_count": len(ch_text.split()),
        })

    return chapters


def _split_great_gatsby(text: str, count: int) -> list[dict]:
    """Custom chapter splitting for The Great Gatsby.

    Chapters are separated by centered Roman numerals on their own line
    (lots of leading whitespace + Roman numeral).
    """
    pattern = re.compile(r"^\s{10,}[IVX]+\s*$", re.MULTILINE)
    matches = list(pattern.finditer(text))

    if not matches:
        # Fallback: treat entire text as one chapter
        return [{"chapter_number": 1, "title": None, "text": text,
                 "word_count": len(text.split())}]

    chapters = []
    for i, match in enumerate(matches[:count]):
        start = match.start()
        if i + 1 < len(matches):
            end = matches[i + 1].start()
        else:
            end = len(text)
        ch_text = text[start:end].strip()
        chapters.append({
            "chapter_number": i + 1,
            "title": match.group().strip(),
            "text": ch_text,
            "word_count": len(ch_text.split()),
        })

    return chapters


def _load_chapters(filename: str, count: int) -> list[dict]:
    """Load a Gutenberg text, strip headers, detect chapters, return first `count`.

    Uses custom splitting for Time Machine and Great Gatsby where
    detect_chapters cannot reliably find chapter boundaries. For all other
    books, uses detect_chapters with the standard Chapter 1 search heuristic.
    """
    path = SAMPLES_DIR / filename
    if not path.exists():
        pytest.skip(f"Sample file {filename} not found")
    text = path.read_text(encoding="utf-8-sig")
    text = _strip_gutenberg(text)

    # --- Custom splitting for books that detect_chapters can't handle ---
    if filename == "time_machine_full.txt":
        return _split_time_machine(text, count)

    if filename == "great_gatsby_full.txt":
        return _split_great_gatsby(text, count)

    # --- Standard detect_chapters for everything else ---
    chapters = detect_chapters(text)

    # Find the index of "Chapter 1" in the detected chapters.
    start_idx = 0
    for i, ch in enumerate(chapters):
        title = (ch.get("title") or "").strip().upper()
        if title in ("CHAPTER 1", "CHAPTER I", "CHAPTER 1."):
            start_idx = i
            break
        if title.startswith("CHAPTER 1.") or title.startswith("CHAPTER 1 "):
            start_idx = i
            break
        if title.startswith("CHAPTER I.") or title.startswith("CHAPTER I "):
            start_idx = i
            break

    selected = chapters[start_idx : start_idx + count]
    if len(selected) < count:
        # If we don't have enough chapters from that start point, just take the first N.
        selected = chapters[:count]
    return selected


def _load_ground_truth(genre_key: str) -> dict:
    """Load a ground truth JSON file."""
    path = GROUND_TRUTH_DIR / GROUND_TRUTH_FILES[genre_key]
    return json.loads(path.read_text())


def _save_result(genre_key: str, bible: StoryBibleSchema):
    """Save bible result for manual review."""
    RESULTS_DIR.mkdir(exist_ok=True)
    path = RESULTS_DIR / f"{genre_key}_3ch_bible.json"
    path.write_text(json.dumps(bible.model_dump(), indent=2))


# ---------------------------------------------------------------------------
# Module-level generation cache — generates all 5 bibles once, incrementally
# across 3 chapters each. This avoids re-running the ~15 minute generation
# for every test function.
# ---------------------------------------------------------------------------

_bibles_cache = None


def _try_load_cached_bibles():
    """Try to load previously saved bible results from disk to avoid re-running API calls."""
    results = {}
    for _filename, (genre, key) in GENRE_MAP.items():
        path = RESULTS_DIR / f"{key}_3ch_bible.json"
        if not path.exists():
            return None  # Missing at least one — regenerate all
        data = json.loads(path.read_text())
        bible = StoryBibleSchema.model_validate(data)
        results[key] = {"bible": bible, "warnings": []}
    return results


def _get_bibles():
    global _bibles_cache
    if _bibles_cache is not None:
        return _bibles_cache

    # Try loading from disk first (avoids re-running ~20 min of API calls)
    cached = _try_load_cached_bibles()
    if cached is not None:
        print("\n--- Loaded all bibles from cached results on disk ---")
        for key, result in cached.items():
            bible = result["bible"]
            print(f"  {key}: {len(bible.characters)} characters, "
                  f"{len(bible.timeline)} events, "
                  f"{len(bible.settings)} settings")
        _bibles_cache = cached
        return _bibles_cache

    async def _generate_all():
        results = {}
        for filename, (genre, key) in GENRE_MAP.items():
            print(f"\n--- Generating {key} bible (3 chapters, incremental) ---")
            chapters = _load_chapters(filename, NUM_CHAPTERS)
            print(f"  Loaded {len(chapters)} chapters for {key}")

            bible = None
            warnings_all = []

            for i, ch in enumerate(chapters):
                ch_num = i + 1
                print(f"  Processing chapter {ch_num}/{len(chapters)} "
                      f"({ch['word_count']} words)...")

                if bible is None:
                    # First chapter — generate from scratch
                    bible, warnings = await generate_story_bible(
                        chapter_text=ch["text"],
                        chapter_number=ch_num,
                        genre=genre,
                    )
                else:
                    # Subsequent chapters — update existing bible
                    bible, warnings = await generate_story_bible(
                        chapter_text=ch["text"],
                        chapter_number=ch_num,
                        genre=genre,
                        existing_bible=bible.model_dump(),
                    )
                warnings_all.extend(warnings)
                print(f"    -> {len(bible.characters)} characters, "
                      f"{len(bible.timeline)} events, "
                      f"{len(bible.settings)} settings, "
                      f"{len(bible.plot_threads)} plot threads")

            results[key] = {"bible": bible, "warnings": warnings_all}
            _save_result(key, bible)
            print(f"  {key} bible complete: {len(bible.characters)} characters total")

        return results

    _bibles_cache = asyncio.run(_generate_all())
    return _bibles_cache


@pytest.fixture(scope="module")
def bibles():
    return _get_bibles()


@pytest.fixture(scope="module")
def ground_truths():
    return {key: _load_ground_truth(key) for key in GROUND_TRUTH_FILES}


# ---------------------------------------------------------------------------
# Helpers for fuzzy matching
# ---------------------------------------------------------------------------

def _name_match(gt_name: str, generated_names: str) -> bool:
    """Check if a ground truth character name fuzzy-matches the generated names string.

    Checks last name first, then full name, then first name (for single-name characters).
    """
    gt_lower = gt_name.lower()
    gen_lower = generated_names.lower()

    # Try last name (most distinctive)
    parts = gt_lower.split()
    if len(parts) > 1:
        last_name = parts[-1]
        if last_name in gen_lower:
            return True

    # Try full name
    if gt_lower in gen_lower:
        return True

    # Try first name (for single-name characters like Ishmael, Queequeg, Sola)
    first_name = parts[0]
    if len(first_name) > 3 and first_name in gen_lower:
        return True

    return False


def _thread_match(gt_thread: str, generated_threads: list[str]) -> bool:
    """Check if a ground truth plot thread has a fuzzy keyword match in generated threads.

    Extracts key words from the ground truth thread and checks if any generated
    thread contains at least 2 of them.
    """
    # Extract meaningful keywords (skip short common words)
    stopwords = {
        "the", "and", "for", "that", "this", "with", "from", "are", "was",
        "his", "her", "its", "who", "how", "has", "had", "been", "will",
        "she", "they", "them", "their", "into", "about", "than",
    }
    gt_words = [
        w.lower().strip(".,;:!?—-\"'()")
        for w in gt_thread.split()
        if len(w) > 2
    ]
    keywords = [w for w in gt_words if w not in stopwords]

    for gen_thread in generated_threads:
        gen_lower = gen_thread.lower()
        matches = sum(1 for kw in keywords if kw in gen_lower)
        if matches >= 2 or (len(keywords) <= 2 and matches >= 1):
            return True
    return False


def _setting_match(gt_name: str, generated_settings: list[str]) -> bool:
    """Check if a ground truth setting name appears in generated settings."""
    gt_lower = gt_name.lower()
    # Try the full name
    for gen_name in generated_settings:
        if gt_lower in gen_name.lower() or gen_name.lower() in gt_lower:
            return True
    # Try individual significant words from the ground truth name
    gt_words = [w.lower() for w in gt_name.split() if len(w) > 3]
    for gen_name in generated_settings:
        gen_lower = gen_name.lower()
        if any(w in gen_lower for w in gt_words):
            return True
    return False


# ---------------------------------------------------------------------------
# Tests: JSON validity
# ---------------------------------------------------------------------------

@pytest.mark.api
def test_all_produce_valid_schema(bibles):
    """All generated bibles must be valid StoryBibleSchema instances."""
    for key, result in bibles.items():
        assert isinstance(result["bible"], StoryBibleSchema), (
            f"{key}: not a valid StoryBibleSchema"
        )


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


# ---------------------------------------------------------------------------
# Tests: Character recall (>70% of important ground truth characters found)
# ---------------------------------------------------------------------------

@pytest.mark.api
@pytest.mark.parametrize("genre_key", list(GROUND_TRUTH_FILES.keys()))
def test_character_recall(bibles, ground_truths, genre_key):
    """At least 70% of important ground truth characters should be found."""
    bible = bibles[genre_key]["bible"]
    gt = ground_truths[genre_key]

    # "Important" = protagonist or supporting in ground truth
    important_gt_chars = [
        c for c in gt["characters"]
        if c["role"] in ("protagonist", "supporting")
    ]
    if not important_gt_chars:
        pytest.skip(f"No important characters in {genre_key} ground truth")

    generated_names = " ".join(c.name for c in bible.characters)

    found = []
    missed = []
    for gt_char in important_gt_chars:
        if _name_match(gt_char["name"], generated_names):
            found.append(gt_char["name"])
        else:
            missed.append(gt_char["name"])

    recall = len(found) / len(important_gt_chars)
    assert recall >= 0.70, (
        f"{genre_key}: character recall {recall:.0%} < 70%. "
        f"Found: {found}. Missed: {missed}. "
        f"Generated: {[c.name for c in bible.characters]}"
    )


# ---------------------------------------------------------------------------
# Tests: Voice profile match (POV and tense must match)
# ---------------------------------------------------------------------------

@pytest.mark.api
@pytest.mark.parametrize("genre_key", list(GROUND_TRUTH_FILES.keys()))
def test_voice_profile_match(bibles, ground_truths, genre_key):
    """POV and tense must match ground truth (case-insensitive contains check)."""
    bible = bibles[genre_key]["bible"]
    gt_voice = ground_truths[genre_key]["voice_profile"]

    gen_pov = bible.voice_profile.pov.lower()
    gen_tense = bible.voice_profile.tense.lower()

    # Extract the key word from ground truth (e.g. "first" from "first person")
    gt_pov_key = gt_voice["pov"].split()[0].lower()  # "first", "third"
    gt_tense_key = gt_voice["tense"].lower()  # "past", "present"

    assert gt_pov_key in gen_pov, (
        f"{genre_key}: POV mismatch. Expected containing '{gt_pov_key}', got '{gen_pov}'"
    )
    assert gt_tense_key in gen_tense, (
        f"{genre_key}: Tense mismatch. Expected containing '{gt_tense_key}', got '{gen_tense}'"
    )


# ---------------------------------------------------------------------------
# Tests: Plot thread recall (>50% of ground truth threads found)
# ---------------------------------------------------------------------------

@pytest.mark.api
@pytest.mark.parametrize("genre_key", list(GROUND_TRUTH_FILES.keys()))
def test_plot_thread_recall(bibles, ground_truths, genre_key):
    """At least 50% of ground truth plot threads should have a fuzzy match."""
    bible = bibles[genre_key]["bible"]
    gt = ground_truths[genre_key]

    gt_threads = gt.get("plot_threads", [])
    if not gt_threads:
        pytest.skip(f"No plot threads in {genre_key} ground truth")

    generated_thread_texts = [pt.thread for pt in bible.plot_threads]

    found = []
    missed = []
    for gt_pt in gt_threads:
        gt_text = gt_pt["thread"]
        if _thread_match(gt_text, generated_thread_texts):
            found.append(gt_text)
        else:
            missed.append(gt_text)

    recall = len(found) / len(gt_threads)
    assert recall >= 0.50, (
        f"{genre_key}: plot thread recall {recall:.0%} < 50%. "
        f"Found: {found}. Missed: {missed}. "
        f"Generated: {generated_thread_texts}"
    )


# ---------------------------------------------------------------------------
# Tests: No protagonist hallucination
# ---------------------------------------------------------------------------

@pytest.mark.api
@pytest.mark.parametrize("genre_key", list(GROUND_TRUTH_FILES.keys()))
def test_no_protagonist_hallucination(bibles, ground_truths, genre_key):
    """Characters marked as protagonist should correspond to actual ground truth protagonists."""
    bible = bibles[genre_key]["bible"]
    gt = ground_truths[genre_key]

    gt_protagonist_names = [
        c["name"] for c in gt["characters"] if c["role"] == "protagonist"
    ]
    # Also accept supporting characters as valid (some books have ensemble casts
    # and Claude may reasonably promote a supporting character to protagonist).
    gt_acceptable_names = [
        c["name"] for c in gt["characters"]
        if c["role"] in ("protagonist", "supporting")
    ]

    gen_protagonists = [c for c in bible.characters if c.role == "protagonist"]

    # There should not be more than 3 protagonists (sanity check)
    assert len(gen_protagonists) <= 3, (
        f"{genre_key}: too many protagonists ({len(gen_protagonists)}): "
        f"{[p.name for p in gen_protagonists]}"
    )

    # Each generated protagonist should match a ground truth protagonist or supporting character
    # Build a name string that includes aliases from ground truth
    gt_acceptable_chars = [
        c for c in gt["characters"]
        if c["role"] in ("protagonist", "supporting")
    ]
    all_gt_names_parts = []
    for c in gt_acceptable_chars:
        all_gt_names_parts.append(c["name"])
        all_gt_names_parts.extend(c.get("aliases", []))
    all_gt_names = " ".join(all_gt_names_parts)

    for gen_prot in gen_protagonists:
        matched = _name_match(gen_prot.name, all_gt_names)
        assert matched, (
            f"{genre_key}: hallucinated protagonist '{gen_prot.name}' — "
            f"not in ground truth important characters: {gt_acceptable_names}"
        )


# ---------------------------------------------------------------------------
# Tests: Setting recall (>50% of ground truth settings found)
# ---------------------------------------------------------------------------

@pytest.mark.api
@pytest.mark.parametrize("genre_key", list(GROUND_TRUTH_FILES.keys()))
def test_setting_recall(bibles, ground_truths, genre_key):
    """At least 50% of ground truth settings should be found."""
    bible = bibles[genre_key]["bible"]
    gt = ground_truths[genre_key]

    gt_settings = gt.get("settings", [])
    if not gt_settings:
        pytest.skip(f"No settings in {genre_key} ground truth")

    generated_setting_names = [s.name for s in bible.settings]

    found = []
    missed = []
    for gt_setting in gt_settings:
        gt_name = gt_setting["name"]
        if _setting_match(gt_name, generated_setting_names):
            found.append(gt_name)
        else:
            missed.append(gt_name)

    recall = len(found) / len(gt_settings)
    assert recall >= 0.50, (
        f"{genre_key}: setting recall {recall:.0%} < 50%. "
        f"Found: {found}. Missed: {missed}. "
        f"Generated: {generated_setting_names}"
    )
