"""Tests for feedback export (PDF and DOCX).

Per DECISION-010: verify generation produces valid output,
auth scoping works, and invalid format is rejected.
"""

import io
import zipfile

import pytest

from app.analysis.export import generate_feedback_docx, generate_feedback_pdf

# --- Test data ---

SAMPLE_SUMMARY = {
    "total_issues": 5,
    "critical": 1,
    "warning": 2,
    "note": 2,
    "chapters_analyzed": 2,
    "chapters_total": 3,
}

SAMPLE_CHAPTERS = [
    {
        "chapter_number": 1,
        "title": "The Beginning",
        "word_count": 3200,
        "status": "analyzed",
        "issues": [
            {
                "severity": "critical",
                "type": "consistency",
                "description": "Character age changes from 25 to 30 mid-chapter.",
                "suggestion": "Pick one age and stick with it.",
            },
            {
                "severity": "warning",
                "type": "pacing",
                "description": "Long exposition slows momentum.",
                "suggestion": "Break into shorter scenes.",
            },
        ],
    },
    {
        "chapter_number": 2,
        "title": "The Middle",
        "word_count": 4100,
        "status": "analyzed",
        "issues": [
            {
                "severity": "note",
                "type": "voice",
                "description": "Dialogue tag usage could be more varied.",
                "suggestion": "Use action beats instead of said.",
            },
        ],
    },
    {
        "chapter_number": 3,
        "title": None,
        "word_count": None,
        "status": "pending",
        "issues": [],
    },
]

SAMPLE_DOC_SUMMARY = {
    "overall_assessment": "The manuscript presents a compelling argument but needs stronger evidence.",
    "thesis_clarity_score": "clear",
    "argument_coherence": "mostly_coherent",
    "evidence_density": "uneven",
    "tone_consistency": "consistent",
    "top_strengths": ["Clear thesis", "Engaging style"],
    "top_priorities": ["More evidence in chapter 3", "Tighten conclusion"],
    "format_specific_notes": "Consider adding a bibliography.",
}


class TestGeneratePdf:
    def test_produces_nonempty_bytes(self):
        result = generate_feedback_pdf(
            "Test Manuscript", "Fantasy", SAMPLE_SUMMARY, SAMPLE_CHAPTERS
        )
        assert isinstance(result, bytes)
        assert len(result) > 100
        # PDF magic bytes
        assert result[:5] == b"%PDF-"

    def test_with_nonfiction_summary(self):
        result = generate_feedback_pdf(
            "Nonfiction Test",
            "Academic",
            SAMPLE_SUMMARY,
            SAMPLE_CHAPTERS,
            document_summary=SAMPLE_DOC_SUMMARY,
        )
        assert isinstance(result, bytes)
        assert len(result) > 100
        assert result[:5] == b"%PDF-"

    def test_empty_chapters(self):
        result = generate_feedback_pdf("Empty", None, SAMPLE_SUMMARY, [])
        assert isinstance(result, bytes)
        assert result[:5] == b"%PDF-"

    def test_special_characters_in_title(self):
        result = generate_feedback_pdf(
            "Title with <special> & \"chars\"", None, SAMPLE_SUMMARY, SAMPLE_CHAPTERS
        )
        assert isinstance(result, bytes)
        assert result[:5] == b"%PDF-"


class TestGenerateDocx:
    def test_produces_valid_docx(self):
        result = generate_feedback_docx(
            "Test Manuscript", "Fantasy", SAMPLE_SUMMARY, SAMPLE_CHAPTERS
        )
        assert isinstance(result, bytes)
        assert len(result) > 100
        # DOCX is a ZIP file containing word/document.xml
        buf = io.BytesIO(result)
        assert zipfile.is_zipfile(buf)
        with zipfile.ZipFile(buf) as zf:
            assert "word/document.xml" in zf.namelist()

    def test_with_nonfiction_summary(self):
        result = generate_feedback_docx(
            "Nonfiction Test",
            "Academic",
            SAMPLE_SUMMARY,
            SAMPLE_CHAPTERS,
            document_summary=SAMPLE_DOC_SUMMARY,
        )
        assert isinstance(result, bytes)
        buf = io.BytesIO(result)
        assert zipfile.is_zipfile(buf)

    def test_empty_chapters(self):
        result = generate_feedback_docx("Empty", None, SAMPLE_SUMMARY, [])
        assert isinstance(result, bytes)
        buf = io.BytesIO(result)
        assert zipfile.is_zipfile(buf)


class TestExportEndpoint:
    """Tests for the export API endpoint.

    These require the full app test fixtures (db_session, client)
    from conftest.py. They test auth scoping and format validation.
    """

    @pytest.mark.asyncio
    async def test_returns_404_for_nonexistent_manuscript(self, client, db_session):
        """Export should return 404 for a manuscript that doesn't exist."""
        import uuid

        from app.db.models import User

        # Create a user and set auth cookie
        user = User(
            id=uuid.uuid4(),
            email="exporter@test.com",
            password_hash="fakehash",
            is_provisional=False,
            email_verified=True,
        )
        db_session.add(user)
        await db_session.commit()

        # Login to get cookie
        from unittest.mock import patch

        with patch("app.auth.router.verify_password", return_value=True):
            login_resp = await client.post(
                "/auth/login",
                json={"email": "exporter@test.com", "password": "test"},
            )
        assert login_resp.status_code == 200

        # Try to export nonexistent manuscript
        fake_id = uuid.uuid4()
        resp = await client.get(f"/bible/{fake_id}/feedback/export?format=pdf")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_returns_400_for_invalid_format(self, client, db_session):
        """Export should return 422 for an unsupported format parameter."""
        import uuid

        from app.db.models import User

        user = User(
            id=uuid.uuid4(),
            email="exporter2@test.com",
            password_hash="fakehash",
            is_provisional=False,
            email_verified=True,
        )
        db_session.add(user)
        await db_session.commit()

        from unittest.mock import patch

        with patch("app.auth.router.verify_password", return_value=True):
            login_resp = await client.post(
                "/auth/login",
                json={"email": "exporter2@test.com", "password": "test"},
            )
        assert login_resp.status_code == 200

        fake_id = uuid.uuid4()
        resp = await client.get(f"/bible/{fake_id}/feedback/export?format=xlsx")
        # FastAPI returns 422 for invalid enum values
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_returns_404_for_other_users_manuscript(self, client, db_session):
        """Export should return 404 when accessing another user's manuscript."""
        import uuid
        from datetime import datetime, timezone

        from app.db.models import (
            DocumentType,
            Manuscript,
            ManuscriptStatus,
            PaymentStatus,
            User,
        )

        # Create owner and attacker
        owner = User(
            id=uuid.uuid4(),
            email="owner@test.com",
            password_hash="fakehash",
            is_provisional=False,
            email_verified=True,
        )
        attacker = User(
            id=uuid.uuid4(),
            email="attacker@test.com",
            password_hash="fakehash",
            is_provisional=False,
            email_verified=True,
        )
        db_session.add_all([owner, attacker])
        await db_session.flush()

        # Create manuscript owned by owner
        ms = Manuscript(
            id=uuid.uuid4(),
            user_id=owner.id,
            title="Owner's Book",
            status=ManuscriptStatus.complete,
            payment_status=PaymentStatus.paid,
            document_type=DocumentType.fiction,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        db_session.add(ms)
        await db_session.commit()

        # Login as attacker
        from unittest.mock import patch

        with patch("app.auth.router.verify_password", return_value=True):
            login_resp = await client.post(
                "/auth/login",
                json={"email": "attacker@test.com", "password": "test"},
            )
        assert login_resp.status_code == 200

        # Try to export owner's manuscript as attacker
        resp = await client.get(f"/bible/{ms.id}/feedback/export?format=pdf")
        assert resp.status_code == 404
