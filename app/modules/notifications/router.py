"""HTTP surface of the notifications module, mounted at ``/api/v1/notifications``.

A signed-in user reads their own in-app notifications with an unread count, marks
them read, and chooses which categories they receive (S14.1).
"""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.modules.accounts.service import UserSummary, current_user
from app.modules.notifications import service
from app.modules.notifications.service import NotificationKind

router = APIRouter(prefix="/notifications", tags=["notifications"])

NOT_FOUND_MESSAGE = "Notification not found"


class NotificationResponse(BaseModel):
    id: int
    kind: NotificationKind
    title: str
    body: str | None
    link: str | None
    is_read: bool
    created_at: datetime | None


class NotificationListResponse(BaseModel):
    items: list[NotificationResponse]
    unread_count: int


class UnreadCountResponse(BaseModel):
    unread_count: int


class PreferenceResponse(BaseModel):
    kind: NotificationKind
    label: str
    enabled: bool


class PreferencesRequest(BaseModel):
    """Only the categories named change; the others keep their setting."""

    preferences: dict[NotificationKind, bool]


def _preferences(db: Session, user_id: int) -> list[PreferenceResponse]:
    return [
        PreferenceResponse(kind=kind, label=service.CATEGORY_LABELS[kind], enabled=enabled)
        for kind, enabled in service.preferences(db, user_id).items()
    ]


@router.get("", response_model=NotificationListResponse)
def my_notifications(
    unread_only: bool = False,
    limit: int = Query(default=50, ge=1, le=100),
    user: UserSummary = Depends(current_user),
    db: Session = Depends(get_db),
) -> NotificationListResponse:
    """The caller's notifications, newest first, with the unread count."""
    items = service.notifications_for(db, user.id, limit=limit, unread_only=unread_only)
    return NotificationListResponse(
        items=[NotificationResponse(**n.__dict__) for n in items],
        unread_count=service.unread_count(db, user.id),
    )


@router.get("/unread-count", response_model=UnreadCountResponse)
def my_unread_count(
    user: UserSummary = Depends(current_user), db: Session = Depends(get_db)
) -> UnreadCountResponse:
    """Just the number for the notification bell, cheap enough to poll."""
    return UnreadCountResponse(unread_count=service.unread_count(db, user.id))


@router.get("/preferences", response_model=list[PreferenceResponse])
def my_preferences(
    user: UserSummary = Depends(current_user), db: Session = Depends(get_db)
) -> list[PreferenceResponse]:
    """Notification Settings: every category and whether the caller receives it."""
    return _preferences(db, user.id)


@router.put("/preferences", response_model=list[PreferenceResponse])
def update_preferences(
    payload: PreferencesRequest,
    user: UserSummary = Depends(current_user),
    db: Session = Depends(get_db),
) -> list[PreferenceResponse]:
    """Switch categories on or off, e.g. ``{"preferences": {"quiz_update": false}}``.

    The choice is stored with the account, so it holds on every device and after
    signing in again. A switched-off category is not generated at all; notifications
    already received stay in the list.
    """
    for kind, enabled in payload.preferences.items():
        service.set_preference(db, user.id, kind, enabled)
    db.commit()
    return _preferences(db, user.id)


@router.post("/read-all", response_model=UnreadCountResponse)
def mark_all_read(
    user: UserSummary = Depends(current_user), db: Session = Depends(get_db)
) -> UnreadCountResponse:
    """Mark every notification of the caller read."""
    service.mark_all_read(db, user.id)
    db.commit()
    return UnreadCountResponse(unread_count=0)


@router.post("/{notification_id}/read", status_code=status.HTTP_204_NO_CONTENT)
def mark_read(
    notification_id: int,
    user: UserSummary = Depends(current_user),
    db: Session = Depends(get_db),
) -> None:
    """Mark one of the caller's notifications as read. Others' are 404."""
    if not service.mark_read(db, user.id, notification_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=NOT_FOUND_MESSAGE)
    db.commit()
