# DECISION 001: Database Schema

**Status:** DECIDED — 2026-03-11
**Scope:** Full PostgreSQL schema for GhostEditor MVP

---

## ARCHITECT proposes:

Eight tables covering auth, manuscripts, analysis, async jobs, and email marketing.
Key design choices made explicit:

**1. UUIDs everywhere.** No serial IDs. Manuscript and chapter IDs appear in URLs and API
responses — enumerable integers let attackers probe for other users' data.

**2. Provisional users for the email capture funnel.** The blueprint describes a two-step
flow: email-only capture (Chapter 1 upload, free bible) → full registration (password +
payment). Rather than two tables with a messy merge, `users.password_hash` is nullable.
A provisional user has an email and a verification token but no password. When they
complete registration, we UPDATE the same row. The manuscript FK always points to `users.id`.

**3. Soft deletes on users and manuscripts.** GDPR requires deletion "within 30 days of
request," not instantly. `deleted_at` column + a daily purge job that hard-deletes rows
(and their S3 files) older than 30 days. No CASCADE DELETE on foreign keys — the purge
job handles the full cascade explicitly so nothing is missed (S3, Redis, all child rows).

**4. JSONB for story bibles.** The primary access pattern is "fetch entire bible for
manuscript X" — never "search across all bibles for character Y." JSONB lets us ship fast,
and PostgreSQL's JSONB operators are available if we ever need partial queries. Versioned
with a separate snapshots table for rollback.

**5. PostgreSQL ENUMs for status columns.** Prevents silent data corruption from typos in
application code. Adding new values is cheap (`ALTER TYPE ... ADD VALUE`).

**6. `updated_at` trigger on every mutable table.** Single function, applied via trigger.
No developer has to remember to set it.

### Proposed schema:

```sql
-- =============================================================
-- ENUM TYPES
-- =============================================================

CREATE TYPE subscription_status AS ENUM ('free', 'per_use', 'subscribed');
CREATE TYPE manuscript_status AS ENUM (
    'uploading', 'extracting', 'bible_generating', 'bible_complete',
    'analyzing', 'complete', 'error'
);
CREATE TYPE payment_status AS ENUM ('unpaid', 'paid', 'refunded');
CREATE TYPE chapter_status AS ENUM (
    'uploaded', 'extracting', 'extracted', 'analyzing', 'analyzed', 'error'
);
CREATE TYPE job_status AS ENUM ('pending', 'running', 'completed', 'failed', 'cancelled');
CREATE TYPE job_type AS ENUM (
    'text_extraction', 'story_bible_generation', 'chapter_analysis', 'pacing_analysis'
);
CREATE TYPE issue_severity AS ENUM ('critical', 'warning', 'note');

-- =============================================================
-- TABLES
-- =============================================================

-- 1. USERS
-- Supports provisional state (email-only) for the free bible funnel.
-- password_hash is NULL for provisional users.
CREATE TABLE users (
    id                           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email                        TEXT UNIQUE NOT NULL,
    password_hash                TEXT,
    email_verified               BOOLEAN NOT NULL DEFAULT FALSE,
    verification_token           TEXT,
    verification_token_expires   TIMESTAMPTZ,
    password_reset_token         TEXT,
    password_reset_token_expires TIMESTAMPTZ,
    is_provisional               BOOLEAN NOT NULL DEFAULT TRUE,
    stripe_customer_id           TEXT,
    subscription_status          subscription_status NOT NULL DEFAULT 'free',
    tos_accepted_at              TIMESTAMPTZ,
    deleted_at                   TIMESTAMPTZ,
    created_at                   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at                   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 2. MANUSCRIPTS
-- No ON DELETE CASCADE. Purge job handles deletion explicitly.
CREATE TABLE manuscripts (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL REFERENCES users(id),
    title           TEXT NOT NULL,
    genre           TEXT,
    word_count_est  INTEGER,
    chapter_count   INTEGER,
    status          manuscript_status NOT NULL DEFAULT 'uploading',
    payment_status  payment_status NOT NULL DEFAULT 'unpaid',
    s3_key          TEXT,
    deleted_at      TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 3. CHAPTERS
CREATE TABLE chapters (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    manuscript_id   UUID NOT NULL REFERENCES manuscripts(id),
    chapter_number  INTEGER NOT NULL,
    title           TEXT,
    raw_text        TEXT,
    s3_key          TEXT,
    word_count      INTEGER,
    status          chapter_status NOT NULL DEFAULT 'uploaded',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (manuscript_id, chapter_number)
);

-- 4. STORY BIBLES (current version, one per manuscript)
CREATE TABLE story_bibles (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    manuscript_id   UUID NOT NULL UNIQUE REFERENCES manuscripts(id),
    bible_json      JSONB NOT NULL,
    version         INTEGER NOT NULL DEFAULT 1,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 5. STORY BIBLE VERSIONS (historical snapshots for rollback)
CREATE TABLE story_bible_versions (
    id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    story_bible_id        UUID NOT NULL REFERENCES story_bibles(id),
    bible_json            JSONB NOT NULL,
    version               INTEGER NOT NULL,
    created_by_chapter_id UUID REFERENCES chapters(id),
    created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (story_bible_id, version)
);

-- 6. CHAPTER ANALYSES
CREATE TABLE chapter_analyses (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    chapter_id      UUID NOT NULL REFERENCES chapters(id),
    issues_json     JSONB NOT NULL,
    pacing_json     JSONB,
    genre_notes     JSONB,
    prompt_version  TEXT NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 7. JOBS (async processing status visible to frontend)
CREATE TABLE jobs (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    manuscript_id   UUID NOT NULL REFERENCES manuscripts(id),
    chapter_id      UUID REFERENCES chapters(id),
    job_type        job_type NOT NULL,
    status          job_status NOT NULL DEFAULT 'pending',
    progress_pct    INTEGER NOT NULL DEFAULT 0
                    CHECK (progress_pct >= 0 AND progress_pct <= 100),
    current_step    TEXT,
    error_message   TEXT,
    attempts        INTEGER NOT NULL DEFAULT 0,
    max_attempts    INTEGER NOT NULL DEFAULT 3,
    started_at      TIMESTAMPTZ,
    completed_at    TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 8. EMAIL EVENTS (drip sequence tracking)
CREATE TABLE email_events (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL REFERENCES users(id),
    event_type      TEXT NOT NULL,
    manuscript_id   UUID REFERENCES manuscripts(id),
    scheduled_at    TIMESTAMPTZ NOT NULL,
    sent_at         TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- =============================================================
-- INDEXES
-- =============================================================

-- User lookups (filter soft-deleted)
CREATE INDEX idx_users_email_active
    ON users(email) WHERE deleted_at IS NULL;

-- Manuscript queries scoped to user (the most common query)
CREATE INDEX idx_manuscripts_user_active
    ON manuscripts(user_id) WHERE deleted_at IS NULL;

-- Purge job: find records due for hard deletion
CREATE INDEX idx_users_pending_purge
    ON users(deleted_at) WHERE deleted_at IS NOT NULL;
CREATE INDEX idx_manuscripts_pending_purge
    ON manuscripts(deleted_at) WHERE deleted_at IS NOT NULL;

-- Chapter lookups by manuscript
CREATE INDEX idx_chapters_manuscript
    ON chapters(manuscript_id);

-- Analysis lookups by chapter
CREATE INDEX idx_analyses_chapter
    ON chapter_analyses(chapter_id);

-- Job queue: find pending/running jobs
CREATE INDEX idx_jobs_pending
    ON jobs(status, created_at) WHERE status IN ('pending', 'running');
CREATE INDEX idx_jobs_manuscript
    ON jobs(manuscript_id);

-- Email drip: find unsent events due for dispatch
CREATE INDEX idx_email_events_unsent
    ON email_events(scheduled_at) WHERE sent_at IS NULL;

-- Story bible version history
CREATE INDEX idx_bible_versions_bible
    ON story_bible_versions(story_bible_id);

-- =============================================================
-- updated_at TRIGGER
-- =============================================================

CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_users_updated_at
    BEFORE UPDATE ON users FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER trg_manuscripts_updated_at
    BEFORE UPDATE ON manuscripts FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER trg_chapters_updated_at
    BEFORE UPDATE ON chapters FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER trg_story_bibles_updated_at
    BEFORE UPDATE ON story_bibles FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER trg_jobs_updated_at
    BEFORE UPDATE ON jobs FOR EACH ROW EXECUTE FUNCTION set_updated_at();
```

### Entity relationship summary:

```
users 1──* manuscripts 1──* chapters 1──* chapter_analyses
                       1──1 story_bibles 1──* story_bible_versions
                       1──* jobs
users 1──* email_events
```

### Tradeoffs named:

- **JSONB vs normalized tables for story bible:** JSONB wins for MVP speed but means no
  cross-manuscript queries and schema evolution lives in application code. Acceptable because
  the product never queries across manuscripts — each author sees only their own data.
- **Raw text in PostgreSQL vs S3-only:** Text stored in PG for fast re-analysis without S3
  round-trips. At MVP scale (hundreds of manuscripts, not millions) this is fine. The s3_key
  on chapters is the original file reference for deletion; raw_text is the extracted plaintext.
- **No ON DELETE CASCADE:** Trades convenience for safety. The purge job is more code but
  gives us a 30-day recovery window and explicit S3 cleanup that CASCADE can't provide.

---

## ADVERSARY attacks:

### Attack 1: The provisional user is a half-baked account that will leak data

`password_hash` is nullable, `is_provisional` is a boolean. A provisional user can own
manuscripts and receive analysis results — but they have no password. What prevents someone
from:

(a) **Hijacking a provisional account.** If email verification is the only gate, and the
verification link is predictable or interceptable, an attacker who knows an author's email
can complete the flow and take ownership of their Chapter 1 and story bible.

(b) **Querying the API without authentication.** The provisional user has a `users.id` but
presumably no JWT (no password → no login). How do they get a session token? If you issue a
JWT at email verification time (before password creation), that token is the ONLY credential
protecting their manuscript. Lose it, and there's no password reset path — they never set a
password.

(c) **Provisional users accumulating forever.** Someone scrapes email addresses and signs
up 100K provisional accounts. No password = no friction. Without a TTL on provisional
accounts, the users table grows unbounded with dead rows.

**Failure scenario:** An attacker enters `victim@email.com`, intercepts the verification
email (shared computer, forwarded email), uploads a manuscript in the victim's name, and
the real author has no way to recover the account because there's no password-based auth.

### Attack 2: Soft delete without query-level enforcement is a data leak waiting to happen

Every single query that touches `users` or `manuscripts` must include
`WHERE deleted_at IS NULL`. One missed filter and:

- A "deleted" user's manuscripts appear in search results or admin dashboards.
- A "deleted" manuscript's analysis is returned by `/feedback/{ms_id}`.
- A background job picks up a deleted manuscript for re-analysis.

The partial index on `deleted_at IS NULL` helps performance but does NOT enforce filtering.
This is a discipline-based defense — it works until the first tired developer writes a raw
query at 2am and ships a data leak.

**Failure scenario:** Six months post-launch, a new endpoint joins manuscripts to chapters
and forgets the `deleted_at IS NULL` filter. A user who requested GDPR deletion sees their
"deleted" manuscript reappear when a new feature launches. They file a GDPR complaint.

### Attack 3: Story bible version history has no size cap and no cleanup

`story_bible_versions` stores a FULL JSONB snapshot per version. A 50-chapter fantasy
manuscript means 50 snapshots of a growing JSON blob. If the bible reaches 100KB by
Chapter 50, the version history alone is 50 * ~50KB (average) = ~2.5MB per manuscript.
At 1,000 manuscripts, that's 2.5GB in version history alone.

There's no:
- Maximum number of versions retained
- Compression or diff-based storage
- Cleanup job to prune old versions after N days

**Failure scenario:** A power user uploads a 100-chapter manuscript. The story bible
version table for that single manuscript consumes 10MB+. Multiply by thousands of users
and the database grows faster than revenue.

### Attack 4: `chapter_analyses` has no UNIQUE constraint — duplicate analyses accumulate silently

Nothing prevents creating multiple `chapter_analyses` rows for the same `chapter_id`.
If a user clicks "retry analysis" or if the job worker crashes after writing the result
but before updating job status, duplicate analyses are created. Which one does the frontend
display? The first? The latest? Whichever the ORM returns?

**Failure scenario:** Job worker completes analysis, writes to `chapter_analyses`, then
crashes before marking the job as `completed`. The job is retried (because `attempts <
max_attempts`), produces a second analysis row. The frontend shows inconsistent results
depending on query ordering. The user sees their feedback change every time they refresh.

### Attack 5: No foreign key from `email_events` to the purge chain

`email_events` references `users(id)` but has no `ON DELETE` behavior specified, and
there's no `deleted_at` on this table. When the purge job hard-deletes a user, these
rows either:

(a) Fail with an FK violation (if the DB enforces referential integrity on hard delete), or
(b) Become orphaned (if the FK is deferred or the purge job deletes email_events first).

Neither is documented. The email drip cron job will try to send emails to deleted users
unless it also checks `users.deleted_at`.

**Failure scenario:** User requests account deletion. Purge job soft-deletes them. Two
days later, the drip cron fires "Your story bible is waiting!" to someone who explicitly
asked to be forgotten. GDPR violation AND a bad user experience.

---

## JUDGE decides:

**Verdict: ARCHITECT's schema is approved with five required changes.**

The core design — UUIDs, soft deletes, JSONB bibles, provisional users, ENUMs — is sound
for MVP. ADVERSARY raised valid structural risks that are cheap to fix now and expensive to
fix after launch.

### Required changes:

**1. Provisional user hardening (Attack 1): VALID.**

- Provisional JWT tokens must be short-lived (1 hour, not the standard 7 days for full
  accounts). Issued at email verification, they allow Chapter 1 upload and bible viewing
  ONLY. Store `provisional_token_expires` — enforce in auth middleware.
- Add a TTL: provisional users with no password set after 30 days are auto-purged by the
  same daily purge job. Add index: `CREATE INDEX idx_users_provisional_stale ON
  users(created_at) WHERE is_provisional = TRUE AND deleted_at IS NULL;`
- The verification token must be cryptographically random (32 bytes, hex-encoded) and
  single-use. Delete it after verification. This is application logic, not schema, but
  ARCHITECT must enforce it in the auth module.

**2. Soft delete enforcement (Attack 2): VALID.**

- All database queries MUST go through a repository layer that applies
  `WHERE deleted_at IS NULL` by default. No raw queries allowed in endpoint handlers.
  Implement as a SQLAlchemy mixin or a base query method that auto-filters.
- The purge job is the ONLY code path that queries `WHERE deleted_at IS NOT NULL`.
  Document this in code comments.
- This is an architecture enforcement, not a schema change — but ARCHITECT must implement
  it as part of the DB layer, not leave it to endpoint developers.

**3. Bible version cap (Attack 3): PARTIALLY VALID.**

- At MVP scale (hundreds of manuscripts), 2.5GB is not a real problem. The version table
  is insurance for prompt debugging and rollback, not a permanent archive.
- **However:** add a cap of 50 versions per story bible. When version 51 is written,
  delete version 1. Implement in application code. This bounds the worst case.
- Diff-based storage is over-engineering for MVP. Revisit if storage costs become material.

**4. Duplicate analysis prevention (Attack 4): VALID.**

- Add a unique constraint: `UNIQUE (chapter_id, prompt_version)`. If a retry produces a
  new analysis with the same prompt version, it UPSERTs instead of inserting. If the prompt
  version changes (e.g., `chapter_analysis_v2`), old results are preserved alongside new
  ones — which is what we want for eval comparison.
- Change `chapter_analyses` to:
  ```sql
  UNIQUE (chapter_id, prompt_version)
  ```

**5. Email events in the purge chain (Attack 5): VALID.**

- The purge job must delete `email_events` rows for a user BEFORE hard-deleting the user.
  Document the purge order explicitly:
  1. Cancel pending email events
  2. Delete chapter_analyses (via chapters → manuscripts → user)
  3. Delete story_bible_versions → story_bibles
  4. Delete jobs
  5. Delete chapters
  6. Delete manuscripts (and S3 files)
  7. Delete email_events
  8. Delete user
- Additionally: the email drip cron must join against `users` and filter
  `WHERE users.deleted_at IS NULL` before sending. Add this to the query, not as an
  application-level check.

### Green light:

Apply the five changes above. Then write the migration. ARCHITECT may proceed.

---

## Final schema amendments (post-JUDGE):

```sql
-- Add to chapter_analyses:
ALTER TABLE chapter_analyses
    ADD CONSTRAINT uq_analysis_chapter_prompt UNIQUE (chapter_id, prompt_version);

-- Add provisional user cleanup index:
CREATE INDEX idx_users_provisional_stale
    ON users(created_at)
    WHERE is_provisional = TRUE AND deleted_at IS NULL;
```

### Purge job deletion order (documented):

```
1. email_events      WHERE user_id = ?
2. chapter_analyses  WHERE chapter_id IN (SELECT id FROM chapters WHERE manuscript_id IN (...))
3. story_bible_versions WHERE story_bible_id IN (SELECT id FROM story_bibles WHERE manuscript_id IN (...))
4. story_bibles      WHERE manuscript_id IN (SELECT id FROM manuscripts WHERE user_id = ?)
5. jobs              WHERE manuscript_id IN (...)
6. chapters          WHERE manuscript_id IN (...)
7. manuscripts       WHERE user_id = ?  (+ S3 delete for each s3_key)
8. users             WHERE id = ?       (hard delete)
```

### Bible version cap:

Application code enforces max 50 versions per story_bible_id. On insert of version N > 50,
delete version (N - 50) in the same transaction.
