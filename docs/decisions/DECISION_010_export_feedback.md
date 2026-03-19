# DECISION 010: Export Feedback as PDF / DOCX

## ARCHITECT proposes:

Add a new endpoint `GET /bible/{manuscript_id}/feedback/export?format=pdf` (also `format=docx`)
that generates a formatted document containing all developmental editing feedback for a manuscript.

**Design:**

- Scoped to authenticated user (full auth via `get_current_user`, not provisional)
- Verifies manuscript ownership before generating
- Generates a structured document with:
  - Title page: manuscript name, genre/format, generation date, summary stats
  - For nonfiction: document synthesis scores (thesis clarity, argument coherence, etc.) at the top
  - Per-chapter/section breakdown with issues sorted by severity
  - Issues table per chapter: severity | type | description | suggestion
- Uses `reportlab` for PDF generation, `python-docx` for DOCX (already in requirements)
- Returns the file as a `StreamingResponse` with appropriate `Content-Type` and `Content-Disposition` headers
- No persistence: generated on-demand per request, no files stored in S3 or DB
- Reuses the same query logic as `get_manuscript_feedback` / `get_nonfiction_feedback`

**Tradeoffs named:**

- On-demand generation is simpler than caching but costs CPU per request
- No async job queue for export: acceptable because generation is fast (no LLM calls)
- Both fiction and nonfiction use the same endpoint, switching on `manuscript.document_type`

## ADVERSARY attacks:

1. **CPU exhaustion via repeated export requests on large manuscripts.** A manuscript with
   50+ chapters and hundreds of issues will produce a large document. If a user (or attacker)
   hammers the export endpoint, each request does O(chapters * issues) work to build the PDF/DOCX
   in memory. Without rate limiting, this could degrade service for other users.
   **Failure scenario:** An attacker scripts 100 concurrent export requests for a 50-chapter
   manuscript. Each request builds a multi-MB PDF in memory. The backend runs out of memory
   or starves other request handlers of CPU time.

2. **Information disclosure via manuscript ID enumeration.** The endpoint takes a manuscript UUID
   in the path. If ownership verification has any timing side-channel or if the error message
   differs between "manuscript does not exist" and "manuscript belongs to another user," an
   attacker could enumerate valid manuscript IDs. Additionally, if the generated document
   includes internal metadata (database IDs, timestamps of analysis runs, or user email), it
   could leak information that should stay server-side.
   **Failure scenario:** The exported PDF includes the user's internal UUID or chapter database
   IDs in headers/footers. The user shares the PDF with a co-author who now has internal
   identifiers they could attempt to use against the API.

## JUDGE decides:

**Green light with two required mitigations.**

1. **Rate limit the export endpoint:** Apply a per-user rate limit of 10 exports per hour.
   This is generous for legitimate use (an author might export a few times while iterating)
   but prevents abuse. Reuse the existing `rate_limit` infrastructure. This addresses
   ADVERSARY's CPU exhaustion concern proportionally without over-engineering.

2. **Sanitize exported content:** The exported document must contain only user-visible data
   (manuscript title, chapter titles, issue descriptions, suggestions). Never include internal
   UUIDs, database IDs, user emails, or analysis timestamps in the exported file. Chapter
   numbers and titles are sufficient identifiers. This is a valid concern but straightforward
   to implement.

ADVERSARY's timing side-channel concern is weak: the endpoint already uses the same
ownership-check pattern as every other endpoint in the codebase (returns 404 for both
"not found" and "not yours"), which is the correct approach. No change needed there.

Proceed with implementation.
