"""Unit tests for drip email scheduling and dispatch.

Tests the 3-email sequence (2h, 2d, 5d), skip-on-paid logic,
and deleted user/manuscript handling.
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from unittest.mock import patch, MagicMock

from app.db.models import EmailEvent, Manuscript, ManuscriptStatus, PaymentStatus, User
from app.email.drip import schedule_drip_emails, process_pending_emails


def _make_user(db_session: AsyncSession, email: str = "drip@example.com") -> User:
    user = User(
        email=email,
        is_provisional=False,
        email_verified=True,
    )
    db_session.add(user)
    return user


def _make_manuscript(db_session: AsyncSession, user_id, title: str = "Test MS") -> Manuscript:
    ms = Manuscript(
        user_id=user_id,
        title=title,
        status=ManuscriptStatus.bible_complete,
        payment_status=PaymentStatus.unpaid,
    )
    db_session.add(ms)
    return ms


@pytest.mark.asyncio
async def test_schedule_drip_creates_three_events(db_session: AsyncSession):
    """Scheduling drip emails creates exactly 3 EmailEvent rows."""
    user = _make_user(db_session)
    await db_session.commit()
    await db_session.refresh(user)

    ms = _make_manuscript(db_session, user.id)
    await db_session.commit()
    await db_session.refresh(ms)

    now = datetime.now(timezone.utc)
    await schedule_drip_emails(db_session, user.id, ms.id, now)

    result = await db_session.execute(
        select(EmailEvent).where(EmailEvent.manuscript_id == ms.id)
    )
    events = result.scalars().all()
    assert len(events) == 3

    event_types = {e.event_type for e in events}
    assert event_types == {"drip_1_chapter_preview", "drip_2_editor_comparison", "drip_3_beta_expiry"}


@pytest.mark.asyncio
async def test_schedule_drip_timing(db_session: AsyncSession):
    """Drip emails are scheduled at correct intervals (2h, 2d, 5d)."""
    user = _make_user(db_session, "timing@example.com")
    await db_session.commit()
    await db_session.refresh(user)

    ms = _make_manuscript(db_session, user.id)
    await db_session.commit()
    await db_session.refresh(ms)

    now = datetime.now(timezone.utc)
    await schedule_drip_emails(db_session, user.id, ms.id, now)

    result = await db_session.execute(
        select(EmailEvent).where(EmailEvent.manuscript_id == ms.id).order_by(EmailEvent.scheduled_at)
    )
    events = result.scalars().all()

    # Check scheduling offsets
    assert abs((events[0].scheduled_at - now).total_seconds() - 7200) < 2  # 2 hours
    assert abs((events[1].scheduled_at - now).total_seconds() - 172800) < 2  # 2 days
    assert abs((events[2].scheduled_at - now).total_seconds() - 432000) < 2  # 5 days


@pytest.mark.asyncio
async def test_process_skips_paid_manuscripts(db_session: AsyncSession):
    """Drip emails for paid manuscripts are marked sent without sending."""
    user = _make_user(db_session, "paid@example.com")
    await db_session.commit()
    await db_session.refresh(user)

    ms = _make_manuscript(db_session, user.id)
    ms.payment_status = PaymentStatus.paid
    await db_session.commit()
    await db_session.refresh(ms)

    # Schedule drip then immediately process
    past = datetime.now(timezone.utc) - timedelta(hours=3)
    await schedule_drip_emails(db_session, user.id, ms.id, past)

    with patch("app.email.sender.send_drip_email_1") as mock_send:
        count = await process_pending_emails(db_session)

    # Emails should be marked as processed but not actually sent
    mock_send.assert_not_called()
    assert count >= 1  # At least 1 event was due

    result = await db_session.execute(
        select(EmailEvent).where(EmailEvent.manuscript_id == ms.id)
    )
    events = result.scalars().all()
    due_events = [e for e in events if e.scheduled_at <= datetime.now(timezone.utc)]
    assert all(e.sent_at is not None for e in due_events)


@pytest.mark.asyncio
async def test_process_skips_deleted_user(db_session: AsyncSession):
    """Drip emails for deleted users are marked sent without sending."""
    user = _make_user(db_session, "deleted@example.com")
    await db_session.commit()
    await db_session.refresh(user)

    ms = _make_manuscript(db_session, user.id)
    await db_session.commit()
    await db_session.refresh(ms)

    past = datetime.now(timezone.utc) - timedelta(hours=3)
    await schedule_drip_emails(db_session, user.id, ms.id, past)

    # Soft-delete user
    user.deleted_at = datetime.now(timezone.utc)
    await db_session.commit()

    with patch("app.email.sender.send_drip_email_1") as mock_send:
        count = await process_pending_emails(db_session)

    mock_send.assert_not_called()


@pytest.mark.asyncio
async def test_process_skips_deleted_manuscript(db_session: AsyncSession):
    """Drip emails for deleted manuscripts are marked sent without sending."""
    user = _make_user(db_session, "msdel@example.com")
    await db_session.commit()
    await db_session.refresh(user)

    ms = _make_manuscript(db_session, user.id)
    await db_session.commit()
    await db_session.refresh(ms)

    past = datetime.now(timezone.utc) - timedelta(hours=3)
    await schedule_drip_emails(db_session, user.id, ms.id, past)

    # Soft-delete manuscript
    ms.deleted_at = datetime.now(timezone.utc)
    await db_session.commit()

    with patch("app.email.sender.send_drip_email_1") as mock_send:
        count = await process_pending_emails(db_session)

    mock_send.assert_not_called()


@pytest.mark.asyncio
async def test_process_dispatches_correct_email(db_session: AsyncSession):
    """Process dispatches the right sender function for each event type."""
    user = _make_user(db_session, "dispatch@example.com")
    await db_session.commit()
    await db_session.refresh(user)

    ms = _make_manuscript(db_session, user.id, "My Novel")
    await db_session.commit()
    await db_session.refresh(ms)

    # Schedule all drip emails in the past so they're all due
    past = datetime.now(timezone.utc) - timedelta(days=6)
    await schedule_drip_emails(db_session, user.id, ms.id, past)

    with patch("app.email.sender.send_drip_email_1", return_value="msg_1") as mock_1, \
         patch("app.email.sender.send_drip_email_2", return_value="msg_2") as mock_2, \
         patch("app.email.sender.send_drip_email_3", return_value="msg_3") as mock_3:
        count = await process_pending_emails(db_session)

    assert count == 3
    mock_1.assert_called_once()
    mock_2.assert_called_once()
    mock_3.assert_called_once()

    # Verify correct arguments (email and title)
    assert mock_1.call_args[0][0] == "dispatch@example.com"
    assert mock_1.call_args[0][1] == "My Novel"


@pytest.mark.asyncio
async def test_process_no_due_emails(db_session: AsyncSession):
    """Process returns 0 when no emails are due."""
    user = _make_user(db_session, "nodue@example.com")
    await db_session.commit()
    await db_session.refresh(user)

    ms = _make_manuscript(db_session, user.id)
    await db_session.commit()
    await db_session.refresh(ms)

    # Schedule in the future
    future = datetime.now(timezone.utc) + timedelta(hours=1)
    await schedule_drip_emails(db_session, user.id, ms.id, future)

    with patch("app.email.sender.send_drip_email_1") as mock_send:
        count = await process_pending_emails(db_session)

    assert count == 0
    mock_send.assert_not_called()
