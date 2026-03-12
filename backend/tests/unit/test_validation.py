import io
import zipfile

import pytest
from httpx import AsyncClient


def make_docx_bytes() -> bytes:
    """Create a minimal valid .docx file (ZIP with word/document.xml)."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("word/document.xml", "<document></document>")
        zf.writestr("[Content_Types].xml", "<Types></Types>")
    return buf.getvalue()


def make_pdf_bytes() -> bytes:
    """Create a minimal PDF-like file."""
    return b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\n"


class TestFileValidation:
    @pytest.mark.asyncio
    async def test_empty_file_rejected(self, client: AsyncClient, db_session):
        """Empty files should be rejected with 422."""
        from unittest.mock import patch

        # Create a full user for auth
        from app.auth.security import hash_password
        from app.db.models import User

        user = User(
            email="uploader@example.com",
            password_hash=hash_password("password123"),
            is_provisional=False,
            email_verified=True,
        )
        db_session.add(user)
        await db_session.commit()

        # Login to get cookie
        login_resp = await client.post(
            "/auth/login", json={"email": "uploader@example.com", "password": "password123"}
        )
        assert login_resp.status_code == 200

        files = {"file": ("test.txt", b"", "text/plain")}
        data = {"title": "Test Manuscript"}
        resp = await client.post("/manuscripts/upload", files=files, data=data)
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_oversized_file_rejected(self, client: AsyncClient, db_session):
        """Files > 10MB should be rejected."""
        from app.auth.security import hash_password
        from app.db.models import User

        user = User(
            email="uploader2@example.com",
            password_hash=hash_password("password123"),
            is_provisional=False,
            email_verified=True,
        )
        db_session.add(user)
        await db_session.commit()

        await client.post(
            "/auth/login", json={"email": "uploader2@example.com", "password": "password123"}
        )

        big_content = b"x" * (11 * 1024 * 1024)  # 11MB
        files = {"file": ("test.txt", big_content, "text/plain")}
        data = {"title": "Big Manuscript"}
        resp = await client.post("/manuscripts/upload", files=files, data=data)
        assert resp.status_code == 413
