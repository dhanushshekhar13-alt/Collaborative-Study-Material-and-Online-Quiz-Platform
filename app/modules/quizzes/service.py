"""Public interface of the quizzes module (quiz building, attempts, grading)."""

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.questions import service as questions
from app.modules.quizzes.models import (
    AttemptAnswer,
    AttemptStatus,
    Quiz,
    QuizAttempt,
    QuizQuestion,
    QuizStatus,
)
from app.modules.quizzes.scoring import score_objective


@dataclass(frozen=True)
class AttemptResult:
    attempt_id: int
    quiz_id: int
    user_id: int
    score: float
    max_score: float
    submitted_at: datetime | None


def create_quiz(
    db: Session,
    *,
    creator_id: int,
    title: str,
    subject: str,
    topic: str,
    difficulty: str,
    duration_minutes: int,
    question_ids: list[int],
    channel_id: int | None = None,
    publish: bool = False,
) -> int:
    """Create a quiz from question-bank questions in the given order; returns its id."""
    quiz = Quiz(
        creator_id=creator_id,
        title=title,
        subject=subject,
        topic=topic,
        difficulty=difficulty,
        duration_minutes=duration_minutes,
        channel_id=channel_id,
        status=QuizStatus.PUBLISHED if publish else QuizStatus.DRAFT,
    )
    db.add(quiz)
    db.flush()
    for position, qid in enumerate(question_ids, start=1):
        db.add(QuizQuestion(quiz_id=quiz.id, question_id=qid, position=position))
    db.flush()
    return quiz.id


def start_attempt(db: Session, quiz_id: int, user_id: int) -> int:
    attempt = QuizAttempt(quiz_id=quiz_id, user_id=user_id)
    db.add(attempt)
    db.flush()
    return attempt.id


def submit_attempt(db: Session, attempt_id: int, responses: dict[int, str]) -> AttemptResult:
    """Store answers, score objective questions and close the attempt (FR-06).

    Descriptive questions are stored unscored; the LLM module fills in their score.
    """
    attempt = db.get(QuizAttempt, attempt_id)
    if attempt is None:
        raise LookupError(f"attempt {attempt_id} not found")
    if attempt.status is AttemptStatus.SUBMITTED:
        raise ValueError("attempt already submitted")

    links = db.scalars(select(QuizQuestion).where(QuizQuestion.quiz_id == attempt.quiz_id)).all()
    views = questions.get_questions(db, (link.question_id for link in links))

    total = earned = 0.0
    for link in links:
        view = views[link.question_id]
        response = responses.get(link.question_id)
        total += link.points
        answer = AttemptAnswer(
            attempt_id=attempt.id,
            question_id=link.question_id,
            response=response,
            difficulty_served=view.difficulty.value,
        )
        if view.is_objective:
            answer.score = score_objective(view, response, link.points)
            answer.is_correct = answer.score == link.points
            earned += answer.score
        db.add(answer)

    attempt.score, attempt.max_score = earned, total
    attempt.status = AttemptStatus.SUBMITTED
    attempt.submitted_at = datetime.now(UTC)
    db.flush()
    return get_attempt_result(db, attempt.id)


def get_attempt_result(db: Session, attempt_id: int) -> AttemptResult:
    a = db.get(QuizAttempt, attempt_id)
    if a is None:
        raise LookupError(f"attempt {attempt_id} not found")
    return AttemptResult(
        a.id, a.quiz_id, a.user_id, a.score or 0.0, a.max_score or 0.0, a.submitted_at
    )


def attempts_for_user(db: Session, user_id: int) -> list[AttemptResult]:
    """Submitted attempts, oldest first (used by analytics, FR-12)."""
    rows = db.scalars(
        select(QuizAttempt)
        .where(QuizAttempt.user_id == user_id, QuizAttempt.status == AttemptStatus.SUBMITTED)
        .order_by(QuizAttempt.submitted_at)
    )
    return [get_attempt_result(db, a.id) for a in rows]
