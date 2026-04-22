# ApplyForge-BE

Production-oriented backend for an AI-powered Job Application Assistant.

## Phase 1 — System understanding
- Multi-user backend owning auth, profile, resume ingestion, RAG, JD analysis, generation, chat, application tracking, and document export.
- Strict ownership checks by `user_id` in every domain table/query.
- Truthful generation rule enforced by grounding prompts in stored profile evidence and retrieved chunks only.
- Out of scope: frontend UI/UX, browser rendering, client-side state.

## Phase 2–4 — Architecture summary
- FastAPI monolith with modular service boundaries under `app/services/*`.
- SQLAlchemy 2 models + Alembic migrations.
- Provider abstraction (`LLMProvider`, `EmbeddingProvider`) with mock implementation for local/dev.
- RAG indexing via `knowledge_documents` + `knowledge_chunks` and JSON embeddings.
- JWT access/refresh auth with refresh token persistence + revocation.
- Background-ready design via separable service methods (can be moved to Celery workers).

## Directory
- `backend/app/api/v1/routes`: REST endpoints grouped by domain.
- `backend/app/models`: ORM and enums.
- `backend/app/services`: business logic and AI/RAG orchestration.
- `backend/alembic`: migrations.
- `backend/tests`: pytest suites.

## Run locally
```bash
cd backend
cp .env.example .env
docker compose up --build
```

API docs: `http://localhost:8000/docs`.

## Dokploy notes
- Deploy `backend` as the application service using the provided Dockerfile.
- Add a managed MySQL 8 service or deploy the included compose topology.
- Set env vars from `.env.example`; always replace `SECRET_KEY`.
- Ensure a persistent volume is mounted at `/app/storage` for uploaded files.

## Tests
```bash
cd backend
pip install -e .[test]
pytest
```

## Phase 9 — Critical review highlights
- Current parsing is heuristic and should be replaced with robust extraction+NER pipeline.
- MySQL JSON/text embedding storage is simple; replace with vector DB for scale.
- Mock LLM/embedding providers are placeholders for production provider adapters.
- Add rate limiting, audit logging, S3/object storage, and async worker queue for heavy tasks.

## Frontend API contract
- See `backend/API_CONTRACT.md` for a full endpoint-by-endpoint request/response reference.
