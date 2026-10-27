"""Public interface of the notifications module (FR-14)."""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.core import events
from app.modules.accounts import service as accounts
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


@dataclass(frozen=True)
class NotificationView:
    id: int
    kind: NotificationKind
    title: str
    body: str | None
    link: str | None
    is_read: bool
    created_at: datetime


def notifications_for(
    db: Session, user_id: int, *, limit: int = 50, unread_only: bool = False
) -> list[NotificationView]:
    """A user's notifications, newest first."""
    stmt = select(Notification).where(Notification.user_id == user_id)
    if unread_only:
        stmt = stmt.where(Notification.is_read.is_(False))
    rows = db.scalars(stmt.order_by(Notification.id.desc()).limit(limit))
    return [
        NotificationView(n.id, n.kind, n.title, n.body, n.link, bool(n.is_read), n.created_at)
        for n in rows
    ]


def unread_count(db: Session, user_id: int) -> int:
    return (
        db.scalar(
            select(func.count(Notification.id)).where(
                Notification.user_id == user_id, Notification.is_read.is_(False)
            )
        )
        or 0
    )


def mark_read(db: Session, user_id: int, notification_id: int) -> bool:
    """Mark one of the user's notifications read; False if it isn't theirs."""
    n = db.get(Notification, notification_id)
    if n is None or n.user_id != user_id:
        return False
    n.is_read = True
    db.flush()
    return True


# --------------------------------------------------------------------------- #
# S14.1 - preferences, mark all read, and the events that create notifications
# --------------------------------------------------------------------------- #

# The categories a user can switch off, in the order Notification Settings lists them.
CATEGORY_LABELS: dict[NotificationKind, str] = {
    NotificationKind.REPLY: "Replies",
    NotificationKind.MENTION: "Mentions",
    NotificationKind.QUIZ_UPDATE: "Quiz Updates",
    NotificationKind.CHANNEL_ACTIVITY: "Channel Updates",
    NotificationKind.DIRECT_MESSAGE: "Direct Messages",
    NotificationKind.MODERATION: "Moderation",
}


def mark_all_read(db: Session, user_id: int) -> int:
    """Mark every unread notification of the user read; returns how many changed."""
    result = db.execute(
        update(Notification)
        .where(Notification.user_id == user_id, Notification.is_read.is_(False))
        .values(is_read=True)
    )
    db.flush()
    return result.rowcount or 0


def preferences(db: Session, user_id: int) -> dict[NotificationKind, bool]:
    """Every category with whether the user receives it (on unless switched off)."""
    stored = dict(
        db.execute(
            select(NotificationPreference.kind, NotificationPreference.enabled).where(
                NotificationPreference.user_id == user_id
            )
        ).all()
    )
    return {kind: bool(stored.get(kind, True)) for kind in CATEGORY_LABELS}


def _message_link(channel_id: int, message_id: int) -> str:
    """Where the web UI opens a channel's chat at one message."""
    return f"/app/#/channels/{channel_id}?message={message_id}"


def _name(db: Session, user_id: int) -> str:
    user = accounts.get_user(db, user_id)
    return user.display_name if user else "Someone"


def _in(channel_name: str | None) -> str:
    return f" in {channel_name}" if channel_name else ""


def _on_channel_message(
    db: Session,
    *,
    channel_id: int,
    channel_name: str | None,
    message_id: int,
    sender_id: int,
    reply_to_user_id: int | None,
    mentioned_user_ids: list[int],
) -> None:
    """A reply or @mention notifies the person it is addressed to, once (UT_27).

    Someone replied to *and* mentioned gets the reply; if they switched replies off,
    the mention still reaches them, since they asked for those.
    """
    sender = _name(db, sender_id)
    link = _message_link(channel_id, message_id)
    recipients: dict[int, list[tuple[NotificationKind, str]]] = {}
    if reply_to_user_id is not None:
        recipients.setdefault(reply_to_user_id, []).append(
            (NotificationKind.REPLY, f"{sender} replied to your message{_in(channel_name)}")
        )
    for user_id in mentioned_user_ids:
        recipients.setdefault(user_id, []).append(
            (NotificationKind.MENTION, f"{sender} mentioned you{_in(channel_name)}")
        )
    for user_id, options in recipients.items():
        if user_id == sender_id:
            continue
        for kind, title in options:
            if notify(db, user_id=user_id, kind=kind, title=title, link=link) is not None:
                break


def _on_quiz_published(
    db: Session,
    *,
    quiz_id: int,
    title: str,
    channel_id: int,
    channel_name: str | None,
    creator_id: int,
    member_ids: list[int],
) -> None:
    """A quiz published in a channel is a Quiz Update for its other members (UT_28)."""
    for user_id in member_ids:
        if user_id != creator_id:
            notify(
                db,
                user_id=user_id,
                kind=NotificationKind.QUIZ_UPDATE,
                title=f'New quiz "{title}"{_in(channel_name)}',
                link=f"/app/#/channels/{channel_id}?quiz={quiz_id}",
            )


def _on_channel_post(
    db: Session,
    *,
    channel_id: int,
    channel_name: str | None,
    post_id: int,
    author_id: int,
    member_ids: list[int],
) -> None:
    """A new discussion post is a Channel Update for the channel's other members."""
    author = _name(db, author_id)
    for user_id in member_ids:
        if user_id != author_id:
            notify(
                db,
                user_id=user_id,
                kind=NotificationKind.CHANNEL_ACTIVITY,
                title=f"{author} posted{_in(channel_name)}",
                link=f"/app/#/channels/{channel_id}?post={post_id}",
            )


events.subscribe(events.CHANNEL_MESSAGE_POSTED, _on_channel_message)
events.subscribe(events.QUIZ_PUBLISHED, _on_quiz_published)
events.subscribe(events.CHANNEL_POST_CREATED, _on_channel_post)
