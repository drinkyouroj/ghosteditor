# GhostEditor Codebase Audit Report

**Date:** 2026-03-18
**Auditor:** Orchestrator (Phase 0)
**Tag:** v0.5.0-pre-review
**Scope:** Full codebase — backend/app/, frontend/src/, tests/, infra/, docs/

---

## Section 1 — Security Findings

### SEC-001: Default JWT secret in production if env var missing
- **File:** `backend/app/config.py:12`
- **Severity:** CRITICAL
- **Description:** `jwt_secret_key` defaults to `"change-me-in-production"`. If the env var is not set, all JWTs are signed with this known value. The `COOKIE_SECURE` flag in `auth/router.py:34` does check this value to toggle Secure cookies, but the actual secret remains exploitable.
- **Fix:** Add a startup check in `main.py` that raises an error if `jwt_secret_key` equals the default value when `COOKIE_SECURE` would be True (i.e., in production). Alternatively, generate a random secret at startup and warn.

### SEC-002: Stripe webhook lacks user_id scoping on manuscript lookup
- **File:** `backend/app/stripe/router.py:222-225`
- **Severity:** HIGH
- **Description:** `_handle_checkout_completed` queries `Manuscript.id == ms_uuid` without filtering by `user_id`. The `user_id` IS available in `session.metadata` but is only used for the User lookup, not as a WHERE clause on the manuscript query. A malicious actor who can control Stripe session metadata could mark any manuscript as paid.
- **Fix:** Add `Manuscript.user_id == user_uuid` to the WHERE clause on line 223.

### SEC-003: Verification token not invalidated after use (reusable)
- **File:** `backend/app/auth/router.py:123-126`
- **Severity:** HIGH
- **Description:** `verify_email` clears the token after use (line 124), but does not check `email_verified` first. If a race condition occurs (two requests with the same token), both could succeed. More importantly, the verify-email endpoint sets cookies and redirects — if the token hash is intercepted from the database, it could be replayed.
- **Fix:** Add `User.email_verified.is_(False)` to the query filter. Add a transaction lock or `SELECT ... FOR UPDATE` to prevent race conditions.

### SEC-004: Password reset token not single-use
- **File:** `backend/app/auth/router.py:226-247`
- **Severity:** MEDIUM
- **Description:** The password reset flow correctly clears the token after use (line 243-244) and increments `token_version`, but there's a TOCTOU window between the SELECT and the UPDATE. Two concurrent reset requests with the same token could both succeed. Same pattern as SEC-003.
- **Fix:** Use `SELECT ... FOR UPDATE` or add an atomic update with a WHERE clause that includes the current token value.

### SEC-005: No CSRF protection on state-changing POST endpoints
- **File:** `backend/app/main.py` (CORS config), all POST endpoints
- **Severity:** MEDIUM
- **Description:** The app uses cookie-based auth but has no CSRF token validation. CORS is configured with `allow_credentials=True` and only allows `localhost:5173` + `base_url`, which provides some protection, but CSRF is still possible if an attacker can run JavaScript on a same-site subdomain. The SameSite=Lax on cookies helps for cross-site POST but doesn't protect against same-site attacks.
- **Fix:** Add a CSRF token (double-submit cookie pattern or custom header check) for all state-changing endpoints. Alternatively, verify the `Origin` or `Referer` header matches `base_url` on all POST/PUT/DELETE requests.

### SEC-006: Webhook IP not verified (Stripe)
- **File:** `backend/app/stripe/router.py:155-191`
- **Severity:** LOW
- **Description:** While webhook signature verification is correctly implemented (DECISION_006 Amendment 3), there's no IP allowlist check. Stripe publishes webhook IPs but they're not validated. The signature check is sufficient for security, but IP validation adds defense in depth.
- **Fix:** Optional — add Stripe webhook IP validation as a pre-check, or document as accepted risk.

### SEC-007: S3 files not encrypted at rest (default)
- **File:** `backend/app/manuscripts/s3.py:27-34`
- **Severity:** LOW
- **Description:** `upload_to_s3` does not specify `ServerSideEncryption`. The blueprint's trust section says "Files are encrypted at rest" — this depends entirely on the S3/MinIO bucket configuration, not application code.
- **Fix:** Add `ServerSideEncryption='AES256'` to the `put_object` call to enforce encryption regardless of bucket policy.

### SEC-008: Account deletion is soft-delete only — no hard purge within 30 days
- **File:** `backend/app/auth/router.py:265-319`
- **Severity:** MEDIUM
- **Description:** Account deletion sets `deleted_at` on users and manuscripts, and does best-effort S3 cleanup. But there's no scheduled job to hard-delete database rows after 30 days as promised in the Privacy Policy ("Files are deleted from S3 within 30 days of account deletion request"). Soft-deleted data persists indefinitely.
- **Fix:** Add a worker cron job that hard-deletes users and all associated rows where `deleted_at` is older than 30 days.

### SEC-009: Email HTML templates vulnerable to injection
- **File:** `backend/app/email/sender.py:44-204`
- **Severity:** MEDIUM
- **Description:** Email templates use f-strings with `verification_url`, `reset_url`, `manuscript_title`, etc. The `manuscript_title` is user-supplied and not HTML-escaped. A title like `<script>alert('xss')</script>` would be injected into the email HTML. Most email clients sanitize JavaScript, but HTML injection (link spoofing, phishing) remains possible.
- **Fix:** HTML-escape all user-supplied values in email templates using `html.escape()`.

### SEC-010: No rate limiting on login endpoint
- **File:** `backend/app/auth/router.py:160-179`
- **Severity:** MEDIUM
- **Description:** Only the upload endpoint has rate limiting. The login endpoint has no rate limiting, making it vulnerable to credential stuffing attacks. The constant-time delay on register/forgot-password is good for anti-enumeration but doesn't prevent brute force on login.
- **Fix:** Add rate limiting to the login endpoint (e.g., 10 attempts per 15 minutes per IP or email).

### SEC-011: No rate limiting on password reset endpoint
- **File:** `backend/app/auth/router.py:201-220`
- **Severity:** LOW
- **Description:** The forgot-password endpoint has anti-enumeration delay but no rate limit. An attacker could trigger thousands of reset emails to a victim's address.
- **Fix:** Add rate limiting per email address (e.g., 3 reset requests per hour).

---

## Section 2 — Code Quality Findings

### CQ-001: Worker creates new SQLAlchemy engine per function call
- **File:** `backend/app/jobs/worker.py:64-66`, `backend/app/stripe/router.py:207-208`, repeated in every webhook handler
- **Severity:** HIGH
- **Description:** `_get_session_factory()` in worker.py creates a new `create_async_engine()` every call. Each engine has its own connection pool. This means the worker may open many connection pools over its lifetime. The Stripe webhook handlers also create fresh engines per invocation (lines 207, 315, 336).
- **Fix:** Create the engine once at module level or in the worker startup hook, and reuse it. For Stripe webhooks, use the app's existing `get_db()` dependency or a shared engine.

### CQ-002: Fiction/nonfiction pipeline inconsistency — `nonfiction_synthesis.py` `total_word_count` is dead parameter
- **File:** `backend/app/analysis/nonfiction_synthesis.py:50`
- **Severity:** MEDIUM
- **Description:** `generate_document_synthesis()` accepts `total_word_count` but never uses it in the prompt template. No caller passes it either. This was flagged in QA but not yet fixed.
- **Fix:** Remove the parameter, or add it to the prompt if word count should influence synthesis.

### CQ-003: Unused import `json` in `argument_map.py`
- **File:** `backend/app/analysis/argument_map.py`
- **Severity:** LOW
- **Description:** After the QA fix that removed `json` usage, the import was already cleaned up. No actual issue remains.

### CQ-004: Duplicate `_sanitize_manuscript_text` function in 3 files
- **File:** `backend/app/analysis/story_bible.py:39-43`, `backend/app/analysis/chapter_analyzer.py:34-36`, `backend/app/analysis/argument_map.py:29-31`
- **Severity:** LOW
- **Description:** The same function is copy-pasted in three modules. This is a minor DRY violation that could lead to divergent behavior if one is updated but not the others.
- **Fix:** Extract to a shared utility (e.g., `analysis/utils.py`). Low priority — not blocking.

### CQ-005: Frontend `getArgumentMap` uses wrong API path
- **File:** `frontend/src/api/client.ts:362`
- **Severity:** HIGH
- **Description:** `getArgumentMap` calls `/bible/${manuscriptId}/argument-map` but the backend router is mounted at `/argument-map/${manuscriptId}` (see `nonfiction_router.py:21`). The frontend path does not exist and will 404.
- **Fix:** Change to `request<ArgumentMap>(\`/argument-map/${manuscriptId}\`)`.

### CQ-006: Frontend `getManuscriptFeedback` path doesn't distinguish fiction/nonfiction
- **File:** `frontend/src/api/client.ts:289`
- **Severity:** MEDIUM
- **Description:** `getManuscriptFeedback` always calls `/bible/${manuscriptId}/feedback` which is the fiction feedback endpoint. For nonfiction manuscripts, it should call `/argument-map/${manuscriptId}/feedback`. The FeedbackPage.tsx uses this same function for both modes, which means nonfiction feedback will either 404 or return fiction-formatted data.
- **Fix:** Add a `getNonfictionFeedback` function that calls `/argument-map/${manuscriptId}/feedback`, and switch based on document type in the FeedbackPage component.

### CQ-007: Build log TODO items in code-like format (Section in docs/build_log.md)
- **File:** `docs/build_log.md:303-311`
- **Severity:** LOW
- **Description:** Four P1 TODO items remain as open checkboxes in the build log. These are documented but not resolved.
- **Fix:** Agent 2 should resolve or document each as a known limitation.

### CQ-008: `Manuscript.document_type` CHECK constraint incomplete
- **File:** `backend/app/db/models.py:141-145`
- **Severity:** LOW
- **Description:** The CHECK constraint allows `document_type = 'nonfiction'` with any `nonfiction_format` value including NULL. A nonfiction manuscript with no format is valid at the DB level, which is fine for MVP but means the code must handle this case everywhere.
- **Fix:** Document as accepted behavior. The code already handles `nonfiction_format = None` throughout.

---

## Section 3 — Test Coverage Gaps

### TC-001: No auth-scoping tests for most endpoints
- **Area:** API endpoints
- **What's missing:** Tests verifying that user A cannot access user B's manuscripts, bibles, feedback, or argument maps. The only auth test is `test_analyze_endpoint.py` which tests 404 on wrong-user access for `/analyze`. Missing for: `GET /manuscripts/{id}`, `DELETE /manuscripts/{id}`, `GET /bible/{id}`, `GET /bible/{id}/feedback`, `GET /argument-map/{id}`, `GET /argument-map/{id}/feedback`, `GET /manuscripts/jobs/{id}`.
- **Priority:** HIGH
- **Suggested tests:** For each endpoint, create two users, have user B try to access user A's data, verify 404.

### TC-002: No nonfiction pipeline integration test
- **Area:** Nonfiction pipeline (argument map generation -> section analysis -> synthesis)
- **What's missing:** The fiction pipeline has `test_e2e_flow.py` but there's no equivalent for nonfiction. The nonfiction eval harness only tests argument map generation (not section analysis or synthesis).
- **Priority:** HIGH
- **Suggested test:** Create an E2E test that mocks LLM calls and exercises: upload nonfiction -> extract -> generate argument map -> analyze sections -> generate synthesis -> verify feedback endpoint returns data.

### TC-003: No unit tests for nonfiction worker functions
- **Area:** `backend/app/jobs/worker.py` — nonfiction worker functions
- **What's missing:** `process_argument_map_generation`, `process_nonfiction_section_analysis`, `process_nonfiction_synthesis` have no unit tests.
- **Priority:** MEDIUM
- **Suggested tests:** Mock LLM calls and DB session, test that each function updates job/manuscript/chapter status correctly, handles errors, and chains to the next function.

### TC-004: No tests for Stripe webhook handlers
- **Area:** `backend/app/stripe/router.py` — webhook event handlers
- **What's missing:** `test_stripe_webhook.py` exists but may not cover the webhook signature verification, subscription cancellation, or subscription update flows comprehensively.
- **Priority:** MEDIUM
- **Suggested tests:** Test webhook with valid/invalid signatures, test subscription lifecycle events.

### TC-005: No nonfiction eval ground truth
- **Area:** `backend/tests/eval/`
- **What's missing:** Nonfiction eval harness has 5 samples but no ground truth JSON. Tests validate schema structure only, not content quality.
- **Priority:** MEDIUM
- **Suggested test:** Create ground truth argument maps for at least 2 nonfiction samples (academic + journalism) with expected thesis, thread counts, and evidence types.

### TC-006: No frontend tests
- **Area:** `frontend/src/`
- **What's missing:** Zero test files in the frontend directory. No unit tests, no integration tests, no E2E tests.
- **Priority:** LOW (for this sprint — frontend testing is a larger initiative)
- **Suggested test:** At minimum, add a smoke test that the App component renders without crashing.

### TC-007: Missing tests for email drip scheduling and dispatch
- **Area:** `backend/app/email/drip.py`
- **What's missing:** `test_drip_emails.py` exists but coverage of edge cases (user deleted mid-drip, manuscript paid between scheduling and dispatch) may be incomplete.
- **Priority:** LOW
- **Suggested test:** Test that drip emails are skipped when manuscript is paid before send time.

---

## Section 4 — Blueprint Gap Analysis

| # | Feature/Requirement | Status | Gap | Complexity |
|---|---|---|---|---|
| 1 | Email + password registration with email verification | BUILT | — | — |
| 2 | Password reset flow | BUILT | — | — |
| 3 | JWT sessions (httpOnly cookie) | BUILT | — | — |
| 4 | Per-user manuscript isolation (user_id scoping) | BUILT | All queries scoped. Missing test coverage (TC-001). | — |
| 5 | Account deletion with data purge | PARTIAL | Soft-delete only. No 30-day hard purge cron (SEC-008). | SMALL |
| 6 | File upload pipeline (DOCX/TXT/PDF) | BUILT | — | — |
| 7 | File validation (magic bytes, size, MIME) | BUILT | — | — |
| 8 | Chapter detection (auto + manual) | BUILT | Manual chapter splitting UI not built (spec: "fallback to manual chapter splitting UI if auto-detect fails"). | LARGE |
| 9 | Story bible generation (Ch1 + incremental) | BUILT | — | — |
| 10 | Async job queue with frontend polling | BUILT | — | — |
| 11 | Chapter analysis engine | BUILT | — | — |
| 12 | Cross-chapter consistency checking | BUILT | Via bible cross-reference in prompt | — |
| 13 | Pacing analysis | BUILT | — | — |
| 14 | Genre convention comparison (5 genres) | BUILT | 8 genres implemented (exceeds spec) | — |
| 15 | Eval harness (5 genre samples + ground truth) | BUILT | Fiction: 5 genres, 5 ground truth. Nonfiction: 5 formats, 0 ground truth. | SMALL |
| 16 | Feedback dashboard (per-chapter tabs, severity sort) | BUILT | — | — |
| 17 | Story bible viewer | BUILT | — | — |
| 18 | Progress indicators | BUILT | — | — |
| 19 | Error handling for malformed files | BUILT | — | — |
| 20 | Non-English detection | BUILT | Via langdetect before Claude call | — |
| 21 | Manuscript deletion + GDPR | PARTIAL | Soft-delete works. S3 cleanup is best-effort. No hard purge cron. | SMALL |
| 22 | Legal pages (ToS, Privacy) | BUILT | — | — |
| 23 | ToS acceptance checkbox at registration | BUILT | `tos_accepted_at` timestamped in complete-registration | — |
| 24 | Stripe per-manuscript + subscription | BUILT | — | — |
| 25 | Beta coupon (BETA code) | BUILT | Via Stripe Promotion Codes | — |
| 26 | Free-tier upload limit (3 manuscripts) | BUILT | DECISION_006 Amendment 4 | — |
| 27 | Email capture before paywall | BUILT | Provisional user flow (email-only registration) | — |
| 28 | 3-email drip sequence (Resend) | BUILT | — | — |
| 29 | Landing page | BUILT | — | — |
| 30 | Rate limiting on upload endpoint | BUILT | 5/hour per user | — |
| 31 | Rate limiting on login endpoint | MISSING | No rate limiting on login | SMALL |
| 32 | Rate limiting on password reset | MISSING | No rate limiting on forgot-password | SMALL |
| 33 | Nonfiction section detection (header-based + chunked) | MISSING | `detect_chapters` with `document_type=nonfiction` param not implemented. Current extraction uses fiction pipeline for all document types. | MEDIUM |
| 34 | Nonfiction argument map generation | BUILT | — | — |
| 35 | Nonfiction section analysis | BUILT | — | — |
| 36 | Nonfiction document synthesis | BUILT | — | — |
| 37 | 30-day hard delete cron for GDPR | MISSING | No cron job to purge soft-deleted data | SMALL |
| 38 | Bible drift warnings surfaced to users | MISSING | Logged but not visible in UI (build_log P1 TODO) | SMALL |
| 39 | Issue cap indicator in UI | MISSING | Silent truncation at 15 issues (build_log P1 TODO) | SMALL |
| 40 | Manual chapter splitting fallback UI | MISSING | Spec says to build this; not implemented | LARGE |
| 41 | Max word count (120K) warning with "process in halves" offer | PARTIAL | Word count check exists but just rejects. No "process in halves" UX. | MEDIUM |

---

## Section 5 — Performance & Scalability Findings

### PERF-001: N+1 query in drip email dispatch
- **File:** `backend/app/email/drip.py:72-98`
- **Severity:** MEDIUM
- **Description:** `process_pending_emails` queries for all due events, then for each event individually queries the User and Manuscript tables. With 100 pending emails, this is 200+ queries.
- **Fix:** Use a single query with JOIN to load events + users + manuscripts in one go.

### PERF-002: S3 client created per operation
- **File:** `backend/app/manuscripts/s3.py:7-15`
- **Severity:** MEDIUM
- **Description:** `get_s3_client()` creates a new `boto3.client` on every call. boto3 clients are thread-safe and should be reused.
- **Fix:** Create the client once at module level (or use a cached property).

### PERF-003: Redis connection pool created per rate limit check
- **File:** `backend/app/rate_limit.py:43`
- **Severity:** MEDIUM
- **Description:** `aioredis.from_url()` creates a new connection each time `check_rate_limit` is called. This is overhead on every upload request.
- **Fix:** Create a module-level Redis connection pool and reuse it.

### PERF-004: Redis connection pool created per job enqueue
- **File:** `backend/app/manuscripts/router.py:139`, `backend/app/manuscripts/router.py:313`
- **Severity:** MEDIUM
- **Description:** `create_pool(RedisSettings.from_dsn(...))` is called every time a job is enqueued. Each call creates a new connection pool.
- **Fix:** Create and cache the Redis pool at app startup.

### PERF-005: Full manuscript text loaded into memory for extraction
- **File:** `backend/app/manuscripts/extraction.py:217-252`
- **Severity:** LOW
- **Description:** `extract_text()` loads the entire file content into memory as bytes and then the extracted text as a string. For the 10MB file limit, this means up to ~20MB per concurrent extraction. Acceptable for MVP but won't scale with many concurrent users.
- **Fix:** Document as acceptable for MVP. Stream processing would be a v2 optimization.

### PERF-006: Nonfiction synthesis timing — no guard against premature fire
- **File:** `backend/app/jobs/worker.py` (nonfiction synthesis enqueue)
- **Severity:** LOW
- **Description:** The nonfiction section analysis worker chains to synthesis after the last section completes. This relies on correct section counting. If a section analysis fails and is retried, the synthesis could fire before all sections are done, or not fire at all.
- **Fix:** Add a pre-synthesis check that verifies ALL sections have status=analyzed before running synthesis. The worker already does something similar for fiction — verify the nonfiction path has the same guard.

---

## Summary

### Finding Counts by Severity

| Category | CRITICAL | HIGH | MEDIUM | LOW |
|---|---|---|---|---|
| Security | 1 | 2 | 4 | 3 |
| Code Quality | 0 | 2 | 2 | 3 |
| Test Coverage | 0 | 2 | 3 | 2 |
| Blueprint Gaps | — | — | — | — |
| Performance | 0 | 0 | 4 | 2 |
| **Total** | **1** | **4** | **13** | **10** |

### Top 5 Most Critical Items

1. **SEC-001 (CRITICAL):** Default JWT secret exploitable in production — startup check needed
2. **SEC-002 (HIGH):** Stripe webhook lacks user_id scoping on manuscript payment
3. **SEC-003 (HIGH):** Verification token reusable via race condition
4. **CQ-005 (HIGH):** Frontend `getArgumentMap` uses wrong API path — nonfiction argument map page broken
5. **TC-001 (HIGH):** No auth-scoping tests for most API endpoints

### Recommended Agent Assignments

**Agent 1 — Security Fixer:**
- SEC-001 (CRITICAL): JWT secret startup check
- SEC-002 (HIGH): Stripe webhook user_id scoping
- SEC-003 (HIGH): Verification token single-use fix
- SEC-004 (MEDIUM): Password reset race condition
- SEC-005 (MEDIUM): CSRF protection
- SEC-008 (MEDIUM): 30-day hard purge cron
- SEC-009 (MEDIUM): Email HTML injection
- SEC-010 (MEDIUM): Login rate limiting
- SEC-011 (LOW): Password reset rate limiting

**Agent 2 — Code Quality + Tech Debt:**
- CQ-001 (HIGH): Worker engine reuse
- CQ-005 (HIGH): Frontend API path fix for argument map
- CQ-006 (MEDIUM): Frontend nonfiction feedback path
- CQ-002 (MEDIUM): Dead `total_word_count` parameter
- CQ-004 (LOW): Duplicate sanitize function
- CQ-007 (LOW): Build log TODO resolution
- CQ-008 (LOW): Document CHECK constraint behavior
- PERF-001 through PERF-004: Connection pool reuse fixes

**Agent 3 — Test Coverage:**
- TC-001 (HIGH): Auth-scoping tests for all endpoints
- TC-002 (HIGH): Nonfiction pipeline E2E integration test
- TC-003 (MEDIUM): Nonfiction worker unit tests
- TC-004 (MEDIUM): Stripe webhook handler tests
- TC-005 (MEDIUM): Nonfiction eval ground truth

**Agent 4 — Blueprint Gap Closer:**
- #31 (SMALL): Login rate limiting (coordinate with Agent 1 — SEC-010)
- #32 (SMALL): Password reset rate limiting (coordinate with Agent 1 — SEC-011)
- #37 (SMALL): 30-day hard delete cron (coordinate with Agent 1 — SEC-008)
- #38 (SMALL): Surface bible drift warnings in UI
- #39 (SMALL): Issue cap indicator in UI
- #33 (MEDIUM): Nonfiction section detection via `document_type` param
- Document as next-sprint: #8 manual chapter splitting UI (LARGE), #40 manual splitting (LARGE), #41 "process in halves" (MEDIUM)
