# Quizzes module

Quizzes, their questions, attempts and answers. Owns `quizzes`, `quiz_questions`,
`quiz_attempts` and `attempt_answers`; module rules are in
[`docs/architecture.md`](../../../docs/architecture.md). Quiz assembly and publishing
is S05.2 (#25); this README covers **taking a quiz**, story **S06.1** (issue #28,
FR-06, NFR-01, NFR-06).

## Taking a quiz

| Method | Path | Auth | Purpose |
| --- | --- | --- | --- |
| `POST` | `/api/v1/quizzes/{quiz_id}/attempts` | `quiz:attempt` | Start the attempt (201), or resume the caller's running one (200). |
| `GET` | `/api/v1/attempts/{attempt_id}` | owner | Questions (no answer key), saved answers, `remaining_seconds`, and the result once submitted. |
| `PUT` | `/api/v1/attempts/{attempt_id}/answers` | owner | Save answers while the timer runs, so they count if time runs out. |
| `POST` | `/api/v1/attempts/{attempt_id}/submit` | owner | Final submission, scored at once: `result.display` is e.g. `4/5 (80%)`. |

Attempt routes sit under `/attempts`, not `/quizzes/{id}`, so they never clash with the
quiz-building routes. Someone else's attempt answers the same 404 as a missing one.

**Who can attempt.** Published quizzes only. A channel quiz needs channel membership
(403 **`Join the channel to attempt this quiz`**); a quiz in a channel the caller cannot
see is a 404. Official quizzes in an Authorized Channel go through exactly the same
code and scoring as peer quizzes (IT_12).

**Unanswered questions.** Every attempt response carries `unanswered_count` and
`warning`, e.g. **`2 questions are unanswered`** (UT_12). What a submit with gaps does
is set by `QUIZ_UNANSWERED_RULE`:

- `warn` (default): `409` with the warning until the client resubmits with
  `"confirm": true`;
- `block`: `422` with the warning until every question is answered.

Answers sent with a refused submit are kept.

**Timer.** `expires_at` is fixed when the attempt starts (start + quiz duration), so
editing the quiz later cannot change a running attempt. The server owns the clock:
whenever an expired attempt is read, saved or submitted it is closed with the answers
saved before the limit, with `auto_submitted: true` (ST_11). A manual submit arriving
up to 5 s late (`SUBMIT_GRACE`) still counts as on time. Starting the same quiz again
while an attempt runs resumes it with its remaining time.

**Scoring.** Objective questions (multiple choice, true/false, short answer) are scored
all-or-nothing by the pure functions in `scoring.py`, never by the LLM (NFR-06). Only
answered questions are scored; blank ones are stored as `unattempted` (score 0, neither
correct nor incorrect). Descriptive answers are stored unscored as
`pending_evaluation` for S08.1. After submission each answer shows its status, the
correct answer and the explanation.

## Service interface

`service.start_attempt`, `service.submit_attempt`, `service.get_attempt_result` and
`service.attempts_for_user` keep their S00.2 signatures (analytics and the seed use
them); they delegate to `attempts.py`, where the timer and scoring flow lives.

## Tests

`tests/test_quiz_attempts.py` — Test Plan cases `UT_11`, `UT_12`, `ST_11`, `IT_12`
under their case IDs, plus the timer, access and NFR-01 timing tests. Time is moved
forward by replacing `attempts.utcnow`.
