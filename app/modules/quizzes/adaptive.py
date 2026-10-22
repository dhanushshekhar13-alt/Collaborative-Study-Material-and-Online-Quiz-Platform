"""Quiz-side storage for adaptive attempts (S07.2, FR-07).

An adaptive quiz grows one question at a time: the LLM module chooses each next
question and appends it here, and each answer is scored straight away with the same
pure scoring functions as any quiz (NFR-06), so the next difficulty can be decided.
Other modules reach this through ``app.modules.quizzes.service``.
"""

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modules.questions import service as questions
from app.modules.quizzes.models import AttemptAnswer, AttemptStatus, QuizAttempt, QuizQuestion
from app.modules.quizzes.scoring import score_objective


class AdaptiveAnswerError(Exception):
    """The answer cannot be recorded (wrong question, already answered, attempt closed)."""


@dataclass(frozen=True)
class ServedQuestion:
    """One question served in an adaptive attempt, in the order it was served."""

    question_id: int
    position: int
    difficulty: str
    topic: str
    response: str | None
    is_correct: bool | None

    @property
    def answered(self) -> bool:
        return self.response is not None


@dataclass(frozen=True)
class AdaptiveAnswer:
    question_id: int
    is_correct: bool
    correct_answer: str | None
    explanation: str | None


def add_question(db: Session, quiz_id: int, question_id: int) -> int:
    """Append a question to the quiz; returns its position."""
    last = db.scalar(select(func.max(QuizQuestion.position)).where(QuizQuestion.quiz_id == quiz_id))
    position = (last or 0) + 1
    db.add(QuizQuestion(quiz_id=quiz_id, question_id=question_id, position=position))
    db.flush()
    return position


def served(db: Session, attempt_id: int) -> list[ServedQuestion]:
    attempt = db.get(QuizAttempt, attempt_id)
    if attempt is None:
        raise LookupError(f"attempt {attempt_id} not found")
    links = db.scalars(
        select(QuizQuestion)
        .where(QuizQuestion.quiz_id == attempt.quiz_id)
        .order_by(QuizQuestion.position)
    ).all()
    views = questions.get_questions(db, (link.question_id for link in links))
    answers = {
        row.question_id: row
        for row in db.scalars(select(AttemptAnswer).where(AttemptAnswer.attempt_id == attempt_id))
    }
    out = []
    for link in links:
        view, row = views[link.question_id], answers.get(link.question_id)
        out.append(
            ServedQuestion(
                question_id=link.question_id,
                position=link.position,
                difficulty=row.difficulty_served
                if row and row.difficulty_served
                else view.difficulty.value,
                topic=view.topic,
                response=row.response if row else None,
                is_correct=row.is_correct if row else None,
            )
        )
    return out


def answer(db: Session, attempt_id: int, question_id: int, response: str) -> AdaptiveAnswer:
    """Record and score the answer to the question currently being shown."""
    attempt = db.get(QuizAttempt, attempt_id)
    if attempt is None:
        raise LookupError(f"attempt {attempt_id} not found")
    if attempt.status is AttemptStatus.SUBMITTED:
        raise AdaptiveAnswerError("This adaptive quiz is already finished")
    history = served(db, attempt_id)
    current = next((s for s in history if not s.answered), None)
    if current is None or current.question_id != question_id:
        raise AdaptiveAnswerError("Answer the question currently shown")
    view = questions.get_questions(db, [question_id])[question_id]
    link_points = 1.0
    score = score_objective(view, response, link_points)
    db.add(
        AttemptAnswer(
            attempt_id=attempt_id,
            question_id=question_id,
            response=response,
            is_correct=score == link_points,
            score=score,
            difficulty_served=view.difficulty.value,
        )
    )
    db.flush()
    return AdaptiveAnswer(question_id, score == link_points, view.correct_answer, view.explanation)
