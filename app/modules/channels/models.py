"""Channel data (FR-02, FR-03): normal and authorized channels and their members."""

import enum
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.core.schema import CreatedAtMixin, enum_column


class ChannelType(enum.StrEnum):
    NORMAL = "normal"
    AUTHORIZED = "authorized"


class ApprovalStatus(enum.StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class MemberRole(enum.StrEnum):
    MEMBER = "member"
    MODERATOR = "moderator"
    OWNER = "owner"


class Channel(CreatedAtMixin, Base):
    __tablename__ = "channels"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), index=True)
    description: Mapped[str | None] = mapped_column(Text)
    subject: Mapped[str | None] = mapped_column(String(100), index=True)
    topic: Mapped[str | None] = mapped_column(String(100))
    channel_type: Mapped[ChannelType] = mapped_column(
        enum_column(ChannelType, "channel_type"), default=ChannelType.NORMAL
    )
    approval_status: Mapped[ApprovalStatus] = mapped_column(
        enum_column(ApprovalStatus, "channel_approval_status"), default=ApprovalStatus.APPROVED
    )
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)


class ChannelMembership(Base):
    __tablename__ = "channel_memberships"
    __table_args__ = (UniqueConstraint("channel_id", "user_id", name="uq_channel_member"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    channel_id: Mapped[int] = mapped_column(ForeignKey("channels.id", ondelete="CASCADE"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    role: Mapped[MemberRole] = mapped_column(
        enum_column(MemberRole, "channel_member_role"), default=MemberRole.MEMBER
    )
    joined_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
