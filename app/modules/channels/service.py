"""Public interface of the channels module."""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.channels.models import (
    ApprovalStatus,
    Channel,
    ChannelMembership,
    ChannelType,
    MemberRole,
)


@dataclass(frozen=True)
class ChannelSummary:
    id: int
    name: str
    channel_type: ChannelType
    approval_status: ApprovalStatus
    subject: str | None
    topic: str | None


def _summary(c: Channel) -> ChannelSummary:
    return ChannelSummary(c.id, c.name, c.channel_type, c.approval_status, c.subject, c.topic)


def create_channel(
    db: Session,
    *,
    name: str,
    created_by: int,
    channel_type: ChannelType = ChannelType.NORMAL,
    subject: str | None = None,
    topic: str | None = None,
    description: str | None = None,
) -> ChannelSummary:
    """Create a channel and make its creator the owner.

    Authorized channels start as pending until an admin approves them (FR-02).
    """
    status = (
        ApprovalStatus.PENDING
        if channel_type is ChannelType.AUTHORIZED
        else ApprovalStatus.APPROVED
    )
    channel = Channel(
        name=name,
        description=description,
        subject=subject,
        topic=topic,
        channel_type=channel_type,
        approval_status=status,
        created_by=created_by,
    )
    db.add(channel)
    db.flush()
    db.add(ChannelMembership(channel_id=channel.id, user_id=created_by, role=MemberRole.OWNER))
    db.flush()
    return _summary(channel)


def set_approval(db: Session, channel_id: int, status: ApprovalStatus) -> None:
    channel = db.get(Channel, channel_id)
    if channel is None:
        raise LookupError(f"channel {channel_id} not found")
    channel.approval_status = status


def get_channel(db: Session, channel_id: int) -> ChannelSummary | None:
    channel = db.get(Channel, channel_id)
    return _summary(channel) if channel else None


def add_member(
    db: Session, channel_id: int, user_id: int, role: MemberRole = MemberRole.MEMBER
) -> None:
    if member_role(db, channel_id, user_id) is None:
        db.add(ChannelMembership(channel_id=channel_id, user_id=user_id, role=role))
        db.flush()


def member_role(db: Session, channel_id: int, user_id: int) -> MemberRole | None:
    """The user's role in the channel, or ``None`` if they are not a member."""
    return db.scalar(
        select(ChannelMembership.role).where(
            ChannelMembership.channel_id == channel_id, ChannelMembership.user_id == user_id
        )
    )


def is_member(db: Session, channel_id: int, user_id: int) -> bool:
    return member_role(db, channel_id, user_id) is not None
