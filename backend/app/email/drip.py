"""Drip email scheduling and dispatch.

Per blueprint: Store email sequences as simple scheduled tasks in PostgreSQL
(created_at + send_offset); a cron job checks every hour and dispatches.

The 3-email drip sequence fires after bible generation when payment hasn't occurred:
- Hour 2:  "Here's what GhostEditor found in your first chapter"
- Day 2:   "3 things developmental editors check"
- Day 5:   "Your beta discount expires soon"
"""

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import EmailEvent, Manuscript, PaymentStatus, User

logger = logging.getLogger(__name__)

from app.config import settings

BASE_URL = settings.base_url

# Drip schedule: (event_type, delay from bible creation)
DRIP_SCHEDULE = [
    ("drip_1_chapter_preview", timedelta(hours=2)),
    ("drip_2_editor_comparison", timedelta(days=2)),
    ("drip_3_beta_expiry", timedelta(days=5)),
]


async def schedule_drip_emails(
    db: AsyncSession,
    user_id,
    manuscript_id,
    bible_created_at: datetime,
):
    """Schedule the 3-email drip sequence for an unpaid manuscript.

    Called after story bible generation completes.
    """
    for event_type, delay in DRIP_SCHEDULE:
        event = EmailEvent(
            user_id=user_id,
            event_type=event_type,
            manuscript_id=manuscript_id,
            scheduled_at=bible_created_at + delay,
        )
        db.add(event)

    await db.commit()
    logger.info(f"Scheduled 3 drip emails for manuscript {manuscript_id}")


async def process_pending_emails(db: AsyncSession):
    """Process all due, unsent email events.

    Called periodically (every hour) by the worker or a cron task.
    Skips emails for manuscripts that have already been paid for.
    """
    from app.email.sender import (
        send_drip_email_1,
        send_drip_email_2,
        send_drip_email_3,
    )

    now = datetime.now(timezone.utc)

    # Get all due, unsent events
    result = await db.execute(
        select(EmailEvent)
        .where(
            EmailEvent.sent_at.is_(None),
            EmailEvent.scheduled_at <= now,
        )
        .order_by(EmailEvent.scheduled_at)
    )
    events = result.scalars().all()

    for event in events:
        # Get user and manuscript
        user_result = await db.execute(
            select(User).where(User.id == event.user_id, User.deleted_at.is_(None))
        )
        user = user_result.scalar_one_or_none()
        if not user:
            event.sent_at = now  # Mark as processed (user deleted)
            continue

        ms_result = await db.execute(
            select(Manuscript).where(Manuscript.id == event.manuscript_id)
        )
        manuscript = ms_result.scalar_one_or_none()
        if not manuscript or manuscript.deleted_at is not None:
            event.sent_at = now  # Mark as processed (manuscript deleted)
            continue

        # Skip if already paid — no need for drip emails
        if manuscript.payment_status == PaymentStatus.paid:
            event.sent_at = now
            logger.info(f"Skipping drip email {event.event_type} — manuscript already paid")
            continue

        bible_url = f"{BASE_URL}/manuscripts/{manuscript.id}/bible"
        pricing_url = f"{BASE_URL}/manuscripts/{manuscript.id}/pricing"

        # Dispatch based on event type
        sent = False
        if event.event_type == "drip_1_chapter_preview":
            sent = send_drip_email_1(user.email, manuscript.title, bible_url) is not None
        elif event.event_type == "drip_2_editor_comparison":
            sent = send_drip_email_2(user.email, manuscript.title, pricing_url) is not None
        elif event.event_type == "drip_3_beta_expiry":
            sent = send_drip_email_3(user.email, manuscript.title, pricing_url) is not None
        elif event.event_type == "bible_ready":
            from app.email.sender import send_bible_ready_email
            sent = send_bible_ready_email(user.email, manuscript.title, bible_url) is not None

        if sent:
            event.sent_at = now
            logger.info(f"Sent {event.event_type} email to {user.email}")
        else:
            # Mark as sent anyway to prevent retry loops (email service may be down)
            event.sent_at = now
            logger.warning(f"Failed to send {event.event_type} to {user.email}, marking as processed")

    await db.commit()
    return len(events)
