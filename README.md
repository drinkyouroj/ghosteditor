# GhostEditor

AI-powered developmental editing for self-published authors. Upload a manuscript, get a structured Story Bible and chapter-by-chapter feedback on consistency, pacing, and genre conventions.

## How It Works

1. **Upload** a manuscript (DOCX, PDF, or TXT)
2. **Story Bible** is generated automatically from Chapter 1 — characters, timeline, settings, world rules, voice profile, plot threads
3. **Chapter Analysis** runs sequentially, updating the bible and flagging issues against it: continuity errors, pacing problems, genre convention violations
4. **Feedback Dashboard** shows results organized by chapter and severity (Critical / Warning / Note)

## Architecture

```
React (Vite)  →  FastAPI  →  Claude API (Sonnet + Haiku)
                    ↕
              PostgreSQL + Redis (arq) + S3/MinIO
```

- **Story Bible generation**: Claude Sonnet — builds and incrementally updates a structured JSON bible per chapter
- **Chapter analysis**: Claude Haiku — flags issues against the bible with severity, location, and suggestions
- **Job queue**: arq (Redis) — sequential chapter processing with retry and stall recovery
- **Storage**: PostgreSQL (manuscripts, bibles, analysis results), S3/MinIO (original files)

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
│   │   ├── analysis/        # Claude API integration, prompts, JSON repair
│   │   ├── auth/            # JWT auth, registration, password reset
│   │   ├── manuscripts/     # Upload, extraction, chapter detection
│   │   ├── jobs/            # arq worker, stall recovery
│   │   └── db/              # SQLAlchemy models, Alembic migrations
│   └── tests/
│       ├── eval/            # Prompt eval harness (Gutenberg samples)
│       └── unit/
├── frontend/src/
│   ├── pages/               # Dashboard, manuscript, feedback, bible views
│   ├── components/          # Shared UI components
│   └── api/                 # API client
├── infra/
│   └── docker-compose.yml
└── docs/
    ├── blueprint.md         # Full MVP specification
    ├── decisions/           # Adversarial protocol decision records
    ├── build_log.md         # Development progress
    └── eval_log.md          # Prompt evaluation results
```

## Development Process

All significant design decisions go through a three-agent adversarial review (Architect / Adversary / Judge) documented in `docs/decisions/`. See `CLAUDE.md` for the full protocol.

## Tests

```bash
cd backend

# Unit tests
PYTHONPATH=. .venv/bin/pytest tests/unit/ -v

# Eval harness (requires ANTHROPIC_API_KEY)
PYTHONPATH=. .venv/bin/pytest tests/eval/ -v
```

## License

Proprietary. All rights reserved.
