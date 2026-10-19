"""Objective answer scoring (FR-06).

Pure functions with no database or framework dependency, so scoring rules can be
tested in isolation and no other module can change them by accident.
"""

from app.modules.questions.service import QuestionView

_TRUE = {"true", "t", "yes", "1"}
_FALSE = {"false", "f", "no", "0"}


def _normalise(text: str) -> str:
    return " ".join(text.strip().lower().split())


def score_objective(question: QuestionView, response: str | None, points: float = 1.0) -> float:
    """Return the points earned for an objective answer (all or nothing)."""
    if not question.is_objective:
        raise ValueError("descriptive answers are evaluated by the LLM module (FR-08)")
    if response is None or question.correct_answer is None:
        return 0.0
    given, expected = _normalise(response), _normalise(question.correct_answer)
    if question.question_type.value == "true_false":
        given_bool = True if given in _TRUE else False if given in _FALSE else None
        expected_bool = expected in _TRUE
        return points if given_bool is expected_bool else 0.0
    return points if given == expected else 0.0
