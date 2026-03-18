"""TC-004: Enhanced Stripe webhook tests.

Supplements test_stripe_webhook.py with:
- Duplicate webhook idempotency (same session_id)
- Webhook for non-existent manuscript
- Direct handler tests for checkout completion and subscription cancellation
"""

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.security import hash_password
from app.db.models import (
    Chapter,
    ChapterStatus,
    Manuscript,
    ManuscriptStatus,
    PaymentStatus,
    SubscriptionStatus,
    User,
)


def _make_user(email: str = "stripe-enh@example.com", **kwargs) -> User:
    defaults = dict(
        password_hash=hash_password("password123"),
        is_provisional=False,
        email_verified=True,
    )
    defaults.update(kwargs)
    return User(email=email, **defaults)


@pytest.mark.asyncio
async def test_checkout_completed_handler_marks_paid(db_session: AsyncSession):
    """_handle_checkout_completed should set payment_status=paid on the manuscript."""
    from app.stripe.router import _handle_checkout_completed

    user = _make_user("checkout-handler@example.com")
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)

    ms = Manuscript(
        user_id=user.id,
        title="Checkout Handler Test",
        status=ManuscriptStatus.bible_complete,
        payment_status=PaymentStatus.unpaid,
    )
    db_session.add(ms)
    await db_session.commit()
    await db_session.refresh(ms)

    session_obj = MagicMock()
    session_obj.id = "cs_handler_test_123"
    session_obj.metadata = {"manuscript_id": str(ms.id), "user_id": str(user.id)}
    session_obj.mode = "payment"
    session_obj.customer = "cus_handler_test"

    with patch("app.stripe.router.settings") as mock_settings:
        mock_settings.database_url = db_session.get_bind().url.render_as_string(hide_password=False)
        mock_settings.redis_url = "redis://localhost:6380/0"

        with patch("app.stripe.router.create_pool", new_callable=AsyncMock):
            await _handle_checkout_completed(session_obj)

    await db_session.expire_all()
    result = await db_session.execute(select(Manuscript).where(Manuscript.id == ms.id))
    updated_ms = result.scalar_one()
    assert updated_ms.payment_status == PaymentStatus.paid
    assert updated_ms.stripe_session_id == "cs_handler_test_123"


@pytest.mark.asyncio
async def test_checkout_completed_idempotent_duplicate(db_session: AsyncSession):
    """Duplicate webhook with same session_id should be a no-op."""
    from app.stripe.router import _handle_checkout_completed

    user = _make_user("checkout-idem@example.com")
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)

    ms = Manuscript(
        user_id=user.id,
        title="Idempotent Test",
        status=ManuscriptStatus.bible_complete,
        payment_status=PaymentStatus.paid,
        stripe_session_id="cs_duplicate_123",
    )
    db_session.add(ms)
    await db_session.commit()
    await db_session.refresh(ms)

    session_obj = MagicMock()
    session_obj.id = "cs_duplicate_123"
    session_obj.metadata = {"manuscript_id": str(ms.id), "user_id": str(user.id)}
    session_obj.mode = "payment"
    session_obj.customer = "cus_dup_test"

    with patch("app.stripe.router.settings") as mock_settings:
        mock_settings.database_url = db_session.get_bind().url.render_as_string(hide_password=False)
        mock_settings.redis_url = "redis://localhost:6380/0"

        # Should return early without error
        await _handle_checkout_completed(session_obj)

    # Manuscript should remain unchanged
    await db_session.expire_all()
    result = await db_session.execute(select(Manuscript).where(Manuscript.id == ms.id))
    updated_ms = result.scalar_one()
    assert updated_ms.payment_status == PaymentStatus.paid
    assert updated_ms.stripe_session_id == "cs_duplicate_123"


@pytest.mark.asyncio
async def test_checkout_completed_nonexistent_manuscript(db_session: AsyncSession):
    """Webhook for non-existent manuscript should not crash."""
    from app.stripe.router import _handle_checkout_completed

    session_obj = MagicMock()
    session_obj.id = "cs_nonexistent_123"
    session_obj.metadata = {
        "manuscript_id": str(uuid.uuid4()),
        "user_id": str(uuid.uuid4()),
    }
    session_obj.mode = "payment"
    session_obj.customer = "cus_nonexistent"

    with patch("app.stripe.router.settings") as mock_settings:
        mock_settings.database_url = db_session.get_bind().url.render_as_string(hide_password=False)
        mock_settings.redis_url = "redis://localhost:6380/0"

        # Should return gracefully without error
        await _handle_checkout_completed(session_obj)


@pytest.mark.asyncio
async def test_checkout_completed_missing_metadata(db_session: AsyncSession):
    """Webhook with missing metadata fields should not crash."""
    from app.stripe.router import _handle_checkout_completed

    session_obj = MagicMock()
    session_obj.id = "cs_no_meta_123"
    session_obj.metadata = {}
    session_obj.mode = "payment"
    session_obj.customer = "cus_no_meta"

    with patch("app.stripe.router.settings") as mock_settings:
        mock_settings.database_url = db_session.get_bind().url.render_as_string(hide_password=False)
        mock_settings.redis_url = "redis://localhost:6380/0"

        # Should return early without error
        await _handle_checkout_completed(session_obj)


@pytest.mark.asyncio
async def test_subscription_cancelled_handler_sets_free(db_session: AsyncSession):
    """_handle_subscription_cancelled should set user to free tier."""
    from app.stripe.router import _handle_subscription_cancelled

    user = _make_user(
        "sub-cancel@example.com",
        stripe_customer_id="cus_cancel_direct",
        subscription_status=SubscriptionStatus.subscribed,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)

    sub_obj = MagicMock()
    sub_obj.customer = "cus_cancel_direct"

    with patch("app.stripe.router.settings") as mock_settings:
        mock_settings.database_url = db_session.get_bind().url.render_as_string(hide_password=False)

        await _handle_subscription_cancelled(sub_obj)

    await db_session.expire_all()
    result = await db_session.execute(select(User).where(User.id == user.id))
    updated_user = result.scalar_one()
    assert updated_user.subscription_status == SubscriptionStatus.free


@pytest.mark.asyncio
async def test_subscription_cancelled_nonexistent_customer(db_session: AsyncSession):
    """Cancellation for non-existent customer should not crash."""
    from app.stripe.router import _handle_subscription_cancelled

    sub_obj = MagicMock()
    sub_obj.customer = "cus_doesnt_exist"

    with patch("app.stripe.router.settings") as mock_settings:
        mock_settings.database_url = db_session.get_bind().url.render_as_string(hide_password=False)

        # Should return gracefully
        await _handle_subscription_cancelled(sub_obj)
