"""Public interface of the quizzes module (quiz building, attempts, grading)."""

from collections.abc import Collection
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, or_, select
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
    AttemptAnswer,
    AttemptStatus,
    Quiz,
    QuizAttempt,
    QuizQuestion,
    QuizStatus,
)


@dataclass(frozen=True)
class AttemptResult:
    """A scored attempt's totals."""

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
    """The attempt's score totals; raises ``LookupError`` if it does not exist."""
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


# --------------------------------------------------------------------------- #
# S12.2 - what recommendations may offer, and what the user has already done
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class QuizListing:
    """A published, attemptable quiz as a recommendation shows it."""

    id: int
    title: str
    subject: str
    topic: str
    difficulty: str
    channel_id: int | None
    creator_id: int
    created_at: datetime | None
    question_count: int
    attempt_count: int


def published_in_scope(
    db: Session,
    *,
    channel_ids: Collection[int],
    include_general: bool = True,
    subject: str | None = None,
    topic: str | None = None,
    exclude_creator_id: int | None = None,
    limit: int = 50,
) -> list[QuizListing]:
    """Published quizzes with questions, general and in the given channels.

    Adaptive runs are not listed (they are one person's session, not a quiz to share).
    Subject and topic match whole values, ignoring case. Most attempted first, then
    newest.
    """
    scope = [Quiz.channel_id.in_(list(channel_ids))] if channel_ids else []
    if include_general:
        scope.append(Quiz.channel_id.is_(None))
    if not scope:
        return []
    question_count = (
        select(func.count(QuizQuestion.id)).where(QuizQuestion.quiz_id == Quiz.id).scalar_subquery()
    )
    attempt_count = (
        select(func.count(QuizAttempt.id))
        .where(QuizAttempt.quiz_id == Quiz.id, QuizAttempt.status == AttemptStatus.SUBMITTED)
        .scalar_subquery()
    )
    stmt = select(Quiz, question_count, attempt_count).where(
        Quiz.status == QuizStatus.PUBLISHED,
        Quiz.is_adaptive.is_(False),
        question_count > 0,
        or_(*scope),
    )
    if subject and subject.strip():
        stmt = stmt.where(func.lower(Quiz.subject) == subject.strip().lower())
    if topic and topic.strip():
        stmt = stmt.where(func.lower(Quiz.topic) == topic.strip().lower())
    if exclude_creator_id is not None:
        stmt = stmt.where(Quiz.creator_id != exclude_creator_id)
    stmt = stmt.order_by(attempt_count.desc(), Quiz.created_at.desc(), Quiz.id.desc())
    return [
        QuizListing(
            q.id,
            q.title,
            q.subject,
            q.topic,
            q.difficulty,
            q.channel_id,
            q.creator_id,
            q.created_at,
            int(n_questions),
            int(n_attempts),
        )
        for q, n_questions, n_attempts in db.execute(stmt.limit(limit))
    ]


@dataclass(frozen=True)
class QuizInfo:
    """What another module may show about one quiz (a chat preview card, S11.2)."""

    id: int
    title: str
    subject: str
    topic: str
    difficulty: str
    duration_minutes: int
    channel_id: int | None
    creator_id: int
    status: QuizStatus
    is_adaptive: bool
    question_count: int


def describe_quiz(db: Session, quiz_id: int) -> QuizInfo | None:
    """One quiz's current details, whatever its status (callers check access)."""
    quiz = db.get(Quiz, quiz_id)
    if quiz is None:
        return None
    count = db.scalar(select(func.count(QuizQuestion.id)).where(QuizQuestion.quiz_id == quiz.id))
    return QuizInfo(
        quiz.id,
        quiz.title,
        quiz.subject,
        quiz.topic,
        quiz.difficulty,
        quiz.duration_minutes,
        quiz.channel_id,
        quiz.creator_id,
        quiz.status,
        bool(quiz.is_adaptive),
        int(count or 0),
    )


def attempted_quiz_ids(db: Session, user_id: int) -> set[int]:
    """Quizzes the user has submitted at least one attempt at."""
    return set(
        db.scalars(
            select(QuizAttempt.quiz_id).where(
                QuizAttempt.user_id == user_id, QuizAttempt.status == AttemptStatus.SUBMITTED
            )
        )
    )


def answered_question_ids(db: Session, user_id: int) -> set[int]:
    """Questions the user has answered in a submitted attempt."""
    return set(
        db.scalars(
            select(AttemptAnswer.question_id)
            .join(QuizAttempt, QuizAttempt.id == AttemptAnswer.attempt_id)
            .where(
                QuizAttempt.user_id == user_id,
                QuizAttempt.status == AttemptStatus.SUBMITTED,
                AttemptAnswer.response.is_not(None),
            )
        )
    )


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
    """The user's most recent published quizzes, newest first."""
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
