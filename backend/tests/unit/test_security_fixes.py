"""Unit tests for security audit fixes (SEC-001 through SEC-011).

Each test validates a specific security remediation from the audit report.
"""

import html
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.security import generate_token, hash_password, hash_token
from app.db.models import (
    EmailEvent,
    Manuscript,
    ManuscriptStatus,
    PaymentStatus,
    User,
)


# ---------------------------------------------------------------------------
# SEC-001: JWT secret startup guard
# ---------------------------------------------------------------------------


class TestJWTSecretGuard:
    def test_is_dev_mode_with_localhost_base_url(self):
        """Dev mode detected when base_url contains localhost."""
        from app.main import _is_dev_mode

        with patch("app.main.settings") as mock_settings:
            mock_settings.base_url = "http://localhost:5173"
            mock_settings.s3_endpoint_url = ""
            assert _is_dev_mode() is True

    def test_is_dev_mode_with_localhost_s3(self):
        """Dev mode detected when s3_endpoint_url contains localhost."""
        from app.main import _is_dev_mode

        with patch("app.main.settings") as mock_settings:
            mock_settings.base_url = "https://app.ghosteditor.com"
            mock_settings.s3_endpoint_url = "http://localhost:9000"
            assert _is_dev_mode() is True

    def test_not_dev_mode_in_production(self):
        """Production detected when neither URL contains localhost."""
        from app.main import _is_dev_mode

        with patch("app.main.settings") as mock_settings:
            mock_settings.base_url = "https://app.ghosteditor.com"
            mock_settings.s3_endpoint_url = "https://s3.amazonaws.com"
            assert _is_dev_mode() is False

    @pytest.mark.asyncio
    async def test_startup_raises_on_default_secret_in_production(self):
        """Startup should raise RuntimeError with default JWT secret in production."""
        from app.main import lifespan, app

        with patch("app.main.settings") as mock_settings:
            mock_settings.jwt_secret_key = "change-me-in-production"
            mock_settings.base_url = "https://app.ghosteditor.com"
            mock_settings.s3_endpoint_url = ""
            with pytest.raises(RuntimeError, match="JWT_SECRET_KEY"):
                async with lifespan(app):
                    pass

    @pytest.mark.asyncio
    async def test_startup_allows_default_secret_in_dev(self):
        """Startup should not raise with default JWT secret in dev mode."""
        from app.main import lifespan, app

        with patch("app.main.settings") as mock_settings:
            mock_settings.jwt_secret_key = "change-me-in-production"
            mock_settings.base_url = "http://localhost:5173"
            mock_settings.s3_endpoint_url = "http://localhost:9000"
            # Should not raise — may fail on S3 bucket check, which is fine
            try:
                async with lifespan(app):
                    pass
            except RuntimeError as e:
                if "JWT_SECRET_KEY" in str(e):
                    pytest.fail("Should not raise JWT error in dev mode")


# ---------------------------------------------------------------------------
# SEC-002: Stripe webhook user_id scoping
# ---------------------------------------------------------------------------


class TestStripeWebhookUserScoping:
    @pytest.mark.asyncio
    async def test_webhook_mismatched_user_does_not_mark_paid(
        self, db_session: AsyncSession
    ):
        """A webhook with wrong user_id should not mark the manuscript as paid."""
        from app.stripe.router import _handle_checkout_completed

        # Create two users
        user_a = User(
            email="user-a@example.com",
            password_hash=hash_password("password"),
            is_provisional=False,
            email_verified=True,
        )
        user_b = User(
            email="user-b@example.com",
            password_hash=hash_password("password"),
            is_provisional=False,
            email_verified=True,
        )
        db_session.add_all([user_a, user_b])
        await db_session.commit()
        await db_session.refresh(user_a)
        await db_session.refresh(user_b)

        # Create manuscript owned by user_a
        ms = Manuscript(
            user_id=user_a.id,
            title="User A's Book",
            status=ManuscriptStatus.bible_complete,
            payment_status=PaymentStatus.unpaid,
        )
        db_session.add(ms)
        await db_session.commit()
        await db_session.refresh(ms)

        ms_id = ms.id

        # Simulate webhook with user_b's ID but user_a's manuscript
        session_obj = MagicMock()
        session_obj.id = "cs_test_mismatch"
        session_obj.metadata = {
            "manuscript_id": str(ms_id),
            "user_id": str(user_b.id),  # Wrong user!
        }
        session_obj.mode = "payment"
        session_obj.customer = "cus_test"

        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine, AsyncSession as AS

        db_url = db_session.get_bind().url.render_as_string(hide_password=False)
        engine = create_async_engine(db_url, echo=False)
        factory = async_sessionmaker(engine, class_=AS, expire_on_commit=False)

        with patch("app.stripe.router._get_webhook_session_factory", return_value=factory):
            with patch("app.stripe.router.settings") as mock_settings:
                mock_settings.redis_url = "redis://localhost:6379/0"
                await _handle_checkout_completed(session_obj)

        await engine.dispose()

        # Refresh and verify manuscript is still unpaid
        db_session.expire_all()
        result = await db_session.execute(
            select(Manuscript).where(Manuscript.id == ms_id)
        )
        refreshed_ms = result.scalar_one()
        assert refreshed_ms.payment_status == PaymentStatus.unpaid


# ---------------------------------------------------------------------------
# SEC-003: Verification token reuse
# ---------------------------------------------------------------------------


class TestVerificationTokenReuse:
    @pytest.mark.asyncio
    async def test_verify_email_twice_fails_second_time(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Second verification with the same token should fail."""
        token = generate_token()
        user = User(
            email="verify-once@example.com",
            verification_token=hash_token(token),
            verification_token_expires=datetime.now(timezone.utc) + timedelta(hours=1),
            email_verified=False,
        )
        db_session.add(user)
        await db_session.commit()

        # First verification — should succeed (302 redirect on success)
        resp1 = await client.get(f"/auth/verify-email?token={token}", follow_redirects=False)
        assert resp1.status_code == 302

        # Second verification — should fail because email_verified is now True
        resp2 = await client.get(f"/auth/verify-email?token={token}")
        assert resp2.status_code == 400


# ---------------------------------------------------------------------------
# SEC-004: Password reset token race condition
# ---------------------------------------------------------------------------


class TestPasswordResetTokenReuse:
    @pytest.mark.asyncio
    async def test_reset_password_twice_fails_second_time(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Second password reset with the same token should fail."""
        token = generate_token()
        user = User(
            email="reset-once@example.com",
            password_hash=hash_password("oldpassword"),
            password_reset_token=hash_token(token),
            password_reset_token_expires=datetime.now(timezone.utc) + timedelta(hours=1),
            is_provisional=False,
            email_verified=True,
        )
        db_session.add(user)
        await db_session.commit()

        # First reset — should succeed
        resp1 = await client.post(
            "/auth/reset-password",
            json={"token": token, "new_password": "newpassword123"},
        )
        assert resp1.status_code == 200

        # Second reset — should fail because token was consumed
        resp2 = await client.post(
            "/auth/reset-password",
            json={"token": token, "new_password": "anotherpassword"},
        )
        assert resp2.status_code == 400


# ---------------------------------------------------------------------------
# SEC-009: Email HTML injection
# ---------------------------------------------------------------------------


class TestEmailHTMLInjection:
    def test_bible_ready_email_escapes_title(self):
        """Manuscript title with HTML tags should be escaped in email output."""
        from app.email.sender import send_bible_ready_email

        malicious_title = '<script>alert("xss")</script>'

        with patch("app.email.sender._send") as mock_send:
            mock_send.return_value = "msg-id"
            send_bible_ready_email(
                "test@example.com",
                malicious_title,
                "https://example.com/bible",
            )

            call_args = mock_send.call_args
            html_body = call_args.kwargs.get("html") or call_args[1].get("html")
            # The raw script tag should NOT appear in the HTML
            assert "<script>" not in html_body
            # The escaped version should appear
            assert html.escape(malicious_title) in html_body

    def test_drip_email_escapes_title(self):
        """Drip email should escape manuscript title."""
        from app.email.sender import send_drip_email_1

        malicious_title = '<img src=x onerror=alert(1)>'

        with patch("app.email.sender._send") as mock_send:
            mock_send.return_value = "msg-id"
            send_drip_email_1(
                "test@example.com",
                malicious_title,
                "https://example.com/bible",
            )

            call_args = mock_send.call_args
            html_body = call_args.kwargs.get("html") or call_args[1].get("html")
            assert "<img src=x" not in html_body
            assert html.escape(malicious_title) in html_body


# ---------------------------------------------------------------------------
# SEC-010: Login rate limiting
# ---------------------------------------------------------------------------


class TestLoginRateLimit:
    @pytest.mark.asyncio
    async def test_login_rate_limit_blocks_after_threshold(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """11 login attempts in 15 minutes should return 429."""
        user = User(
            email="ratelimit@example.com",
            password_hash=hash_password("password123"),
            is_provisional=False,
            email_verified=True,
        )
        db_session.add(user)
        await db_session.commit()

        mock_redis = AsyncMock()
        # Simulate count exceeding limit on the 11th request
        mock_redis.incr = AsyncMock(return_value=11)
        mock_redis.ttl = AsyncMock(return_value=600)
        mock_redis.aclose = AsyncMock()

        with patch("app.rate_limit._get_redis", return_value=mock_redis):
            resp = await client.post(
                "/auth/login",
                json={"email": "ratelimit@example.com", "password": "password123"},
            )
            assert resp.status_code == 429
            assert "rate limit" in resp.json()["detail"].lower()


# ---------------------------------------------------------------------------
# SEC-011: Password reset rate limiting
# ---------------------------------------------------------------------------


class TestPasswordResetRateLimit:
    @pytest.mark.asyncio
    async def test_forgot_password_rate_limit_blocks_after_threshold(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """4 password reset requests in 1 hour should return 429."""
        mock_redis = AsyncMock()
        # Simulate count exceeding limit (4th request, limit is 3)
        mock_redis.incr = AsyncMock(return_value=4)
        mock_redis.ttl = AsyncMock(return_value=3000)
        mock_redis.aclose = AsyncMock()

        with patch("app.rate_limit._get_redis", return_value=mock_redis):
            resp = await client.post(
                "/auth/forgot-password",
                json={"email": "reset-limit@example.com"},
            )
            assert resp.status_code == 429
            assert "rate limit" in resp.json()["detail"].lower()


# ---------------------------------------------------------------------------
# SEC-008: Hard purge cron
# ---------------------------------------------------------------------------


class TestHardPurgeCron:
    @pytest.mark.asyncio
    async def test_purge_deletes_old_soft_deleted_user(self, db_session: AsyncSession):
        """Users soft-deleted more than 30 days ago should be hard-deleted."""
        from app.jobs.worker import _purge_deleted_data

        # Create a user soft-deleted 31 days ago
        old_user = User(
            email="old-delete@example.com",
            deleted_at=datetime.now(timezone.utc) - timedelta(days=31),
            is_provisional=False,
            email_verified=True,
        )
        db_session.add(old_user)
        await db_session.commit()
        await db_session.refresh(old_user)
        user_id = old_user.id

        # Run the purge with a mock context
        with patch("app.jobs.worker._get_session_factory") as mock_factory:
            # Use the real session factory that returns our test session
            from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine, AsyncSession as AS

            engine = create_async_engine(
                db_session.get_bind().url.render_as_string(hide_password=False),
                echo=False,
            )
            real_factory = async_sessionmaker(engine, class_=AS, expire_on_commit=False)
            mock_factory.return_value = real_factory

            await _purge_deleted_data(ctx={})

            await engine.dispose()

        # Verify the user is gone
        db_session.expire_all()
        result = await db_session.execute(select(User).where(User.id == user_id))
        assert result.scalar_one_or_none() is None

    @pytest.mark.asyncio
    async def test_purge_ignores_recently_deleted_user(self, db_session: AsyncSession):
        """Users soft-deleted less than 30 days ago should NOT be hard-deleted."""
        from app.jobs.worker import _purge_deleted_data

        # Create a user soft-deleted 10 days ago
        recent_user = User(
            email="recent-delete@example.com",
            deleted_at=datetime.now(timezone.utc) - timedelta(days=10),
            is_provisional=False,
            email_verified=True,
        )
        db_session.add(recent_user)
        await db_session.commit()
        await db_session.refresh(recent_user)
        user_id = recent_user.id

        with patch("app.jobs.worker._get_session_factory") as mock_factory:
            from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine, AsyncSession as AS

            engine = create_async_engine(
                db_session.get_bind().url.render_as_string(hide_password=False),
                echo=False,
            )
            real_factory = async_sessionmaker(engine, class_=AS, expire_on_commit=False)
            mock_factory.return_value = real_factory

            await _purge_deleted_data(ctx={})

            await engine.dispose()

        # Verify the user still exists
        db_session.expire_all()
        result = await db_session.execute(select(User).where(User.id == user_id))
        assert result.scalar_one_or_none() is not None
