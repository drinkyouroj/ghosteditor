"""Eval tests: chapter analysis engine against 5 Gutenberg samples.

Runs the chapter analysis prompt on chapters 1-3 of each genre sample,
using the cached story bibles from the bible ground truth eval as bible
context for chapters 2-3. Validates JSON validity, schema compliance,
severity calibration, pacing structure, and genre notes.

These tests make real Claude API calls. Marked with @pytest.mark.api:
    pytest tests/eval/test_chapter_analysis.py -v -m api
"""

import asyncio
import json
import re
import pytest
from pathlib import Path

from app.analysis.chapter_analyzer import analyze_chapter
from app.analysis.issue_schema import ChapterAnalysisResult

from tests.eval.conftest import get_backend_name

SAMPLES_DIR = Path(__file__).parent / "samples"
BIBLE_RESULTS_DIR_BASE = Path(__file__).parent / "bible_results"
ANALYSIS_RESULTS_DIR_BASE = Path(__file__).parent / "analysis_results"


def _bible_results_dir() -> Path:
    """Return the backend-scoped bible results directory."""
    return BIBLE_RESULTS_DIR_BASE / get_backend_name()


def _analysis_results_dir() -> Path:
    """Return the backend-scoped analysis results directory."""
    return ANALYSIS_RESULTS_DIR_BASE / get_backend_name()

START_MARKER = "*** START OF THE PROJECT GUTENBERG EBOOK"
END_MARKER = "*** END OF THE PROJECT GUTENBERG EBOOK"

GENRE_MAP = {
    "pride_and_prejudice_full.txt": ("Romance", "romance"),
    "time_machine_full.txt": ("Fantasy", "fantasy"),
    "riddle_of_sands_full.txt": ("Thriller", "thriller"),
    "great_gatsby_full.txt": ("Literary Fiction", "literary"),
    "moonstone_full.txt": ("Mystery", "mystery"),
}

NUM_CHAPTERS = 3

# ---------------------------------------------------------------------------
# Text loading helpers (shared logic with bible ground truth tests)
# ---------------------------------------------------------------------------


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
    """Custom chapter splitting for The Time Machine (Roman numeral sections)."""
    pattern = re.compile(r"^ [IVX]+\.\n", re.MULTILINE)
    matches = list(pattern.finditer(text))
    if not matches:
        return [{"chapter_number": 1, "title": None, "text": text,
                 "word_count": len(text.split())}]
    chapters = []
    for i, match in enumerate(matches[:count]):
        start = match.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        ch_text = text[start:end].strip()
        chapters.append({
            "chapter_number": i + 1,
            "title": match.group().strip(),
            "text": ch_text,
            "word_count": len(ch_text.split()),
        })
    return chapters


def _split_great_gatsby(text: str, count: int) -> list[dict]:
    """Custom chapter splitting for The Great Gatsby (centered Roman numerals)."""
    pattern = re.compile(r"^\s{10,}[IVX]+\s*$", re.MULTILINE)
    matches = list(pattern.finditer(text))
    if not matches:
        return [{"chapter_number": 1, "title": None, "text": text,
                 "word_count": len(text.split())}]
    chapters = []
    for i, match in enumerate(matches[:count]):
        start = match.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        ch_text = text[start:end].strip()
        chapters.append({
            "chapter_number": i + 1,
            "title": match.group().strip(),
            "text": ch_text,
            "word_count": len(ch_text.split()),
        })
    return chapters


def _load_chapters(filename: str, count: int) -> list[dict]:
    """Load a Gutenberg text, strip headers, detect chapters, return first `count`."""
    from app.manuscripts.extraction import detect_chapters_sync as detect_chapters

    path = SAMPLES_DIR / filename
    if not path.exists():
        pytest.skip(f"Sample file {filename} not found")
    text = path.read_text(encoding="utf-8-sig")
    text = _strip_gutenberg(text)

    if filename == "time_machine_full.txt":
        return _split_time_machine(text, count)
    if filename == "great_gatsby_full.txt":
        return _split_great_gatsby(text, count)

    chapters = detect_chapters(text)
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

    selected = chapters[start_idx: start_idx + count]
    if len(selected) < count:
        selected = chapters[:count]
    return selected


def _load_cached_bible(genre_key: str) -> dict | None:
    """Load a cached story bible from the bible eval results (backend-scoped)."""
    path = _bible_results_dir() / f"{genre_key}_3ch_bible.json"
    if not path.exists():
        return None
    return json.loads(path.read_text())


# ---------------------------------------------------------------------------
# Module-level analysis cache
# ---------------------------------------------------------------------------

_analysis_cache = None


def _save_analysis(genre_key: str, ch_num: int, result: ChapterAnalysisResult):
    """Save analysis result for manual review (scoped by LLM backend)."""
    results_dir = _analysis_results_dir()
    results_dir.mkdir(parents=True, exist_ok=True)
    path = results_dir / f"{genre_key}_ch{ch_num}_analysis.json"
    path.write_text(json.dumps(result.model_dump(), indent=2))


def _try_load_cached_analyses():
    """Try to load previously saved analysis results from disk (backend-scoped)."""
    results_dir = _analysis_results_dir()
    results = {}
    for _filename, (genre, key) in GENRE_MAP.items():
        genre_results = {}
        for ch_num in range(1, NUM_CHAPTERS + 1):
            path = results_dir / f"{key}_ch{ch_num}_analysis.json"
            if not path.exists():
                return None
            data = json.loads(path.read_text())
            analysis = ChapterAnalysisResult.model_validate(data)
            genre_results[ch_num] = {"analysis": analysis, "warnings": []}
        results[key] = genre_results
    return results


def _get_analyses():
    global _analysis_cache
    if _analysis_cache is not None:
        return _analysis_cache

    # Try loading from disk first
    cached = _try_load_cached_analyses()
    if cached is not None:
        print("\n--- Loaded all analyses from cached results on disk ---")
        for key, chapters in cached.items():
            for ch_num, result in chapters.items():
                n_issues = len(result["analysis"].issues)
                print(f"  {key} ch{ch_num}: {n_issues} issues")
        _analysis_cache = cached
        return _analysis_cache

    async def _run_all():
        results = {}
        for filename, (genre, key) in GENRE_MAP.items():
            print(f"\n--- Analyzing {key} (3 chapters) ---")
            chapters = _load_chapters(filename, NUM_CHAPTERS)
            bible_json = _load_cached_bible(key)

            if bible_json is None:
                print(f"  WARNING: No cached bible for {key}, running without bible context")

            genre_results = {}
            for i, ch in enumerate(chapters):
                ch_num = i + 1
                print(f"  Analyzing chapter {ch_num}/{len(chapters)} "
                      f"({ch['word_count']} words)...")

                # Chapter 1: no bible (first-chapter mode)
                # Chapters 2+: use the cached bible
                ch_bible = None if ch_num == 1 else bible_json

                analysis, warnings = await analyze_chapter(
                    chapter_text=ch["text"],
                    chapter_number=ch_num,
                    genre=genre,
                    bible_json=ch_bible,
                )

                genre_results[ch_num] = {"analysis": analysis, "warnings": warnings}
                _save_analysis(key, ch_num, analysis)

                n_issues = len(analysis.issues)
                severities = {}
                for issue in analysis.issues:
                    severities[issue.severity] = severities.get(issue.severity, 0) + 1
                print(f"    -> {n_issues} issues: {severities}")
                print(f"    -> pacing: {analysis.pacing.tension_arc}, "
                      f"{analysis.pacing.scene_count} scenes")

            results[key] = genre_results
            print(f"  {key} analysis complete")

        return results

    _analysis_cache = asyncio.run(_run_all())
    return _analysis_cache


@pytest.fixture(scope="module")
def analyses():
    return _get_analyses()


# ---------------------------------------------------------------------------
# Helper to flatten all analyses into a list for parametrize
# ---------------------------------------------------------------------------

ALL_GENRE_KEYS = list(GENRE_MAP.values())  # [(genre_label, genre_key), ...]
GENRE_KEYS = [v[1] for v in GENRE_MAP.values()]
CHAPTER_NUMS = list(range(1, NUM_CHAPTERS + 1))

# Parametrize over (genre_key, chapter_number) pairs
GENRE_CHAPTER_PAIRS = [
    (gk, ch) for gk in GENRE_KEYS for ch in CHAPTER_NUMS
]


# ---------------------------------------------------------------------------
# Tests: JSON validity and schema compliance
# ---------------------------------------------------------------------------

@pytest.mark.api
def test_all_produce_valid_schema(analyses):
    """All chapter analyses must be valid ChapterAnalysisResult instances."""
    for key, chapters in analyses.items():
        for ch_num, result in chapters.items():
            assert isinstance(result["analysis"], ChapterAnalysisResult), (
                f"{key} ch{ch_num}: not a valid ChapterAnalysisResult"
            )


@pytest.mark.api
def test_all_json_roundtrip(analyses):
    """All analyses survive JSON serialization/deserialization."""
    for key, chapters in analyses.items():
        for ch_num, result in chapters.items():
            analysis = result["analysis"]
            dumped = json.dumps(analysis.model_dump())
            loaded = json.loads(dumped)
            reconstructed = ChapterAnalysisResult.model_validate(loaded)
            assert len(reconstructed.issues) == len(analysis.issues), (
                f"{key} ch{ch_num}: issue count mismatch after roundtrip"
            )


# ---------------------------------------------------------------------------
# Tests: Issue quality
# ---------------------------------------------------------------------------

@pytest.mark.api
@pytest.mark.parametrize("genre_key,ch_num", GENRE_CHAPTER_PAIRS)
def test_issues_have_required_fields(analyses, genre_key, ch_num):
    """Every issue must have a non-empty description and valid type/severity."""
    valid_types = {
        "consistency", "pacing", "character", "plot",
        "voice", "worldbuilding", "genre_convention",
    }
    valid_severities = {"critical", "warning", "note"}

    analysis = analyses[genre_key][ch_num]["analysis"]
    for i, issue in enumerate(analysis.issues):
        assert issue.description and issue.description.strip(), (
            f"{genre_key} ch{ch_num} issue[{i}]: empty description"
        )
        assert issue.type in valid_types, (
            f"{genre_key} ch{ch_num} issue[{i}]: invalid type '{issue.type}'"
        )
        assert issue.severity in valid_severities, (
            f"{genre_key} ch{ch_num} issue[{i}]: invalid severity '{issue.severity}'"
        )
        assert issue.suggestion and issue.suggestion.strip(), (
            f"{genre_key} ch{ch_num} issue[{i}]: empty suggestion"
        )


@pytest.mark.api
@pytest.mark.parametrize("genre_key,ch_num", GENRE_CHAPTER_PAIRS)
def test_issue_count_within_bounds(analyses, genre_key, ch_num):
    """Each chapter should produce at least 1 issue and no more than 15."""
    analysis = analyses[genre_key][ch_num]["analysis"]
    n = len(analysis.issues)
    assert n >= 1, (
        f"{genre_key} ch{ch_num}: no issues found — suspicious for a developmental edit"
    )
    assert n <= 15, (
        f"{genre_key} ch{ch_num}: {n} issues exceeds 15-issue cap"
    )


@pytest.mark.api
@pytest.mark.parametrize("genre_key", GENRE_KEYS)
def test_chapter1_no_consistency_criticals(analyses, genre_key):
    """Chapter 1 (no bible) should not have critical consistency issues."""
    analysis = analyses[genre_key][1]["analysis"]
    consistency_criticals = [
        issue for issue in analysis.issues
        if issue.type == "consistency" and issue.severity == "critical"
    ]
    assert len(consistency_criticals) == 0, (
        f"{genre_key} ch1: found {len(consistency_criticals)} critical consistency "
        f"issues but chapter 1 has no bible to check against. "
        f"Issues: {[i.description for i in consistency_criticals]}"
    )


@pytest.mark.api
@pytest.mark.parametrize("genre_key", GENRE_KEYS)
def test_severity_calibration_conservative(analyses, genre_key):
    """Across all 3 chapters, critical issues should be rare (<=5 total per genre)."""
    total_criticals = 0
    for ch_num in CHAPTER_NUMS:
        analysis = analyses[genre_key][ch_num]["analysis"]
        criticals = [i for i in analysis.issues if i.severity == "critical"]
        total_criticals += len(criticals)

    assert total_criticals <= 5, (
        f"{genre_key}: {total_criticals} total critical issues across 3 chapters. "
        "Severity calibration may be too aggressive — classic literature should not "
        "have many genuine contradictions."
    )


# ---------------------------------------------------------------------------
# Tests: Pacing structure
# ---------------------------------------------------------------------------

@pytest.mark.api
@pytest.mark.parametrize("genre_key,ch_num", GENRE_CHAPTER_PAIRS)
def test_pacing_has_content(analyses, genre_key, ch_num):
    """Pacing analysis should have scene count, summary, and tension arc."""
    pacing = analyses[genre_key][ch_num]["analysis"].pacing
    assert pacing.scene_count >= 1, (
        f"{genre_key} ch{ch_num}: scene_count is {pacing.scene_count}"
    )
    assert pacing.chapter_summary and len(pacing.chapter_summary) > 20, (
        f"{genre_key} ch{ch_num}: chapter_summary too short or empty"
    )
    assert pacing.tension_arc in ("rising", "falling", "flat", "mixed"), (
        f"{genre_key} ch{ch_num}: invalid tension_arc '{pacing.tension_arc}'"
    )


@pytest.mark.api
@pytest.mark.parametrize("genre_key,ch_num", GENRE_CHAPTER_PAIRS)
def test_pacing_characters_present(analyses, genre_key, ch_num):
    """Pacing should identify at least one character present in the chapter."""
    pacing = analyses[genre_key][ch_num]["analysis"].pacing
    assert len(pacing.characters_present) >= 1, (
        f"{genre_key} ch{ch_num}: no characters_present in pacing"
    )


@pytest.mark.api
@pytest.mark.parametrize("genre_key,ch_num", GENRE_CHAPTER_PAIRS)
def test_pacing_scene_types_valid(analyses, genre_key, ch_num):
    """Scene types should use the expected vocabulary."""
    valid_scene_types = {
        "dialogue", "action", "exposition", "introspection",
        "transition", "flashback",
    }
    pacing = analyses[genre_key][ch_num]["analysis"].pacing
    for st in pacing.scene_types:
        assert st.lower() in valid_scene_types, (
            f"{genre_key} ch{ch_num}: unexpected scene type '{st}'"
        )


# ---------------------------------------------------------------------------
# Tests: Genre notes
# ---------------------------------------------------------------------------

@pytest.mark.api
@pytest.mark.parametrize("genre_key,ch_num", GENRE_CHAPTER_PAIRS)
def test_genre_notes_present(analyses, genre_key, ch_num):
    """Genre notes should identify at least one convention met (unless weak fit)."""
    genre_notes = analyses[genre_key][ch_num]["analysis"].genre_notes
    # Allow 0 conventions_met when genre_fit is "weak" — some opening chapters
    # (e.g., Riddle of the Sands' epistolary preface) legitimately don't hit
    # any genre conventions yet.
    if genre_notes.genre_fit_score == "weak":
        return
    assert len(genre_notes.conventions_met) >= 1, (
        f"{genre_key} ch{ch_num}: no conventions_met in genre_notes "
        f"(genre_fit_score={genre_notes.genre_fit_score})"
    )


@pytest.mark.api
@pytest.mark.parametrize("genre_key,ch_num", GENRE_CHAPTER_PAIRS)
def test_genre_fit_score_valid(analyses, genre_key, ch_num):
    """Genre fit score should be one of the valid values."""
    score = analyses[genre_key][ch_num]["analysis"].genre_notes.genre_fit_score
    assert score in ("strong", "moderate", "weak"), (
        f"{genre_key} ch{ch_num}: invalid genre_fit_score '{score}'"
    )


@pytest.mark.api
@pytest.mark.parametrize("genre_key", GENRE_KEYS)
def test_genre_fit_not_all_weak(analyses, genre_key):
    """Classic literature should not get 'weak' genre fit on all 3 chapters."""
    scores = [
        analyses[genre_key][ch_num]["analysis"].genre_notes.genre_fit_score
        for ch_num in CHAPTER_NUMS
    ]
    weak_count = sum(1 for s in scores if s == "weak")
    assert weak_count < 3, (
        f"{genre_key}: all 3 chapters scored 'weak' genre fit — "
        f"something is wrong with the genre notes prompt or conventions"
    )


# ---------------------------------------------------------------------------
# Tests: Cross-bible reference quality (chapters 2-3 only)
# ---------------------------------------------------------------------------

@pytest.mark.api
@pytest.mark.parametrize("genre_key", GENRE_KEYS)
def test_bible_characters_appear_in_pacing(analyses, genre_key):
    """Characters from pacing should be recognizable names, not hallucinated."""
    bible_json = _load_cached_bible(genre_key)
    if bible_json is None:
        pytest.skip(f"No cached bible for {genre_key}")

    bible_char_names = " ".join(
        c.get("name", "") for c in bible_json.get("characters", [])
    ).lower()

    # Check chapters 2-3 (which had bible context)
    for ch_num in [2, 3]:
        pacing = analyses[genre_key][ch_num]["analysis"].pacing
        # At least half of characters_present should match bible characters
        if not pacing.characters_present:
            continue
        matched = sum(
            1 for name in pacing.characters_present
            if any(part.lower() in bible_char_names
                   for part in name.split() if len(part) > 2)
        )
        ratio = matched / len(pacing.characters_present)
        assert ratio >= 0.5, (
            f"{genre_key} ch{ch_num}: only {matched}/{len(pacing.characters_present)} "
            f"pacing characters match bible. "
            f"Pacing chars: {pacing.characters_present}"
        )


# ---------------------------------------------------------------------------
# Summary test — print a report for manual review
# ---------------------------------------------------------------------------

@pytest.mark.api
def test_print_summary_report(analyses):
    """Print a summary report of all analyses for manual review (always passes)."""
    print("\n" + "=" * 70)
    print("CHAPTER ANALYSIS EVAL SUMMARY")
    print("=" * 70)

    for key in GENRE_KEYS:
        print(f"\n--- {key.upper()} ---")
        for ch_num in CHAPTER_NUMS:
            analysis = analyses[key][ch_num]["analysis"]
            warnings = analyses[key][ch_num]["warnings"]

            severities = {}
            types = {}
            for issue in analysis.issues:
                severities[issue.severity] = severities.get(issue.severity, 0) + 1
                types[issue.type] = types.get(issue.type, 0) + 1

            pacing = analysis.pacing
            genre_notes = analysis.genre_notes

            print(f"  Ch{ch_num}: {len(analysis.issues)} issues "
                  f"({severities}) | types: {types}")
            print(f"    pacing: {pacing.scene_count} scenes, "
                  f"arc={pacing.tension_arc}, "
                  f"chars={pacing.characters_present}")
            print(f"    genre: fit={genre_notes.genre_fit_score}, "
                  f"met={len(genre_notes.conventions_met)}, "
                  f"missed={len(genre_notes.conventions_missed)}")
            if warnings:
                print(f"    warnings: {warnings}")
            print(f"    summary: {pacing.chapter_summary[:100]}...")

    print("\n" + "=" * 70)
