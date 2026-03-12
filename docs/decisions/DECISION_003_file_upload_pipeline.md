# DECISION 003: File Upload Pipeline

**Status:** DECIDED — 2026-03-11
**Scope:** Manuscript upload endpoint, file validation, text extraction, S3 storage, chapter detection

---

## ARCHITECT proposes:

### Overview

A single endpoint `POST /manuscripts/upload` that accepts a manuscript file (.docx, .txt, .pdf),
validates it, stores the original in S3, extracts text, detects chapter boundaries, and creates
the manuscript + chapter rows in PostgreSQL. The endpoint returns immediately after validation
and S3 upload; text extraction and chapter detection run as an async job.

### Endpoint design

**POST /manuscripts/upload**
- Auth: `get_current_user_allow_provisional` (provisional users can upload Chapter 1)
- Content-Type: `multipart/form-data`
- Fields: `file` (required), `title` (required), `genre` (optional)
- Max file size: 10MB (enforced at both reverse proxy and application level)
- Accepted MIME types: `application/vnd.openxmlformats-officedocument.wordprocessingml.document`,
  `text/plain`, `application/pdf`

### Validation sequence

1. **File size check** — reject > 10MB with 413.
2. **MIME type check** — read first 2048 bytes, check magic bytes with `python-magic`. Do NOT
   trust the Content-Type header or file extension alone.
3. **Extension check** — secondary validation. Must match: `.docx`, `.txt`, `.pdf`.
4. If all pass → upload original file to S3 under key `manuscripts/{user_id}/{manuscript_id}/{filename}`.
5. Create `manuscripts` row with `status='uploading'` and `s3_key`.
6. Enqueue async job: `text_extraction` for the manuscript.
7. Return 201 with `{manuscript_id, status, job_id}`.

### Text extraction (async job)

Runs in the Redis worker:

1. Download file from S3.
2. Extract based on format:
   - **DOCX:** `python-docx` → iterate paragraphs, preserve paragraph breaks.
   - **TXT:** Read with charset detection (try UTF-8 first, fallback to chardet).
   - **PDF:** `pypdf2` → extract text per page. If extraction yields < 100 chars per page
     on average, flag as "likely scanned" and mark manuscript as `error` with message
     "This PDF appears to be a scanned image."
3. **Chapter detection:**
   - Regex patterns (in priority order):
     - `^Chapter\s+\d+` (Chapter 1, Chapter 2, ...)
     - `^Chapter\s+[A-Z][a-z]+` (Chapter One, Chapter Two, ...)
     - `^CHAPTER\s+` (all caps variant)
     - `^\d+\.?\s*$` (bare number on its own line)
   - Split text at detected chapter boundaries.
   - If no chapters detected: treat entire text as Chapter 1.
   - Create `chapters` rows with `chapter_number`, `raw_text`, `word_count`.
4. **Word count:** Count words across all chapters. If total > 120,000 words, mark manuscript
   as `error` with message "Manuscript exceeds 120,000 word limit."
5. **Language detection:** Check first 1,000 words. If not English (use a simple heuristic —
   high ASCII ratio or use `langdetect`), mark as error.
6. Update manuscript `status` to `extracting` → `bible_generating` (if Chapter 1 ready)
   or `error`.

### Additional endpoints

**GET /manuscripts** — List user's manuscripts. Auth: `get_current_user`.
Returns `[{id, title, status, payment_status, chapter_count, word_count_est, created_at}]`.
Filters `deleted_at IS NULL` (enforced by query layer).

**GET /manuscripts/{id}** — Get single manuscript detail. Auth: `get_current_user_allow_provisional`.
Scoped to `user_id`. Returns manuscript + chapters list + job status.

**DELETE /manuscripts/{id}** — Soft-delete manuscript. Auth: `get_current_user`.
Sets `deleted_at = now()`. Does NOT immediately delete S3 file (purge job handles that).

**GET /jobs/{id}** — Get job status. Auth: `get_current_user_allow_provisional`.
Returns `{id, status, progress_pct, current_step, error_message}`.
Scoped: job must belong to a manuscript owned by the requesting user.

### Tradeoffs named

- **Sync validation + async extraction:** User gets immediate feedback on bad files but
  doesn't wait for extraction. Trade: the manuscript is in a "processing" state that
  the frontend must handle.
- **S3 before extraction:** We store the original even if extraction fails. Trade: wasted
  S3 storage on bad files. Benefit: the user can retry without re-uploading.
- **Chapter detection is regex-based:** Will miss creative chapter headers ("PART ONE:
  THE BEGINNING"). Trade: some manuscripts need manual chapter splitting. Benefit:
  deterministic, debuggable, no Claude API cost.

---

## ADVERSARY attacks:

### Attack 1: Magic bytes check with python-magic is OS-dependent and fragile

`python-magic` is a wrapper around `libmagic`, which behaves differently on macOS, Linux,
and Docker. The MIME types it returns for `.docx` files vary:
- Linux libmagic: `application/vnd.openxmlformats-officedocument.wordprocessingml.document`
- Some versions: `application/zip` (because .docx IS a zip file)
- macOS: sometimes `application/octet-stream`

If the magic bytes check returns `application/zip` for a valid .docx, the file is rejected
despite being perfectly valid.

**Failure scenario:** A user uploads a valid .docx file. On the developer's Mac, it works.
In the Docker production container, libmagic returns `application/zip`. The user gets
"Invalid file type" and has no idea why. They email support. Nobody can reproduce it locally.

### Attack 2: No file size limit at the ASGI server level — memory bomb before validation

The 10MB limit is checked "at both reverse proxy and application level." But FastAPI's
`UploadFile` reads the file into memory (or a temp file for large uploads) BEFORE the
endpoint handler runs. If someone sends a 2GB file, the server has already consumed
memory/disk by the time the 10MB check runs.

**Failure scenario:** Attacker sends 100 concurrent requests with 1GB files. The server
runs out of memory before a single validation check runs. DoS achieved without any
authentication.

**Fix:** Configure uvicorn's `--limit-max-request-size` or use a middleware that streams
the body and aborts early.

### Attack 3: Path traversal in S3 key via filename

The S3 key is `manuscripts/{user_id}/{manuscript_id}/{filename}`. The `filename` comes
from the uploaded file's metadata. A crafted filename like `../../../admin/config.json`
creates an S3 key of `manuscripts/{user_id}/{manuscript_id}/../../../admin/config.json`,
which resolves to `admin/config.json` in the S3 bucket.

While S3 doesn't treat `/..` as path traversal (keys are flat strings), the resulting
key is misleading and could confuse cleanup/purge logic that uses prefix-based listing.
If we ever add a bucket policy based on key prefixes, this becomes a real access control bypass.

### Attack 4: Charset detection fallback enables content injection

For `.txt` files, ARCHITECT proposes "try UTF-8 first, fallback to chardet." The
`chardet` library guesses encoding from byte patterns. It can be wrong. If it guesses
wrong, the decoded text is corrupted — but silently. The corrupted text goes into the
database and then into Claude prompts.

Worse: an attacker can craft a file that is valid UTF-8 (passes the first check) but
contains carefully placed byte sequences that, when interpreted as the "detected"
encoding by chardet, produce different content — including potential prompt injection
text.

**Failure scenario:** File is valid UTF-8 but chardet never runs because UTF-8
succeeds. This is actually fine. The real risk is: file is NOT valid UTF-8, chardet
guesses Windows-1252, and special characters render as different text than intended,
potentially bypassing the prompt injection guard.

### Attack 5: Chapter detection regex is denial-of-service on pathological input

The regex `^Chapter\s+\d+` is fine. But `^\d+\.?\s*$` (bare number on its own line)
matches every page number in a PDF extraction. A 300-page novel produces 300 "chapters"
of ~1 paragraph each. The database fills with hundreds of chapter rows. The story
bible generation job tries to process all of them. Claude API costs spike.

**Failure scenario:** User uploads a PDF where page numbers appear as separate lines
in extracted text. Chapter detection creates 300+ chapters. The frontend shows 300 tabs.
Analysis costs $15 instead of $2. The user is charged $49 for garbage results.

---

## JUDGE decides:

**Verdict: ARCHITECT's pipeline is approved with five required changes.**

The sync-validate / async-extract split is correct. The tradeoffs are reasonable.
ADVERSARY found real issues.

### Required changes:

**1. DOCX magic bytes handling (Attack 1): VALID.**

Do not rely solely on `python-magic` for DOCX detection. Use a two-step check:
- First: check if the file extension is `.docx` AND the file is a valid ZIP archive
  (try opening with `zipfile.is_zipfile()`). If yes, check for the presence of
  `word/document.xml` inside the ZIP. This is the definitive DOCX test.
- For `.txt`: magic bytes check is fine (text files are simple).
- For `.pdf`: check first 5 bytes for `%PDF-` magic header.

Drop the dependency on `python-magic` entirely. It's not worth the cross-platform headache
for three file types. Replace with targeted checks.

**2. File size limit at server level (Attack 2): VALID.**

Add a streaming middleware or configure the ASGI server to reject bodies > 10MB before
reading them into memory. Use Starlette's `ContentSizeLimitMiddleware` (or write a
simple one) that returns 413 if `Content-Length > 10MB` AND aborts the connection if
the body exceeds 10MB without a Content-Length header.

This is defense in depth — the application-level check is still the primary gate.

**3. Filename sanitization (Attack 3): VALID but low impact.**

Sanitize the filename before constructing the S3 key. Strip path separators, limit to
alphanumeric + hyphen + underscore + dot. Or better: don't use the original filename
at all. Use `{manuscript_id}.{ext}` as the S3 key. The original filename is metadata
stored in the database, not part of the key.

**Change:** S3 key format is `manuscripts/{user_id}/{manuscript_id}/original.{ext}`.
No user-supplied filename in the key.

**4. Charset handling (Attack 4): PARTIALLY VALID.**

Drop chardet. Accept only UTF-8 for .txt files. If the file is not valid UTF-8, return
a 422 with "This file does not appear to be UTF-8 encoded. Please re-save as UTF-8."

Authors using Word save as .docx (not .txt). Authors using Google Docs export as .docx.
The only .txt users are technical users who know what UTF-8 is. The edge case of
non-UTF-8 .txt files is not worth the complexity.

**5. Chapter detection guardrails (Attack 5): VALID.**

- The bare-number regex (`^\d+\.?\s*$`) is too aggressive. Remove it.
- Add a minimum chapter length: if a detected chapter is < 200 words, merge it with
  the next chapter. This catches page numbers, part headers, and other false positives.
- Cap at 100 chapters per manuscript. If detection yields > 100, fall back to "entire
  text as Chapter 1" and flag for manual splitting.
- Log the chapter detection results for debugging.

### Green light:

Apply the five changes. Then implement.
