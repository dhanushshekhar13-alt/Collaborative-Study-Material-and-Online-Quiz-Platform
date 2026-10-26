# Analytics module

Performance tracking and the analytics dashboard: **FR-12**, story **S12.1** (issue
#40), under **NFR-03**. Module rules are in [`docs/architecture.md`](../../../docs/architecture.md).

## Data

| Table | Holds |
| --- | --- |
| `performance_attempts` | The ledger: one row per submitted attempt with exactly what it contributed (subject, topic, score, max score, time). Feeds the trend charts. |
| `performance_topics` | Running totals per user, subject and topic: attempt count, points earned, points possible. |

Both are derived from the quizzes module's attempts and can always be rebuilt from
them (migration `0009`). Subject and topic come from the quiz the attempt was taken at.
Accuracy is points earned over points possible, so a descriptive answer still awaiting
evaluation counts 0 until it is scored, exactly as in Quiz History.

## How the figures stay correct

1. **Each attempt updates them.** `quizzes` publishes `quizzes.attempt_scored`
   (`app/core/events.py`) when an attempt is submitted, auto-submitted by its timer, or
   re-scored by a descriptive evaluation. `service.record_attempt` replaces that
   attempt's previous contribution with the new one in the same transaction, so a
   re-score is never counted twice.
2. **They reconcile with the attempts.** `service.sync_user` runs before every
   dashboard read: it closes expired attempts, folds in anything the event missed,
   removes attempts that no longer exist and, if any running total still disagrees
   with the raw attempts, rebuilds the user's figures.
3. **Recomputation gives the same answer.** `service.recompute` derives the figures
   from raw attempts without writing; `service.rebuild_user` rebuilds the stored
   figures from scratch. `GET /analytics/reconciliation` shows stored beside
   recomputed (read-only, no sync), with `matches`.

## Strong and weak topics

| Rule | Value |
| --- | --- |
| Attempts a topic needs before it is labelled (`MIN_ATTEMPTS`) | 2 |
| Strong (`STRONG_AT`) | accuracy ≥ 75% |
| Weak (`WEAK_BELOW`) | accuracy < 50% |
| In between | developing |

Topics with fewer attempts are returned under `not_enough_data` and never appear as
strengths or weak areas. The labels are recomputed on every read, so they change as
soon as a new attempt is recorded. The trend is a least-squares line through attempt
percentages in order; a slope of at least ±1 point per attempt is improving/declining.

## Endpoints

| Method | Path | Auth | Purpose |
| --- | --- | --- | --- |
| `GET` | `/api/v1/analytics/dashboard` | bearer | The caller's totals, per-subject and per-topic accuracy with standings, overall and per-subject trends, strengths, weak areas, and an `empty_message` for a user with no attempts. |
| `GET` | `/api/v1/analytics/reconciliation` | bearer | Stored versus recomputed figures for the caller. |

The web UI's **Performance** page (`#/performance`) draws these as charts: accuracy by
subject, accuracy by topic coloured by standing with the threshold lines, and score
trends with the fitted line.
