# DECISION 006: Stripe Payment Integration

## Context

GhostEditor needs monetization before soft launch. The blueprint specifies:
- $49 per manuscript (one-time)
- $79/month subscription (unlimited manuscripts)
- Beta coupon: BETA → $29 first manuscript
- Free tier: Chapter 1 story bible generation (no payment required)
- Payment gate: full chapter analysis requires payment

## ARCHITECT proposes:

**Use Stripe Checkout (hosted) with webhooks for payment confirmation.**

### Payment Flow

1. User uploads manuscript → text extraction + story bible generation runs free
2. After bible_complete, frontend shows story bible + paywall prompt
3. User clicks "Unlock Full Analysis" → frontend calls `POST /stripe/create-checkout-session`
4. Backend creates Stripe Checkout Session with:
   - For per-manuscript: `mode=payment`, price=$49, metadata={manuscript_id}
   - For subscription: `mode=subscription`, price=$79/month
5. Frontend redirects to Stripe-hosted checkout page
6. On success: Stripe fires `checkout.session.completed` webhook
7. Webhook handler:
   - For one-time: sets `manuscript.payment_status = paid`, enqueues chapter analysis jobs
   - For subscription: sets `user.subscription_status = subscribed`, `user.stripe_customer_id`, enqueues analysis for current manuscript
8. Frontend polls manuscript status → sees `analyzing` → shows progress

### Why Stripe Checkout (hosted) over Payment Element:

- No PCI DSS burden — card data never touches our server
- Simpler implementation (redirect flow vs embedded form)
- Built-in support for promotion codes (beta coupons)
- Automatic receipt emails from Stripe
- Mobile-optimized payment UI for free

### Endpoints

- `POST /stripe/create-checkout-session` — creates session, returns {url}
- `POST /stripe/webhook` — receives Stripe events (no auth — uses webhook signature)
- `GET /stripe/subscription` — returns current subscription status
- `POST /stripe/cancel-subscription` — cancels at period end

### Subscription Logic

- Subscribed users: all manuscripts auto-marked paid on upload
- Per-use users: each manuscript requires individual payment
- Free users: story bible only, no chapter analysis

### Beta Coupon

- Create a Stripe Promotion Code "BETA" attached to a coupon ($20 off = $29 net)
- Stripe Checkout natively supports promotion code entry field
- No custom coupon logic needed in our backend

### Database Changes

None — existing schema already has:
- `User.stripe_customer_id`
- `User.subscription_status` (free/per_use/subscribed)
- `Manuscript.payment_status` (unpaid/paid/refunded)

Only change: new Alembic migration to add `stripe_session_id` to Manuscript for idempotent webhook handling.

### Tradeoffs

- Stripe Checkout redirect leaves our domain briefly (acceptable for MVP)
- Webhook delivery can be delayed (up to ~60s in rare cases) — frontend polls manuscript status
- No invoice/billing history page for MVP (Stripe sends receipts directly)

## ADVERSARY attacks:

### 1. Webhook replay and race conditions

If the webhook fires twice (Stripe retries on timeout), analysis jobs get enqueued twice. The `checkout.session.completed` handler must be idempotent. Also: what if the user hits the "Unlock" button twice before the first checkout completes? They could create two Stripe sessions for the same manuscript.

**Failure scenario:** Double-charging the user, or double-enqueuing analysis jobs that waste Claude API credits.

### 2. Subscription cancellation leaves manuscripts in limbo

When a subscriber cancels, their existing manuscripts should keep their analysis (they paid for the period). But what about manuscripts uploaded between cancel request and period end? The blueprint says $79/month = "unlimited manuscripts" — does that mean manuscripts uploaded during the paid period keep their analysis forever? If so, a user could batch-upload 50 manuscripts on day 29, cancel, and walk away with $2,450 worth of analysis for $79.

**Failure scenario:** Business model collapse from subscription abuse.

### 3. Webhook endpoint is unauthenticated — abuse vector

The `/stripe/webhook` endpoint has no JWT auth (by design — Stripe calls it). An attacker who discovers the endpoint could forge webhook payloads to unlock manuscripts without paying. The webhook signature verification must be bulletproof and the webhook secret must never be exposed.

**Failure scenario:** Free manuscript analysis for anyone who can craft a valid-looking POST request.

### 4. Free-tier abuse through repeated uploads

Nothing stops a free user from uploading the same manuscript repeatedly to get Chapter 1 story bibles for different manuscripts. They could upload 100 manuscripts and get 100 free story bibles at our Claude API cost ($1-3 each = $100-300).

**Failure scenario:** API cost blowout from free-tier abuse.

### 5. Payment status desync between Stripe and our database

If the webhook handler fails after Stripe charges the user (our DB write fails), the user is charged but their manuscript isn't unlocked. Stripe will retry the webhook, but if our handler keeps failing (bad migration, bug), the user is stuck in a charged-but-locked state.

**Failure scenario:** User pays $49, manuscript stays locked, support nightmare.

## JUDGE decides:

**Approved with 4 amendments.**

The Stripe Checkout approach is correct for MVP — minimal PCI surface, proven flow, and the existing schema is well-prepared. ADVERSARY's attacks are valid and require mitigations:

### Amendment 1: Idempotent webhook handler (addresses Attack #1)

Store `stripe_session_id` on the Manuscript. Webhook handler checks: if `manuscript.stripe_session_id == session.id` AND `payment_status == paid`, skip (already processed). For the double-click problem: `create-checkout-session` returns the existing session URL if one is already pending for that manuscript (check `stripe_session_id` is set and `payment_status` is still `unpaid`).

### Amendment 2: Subscription analysis cap (addresses Attack #2)

Subscribers get unlimited manuscripts **during their active billing period**. Manuscripts uploaded during an active subscription are marked `paid` at upload time. After cancellation takes effect (period end), new manuscripts default to `unpaid`. Already-analyzed manuscripts keep their results forever — the analysis is the product, not ongoing access.

No per-period cap for MVP. Monitor for abuse; add a reasonable cap (e.g., 10/month) in v2 if needed.

### Amendment 3: Webhook signature verification is mandatory (addresses Attack #3)

The webhook handler MUST verify `stripe.Webhook.construct_event()` with the webhook signing secret on every request. If verification fails, return 400 immediately. Never process unverified payloads. Log all verification failures with IP for monitoring.

### Amendment 4: Free-tier rate limit (addresses Attack #4)

Enforce a maximum of 3 manuscripts per free-tier user. This is a database check in the upload endpoint: count non-deleted manuscripts where `user.subscription_status == free`. Return 402 with a message directing them to payment. This addresses both cost and abuse concerns while being generous enough for genuine trial users.

**Attack #5 (desync):** Accepted risk for MVP. Stripe retries webhooks for up to 3 days. If handler consistently fails, it's a deploy bug we'd catch in monitoring. Add a manual "re-check payment" button in v2.

**Green light. Implement with all 4 amendments.**
