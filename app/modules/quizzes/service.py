"""Public interface of the quizzes module (quiz building, attempts, grading)."""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modules.quizzes import adaptive, attempts
from app.modules.quizzes.adaptive import (  # noqa: F401 - re-exported for callers
    AdaptiveAnswer,
    AdaptiveAnswerError,
    ServedQuestion,
)
from app.modules.quizzes.attempts import (  # noqa: F401 - re-exported for callers
    ATTEMPT_SCORED,
    AttemptOutcome,
    NotSubmittedError,
    PendingDescriptive,
    ScoreBreakdown,
    ScorePart,
)
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
    adaptive: bool = False,
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
        is_adaptive=adaptive,
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


def open_attempt_for(db: Session, quiz_id: int, user_id: int) -> int | None:
    """The user's attempt at this quiz that is still running, if any (S09.1 resume)."""
    return attempts.open_attempt_for(db, quiz_id, user_id)


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


# --------------------------------------------------------------------------- #
# S12.1 - scored attempts with the quiz's subject and topic, for analytics
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ScoredAttempt:
    """A submitted attempt with the subject and topic of the quiz it was taken at."""

    attempt_id: int
    user_id: int
    quiz_id: int
    quiz_title: str
    subject: str
    topic: str
    score: float
    max_score: float
    submitted_at: datetime


def _scored(attempt: QuizAttempt, quiz: Quiz) -> ScoredAttempt:
    return ScoredAttempt(
        attempt_id=attempt.id,
        user_id=attempt.user_id,
        quiz_id=quiz.id,
        quiz_title=quiz.title,
        subject=quiz.subject,
        topic=quiz.topic,
        score=attempt.score or 0.0,
        max_score=attempt.max_score or 0.0,
        submitted_at=attempt.submitted_at,
    )


def scored_attempt(db: Session, attempt_id: int) -> ScoredAttempt | None:
    """The attempt as analytics sees it, or ``None`` if it is missing or still running."""
    row = db.execute(
        select(QuizAttempt, Quiz)
        .join(Quiz, Quiz.id == QuizAttempt.quiz_id)
        .where(QuizAttempt.id == attempt_id, QuizAttempt.status == AttemptStatus.SUBMITTED)
    ).first()
    return _scored(*row) if row else None


def scored_attempts_for_user(db: Session, user_id: int) -> list[ScoredAttempt]:
    """Every submitted attempt of the user, oldest first (ties broken by id)."""
    rows = db.execute(
        select(QuizAttempt, Quiz)
        .join(Quiz, Quiz.id == QuizAttempt.quiz_id)
        .where(QuizAttempt.user_id == user_id, QuizAttempt.status == AttemptStatus.SUBMITTED)
        .order_by(QuizAttempt.submitted_at, QuizAttempt.id)
    ).all()
    return [_scored(attempt, quiz) for attempt, quiz in rows]


def close_expired_attempts(db: Session, user_id: int) -> int:
    """Auto-submit the user's attempts whose time ran out; returns how many closed."""
    return attempts.close_expired_for_user(db, user_id)


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


# --------------------------------------------------------------------------- #
# S07.2 - adaptive attempts (driven by the LLM module)
# --------------------------------------------------------------------------- #


def add_quiz_question(db: Session, quiz_id: int, question_id: int) -> int:
    """Append a question to an adaptive quiz as it is served; returns its position."""
    return adaptive.add_question(db, quiz_id, question_id)


def adaptive_history(db: Session, attempt_id: int) -> list[ServedQuestion]:
    """Questions served so far with their difficulty and result, in serving order."""
    return adaptive.served(db, attempt_id)


def answer_adaptive(
    db: Session, attempt_id: int, question_id: int, response: str
) -> AdaptiveAnswer:
    """Score the current question's answer immediately (deterministic scoring)."""
    return adaptive.answer(db, attempt_id, question_id, response)


def finish_attempt(db: Session, attempt_id: int) -> AttemptOutcome:
    """Close the attempt and return its result."""
    attempts.submit(db, attempt_id)
    return attempts.state(db, attempt_id).outcome


def attempt_outcome(db: Session, attempt_id: int) -> AttemptOutcome | None:
    """The result of a submitted attempt, or ``None`` while it is still running."""
    return attempts.state(db, attempt_id).outcome


# --------------------------------------------------------------------------- #
# S08.2 - descriptive results stored beside the deterministic objective score
# --------------------------------------------------------------------------- #


def attempt_owner(db: Session, attempt_id: int) -> int | None:
    """Who took the attempt, or ``None`` if there is no such attempt."""
    return attempts.owner_of(db, attempt_id)


def pending_descriptive_answers(db: Session, attempt_id: int) -> list[PendingDescriptive]:
    """Answered descriptive questions of a submitted attempt not evaluated yet.

    Raises ``NotSubmittedError`` while the attempt is still running.
    """
    return attempts.pending_descriptive(db, attempt_id)


def record_descriptive_evaluation(
    db: Session,
    attempt_id: int,
    question_id: int,
    *,
    score: float,
    max_score: float,
    feedback: dict,
) -> float:
    """Store a descriptive answer's evaluation in the attempt; returns the points earned.

    The 0-``max_score`` result is scaled to the question's points and added to the
    attempt total. Objective answers cannot be written (``ValueError``), so their
    deterministic marks never depend on the LLM (NFR-06).
    """
    return attempts.record_descriptive(
        db, attempt_id, question_id, score=score, max_score=max_score, feedback=feedback
    )


def score_breakdown(db: Session, attempt_id: int) -> ScoreBreakdown:
    """The attempt's objective, descriptive and total score with an explanation."""
    return attempts.breakdown(db, attempt_id)
