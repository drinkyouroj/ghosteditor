# MVP Blueprint: GhostEditor

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│                     GhostEditor Web App                     │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│   [Frontend — React]                                        │
│   ├── Chapter Upload UI (drag-drop DOCX/TXT)               │
│   ├── Story Bible Viewer (collapsible panel)               │
│   ├── Chapter Feedback Dashboard (per-chapter tabs)        │
│   └── Issue List (sorted by severity)                      │
│                                                             │
│   [Backend — Python/FastAPI]                               │
│   ├── /upload_chapter  → text extraction → queue           │
│   ├── /bible/{ms_id}   → story bible CRUD                 │
│   ├── /analyze/{ch_id} → Claude analysis job              │
│   └── /feedback/{ms_id} → paginated feedback results      │
│                                                             │
│   [Processing Layer]                                        │
│   ├── Text Extractor (pypdf2, python-docx)                 │
│   ├── Story Bible Builder (Chapter 1 → structured JSON)    │
│   ├── Cross-Chapter Analyzer (bible + chapter → issues)    │
│   └── Genre Comparator (genre conventions library)         │
│                                                             │
│   [Storage]                                                 │
│   ├── PostgreSQL: manuscripts, chapters, story bibles      │
│   ├── S3: original file storage                            │
│   └── Redis: job queue (chapter analysis tasks)            │
│                                                             │
│   [External]                                               │
│   ├── Claude API (claude-3-5-sonnet-20241022)              │
│   └── Stripe (billing)                                     │
└─────────────────────────────────────────────────────────────┘
```

**Story Bible Schema (JSON stored in PostgreSQL):**
```json
{
  "characters": [{"name": "...", "description": "...", "first_appearance": "ch1", "traits": [...], "physical": {...}}],
  "timeline": [{"event": "...", "chapter": 1, "date_in_story": "..."}],
  "world_rules": ["magic system rule 1", "technology limitation 1"],
  "voice_profile": {"pov": "third-person limited", "tense": "past", "tone": "...", "avg_sentence_length": 18},
  "settings": [{"name": "...", "description": "...", "chapter_introduced": 1}],
  "plot_threads": [{"thread": "...", "status": "open/resolved", "chapters": [1,2,3]}]
}
```

## Week-by-Week Build Plan

**Week 1: File Pipeline + Story Bible Generation**
- Set up FastAPI project structure, PostgreSQL schema, S3 bucket
- Build chapter upload endpoint (DOCX/TXT/PDF → clean text extraction)
- Design and iterate story bible generation prompt for Chapter 1
- Test story bible quality on 5 real manuscript samples (Project Gutenberg public domain novels for testing)
- Store story bible as structured JSON in PostgreSQL
- Basic React UI: upload flow, story bible display panel

**Week 2: Chapter Analysis Engine**
- Build chapter-by-chapter analysis prompt (bible JSON + chapter text → issues JSON)
- Issue schema: `{type, severity, chapter_location, description, original_text, suggestion}`
- Implement cross-chapter consistency checking (character details, timeline events, world rules)
- Pacing analysis (character presence per chapter, scene type balance)
- Genre convention comparison (load 5 genre templates: romance, thriller, fantasy, literary fiction, mystery)
- Test on 3 full manuscript chapters with known issues

**Week 3: Feedback Dashboard + Polish**
- Build React feedback dashboard: per-chapter tabs, issue list sorted by severity (CRITICAL / WARNING / NOTE)
- Story bible viewer (expandable sections for characters, timeline, world rules)
- Issue detail view: highlighted text snippet, explanation, suggested fix
- Progress indicator (chapter N of N analyzed, bible completeness score)
- Error handling for malformed files, extremely long chapters, edge cases

**Week 4: Billing, Beta Launch, Marketing**
- Stripe integration: $49 per-manuscript (one-time charge on analysis start), $79/mo subscription
- Beta pricing toggle: $29 for first manuscript (coupon code system)
- Landing page with demo video recorded in Week 3
- Email outreach to 50 self-publishing community moderators (r/selfpublishing, 20BooksTo50K Facebook group, Absolute Write forums)
- Product Hunt draft prepared for Day 30 launch

## API & Tool Stack

| Service | Purpose | Cost Estimate |
|---------|---------|---------------|
| Claude API (claude-3-5-sonnet) | Story bible generation + chapter analysis | ~$1–3 per full manuscript (100K words in 20-chapter batches) |
| PostgreSQL (Railway or Render) | Manuscript data, story bibles, feedback | $5–7/mo |
| S3 (AWS) | Original file storage | <$1/mo at 100 manuscripts |
| Redis (Upstash) | Chapter analysis job queue | Free tier adequate |
| Stripe | Billing (per-use + subscription) | 2.9% + $0.30 per transaction |
| Render/Railway | Backend hosting | $7–10/mo |
| Vercel | Frontend hosting | Free |
| **Total at $49 pricing** | | ~$7–12/mo infra + $1–3/manuscript API cost; 4–5x margin at $49 price |

**Claude API context per chapter analysis:**
- Story bible: ~8,000–12,000 tokens (grows through the manuscript)
- Chapter text: ~3,000–6,000 tokens (12–24 pages)
- Prompt + response: ~2,000 tokens overhead
- **Total per chapter: ~15,000–20,000 tokens → $0.04–0.06 per chapter at sonnet pricing**
- **50-chapter manuscript: ~$2–3 total API cost at $49 revenue → healthy unit economics**

## Monetization Implementation

**Pricing Page:**
```
Per Manuscript        Monthly Subscription
─────────────        ────────────────────
$49 per book          $79/month
Up to 100K words      Unlimited manuscripts
One-time payment      Active writer discount
No subscription       Cancel anytime
```

**Payment Flow:**
1. User creates account (email + password, no credit card)
2. User uploads Chapter 1 (free — story bible generation is free preview)
3. After viewing story bible, user is prompted to pay to analyze remaining chapters
4. Stripe Checkout (hosted) for $49 one-time or $79/mo subscription
5. Analysis resumes immediately on payment confirmation
6. Beta users: enter code BETA at checkout → $29 first manuscript

**Trial Hook:** Free story bible generation from Chapter 1 is the unconverted lead magnet. It's genuinely useful (authors love seeing their characters and timeline organized) and it demonstrates product quality before asking for payment.

## Launch Strategy

**Week 4 Targets:**
- 5 paying beta customers at $29 each = $145 in first week
- 50 community posts/comments with genuine value (not spam)
- 1 Product Hunt launch day (targeting Featured)

**Where to Post:**
1. r/selfpublishing (800K members) — post a genuine "I built this after struggling with developmental editing costs" story
2. 20BooksTo50K Facebook group (70K+ members) — direct to creators who earn from books
3. Absolute Write Water Cooler (writers forum) — traditional writing community
4. Kboards Writers' Cafe (KDP-focused self-pub community)
5. Twitter/X: target follows/replies to self-publishing YouTubers (Shadoe Healy, Heart Breathings)

**Cold Outreach Copy:**
```
Subject: Built a $49 alternative to $5K developmental editing — want early access?

Hi [Name],

I'm a developer who got frustrated watching self-published authors (including my partner) 
either skip developmental editing or pay $3-8K for it.

I built a tool that reads your manuscript chapter-by-chapter, builds a story bible tracking 
all your characters and plot threads, and flags structural issues, pacing problems, and 
continuity errors.

It's $29 for your first manuscript during beta (vs. the $3K alternative).

Would love a test drive and feedback. Link: [URL]

Happy to chat — no sales pitch, just trying to make it genuinely useful.
[Name]
```

**First 10 Customers Path:**
1. Post in r/selfpublishing on launch day
2. DM 20 active members of 20BooksTo50K who have posted about editing costs
3. Offer free analysis of their manuscript draft in exchange for honest feedback
4. Ask each converted user for one referral — self-pub authors have writing groups

## Risk Register

| Risk | Probability | Impact | Mitigation |
|------|-------------|--------|------------|
| **Context window limitations degrade quality on long novels** | Medium | High | Limit MVP to manuscripts under 80K words; implement story bible compression (summarize older chapters into condensed entries) for longer works in V1 |
| **Anti-AI sentiment in self-publishing communities generates backlash on launch** | Medium | Medium | Soft launch in smaller, more tool-pragmatic communities first (KDP-focused, earnings-focused groups); explicit "feedback tool, not writing tool" messaging; gather 5 testimonials before broad launch |
| **API cost per manuscript increases (Claude pricing changes)** | Low | Medium | Architecture supports model swapping; $49 price point leaves room for 3–5x cost increase before economics break; monitor monthly |

---

## Gap Analysis & Hardening (Added Post-Review)

### Auth & User Accounts

**What's needed:**
- Email + password registration with email verification
- Password reset flow (forgot password → email link → reset)
- JWT sessions (short-lived access token + refresh token stored in httpOnly cookie)
- Per-user manuscript isolation — all queries scoped to `user_id`
- Account deletion with manuscript data purge (required for GDPR and trust)

**Implementation:**
Use FastAPI with `python-jose` for JWT and `passlib[bcrypt]` for password hashing. No OAuth in MVP — adds complexity without adding enough value for the target persona (self-pub authors are not developer-class users who expect "Sign in with Google"). Add OAuth in v1 if drop-off at registration is measurable.

**Database additions:**
```sql
CREATE TABLE users (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  email TEXT UNIQUE NOT NULL,
  password_hash TEXT NOT NULL,
  email_verified BOOLEAN DEFAULT FALSE,
  verification_token TEXT,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  stripe_customer_id TEXT,
  subscription_status TEXT DEFAULT 'free' -- free | per_use | subscribed
);

CREATE TABLE manuscripts (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID REFERENCES users(id) ON DELETE CASCADE,
  title TEXT NOT NULL,
  word_count_est INTEGER,
  status TEXT DEFAULT 'uploading', -- uploading | processing | complete | error
  payment_status TEXT DEFAULT 'unpaid', -- unpaid | paid
  created_at TIMESTAMPTZ DEFAULT NOW()
);
```

**Build time:** 2 days. Do this on Day 1 of Week 1 before touching anything else — auth touches every subsequent endpoint.

---

### Async Job Status & File Processing Limits

**The problem:** Analysis of a 20-chapter manuscript takes 5–15 minutes. Users will close the tab. There's no feedback on upload progress for large files. The current plan has no frontend polling or websocket strategy.

**File limits:**
- Max file size: 10MB (covers virtually all prose manuscripts; reject with clear error message)
- Max word count: 120,000 words (equivalent to a long fantasy novel; anything larger triggers a "manuscript too long" warning with an offer to process in halves)
- Accepted formats: .docx, .txt, .pdf (warn that PDF extraction quality varies — scanned PDFs will produce garbage)
- Chapter detection: auto-detect on "Chapter N" / "Chapter [Word]" headers; fallback to manual chapter splitting UI if auto-detect fails

**Async architecture:**
```
Upload → /upload_chapter endpoint validates file → saves to S3 → creates job in Redis queue
                                                                          ↓
                                                              Worker process pulls job
                                                              → extracts text
                                                              → calls Claude
                                                              → writes results to PostgreSQL
                                                              → updates job status
Frontend polls GET /job_status/{job_id} every 5s → renders progress bar
```

**Frontend progress states:**
```
● Uploading file...          (immediate)
● Extracting text...         (2-5 seconds)
● Building story bible...    (15-45 seconds)
● Analyzing chapter 1 of N... (per chapter, ~30-60s each)
● Analysis complete ✓
```

Use Server-Sent Events (SSE) or simple polling (polling is simpler, use it for MVP). The job status endpoint returns `{status, progress_pct, current_step, error_message}`.

**Build time:** 1.5 days. Add to Week 1 alongside the upload pipeline.

---

### Error States & Degraded Output Handling

**File extraction failures:**
- PDF with images only (scanned book) → return HTTP 422 with message: "This PDF appears to be a scanned image. Please export your manuscript as DOCX from your word processor."
- DOCX with tracked changes → strip changes, process clean text, warn user: "Tracked changes were detected and removed before analysis."
- Corrupt file → HTTP 400: "This file could not be read. Try re-saving as .docx from Microsoft Word or Google Docs."

**Claude API failures:**
- Malformed JSON response (Claude occasionally produces invalid JSON despite prompting) → retry once with explicit JSON repair instruction; if second attempt fails, mark chapter as `analysis_error` and surface to user with "Analysis failed for this chapter — click to retry"
- Rate limit from Anthropic → exponential backoff in worker (2s, 4s, 8s); job stays in queue
- Response truncated (hit output token limit on very long chapters) → detect by checking if JSON is complete; if not, split chapter in half and re-analyze

**Prompt guard for bad input:**
- Empty chapters, chapters with only dialogue and no prose, chapters under 500 words → flag as "Chapter too short for meaningful analysis" rather than producing thin feedback
- Non-English manuscripts → detect language in text extraction step; return "GhostEditor currently supports English-language manuscripts only" rather than producing confidently wrong analysis

**Build time:** 1 day distributed across Weeks 1–3 as each error surface is built.

---

### Legal, IP & Trust

**This is the #1 objection from self-publishing authors before they hand over an unpublished manuscript. Address it before launch, not after.**

**Required on landing page (above the fold or in a trust bar):**
> "Your manuscript is never used to train AI models. Files are encrypted at rest. You can delete your manuscript and all associated data at any time from your account settings."

**Terms of Service must explicitly state:**
1. User retains full copyright of uploaded manuscripts
2. GhostEditor does not use uploaded content to train, fine-tune, or improve any AI model
3. Files are deleted from S3 within 30 days of account deletion request
4. Analysis results (story bibles, feedback) are deleted with the account

**Implementation:**
- Add "Delete my manuscript and all data" button in manuscript settings — cascade deletes S3 file, PostgreSQL rows, and Redis jobs
- Add ToS acceptance checkbox at registration (not just a link — checkbox creates a timestamped consent record)
- Privacy policy page (use a generator like Termly for MVP; $20/mo or one-time — worth it)
- Add Anthropic's data processing terms to your own terms: Claude API by default does not train on API inputs, but you should explicitly confirm this and surface it to users

**Build time:** 0.5 days for the delete flow; 1 day for legal pages and ToS consent. Do this in Week 4 before public launch.

---

### Prompt Quality & Eval Harness

**The problem:** Prompt engineering for structured JSON extraction across wildly different genres, writing styles, and manuscript qualities is not a one-time task. A prompt that works on a tight thriller will hallucinate character names on a sprawling fantasy with 40 characters.

**Minimum eval harness for MVP:**
Create a test suite of 5 manuscript samples covering:
1. Contemporary romance (simple cast, linear timeline)
2. Epic fantasy (large cast, non-linear timeline, invented world rules)
3. Literary fiction (unreliable narrator, ambiguous timeline)
4. Thriller (multiple POV, fast pacing, chapter-level cliffhangers)
5. Mystery (information deliberately withheld from reader)

For each sample, manually create a "ground truth" story bible and a set of known issues. Run prompts against samples and score:
- Character extraction accuracy (% of named characters correctly identified)
- Timeline event accuracy (% of events correctly ordered)
- Issue detection recall (% of known issues flagged)
- False positive rate (% of flagged issues that are not real problems)
- JSON validity rate (% of responses that parse without repair)

**Target before launch:** >85% JSON validity, >70% issue detection recall, <20% false positive rate on the test suite.

**Tooling:** A simple Python script that runs each test chapter through the production prompt, parses output, and compares against ground truth JSON. No fancy framework needed — just `pytest` and `json.loads()`.

**Ongoing:** After launch, add real user manuscripts to the test suite when users report bad feedback (with permission). This is how the prompt improves over time without retraining anything.

**Build time:** 2 days in Week 2. This is not optional — shipping without it means the first bad review comes from a paying customer instead of a test run.

---

### Email Capture Before Paywall

**The current flow loses every user who uploads Chapter 1, sees the story bible, and doesn't immediately pay.** These are warm leads — they've already seen the product work.

**Fix:** Insert an email gate between the free story bible view and the paywall.

**Revised payment flow:**
1. User visits landing page → clicks "Try free"
2. **Email capture form** (no password yet): "Enter your email to get started — no credit card required"
3. Email verified → user lands in a lightweight session
4. Upload Chapter 1 → story bible generated → story bible displayed
5. **Email delivered:** "Your GhostEditor story bible is ready — [link to view]" (this is the retention hook if they close the tab)
6. To analyze remaining chapters → full account creation (password) + payment
7. On abandon (no payment within 48 hours): **automated follow-up email sequence:**
   - Hour 2: "Here's what GhostEditor found in your first chapter" (tease one issue from the story bible)
   - Day 2: "Your story bible is waiting — 3 things developmental editors check that GhostEditor catches automatically"
   - Day 5: Final: "Your beta discount expires soon — $29 for your full manuscript"

**Email tooling:** Use Resend (free tier: 3,000 emails/mo, $20/mo after) or Postmark. Do not use Mailchimp for transactional email. Store email sequences as simple scheduled tasks in PostgreSQL (created_at + send_offset); a cron job checks every hour and dispatches.

**Build time:** 1 day for email capture flow + 0.5 days for the 3-email drip sequence. Add in Week 4.

---

### Revised Week-by-Week Build Plan (Updated)

**Week 1: Foundation**
- Day 1–2: Auth system (registration, login, JWT, email verification, password reset)
- Day 2–3: File pipeline (upload endpoint, S3, text extraction, file validation + limits)
- Day 4–5: Story bible generation prompt + async job queue + frontend polling
- Day 5: Test on 5 Project Gutenberg samples; fix extraction edge cases

**Week 2: Analysis Engine + Eval Harness**
- Day 1–2: Chapter analysis prompt (issues JSON) + cross-chapter consistency check
- Day 2: Build eval harness with 5 genre test samples + ground truth JSON
- Day 3: Run eval, fix prompt failures until hitting >85% JSON validity
- Day 4–5: Pacing analysis, genre convention templates, error state handling for malformed Claude responses

**Week 3: Frontend + Polish**
- Day 1–2: Feedback dashboard (per-chapter tabs, severity sort, issue detail view)
- Day 2–3: Story bible viewer (character cards, timeline view, world rules panel)
- Day 4: Progress indicators, error state UI, manuscript deletion flow
- Day 5: Legal pages (ToS, Privacy Policy), trust copy on landing page

**Week 4: Monetization + Email + Launch**
- Day 1: Stripe integration (per-manuscript + subscription, beta coupon)
- Day 2: Email capture flow + 3-email drip sequence (Resend)
- Day 3: Landing page, demo video, Product Hunt draft
- Day 4–5: Soft launch (KDP forums, Kboards), DM outreach to 20BooksTo50K members

**Revised infra cost:**
| Addition | Cost |
|----------|------|
| Resend (email) | Free tier → $20/mo |
| Termly (legal) | ~$20/mo or one-time |
| **Revised total** | ~$30–55/mo infra at launch |

Unit economics still hold: $49 revenue vs. $1–3 API cost + $1–2 infra allocation per manuscript.
