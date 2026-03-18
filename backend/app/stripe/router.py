"""Stripe payment integration endpoints.

Per DECISION_006: Stripe Checkout (hosted) with webhooks for payment confirmation.
- Per-manuscript: $49 one-time (mode=payment)
- Subscription: $79/month unlimited (mode=subscription)
- Beta coupon: Stripe Promotion Code "BETA" ($20 off)
"""

from __future__ import annotations

import logging
import uuid

import stripe
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.config import settings
from app.db.models import (
    Job,
    JobType,
    Manuscript,
    ManuscriptStatus,
    PaymentStatus,
    SubscriptionStatus,
    User,
)
from app.db.session import get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/stripe", tags=["stripe"])

stripe.api_key = settings.stripe_secret_key

# Stripe Price IDs — set in environment or create via Stripe Dashboard
# These are looked up at runtime from env/config; for MVP we create them on first use
MANUSCRIPT_PRICE_AMOUNT = 4900  # $49.00 in cents
SUBSCRIPTION_PRICE_AMOUNT = 7900  # $79.00/month in cents


class CheckoutRequest(BaseModel):
    manuscript_id: str
    mode: str = "payment"  # "payment" or "subscription"


class CheckoutResponse(BaseModel):
    url: str
    session_id: str


class SubscriptionResponse(BaseModel):
    status: str
    current_period_end: str | None = None
    cancel_at_period_end: bool = False


@router.post("/create-checkout-session", response_model=CheckoutResponse)
async def create_checkout_session(
    body: CheckoutRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a Stripe Checkout Session for manuscript analysis payment.

    Per DECISION_006 Amendment 1: returns existing session URL if one is
    already pending for this manuscript (prevents double-click charges).
    """
    if not settings.stripe_secret_key:
        raise HTTPException(status_code=503, detail="Payment system not configured")

    ms_uuid = uuid.UUID(body.manuscript_id)

    # Verify manuscript ownership and status
    result = await db.execute(
        select(Manuscript).where(
            Manuscript.id == ms_uuid,
            Manuscript.user_id == user.id,
            Manuscript.deleted_at.is_(None),
        )
    )
    manuscript = result.scalar_one_or_none()
    if manuscript is None:
        raise HTTPException(status_code=404, detail="Manuscript not found")

    if manuscript.payment_status == PaymentStatus.paid:
        raise HTTPException(status_code=400, detail="Manuscript already paid for")

    # Amendment 1: Return existing session if pending
    if manuscript.stripe_session_id and manuscript.payment_status == PaymentStatus.unpaid:
        try:
            existing_session = stripe.checkout.Session.retrieve(manuscript.stripe_session_id)
            if existing_session.status == "open":
                return CheckoutResponse(url=existing_session.url, session_id=existing_session.id)
        except stripe.StripeError:
            pass  # Session expired or invalid — create new one

    # Ensure Stripe customer exists
    if not user.stripe_customer_id:
        customer = stripe.Customer.create(email=user.email)
        user.stripe_customer_id = customer.id
        await db.commit()

    # Build checkout session params
    base_url = settings.base_url
    common_params = {
        "customer": user.stripe_customer_id,
        "success_url": f"{base_url}/manuscripts/{body.manuscript_id}?payment=success",
        "cancel_url": f"{base_url}/manuscripts/{body.manuscript_id}?payment=cancelled",
        "metadata": {
            "manuscript_id": body.manuscript_id,
            "user_id": str(user.id),
        },
        "allow_promotion_codes": True,  # Enables BETA coupon entry
    }

    if body.mode == "subscription":
        session = stripe.checkout.Session.create(
            mode="subscription",
            line_items=[{
                "price_data": {
                    "currency": "usd",
                    "product_data": {"name": "GhostEditor Monthly — Unlimited Manuscripts"},
                    "unit_amount": SUBSCRIPTION_PRICE_AMOUNT,
                    "recurring": {"interval": "month"},
                },
                "quantity": 1,
            }],
            **common_params,
        )
    else:
        session = stripe.checkout.Session.create(
            mode="payment",
            line_items=[{
                "price_data": {
                    "currency": "usd",
                    "product_data": {"name": f"GhostEditor Analysis — {manuscript.title}"},
                    "unit_amount": MANUSCRIPT_PRICE_AMOUNT,
                },
                "quantity": 1,
            }],
            **common_params,
        )

    # Store session ID for idempotency (Amendment 1)
    manuscript.stripe_session_id = session.id
    await db.commit()

    return CheckoutResponse(url=session.url, session_id=session.id)


@router.post("/webhook")
async def stripe_webhook(request: Request):
    """Handle Stripe webhook events.

    Per DECISION_006 Amendment 3: webhook signature verification is mandatory.
    Per Amendment 1: idempotent — checks stripe_session_id before processing.
    """
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature")

    if not sig_header:
        logger.warning("Stripe webhook received without signature header")
        raise HTTPException(status_code=400, detail="Missing signature")

    if not settings.stripe_webhook_secret:
        logger.error("STRIPE_WEBHOOK_SECRET not configured")
        raise HTTPException(status_code=500, detail="Webhook not configured")

    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, settings.stripe_webhook_secret
        )
    except stripe.SignatureVerificationError:
        logger.warning(f"Stripe webhook signature verification failed (IP: {request.client.host})")
        raise HTTPException(status_code=400, detail="Invalid signature")
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid payload")

    # Handle events
    if event.type == "checkout.session.completed":
        await _handle_checkout_completed(event.data.object)
    elif event.type == "customer.subscription.deleted":
        await _handle_subscription_cancelled(event.data.object)
    elif event.type == "customer.subscription.updated":
        await _handle_subscription_updated(event.data.object)

    return {"status": "ok"}


async def _handle_checkout_completed(session):
    """Process successful checkout — unlock manuscript analysis."""
    from arq import create_pool
    from arq.connections import RedisSettings

    from app.analysis.chapter_analyzer import analyze_chapter
    from app.db.models import Chapter, ChapterStatus

    session_factory_mod = __import__("app.db.session", fromlist=["async_session_factory"])

    from sqlalchemy.ext.asyncio import AsyncSession as AS
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine(settings.database_url, echo=False)
    SessionFactory = async_sessionmaker(engine, class_=AS, expire_on_commit=False)

    manuscript_id = session.metadata.get("manuscript_id")
    user_id = session.metadata.get("user_id")

    if not manuscript_id or not user_id:
        logger.error(f"Checkout session {session.id} missing metadata")
        return

    ms_uuid = uuid.UUID(manuscript_id)
    user_uuid = uuid.UUID(user_id)

    async with SessionFactory() as db:
        # Get manuscript — scoped to user_id to prevent cross-user payment injection (SEC-002)
        result = await db.execute(
            select(Manuscript).where(
                Manuscript.id == ms_uuid,
                Manuscript.user_id == user_uuid,
            )
        )
        manuscript = result.scalar_one_or_none()
        if manuscript is None:
            logger.error(f"Manuscript {manuscript_id} not found for user {user_id} in checkout {session.id}")
            return

        # Amendment 1: Idempotency check
        if manuscript.payment_status == PaymentStatus.paid and manuscript.stripe_session_id == session.id:
            logger.info(f"Duplicate webhook for session {session.id}, skipping")
            return

        # Update payment status
        manuscript.payment_status = PaymentStatus.paid
        manuscript.stripe_session_id = session.id

        # Handle subscription mode
        if session.mode == "subscription":
            user_result = await db.execute(
                select(User).where(User.id == user_uuid)
            )
            user = user_result.scalar_one_or_none()
            if user:
                user.subscription_status = SubscriptionStatus.subscribed
                user.stripe_customer_id = session.customer

        await db.commit()

        # Enqueue chapter analysis jobs if manuscript is ready
        # Uses flush-enqueue-commit pattern: create job rows, flush to get IDs,
        # enqueue to Redis, then commit. If enqueue fails, rollback so manuscript
        # stays at bible_complete + paid (recoverable via /analyze endpoint).
        if manuscript.status in (ManuscriptStatus.bible_complete, ManuscriptStatus.complete):
            # Get chapters that need analysis
            chapters_result = await db.execute(
                select(Chapter)
                .where(
                    Chapter.manuscript_id == ms_uuid,
                    Chapter.status.in_([ChapterStatus.extracted, ChapterStatus.uploaded]),
                )
                .order_by(Chapter.chapter_number)
            )
            chapters = chapters_result.scalars().all()

            if chapters:
                # Create all job rows without committing
                jobs = []
                for chapter in chapters:
                    job = Job(
                        manuscript_id=ms_uuid,
                        chapter_id=chapter.id,
                        job_type=JobType.chapter_analysis,
                        current_step="Queued for chapter analysis",
                    )
                    db.add(job)
                    jobs.append(job)

                # Flush to get job IDs assigned without committing
                await db.flush()

                try:
                    redis = await create_pool(RedisSettings.from_dsn(settings.redis_url))
                    for job, chapter in zip(jobs, chapters):
                        await redis.enqueue_job(
                            "process_chapter_analysis",
                            str(job.id),
                            str(ms_uuid),
                            str(chapter.id),
                        )
                    # All enqueues succeeded — now commit jobs + status change together
                    manuscript.status = ManuscriptStatus.analyzing
                    await db.commit()
                except Exception as e:
                    logger.critical(
                        f"Failed to enqueue analysis jobs for paid manuscript "
                        f"{manuscript_id}: {e}"
                    )
                    await db.rollback()
                    # Manuscript stays at bible_complete + paid
                    # User can trigger /analyze manually to recover
                    return

            logger.info(f"Payment confirmed for manuscript {manuscript_id}, {len(chapters)} analysis jobs enqueued")

    await engine.dispose()


async def _handle_subscription_cancelled(subscription):
    """Handle subscription cancellation — update user status."""
    from sqlalchemy.ext.asyncio import AsyncSession as AS
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine(settings.database_url, echo=False)
    SessionFactory = async_sessionmaker(engine, class_=AS, expire_on_commit=False)

    async with SessionFactory() as db:
        result = await db.execute(
            select(User).where(User.stripe_customer_id == subscription.customer)
        )
        user = result.scalar_one_or_none()
        if user:
            user.subscription_status = SubscriptionStatus.free
            await db.commit()
            logger.info(f"Subscription cancelled for user {user.id}")

    await engine.dispose()


async def _handle_subscription_updated(subscription):
    """Handle subscription status changes (e.g., past_due, active)."""
    from sqlalchemy.ext.asyncio import AsyncSession as AS
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine(settings.database_url, echo=False)
    SessionFactory = async_sessionmaker(engine, class_=AS, expire_on_commit=False)

    async with SessionFactory() as db:
        result = await db.execute(
            select(User).where(User.stripe_customer_id == subscription.customer)
        )
        user = result.scalar_one_or_none()
        if user:
            if subscription.status == "active":
                user.subscription_status = SubscriptionStatus.subscribed
            elif subscription.status in ("canceled", "unpaid", "past_due"):
                user.subscription_status = SubscriptionStatus.free
            await db.commit()

    await engine.dispose()


@router.get("/subscription", response_model=SubscriptionResponse)
async def get_subscription(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get current user's subscription status."""
    if user.subscription_status != SubscriptionStatus.subscribed or not user.stripe_customer_id:
        return SubscriptionResponse(status=user.subscription_status.value)

    try:
        subscriptions = stripe.Subscription.list(
            customer=user.stripe_customer_id,
            status="active",
            limit=1,
        )
        if subscriptions.data:
            sub = subscriptions.data[0]
            return SubscriptionResponse(
                status="subscribed",
                current_period_end=str(sub.current_period_end),
                cancel_at_period_end=sub.cancel_at_period_end,
            )
    except stripe.StripeError as e:
        logger.error(f"Stripe API error fetching subscription: {e}")

    return SubscriptionResponse(status=user.subscription_status.value)


@router.post("/cancel-subscription")
async def cancel_subscription(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Cancel subscription at period end."""
    if user.subscription_status != SubscriptionStatus.subscribed or not user.stripe_customer_id:
        raise HTTPException(status_code=400, detail="No active subscription")

    try:
        subscriptions = stripe.Subscription.list(
            customer=user.stripe_customer_id,
            status="active",
            limit=1,
        )
        if subscriptions.data:
            stripe.Subscription.modify(
                subscriptions.data[0].id,
                cancel_at_period_end=True,
            )
            return {"message": "Subscription will cancel at end of current billing period"}
    except stripe.StripeError as e:
        logger.error(f"Stripe error cancelling subscription: {e}")
        raise HTTPException(status_code=502, detail="Failed to cancel subscription")

    raise HTTPException(status_code=400, detail="No active subscription found")
