# Quizzes module

Quizzes, their questions, attempts and answers. Owns `quizzes`, `quiz_questions`,
`quiz_attempts` and `attempt_answers`; module rules are in
[`docs/architecture.md`](../../../docs/architecture.md). Quiz assembly and publishing
is S05.2 (#25); this README covers **taking a quiz**, story **S06.1** (issue #28,
FR-06, NFR-01, NFR-06), **My Quiz History**, story **S06.2** (issue #29, FR-06,
NFR-04), and **descriptive results beside objective scoring**, story **S08.2**
(issue #33, FR-08, NFR-06).

Quiz responses carry `creator_name` with `creator_id`, so quizzes contributed in a
channel are attributed to their author (S03.2).

## Taking a quiz

| Method | Path | Auth | Purpose |
| --- | --- | --- | --- |
| `POST` | `/api/v1/quizzes/{quiz_id}/attempts` | `quiz:attempt` | Start the attempt (201), or resume the caller's running one (200). |
| `GET` | `/api/v1/attempts/{attempt_id}` | owner | Questions (no answer key), saved answers, `remaining_seconds`, and the result once submitted. |
| `PUT` | `/api/v1/attempts/{attempt_id}/answers` | owner | Save answers while the timer runs, so they count if time runs out. |
| `POST` | `/api/v1/attempts/{attempt_id}/submit` | owner | Final submission, scored at once: `result.display` is e.g. `4/5 (80%)`. |
| `GET` | `/api/v1/attempts[?quiz_id=]` | signed in | My Quiz History: the caller's submitted attempts, newest first, with `average_accuracy` and `best_percentage`. |

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
`pending_evaluation` until they are evaluated (S08.2, below). After submission each answer shows its status, the
correct answer and the explanation.

## My Quiz History (S06.2)

Every submitted attempt stays stored with its score, maximum, start and submission
times and per-question answers. `GET /attempts` lists the caller's submitted attempts
(newest first) with score, percentage, `display` and timestamps; opening one with
`GET /attempts/{id}` returns the same per-question breakdown that was shown at
submission (IT_11). Attempts still running are not listed. An attempt whose timer ran
out while the user was away is closed first and listed with `auto_submitted: true`.

`average_accuracy` is the **mean of the attempts' percentages**, so each attempt counts
once whatever its length: 60, 80, 100 and 90 percent give **82.5** (ST_12). It is
rounded to one decimal and is `null` with no attempts. (The analytics module's
`PerformanceSummary.accuracy` is total score over total possible, a different measure
kept for FR-12.)

**Reliability (NFR-04).** Each request is one transaction: `get_db` rolls back a
request that fails and every route commits only once its work is complete, so a
submission that fails part-way leaves the attempt open with the answers saved before
it, never half-scored. A transient database failure (`OperationalError`: connection
lost, lock timeout) returns **503** with *"Temporary problem saving your data. Nothing
from this request was saved and your earlier work is safe; please try again."*;
retrying works, and attempts committed earlier are untouched.

## Descriptive results beside objective scoring (S08.2)

Submitting never calls the LLM. Afterwards the client calls
`POST /api/v1/attempts/{id}/evaluate` (LLM module), which evaluates each pending
written answer 0-10 and stores it here through `service.record_descriptive_evaluation`:

- the answer's `score` becomes its evaluation scaled to the question's points
  (`7.5/10` on a 1-point question is `0.75`), its status becomes `evaluated`, and
  `evaluation` holds the stored score, concepts, strengths, weaknesses, summary,
  evaluator and time. `GET /attempts/{id}` and My Quiz History show it from then on
  without calling the LLM again (ST_15);
- the attempt total gains those points. Objective answers cannot be written through
  this function (`ValueError`), so their marks depend only on `scoring.py` (ST_16,
  NFR-06).

Every result and history entry carries `breakdown`: `objective` (answer-key marks),
`descriptive` (evaluations scaled to points), `total` (their sum, equal to
`score`/`max_score`), `pending_evaluation` and an `explanation`, e.g. *"Total 3.2/4 =
objective 3/3 (marked automatically against the answer key) + descriptive 0.2/1 (each
written answer's 0-10 evaluation scaled to the question's points)."* Pending written
answers count 0 and the explanation says so. Blank written answers are `unattempted`
and never evaluated.

## Service interface

`service.start_attempt`, `service.submit_attempt`, `service.get_attempt_result` and
`service.attempts_for_user` keep their S00.2 signatures (analytics and the seed use
them); they delegate to `attempts.py`, where the timer and scoring flow lives.

## Tests

`tests/test_quiz_attempts.py` — Test Plan cases `UT_11`, `UT_12`, `ST_11`, `IT_12`
under their case IDs, plus the timer, access and NFR-01 timing tests. Time is moved
forward by replacing `attempts.utcnow`.

`tests/test_quiz_history.py` — `IT_11`, `ST_12` and the NFR-04 transient-failure tests,
which run the real `get_db` against a file database and inject a failure mid-submit
and at commit.

`tests/test_descriptive_results.py` — `ST_15`, `ST_16` (each with the LLM faked and
with concept matching) and the score-breakdown, scaling and NFR-06 tests.
