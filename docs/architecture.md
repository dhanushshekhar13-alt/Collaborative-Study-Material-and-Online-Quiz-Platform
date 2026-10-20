# Architecture

The API is a modular monolith: one FastAPI process and one database, split into
feature modules under `app/modules/`. Each module can change on its own as long as its
public service interface keeps its promises (NFR-07).

## Module rules

1. A module **owns its tables**. Its `models.py` defines them and only that module
   reads or writes them.
2. A module **exposes one public interface**, `service.py`. The functions there return
   plain dataclasses (for example `QuestionView`, `AttemptResult`), never ORM objects,
   so callers cannot reach into another module's data.
3. A module may import `app.core`, its own package, and the `service` of the modules
   listed for it in `app/modules/__init__.py` (`ALLOWED_DEPENDENCIES`). Nothing else.
4. Tables may reference other modules' tables through foreign keys; that needs no
   Python import and keeps referential integrity in the database.

`tests/test_module_boundaries.py` fails the build if any of these rules is broken.

## Modules

| Module | Requirements | Owns tables | May call |
| --- | --- | --- | --- |
| accounts | FR-01 | users, profile_pictures | – |
| channels | FR-02, FR-03 | channels, channel_memberships | accounts |
| materials | FR-04 | study_materials, material_files, material_reviews | accounts, channels |
| questions | FR-05, FR-07 | questions, question_ratings | accounts, channels, moderation |
| quizzes | FR-05, FR-06, FR-07 | quizzes, quiz_questions, quiz_attempts, attempt_answers | questions |
| llm | FR-07, FR-08, FR-09 | llm_requests | questions, quizzes |
| chat | FR-10, FR-11 | channel_messages, conversations, conversation_participants, direct_messages, user_blocks | – |
| analytics | FR-12 | none (derived from quiz attempts) | quizzes |
| moderation | FR-13, FR-14 | reports, moderation_actions | – |
| reputation | FR-01, FR-13 | reputation_events, badges, user_badges | accounts |
| notifications | FR-14 | notifications, notification_preferences | – |
| profiles | FR-01 | none (assembled from other modules' services) | accounts, materials, questions, quizzes, reputation |

Routes that need a signed-in user or a permission use the guards exported by
`app.modules.accounts.service` (`current_user`, `require_capability`,
`require_roles`); see `app/modules/accounts/README.md`. A module adding its first
guarded route adds `accounts` to its `ALLOWED_DEPENDENCIES` entry. That keeps the
dependency graph honest: chat's guard on accounts gives quizzes no path to chat.

Quiz scoring lives in `app/modules/quizzes/scoring.py` as pure functions. No module
that quizzes depends on can reach chat, so a change to chat can never require a change
to scoring (checked by `test_quiz_scoring_does_not_depend_on_chat` and
`test_chat_activity_does_not_change_quiz_scores`).

Cross-cutting references that point at "any content" (a shared resource in a chat
message, the target of a report) are stored as a type plus an id rather than a
foreign key, so the owning modules stay independent.

## Data model

```mermaid
erDiagram
  users ||--o{ channel_memberships : joins
  channels ||--o{ channel_memberships : has
  users ||--o{ channels : creates
  channels ||--o{ study_materials : contains
  study_materials ||--o{ material_reviews : rated_by
  study_materials ||--o| material_files : stores
  channels ||--o{ questions : contains
  questions ||--o{ question_ratings : rated_by
  channels ||--o{ quizzes : contains
  quizzes ||--o{ quiz_questions : lists
  questions ||--o{ quiz_questions : used_in
  quizzes ||--o{ quiz_attempts : attempted
  quiz_attempts ||--o{ attempt_answers : records
  users ||--o{ llm_requests : makes
  channels ||--o{ channel_messages : carries
  channel_messages ||--o{ channel_messages : replies
  conversations ||--o{ conversation_participants : between
  conversations ||--o{ direct_messages : carries
  users ||--o{ user_blocks : blocks
  users ||--o{ reports : files
  reports ||--o{ moderation_actions : leads_to
  users ||--o| profile_pictures : shows
  users ||--o{ reputation_events : earns
  badges ||--o{ user_badges : awarded
  users ||--o{ notifications : receives
  users ||--o{ notification_preferences : sets
```

## Migrations and seed data

Schema changes are Alembic migrations in `alembic/versions/`. Every migration must
upgrade and downgrade cleanly on an empty database and leave the schema identical to
the models; `tests/test_migrations.py` checks this on SQLite and, in CI, PostgreSQL.

`python -m app.seed` loads one user per role (student, authorized user, admin), a
peer channel, an approved authorized channel, materials, questions, a quiz with one
scored attempt, chat messages and a reputation event. The users' password comes from
`SEED_USER_PASSWORD`, or is generated and printed once.

## API contract

The API is versioned under `/api/v1`. The OpenAPI document for v1 is committed at
`docs/api/openapi-v1.json`; regenerate it with `python scripts/export_openapi.py`
after changing any route. CI fails when the committed contract and the code differ,
so every API change is visible in review. A breaking change gets a new version prefix
and a new contract file rather than editing v1 in place.
