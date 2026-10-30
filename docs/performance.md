# Performance (NFR-01)

How fast the platform must answer, how that is measured, and what the measurements
showed. Issue #46.

## Targets

Written down before testing, from the SRS (NFR-01). Times are measured at the
**95th percentile** under **normal load: up to 50 concurrent users**.

| Operation | p95 target | What is timed |
| --- | --- | --- |
| page load | 2.0 s | every API call the page makes before it can render |
| search/filter | 2.0 s | one material, question or channel search or filter request |
| quiz submission | 3.0 s | submit with objective scoring, then load the result page |

Operations that wait on the external LLM (question generation, descriptive-answer
evaluation, Ask AI) are excluded, as the SRS says.

The numbers live in code in `scripts/load_test.py` (`TARGETS`, `PERCENTILE`,
`CONCURRENT_USERS`); `tests/test_performance.py` checks that this table and the code
agree.

## How it is measured

`scripts/load_test.py` seeds a class-sized dataset — 50 students, 8 channels,
400 materials, 400 questions, 24 published quizzes, 150 past attempts, 400 channel
messages — then runs 50 virtual students at once. Each one works through every
common operation in its own random order, with a pause of 0.5–1.5 s between
operations (far busier than a person reading a page), for a number of rounds.

The operations mirror the web UI (`app/web/static`): a page load is the calls that
page makes, with calls the UI fires together (`Promise.all`) fired together here. The
pages covered are Dashboard, My performance, Recommended for you, Profile, Channel
directory, Channel page, Materials, Material detail, Question bank, Quizzes, Quiz
history, Leaderboard, Notifications and Start a quiz. Searches cover material search,
material filter, question search and channel search. A quiz submission starts an
attempt (not timed), then times submit + result page.

Requests go through the real ASGI app with real per-request database sessions and the
same 40-thread pool uvicorn uses, so they queue for threads and connections as they
would in production. The client runs in the same process, so the numbers include its
overhead and are an upper bound for one server worker.

```bash
python -m scripts.load_test                         # temp SQLite file, 3 rounds
python -m scripts.load_test --database-url postgresql+psycopg://user:pw@host/empty_db
python -m scripts.load_test --think 0               # stress: no pause at all
```

It prints a per-operation table and exits non-zero if any target is missed. In CI,
`tests/test_performance.py` runs one round on SQLite and on PostgreSQL 16.

## Results (30 Oct 2026)

One server process, 2 vCPUs, 50 concurrent users, 3 rounds (2,850 operations).

| Database | Page load p95 | Search/filter p95 | Quiz submission p95 | Result |
| --- | --- | --- | --- | --- |
| SQLite | 0.67 s | 0.55 s | 1.16 s | pass |
| PostgreSQL 16 | 1.11 s | 0.99 s | 1.51 s | pass |

Every single operation also met its target on both databases (the slowest page,
Materials on PostgreSQL, had a p95 of 1.4 s), and no request failed.

## What the load test found, and the fixes

1. **The quiz list made one query per question of every quiz.** `GET /quizzes` ran
   531 queries (152 ms alone) with 24 quizzes, and under load the Quizzes page's p95
   was **5.7 s**. Because the server is busy for every request, it also pushed the
   Dashboard to 2.1 s and all page loads to 4.4 s p95. Quiz lists now load the
   questions of all listed quizzes in one batch (`building._details`,
   `questions.get_bank_questions`): 6 queries, 17 ms, and the Quizzes page's p95 is
   now 0.6 s. This is the same change as NFR-05's PR #102, copied line for line so
   the two PRs merge cleanly in either order.
2. **A burst could deadlock the connection pool.** With no pause between operations,
   requests failed after 30 s with `QueuePool limit of size 5 overflow 10 reached`.
   FastAPI's 40 threads were all waiting for a connection, while every connection
   belonged to a request waiting for a thread. The server now admits at most one API
   request per database connection (`app/core/concurrency.py`). The others wait at the
   door, holding nothing. The pool is set by `DATABASE_POOL_SIZE` (10) and
   `DATABASE_MAX_OVERFLOW` (20), and the total must stay under 40. A 60-request burst
   on a 2-connection pool is now served without errors
   (`test_NFR_01_a_burst_larger_than_the_pool_is_served_without_errors`).

## Limits and headroom

- With **no pause at all** (`--think 0`), a single worker on PostgreSQL is saturated:
  every request still succeeds, but the slowest pages reach 2.1–2.7 s p95. That is far
  beyond normal load, where each user acts every second or so. Running more uvicorn
  workers (`--workers 2`) would add headroom if usage ever looks like that.
- SQLite serialises writes. Under the no-pause stress some quiz submissions on SQLite
  got the NFR-04 "Temporary problem saving your data" 503 after the 5-second lock
  wait. Production uses PostgreSQL, which has no such limit.
- Material and question search still use one query per row for some details (about
  40 queries per page of 50). They are inside the target. PR #102 (NFR-05) batches
  them.
