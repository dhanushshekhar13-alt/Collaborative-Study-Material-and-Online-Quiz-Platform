"""Public interface of the reputation module (FR-01, FR-13).

S13.1 adds peer-rating reputation, threshold badges and the contributor leaderboard.
A user's reputation is always the sum of their ``reputation_events``; every change is
one event carrying its reason and source, so any total can be audited back to the
ratings that produced it.
"""

from collections.abc import Collection
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.modules.accounts import service as accounts
from app.modules.reputation.models import Badge, ReputationEvent, UserBadge

PEER_RATING = "peer_rating"
PEER_RATING_WITHDRAWN = "peer_rating_withdrawn"


@dataclass(frozen=True)
class BadgeLevel:
    """A badge awarded automatically once a user's reputation reaches ``threshold``."""

    code: str
    name: str
    description: str
    threshold: int


# Lowest first. Badges are kept once earned, even if reputation later drops.
BADGE_LEVELS: tuple[BadgeLevel, ...] = (
    BadgeLevel(
        "rising-contributor",
        "Rising Contributor",
        "Reached 75 reputation from well-rated contributions",
        75,
    ),
    BadgeLevel(
        "top-contributor",
        "Top Contributor",
        "Reached 100 reputation from well-rated contributions",
        100,
    ),
    BadgeLevel(
        "community-champion",
        "Community Champion",
        "Reached 250 reputation from well-rated contributions",
        250,
    ),
)


def award(
    db: Session,
    *,
    user_id: int,
    points: int,
    reason: str,
    source_type: str | None = None,
    source_id: int | None = None,
) -> None:
    """Record a reputation change, update the cached total and award any new badges."""
    db.add(
        ReputationEvent(
            user_id=user_id,
            points=points,
            reason=reason,
            source_type=source_type,
            source_id=source_id,
        )
    )
    accounts.add_reputation(db, user_id, points)
    db.flush()
    award_threshold_badges(db, user_id)


def total(db: Session, user_id: int) -> int:
    """Return the user's total reputation points."""
    return db.scalar(
        select(func.coalesce(func.sum(ReputationEvent.points), 0)).where(
            ReputationEvent.user_id == user_id
        )
    )


def ensure_badge(db: Session, code: str, name: str, description: str | None = None) -> int:
    """Return the id of the badge with ``code``, creating it if needed."""
    badge = db.scalar(select(Badge).where(Badge.code == code))
    if badge is None:
        badge = Badge(code=code, name=name, description=description)
        db.add(badge)
        db.flush()
    return badge.id


def grant_badge(db: Session, user_id: int, badge_id: int) -> None:
    """Award the badge to the user; does nothing if they already hold it."""
    exists = db.scalar(
        select(UserBadge.id).where(UserBadge.user_id == user_id, UserBadge.badge_id == badge_id)
    )
    if exists is None:
        db.add(UserBadge(user_id=user_id, badge_id=badge_id))
        db.flush()


@dataclass(frozen=True)
class EarnedBadge:
    code: str
    name: str
    description: str | None
    earned_at: datetime


def badges_for(db: Session, user_id: int) -> list[EarnedBadge]:
    """Badges the user has earned, most recent first (S01.2 profile)."""
    rows = db.execute(
        select(Badge, UserBadge.created_at)
        .join(UserBadge, UserBadge.badge_id == Badge.id)
        .where(UserBadge.user_id == user_id)
        .order_by(UserBadge.created_at.desc(), Badge.id.desc())
    )
    return [EarnedBadge(b.code, b.name, b.description, earned_at) for b, earned_at in rows]


# --------------------------------------------------------------------------- #
# S13.1 - peer ratings, threshold badges and the leaderboard
# --------------------------------------------------------------------------- #


def record_peer_rating(
    db: Session,
    *,
    contributor_id: int,
    rater_id: int,
    rating: int,
    source_type: str,
    source_id: int,
) -> int:
    """Bring the contributor's reputation in line with one peer's rating. Caller commits.

    ``source_type``/``source_id`` identify the rating row (one per rater and item), so a
    rating counts once however often it is changed: a positive rating (at least
    ``settings.reputation_positive_rating`` stars) is worth
    ``settings.reputation_points_per_positive_rating`` points; lowering it below that
    withdraws them with a compensating event. A self-rating never changes reputation.
    Returns the change in points (0 when nothing changed).
    """
    if contributor_id == rater_id:
        return 0
    earned = db.scalar(
        select(func.coalesce(func.sum(ReputationEvent.points), 0)).where(
            ReputationEvent.user_id == contributor_id,
            ReputationEvent.source_type == source_type,
            ReputationEvent.source_id == source_id,
        )
    )
    target = (
        settings.reputation_points_per_positive_rating
        if rating >= settings.reputation_positive_rating
        else 0
    )
    change = target - earned
    if change:
        award(
            db,
            user_id=contributor_id,
            points=change,
            reason=PEER_RATING if change > 0 else PEER_RATING_WITHDRAWN,
            source_type=source_type,
            source_id=source_id,
        )
    return change


def award_threshold_badges(db: Session, user_id: int) -> list[str]:
    """Grant every badge whose threshold the user's total has reached; returns new codes."""
    current = total(db, user_id)
    earned = {b.code for b in badges_for(db, user_id)}
    granted = []
    for level in BADGE_LEVELS:
        if current >= level.threshold and level.code not in earned:
            grant_badge(db, user_id, ensure_badge(db, level.code, level.name, level.description))
            granted.append(level.code)
    return granted


@dataclass(frozen=True)
class ReputationEntry:
    """One audited reputation change."""

    id: int
    points: int
    reason: str
    source_type: str | None
    source_id: int | None
    created_at: datetime


def events_for(
    db: Session, user_id: int, *, limit: int = 50, offset: int = 0
) -> tuple[list[ReputationEntry], int]:
    """The user's reputation events, newest first, and how many there are."""
    where = ReputationEvent.user_id == user_id
    count = db.scalar(select(func.count(ReputationEvent.id)).where(where)) or 0
    rows = db.scalars(
        select(ReputationEvent)
        .where(where)
        .order_by(ReputationEvent.created_at.desc(), ReputationEvent.id.desc())
        .limit(limit)
        .offset(offset)
    )
    return [
        ReputationEntry(e.id, e.points, e.reason, e.source_type, e.source_id, e.created_at)
        for e in rows
    ], count


@dataclass(frozen=True)
class LeaderboardEntry:
    rank: int
    user_id: int
    display_name: str
    username: str | None
    reputation: int
    badges: list[str]


def _totals():
    return (
        select(
            ReputationEvent.user_id.label("user_id"),
            func.sum(ReputationEvent.points).label("points"),
        )
        .group_by(ReputationEvent.user_id)
        .having(func.sum(ReputationEvent.points) > 0)
        .subquery()
    )


def badge_codes_for(db: Session, user_ids: Collection[int]) -> dict[int, list[str]]:
    """Earned badge codes per user, in threshold order then by code."""
    ids = list(user_ids)
    order = {level.code: i for i, level in enumerate(BADGE_LEVELS)}
    result: dict[int, list[str]] = {i: [] for i in ids}
    if not ids:
        return result
    rows = db.execute(
        select(UserBadge.user_id, Badge.code)
        .join(Badge, Badge.id == UserBadge.badge_id)
        .where(UserBadge.user_id.in_(ids))
    )
    for user_id, code in rows:
        result[user_id].append(code)
    for codes in result.values():
        codes.sort(key=lambda c: (order.get(c, len(order)), c))
    return result


def leaderboard(
    db: Session, *, limit: int = 20, offset: int = 0
) -> tuple[list[LeaderboardEntry], int]:
    """Users with positive reputation, highest first, and how many there are.

    Ranks follow the current event totals; users with equal totals share a rank and
    are listed by user id.
    """
    totals = _totals()
    rank = func.rank().over(order_by=desc(totals.c.points)).label("rank")
    count = db.scalar(select(func.count()).select_from(totals)) or 0
    rows = db.execute(
        select(totals.c.user_id, totals.c.points, rank)
        .order_by(desc(totals.c.points), totals.c.user_id)
        .limit(limit)
        .offset(offset)
    ).all()
    ids = [r.user_id for r in rows]
    users = accounts.get_users(db, ids)
    badges = badge_codes_for(db, ids)
    return [
        LeaderboardEntry(
            rank=r.rank,
            user_id=r.user_id,
            display_name=users[r.user_id].display_name,
            username=users[r.user_id].username,
            reputation=int(r.points),
            badges=badges[r.user_id],
        )
        for r in rows
        if r.user_id in users
    ], count


def rank_of(db: Session, user_id: int) -> int | None:
    """The user's leaderboard rank, or ``None`` while they have no positive reputation."""
    mine = total(db, user_id)
    if mine <= 0:
        return None
    totals = _totals()
    ahead = db.scalar(select(func.count()).select_from(totals).where(totals.c.points > mine))
    return (ahead or 0) + 1
