"""Quiz attempts: timer, saved answers, the unanswered check and scoring (S06.1, FR-06).

The server owns the clock. An attempt's time limit (``expires_at``) is fixed when it
starts, and whenever an expired attempt is read or written it is closed with the
answers saved so far, so a client that stops ticking cannot extend the time.

Objective questions are scored here with the pure functions in ``scoring.py``, never
by the LLM (NFR-06). Descriptive answers are stored unscored for S08.1 to evaluate.
Other modules reach this through ``app.modules.quizzes.service``.
"""

import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum

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

# A manual submit that reaches the server this soon after the limit still counts as
# on time, so network latency at the last second does not cost the answers in it.
SUBMIT_GRACE = timedelta(seconds=5)


def utcnow() -> datetime:
    """The attempt clock. Tests replace it to move time forward."""
    return datetime.now(UTC)


def _aware(value: datetime | None) -> datetime | None:
    """SQLite returns naive datetimes; every stored time is UTC."""
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


class AnswerStatus(StrEnum):
    CORRECT = "correct"
    INCORRECT = "incorrect"
    UNATTEMPTED = "unattempted"
    PENDING = "pending_evaluation"  # descriptive, scored by the LLM module (S08.1)


@dataclass(frozen=True)
class AttemptQuestion:
    """A question as the person taking the quiz sees it: no answer key."""

    question_id: int
    position: int
    text: str
    question_type: str
    options: tuple[str, ...]
    points: float
    response: str | None


@dataclass(frozen=True)
class AnswerOutcome:
    question_id: int
    position: int
    response: str | None
    status: AnswerStatus
    score: float | None
    points: float
    correct_answer: str | None
    explanation: str | None


@dataclass(frozen=True)
class AttemptOutcome:
    score: float
    max_score: float
    percentage: float
    correct: int
    incorrect: int
    unattempted: int
    pending_evaluation: int
    submitted_at: datetime | None
    answers: tuple[AnswerOutcome, ...]

    @property
    def display(self) -> str:
        """The result line shown straight after submitting, e.g. ``4/5 (80%)``."""
        return f"{self.score:g}/{self.max_score:g} ({self.percentage:g}%)"


@dataclass(frozen=True)
class AttemptState:
    id: int
    quiz_id: int
    quiz_title: str
    user_id: int
    status: AttemptStatus
    started_at: datetime
    expires_at: datetime | None
    remaining_seconds: int | None
    auto_submitted: bool
    questions: tuple[AttemptQuestion, ...]
    unanswered_question_ids: tuple[int, ...]
    outcome: AttemptOutcome | None

    @property
    def unanswered_count(self) -> int:
        return len(self.unanswered_question_ids)


@dataclass(frozen=True)
class QuizAccessInfo:
    """What the router needs to decide whether someone may attempt a quiz."""

    id: int
    title: str
    channel_id: int | None
    creator_id: int
    is_published: bool
    question_count: int


def unanswered_warning(count: int) -> str:
    """The warning shown before final submission (UT_12): "2 questions are unanswered"."""
    return f"{count} question is unanswered" if count == 1 else f"{count} questions are unanswered"


def _answered(response: str | None) -> bool:
    return response is not None and response.strip() != ""


def quiz_access_info(db: Session, quiz_id: int) -> QuizAccessInfo | None:
    quiz = db.get(Quiz, quiz_id)
    if quiz is None:
        return None
    count = len(db.scalars(select(QuizQuestion.id).where(QuizQuestion.quiz_id == quiz_id)).all())
    return QuizAccessInfo(
        quiz.id,
        quiz.title,
        quiz.channel_id,
        quiz.creator_id,
        quiz.status is QuizStatus.PUBLISHED,
        count,
    )


def _links(db: Session, quiz_id: int) -> list[QuizQuestion]:
    return list(
        db.scalars(
            select(QuizQuestion)
            .where(QuizQuestion.quiz_id == quiz_id)
            .order_by(QuizQuestion.position, QuizQuestion.id)
        )
    )


def _saved(db: Session, attempt_id: int) -> dict[int, AttemptAnswer]:
    rows = db.scalars(select(AttemptAnswer).where(AttemptAnswer.attempt_id == attempt_id))
    return {row.question_id: row for row in rows}


def _expired(attempt: QuizAttempt, now: datetime, grace: timedelta = timedelta(0)) -> bool:
    expires_at = _aware(attempt.expires_at)
    return expires_at is not None and now > expires_at + grace


def _get(db: Session, attempt_id: int) -> QuizAttempt:
    attempt = db.get(QuizAttempt, attempt_id)
    if attempt is None:
        raise LookupError(f"attempt {attempt_id} not found")
    return attempt


def owner_of(db: Session, attempt_id: int) -> int | None:
    """Who is taking the attempt, or ``None`` if there is no such attempt."""
    attempt = db.get(QuizAttempt, attempt_id)
    return attempt.user_id if attempt else None


def open_attempt_for(db: Session, quiz_id: int, user_id: int) -> int | None:
    """The user's unfinished attempt at this quiz, if there is one still running."""
    rows = db.scalars(
        select(QuizAttempt).where(
            QuizAttempt.quiz_id == quiz_id,
            QuizAttempt.user_id == user_id,
            QuizAttempt.status == AttemptStatus.IN_PROGRESS,
        )
    ).all()
    now = utcnow()
    running = None
    for attempt in rows:
        if _expired(attempt, now):
            _finalise(db, attempt, now, auto=True)
        else:
            running = attempt.id
    return running


def start(db: Session, quiz_id: int, user_id: int) -> int:
    """Start an attempt; its time limit is fixed now from the quiz duration."""
    quiz = db.get(Quiz, quiz_id)
    if quiz is None:
        raise LookupError(f"quiz {quiz_id} not found")
    now = utcnow()
    expires_at = now + timedelta(minutes=quiz.duration_minutes) if quiz.duration_minutes else None
    attempt = QuizAttempt(quiz_id=quiz_id, user_id=user_id, started_at=now, expires_at=expires_at)
    db.add(attempt)
    db.flush()
    return attempt.id


def _store(db: Session, attempt: QuizAttempt, responses: dict[int, str | None]) -> None:
    """Upsert the given answers; a blank answer clears an earlier one."""
    allowed = {link.question_id for link in _links(db, attempt.quiz_id)}
    unknown = sorted(set(responses) - allowed)
    if unknown:
        raise KeyError(unknown[0])
    saved = _saved(db, attempt.id)
    for question_id, response in responses.items():
        value = response if _answered(response) else None
        row = saved.get(question_id)
        if row is None:
            db.add(AttemptAnswer(attempt_id=attempt.id, question_id=question_id, response=value))
        else:
            row.response = value
    db.flush()


def _finalise(db: Session, attempt: QuizAttempt, now: datetime, *, auto: bool) -> None:
    """Score every question and close the attempt.

    Only answered questions are scored; the rest are recorded as unattempted (score 0,
    neither correct nor incorrect). Descriptive answers stay unscored for S08.1.
    """
    links = _links(db, attempt.quiz_id)
    views = questions.get_questions(db, (link.question_id for link in links))
    saved = _saved(db, attempt.id)
    total = earned = 0.0
    for link in links:
        view = views[link.question_id]
        row = saved.get(link.question_id)
        if row is None:
            row = AttemptAnswer(attempt_id=attempt.id, question_id=link.question_id)
            db.add(row)
        row.difficulty_served = view.difficulty.value
        total += link.points
        if not view.is_objective:
            row.score, row.is_correct = None, None
        elif not _answered(row.response):
            row.response, row.score, row.is_correct = None, 0.0, None
        else:
            row.score = score_objective(view, row.response, link.points)
            row.is_correct = row.score == link.points
            earned += row.score
    attempt.score, attempt.max_score = earned, total
    attempt.status = AttemptStatus.SUBMITTED
    attempt.auto_submitted = auto
    # An attempt closed by the timer is recorded as submitted at its limit.
    attempt.submitted_at = _aware(attempt.expires_at) if auto else now
    db.flush()


def save_answers(db: Session, attempt_id: int, responses: dict[int, str | None]) -> None:
    """Save answers while the attempt runs; an expired attempt is closed instead.

    Raises ``KeyError`` with the question id when a question is not in the quiz and
    ``ValueError`` when the attempt is already submitted.
    """
    attempt = _get(db, attempt_id)
    if attempt.status is AttemptStatus.SUBMITTED:
        raise ValueError("attempt already submitted")
    now = utcnow()
    if _expired(attempt, now):
        _finalise(db, attempt, now, auto=True)
        return
    _store(db, attempt, responses)


def unanswered(db: Session, attempt_id: int) -> list[int]:
    attempt = _get(db, attempt_id)
    saved = _saved(db, attempt.id)
    return [
        link.question_id
        for link in _links(db, attempt.quiz_id)
        if not _answered(saved[link.question_id].response if link.question_id in saved else None)
    ]


def submit(
    db: Session,
    attempt_id: int,
    responses: dict[int, str | None] | None = None,
    *,
    allow_unanswered: bool = True,
) -> list[int]:
    """Save the final answers and close the attempt (FR-06).

    Returns the unanswered question ids. When some are unanswered and
    ``allow_unanswered`` is false, the answers are saved but the attempt stays open so
    the caller can warn first (UT_12). Past the time limit (plus a short grace for
    latency) the attempt is closed with what was saved before the limit, whatever
    ``allow_unanswered`` says (ST_11).
    """
    attempt = _get(db, attempt_id)
    if attempt.status is AttemptStatus.SUBMITTED:
        raise ValueError("attempt already submitted")
    now = utcnow()
    if _expired(attempt, now, SUBMIT_GRACE):
        _finalise(db, attempt, now, auto=True)
        return unanswered(db, attempt_id)
    if responses:
        _store(db, attempt, responses)
    missing = unanswered(db, attempt_id)
    if missing and not allow_unanswered:
        return missing
    _finalise(db, attempt, now, auto=False)
    return missing


def close_if_expired(db: Session, attempt_id: int, grace: timedelta = timedelta(0)) -> bool:
    """Auto-submit the attempt if its timer (plus ``grace``) has run out; True if so."""
    attempt = _get(db, attempt_id)
    now = utcnow()
    if attempt.status is AttemptStatus.IN_PROGRESS and _expired(attempt, now, grace):
        _finalise(db, attempt, now, auto=True)
        return True
    return False


def _outcome(
    attempt: QuizAttempt,
    links: list[QuizQuestion],
    views: dict[int, questions.QuestionView],
    saved: dict[int, AttemptAnswer],
) -> AttemptOutcome:
    answers, counts = [], dict.fromkeys(AnswerStatus, 0)
    for link in links:
        view, row = views[link.question_id], saved.get(link.question_id)
        response = row.response if row else None
        if not view.is_objective:
            status = AnswerStatus.PENDING if _answered(response) else AnswerStatus.UNATTEMPTED
        elif not _answered(response):
            status = AnswerStatus.UNATTEMPTED
        else:
            status = AnswerStatus.CORRECT if row.is_correct else AnswerStatus.INCORRECT
        counts[status] += 1
        answers.append(
            AnswerOutcome(
                question_id=link.question_id,
                position=link.position,
                response=response,
                status=status,
                score=row.score if row else None,
                points=link.points,
                correct_answer=view.correct_answer,
                explanation=view.explanation,
            )
        )
    score, max_score = attempt.score or 0.0, attempt.max_score or 0.0
    percentage = round(score / max_score * 100, 1) if max_score else 0.0
    return AttemptOutcome(
        score=score,
        max_score=max_score,
        percentage=percentage,
        correct=counts[AnswerStatus.CORRECT],
        incorrect=counts[AnswerStatus.INCORRECT],
        unattempted=counts[AnswerStatus.UNATTEMPTED],
        pending_evaluation=counts[AnswerStatus.PENDING],
        submitted_at=_aware(attempt.submitted_at),
        answers=tuple(answers),
    )


def state(db: Session, attempt_id: int) -> AttemptState:
    """The attempt as its owner sees it: questions, saved answers, time left, result."""
    attempt = _get(db, attempt_id)
    quiz = db.get(Quiz, attempt.quiz_id)
    links = _links(db, attempt.quiz_id)
    views = questions.get_questions(db, (link.question_id for link in links))
    saved = _saved(db, attempt.id)
    responses = {qid: row.response for qid, row in saved.items()}
    expires_at = _aware(attempt.expires_at)
    remaining = None
    if expires_at is not None:
        if attempt.status is AttemptStatus.SUBMITTED:
            remaining = 0
        else:
            remaining = max(0, math.ceil((expires_at - utcnow()).total_seconds()))
    return AttemptState(
        id=attempt.id,
        quiz_id=attempt.quiz_id,
        quiz_title=quiz.title,
        user_id=attempt.user_id,
        status=attempt.status,
        started_at=_aware(attempt.started_at),
        expires_at=expires_at,
        remaining_seconds=remaining,
        auto_submitted=attempt.auto_submitted,
        questions=tuple(
            AttemptQuestion(
                question_id=link.question_id,
                position=link.position,
                text=views[link.question_id].text,
                question_type=views[link.question_id].question_type.value,
                options=views[link.question_id].options,
                points=link.points,
                response=responses.get(link.question_id),
            )
            for link in links
        ),
        unanswered_question_ids=tuple(
            link.question_id for link in links if not _answered(responses.get(link.question_id))
        ),
        outcome=(
            _outcome(attempt, links, views, saved)
            if attempt.status is AttemptStatus.SUBMITTED
            else None
        ),
    )
