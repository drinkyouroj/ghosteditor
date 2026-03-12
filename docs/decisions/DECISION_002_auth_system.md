# DECISION 002: Auth System

**Status:** DECIDED — 2026-03-11
**Scope:** Registration, login, JWT session management, email verification, password reset, provisional user flow

---

## ARCHITECT proposes:

### Overview

Four auth endpoints plus two token-based verification flows. Two user states: provisional
(email-only, limited access) and full (password set, full access). JWTs in httpOnly cookies,
not localStorage.

### Endpoints

**POST /auth/register** — Email-only registration (provisional user)
- Accepts `{email}`. Creates user with `is_provisional=true`, no password.
- Generates cryptographically random verification token (32 bytes, hex-encoded).
- Stores token hash (not plaintext) in `verification_token` with 1-hour expiry.
- Sends verification email with link containing the plaintext token.
- Returns 201 with `{message: "Verification email sent"}`. No session issued yet.
- Rate limited: 3 registrations per IP per hour.

**GET /auth/verify-email?token=...** — Email verification
- Hashes the incoming token, matches against `verification_token` in DB.
- If valid and not expired: sets `email_verified=true`, clears `verification_token`.
- Issues a provisional JWT (1-hour expiry) in httpOnly cookie.
- Provisional JWT claims: `{sub: user_id, type: "provisional", exp: +1h}`.
- Provisional tokens allow ONLY: `POST /manuscripts/upload` (Chapter 1 only),
  `GET /bible/{ms_id}`, `GET /job_status/{job_id}`.
- Returns 302 redirect to frontend dashboard.

**POST /auth/complete-registration** — Upgrade to full account
- Requires provisional JWT. Accepts `{password, tos_accepted: true}`.
- Password must be >= 8 characters. Hashed with bcrypt (cost factor 12).
- Sets `password_hash`, `is_provisional=false`, `tos_accepted_at=now()`.
- Issues full JWT pair: access token (30 min) + refresh token (7 days), both httpOnly.
- Full JWT claims: `{sub: user_id, type: "full", exp: +30m}`.
- Returns 200 with user profile.

**POST /auth/login** — Full user login
- Accepts `{email, password}`. Verifies against bcrypt hash.
- If user is provisional → 403: "Please complete registration first."
- If user is soft-deleted → 401: standard "Invalid credentials" (no info leak).
- Issues full JWT pair (access + refresh) in httpOnly cookies.
- Rate limited: 5 attempts per email per 15 minutes, 20 per IP per 15 minutes.

**POST /auth/refresh** — Token refresh
- Reads refresh token from httpOnly cookie.
- Validates, issues new access token. Refresh token is NOT rotated in MVP
  (simpler, acceptable risk at this scale).

**POST /auth/forgot-password** — Request password reset
- Accepts `{email}`. Always returns 200 (no email enumeration).
- If user exists and is not provisional: generates reset token (32 bytes, hex),
  stores hash with 1-hour expiry, sends reset email.

**POST /auth/reset-password** — Complete password reset
- Accepts `{token, new_password}`. Validates token hash, sets new password,
  clears reset token, invalidates all existing sessions by rotating a
  `token_version` counter checked on each JWT validation.

### Architecture decisions

**1. Token hashing.** Verification and reset tokens are stored as SHA-256 hashes,
not plaintext. If the DB is compromised, leaked hashes can't be used to verify
emails or reset passwords.

**2. Soft-delete aware.** Login and registration queries filter
`WHERE deleted_at IS NULL`. A deleted user cannot log in, and their email becomes
available for re-registration after hard purge.

**3. Auth middleware.** A FastAPI dependency (`get_current_user`) extracts the JWT
from the httpOnly cookie, validates it, and returns the user. A second dependency
(`require_full_user`) extends this to reject provisional tokens. Endpoints declare
which they need.

**4. No OAuth in MVP.** Adds complexity (callback URLs, provider-specific quirks,
account linking) without adding value for the target persona. Revisit in v1.

**5. CORS.** Backend sets `Access-Control-Allow-Credentials: true` with an explicit
origin whitelist. No wildcard origins when credentials are involved.

### Tradeoffs named

- **No refresh token rotation:** Simpler, but a stolen refresh token is valid for
  7 days. Acceptable at MVP scale; add rotation when user count justifies it.
- **No session table:** JWTs are stateless. `token_version` on the user row
  provides a coarse invalidation mechanism (resets ALL sessions). Per-session
  revocation requires a session table — defer to v1.
- **Bcrypt cost 12:** ~250ms per hash. Slow enough to resist brute force, fast
  enough to not annoy users. Standard choice.

---

## ADVERSARY attacks:

### Attack 1: Provisional JWT scope enforcement is discipline-based — one missed check and manuscripts leak

The provisional JWT is structurally identical to a full JWT. The ONLY difference is a
`type: "provisional"` claim. Enforcement depends on every restricted endpoint checking
this claim. When a new endpoint is added six months from now, will the developer remember
to use `require_full_user` instead of `get_current_user`?

**Failure scenario:** A new `GET /manuscripts` endpoint is added. Developer uses
`get_current_user` (which accepts provisional tokens). A provisional user — who has
only given an email, no password, no ToS acceptance — can now list all their manuscripts
and potentially access analysis results they haven't paid for.

**The fix is architectural, not procedural:** Default to rejecting provisional tokens.
Make `require_full_user` the default dependency. Create a separate, explicitly-named
`allow_provisional_user` dependency for the 2-3 endpoints that need it. New endpoints
are secure by default; you have to opt IN to provisional access, not opt OUT.

### Attack 2: No CSRF protection on cookie-based auth

httpOnly cookies are sent automatically by the browser on every request to the domain.
If a user is logged into GhostEditor and visits a malicious site, that site can submit
a form POST to `POST /auth/complete-registration` or any state-changing endpoint.
The browser attaches the cookie. The server processes it.

**Failure scenario:** Attacker creates a page with a hidden form that POSTs to
`/manuscripts/upload` with a crafted file. Victim visits the page while logged in.
The browser sends the request with the auth cookie. The file is uploaded under the
victim's account. Depending on the file, this could inject content or consume the
user's paid analysis quota.

SameSite=Lax cookies mitigate most CSRF but do NOT protect against top-level
navigations (GET requests with side effects) or same-site subdomains. If the frontend
and backend are on different subdomains (e.g., `app.ghosteditor.com` and
`api.ghosteditor.com`), SameSite=Lax is insufficient.

### Attack 3: Rate limiting by IP is trivially bypassed and punishes shared networks

Rate limiting login by IP address:
- Is bypassed by anyone with access to a residential proxy service ($5/month gets you
  thousands of IPs).
- Punishes universities, coworking spaces, and corporate networks where many users
  share an IP.
- Does not protect against credential stuffing distributed across IPs.

The per-email rate limit (5 attempts/15min) is the real defense. The IP limit is
theater that creates false positives without stopping real attackers.

### Attack 4: `token_version` for session invalidation has no column in the schema

ARCHITECT says password reset "invalidates all existing sessions by rotating a
`token_version` counter checked on each JWT validation." But `token_version` doesn't
exist in the users table from DECISION_001. This is either an oversight or it requires
a schema migration before the auth system can ship. If it's an afterthought, it wasn't
ADVERSARY-reviewed.

### Attack 5: Email enumeration via timing on /auth/register

`POST /auth/register` returns 201 for new users but must return something different for
existing emails (can't create a duplicate). If it returns 409 "Email already registered,"
an attacker can enumerate every email in the system. If it returns 201 for all cases
(to prevent enumeration), then a real user who typos their email and re-registers gets
no feedback.

The verification email path also leaks: sending an email takes ~200-500ms; not sending
(because the user exists) returns in ~5ms. Timing side-channel reveals whether an email
is registered.

---

## JUDGE decides:

**Verdict: ARCHITECT's auth design is approved with four required changes.**

The overall structure — provisional/full user states, JWT in httpOnly cookies, token
hashing, no OAuth — is correct for MVP. ADVERSARY raised valid issues.

### Required changes:

**1. Default-deny on provisional tokens (Attack 1): VALID and critical.**

Reverse the dependency defaults:
- `get_current_user` → requires full user (type="full"). This is what 95% of endpoints use.
- `get_current_user_allow_provisional` → accepts both types. Used ONLY on the 2-3
  provisional-access endpoints. Name it long and explicit so developers think before using it.

This is the correct architectural choice. Secure by default, opt-in to reduced security.

**2. CSRF mitigation (Attack 2): PARTIALLY VALID.**

For MVP with frontend and backend on the same domain (or same-site subdomains):
- Set cookies with `SameSite=Lax` (blocks cross-site POST requests).
- Set `Secure=True` in production.
- This is sufficient for MVP.

Do NOT add CSRF tokens yet — they add complexity and SameSite=Lax covers the MVP threat
model. If we move to cross-site deployment, revisit.

**3. Rate limiting (Attack 3): PARTIALLY VALID.**

Keep both IP and per-email rate limits. ADVERSARY is right that IP-only is weak, but
wrong that it's "theater" — it still blocks unsophisticated attacks and automated
scanners. The per-email limit is the primary defense; the IP limit is defense in depth.

No changes needed to the proposed limits. Document that the IP limit is not the primary
defense.

**4. Add `token_version` to users table (Attack 4): VALID.**

Add to the users table:
```sql
token_version INTEGER NOT NULL DEFAULT 1
```
Include in JWT claims. Check on every token validation: if `jwt.token_version !=
user.token_version`, reject the token. Increment on password reset and explicit
"log out everywhere" action.

This requires a schema migration (002). Write it now.

**5. Email enumeration (Attack 5): VALID but acceptable for MVP.**

On `/auth/register`:
- If email already exists AND is verified: return 200 with the same "Verification email
  sent" message. Do NOT send another email. Add a constant-time delay (~300ms) to mask
  the timing difference.
- If email exists but unverified: resend the verification email. Return 200.
- If email is new: create user, send email. Return 201... no, return 200. Same response
  code for all three cases.

Same response code + constant delay = no enumeration. Users who need to know if they're
already registered will get the verification email or can try logging in.

### Green light:

Apply the four changes above. Write migration 002 for `token_version`. Then implement.

---

## Final design amendments (post-JUDGE):

### Migration 002: Add token_version

```sql
ALTER TABLE users ADD COLUMN token_version INTEGER NOT NULL DEFAULT 1;
```

### Dependency naming (secure by default)

```python
# Default — requires full (non-provisional) user. Use on all endpoints.
async def get_current_user(...)

# Explicit opt-in — allows provisional users. Use only where needed.
async def get_current_user_allow_provisional(...)
```

### Registration response

All cases return HTTP 200 with `{"message": "If this email is valid, a verification link has been sent."}`.
Add `await asyncio.sleep(0.3)` before responding to mask timing differences.

### Cookie settings

```python
response.set_cookie(
    key="access_token",
    value=token,
    httponly=True,
    secure=True,       # False in local dev
    samesite="lax",
    max_age=...,
    path="/",
)
```
