"""Public interface of the reputation module (FR-13)."""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modules.accounts import service as accounts
from app.modules.reputation.models import Badge, ReputationEvent, UserBadge


def award(
    db: Session,
    *,
    user_id: int,
    points: int,
    reason: str,
    source_type: str | None = None,
    source_id: int | None = None,
) -> None:
    """Record a reputation change and update the user's cached total."""
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


def total(db: Session, user_id: int) -> int:
    return db.scalar(
        select(func.coalesce(func.sum(ReputationEvent.points), 0)).where(
            ReputationEvent.user_id == user_id
        )
    )


def ensure_badge(db: Session, code: str, name: str, description: str | None = None) -> int:
    badge = db.scalar(select(Badge).where(Badge.code == code))
    if badge is None:
        badge = Badge(code=code, name=name, description=description)
        db.add(badge)
        db.flush()
    return badge.id


def grant_badge(db: Session, user_id: int, badge_id: int) -> None:
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
