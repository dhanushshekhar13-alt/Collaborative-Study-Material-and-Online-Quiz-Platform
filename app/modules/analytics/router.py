"""HTTP surface of the analytics module (S12.1, S12.2), mounted under ``/api/v1``.

Every endpoint is about the signed-in user's own performance only.
"""

from dataclasses import asdict
from datetime import datetime

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.modules.accounts.service import UserSummary, current_user
from app.modules.analytics import service
from app.modules.analytics.recommendations import ItemType, Mode
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


# --------------------------------------------------------------------------- #
# S12.2 - recommendations targeting weak topics
# --------------------------------------------------------------------------- #


class FocusTopicResponse(BaseModel):
    subject: str
    topic: str
    accuracy: float
    attempts: int
    reason: str


class RecommendationResponse(BaseModel):
    item_type: ItemType
    item_id: int
    title: str
    subject: str
    topic: str | None
    channel_id: int | None
    consumed: bool = Field(description="Already attempted, answered, rated or opened")
    reason: str = Field(description="Why it is recommended")
    rating_average: float | None
    rating_count: int
    attempt_count: int | None = Field(description="Quizzes: how often others attempted it")
    difficulty: str | None
    api_path: str


class RecommendationsResponse(BaseModel):
    mode: Mode = Field(description="targeted: for weak topics; general: popular picks")
    message: str
    focus_topics: list[FocusTopicResponse] = Field(description="Weak topics, weakest first")
    items: list[RecommendationResponse]


class OpenedRequest(BaseModel):
    item_type: ItemType
    item_id: int = Field(ge=1)


@router.get("/recommendations", response_model=RecommendationsResponse)
def my_recommendations(
    limit: int = Query(default=12, ge=1, le=50),
    user: UserSummary = Depends(current_user),
    db: Session = Depends(get_db),
) -> RecommendationsResponse:
    """Recommended for You (UC-13): materials, questions and quizzes for weak topics.

    Items for the weakest topic come first; items already attempted, answered, rated
    or opened are listed after the fresh ones. With no weak topics the list is popular
    items instead (``mode: general``). Worked out on every request, so it follows the
    latest attempt.
    """
    found = service.recommendations_for(db, user.id, limit=limit)
    db.commit()  # keep any performance figures the dashboard sync corrected
    return RecommendationsResponse(**asdict(found))


@router.post("/recommendations/opened", status_code=status.HTTP_204_NO_CONTENT)
def recommendation_opened(
    payload: OpenedRequest,
    user: UserSummary = Depends(current_user),
    db: Session = Depends(get_db),
) -> None:
    """Record that the caller opened a recommended item; it is then de-prioritised."""
    service.record_recommendation_open(db, user.id, payload.item_type, payload.item_id)
    db.commit()
