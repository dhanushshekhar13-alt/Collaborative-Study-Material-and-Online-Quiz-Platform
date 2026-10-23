"""Llm module (FR-07, FR-08, FR-09).

Owns tables: llm_requests, adaptive_sessions.
Public interface: ``app.modules.llm.service``.
May call the services of: ``accounts``, ``channels``, ``questions``, ``quizzes``.

S07.1: ``provider.py`` (LLM providers, including the deterministic fake),
``generation.py`` (validated generation and the review gate), ``router.py`` (``/ai``).
S08.1: ``evaluation.py`` (descriptive answer scoring with concept feedback) and
``evaluation_router.py`` (``/evaluations``).
S08.2: ``evaluation.evaluate_attempt`` and ``POST /attempts/{id}/evaluate`` store
descriptive results in a quiz attempt through the quizzes service.
"""
