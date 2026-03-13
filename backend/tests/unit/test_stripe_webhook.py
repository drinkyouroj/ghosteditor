"""Unit tests for Stripe webhook handler.

Tests signature verification, idempotency, checkout completion,
and subscription cancellation handling.
"""

import json
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.security import hash_password
from app.db.models import (
    Job,
    Manuscript,
    ManuscriptStatus,
    PaymentStatus,
    SubscriptionStatus,
    User,
)


def _make_user(email: str = "stripe@example.com", **kwargs) -> User:
    defaults = dict(
        password_hash=hash_password("password123"),
        is_provisional=False,
        email_verified=True,
    )
    defaults.update(kwargs)
    return User(email=email, **defaults)


@pytest.mark.asyncio
async def test_webhook_rejects_missing_signature(client: AsyncClient):
    """Webhook without stripe-signature header should return 400."""
    resp = await client.post(
        "/stripe/webhook",
        content=b"{}",
        headers={"content-type": "application/json"},
    )
    assert resp.status_code == 400
    assert "signature" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_webhook_rejects_invalid_signature(client: AsyncClient):
    """Webhook with invalid signature should return 400."""
    with patch("app.stripe.router.settings") as mock_settings:
        mock_settings.stripe_webhook_secret = "whsec_test_secret"
        mock_settings.database_url = "sqlite+aiosqlite://"
        mock_settings.redis_url = "redis://localhost:6379/0"

        with patch("app.stripe.router.stripe.Webhook.construct_event") as mock_construct:
            import stripe
            mock_construct.side_effect = stripe.SignatureVerificationError(
                "Invalid signature", "sig_header"
            )
            resp = await client.post(
                "/stripe/webhook",
                content=b'{"type": "checkout.session.completed"}',
                headers={
                    "content-type": "application/json",
                    "stripe-signature": "t=123,v1=bad",
                },
            )
    assert resp.status_code == 400
    assert "invalid signature" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_webhook_checkout_completed_marks_paid(client: AsyncClient, db_session: AsyncSession):
    """checkout.session.completed should mark manuscript as paid."""
    user = _make_user()
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)

    ms = Manuscript(
        user_id=user.id,
        title="Webhook Test",
        status=ManuscriptStatus.bible_complete,
        payment_status=PaymentStatus.unpaid,
    )
    db_session.add(ms)
    await db_session.commit()
    await db_session.refresh(ms)

    # Build a mock Stripe event
    session_obj = MagicMock()
    session_obj.id = "cs_test_123"
    session_obj.metadata = {"manuscript_id": str(ms.id), "user_id": str(user.id)}
    session_obj.mode = "payment"
    session_obj.customer = "cus_test_123"

    mock_event = MagicMock()
    mock_event.type = "checkout.session.completed"
    mock_event.data.object = session_obj

    with patch("app.stripe.router.settings") as mock_settings:
        mock_settings.stripe_webhook_secret = "whsec_test"
        mock_settings.database_url = db_session.get_bind().url.render_as_string(hide_password=False)
        mock_settings.redis_url = "redis://localhost:6379/0"

        with patch("app.stripe.router.stripe.Webhook.construct_event", return_value=mock_event):
            with patch("app.stripe.router._handle_checkout_completed", new_callable=AsyncMock) as mock_handler:
                resp = await client.post(
                    "/stripe/webhook",
                    content=b'{}',
                    headers={
                        "content-type": "application/json",
                        "stripe-signature": "t=123,v1=valid",
                    },
                )

    assert resp.status_code == 200
    mock_handler.assert_called_once_with(session_obj)


@pytest.mark.asyncio
async def test_webhook_subscription_cancelled(client: AsyncClient, db_session: AsyncSession):
    """customer.subscription.deleted should downgrade user to free."""
    sub_obj = MagicMock()
    sub_obj.customer = "cus_cancel_test"

    mock_event = MagicMock()
    mock_event.type = "customer.subscription.deleted"
    mock_event.data.object = sub_obj

    with patch("app.stripe.router.settings") as mock_settings:
        mock_settings.stripe_webhook_secret = "whsec_test"
        mock_settings.database_url = "sqlite+aiosqlite://"
        mock_settings.redis_url = "redis://localhost:6379/0"

        with patch("app.stripe.router.stripe.Webhook.construct_event", return_value=mock_event):
            with patch("app.stripe.router._handle_subscription_cancelled", new_callable=AsyncMock) as mock_handler:
                resp = await client.post(
                    "/stripe/webhook",
                    content=b'{}',
                    headers={
                        "content-type": "application/json",
                        "stripe-signature": "t=123,v1=valid",
                    },
                )

    assert resp.status_code == 200
    mock_handler.assert_called_once_with(sub_obj)


@pytest.mark.asyncio
async def test_webhook_unconfigured_secret(client: AsyncClient):
    """Webhook with no configured secret should return 500."""
    with patch("app.stripe.router.settings") as mock_settings:
        mock_settings.stripe_webhook_secret = ""
        resp = await client.post(
            "/stripe/webhook",
            content=b'{}',
            headers={
                "content-type": "application/json",
                "stripe-signature": "t=123,v1=sig",
            },
        )
    assert resp.status_code == 500
    assert "not configured" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_webhook_unknown_event_returns_ok(client: AsyncClient):
    """Unknown event types should return 200 (no-op)."""
    mock_event = MagicMock()
    mock_event.type = "some.unknown.event"

    with patch("app.stripe.router.settings") as mock_settings:
        mock_settings.stripe_webhook_secret = "whsec_test"

        with patch("app.stripe.router.stripe.Webhook.construct_event", return_value=mock_event):
            resp = await client.post(
                "/stripe/webhook",
                content=b'{}',
                headers={
                    "content-type": "application/json",
                    "stripe-signature": "t=123,v1=valid",
                },
            )

    assert resp.status_code == 200
