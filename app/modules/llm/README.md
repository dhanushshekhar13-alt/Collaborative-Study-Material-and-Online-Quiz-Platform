# LLM module

Everything that talks to the LLM provider. Owns `llm_requests`, an audit record of
every request with its output or error; module rules are in
[`docs/architecture.md`](../../../docs/architecture.md). Question generation and
adaptive quizzes (S07.1, S07.2) use `provider.py`, `generation.py`, `adaptive.py` and
`router.py` (`/ai`). This README covers **descriptive answer evaluation**, story **S08.1** (issue #32, FR-08, NFR-06).

## Provider client (`client.py`)

`LLM_PROVIDER` picks `anthropic` or `openai`; `LLM_MODEL` is that provider's model
name and `LLM_API_KEY` its key (environment only, never committed). With
`LLM_PROVIDER=none` or no key, `provider_client` returns `None` and the features fall
back as described below. The client uses only the standard library (one HTTPS POST,
20 s timeout); every failure becomes `LlmError`. Routes get the client through the
`get_llm_client` dependency (`evaluation_router.py`), which tests override with a fake.

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
  The student's answer is sent between markers, and the system prompt tells the model
  to treat it as data, never instructions.
- `concept-match`: deterministic. The score is the share of expected concepts the
  answer mentions (word stems, so "isolated" counts for Isolation). Strengths and
  weaknesses come from the concepts covered and missed and from whether the answer
  gives examples. It is used when no LLM is configured, so the prototype works offline. It
  is also used when the LLM call fails or replies with something unusable (the failed
  request is still recorded).

A question with no `expected_concepts` (only a model answer) needs the LLM. Without
one it is refused with 422 *"This question has no expected concepts to evaluate
against"*. Objective questions are refused (*"Only descriptive questions can be
evaluated"*): they are scored deterministically by the quizzes module. A question the
caller cannot read in the bank is a 404.

Storing descriptive results inside a quiz attempt beside the objective score is
S08.2 (#33). It can call `evaluation.evaluate`.

## Dependencies

`accounts` (route guard), `channels` (question visibility), `questions` (the question
and its expected concepts) and `quizzes`.

## Tests

`tests/test_descriptive_evaluation.py`: Test Plan cases `UT_15`, `UT_16`, `IT_15`,
`IT_16` under their case IDs (each with the LLM faked and with concept matching), plus
scale, fallback, audit, prompt, access and provider-client tests.
