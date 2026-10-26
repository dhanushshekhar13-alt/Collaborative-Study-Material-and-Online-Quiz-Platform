"""HTTP surface of the analytics module (S12.1), mounted under ``/api/v1``.

Both endpoints show the signed-in user's own performance only.
"""

from dataclasses import asdict
from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.modules.accounts.service import UserSummary, current_user
from app.modules.analytics import service
from app.modules.analytics.service import Direction, Standing

router = APIRouter(prefix="/analytics", tags=["analytics"])


class TopicResponse(BaseModel):
    subject: str
    topic: str
    attempts: int
    total_score: float
    total_possible: float
    accuracy: float | None = Field(description="Percent of points earned; null if none possible")
    standing: Standing


class TrendPointResponse(BaseModel):
    attempt_id: int
    quiz_id: int
    quiz_title: str
    subject: str
    topic: str
    submitted_at: datetime
    percentage: float
    running_accuracy: float = Field(description="Accuracy over every attempt up to this one")


class TrendResponse(BaseModel):
    points: list[TrendPointResponse]
    slope: float | None = Field(description="Least-squares slope, percentage points per attempt")
    direction: Direction
    line_start: float | None
    line_end: float | None


class SubjectResponse(BaseModel):
    subject: str
    attempts: int
    total_score: float
    total_possible: float
    accuracy: float | None
    topics: list[TopicResponse]
    trend: TrendResponse


class ThresholdsResponse(BaseModel):
    min_attempts: int = Field(description="Attempts a topic needs before it is labelled")
    strong_at: float = Field(description="Accuracy (percent) at or above which a topic is strong")
    weak_below: float = Field(description="Accuracy (percent) below which a topic is weak")


class DashboardResponse(BaseModel):
    attempts: int
    total_score: float
    total_possible: float
    accuracy: float | None
    empty: bool
    empty_message: str | None
    subjects: list[SubjectResponse]
    topics: list[TopicResponse] = Field(description="Every topic, by subject then topic")
    trend: TrendResponse
    strengths: list[TopicResponse]
    weak_areas: list[TopicResponse]
    not_enough_data: list[TopicResponse]
    thresholds: ThresholdsResponse


class ReconciliationRow(BaseModel):
    subject: str
    topic: str
    stored_attempts: int
    stored_score: float
    stored_possible: float
    recomputed_attempts: int
    recomputed_score: float
    recomputed_possible: float


class ReconciliationResponse(BaseModel):
    matches: bool
    topics: list[ReconciliationRow]


@router.get("/dashboard", response_model=DashboardResponse)
def my_dashboard(
    user: UserSummary = Depends(current_user), db: Session = Depends(get_db)
) -> DashboardResponse:
    """The caller's performance by subject and topic, trends and strong/weak topics."""
    found = service.dashboard(db, user.id)
    db.commit()  # keep any figures the sync corrected
    body = asdict(found)
    return DashboardResponse(
        **{k: body[k] for k in ("attempts", "total_score", "total_possible", "accuracy")},
        empty=found.empty_message is not None,
        empty_message=found.empty_message,
        subjects=body["subjects"],
        topics=body["topics"],
        trend=body["trend"],
        strengths=body["strengths"],
        weak_areas=body["weak_areas"],
        not_enough_data=body["not_enough_data"],
        thresholds=ThresholdsResponse(
            min_attempts=found.min_attempts,
            strong_at=found.strong_at,
            weak_below=found.weak_below,
        ),
    )


@router.get("/reconciliation", response_model=ReconciliationResponse)
def my_reconciliation(
    user: UserSummary = Depends(current_user), db: Session = Depends(get_db)
) -> ReconciliationResponse:
    """Stored figures beside the same figures recomputed from the raw attempts.

    Read-only: it does not sync first, so it shows whether the stored figures already
    agree with the attempt records.
    """
    kept, fresh = service.stored(db, user.id), service.recompute(db, user.id)
    rows = []
    for key in sorted(set(kept) | set(fresh)):
        s, r = kept.get(key), fresh.get(key)
        rows.append(
            ReconciliationRow(
                subject=key[0],
                topic=key[1],
                stored_attempts=s.attempts if s else 0,
                stored_score=s.total_score if s else 0.0,
                stored_possible=s.total_possible if s else 0.0,
                recomputed_attempts=r.attempts if r else 0,
                recomputed_score=r.total_score if r else 0.0,
                recomputed_possible=r.total_possible if r else 0.0,
            )
        )
    return ReconciliationResponse(matches=kept == fresh, topics=rows)
