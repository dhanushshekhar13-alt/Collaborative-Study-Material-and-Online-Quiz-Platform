"""Public interface of the channels module."""

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core import events
from app.modules.channels.models import (
    ApprovalStatus,
    Channel,
    ChannelMembership,
    ChannelPost,
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
    description: str | None = None
    created_by: int | None = None
    created_at: datetime | None = None
    member_count: int = 0

    @property
    def type_label(self) -> str:
        """The label shown wherever the channel appears (S02.1, ST_04)."""
        return "Authorized" if self.channel_type is ChannelType.AUTHORIZED else "Normal"


def _member_count(db: Session, channel_id: int) -> int:
    return (
        db.scalar(
            select(func.count(ChannelMembership.id)).where(
                ChannelMembership.channel_id == channel_id
            )
        )
        or 0
    )


def _summary(c: Channel, member_count: int = 0) -> ChannelSummary:
    return ChannelSummary(
        c.id,
        c.name,
        c.channel_type,
        c.approval_status,
        c.subject,
        c.topic,
        c.description,
        c.created_by,
        c.created_at,
        member_count,
    )


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


# --------------------------------------------------------------------------- #
# S02.1 - creation, classification, discovery and approval
# --------------------------------------------------------------------------- #


def describe_channel(db: Session, channel_id: int) -> ChannelSummary | None:
    """A channel with its member count, for channel pages and lists."""
    channel = db.get(Channel, channel_id)
    return _summary(channel, _member_count(db, channel.id)) if channel else None


def is_visible_to(summary: ChannelSummary, viewer_id: int, viewer_is_admin: bool) -> bool:
    """Approved channels are visible to everyone; a pending or rejected Authorized
    Channel only to the person who requested it and to admins."""
    if summary.approval_status is ApprovalStatus.APPROVED:
        return True
    return viewer_is_admin or summary.created_by == viewer_id


def list_channels(
    db: Session,
    *,
    viewer_id: int,
    viewer_is_admin: bool,
    query: str | None = None,
    channel_type: ChannelType | None = None,
) -> list[ChannelSummary]:
    """Channels the viewer may see, optionally searched by name, subject or topic."""
    statement = select(Channel).order_by(Channel.name, Channel.id)
    if channel_type is not None:
        statement = statement.where(Channel.channel_type == channel_type)
    if query and query.strip():
        needle = f"%{query.strip().lower()}%"
        statement = statement.where(
            or_(
                func.lower(Channel.name).like(needle),
                func.lower(Channel.subject).like(needle),
                func.lower(Channel.topic).like(needle),
            )
        )
    if not viewer_is_admin:
        statement = statement.where(
            or_(Channel.approval_status == ApprovalStatus.APPROVED, Channel.created_by == viewer_id)
        )
    return [_summary(c, _member_count(db, c.id)) for c in db.scalars(statement)]


def pending_requests(db: Session) -> list[ChannelSummary]:
    """Authorized Channel requests awaiting an admin decision, oldest first."""
    statement = (
        select(Channel)
        .where(Channel.approval_status == ApprovalStatus.PENDING)
        .order_by(Channel.created_at, Channel.id)
    )
    return [_summary(c, _member_count(db, c.id)) for c in db.scalars(statement)]


# --------------------------------------------------------------------------- #
# S02.2 - membership: join, leave, My Channels and member management
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class MemberSummary:
    user_id: int
    role: MemberRole
    joined_at: datetime | None


# Who may remove whom. An owner manages everyone but themselves; a moderator manages
# plain members only, so moderators cannot remove each other or the owner.
_REMOVABLE_BY: dict[MemberRole, frozenset[MemberRole]] = {
    MemberRole.OWNER: frozenset({MemberRole.MODERATOR, MemberRole.MEMBER}),
    MemberRole.MODERATOR: frozenset({MemberRole.MEMBER}),
    MemberRole.MEMBER: frozenset(),
}


def can_manage_members(role: MemberRole | None) -> bool:
    """Whether a channel role may remove members at all (creator or moderator)."""
    return role is not None and bool(_REMOVABLE_BY[role])


def can_remove(actor_role: MemberRole | None, target_role: MemberRole) -> bool:
    return actor_role is not None and target_role in _REMOVABLE_BY[actor_role]


def remove_member(db: Session, channel_id: int, user_id: int) -> bool:
    """Delete a membership. Returns ``False`` if the user was not a member.

    Access to channel-restricted content is decided by ``is_member``, so it ends the
    moment the membership row is gone.
    """
    membership = db.scalar(
        select(ChannelMembership).where(
            ChannelMembership.channel_id == channel_id, ChannelMembership.user_id == user_id
        )
    )
    if membership is None:
        return False
    db.delete(membership)
    db.flush()
    return True


def list_members(db: Session, channel_id: int) -> list[MemberSummary]:
    """A channel's members, owner first, then moderators, then by join order."""
    rank = {MemberRole.OWNER: 0, MemberRole.MODERATOR: 1, MemberRole.MEMBER: 2}
    rows = db.scalars(
        select(ChannelMembership)
        .where(ChannelMembership.channel_id == channel_id)
        .order_by(ChannelMembership.joined_at, ChannelMembership.id)
    )
    members = [MemberSummary(m.user_id, m.role, m.joined_at) for m in rows]
    return sorted(members, key=lambda m: rank[m.role])


def channels_for_member(db: Session, user_id: int) -> list[tuple[ChannelSummary, MemberRole]]:
    """My Channels: every channel the user belongs to, with their role in it."""
    rows = db.execute(
        select(Channel, ChannelMembership.role)
        .join(ChannelMembership, ChannelMembership.channel_id == Channel.id)
        .where(ChannelMembership.user_id == user_id)
        .order_by(Channel.name, Channel.id)
    )
    return [(_summary(c, _member_count(db, c.id)), role) for c, role in rows]


# --------------------------------------------------------------------------- #
# S03.1 - official content in Authorized Channels
# --------------------------------------------------------------------------- #


def manages_official_content(
    summary: ChannelSummary,
    *,
    is_member: bool,
    has_capability: bool,
    is_admin: bool = False,
) -> bool:
    """Whether a user may add, edit and remove official content in this channel.

    Only an approved Authorized Channel holds official content. Inside it, admins
    manage everything; otherwise the user needs ``official_content:manage`` (an
    authorized user) and membership of the channel. Regular members never do, so
    the UI hides "Manage Official Content" for them and the API refuses them.
    """
    if summary.channel_type is not ChannelType.AUTHORIZED:
        return False
    if summary.approval_status is not ApprovalStatus.APPROVED:
        return False
    return is_admin or (has_capability and is_member)


# --------------------------------------------------------------------------- #
# S03.2 - discussion posts
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class PostSummary:
    id: int
    channel_id: int
    author_id: int
    body: str
    created_at: datetime | None
    edited_at: datetime | None

    @property
    def is_edited(self) -> bool:
        return self.edited_at is not None


def _post(p: ChannelPost) -> PostSummary:
    return PostSummary(p.id, p.channel_id, p.author_id, p.body, p.created_at, p.edited_at)


def create_post(db: Session, channel_id: int, author_id: int, body: str) -> PostSummary:
    """Add a discussion post to a channel. Caller checks membership and commits."""
    post = ChannelPost(channel_id=channel_id, author_id=author_id, body=body)
    db.add(post)
    db.flush()
    db.refresh(post)
    channel = db.get(Channel, channel_id)
    events.publish(
        events.CHANNEL_POST_CREATED,
        db,
        channel_id=channel_id,
        channel_name=channel.name if channel else None,
        post_id=post.id,
        author_id=author_id,
        member_ids=[m.user_id for m in list_members(db, channel_id)],
    )
    return _post(post)


def get_post(db: Session, channel_id: int, post_id: int) -> PostSummary | None:
    post = db.get(ChannelPost, post_id)
    return _post(post) if post is not None and post.channel_id == channel_id else None


def list_posts(
    db: Session, channel_id: int, *, limit: int = 50, offset: int = 0
) -> tuple[list[PostSummary], int]:
    """A channel's posts, newest first, and how many there are."""
    where = ChannelPost.channel_id == channel_id
    total = db.scalar(select(func.count(ChannelPost.id)).where(where)) or 0
    rows = db.scalars(
        select(ChannelPost)
        .where(where)
        .order_by(ChannelPost.created_at.desc(), ChannelPost.id.desc())
        .limit(limit)
        .offset(offset)
    )
    return [_post(p) for p in rows], total


def edit_post(db: Session, post_id: int, body: str) -> PostSummary:
    """Replace a post's text and mark it edited. Caller checks authorship and commits."""
    post = db.get(ChannelPost, post_id)
    if post is None:
        raise LookupError(f"post {post_id} not found")
    post.body = body
    post.edited_at = datetime.now(UTC)
    db.flush()
    return _post(post)


def delete_post(db: Session, post_id: int) -> None:
    post = db.get(ChannelPost, post_id)
    if post is not None:
        db.delete(post)
        db.flush()
