"""Quiz attempts: timer, saved answers, the unanswered check and scoring (S06.1, FR-06).

The server owns the clock. An attempt's time limit (``expires_at``) is fixed when it
starts, and whenever an expired attempt is read or written it is closed with the
answers saved so far, so a client that stops ticking cannot extend the time.

Objective questions are scored here with the pure functions in ``scoring.py``, never
by the LLM (NFR-06). Descriptive answers are stored unscored; the LLM module evaluates
them afterwards and records each result with ``record_descriptive`` (S08.2), which can
only ever write descriptive answers. Other modules reach this through
``app.modules.quizzes.service``.
"""

import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import events
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

# Published (with ``attempt_id``) whenever an attempt's score is set or changes: on
# submit, on auto-submit at the time limit and when a descriptive answer is evaluated.
# Analytics (S12.1) folds it into the user's stored performance figures.
ATTEMPT_SCORED = "quizzes.attempt_scored"


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
    PENDING = "pending_evaluation"  # descriptive, not evaluated yet
    EVALUATED = "evaluated"  # descriptive, scored by the LLM module (S08.2)


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
    question_type: str = ""
    # Descriptive only: the stored 0-10 evaluation with its concept feedback (S08.2).
    evaluation: dict[str, Any] | None = None


@dataclass(frozen=True)
class ScorePart:
    """Points earned out of points available, for one part of an attempt."""

    score: float
    max_score: float

    @property
    def percentage(self) -> float:
        return round(self.score / self.max_score * 100, 1) if self.max_score else 0.0

    @property
    def display(self) -> str:
        return f"{self.score:g}/{self.max_score:g} ({self.percentage:g}%)"


@dataclass(frozen=True)
class ScoreBreakdown:
    """An attempt's total split into its deterministic and its evaluated part (S08.2).

    ``objective`` comes only from the answer key (``scoring.py``); ``descriptive`` only
    from stored evaluations, each 0-10 result scaled to the question's points. The
    total is their sum, so neither part can change the other (NFR-06).
    """

    objective: ScorePart
    descriptive: ScorePart
    pending_evaluation: int

    @property
    def total(self) -> ScorePart:
        return ScorePart(
            _points(self.objective.score + self.descriptive.score),
            _points(self.objective.max_score + self.descriptive.max_score),
        )

    @property
    def explanation(self) -> str:
        """How the total is made, e.g. "Total 3.5/4 = objective 3/3 + descriptive 0.5/1 …"."""
        total, obj, desc = self.total, self.objective, self.descriptive
        text = (
            f"Total {total.score:g}/{total.max_score:g} = objective {obj.score:g}/{obj.max_score:g}"
        )
        if desc.max_score:
            text += (
                f" (marked automatically against the answer key) + descriptive "
                f"{desc.score:g}/{desc.max_score:g} (each written answer's 0-10 evaluation "
                "scaled to the question's points)."
            )
        else:
            text += " (marked automatically against the answer key)."
        if self.pending_evaluation:
            text += (
                f" {pending_text(self.pending_evaluation)} awaiting evaluation and "
                "counting 0 until then."
            )
        return text


def pending_text(count: int) -> str:
    return f"{count} descriptive answer is" if count == 1 else f"{count} descriptive answers are"


def _points(value: float) -> float:
    """Round away float noise (0.1 + 0.2) so totals display cleanly."""
    return round(value, 2)


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
    evaluated: int = 0
    breakdown: ScoreBreakdown | None = None

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
    events.publish(ATTEMPT_SCORED, db, attempt_id=attempt.id)


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
            if not _answered(response):
                status = AnswerStatus.UNATTEMPTED
            elif row.feedback is not None and row.score is not None:
                status = AnswerStatus.EVALUATED
            else:
                status = AnswerStatus.PENDING
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
                question_type=view.question_type.value,
                evaluation=(dict(row.feedback) if status is AnswerStatus.EVALUATED else None),
            )
        )
    score, max_score = attempt.score or 0.0, attempt.max_score or 0.0
    percentage = round(score / max_score * 100, 1) if max_score else 0.0
    return AttemptOutcome(
        evaluated=counts[AnswerStatus.EVALUATED],
        breakdown=_breakdown(links, views, saved),
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


# --------------------------------------------------------------------------- #
# Descriptive results beside objective scoring (S08.2, FR-08, NFR-06)
# --------------------------------------------------------------------------- #


def _breakdown(
    links: list[QuizQuestion],
    views: dict[int, questions.QuestionView],
    saved: dict[int, AttemptAnswer],
) -> ScoreBreakdown:
    obj = obj_max = desc = desc_max = 0.0
    pending = 0
    for link in links:
        row = saved.get(link.question_id)
        earned = row.score if row is not None and row.score is not None else 0.0
        if views[link.question_id].is_objective:
            obj, obj_max = obj + earned, obj_max + link.points
            continue
        desc, desc_max = desc + earned, desc_max + link.points
        if row is not None and _answered(row.response) and row.score is None:
            pending += 1
    return ScoreBreakdown(
        ScorePart(_points(obj), _points(obj_max)),
        ScorePart(_points(desc), _points(desc_max)),
        pending,
    )


@dataclass(frozen=True)
class PendingDescriptive:
    """A written answer in a submitted attempt that has not been evaluated yet."""

    attempt_id: int
    question_id: int
    response: str
    points: float
    channel_id: int | None


class NotSubmittedError(ValueError):
    """The attempt is still running, so its written answers cannot be evaluated yet."""


def pending_descriptive(db: Session, attempt_id: int) -> list[PendingDescriptive]:
    """The attempt's answered descriptive questions still awaiting evaluation, in order.

    Raises ``LookupError`` for an unknown attempt and ``NotSubmittedError`` while it is
    still running (answers can change until submission, so nothing is evaluated early).
    """
    attempt = _get(db, attempt_id)
    if attempt.status is not AttemptStatus.SUBMITTED:
        raise NotSubmittedError("attempt not submitted")
    quiz = db.get(Quiz, attempt.quiz_id)
    links = _links(db, attempt.quiz_id)
    views = questions.get_questions(db, (link.question_id for link in links))
    saved = _saved(db, attempt_id)
    found = []
    for link in links:
        row = saved.get(link.question_id)
        if views[link.question_id].is_objective or row is None:
            continue
        if _answered(row.response) and row.score is None:
            found.append(
                PendingDescriptive(
                    attempt_id, link.question_id, row.response, link.points, quiz.channel_id
                )
            )
    return found


def record_descriptive(
    db: Session,
    attempt_id: int,
    question_id: int,
    *,
    score: float,
    max_score: float,
    feedback: dict[str, Any],
) -> float:
    """Store one descriptive evaluation and add it to the attempt total; returns the points.

    The ``score`` out of ``max_score`` (0-10) is scaled to the question's points in the
    quiz. Only a submitted attempt's descriptive answers can be written here: an
    objective question raises ``ValueError``, so the LLM module can never change the
    deterministic marks (NFR-06). Recording the same answer again replaces it.
    """
    attempt = _get(db, attempt_id)
    if attempt.status is not AttemptStatus.SUBMITTED:
        raise NotSubmittedError("attempt not submitted")
    link = db.scalar(
        select(QuizQuestion).where(
            QuizQuestion.quiz_id == attempt.quiz_id, QuizQuestion.question_id == question_id
        )
    )
    row = _saved(db, attempt_id).get(question_id)
    if link is None or row is None or not _answered(row.response):
        raise KeyError(question_id)
    if questions.get_questions(db, [question_id])[question_id].is_objective:
        raise ValueError("objective answers are scored deterministically, never evaluated")
    if max_score <= 0 or not 0 <= score <= max_score:
        raise ValueError(f"score {score} is outside 0-{max_score}")
    earned = _points(link.points * score / max_score)
    previous = row.score or 0.0
    row.score, row.is_correct = earned, None
    row.feedback = {**feedback, "score": score, "max_score": max_score}
    attempt.score = _points((attempt.score or 0.0) - previous + earned)
    db.flush()
    events.publish(ATTEMPT_SCORED, db, attempt_id=attempt.id)
    return earned


def breakdown(db: Session, attempt_id: int) -> ScoreBreakdown:
    attempt = _get(db, attempt_id)
    links = _links(db, attempt.quiz_id)
    views = questions.get_questions(db, (link.question_id for link in links))
    return _breakdown(links, views, _saved(db, attempt_id))


# --------------------------------------------------------------------------- #
# My Quiz History (S06.2, FR-06, NFR-04)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class HistoryEntry:
    """One submitted attempt as listed in My Quiz History."""

    attempt_id: int
    quiz_id: int
    quiz_title: str
    subject: str
    topic: str
    started_at: datetime
    submitted_at: datetime | None
    auto_submitted: bool
    score: float
    max_score: float
    percentage: float
    breakdown: ScoreBreakdown | None = None

    @property
    def display(self) -> str:
        return f"{self.score:g}/{self.max_score:g} ({self.percentage:g}%)"


@dataclass(frozen=True)
class QuizHistory:
    """The user's submitted attempts, newest first, with their aggregate accuracy.

    ``average_accuracy`` is the mean of the attempts' percentages, so every attempt
    counts once whatever its quiz length: 60, 80, 100 and 90 percent give 82.5.
    """

    entries: tuple[HistoryEntry, ...]
    average_accuracy: float | None
    best_percentage: float | None


def _percentage(score: float, max_score: float) -> float:
    return score / max_score * 100 if max_score else 0.0


def close_expired_for_user(db: Session, user_id: int) -> int:
    """Auto-submit every attempt of the user whose timer has run out; returns how many.

    The server owns the clock, so an attempt abandoned when its time ran out shows up
    in the history with the answers saved before the limit, without being reopened.
    """
    now = utcnow()
    rows = db.scalars(
        select(QuizAttempt).where(
            QuizAttempt.user_id == user_id,
            QuizAttempt.status == AttemptStatus.IN_PROGRESS,
            QuizAttempt.expires_at.is_not(None),
        )
    ).all()
    closed = 0
    for attempt in rows:
        if _expired(attempt, now):
            _finalise(db, attempt, now, auto=True)
            closed += 1
    return closed


def history(db: Session, user_id: int, quiz_id: int | None = None) -> QuizHistory:
    """The user's submitted attempts (optionally at one quiz), newest first."""
    query = (
        select(QuizAttempt, Quiz)
        .join(Quiz, Quiz.id == QuizAttempt.quiz_id)
        .where(QuizAttempt.user_id == user_id, QuizAttempt.status == AttemptStatus.SUBMITTED)
        .order_by(QuizAttempt.submitted_at.desc(), QuizAttempt.id.desc())
    )
    if quiz_id is not None:
        query = query.where(QuizAttempt.quiz_id == quiz_id)
    rows = list(db.execute(query))
    breakdowns = _breakdowns(db, [attempt for attempt, _ in rows])
    entries = []
    for attempt, quiz in rows:
        score, max_score = attempt.score or 0.0, attempt.max_score or 0.0
        entries.append(
            HistoryEntry(
                attempt_id=attempt.id,
                quiz_id=quiz.id,
                quiz_title=quiz.title,
                subject=quiz.subject,
                topic=quiz.topic,
                started_at=_aware(attempt.started_at),
                submitted_at=_aware(attempt.submitted_at),
                auto_submitted=attempt.auto_submitted,
                score=score,
                max_score=max_score,
                percentage=round(_percentage(score, max_score), 1),
                breakdown=breakdowns[attempt.id],
            )
        )
    exact = [_percentage(e.score, e.max_score) for e in entries]
    return QuizHistory(
        entries=tuple(entries),
        average_accuracy=round(sum(exact) / len(exact), 1) if exact else None,
        best_percentage=round(max(exact), 1) if exact else None,
    )


def _breakdowns(db: Session, listed: list[QuizAttempt]) -> dict[int, ScoreBreakdown]:
    """The score breakdown of each listed attempt, in a fixed number of queries."""
    if not listed:
        return {}
    quiz_ids = {a.quiz_id for a in listed}
    links: dict[int, list[QuizQuestion]] = {qid: [] for qid in quiz_ids}
    for link in db.scalars(
        select(QuizQuestion)
        .where(QuizQuestion.quiz_id.in_(sorted(quiz_ids)))
        .order_by(QuizQuestion.position, QuizQuestion.id)
    ):
        links[link.quiz_id].append(link)
    saved: dict[int, dict[int, AttemptAnswer]] = {a.id: {} for a in listed}
    for row in db.scalars(select(AttemptAnswer).where(AttemptAnswer.attempt_id.in_(list(saved)))):
        saved[row.attempt_id][row.question_id] = row
    views = questions.get_questions(
        db, {link.question_id for quiz_links in links.values() for link in quiz_links}
    )
    return {a.id: _breakdown(links[a.quiz_id], views, saved[a.id]) for a in listed}
