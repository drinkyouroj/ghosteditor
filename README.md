# GhostEditor

AI-powered developmental editing for self-published authors. Upload a manuscript — fiction or nonfiction — and get structured, chapter-by-chapter feedback on consistency, argument quality, pacing, and genre or format conventions.

## How It Works

### Fiction

1. **Upload** a manuscript (DOCX, PDF, or TXT) and select your genre
2. **Story Bible** is generated automatically from Chapter 1 — characters, timeline, settings, world rules, voice profile, plot threads
3. **Chapter Analysis** runs sequentially, updating the bible and flagging issues: continuity errors, pacing problems, genre convention violations
4. **Feedback Dashboard** shows results organized by chapter and severity (Critical / Warning / Note)

### Nonfiction

1. **Upload** a manuscript and select a nonfiction format (Academic, Personal Essay, Journalism, Self-Help, or Business)
2. **Argument Map** is generated — central thesis, argument threads, evidence log, voice profile, structural markers
3. **Section Analysis** evaluates each section across five dimensions: argument structure, evidence quality, clarity, organization, and tone — with format-specific conventions applied
4. **Document Synthesis** produces an overall assessment with scores for thesis clarity, argument coherence, evidence density, and tone consistency, plus prioritized recommendations
5. **Feedback Dashboard** shows per-section results with dimension filtering and a document summary panel

## Architecture

```
React (Vite)  →  FastAPI  →  Claude API (Sonnet + Haiku)
                    ↕
              PostgreSQL + Redis (arq) + S3/MinIO
```

- **Story Bible / Argument Map generation**: Claude — builds a structured JSON representation of the manuscript's core elements
- **Chapter / Section analysis**: Claude — flags issues against the bible or argument map with severity, location, and suggestions
- **Document synthesis** (nonfiction): Claude — produces a document-level assessment from structured analysis data
- **Job queue**: arq (Redis) — sequential processing with retry, stall recovery, and stuck-manuscript auto-recovery
- **Storage**: PostgreSQL (manuscripts, bibles, argument maps, analysis results), S3/MinIO (original files)

## Quick Start

### Prerequisites

- Python 3.11+
- Node.js 18+
- Docker

### Setup

```bash
# Start infrastructure (uses project-isolated ports)
docker-compose -f infra/docker-compose.yml up -d

# Backend
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp ../.env.example ../.env  # then edit with your keys
PYTHONPATH=. alembic upgrade head
PYTHONPATH=. uvicorn app.main:app --reload

# Worker (separate terminal)
cd backend
PYTHONPATH=. .venv/bin/arq app.jobs.worker.WorkerSettings

# Frontend (separate terminal)
cd frontend
npm install
npm run dev
```

### Port Assignments

| Service    | Port |
|------------|------|
| PostgreSQL | 5433 |
| Redis      | 6380 |
| MinIO API  | 9002 |
| MinIO Console | 9003 |
| Backend API | 8000 |
| Frontend   | 5173 |

## Project Structure

```
├── backend/
│   ├── app/
│   │   ├── analysis/        # Claude API, prompts, JSON repair, export
│   │   │   ├── prompts/     # Versioned prompt files (bible, analysis, splitting, nonfiction)
│   │   │   ├── story_bible.py / argument_map.py   # Fiction / nonfiction generation
│   │   │   ├── chapter_analyzer.py / nonfiction_analyzer.py
│   │   │   └── nonfiction_synthesis.py
│   │   ├── auth/            # JWT auth, registration, password reset
│   │   ├── manuscripts/     # Upload, extraction, chapter/section detection
│   │   ├── jobs/            # arq worker, stall recovery, GDPR purge cron
│   │   ├── email/           # Resend integration, drip sequences
│   │   ├── stripe/          # Checkout, webhooks, subscriptions
│   │   └── db/              # SQLAlchemy models, Alembic migrations
│   └── tests/
│       ├── eval/            # Prompt eval harness (5 fiction + 5 nonfiction samples)
│       └── unit/            # 138+ unit tests
├── frontend/src/
│   ├── pages/               # Dashboard, manuscript, feedback, bible, argument map views
│   ├── components/          # Shared UI components
│   ├── api/                 # Typed API client
│   └── __tests__/           # Vitest smoke tests
├── infra/
│   └── docker-compose.yml
└── docs/
    ├── blueprint.md         # Full MVP specification
    ├── decisions/           # Adversarial protocol decision records (DECISION-001 through 011)
    ├── review/              # Codebase audit report
    └── build_log.md         # Development progress
```

## Development Process

All significant design decisions go through a three-agent adversarial review (Architect / Adversary / Judge) documented in `docs/decisions/`. See `CLAUDE.md` for the full protocol.

## Tests

```bash
# Backend unit tests (138+)
cd backend
PYTHONPATH=. .venv/bin/pytest tests/unit/ -v

# Backend eval harness (requires ANTHROPIC_API_KEY — costs real API money)
PYTHONPATH=. .venv/bin/pytest tests/eval/ -v -m api

# Frontend smoke tests
cd frontend
npm test
```

## License

Proprietary. All rights reserved.
