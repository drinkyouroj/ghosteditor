# Build Log

## 2026-03-11 — Week 1 Foundation

### Infrastructure (Step 1)
- Created `infra/docker-compose.yml` — PostgreSQL 16 + Redis 7 with healthchecks
- Created `.env.example` with all required environment variables
- Set up FastAPI project skeleton: `backend/app/main.py`, config, DB session

### Database Schema (Step 2 — DECISION_001)
- DECISION_001 written, ADVERSARY attacked, JUDGE approved with 5 amendments
- SQLAlchemy models in `backend/app/db/models.py` (8 tables)
- Alembic migration 001: full schema with ENUMs, indexes, triggers
- Migration 002: `token_version` column per DECISION_002 JUDGE amendment
- JUDGE amendments implemented: unique constraint on `chapter_analyses`, provisional stale index, bible version cap (app-level)

### Auth System (Step 3 — DECISION_002)
- DECISION_002 written, ADVERSARY attacked, JUDGE approved with 4 amendments
- Endpoints: register, verify-email, complete-registration, login, refresh, forgot-password, reset-password, logout, me
- JUDGE amendments implemented:
  - Default-deny on provisional tokens (`get_current_user` requires full; `get_current_user_allow_provisional` is explicit opt-in)
  - SameSite=Lax cookies, Secure=True in production
  - Constant-time delay on register/forgot-password to prevent enumeration
  - `token_version` checked on every JWT validation
- Token hashing (SHA-256) for verification and reset tokens
- Unit tests: 10 test cases covering registration, verification, login, auth failures

### File Upload Pipeline (Step 4 — DECISION_003)
- DECISION_003 written, ADVERSARY attacked, JUDGE approved with 5 amendments
- Endpoints: POST /manuscripts/upload, GET /manuscripts, GET /manuscripts/{id}, DELETE /manuscripts/{id}, GET /manuscripts/jobs/{id}
- JUDGE amendments implemented:
  - Dropped python-magic; targeted validation per format (ZIP check for DOCX, %PDF- header, UTF-8 for TXT)
  - ContentSizeLimitMiddleware rejects > 10MB before body is read
  - S3 key uses `original.{ext}`, no user-supplied filename
  - UTF-8 only for .txt, no chardet fallback
  - Chapter detection: no bare-number regex, min 200 words/chapter merge, max 100 chapter cap
- Text extraction: DOCX (python-docx), PDF (PyPDF2, scanned detection), TXT (UTF-8)
- Chapter detection with merge and cap logic
- Unit tests: chapter detection, word count limits, validation

### Story Bible Generation (Step 5 — DECISION_004)
- DECISION_004 written, ADVERSARY attacked, JUDGE approved with 5 amendments
- Two prompt modes: initial generation (Ch1) + incremental update (Ch2+)
- Prompt files: `prompts/story_bible_v1.txt`, `prompts/story_bible_update_v1.txt`
- JUDGE amendments implemented:
  - Drift detection: programmatic check that entity counts don't decrease on update
  - JSON repair pipeline: strip code fences → fix trailing commas → retry with explicit instruction
  - Prompt injection hardening: escape `</manuscript_text>` tags in input text
  - Voice profile update window: allow update on Ch2 if POV/tense differs (prologue detection)
  - Pydantic schema validation: `StoryBibleSchema` with defaults and type coercion
- `GET /bible/{manuscript_id}` endpoint for frontend bible viewing
- Unit tests: JSON repair, schema validation with edge cases

### Async Job Queue (Step 6)
- arq worker with `process_text_extraction` and `process_bible_generation` functions
- Extraction job: download S3 → extract text → detect chapters → save → enqueue bible job
- Bible job: load chapters → call Claude → validate → save bible + version snapshot
- Version cap enforcement (max 50 per bible, per DECISION_001 JUDGE)
- Upload endpoint wired to enqueue arq jobs on upload
- Worker config: `backend/app/jobs/worker.py` (run with `arq app.jobs.worker.WorkerSettings`)

### Known limitations
- Email sending is stubbed (TODO comments at send points)
- Rate limiting not yet implemented (documented in decisions, needs middleware)
- S3 upload is synchronous in the endpoint (acceptable for MVP file sizes)
- No frontend yet — backend-only at this point

### Gutenberg Eval Testing (Step 7)
- Downloaded 5 Project Gutenberg samples: P&P (romance), Princess of Mars (fantasy), Moby Dick (literary), 39 Steps (thriller), Hound of Baskervilles (mystery)
- Ran extraction pipeline against all 5 — found and fixed 2 bugs:
  - **TOC false positives:** Added 50-word minimum filter to remove table-of-contents entries from chapter detection. Moby Dick had 202 raw matches → 136 real chapters after filter.
  - **Pre-header text loss:** Text before the first detected chapter header is now captured as implicit Chapter 1 (P&P was losing its entire first chapter).
  - Raised MAX_CHAPTERS from 100 → 150 (Moby Dick has 135 legitimate chapters).
- 13 Gutenberg eval tests written and passing
- Full results documented in `docs/eval_log.md`
- 40 total tests passing (unit + eval)

### Story Bible Generation Eval (Step 7 continued)
- Ran Claude API (`claude-sonnet-4-20250514`) story bible generation on first chapters of all 5 Gutenberg samples
- **100% JSON validity** — all 5 produced valid JSON on first try, no retries needed
- Character extraction verified: Bennet family (P&P), Ishmael (Moby Dick), Holmes+Watson (Hound), Carter (Mars), Hannay+Scudder (39 Steps)
- Voice profiles correct for all 5: POV (first/third) and tense (past) detected accurately
- Found and fixed Moby Dick eval issue: front matter (668-word TOC) was being sent instead of "Loomings" chapter
- Bible results saved to `tests/eval/bible_results/` for manual review
- 15 API eval tests written and passing
- Full results documented in `docs/eval_log.md`

### Infrastructure Fixes (2026-03-12)
- Fixed Alembic migration enum conflict: SQLAlchemy metadata auto-created PostgreSQL enum types, causing duplicate type errors. Fixed by adding `create_type=False` to model Enum definitions.
- Fixed bcrypt incompatibility: passlib doesn't work with bcrypt >= 4.1. Pinned to `bcrypt==4.0.1`.
- All 67 tests passing (52 unit/extraction + 15 API eval)

### React Frontend Shell (2026-03-12)
- Vite 5 + React 18 + TypeScript + React Router 6
- API client (`src/api/client.ts`): typed fetch wrapper with cookie auth for all backend endpoints
- Auth pages: Register (email-only provisional flow), Login (email + password)
- Dashboard: manuscript list with status badges, delete confirmation, links to bible viewer
- Upload page: file picker (docx/txt/pdf), title + genre form, job polling with progress bar (5s interval)
- Manuscript detail: chapter table with word counts and status
- Story Bible viewer: tabbed display (Characters, Timeline, Settings, Voice/World Rules, Plot Threads)
  - Characters grouped by role (protagonist/supporting/minor) with trait tags and relationship badges
  - Timeline with dot-connected event list
  - Voice profile with POV/tense/tone display
  - Plot threads with open/resolved status
- Layout with nav header, auth state management, logout
- Vite dev proxy routes `/auth`, `/manuscripts`, `/bible`, `/health` to backend on :8000
- TypeScript compiles cleanly, production build succeeds (180KB gzipped JS + 9KB CSS)

### Week 1 Complete

---

## 2026-03-13 — Week 2 Ground Truth Eval

### Story Bible Ground Truth Eval Harness (Step 11)
- Created 5 ground truth JSON files in `tests/eval/ground_truth/` (romance, fantasy, literary, thriller, mystery)
- Each ground truth covers first 3 chapters with hand-curated characters, voice profile, settings, plot threads
- Built `test_bible_ground_truth.py` — 27 parametrized tests comparing Claude output vs ground truth
- Incremental 3-chapter generation: chapter 1 from scratch, chapters 2-3 update existing bible
- Disk-based caching of generated bibles to avoid re-running ~20min of API calls during iteration
- **Result: 27/27 tests passing.** All quality targets exceeded:
  - 100% JSON validity, 100% character recall, 100% voice profile match
  - 100% plot thread recall, 100% setting recall, 0 protagonist hallucinations
- Moved root `tests/conftest.py` (FastAPI app fixtures) to `tests/unit/conftest.py` to prevent eval tests from importing the full app stack
- Fixed `resend==2.5.0` → `2.5.1` (removed PyPI version)
- Added `pytest.ini` with `api` mark registration
- Full results in `docs/eval_log.md`

### Story Bible Ground Truth Eval v2 — New Samples (2026-03-13)
- Replaced sample texts to match user-specified Gutenberg titles:
  - Fantasy: A Princess of Mars → **The Time Machine** (Wells)
  - Thriller: The Thirty-Nine Steps → **The Riddle of the Sands** (Childers)
  - Literary: Moby Dick → **The Great Gatsby** (Fitzgerald)
  - Mystery: Hound of the Baskervilles → **The Moonstone** (Collins)
  - Romance: Pride and Prejudice (unchanged)
- Added custom chapter splitting for books with non-standard headers:
  - Time Machine: Roman numeral sections (" I.", " II.", " III.")
  - Great Gatsby: centered Roman numerals with whitespace
- Rewrote all 5 ground truth JSON files for the new texts
- **Result: 27/27 tests passing.** 100% JSON validity, all quality targets exceeded.
- Full results in `docs/eval_log.md`

### Chapter Analysis Engine (Steps 8-10)
- DECISION_005 written and approved — single-prompt architecture
- chapter_analysis_v1.txt: developmental editing prompt with bible cross-reference
- chapter_analyzer.py: async analysis with JSON repair/retry pipeline
- issue_schema.py: Pydantic models with severity-based filtering (15-issue cap)
- genre_conventions.py: 8 genre templates with alias matching
- Smoke tested on P&P Ch1: 3 issues (0 critical, 2 warning, 1 note)

### Known limitations
- Entity count inflation: Gatsby generated 23 characters and Riddle of Sands 21 settings across 3 chapters
- Fuzzy matching in eval harness needs alias awareness (narrator aliases, nickname aliases)

### Chapter Analysis Eval Harness (2026-03-13)
- Built `test_chapter_analysis.py` — 128 parametrized tests across 5 genres × 3 chapters
- Runs `analyze_chapter()` against same 5 Gutenberg samples used for bible eval
- Chapter 1: no-bible mode (first-chapter analysis). Chapters 2-3: full bible cross-reference
- Disk-based caching of analysis results to avoid re-running ~$3 of API calls during iteration
- **Result: 128/128 tests passing.** All quality targets met:
  - 100% JSON validity and schema compliance
  - 100% issue field completeness (description, type, severity, suggestion)
  - Conservative severity calibration: 0-4 criticals per genre across 3 chapters
  - Chapter 1 correctly produces 0 critical consistency issues (no bible to check against)
  - Pacing detection: scene counts, tension arcs, character presence all populated
  - Genre fit: Gatsby gets "strong" on all 3 chapters, Riddle of Sands Ch1 correctly "weak" (epistolary preface)
  - Pacing characters cross-reference bible entries at >50% match rate
- One initial failure fixed: Thriller Ch1 (961-word epistolary preface) has 0 conventions_met — allowed when genre_fit is "weak"
- Analysis results saved to `tests/eval/analysis_results/` for manual review
- Full results documented in `docs/eval_log.md`

### Error State Handling (2026-03-13)
- **Claude API error translation**: Both `story_bible.py` and `chapter_analyzer.py` now catch all Anthropic exceptions (`RateLimitError`, `AuthenticationError`, `APIStatusError`, `APITimeoutError`, `APIConnectionError`) and translate them into user-friendly error messages
- **Extraction edge cases**: `extract_text()` now validates extracted text is non-empty and has >= 50 words. Corrupt DOCX (bad ZIP) and damaged PDF files produce helpful error messages instead of stack traces
- **Worker retry logic**: Transient errors (rate limits, timeouts, overloaded) automatically re-enqueue the job with 30-second backoff, up to `max_attempts` (default 3). Permanent errors fail immediately with user-facing messages
- **Stalled job recovery**: Worker startup hook (`_recover_stalled_jobs`) finds jobs stuck in `running` for 15+ minutes and fails them cleanly, resetting manuscript status from stuck intermediate states
- **Frontend fixes**:
  - Dashboard status labels aligned with backend enums (`bible_generating`, `bible_complete`)
  - Story Bible button shows for `bible_complete` and `analyzing` states (not just `complete`)
  - Error state shows help text on dashboard manuscripts
  - Upload page job polling fixed: checks for `completed` (not `complete`) to match `JobStatus` enum
- 19 error handling tests written and passing (extraction edge cases, transient error detection, Claude API error mocking)

### Week 2 Complete

---

## 2026-03-13 — Week 3 Frontend Polish

### Feedback Dashboard (Step 15)
- **Backend**: `GET /bible/{manuscript_id}/feedback` endpoint
  - Returns all chapters with analysis results (issues, pacing, genre notes)
  - Supports `?severity=` and `?issue_type=` query parameter filters
  - Includes summary counts (total issues by severity, chapters analyzed vs total)
  - User-scoped with full auth requirement (not provisional)
  - Issues sorted by severity (critical first)
- **Frontend**: `FeedbackPage.tsx` — full feedback dashboard
  - Summary bar with issue counts by severity and analysis progress
  - Sidebar with chapter tabs showing per-chapter issue count badges (color-coded)
  - Per-chapter detail view: pacing analysis, genre fit score, and filtered issue list
  - Expandable issue cards: click to reveal original text snippet and suggestion
  - Filter dropdowns for severity and issue type
  - Responsive layout (sidebar → horizontal scroll on mobile)
- **Navigation**: "View Feedback" button on dashboard (complete manuscripts) and manuscript detail page
- **API client**: Added `ManuscriptFeedback`, `ChapterFeedback`, `Issue`, `PacingAnalysis`, `GenreNotes` types
- Production build: 189KB JS + 16KB CSS gzipped

### Progress Indicators + Error State UI (Step 17)
- **ManuscriptPage** rewritten with:
  - Auto-refresh polling (5s) while manuscript is processing (uploading, extracting, bible_generating, analyzing)
  - Animated progress indicator with status label and pulsing dot
  - Progress bar for chapter analysis ("3 of 12 chapters analyzed")
  - Error state banner with actionable help text
  - Per-chapter status badges: color-coded pills (green=analyzed, amber=analyzing, gray=pending, red=error)
  - Back-to-dashboard navigation link
- Status labels mapped to user-friendly text throughout (e.g. "bible_generating" → "Building story bible...")

### Manuscript Deletion + GDPR (Step 18)
- **Account deletion endpoint**: `DELETE /auth/account`
  - Soft-deletes user (sets `deleted_at`)
  - Soft-deletes all user manuscripts
  - Best-effort S3 file cleanup (immediate)
  - Invalidates all sessions (increments `token_version`)
  - Clears auth cookies
- **Enhanced manuscript deletion**: `DELETE /manuscripts/{id}` now cleans up S3 files immediately (best-effort)
- **Settings page** (`/settings`): Data & Privacy section with account deletion flow
  - Danger zone with "Delete my account" button
  - Confirmation step: lists all data that will be deleted, requires typing "DELETE"
  - Settings link added to navigation header
- **API client**: Added `deleteAccount()` function

### Legal Pages (Step 19)
- **Terms of Service** (`/terms`): 10 sections covering IP retention, AI data use, data storage, account deletion, acceptable use, liability
- **Privacy Policy** (`/privacy`): 9 sections covering data collection, AI processing, storage/security, data retention, sharing, user rights, cookies
- Key provisions: "Your manuscripts are never used to train AI models", immediate S3 deletion on account delete, 30-day database purge
- Footer links to ToS and Privacy Policy on every page
- Routes accessible without authentication
- Production build: 202KB JS + 20KB CSS gzipped

### Week 3 Complete

---

## 2026-03-13 — Week 4 Monetization + Email + Launch

### Stripe Payment Integration (Step 20 — DECISION_006)
- DECISION_006 written, ADVERSARY attacked, JUDGE approved with 4 amendments
- **Stripe Checkout (hosted)** — PCI-compliant redirect flow, no card data on our servers
- Endpoints: POST /stripe/create-checkout-session, POST /stripe/webhook, GET /stripe/subscription, POST /stripe/cancel-subscription
- **Per-manuscript**: $49 one-time payment via Stripe Checkout
- **Subscription**: $79/month unlimited manuscripts
- **Beta coupon**: Stripe Promotion Codes enabled (code BETA = $20 off)
- JUDGE amendments implemented:
  - Amendment 1: Idempotent webhook handler — stripe_session_id on manuscripts, duplicate detection
  - Amendment 2: Subscriber auto-pay — manuscripts marked paid at upload for active subscribers
  - Amendment 3: Webhook signature verification mandatory on every request
  - Amendment 4: Free-tier upload limit (3 manuscripts max)
- Alembic migration 003: `stripe_session_id` column
- Worker: `process_chapter_analysis` function enqueued by webhook after payment
- Payment gate: bible_complete + unpaid = paywall prompt in frontend
- Frontend: PricingPage with dual pricing cards, payment success/cancel redirects
- 46 non-DB tests passing

### Email System (Step 21)
- **Resend integration**: `backend/app/email/sender.py` with 7 email templates
  - Verification, password reset, bible-ready notification
  - 3-email drip sequence: Hour 2 (chapter preview), Day 2 (editor comparison), Day 5 (beta expiry)
- **Drip scheduler**: `backend/app/email/drip.py` — PostgreSQL EmailEvent-based scheduling
  - Drip emails scheduled after bible generation for unpaid manuscripts
  - Skipped automatically if manuscript is paid before send time
- **Worker cron job**: Dispatches pending emails every hour via arq cron
- Email sending is real (Resend API) when RESEND_API_KEY is set, logged when not

### Landing Page (Step 22)
- Public marketing page at `/` for unauthenticated users
- Sections: Hero, social proof, features grid (4 cards), how-it-works (3 steps), pricing, trust, final CTA
- Pricing section with per-manuscript ($49) and subscription ($79/mo) cards
- Beta coupon callout: "Enter code BETA at checkout for $20 off"
- Trust section: "Never used for AI training", "Delete anytime", "You own everything"
- Authenticated users redirect from `/` to `/dashboard`
- All internal navigation updated from `/` to `/dashboard`
- Responsive layout with mobile breakpoints
- Production build: 213KB JS + 28KB CSS gzipped (65KB JS gzipped)

### Known limitations
- Stripe products/prices created inline (not pre-configured via Dashboard) — fine for MVP
- No billing history page (Stripe sends receipts directly)
- Email drip sequences use hardcoded base URL (localhost:5173) — needs config for production
- No subscription management UI beyond cancel (upgrade/downgrade not needed for 2-tier pricing)
