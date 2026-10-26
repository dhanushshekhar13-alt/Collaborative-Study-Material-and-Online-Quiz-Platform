"""Public interface of the analytics module (S12.1, FR-12).

How the figures are kept
------------------------
Every time the quizzes module scores an attempt it publishes ``ATTEMPT_SCORED`` and
``record_attempt`` folds that attempt into the user's stored figures in the same
transaction: the attempt's ledger row (``performance_attempts``) and the running total
for its subject and topic (``performance_topics``). A later re-score (a descriptive
answer evaluated afterwards) replaces the attempt's earlier contribution, so nothing
is counted twice.

``sync_user`` re-runs that fold against the user's raw attempts before the dashboard
is read. It closes attempts whose timer ran out, adds anything the event missed,
corrects anything that changed and removes attempts that no longer exist; if a
running total still disagrees with the raw attempts it rebuilds the user's figures.
So the stored figures always reconcile with the attempt records. ``recompute`` derives the
same figures from the raw attempts without touching storage; ``rebuild_user`` throws
the stored figures away and rebuilds them from scratch.

How topics are classified
-------------------------
A topic needs at least ``MIN_ATTEMPTS`` attempts before it is labelled; until then it
is listed as "not enough data" rather than being called strong or weak. Accuracy is
points earned over points possible. At or above ``STRONG_AT`` percent it is a
strength, below ``WEAK_BELOW`` percent a weak area, and in between "developing".
"""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.core import events
from app.modules.analytics.models import PerformanceAttempt, PerformanceTopic
from app.modules.quizzes import service as quizzes

MIN_ATTEMPTS = 2
STRONG_AT = 75.0
WEAK_BELOW = 50.0
# Least-squares slope, in percentage points per attempt, that counts as a real trend.
TREND_STEP = 1.0

EMPTY_MESSAGE = "No quiz attempts yet. Attempt a quiz to see your performance here."


class Standing(StrEnum):
    STRONG = "strong"
    DEVELOPING = "developing"
    WEAK = "weak"
    NOT_ENOUGH_DATA = "not_enough_data"


class Direction(StrEnum):
    IMPROVING = "improving"
    STEADY = "steady"
    DECLINING = "declining"
    NOT_ENOUGH_DATA = "not_enough_data"


def _round(value: float) -> float:
    """Keep sums of 2-decimal points exact, so stored and recomputed figures match."""
    return round(value, 6)


def _percent(score: float, possible: float) -> float | None:
    return round(score / possible * 100, 1) if possible else None


# --------------------------------------------------------------------------- #
# Aggregates
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class TopicTotals:
    subject: str
    topic: str
    attempts: int
    total_score: float
    total_possible: float

    @property
    def accuracy(self) -> float | None:
        return _percent(self.total_score, self.total_possible)


@dataclass(frozen=True)
class PerformanceSummary:
    attempts: int
    total_score: float
    total_possible: float

    @property
    def accuracy(self) -> float:
        return self.total_score / self.total_possible if self.total_possible else 0.0


def recompute(db: Session, user_id: int) -> dict[tuple[str, str], TopicTotals]:
    """The user's per-topic figures derived from raw attempts alone (writes nothing)."""
    return _totals(quizzes.scored_attempts_for_user(db, user_id))


def _totals(scored: list[quizzes.ScoredAttempt]) -> dict[tuple[str, str], TopicTotals]:
    sums: dict[tuple[str, str], list[float]] = defaultdict(lambda: [0, 0.0, 0.0])
    for a in scored:
        entry = sums[(a.subject, a.topic)]
        entry[0] += 1
        entry[1] += a.score
        entry[2] += a.max_score
    return {
        key: TopicTotals(key[0], key[1], int(n), _round(score), _round(possible))
        for key, (n, score, possible) in sums.items()
    }


def stored(db: Session, user_id: int) -> dict[tuple[str, str], TopicTotals]:
    """The user's per-topic figures as currently stored."""
    rows = db.scalars(select(PerformanceTopic).where(PerformanceTopic.user_id == user_id))
    return {
        (r.subject, r.topic): TopicTotals(
            r.subject, r.topic, r.attempts, _round(r.total_score), _round(r.total_possible)
        )
        for r in rows
    }


def summary_for_user(db: Session, user_id: int) -> PerformanceSummary:
    """Overall attempt count and points from the stored figures."""
    totals = stored(db, user_id).values()
    return PerformanceSummary(
        attempts=sum(t.attempts for t in totals),
        total_score=_round(sum(t.total_score for t in totals)),
        total_possible=_round(sum(t.total_possible for t in totals)),
    )


# --------------------------------------------------------------------------- #
# Keeping the stored figures current
# --------------------------------------------------------------------------- #


def _same(row: PerformanceAttempt, a: quizzes.ScoredAttempt) -> bool:
    return (row.subject, row.topic, row.quiz_title, row.quiz_id) == (
        a.subject,
        a.topic,
        a.quiz_title,
        a.quiz_id,
    ) and (_round(row.score), _round(row.max_score)) == (_round(a.score), _round(a.max_score))


def _topic(db: Session, user_id: int, subject: str, topic: str) -> PerformanceTopic:
    row = db.scalar(
        select(PerformanceTopic).where(
            PerformanceTopic.user_id == user_id,
            PerformanceTopic.subject == subject,
            PerformanceTopic.topic == topic,
        )
    )
    if row is None:
        row = PerformanceTopic(
            user_id=user_id,
            subject=subject,
            topic=topic,
            attempts=0,
            total_score=0.0,
            total_possible=0.0,
        )
        db.add(row)
    return row


def _adjust(db: Session, user_id: int, subject: str, topic: str, sign: int, score, possible):
    row = _topic(db, user_id, subject, topic)
    row.attempts += sign
    row.total_score = _round(row.total_score + sign * score)
    row.total_possible = _round(row.total_possible + sign * possible)
    db.flush()
    if row.attempts <= 0:
        db.delete(row)
    else:
        row.last_attempt_at = db.scalar(
            select(func.max(PerformanceAttempt.submitted_at)).where(
                PerformanceAttempt.user_id == user_id,
                PerformanceAttempt.subject == subject,
                PerformanceAttempt.topic == topic,
            )
        )
    db.flush()


def _fold(db: Session, attempt_id: int, current: quizzes.ScoredAttempt | None) -> bool:
    """Make the stored figures reflect ``current`` for this attempt; True if changed."""
    old = db.get(PerformanceAttempt, attempt_id)
    if old is not None and current is not None and _same(old, current):
        return False
    if old is None and current is None:
        return False
    if old is not None:
        db.delete(old)
        db.flush()
        _adjust(db, old.user_id, old.subject, old.topic, -1, old.score, old.max_score)
    if current is not None:
        db.add(
            PerformanceAttempt(
                attempt_id=current.attempt_id,
                user_id=current.user_id,
                quiz_id=current.quiz_id,
                quiz_title=current.quiz_title,
                subject=current.subject,
                topic=current.topic,
                score=current.score,
                max_score=current.max_score,
                submitted_at=current.submitted_at,
            )
        )
        db.flush()
        _adjust(
            db, current.user_id, current.subject, current.topic, 1, current.score, current.max_score
        )
    return True


def record_attempt(db: Session, attempt_id: int) -> None:
    """Fold one scored attempt into its user's figures (the ``ATTEMPT_SCORED`` handler)."""
    _fold(db, attempt_id, quizzes.scored_attempt(db, attempt_id))


def _on_attempt_scored(db: Session, *, attempt_id: int) -> None:
    record_attempt(db, attempt_id)


events.subscribe(quizzes.ATTEMPT_SCORED, _on_attempt_scored)


def sync_user(db: Session, user_id: int) -> int:
    """Reconcile the user's stored figures with their raw attempts; returns rows fixed."""
    quizzes.close_expired_attempts(db, user_id)
    current = {a.attempt_id: a for a in quizzes.scored_attempts_for_user(db, user_id)}
    ledger = set(
        db.scalars(
            select(PerformanceAttempt.attempt_id).where(PerformanceAttempt.user_id == user_id)
        )
    )
    changed = 0
    for attempt_id in sorted(ledger | set(current)):
        changed += _fold(db, attempt_id, current.get(attempt_id))
    if stored(db, user_id) != _totals(list(current.values())):
        # A running total no longer matches its ledger (edited by hand, say): rebuild.
        rebuild_user(db, user_id)
        changed += 1
    return changed


def rebuild_user(db: Session, user_id: int) -> None:
    """Discard the user's stored figures and rebuild them from raw attempts."""
    db.execute(delete(PerformanceAttempt).where(PerformanceAttempt.user_id == user_id))
    db.execute(delete(PerformanceTopic).where(PerformanceTopic.user_id == user_id))
    db.flush()
    for a in quizzes.scored_attempts_for_user(db, user_id):
        _fold(db, a.attempt_id, a)


# --------------------------------------------------------------------------- #
# The dashboard
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class TopicPerformance:
    subject: str
    topic: str
    attempts: int
    total_score: float
    total_possible: float
    accuracy: float | None
    standing: Standing


@dataclass(frozen=True)
class TrendPoint:
    attempt_id: int
    quiz_id: int
    quiz_title: str
    subject: str
    topic: str
    submitted_at: datetime
    percentage: float
    running_accuracy: float


@dataclass(frozen=True)
class Trend:
    points: list[TrendPoint]
    slope: float | None
    direction: Direction
    # The fitted least-squares line at the first and last point, for drawing.
    line_start: float | None
    line_end: float | None


@dataclass(frozen=True)
class SubjectPerformance:
    subject: str
    attempts: int
    total_score: float
    total_possible: float
    accuracy: float | None
    topics: list[TopicPerformance]
    trend: Trend


@dataclass(frozen=True)
class Dashboard:
    attempts: int
    total_score: float
    total_possible: float
    accuracy: float | None
    subjects: list[SubjectPerformance]
    trend: Trend
    strengths: list[TopicPerformance]
    weak_areas: list[TopicPerformance]
    not_enough_data: list[TopicPerformance]
    empty_message: str | None
    min_attempts: int = MIN_ATTEMPTS
    strong_at: float = STRONG_AT
    weak_below: float = WEAK_BELOW
    topics: list[TopicPerformance] = field(default_factory=list)


def classify(attempts: int, accuracy: float | None) -> Standing:
    """Strong, developing or weak by accuracy; too few attempts are never labelled."""
    if attempts < MIN_ATTEMPTS or accuracy is None:
        return Standing.NOT_ENOUGH_DATA
    if accuracy >= STRONG_AT:
        return Standing.STRONG
    if accuracy < WEAK_BELOW:
        return Standing.WEAK
    return Standing.DEVELOPING


def trend(points: list[TrendPoint]) -> Trend:
    """Least-squares line through the attempt percentages, in attempt order."""
    n = len(points)
    if n < 2:
        return Trend(points, None, Direction.NOT_ENOUGH_DATA, None, None)
    ys = [p.percentage for p in points]
    mean_x, mean_y = (n - 1) / 2, sum(ys) / n
    sxx = sum((x - mean_x) ** 2 for x in range(n))
    slope = sum((x - mean_x) * (y - mean_y) for x, y in enumerate(ys)) / sxx
    intercept = mean_y - slope * mean_x
    if slope >= TREND_STEP:
        direction = Direction.IMPROVING
    elif slope <= -TREND_STEP:
        direction = Direction.DECLINING
    else:
        direction = Direction.STEADY
    return Trend(
        points,
        round(slope, 2),
        direction,
        round(intercept, 1),
        round(intercept + slope * (n - 1), 1),
    )


def _points(rows: list[PerformanceAttempt]) -> list[TrendPoint]:
    out, score, possible = [], 0.0, 0.0
    for r in rows:
        score += r.score
        possible += r.max_score
        out.append(
            TrendPoint(
                attempt_id=r.attempt_id,
                quiz_id=r.quiz_id,
                quiz_title=r.quiz_title,
                subject=r.subject,
                topic=r.topic,
                submitted_at=r.submitted_at,
                percentage=_percent(r.score, r.max_score) or 0.0,
                running_accuracy=_percent(score, possible) or 0.0,
            )
        )
    return out


def _topic_view(t: TopicTotals) -> TopicPerformance:
    return TopicPerformance(
        t.subject,
        t.topic,
        t.attempts,
        t.total_score,
        t.total_possible,
        t.accuracy,
        classify(t.attempts, t.accuracy),
    )


def dashboard(db: Session, user_id: int) -> Dashboard:
    """Performance by subject and topic with trends and strong/weak topics.

    Reconciles the stored figures with the raw attempts first (``sync_user``), so the
    dashboard always includes the latest attempt.
    """
    sync_user(db, user_id)
    ledger = list(
        db.scalars(
            select(PerformanceAttempt)
            .where(PerformanceAttempt.user_id == user_id)
            .order_by(PerformanceAttempt.submitted_at, PerformanceAttempt.attempt_id)
        )
    )
    topics = sorted(
        (_topic_view(t) for t in stored(db, user_id).values()),
        key=lambda t: (t.subject.lower(), t.topic.lower()),
    )
    by_subject: dict[str, list[TopicPerformance]] = defaultdict(list)
    for t in topics:
        by_subject[t.subject].append(t)
    subjects = []
    for name, items in sorted(by_subject.items(), key=lambda kv: kv[0].lower()):
        score = _round(sum(t.total_score for t in items))
        possible = _round(sum(t.total_possible for t in items))
        subjects.append(
            SubjectPerformance(
                subject=name,
                attempts=sum(t.attempts for t in items),
                total_score=score,
                total_possible=possible,
                accuracy=_percent(score, possible),
                topics=items,
                trend=trend(_points([r for r in ledger if r.subject == name])),
            )
        )
    total = summary_for_user(db, user_id)
    ranked = sorted(topics, key=lambda t: -(t.accuracy or 0))
    return Dashboard(
        attempts=total.attempts,
        total_score=total.total_score,
        total_possible=total.total_possible,
        accuracy=_percent(total.total_score, total.total_possible),
        subjects=subjects,
        trend=trend(_points(ledger)),
        strengths=[t for t in ranked if t.standing is Standing.STRONG],
        weak_areas=[t for t in reversed(ranked) if t.standing is Standing.WEAK],
        not_enough_data=[t for t in topics if t.standing is Standing.NOT_ENOUGH_DATA],
        empty_message=None if ledger else EMPTY_MESSAGE,
        topics=topics,
    )
