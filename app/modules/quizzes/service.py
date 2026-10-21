"""Public interface of the quizzes module (quiz building, attempts, grading)."""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modules.quizzes import attempts
from app.modules.quizzes.models import (
    AttemptStatus,
    Quiz,
    QuizAttempt,
    QuizQuestion,
    QuizStatus,
)


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
    """Start an attempt with its time limit fixed from the quiz duration (S06.1)."""
    return attempts.start(db, quiz_id, user_id)


def submit_attempt(db: Session, attempt_id: int, responses: dict[int, str]) -> AttemptResult:
    """Store answers, score objective questions and close the attempt (FR-06).

    Unanswered questions are recorded as unattempted. Descriptive questions are stored
    unscored; the LLM module fills in their score. The HTTP flow, with the unanswered
    warning and the timer, is in ``attempts`` and ``attempts_router``.
    """
    attempts.submit(db, attempt_id, responses)
    return get_attempt_result(db, attempt_id)


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


@dataclass(frozen=True)
class QuizContribution:
    """One quiz the user contributed, for their profile's history (S01.2)."""

    id: int
    title: str
    created_at: datetime


def count_created_by(db: Session, user_id: int) -> int:
    """How many published quizzes the user created; drafts and removed ones don't count."""
    return (
        db.scalar(
            select(func.count(Quiz.id)).where(
                Quiz.creator_id == user_id, Quiz.status == QuizStatus.PUBLISHED
            )
        )
        or 0
    )


def recent_created_by(db: Session, user_id: int, limit: int = 10) -> list[QuizContribution]:
    rows = db.scalars(
        select(Quiz)
        .where(Quiz.creator_id == user_id, Quiz.status == QuizStatus.PUBLISHED)
        .order_by(Quiz.created_at.desc(), Quiz.id.desc())
        .limit(limit)
    )
    return [QuizContribution(r.id, r.title, r.created_at) for r in rows]
