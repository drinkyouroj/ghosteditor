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

### Next milestone (Week 2)
- DECISION for chapter analysis prompt design
- Chapter analysis engine
- Cross-chapter consistency checker
- Eval harness with ground truth JSON
