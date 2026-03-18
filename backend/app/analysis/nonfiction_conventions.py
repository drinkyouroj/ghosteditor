"""Nonfiction convention template loader for section analysis.

Maps nonfiction format enum values to convention template files in the prompts
directory. Mirrors the pattern from genre_conventions.py.
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).parent / "prompts"

# Maps nonfiction_format enum values to template filenames
_FORMAT_TO_FILE: dict[str, str] = {
    "academic": "nonfiction_conventions_academic.txt",
    "personal_essay": "nonfiction_conventions_personal_essay.txt",
    "journalism": "nonfiction_conventions_journalism.txt",
    "self_help": "nonfiction_conventions_self_help.txt",
    "business": "nonfiction_conventions_business.txt",
}


def get_nonfiction_conventions(format_name: str) -> str:
    """Load the convention template for the given nonfiction format.

    Args:
        format_name: One of the nonfiction_format enum values
            (academic, personal_essay, journalism, self_help, business).

    Returns:
        The convention template text, or empty string if format is unknown.
    """
    if not format_name:
        logger.warning("Empty nonfiction format provided, returning no conventions")
        return ""

    normalized = format_name.lower().strip()
    filename = _FORMAT_TO_FILE.get(normalized)

    if filename is None:
        logger.warning(
            "Unknown nonfiction format %r — no conventions available. "
            "Valid formats: %s",
            format_name,
            ", ".join(sorted(_FORMAT_TO_FILE.keys())),
        )
        return ""

    path = PROMPTS_DIR / filename
    if not path.exists():
        logger.error(
            "Convention template file missing: %s. "
            "Expected at %s",
            filename,
            path,
        )
        return ""

    return path.read_text()
