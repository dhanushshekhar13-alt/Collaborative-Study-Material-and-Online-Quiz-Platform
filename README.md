# Collaborative Study Material and Online Quiz Platform

This repository implements the requirements in `Documents/` against the revised
three-sprint delivery deadline of 30 October 2026.
The first increment establishes a modular API and a PostgreSQL-backed data model.

## Delivery plan

The proposed delivery plan uses three one-week iterations. Mirror these dates in the GitHub Project settings before Sprint 1 begins. Each sprint ends with a reviewable
pull request and a board update; issues are closed only when their acceptance criteria
are met and the change is merged.

| Sprint | Dates | Planned focus |
| --- | --- | --- |
| 1 | 12–16 Oct | Foundation and core access MVP: repository/data model, account access, channel discovery and membership |
| 2 | 19–23 Oct | Shared study-material MVP: publishing, discovery, ratings and reports |
| 3 | 26–30 Oct | Quiz MVP: question bank, authoring, attempts, scoring, acceptance review and freeze |

Backlog refinement and story-point estimation are due by 12 Oct. The submitted
deliverables schedule calls 12–16 Oct an optional dry-run week, then specifies Sprint 1
for 19–23 Oct and Sprint 2 for 26–30 Oct. To retain three sprints, this plan uses the
optional week as a real Sprint 1; document this schedule deviation in the final demo.
This is a timeboxed MVP plan, not a claim that every backlog issue will be completed.
Issues that do not fit remain prioritized follow-on work after the 30 Oct freeze.

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
- `POST /auth/register` — create a student account with a validated email and scrypt-hashed
  password. Success returns a `Registration successful` message and the new profile;
  invalid email returns `Enter a valid email address`.
- `POST /auth/token` — exchange a username/email and password for a signed bearer token.
- `GET /auth/me` — return the authenticated account profile; requires a valid bearer token.
- `PATCH /auth/me` — update the authenticated user's display name and/or bio; send `bio: null` to clear the bio.
- `POST /channels` — create a Normal channel and automatically join its creator. Creating
  an Authorized channel requires an `authorized_user` or `admin` role.
- `GET /channels?query=...` — search channel names and subjects; requires authentication
  and returns the channel type for each result.
- `GET /channels/{channel_id}` — return channel details to joined members, the creator,
  moderators, or admins; a user who leaves or is removed receives `403`.
- `GET /channels/mine` — list channels the authenticated user has joined.
- `POST /channels/{channel_id}/memberships` — join a channel; repeating the request is safe.
- `DELETE /channels/{channel_id}/memberships/me` — leave a channel and remove the user's
  membership record.
- `GET /channels/{channel_id}/members` — list members for a joined user, creator, moderator
  or admin.
- `DELETE /channels/{channel_id}/members/{member_id}` — creator, moderator or admin removes
  a member; clients should ask for confirmation before sending this request.
- `GET /docs` — interactive API documentation.

Role checks are provided by the accounts module for protected feature routers. Newly
registered accounts receive the seeded `student` role; clients cannot choose a role
at registration. Apply database migrations before registering users.

Functional endpoints will be added in the corresponding modules as their stories are
implemented. See `docs/architecture.md` for current boundaries and schema scope.
