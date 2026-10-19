"""Notification data (FR-14): in-app notifications and per-user preferences."""

import enum

from sqlalchemy import Boolean, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.core.schema import CreatedAtMixin, enum_column


class NotificationKind(enum.StrEnum):
    CHANNEL_ACTIVITY = "channel_activity"
    REPLY = "reply"
    MENTION = "mention"
    DIRECT_MESSAGE = "direct_message"
    QUIZ_UPDATE = "quiz_update"
    MODERATION = "moderation"


class Notification(CreatedAtMixin, Base):
    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    kind: Mapped[NotificationKind] = mapped_column(
        enum_column(NotificationKind, "notification_kind")
    )
    title: Mapped[str] = mapped_column(String(200))
    body: Mapped[str | None] = mapped_column(Text)
    link: Mapped[str | None] = mapped_column(String(500))
    is_read: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")


class NotificationPreference(Base):
    __tablename__ = "notification_preferences"
    __table_args__ = (UniqueConstraint("user_id", "kind", name="uq_notification_preference"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    kind: Mapped[NotificationKind] = mapped_column(
        enum_column(NotificationKind, "notification_preference_kind")
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default="1")
