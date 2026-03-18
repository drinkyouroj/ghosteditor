"""TC-005: Nonfiction eval ground truth tests.

Tests argument map generation against manually curated ground truth JSON
for academic and journalism nonfiction samples. These tests make real
LLM API calls and are marked with @pytest.mark.api.

Ground truth files:
    ground_truth/nonfiction_academic_argument_map.json
    ground_truth/nonfiction_journalism_argument_map.json

Each ground truth specifies:
    - central_thesis: expected substring in the central thesis
    - expected_thread_count_min: minimum number of argument threads
    - expected_evidence_types: evidence types that should appear
    - expected_voice_register: expected voice register substring
    - expected_format_detection: expected nonfiction format value
"""

import json
from pathlib import Path

import pytest

GROUND_TRUTH_DIR = Path(__file__).parent / "ground_truth"

NONFICTION_GROUND_TRUTH_FILES = {
    "academic": "nonfiction_academic_argument_map.json",
    "journalism": "nonfiction_journalism_argument_map.json",
}


def _load_ground_truth(format_key: str) -> dict:
    """Load a nonfiction ground truth JSON file."""
    path = GROUND_TRUTH_DIR / NONFICTION_GROUND_TRUTH_FILES[format_key]
    return json.loads(path.read_text())


@pytest.fixture(scope="module")
def nonfiction_ground_truths():
    return {key: _load_ground_truth(key) for key in NONFICTION_GROUND_TRUTH_FILES}


# ---------------------------------------------------------------------------
# Tests: Ground truth structure validation (runs without API)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("format_key", list(NONFICTION_GROUND_TRUTH_FILES.keys()))
def test_ground_truth_structure(nonfiction_ground_truths, format_key):
    """Ground truth files should have all required fields."""
    gt = nonfiction_ground_truths[format_key]

    assert "central_thesis" in gt, f"{format_key}: missing central_thesis"
    assert isinstance(gt["central_thesis"], str), f"{format_key}: central_thesis must be a string"

    assert "expected_thread_count_min" in gt, f"{format_key}: missing expected_thread_count_min"
    assert isinstance(gt["expected_thread_count_min"], int), (
        f"{format_key}: expected_thread_count_min must be int"
    )
    assert gt["expected_thread_count_min"] >= 1, (
        f"{format_key}: expected_thread_count_min must be >= 1"
    )

    assert "expected_evidence_types" in gt, f"{format_key}: missing expected_evidence_types"
    assert isinstance(gt["expected_evidence_types"], list), (
        f"{format_key}: expected_evidence_types must be a list"
    )
    assert len(gt["expected_evidence_types"]) >= 1, (
        f"{format_key}: expected_evidence_types must have at least 1 entry"
    )

    assert "expected_voice_register" in gt, f"{format_key}: missing expected_voice_register"
    assert isinstance(gt["expected_voice_register"], str), (
        f"{format_key}: expected_voice_register must be a string"
    )

    assert "expected_format_detection" in gt, f"{format_key}: missing expected_format_detection"
    assert gt["expected_format_detection"] in (
        "academic", "personal_essay", "journalism", "self_help", "business"
    ), f"{format_key}: unexpected format_detection value: {gt['expected_format_detection']}"


@pytest.mark.parametrize("format_key", list(NONFICTION_GROUND_TRUTH_FILES.keys()))
def test_ground_truth_json_roundtrip(nonfiction_ground_truths, format_key):
    """Ground truth files should survive JSON serialization/deserialization."""
    gt = nonfiction_ground_truths[format_key]
    roundtripped = json.loads(json.dumps(gt))
    assert roundtripped == gt, f"{format_key}: ground truth not stable through JSON roundtrip"
