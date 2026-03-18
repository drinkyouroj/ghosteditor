"""Eval tests: nonfiction argument map generation via LLM API.

Runs generate_argument_map() against 5 nonfiction samples (one per format).
Validates JSON structure, schema compliance, and content-level expectations.

These tests make real LLM API calls and cost real money. They are marked
with @pytest.mark.api so they can be run selectively:
    pytest tests/eval/test_nonfiction_argument_map.py -v -m api

Sample-to-format mapping:
    Academic         -> nonfiction_academic_sample.txt
    Personal Essay   -> nonfiction_personal_essay_sample.txt
    Journalism       -> nonfiction_journalism_sample.txt
    Self-Help        -> nonfiction_self_help_sample.txt
    Business         -> nonfiction_business_sample.txt
"""

import asyncio
import json
import pytest
from pathlib import Path

from app.analysis.argument_map import generate_argument_map
from app.analysis.argument_map_schema import ArgumentMapSchema

from tests.eval.conftest import get_backend_name

SAMPLES_DIR = Path(__file__).parent / "samples"
RESULTS_DIR_BASE = Path(__file__).parent / "nonfiction_results"


def _results_dir() -> Path:
    """Return the backend-scoped results directory."""
    return RESULTS_DIR_BASE / get_backend_name()


FORMAT_MAP = {
    "nonfiction_academic_sample.txt": ("academic", "academic"),
    "nonfiction_personal_essay_sample.txt": ("personal_essay", "personal_essay"),
    "nonfiction_journalism_sample.txt": ("journalism", "journalism"),
    "nonfiction_self_help_sample.txt": ("self_help", "self_help"),
    "nonfiction_business_sample.txt": ("business", "business"),
}

# Expected thesis keywords per format (at minimum, these should appear)
EXPECTED_THESIS_KEYWORDS = {
    "academic": ["decision", "uncertainty"],
    "personal_essay": ["attention", "algorithm", "social media"],
    "journalism": ["water", "infrastructure"],
    "self_help": ["morning", "attention"],
    "business": ["remote", "distributed"],
}

# Expected voice profiles
EXPECTED_VOICE = {
    "academic": {"register_contains": "formal", "pov_contains": "first"},
    "personal_essay": {"register_contains": "personal", "pov_contains": "first"},
    "journalism": {"register_contains": "professional", "pov_contains": "third"},
    "self_help": {"register_contains": "conversational", "pov_contains": "first"},
    "business": {"register_contains": "professional", "pov_contains": "first"},
}


def _load_sample(filename: str) -> str:
    """Load a nonfiction sample text."""
    path = SAMPLES_DIR / filename
    if not path.exists():
        pytest.skip(f"Sample file {filename} not found")
    return path.read_text(encoding="utf-8-sig").strip()


def _save_result(format_key: str, arg_map: ArgumentMapSchema):
    """Save argument map result for manual review (scoped by LLM backend)."""
    results_dir = _results_dir()
    results_dir.mkdir(parents=True, exist_ok=True)
    path = results_dir / f"{format_key}_argument_map.json"
    path.write_text(json.dumps(arg_map.model_dump(), indent=2))


# Generate all argument maps once at module level
_maps_cache = None


def _get_maps():
    global _maps_cache
    if _maps_cache is not None:
        return _maps_cache

    async def _generate_all():
        results = {}
        for filename, (nf_format, key) in FORMAT_MAP.items():
            text = _load_sample(filename)
            arg_map, warnings = await generate_argument_map(
                manuscript_text=text,
                nonfiction_format=nf_format,
            )
            results[key] = {"map": arg_map, "warnings": warnings}
            _save_result(key, arg_map)
            print(
                f"  Generated {key} argument map: "
                f"{len(arg_map.argument_threads)} threads, "
                f"{len(arg_map.evidence_log)} evidence items"
            )
        return results

    _maps_cache = asyncio.run(_generate_all())
    return _maps_cache


@pytest.fixture(scope="module")
def arg_maps():
    return _get_maps()


# --- Structure tests (all formats) ---


@pytest.mark.api
def test_all_produce_valid_schema(arg_maps):
    """All samples should produce a valid ArgumentMapSchema."""
    for key, result in arg_maps.items():
        assert isinstance(result["map"], ArgumentMapSchema), (
            f"{key}: not a valid ArgumentMapSchema"
        )


@pytest.mark.api
def test_all_have_central_thesis(arg_maps):
    """All samples should identify a central thesis."""
    for key, result in arg_maps.items():
        arg_map = result["map"]
        assert arg_map.central_thesis is not None, f"{key}: no central thesis"
        assert len(arg_map.central_thesis) > 10, (
            f"{key}: central thesis too short: {arg_map.central_thesis!r}"
        )


@pytest.mark.api
def test_all_have_argument_threads(arg_maps):
    """All samples should identify at least one argument thread."""
    for key, result in arg_maps.items():
        arg_map = result["map"]
        assert len(arg_map.argument_threads) >= 1, (
            f"{key}: no argument threads extracted"
        )


@pytest.mark.api
def test_all_have_evidence(arg_maps):
    """All samples should identify at least one evidence item."""
    for key, result in arg_maps.items():
        arg_map = result["map"]
        assert len(arg_map.evidence_log) >= 1, (
            f"{key}: no evidence items extracted"
        )


@pytest.mark.api
def test_all_have_voice_profile(arg_maps):
    """All samples should produce a voice profile."""
    for key, result in arg_maps.items():
        arg_map = result["map"]
        assert arg_map.voice_profile is not None, f"{key}: no voice profile"
        assert arg_map.voice_profile.register, f"{key}: empty register"
        assert arg_map.voice_profile.pov, f"{key}: empty pov"


@pytest.mark.api
def test_all_have_format_detection(arg_maps):
    """All samples should detect a format with confidence."""
    for key, result in arg_maps.items():
        arg_map = result["map"]
        assert arg_map.detected_format_confidence is not None, (
            f"{key}: no format detection"
        )
        assert arg_map.detected_format_confidence.format, (
            f"{key}: empty detected format"
        )
        assert arg_map.detected_format_confidence.confidence in (
            "high", "medium", "low"
        ), f"{key}: invalid confidence: {arg_map.detected_format_confidence.confidence}"


# --- Content tests (format-specific) ---


@pytest.mark.api
def test_thesis_keywords(arg_maps):
    """Central thesis should contain expected keywords for each format."""
    for key, keywords in EXPECTED_THESIS_KEYWORDS.items():
        arg_map = arg_maps[key]["map"]
        thesis_lower = arg_map.central_thesis.lower()
        assert any(kw in thesis_lower for kw in keywords), (
            f"{key}: thesis '{arg_map.central_thesis}' does not contain "
            f"any expected keywords: {keywords}"
        )


@pytest.mark.api
def test_academic_has_citations(arg_maps):
    """Academic sample should identify citation-type evidence."""
    arg_map = arg_maps["academic"]["map"]
    evidence_types = [e.type for e in arg_map.evidence_log]
    assert "citation" in evidence_types, (
        f"Academic sample should have citation evidence, "
        f"found types: {set(evidence_types)}"
    )


@pytest.mark.api
def test_journalism_has_statistics(arg_maps):
    """Journalism sample should identify statistic-type evidence."""
    arg_map = arg_maps["journalism"]["map"]
    evidence_types = [e.type for e in arg_map.evidence_log]
    has_stats = "statistic" in evidence_types or "citation" in evidence_types
    assert has_stats, (
        f"Journalism sample should have statistic or citation evidence, "
        f"found types: {set(evidence_types)}"
    )


@pytest.mark.api
def test_personal_essay_has_anecdotes(arg_maps):
    """Personal essay should identify anecdote-type evidence."""
    arg_map = arg_maps["personal_essay"]["map"]
    evidence_types = [e.type for e in arg_map.evidence_log]
    has_anecdotes = "anecdote" in evidence_types or "example" in evidence_types
    assert has_anecdotes, (
        f"Personal essay should have anecdote or example evidence, "
        f"found types: {set(evidence_types)}"
    )


@pytest.mark.api
def test_self_help_has_examples(arg_maps):
    """Self-help sample should identify example or statistic evidence."""
    arg_map = arg_maps["self_help"]["map"]
    evidence_types = [e.type for e in arg_map.evidence_log]
    has_examples = any(
        t in evidence_types for t in ("example", "statistic", "anecdote")
    )
    assert has_examples, (
        f"Self-help sample should have example/statistic/anecdote evidence, "
        f"found types: {set(evidence_types)}"
    )


@pytest.mark.api
def test_format_detection_accuracy(arg_maps):
    """Detected format should match the sample's actual format."""
    for key, result in arg_maps.items():
        arg_map = result["map"]
        detected = arg_map.detected_format_confidence.format.lower()
        # Allow some flexibility in format naming
        if key == "personal_essay":
            assert detected in ("personal_essay", "personal essay", "memoir", "essay"), (
                f"{key}: detected format '{detected}' does not match expected"
            )
        else:
            assert key in detected or detected in key, (
                f"{key}: detected format '{detected}' does not match expected format"
            )


# --- Voice profile tests ---


@pytest.mark.api
def test_voice_profiles(arg_maps):
    """Check register and POV detection for each format."""
    for key, expected in EXPECTED_VOICE.items():
        arg_map = arg_maps[key]["map"]
        register = arg_map.voice_profile.register.lower()
        pov = arg_map.voice_profile.pov.lower()
        assert expected["register_contains"] in register or register in expected["register_contains"], (
            f"{key}: expected register containing '{expected['register_contains']}', "
            f"got '{register}'"
        )
        # POV check is more flexible — "first person" could be "first person singular"
        assert expected["pov_contains"] in pov, (
            f"{key}: expected POV containing '{expected['pov_contains']}', got '{pov}'"
        )


# --- Argument thread quality tests ---


@pytest.mark.api
def test_threads_have_valid_status(arg_maps):
    """All threads should have a valid status value."""
    valid_statuses = {"open", "supported", "unresolved", "abandoned", "resolved"}
    for key, result in arg_maps.items():
        arg_map = result["map"]
        for thread in arg_map.argument_threads:
            assert thread.status in valid_statuses, (
                f"{key}: thread '{thread.id}' has invalid status '{thread.status}'"
            )


@pytest.mark.api
def test_threads_have_claims(arg_maps):
    """All threads should have non-empty claims."""
    for key, result in arg_maps.items():
        arg_map = result["map"]
        for thread in arg_map.argument_threads:
            assert thread.claim and len(thread.claim) > 5, (
                f"{key}: thread '{thread.id}' has empty or too-short claim"
            )


@pytest.mark.api
def test_evidence_linked_to_threads(arg_maps):
    """At least some evidence should be linked to argument threads."""
    for key, result in arg_maps.items():
        arg_map = result["map"]
        if not arg_map.evidence_log:
            continue
        linked = [e for e in arg_map.evidence_log if e.supports_thread is not None]
        assert len(linked) >= 1, (
            f"{key}: no evidence items linked to argument threads"
        )


# --- JSON round-trip test ---


@pytest.mark.api
def test_maps_json_roundtrip(arg_maps):
    """All argument maps should survive JSON serialization/deserialization."""
    for key, result in arg_maps.items():
        arg_map = result["map"]
        dumped = json.dumps(arg_map.model_dump())
        loaded = json.loads(dumped)
        reconstructed = ArgumentMapSchema.model_validate(loaded)
        assert len(reconstructed.argument_threads) == len(arg_map.argument_threads), (
            f"{key}: thread count mismatch after roundtrip"
        )
        assert len(reconstructed.evidence_log) == len(arg_map.evidence_log), (
            f"{key}: evidence count mismatch after roundtrip"
        )
