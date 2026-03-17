"""Redis-backed rate limiting for API endpoints.

Per blueprint: "Rate limiting on upload endpoint (max 5 uploads/hour per user)"
Uses a sliding window counter stored in Redis.
"""

import logging
from datetime import timedelta

import redis.asyncio as aioredis
from fastapi import HTTPException

from app.config import settings

logger = logging.getLogger(__name__)

# Rate limit defaults
UPLOAD_RATE_LIMIT = 5  # max uploads per window
UPLOAD_RATE_WINDOW = timedelta(hours=1)


async def check_rate_limit(
    user_id: str,
    action: str = "upload",
    max_requests: int = UPLOAD_RATE_LIMIT,
    window: timedelta = UPLOAD_RATE_WINDOW,
    user_email: str | None = None,
):
    """Check and increment rate limit for a user action.

    Raises HTTP 429 if the limit is exceeded.
    Uses Redis INCR + EXPIRE for a simple fixed-window counter.
    """
    # Check if user is exempt
    if user_email and settings.rate_limit_exempt_emails:
        exempt = {e.strip().lower() for e in settings.rate_limit_exempt_emails.split(",") if e.strip()}
        if user_email.lower() in exempt:
            return
    key = f"ratelimit:{action}:{user_id}"
    window_seconds = int(window.total_seconds())

    try:
        r = aioredis.from_url(settings.redis_url)
        try:
            count = await r.incr(key)
            if count == 1:
                await r.expire(key, window_seconds)

            if count > max_requests:
                ttl = await r.ttl(key)
                minutes_remaining = max(1, ttl // 60)
                raise HTTPException(
                    status_code=429,
                    detail=f"Rate limit exceeded. Maximum {max_requests} uploads per hour. "
                    f"Try again in ~{minutes_remaining} minutes.",
                )
        finally:
            await r.aclose()
    except HTTPException:
        raise
    except Exception as e:
        # If Redis is down, allow the request (fail open) but log the issue
        logger.warning(f"Rate limit check failed (allowing request): {e}")
