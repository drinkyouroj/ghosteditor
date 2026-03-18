# DECISION 008: Nonfiction Section Detection and Database Schema

## Context

GhostEditor currently handles fiction manuscripts only. The existing 3-tier fallback
chain (DECISION_007) detects manuscript structure via: LLM splitting -> auto-split at
~4K word boundaries -> regex-based chapter detection. This works well for novels and
plays but does not account for nonfiction documents which have fundamentally different
structural patterns (headings, subheadings, no chapter markers) and require different
analysis dimensions (argument mapping vs. story bible, source evaluation vs. character
consistency).

This DECISION covers two coupled concerns:
1. How to detect and chunk nonfiction sections for analysis
2. Database schema additions to support nonfiction manuscripts

---

## Part 1: Nonfiction Section Detection

### ARCHITECT proposes:

#### Header detection with 1,500-word chunked fallback

Extend the existing 3-tier fallback chain with a nonfiction-aware path. When a
manuscript is marked as `document_type = 'nonfiction'`, the section detection
pipeline uses a nonfiction-specific strategy:

**Step 1: Header detection**

Scan the extracted text for structural headers using a pattern-matching approach:

```python
NONFICTION_HEADER_PATTERNS = [
    # Markdown-style headers
    r'^#{1,3}\s+.+$',
    # ALL-CAPS lines (common in academic/business writing)
    r'^[A-Z][A-Z\s:]{4,}$',
    # Numbered section headers: "1.", "1.1", "Section 1:", "Part I:"
    r'^(?:Section|Part|Chapter)?\s*\d+[\.\):]?\s+.+$',
    # Lines that are short (<80 chars), followed by a blank line, and preceded
    # by a blank line — likely a heading
]
```

A line is classified as a header if it matches any pattern AND:
- Is preceded by a blank line (or is the first line)
- Is followed by a blank line or body text
- Is under 120 characters long
- Is NOT part of a list or citation

**Step 2: Header threshold and fallback**

If **fewer than 2 headers** are detected in the full document, fall back to
**1,500-word chunking** at the nearest paragraph boundary (double newline). The
1,500-word target (vs. 4,000 for fiction auto-split) reflects that nonfiction
sections tend to be shorter and more self-contained — analysis quality degrades
on longer chunks that mix multiple arguments or topics.

If 2 or more headers are detected, split at each header position. Each resulting
section gets the header text as its title.

**Step 3: Record detection method**

Each section result includes a `section_detection_method` field:
- `"header"` — split was performed at detected structural headers
- `"chunked"` — fewer than 2 headers found; document was chunked at 1,500 words

This field is stored in `nonfiction_section_results.section_detection_method` so
downstream analysis (Agent 2's prompts) can adjust behavior — e.g., chunked sections
may need extra context about surrounding content since the boundaries are artificial.

**Integration with existing 3-tier fallback chain:**

The nonfiction path runs INSTEAD OF the fiction path, not as an additional fallback
tier. The routing decision happens early based on `manuscripts.document_type`:

```
if document_type == 'fiction':
    # Existing chain: LLM splitting -> auto-split -> regex (DECISION_007)
elif document_type == 'nonfiction':
    # New chain: header detection -> 1,500-word chunked fallback
```

This keeps the two paths cleanly separated. The LLM splitting prompt (DECISION_007)
is fiction-specific and would need a separate nonfiction prompt to work for nonfiction
— that's future work, not in scope here.

**Tradeoffs named:**

- (+) Simple regex header detection is fast, deterministic, and needs no LLM call
- (+) 1,500-word chunks are small enough for focused nonfiction analysis
- (+) `section_detection_method` field gives downstream consumers transparency
- (-) Regex header detection will miss unconventional heading styles
- (-) 1,500-word chunks may split mid-argument in dense academic text
- (-) No LLM-assisted detection for nonfiction yet (future enhancement)

### ADVERSARY attacks:

#### 1. Document with exactly one header

A self-help book with a single "INTRODUCTION" header followed by 30,000 words of
continuous text. The header count is 1 (fewer than 2), so the system falls back to
1,500-word chunking. But that one header was meaningful — it marks the actual start
of content after front matter. The chunking ignores it entirely and may include
front matter (title page, copyright notice, dedication) in the first chunk.

**Failure scenario:** The first analysis chunk contains "Copyright 2024 by Jane Smith.
All rights reserved. For my mother, who always believed..." mixed with the first
1,500 words of actual content. The argument map identifies "Jane Smith's mother" as
a key figure in the book's thesis. The user sees this and loses trust.

#### 2. Headers that appear mid-paragraph

Academic papers and journalism frequently contain inline references to section titles
or use bold text that looks like a header but is actually mid-paragraph emphasis. For
example:

```
The results confirm our hypothesis. IMPLICATIONS FOR POLICY
are discussed in the following section, where we examine...
```

The regex sees "IMPLICATIONS FOR POLICY" on its own (if line-wrapped by the PDF
extractor) and splits there, breaking the paragraph in half. The preceding text
loses its concluding context, and the following section starts mid-sentence.

**Failure scenario:** A 50-page PDF where the text extractor inserts newlines at
the PDF column boundary. The regex matches 47 "headers" that are actually mid-line
wraps, producing 47 tiny sections averaging 300 words each, most starting
mid-sentence. Analysis on each fragment is meaningless.

#### 3. PDF extraction collapses all whitespace

Many PDF-to-text extractors (especially for scanned PDFs or complex layouts)
collapse all whitespace into single spaces or strip blank lines entirely. The header
detection relies on "preceded by a blank line" and "followed by a blank line" as
key signals. When the extractor removes all blank lines, every header looks like
body text and none are detected.

**Failure scenario:** User uploads a well-structured business book as a PDF with
clear chapter headers. After extraction, the text is a single wall of text with no
blank lines. Zero headers detected, system falls back to 1,500-word chunking. The
user's 20-chapter book is split into 40 arbitrary chunks. Every analysis result
refers to "this section" with no meaningful section identity.

#### 4. The 1,500-word chunk size is arbitrary and untested

Why 1,500 words and not 1,000 or 2,000? For academic papers, a single argument
can span 3,000+ words. For listicle-style self-help, each point is 200-500 words.
A fixed chunk size will be wrong for at least half the nonfiction formats.

**Failure scenario:** A philosophy book where each argument builds across 4,000
words. The 1,500-word chunker splits every argument into 2-3 pieces. The analysis
prompt sees fragment 2 of 3 and reports "this section lacks a clear thesis" — because
the thesis was in fragment 1. The user receives 20 "lacks clear thesis" warnings
that are all false positives.

### JUDGE decides:

**Verdict: Green light with required changes.**

The header detection + chunked fallback strategy is the right starting point for
nonfiction. It is deliberately simple and avoidable — a future DECISION can add
LLM-assisted nonfiction splitting once we have eval data on real nonfiction manuscripts.

#### Required changes:

1. **One-header edge case (Attack #1):** When exactly 1 header is detected, treat it
   as a content-start marker. Strip everything before it as front matter, THEN apply
   1,500-word chunking to the remaining text. Set `section_detection_method` to
   `"chunked"` since the primary split strategy is still chunking. Log a warning:
   "Single header detected — treated as content start marker."

2. **Mid-paragraph headers (Attack #2):** ADVERSARY's attack is valid and the most
   dangerous. Add a minimum section length threshold: if a detected header produces a
   section shorter than 200 words, merge it with the following section. This prevents
   the "47 tiny sections" failure mode. Additionally, require that a header candidate
   line must be a complete line (not a fragment) — specifically, it must NOT be followed
   by a lowercase letter or continuation punctuation (comma, semicolon) on the next
   non-blank line. This catches most mid-paragraph false positives from PDF line wrapping.

3. **Collapsed whitespace (Attack #3):** ADVERSARY's attack is valid. When zero headers
   are detected AND the text contains no blank lines at all (suggesting whitespace
   collapse), log a specific warning: "Document appears to have lost formatting during
   extraction. Section boundaries may be inaccurate." Do NOT attempt to reconstruct
   headers from capitalization patterns alone — that path leads to more false positives
   than the chunked fallback. Accept the chunked fallback and surface the warning to
   the user.

4. **Chunk size (Attack #4):** ADVERSARY is right that 1,500 is arbitrary. However,
   making it configurable per-nonfiction-format is premature. Keep 1,500 as the default
   but make it a constant (`NONFICTION_CHUNK_TARGET_WORDS = 1500`) that can be adjusted
   after eval. Add a `+/- 20%` flexibility window: the chunker should look for paragraph
   boundaries within 1,200-1,800 words rather than hard-splitting at exactly 1,500.
   This is the same approach as the fiction auto-split's ~4K target with boundary
   flexibility.

#### Implementation notes:

- The section detection logic should live in `backend/app/manuscripts/extraction.py`
  alongside the existing fiction detection code.
- The `section_detection_method` value is stored per-section-result, not per-manuscript,
  because a future hybrid approach might detect some sections by header and fill gaps
  with chunking.
- The nonfiction path does NOT use the LLM splitting prompt from DECISION_007. That
  prompt is fiction-specific. Nonfiction LLM splitting is a separate future DECISION.

---

## Part 2: Nonfiction Database Schema

### ARCHITECT proposes:

Add the following to the existing schema:

#### Enum additions to `manuscripts` table

```sql
-- New enum type for document classification
CREATE TYPE document_type AS ENUM ('fiction', 'nonfiction');

-- New enum type for nonfiction format subcategory
CREATE TYPE nonfiction_format AS ENUM (
    'academic', 'personal_essay', 'journalism', 'self_help', 'business'
);

ALTER TABLE manuscripts
    ADD COLUMN document_type document_type NOT NULL DEFAULT 'fiction',
    ADD COLUMN nonfiction_format nonfiction_format NULL;
```

- `document_type` defaults to `'fiction'` so existing manuscripts are unaffected.
- `nonfiction_format` is nullable because it only applies when `document_type = 'nonfiction'`
  and may not be set immediately (could be determined by analysis).

#### New table: `argument_maps`

Mirrors `story_bibles` structure. One per nonfiction manuscript. Contains the
structured analysis of the document's argument/thesis structure.

```sql
CREATE TABLE argument_maps (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    manuscript_id UUID NOT NULL UNIQUE REFERENCES manuscripts(id) ON DELETE CASCADE,
    argument_map_json JSONB NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX ix_argument_maps_manuscript_id ON argument_maps(manuscript_id);
```

#### New table: `nonfiction_section_results`

Mirrors `chapter_analyses` structure. Stores per-section analysis results for
nonfiction manuscripts. The `chapter_id` FK points to the `chapters` table because
nonfiction sections are stored as chapters (the `chapters` table is format-agnostic
despite its name).

```sql
CREATE TABLE nonfiction_section_results (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    chapter_id UUID NOT NULL REFERENCES chapters(id) ON DELETE CASCADE,
    section_results_json JSONB NOT NULL,
    dimension TEXT NOT NULL,
    section_detection_method TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX ix_nonfiction_section_results_chapter_id
    ON nonfiction_section_results(chapter_id);
CREATE UNIQUE INDEX uq_nonfiction_section_results_chapter_dimension_version
    ON nonfiction_section_results(chapter_id, dimension, prompt_version);
```

The `dimension` field identifies what aspect was analyzed (e.g., "argument_strength",
"source_evaluation", "clarity", "structure"). This allows multiple analysis passes
per section without overwriting previous results.

The `section_detection_method` field stores `"header"` or `"chunked"` per the
section detection design above. This is per-result because the detection method
affects how the analysis prompt should contextualize the section.

#### New table: `nonfiction_document_summaries`

One row per manuscript. Stores the document-level summary/overview generated after
all sections are analyzed.

```sql
CREATE TABLE nonfiction_document_summaries (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    manuscript_id UUID NOT NULL UNIQUE REFERENCES manuscripts(id) ON DELETE CASCADE,
    summary_json JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX ix_nonfiction_document_summaries_manuscript_id
    ON nonfiction_document_summaries(manuscript_id);
```

**Tradeoffs named:**

- (+) Clean separation: fiction uses `story_bibles` + `chapter_analyses`, nonfiction
  uses `argument_maps` + `nonfiction_section_results` + `nonfiction_document_summaries`
- (+) All new tables use `ON DELETE CASCADE` from `manuscripts`, so deletion works
- (+) Reuses `chapters` table for sections — no structural duplication
- (-) `chapters` table name is misleading for nonfiction sections
- (-) Separate tables mean queries that want "all analysis results regardless of type"
  need a UNION

### ADVERSARY attacks:

#### 1. CASCADE DELETE behavior creates silent data loss risk

All three new tables use `ON DELETE CASCADE` from `manuscripts.id`. If a manuscript
row is deleted (either explicitly or via a cascading user deletion), all argument
maps, section results, and document summaries vanish instantly with no confirmation
and no soft-delete window.

**Failure scenario:** An admin script or a bug in the GDPR deletion flow accidentally
hard-deletes a manuscript row instead of setting `deleted_at`. The cascade instantly
destroys all analysis results — potentially hours of LLM processing — with no recovery
path. The `deleted_at` soft-delete pattern on `manuscripts` is supposed to prevent
this, but nothing enforces that all deletion code paths use soft-delete.

#### 2. Missing indexes on JSONB columns and query patterns

The schema indexes `manuscript_id` and `chapter_id` foreign keys but does not index
any JSONB fields. If the frontend needs to filter or search within `argument_map_json`
(e.g., "show all manuscripts where the argument strength score is below 3"), these
queries will be full table scans.

Additionally, `nonfiction_section_results` has no index on `dimension` alone. A query
like "show all argument_strength results across all chapters of a manuscript" requires
joining through `chapters` to `manuscripts` and then filtering on `dimension` — workable
but potentially slow on large manuscripts with many sections.

**Failure scenario:** Not a crash, but a performance degradation. A user with 10
manuscripts, each with 30 sections and 5 dimensions per section, has 1,500 rows in
`nonfiction_section_results`. Querying "all clarity results for manuscript X" does
a sequential scan on 1,500 rows. At scale this becomes a problem.

#### 3. NULL handling for `nonfiction_format` on fiction manuscripts

`nonfiction_format` is nullable and only meaningful when `document_type = 'nonfiction'`.
But there is no CHECK constraint enforcing this. A fiction manuscript could have
`nonfiction_format = 'academic'` set via a bug or direct DB access. Conversely, a
nonfiction manuscript could have `nonfiction_format = NULL` indefinitely if the
classification step fails or is skipped.

**Failure scenario:** A bug in the upload flow sets `nonfiction_format = 'self_help'`
on a fiction manuscript. The analysis pipeline checks `nonfiction_format` to select
the right prompt template and sends the novel through the self-help analysis pipeline.
The user gets feedback about "argument structure" and "actionable takeaways" for their
fantasy novel.

#### 4. `section_detection_method` is TEXT, not an enum

The `section_detection_method` column is `TEXT NOT NULL` with no constraint. A typo
in code (`"headers"` instead of `"header"`) would silently insert a bad value. The
fiction side (DECISION_007) already uses `split_method` with values "llm", "auto",
"regex" — there is no consistency between the fiction and nonfiction naming.

**Failure scenario:** Code writes `"Header"` (capitalized) to one section and
`"header"` (lowercase) to another. Downstream queries filtering on
`section_detection_method = 'header'` miss half the results.

### JUDGE decides:

**Verdict: Green light with required changes.**

The schema design is clean and follows established patterns. ADVERSARY's attacks
are valid and must be addressed:

#### Required changes:

1. **Cascade delete safety (Attack #1):** The CASCADE is correct and intentional —
   when a manuscript is truly deleted, its analysis data should go with it. However,
   add a CHECK constraint or application-level guard: the `manuscripts` table already
   uses soft-delete via `deleted_at`. Document clearly (as a code comment in the
   migration) that hard-delete of manuscript rows is ONLY permitted via the GDPR
   purge flow, which is a deliberate and audited operation. Do NOT remove CASCADE —
   that would create orphaned analysis rows. The protection is at the application
   layer (never hard-delete except in GDPR purge), not the schema layer.

2. **Index strategy (Attack #2):** ADVERSARY is right about the `dimension` index.
   Add a composite index on `(chapter_id, dimension)` to `nonfiction_section_results`
   — this covers the most common query pattern ("all results of dimension X for
   chapter Y"). Do NOT add JSONB GIN indexes now — that's premature optimization.
   The JSONB columns store complete analysis payloads that are read as blobs, not
   queried into. If JSONB querying becomes necessary, add indexes in a future migration.

3. **NULL constraint (Attack #3):** Add a CHECK constraint:
   ```sql
   CHECK (
       (document_type = 'fiction' AND nonfiction_format IS NULL)
       OR
       (document_type = 'nonfiction')
   )
   ```
   This prevents fiction manuscripts from having a nonfiction_format set. It does
   allow nonfiction manuscripts to have NULL nonfiction_format (the format may be
   determined later by analysis). This is the correct constraint.

4. **Detection method type safety (Attack #4):** Use a PostgreSQL enum type
   `section_detection_method_enum` with values `('header', 'chunked')` instead of
   TEXT. This prevents typos at the DB level. The naming inconsistency with fiction's
   `split_method` is acceptable — they are different concepts for different document
   types and live in different tables.

#### Implementation notes:

- Migration number: 004 (follows 003_add_stripe_session_id.py)
- All new enum types must be created in the migration before they are referenced
- The `argument_maps` index on `manuscript_id` is redundant with the UNIQUE
  constraint — PostgreSQL creates an implicit unique index. Remove the explicit
  `CREATE INDEX` for `argument_maps.manuscript_id`. Same applies to
  `nonfiction_document_summaries.manuscript_id`.
- Update `backend/app/db/models.py` with SQLAlchemy models for all three new tables
  plus the two new enum types
