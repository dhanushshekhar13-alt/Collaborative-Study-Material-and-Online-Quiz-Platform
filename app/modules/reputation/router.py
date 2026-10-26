"""HTTP surface of the reputation module (S13.1), mounted under ``/api/v1``.

``/leaderboard`` ranks contributors by reputation, ``/reputation/me`` shows the caller's
total, rank, badges and audited event history, and ``/badges`` lists the badge levels.
"""

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.modules.accounts.service import UserSummary, current_user
from app.modules.reputation import service

router = APIRouter(tags=["reputation"])


class LeaderboardRow(BaseModel):
    rank: int
    user_id: int
    display_name: str
    username: str | None
    reputation: int
    badges: list[str]


class MyStanding(BaseModel):
    rank: int | None
    reputation: int


class LeaderboardResponse(BaseModel):
    entries: list[LeaderboardRow]
    total: int
    me: MyStanding


class EarnedBadgeResponse(BaseModel):
    code: str
    name: str
    description: str | None
    earned_at: datetime


class ReputationEventResponse(BaseModel):
    id: int
    points: int
    reason: str
    source_type: str | None
    source_id: int | None
    created_at: datetime


class MyReputationResponse(BaseModel):
    reputation: int
    rank: int | None
    badges: list[EarnedBadgeResponse]
    events: list[ReputationEventResponse]
    event_count: int


class BadgeLevelResponse(BaseModel):
    code: str
    name: str
    description: str
    threshold: int
    earned: bool


def _standing(db: Session, user_id: int) -> MyStanding:
    return MyStanding(rank=service.rank_of(db, user_id), reputation=service.total(db, user_id))


@router.get("/leaderboard", response_model=LeaderboardResponse)
def read_leaderboard(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    user: UserSummary = Depends(current_user),
    db: Session = Depends(get_db),
) -> LeaderboardResponse:
    """Contributors ranked by current reputation, highest first (UT_26).

    Only users with positive reputation are listed; equal totals share a rank.
    ``me`` is the caller's own rank (``null`` until they have reputation).
    """
    entries, count = service.leaderboard(db, limit=limit, offset=offset)
    return LeaderboardResponse(
        entries=[LeaderboardRow(**vars(e)) for e in entries],
        total=count,
        me=_standing(db, user.id),
    )


@router.get("/reputation/me", response_model=MyReputationResponse)
def read_my_reputation(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    user: UserSummary = Depends(current_user),
    db: Session = Depends(get_db),
) -> MyReputationResponse:
    """The caller's reputation, rank, badges and the events behind the total, newest first."""
    events, count = service.events_for(db, user.id, limit=limit, offset=offset)
    standing = _standing(db, user.id)
    return MyReputationResponse(
        reputation=standing.reputation,
        rank=standing.rank,
        badges=[EarnedBadgeResponse(**vars(b)) for b in service.badges_for(db, user.id)],
        events=[ReputationEventResponse(**vars(e)) for e in events],
        event_count=count,
    )


@router.get("/badges", response_model=list[BadgeLevelResponse])
def read_badges(
    user: UserSummary = Depends(current_user), db: Session = Depends(get_db)
) -> list[BadgeLevelResponse]:
    """Every badge level, lowest threshold first, with whether the caller has earned it."""
    earned = {b.code for b in service.badges_for(db, user.id)}
    return [
        BadgeLevelResponse(
            code=level.code,
            name=level.name,
            description=level.description,
            threshold=level.threshold,
            earned=level.code in earned,
        )
        for level in service.BADGE_LEVELS
    ]
