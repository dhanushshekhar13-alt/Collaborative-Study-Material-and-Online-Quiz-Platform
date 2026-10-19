"""Public interface of the notifications module (FR-14)."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.notifications.models import (
    Notification,
    NotificationKind,
    NotificationPreference,
)


def is_enabled(db: Session, user_id: int, kind: NotificationKind) -> bool:
    """Notifications are on unless the user has turned that kind off."""
    enabled = db.scalar(
        select(NotificationPreference.enabled).where(
            NotificationPreference.user_id == user_id, NotificationPreference.kind == kind
        )
    )
    return True if enabled is None else enabled


def set_preference(db: Session, user_id: int, kind: NotificationKind, enabled: bool) -> None:
    pref = db.scalar(
        select(NotificationPreference).where(
            NotificationPreference.user_id == user_id, NotificationPreference.kind == kind
        )
    )
    if pref is None:
        db.add(NotificationPreference(user_id=user_id, kind=kind, enabled=enabled))
    else:
        pref.enabled = enabled
    db.flush()


def notify(
    db: Session,
    *,
    user_id: int,
    kind: NotificationKind,
    title: str,
    body: str | None = None,
    link: str | None = None,
) -> int | None:
    """Create a notification if the user wants this kind; returns its id or ``None``."""
    if not is_enabled(db, user_id, kind):
        return None
    n = Notification(user_id=user_id, kind=kind, title=title, body=body, link=link)
    db.add(n)
    db.flush()
    return n.id
