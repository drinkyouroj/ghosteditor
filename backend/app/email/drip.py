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

    # Get all due, unsent events with user and manuscript in a single query
    result = await db.execute(
        select(EmailEvent, User, Manuscript)
        .join(User, EmailEvent.user_id == User.id)
        .join(Manuscript, EmailEvent.manuscript_id == Manuscript.id)
        .where(
            EmailEvent.sent_at.is_(None),
            EmailEvent.scheduled_at <= now,
        )
        .order_by(EmailEvent.scheduled_at)
    )
    rows = result.all()

    # Also mark orphaned events (deleted user/manuscript) — separate query
    orphaned_result = await db.execute(
        select(EmailEvent)
        .outerjoin(User, EmailEvent.user_id == User.id)
        .outerjoin(Manuscript, EmailEvent.manuscript_id == Manuscript.id)
        .where(
            EmailEvent.sent_at.is_(None),
            EmailEvent.scheduled_at <= now,
            (User.id.is_(None)) | (User.deleted_at.is_not(None))
            | (Manuscript.id.is_(None)) | (Manuscript.deleted_at.is_not(None)),
        )
    )
    for orphaned_event in orphaned_result.scalars().all():
        orphaned_event.sent_at = now

    events = []
    for event, user, manuscript in rows:
        # Skip deleted users/manuscripts
        if user.deleted_at is not None or manuscript.deleted_at is not None:
            event.sent_at = now
            continue

        # Skip if already paid — no need for drip emails
        if manuscript.payment_status == PaymentStatus.paid:
            event.sent_at = now
            logger.info(f"Skipping drip email {event.event_type} — manuscript already paid")
            continue

        events.append((event, user, manuscript))

    for event, user, manuscript in events:
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
    return len(rows)
