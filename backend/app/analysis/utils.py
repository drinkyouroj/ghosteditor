"""Shared utilities for analysis modules."""

from __future__ import annotations


def sanitize_manuscript_text(text: str) -> str:
    """Escape closing manuscript_text tags to prevent prompt injection.

    Per DECISION_004 JUDGE amendment #3: any raw manuscript text included
    in a prompt must have </manuscript_text> tags escaped so that user
    content cannot break out of the XML wrapper.
    """
    return text.replace("</manuscript_text>", "&lt;/manuscript_text&gt;")
