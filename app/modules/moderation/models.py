"""Moderation data (FR-04, FR-05, FR-11, FR-13, FR-14): reports and admin actions."""

import enum
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.core.schema import CreatedAtMixin, enum_column


class ReportTarget(enum.StrEnum):
    MATERIAL = "material"
    QUESTION = "question"
    QUIZ = "quiz"
    CHANNEL_MESSAGE = "channel_message"
    DIRECT_MESSAGE = "direct_message"
    USER = "user"
    CHANNEL = "channel"


class ReportReason(enum.StrEnum):
    INCORRECT = "incorrect"
    INAPPROPRIATE = "inappropriate"
    DUPLICATE = "duplicate"
    MISLEADING = "misleading"
    SPAM = "spam"
    OTHER = "other"


class ReportStatus(enum.StrEnum):
    OPEN = "open"
    RESOLVED = "resolved"
    DISMISSED = "dismissed"


class ModerationActionType(enum.StrEnum):
    REMOVE_CONTENT = "remove_content"
    RESTORE_CONTENT = "restore_content"
    SUSPEND_USER = "suspend_user"
    UNSUSPEND_USER = "unsuspend_user"
    APPROVE_CHANNEL = "approve_channel"
    REJECT_CHANNEL = "reject_channel"
    WARN_USER = "warn_user"


class Report(CreatedAtMixin, Base):
    __tablename__ = "reports"

    id: Mapped[int] = mapped_column(primary_key=True)
    reporter_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    target_type: Mapped[ReportTarget] = mapped_column(enum_column(ReportTarget, "report_target"))
    target_id: Mapped[int] = mapped_column(Integer)
    channel_id: Mapped[int | None] = mapped_column(ForeignKey("channels.id", ondelete="SET NULL"))
    reason: Mapped[ReportReason] = mapped_column(enum_column(ReportReason, "report_reason"))
    details: Mapped[str | None] = mapped_column(Text)
    status: Mapped[ReportStatus] = mapped_column(
        enum_column(ReportStatus, "report_status"), default=ReportStatus.OPEN, index=True
    )
    resolved_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    resolution_note: Mapped[str | None] = mapped_column(Text)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ModerationAction(CreatedAtMixin, Base):
    __tablename__ = "moderation_actions"

    id: Mapped[int] = mapped_column(primary_key=True)
    actor_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    action: Mapped[ModerationActionType] = mapped_column(
        enum_column(ModerationActionType, "moderation_action_type")
    )
    target_type: Mapped[ReportTarget] = mapped_column(
        enum_column(ReportTarget, "moderation_action_target")
    )
    target_id: Mapped[int] = mapped_column(Integer)
    report_id: Mapped[int | None] = mapped_column(ForeignKey("reports.id", ondelete="SET NULL"))
    note: Mapped[str | None] = mapped_column(Text)
