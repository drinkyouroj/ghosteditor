# DECISION 011: Re-run Analysis Endpoint

## ARCHITECT proposes:

Add a `POST /manuscripts/{id}/reanalyze` endpoint that lets users re-run
chapter/section analysis on a manuscript without re-uploading the file.

**Use cases:**
- Prompts have improved since the original analysis run
- First analysis hit transient errors on some chapters
- User wants fresh results after reading the initial feedback

**Design:**
- Requires full authentication (no provisional users)
- Manuscript must be in `complete` or `error` status AND `paid`
- Clears existing analysis results based on document type:
  - Fiction: deletes `ChapterAnalysis` rows, resets chapter statuses to `extracted`
  - Nonfiction: deletes `NonfictionSectionResult` rows and `NonfictionDocumentSummary`,
    resets chapter statuses to `extracted`
- Does NOT delete the story bible (fiction) or argument map (nonfiction) — those are
  generated from the manuscript text itself and are independent of chapter analysis
- Re-enqueues the first chapter for analysis using the same chained pattern as
  `POST /manuscripts/{id}/analyze`
- Rate limit: 3 re-analyses per user per day (separate from the upload rate limit)
- Returns 202 Accepted with a message

**Tradeoffs:**
- We clear all analysis results rather than allowing selective re-analysis per chapter.
  Simpler to implement and avoids inconsistent cross-chapter analysis state.
- We keep the story bible / argument map because they are derived from the raw text and
  regenerating them would add cost and latency with no benefit.

## ADVERSARY attacks:

1. **Accidental data loss — no undo.** If a user clicks "Re-run Analysis" by accident,
   all their existing chapter feedback is immediately and permanently deleted. There is no
   confirmation step on the backend, and no way to recover the previous analysis results.
   A user who spent money on analysis and then accidentally triggers this loses all their
   feedback. The frontend must enforce a confirmation dialog, but relying on the frontend
   alone is fragile — a bot or script can hit the endpoint directly.

2. **Race condition: re-analyze while analysis is in progress.** If a manuscript is in
   `analyzing` status (mid-analysis) and the user somehow triggers reanalyze (e.g., via
   direct API call, or the UI shows an error state due to a stale poll), the endpoint could
   delete partially-completed results while the worker is still writing new ones. This
   creates a corrupted state where some chapters have results from the old run and some
   from the new run, or the worker writes to a chapter that was just reset.

## JUDGE decides:

**Green light with two required mitigations:**

1. **Guard against in-progress analysis:** The endpoint must reject requests when the
   manuscript is in `analyzing` status. Only `complete` and `error` are acceptable.
   This is already specified in the ARCHITECT proposal — ADVERSARY's concern is valid
   but already addressed by the status check. No change needed.

2. **Accidental deletion risk is acceptable for MVP.** The frontend confirmation dialog
   provides sufficient protection. Adding backend-side confirmation (e.g., a two-step
   token flow) would over-engineer the MVP. The rate limit (3/day) also naturally limits
   the blast radius. No change needed.

Proceed with implementation as proposed.
