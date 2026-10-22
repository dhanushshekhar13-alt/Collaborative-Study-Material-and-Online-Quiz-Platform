# Collaborative Study Material and Online Quiz Platform

Backend API for the platform specified in `Documents/` (SRS, use cases, test plan and
security requirements). Python 3.12, FastAPI, SQLAlchemy 2, Alembic and PostgreSQL
(SQLite works for local development).

## Delivery plan

Two one-week sprints, tracked on the GitHub Project board:

| Sprint | Dates | Focus |
| --- | --- | --- |
| 1 | 19–23 Oct | Foundation, accounts, channels, materials, question bank, quizzes, LLM generation |
| 2 | 26–30 Oct | Chat, analytics, moderation, admin, notifications, web UI, hardening (NFRs) |

Every story is delivered as one pull request that references its issue
(`Closes #N`). `main` is protected: a pull request can only merge after CI passes.

## Local setup

```sh
git clone https://github.com/dhanushshekhar13-alt/Collaborative-Study-Material-and-Online-Quiz-Platform.git
cd Collaborative-Study-Material-and-Online-Quiz-Platform
python -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[dev]'
cp .env.example .env
alembic upgrade head
python -m app.seed
uvicorn app.main:app --reload
```

The web UI is then at http://127.0.0.1:8000/app/ (see `app/web/README.md`), the API
at http://127.0.0.1:8000/api/v1 and interactive docs at http://127.0.0.1:8000/docs. `python -m app.seed` loads one test user per role plus sample
content; set `SEED_USER_PASSWORD` first or note the password it prints. Run the checks
CI runs with `ruff check . && pytest`.

## Configuration

All settings come from environment variables or a git-ignored `.env` file (see
`.env.example`). No secret has a default and none is committed:

| Variable | Purpose |
| --- | --- |
| `DATABASE_URL` | SQLAlchemy URL; SQLite locally, PostgreSQL in deployment |
| `AUTH_TOKEN_SECRET` | Signing secret for access tokens (at least 32 bytes) |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | Access token lifetime, default 30 |
| `LLM_PROVIDER`, `LLM_MODEL`, `LLM_API_KEY` | LLM integration: `openai` (any OpenAI-compatible API), `anthropic`, `fake` (offline deterministic generator for demos) or `none` (disabled; descriptive answers then use deterministic concept matching) |
| `LLM_BASE_URL`, `LLM_TIMEOUT_SECONDS` | Optional endpoint override and provider timeout in seconds (default 30) |
| `CORS_ORIGINS` | Comma-separated browser origins allowed to call the API |
| `MATERIAL_MAX_UPLOAD_MB` | Largest study material upload accepted, in MB (default 20) |
| `QUIZ_UNANSWERED_RULE` | Submitting a quiz with unanswered questions: `warn` (confirm first, default) or `block` |
| `SEED_USER_PASSWORD` | Optional password for the seeded test users (local only) |

## Deployment

On any host with Docker and a clone of this repository, create `.env` with
`POSTGRES_PASSWORD` and `AUTH_TOKEN_SECRET` (plus the LLM variables if used), then run:

```sh
./scripts/deploy.sh
```

The script pulls the latest `main`, rebuilds the image, applies migrations on start-up
and restarts the API and PostgreSQL containers (`docker-compose.yml`). The API listens
on port 8000.

## Continuous integration

`.github/workflows/ci.yml` runs on every pull request and on pushes to `main`:
`ruff check`, `ruff format --check` and the `pytest` suite, with migration tests run
against a PostgreSQL 16 service as well as SQLite. The `lint-and-test` check
is required before anything merges into `main`.

## Project layout

```text
app/
  core/        configuration and database session
  modules/     one package per product area (models, service interface, routes)
  main.py      application factory and router registration
alembic/       database migrations
tests/         unit and API tests
Documents/     SRS, use cases, test plan, security requirements
docs/          architecture.md, api/openapi-v1.json (API contract), test execution log
scripts/       deploy.sh, export_openapi.py
```
