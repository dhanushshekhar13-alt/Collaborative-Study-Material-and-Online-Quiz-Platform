# LLM module

Everything that talks to the LLM provider. Owns `llm_requests`, an audit record of
every request with its output or error; module rules are in
[`docs/architecture.md`](../../../docs/architecture.md). This README covers
**descriptive answer evaluation**, story **S08.1** (issue #32, FR-08, NFR-06), and
**Ask AI**, story **S09.1** (issue #34, FR-09).

## Provider

Providers live in `provider.py` (S07.1): `LLM_PROVIDER` picks `openai`, `anthropic`,
the offline deterministic `fake` (question generation only) or `none`. Routes get the
provider through `router.get_llm_provider`, which tests override with a fake. The
question generation and adaptive routes (`/ai/generations`, `/ai/adaptive-quizzes`) are
described in `router.py`.

## Descriptive answer evaluation

| Method | Path | Auth | Purpose |
| --- | --- | --- | --- |
| `POST` | `/api/v1/evaluations/descriptive` | signed in | Score `answer` to descriptive question `question_id` and return concept feedback. |

**Scale.** `score` is **0 to 10 in steps of 0.5**, `max_score` is always 10 and
`display` reads e.g. `5/10`. A complete answer scores high (UT_15) and a partial one
proportionally lower (UT_16).

**Feedback.** `covered_concepts` and `missing_concepts` name each of the question's
`expected_concepts` individually (e.g. `["Isolation", "Durability"]`), `strengths` and
`weaknesses` each hold at least one phrase (IT_16) and `summary` is one sentence. An
answer that addresses none of the concepts scores 0 with *"No relevant concepts were
addressed."*

**Blank answers** are refused with **422 `Answer cannot be empty`** before anything is
recorded or sent, so no empty request reaches the LLM (IT_15).

**Evaluators** (`evaluator` in the response):

- `llm`: the provider grades the answer. The reply is validated rather than trusted
  (NFR-06). The score is clamped and rounded to the scale. Concepts are matched back to the
  question's list. Any expected concept it does not confirm as covered is reported missing.
  With no concept covered the score is 0. Empty strengths or weaknesses are filled in.
  The grading instructions travel in the prompt itself (the shared provider's system
  prompt is generic). The student's answer is sent between markers, and the instructions tell
  the model to treat it as data, never instructions.
- `concept-match`: deterministic. The score is the share of expected concepts the
  answer mentions (word stems, so "isolated" counts for Isolation). Strengths and
  weaknesses come from the concepts covered and missed and from whether the answer
  gives examples. It is used when no LLM is configured and with `LLM_PROVIDER=fake`, so the prototype works offline. It
  is also used when the LLM call fails or replies with something unusable (the failed
  request is still recorded).

A question with no `expected_concepts` (only a model answer) needs the LLM. Without
one it is refused with 422 *"This question has no expected concepts to evaluate
against"*. Objective questions are refused (*"Only descriptive questions can be
evaluated"*): they are scored deterministically by the quizzes module. A question the
caller cannot read in the bank is a 404.

## Evaluating a quiz attempt (S08.2)

| Method | Path | Auth | Purpose |
| --- | --- | --- | --- |
| `POST` | `/api/v1/attempts/{attempt_id}/evaluate` | owner | Evaluate the submitted attempt's pending written answers and store the results in it. |

`evaluation.evaluate_attempt` runs `evaluate` on each answered descriptive question the
quizzes module reports as pending, and stores every result through
`quizzes.service.record_descriptive_evaluation`, which scales it to the question's
points and adds it to the attempt total. Each result is committed as soon as it is
ready, so a provider outage part-way keeps the ones done. Answers already evaluated are
never re-sent, so calling again is safe and cheap. An answer that cannot be evaluated
(no expected concepts and no LLM) stays pending and is listed in `not_evaluated` with
the reason. A running attempt is **409** *"Submit the attempt before its written answers
are evaluated"*; someone else's attempt is a 404. The response carries the attempt's
`breakdown` (objective, descriptive, total, explanation).

The quiz UI (UI-2) should call it straight after a successful submit and show the
returned breakdown; the result also stays in `GET /attempts/{id}` and My Quiz History.

## Ask AI: explanations and topic quizzes (S09.1)

| Method | Path | Auth | Purpose |
| --- | --- | --- | --- |
| `POST` | `/api/v1/ai/ask` | signed in | `query` in plain language: explain a concept, or generate a quiz on a topic. |
| `POST` | `/api/v1/ai/ask/{id}/start` | requester | Start (201) or resume (200) the generated topic quiz; answer it with `/attempts/{id}`. |

`assistant.classify` decides the kind from the wording. A query mentioning a quiz
("Give me a quiz on Binary Search Trees", "quiz me on SQL joins", "test me on ...") is
a **quiz** request; anything else is an **explanation** request, and the concept is
taken from it ("Explain B-Trees with an example" gives `B-Trees`).

- **Explanation** (`kind: "explanation"`): `explanation.text` plus at least one
  `explanation.examples` entry (UT_17). The reply is validated: it must be JSON with an
  explanation, at least one example, and mention the concept asked about. An unusable
  or off-topic reply is requested once more, then the request fails with 502. The query
  travels between markers and the prompt tells the model to treat it as data.
- **Topic quiz** (`kind: "quiz"`): 5 questions by default (5-10 via `question_count` or
  "an 8 question quiz" in the query; optional `subject`, default `General`, and
  `difficulty`, default medium). They are generated through `generation.generate`, so
  they are **drafts** with a review batch (`quiz.generation_id`): they serve the
  requester's private quiz, but no bank, search or other user sees them until approved
  on `GET /ai/generations/{id}`. The quiz is a private draft (2 minutes per question)
  that `POST /quizzes/{id}/publish` refuses while any of its questions is unapproved.
  A quiz request with no topic is 422 *"Name the topic for the quiz, ..."*.

A blank query is **422 `Please enter a question`** before anything is recorded or sent
to the LLM (UT_18), whatever the provider setting. With `LLM_PROVIDER=none` the route
answers 503 *"Ask AI is not available on this server right now."*; provider errors are
502 and timeouts 504. Every call is recorded in `llm_requests` (`explain`,
`generate_quiz`). `response_ms` reports the server's time for the answer (ST_18).
`LLM_PROVIDER=fake` answers explanations deterministically (a short glossary for
B-Trees, normalization, indexing and binary search trees, a topic-specific template
otherwise), so the prototype works offline.

## Dependencies

`accounts` (route guard), `channels` (question visibility), `questions` (the question
and its expected concepts) and `quizzes`.

## Tests

`tests/test_descriptive_evaluation.py`: Test Plan cases `UT_15`, `UT_16`, `IT_15`,
`IT_16` under their case IDs (each with the LLM faked and with concept matching), plus
scale, fallback, audit, prompt, access and provider-client tests.

`tests/test_descriptive_results.py`: `ST_15` and `ST_16` (S08.2) and the attempt
evaluation route.

`tests/test_ask_ai.py`: `UT_17`, `UT_18`, `ST_18` and `IT_17` (S09.1), plus validation,
failure, review-gate and access tests.
