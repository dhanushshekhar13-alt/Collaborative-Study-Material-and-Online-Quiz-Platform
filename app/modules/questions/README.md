# Questions module

The shared question bank. Implements the contribution and discovery half of **FR-05**
(story **S05.1**, issue #24); quiz assembly (S05.2) and LLM generation (S07.x) build on
it. Owns `questions` and `question_ratings`; module rules are in
[`docs/architecture.md`](../../../docs/architecture.md).

## Banks

A question belongs to the **general bank** (`channel_id` null) or to one **channel
bank**. Anyone signed in can browse the general bank. A channel bank can be browsed by
anyone who can see the channel (a pending Authorized Channel answers 404, so it is not
revealed) and contributed to by its members and admins.

Only `published` questions are listed or searched. LLM-generated questions start as
`draft` and stay out of the bank until reviewed (FR-07); a draft is readable only by
its author and admins.

## Contribution rules

Text, subject, topic, difficulty and type are required (UT_09). Without a correct
answer the submission is refused with **`Please select the correct answer`** (UT_10):

| Type | Correct answer |
| --- | --- |
| `multiple_choice` | one of at least two distinct options |
| `true_false` | `True` or `False` (options are set to both) |
| `short_answer` | the expected answer |
| `descriptive` | a model answer, or the `expected_concepts` an answer must cover |

## Endpoints

| Method | Path | Auth | Purpose |
| --- | --- | --- | --- |
| `POST` | `/api/v1/questions` | `question:contribute` | Contribute a question; `channel_id` scopes it to a channel bank. |
| `GET` | `/api/v1/questions` | bearer | Search one bank: `q` keyword (text, subject, topic, explanation), `subject`, `topic`, `difficulty`, `type`, `channel_id`, `limit`, `offset`. |
| `GET` | `/api/v1/questions/{id}` | bearer | One question with `rating_average`, `rating_count`, `my_rating`. |
| `PUT` | `/api/v1/questions/{id}/rating` | bearer | Save a 1–5 star rating; rating again replaces it. |
| `POST` | `/api/v1/questions/{id}/reports` | `report:submit` | Report a question (`reason: incorrect` is "Incorrect answer"); logged as an open moderation report, scoped to the question's channel. A repeat while open returns the same report. |

## Dependencies

`accounts` (route guards), `channels` (bank visibility and membership) and
`moderation` (`file_report`). None of them reaches chat.

## Tests

`tests/test_questions.py` — Test Plan cases `UT_09`, `UT_10`, `IT_09` under their case
IDs, plus validation, scope, search filter, rating and report tests.
