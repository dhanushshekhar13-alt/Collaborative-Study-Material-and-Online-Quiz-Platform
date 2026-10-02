# Collaborative Study Material and Online Quiz Platform

This repository is the six-week implementation of the requirements in `Documents/`.
The first increment establishes a modular API and a PostgreSQL-backed data model.

## Delivery plan

The GitHub Project tracks the six-week delivery in three two-week iterations. Each
week ends with a reviewable pull request and a board update; issues are closed only
when their acceptance criteria are met and the change is merged.

| Week | Iteration | Planned focus |
| --- | --- | --- |
| 1 | Sprint 1 | Repository foundation, API boundaries, initial data model, CI |
| 2 | Sprint 1 | Accounts, authentication, profiles, channels and memberships |
| 3 | Sprint 2 | Study material sharing, discovery, ratings and reports |
| 4 | Sprint 2 | Question bank, quiz authoring, attempts and deterministic scoring |
| 5 | Sprint 3 | LLM-assisted learning, chat, analytics and recommendations |
| 6 | Sprint 3 | Moderation, notifications, hardening and acceptance review |

This is a plan, not a claim that work is complete. Sprint and issue status should be
updated as work is actually reviewed and merged. The remaining backlog stays visible
for prioritization after this six-week delivery.

## Stack and architecture

- Python 3.12 or newer, FastAPI, SQLAlchemy 2, Alembic and PostgreSQL.
- A modular monolith: each product area owns its API and domain code under
  `app/modules/`; shared configuration, persistence and cross-cutting concerns live
  under `app/core/`.
- REST API contract is generated from FastAPI's OpenAPI support at `/docs` and
  `/openapi.json`.
- SQLite is supported for local smoke use; PostgreSQL is the deployment database.

The supplied documents do not prescribe an implementation language or deployment
provider. This stack is an initial implementation choice and can be revised through
review before domain features depend on it. No production deployment target or
credentials were supplied, so this increment does not deploy the service.

## Local development

```sh
python -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
cp .env.example .env
uvicorn app.main:app --reload
```

Set `DATABASE_URL` in `.env` to a PostgreSQL connection string for local development.
The default uses a local SQLite file. Apply schema changes with `alembic upgrade head`.
Set `AUTH_TOKEN_SECRET` to a unique random value of at least 32 bytes before using
login; `.env.example` includes a command for generating one. Access tokens expire
after `ACCESS_TOKEN_EXPIRE_MINUTES` (30 by default).

## Current API

- `GET /health` — process health check.
- `POST /auth/register` — create a student account with a validated email and scrypt-hashed password.
- `POST /auth/token` — exchange a username/email and password for a signed bearer token.
- `GET /auth/me` — return the authenticated account profile; requires a valid bearer token.
- `GET /docs` — interactive API documentation.

Role checks are provided by the accounts module for protected feature routers. Newly
registered accounts receive the seeded `student` role; clients cannot choose a role
at registration. Apply database migrations before registering users.

Functional endpoints will be added in the corresponding modules as their stories are
implemented. See `docs/architecture.md` for current boundaries and schema scope.
