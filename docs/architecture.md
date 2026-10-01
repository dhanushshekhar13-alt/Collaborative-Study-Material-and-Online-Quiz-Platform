# Initial architecture and data model

## Design

The first delivery uses a modular monolith. Each module owns its domain model and
will own its API routes and services as implementation proceeds. `app/core` contains
only cross-cutting configuration and persistence. Modules should call one another
through service interfaces rather than importing implementation details.

| Module | Requirements | Initial data entities |
| --- | --- | --- |
| accounts | FR-01, NFR-02 | User, Role |
| channels | FR-02, FR-03, FR-10 | Channel, ChannelMembership |
| materials | FR-04 | StudyMaterial, MaterialRating |
| question_bank | FR-05, FR-07 | Question |
| quizzes | FR-05, FR-06 | Quiz, QuizQuestion, QuizAttempt |
| future modules | FR-08–FR-14 | Chat, AI, analytics, moderation, reputation, notifications |

The initial migration creates the entities needed by the first product increments.
It is a baseline, not the complete FR-01–FR-14 schema. Future stories add their
entities through incremental, reversible Alembic migrations.

## Decisions and boundaries

- The supplied SRS does not select a language, database, hosting provider, or target
  response-time threshold. Python/FastAPI/PostgreSQL are implementation choices for
  this increment; hosting and performance thresholds remain open decisions.
- Passwords are represented only as `password_hash`; authentication and hashing
  behavior arrive with the accounts story. No password value is stored in the model.
- Authorized-channel creation is not enforced by this schema. The accounts/channels
  stories must enforce it at the application authorization boundary.
- `Question.options` is a temporary text representation for this foundation. The
  question-bank API story should replace it with validated structured JSON before
  question authoring is exposed.
- Material `source_url` records a reference. A secure file-storage provider and its
  upload limits must be selected before direct file upload is implemented.

## Local schema changes

```sh
alembic revision --autogenerate -m "describe change"
alembic upgrade head
alembic downgrade -1
```

Review every generated migration before committing it. Migrations must preserve data
and have a downgrade path when rollback is meaningful.
