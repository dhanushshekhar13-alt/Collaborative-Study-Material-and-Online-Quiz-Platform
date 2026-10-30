"""HTTP surface of the channels module, mounted at ``/api/v1/channels``.

S02.1 creation and approval, S02.2 membership, S03.2 discussion posts.
"""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, model_validator
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.modules.accounts import service as accounts
from app.modules.accounts.service import (
    Capability,
    UserSummary,
    capabilities_for,
    current_user,
    require_capability,
)
from app.modules.channels import service
from app.modules.channels.models import ApprovalStatus, ChannelType, MemberRole
from app.modules.moderation import service as moderation
from app.modules.moderation.service import ModerationActionType, ReportTarget
from app.modules.notifications import service as notifications
from app.modules.notifications.service import NotificationKind

router = APIRouter(prefix="/channels", tags=["channels"])

# Wording fixed by the Test Plan (UT_04).
AUTHORIZED_ONLY_MESSAGE = "Only authorized users/admins can create Authorized Channels"
SUBJECT_OR_TOPIC_MESSAGE = "Enter a subject or topic"
NOT_FOUND_MESSAGE = "Channel not found"
# S02.2 membership messages.
ALREADY_MEMBER_MESSAGE = "You are already a member of this channel"
NOT_A_MEMBER_MESSAGE = "You are not a member of this channel"
OWNER_CANNOT_LEAVE_MESSAGE = "The channel creator cannot leave their own channel"
MEMBERS_ONLY_MESSAGE = "Join this channel to view its content"
REMOVE_FORBIDDEN_MESSAGE = "Only the channel creator or a moderator can remove members"
REMOVE_ROLE_FORBIDDEN_MESSAGE = "You cannot remove a member with this role"
REMOVE_SELF_MESSAGE = "Use Leave Channel to leave a channel yourself"
CONFIRM_REMOVAL_MESSAGE = "Confirm removal of this member"
MEMBER_NOT_FOUND_MESSAGE = "This user is not a member of the channel"


class ChannelCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    channel_type: ChannelType = ChannelType.NORMAL
    subject: str | None = Field(default=None, max_length=100)
    topic: str | None = Field(default=None, max_length=100)
    description: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def name_and_subject_or_topic_are_required(self) -> "ChannelCreate":
        self.name = self.name.strip()
        self.subject = (self.subject or "").strip() or None
        self.topic = (self.topic or "").strip() or None
        if not self.name:
            raise ValueError("Enter a channel name")
        if self.subject is None and self.topic is None:
            raise ValueError(SUBJECT_OR_TOPIC_MESSAGE)
        return self


class ChannelResponse(BaseModel):
    id: int
    name: str
    channel_type: ChannelType
    # The label and style key are the same on every endpoint, so lists, search
    # results and channel pages always present the type identically (ST_04).
    type_label: str
    type_style: str
    is_verified: bool
    approval_status: ApprovalStatus
    subject: str | None
    topic: str | None
    description: str | None
    created_by: int | None
    created_at: datetime | None
    member_count: int
    my_role: str | None = None
    # S03.1: whether the caller sees "Manage Official Content" on this channel. False
    # for regular members and in Normal Channels; the API enforces the same rule.
    can_manage_official_content: bool = False


def _response(
    summary: service.ChannelSummary,
    my_role: str | None = None,
    user: UserSummary | None = None,
) -> ChannelResponse:
    authorized = summary.channel_type is ChannelType.AUTHORIZED
    manages = user is not None and service.manages_official_content(
        summary,
        is_member=my_role is not None,
        has_capability=_can(user, Capability.OFFICIAL_CONTENT_MANAGE),
        is_admin=_can(user, Capability.CHANNEL_APPROVE),
    )
    return ChannelResponse(
        id=summary.id,
        name=summary.name,
        channel_type=summary.channel_type,
        type_label=summary.type_label,
        type_style="channel-authorized" if authorized else "channel-normal",
        is_verified=authorized and summary.approval_status is ApprovalStatus.APPROVED,
        approval_status=summary.approval_status,
        subject=summary.subject,
        topic=summary.topic,
        description=summary.description,
        created_by=summary.created_by,
        created_at=summary.created_at,
        member_count=summary.member_count,
        my_role=my_role,
        can_manage_official_content=manages,
    )


def _can(user: UserSummary, capability: Capability) -> bool:
    return capability in capabilities_for(user.role)


@router.post("", status_code=status.HTTP_201_CREATED, response_model=ChannelResponse)
def create_channel(
    payload: ChannelCreate,
    user: UserSummary = Depends(require_capability(Capability.CHANNEL_CREATE_NORMAL)),
    db: Session = Depends(get_db),
) -> ChannelResponse:
    """Create a channel; the creator becomes its owner and first member (UT_03).

    Authorized Channels need ``channel:create_authorized`` (UT_04). One requested by an
    authorized user waits for an admin's approval; one created by an admin is approved
    at once, since the admin is the approver.
    """
    if payload.channel_type is ChannelType.AUTHORIZED and not _can(
        user, Capability.CHANNEL_CREATE_AUTHORIZED
    ):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=AUTHORIZED_ONLY_MESSAGE)

    created = service.create_channel(
        db,
        name=payload.name,
        created_by=user.id,
        channel_type=payload.channel_type,
        subject=payload.subject,
        topic=payload.topic,
        description=payload.description,
    )
    if created.approval_status is ApprovalStatus.PENDING and _can(user, Capability.CHANNEL_APPROVE):
        service.set_approval(db, created.id, ApprovalStatus.APPROVED)
    db.commit()
    return _response(service.describe_channel(db, created.id), "owner", user)


@router.get("", response_model=list[ChannelResponse])
def list_channels(
    q: str | None = Query(default=None, max_length=100, description="Search name/subject/topic"),
    channel_type: ChannelType | None = Query(default=None, alias="type"),
    user: UserSummary = Depends(current_user),
    db: Session = Depends(get_db),
) -> list[ChannelResponse]:
    """Channels the caller can see, with their type label (ST_04).

    Pending or rejected Authorized Channels are visible only to the requester and to
    admins; once approved they appear for everyone (ST_27).
    """
    summaries = service.list_channels(
        db,
        viewer_id=user.id,
        viewer_is_admin=_can(user, Capability.CHANNEL_APPROVE),
        query=q,
        channel_type=channel_type,
    )
    roles = service.member_roles(db, user.id, [s.id for s in summaries])
    return [_response(s, _value(roles.get(s.id)), user) for s in summaries]


@router.get("/mine", response_model=list[ChannelResponse])
def my_channels(
    user: UserSummary = Depends(current_user), db: Session = Depends(get_db)
) -> list[ChannelResponse]:
    """My Channels: every channel the caller belongs to, with their role (IT_03, IT_04)."""
    return [_response(s, role.value, user) for s, role in service.channels_for_member(db, user.id)]


@router.get(
    "/requests",
    response_model=list[ChannelResponse],
    dependencies=[Depends(require_capability(Capability.CHANNEL_APPROVE))],
)
def list_pending_requests(db: Session = Depends(get_db)) -> list[ChannelResponse]:
    """Admin > Channel Requests: Authorized Channels waiting for a decision (ST_27)."""
    return [_response(s) for s in service.pending_requests(db)]


@router.get("/{channel_id}", response_model=ChannelResponse)
def read_channel(
    channel_id: int, user: UserSummary = Depends(current_user), db: Session = Depends(get_db)
) -> ChannelResponse:
    """A channel page. Channels the caller may not see answer 404, not 403, so their
    existence is not revealed."""
    summary = service.describe_channel(db, channel_id)
    if summary is None or not service.is_visible_to(
        summary, user.id, _can(user, Capability.CHANNEL_APPROVE)
    ):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=NOT_FOUND_MESSAGE)
    return _response(summary, _role(db, channel_id, user.id), user)


class ChannelDecision(BaseModel):
    # Optional note for the requester and the audit log, e.g. why a request was rejected.
    note: str | None = Field(default=None, max_length=1000)


_DECISION_ACTION = {
    ApprovalStatus.APPROVED: ModerationActionType.APPROVE_CHANNEL,
    ApprovalStatus.REJECTED: ModerationActionType.REJECT_CHANNEL,
}


def _decide(
    db: Session,
    channel_id: int,
    decision: ApprovalStatus,
    admin: UserSummary,
    payload: ChannelDecision | None,
) -> ChannelResponse:
    """Record an admin's decision on a pending request (S02.1, S14.2).

    The decision is written to the admin audit log with actor, target and time, and the
    requester is notified of the outcome.
    """
    summary = service.describe_channel(db, channel_id)
    if summary is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=NOT_FOUND_MESSAGE)
    if summary.approval_status is not ApprovalStatus.PENDING:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"This channel is already {summary.approval_status.value}",
        )
    note = (payload.note or "").strip() or None if payload else None
    service.set_approval(db, channel_id, decision)
    moderation.log_action(
        db,
        actor_id=admin.id,
        action=_DECISION_ACTION[decision],
        target_type=ReportTarget.CHANNEL,
        target_id=channel_id,
        note=note,
    )
    if summary.created_by is not None:
        approved = decision is ApprovalStatus.APPROVED
        notifications.notify(
            db,
            user_id=summary.created_by,
            kind=NotificationKind.MODERATION,
            title=(
                f'Your Authorized Channel "{summary.name}" was approved'
                if approved
                else f'Your Authorized Channel request "{summary.name}" was rejected'
            ),
            body=note,
            link=f"/channels/{channel_id}",
        )
    db.commit()
    return _response(service.describe_channel(db, channel_id))


@router.post("/{channel_id}/approve", response_model=ChannelResponse)
def approve_channel(
    channel_id: int,
    payload: ChannelDecision | None = None,
    admin: UserSummary = Depends(require_capability(Capability.CHANNEL_APPROVE)),
    db: Session = Depends(get_db),
) -> ChannelResponse:
    """Approve a pending Authorized Channel; it becomes visible platform-wide (ST_27)."""
    return _decide(db, channel_id, ApprovalStatus.APPROVED, admin, payload)


@router.post("/{channel_id}/reject", response_model=ChannelResponse)
def reject_channel(
    channel_id: int,
    payload: ChannelDecision | None = None,
    admin: UserSummary = Depends(require_capability(Capability.CHANNEL_APPROVE)),
    db: Session = Depends(get_db),
) -> ChannelResponse:
    """Reject a pending Authorized Channel; it stays out of the Authorized listing."""
    return _decide(db, channel_id, ApprovalStatus.REJECTED, admin, payload)


# --------------------------------------------------------------------------- #
# S02.2 - join, leave and member management
# --------------------------------------------------------------------------- #


class MemberResponse(BaseModel):
    user_id: int
    username: str | None
    display_name: str
    role: MemberRole
    joined_at: datetime | None


def _visible_channel(db: Session, channel_id: int, user: UserSummary) -> service.ChannelSummary:
    summary = service.describe_channel(db, channel_id)
    if summary is None or not service.is_visible_to(
        summary, user.id, _can(user, Capability.CHANNEL_APPROVE)
    ):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=NOT_FOUND_MESSAGE)
    return summary


@router.post("/{channel_id}/join", response_model=ChannelResponse)
def join_channel(
    channel_id: int, user: UserSummary = Depends(current_user), db: Session = Depends(get_db)
) -> ChannelResponse:
    """Join a channel found through search; membership takes effect at once (IT_03).

    Only channels the caller can see are joinable, so a pending Authorized Channel
    answers 404 to everyone but its requester and admins.
    """
    _visible_channel(db, channel_id, user)
    if service.is_member(db, channel_id, user.id):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=ALREADY_MEMBER_MESSAGE)
    service.add_member(db, channel_id, user.id)
    db.commit()
    return _response(service.describe_channel(db, channel_id), MemberRole.MEMBER.value, user)


@router.post("/{channel_id}/leave", status_code=status.HTTP_204_NO_CONTENT)
def leave_channel(
    channel_id: int, user: UserSummary = Depends(current_user), db: Session = Depends(get_db)
) -> None:
    """Leave a channel; it drops out of My Channels and its content closes (IT_04).

    The creator cannot leave, since that would leave the channel without an owner.
    """
    _visible_channel(db, channel_id, user)
    role = service.member_role(db, channel_id, user.id)
    if role is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=NOT_A_MEMBER_MESSAGE)
    if role is MemberRole.OWNER:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=OWNER_CANNOT_LEAVE_MESSAGE)
    service.remove_member(db, channel_id, user.id)
    db.commit()


@router.get("/{channel_id}/members", response_model=list[MemberResponse])
def list_members(
    channel_id: int, user: UserSummary = Depends(current_user), db: Session = Depends(get_db)
) -> list[MemberResponse]:
    """The member list: channel-restricted, so only members (and admins) may read it."""
    _visible_channel(db, channel_id, user)
    if not service.is_member(db, channel_id, user.id) and not _can(
        user, Capability.CHANNEL_APPROVE
    ):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=MEMBERS_ONLY_MESSAGE)
    members = []
    listed = service.list_members(db, channel_id)
    people = accounts.get_users(db, [m.user_id for m in listed])
    for m in listed:
        account = people.get(m.user_id)
        members.append(
            MemberResponse(
                user_id=m.user_id,
                username=account.username if account else None,
                display_name=account.display_name if account else "",
                role=m.role,
                joined_at=m.joined_at,
            )
        )
    return members


@router.delete("/{channel_id}/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_member(
    channel_id: int,
    user_id: int,
    confirm: bool = Query(default=False, description="Must be true: the UI's confirm step"),
    user: UserSummary = Depends(current_user),
    db: Session = Depends(get_db),
) -> None:
    """Remove a member after confirming (ST_03). Creator or moderator only.

    The creator can remove moderators and members; a moderator only members. The
    removed user loses access to channel-restricted content immediately.
    """
    _visible_channel(db, channel_id, user)
    actor_role = service.member_role(db, channel_id, user.id)
    if not service.can_manage_members(actor_role):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=REMOVE_FORBIDDEN_MESSAGE)
    if user_id == user.id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=REMOVE_SELF_MESSAGE)
    target_role = service.member_role(db, channel_id, user_id)
    if target_role is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=MEMBER_NOT_FOUND_MESSAGE)
    if not service.can_remove(actor_role, target_role):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail=REMOVE_ROLE_FORBIDDEN_MESSAGE
        )
    if not confirm:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=CONFIRM_REMOVAL_MESSAGE)
    service.remove_member(db, channel_id, user_id)
    db.commit()


def _role(db: Session, channel_id: int, user_id: int) -> str | None:
    return _value(service.member_role(db, channel_id, user_id))


def _value(role: MemberRole | None) -> str | None:
    return role.value if role else None


# --------------------------------------------------------------------------- #
# S03.2 - discussion posts
# --------------------------------------------------------------------------- #

POST_REQUIRED_MESSAGE = "Write something to post"
POST_NOT_FOUND_MESSAGE = "Post not found"
POST_MEMBERS_ONLY_MESSAGE = "Join this channel to post"
POST_AUTHOR_ONLY_MESSAGE = "Only the author can edit or delete this post"
CONFIRM_DELETE_POST_MESSAGE = "Confirm deletion of this post"


class PostRequest(BaseModel):
    body: str = Field(max_length=5000)


class PostResponse(BaseModel):
    id: int
    channel_id: int
    author_id: int
    author_name: str | None
    body: str
    created_at: datetime | None
    edited_at: datetime | None
    # The UI shows "edited" when this is true.
    is_edited: bool
    # Whether the caller sees Edit and Delete (its author only).
    can_edit: bool


class PostListResponse(BaseModel):
    items: list[PostResponse]
    total: int
    limit: int
    offset: int


def _post_response(
    db: Session,
    post: service.PostSummary,
    user: UserSummary,
    names: dict[int, str | None] | None = None,
) -> PostResponse:
    if names is not None and post.author_id in names:
        name = names[post.author_id]
    else:
        author = accounts.get_user(db, post.author_id)
        name = author.display_name if author else None
        if names is not None:
            names[post.author_id] = name
    return PostResponse(
        id=post.id,
        channel_id=post.channel_id,
        author_id=post.author_id,
        author_name=name,
        body=post.body,
        created_at=post.created_at,
        edited_at=post.edited_at,
        is_edited=post.is_edited,
        can_edit=post.author_id == user.id,
    )


def _post_body(payload: PostRequest) -> str:
    body = payload.body.strip()
    if not body:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=POST_REQUIRED_MESSAGE
        )
    return body


def _board(db: Session, channel_id: int, user: UserSummary, refusal: str) -> None:
    """The board is channel-restricted content: members (and admins, to read) only."""
    _visible_channel(db, channel_id, user)
    if not service.is_member(db, channel_id, user.id) and not _can(
        user, Capability.CHANNEL_APPROVE
    ):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=refusal)


def _own_post(db: Session, channel_id: int, post_id: int, user: UserSummary) -> service.PostSummary:
    _board(db, channel_id, user, MEMBERS_ONLY_MESSAGE)
    post = service.get_post(db, channel_id, post_id)
    if post is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=POST_NOT_FOUND_MESSAGE)
    if post.author_id != user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=POST_AUTHOR_ONLY_MESSAGE)
    return post


@router.post(
    "/{channel_id}/posts", status_code=status.HTTP_201_CREATED, response_model=PostResponse
)
def create_post(
    channel_id: int,
    payload: PostRequest,
    user: UserSummary = Depends(current_user),
    db: Session = Depends(get_db),
) -> PostResponse:
    """Start a discussion on the channel's board (IT_05). Members only."""
    _visible_channel(db, channel_id, user)
    if not service.is_member(db, channel_id, user.id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=POST_MEMBERS_ONLY_MESSAGE)
    post = service.create_post(db, channel_id, user.id, _post_body(payload))
    db.commit()
    return _post_response(db, post, user)


@router.get("/{channel_id}/posts", response_model=PostListResponse)
def list_posts(
    channel_id: int,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    user: UserSummary = Depends(current_user),
    db: Session = Depends(get_db),
) -> PostListResponse:
    """The channel's discussion board, newest first, each post with its author."""
    _board(db, channel_id, user, MEMBERS_ONLY_MESSAGE)
    posts, total = service.list_posts(db, channel_id, limit=limit, offset=offset)
    names: dict[int, str | None] = {}
    return PostListResponse(
        items=[_post_response(db, p, user, names) for p in posts],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.patch("/{channel_id}/posts/{post_id}", response_model=PostResponse)
def edit_post(
    channel_id: int,
    post_id: int,
    payload: PostRequest,
    user: UserSummary = Depends(current_user),
    db: Session = Depends(get_db),
) -> PostResponse:
    """Edit your own post (IT_05); it then shows as edited."""
    _own_post(db, channel_id, post_id, user)
    post = service.edit_post(db, post_id, _post_body(payload))
    db.commit()
    return _post_response(db, post, user)


@router.delete("/{channel_id}/posts/{post_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_post(
    channel_id: int,
    post_id: int,
    confirm: bool = Query(default=False, description="Must be true: the UI's confirm step"),
    user: UserSummary = Depends(current_user),
    db: Session = Depends(get_db),
) -> None:
    """Delete your own post after confirming (IT_05). Without ``confirm=true`` it is
    refused, so the API cannot skip the UI's confirm step."""
    _own_post(db, channel_id, post_id, user)
    if not confirm:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=CONFIRM_DELETE_POST_MESSAGE
        )
    service.delete_post(db, post_id)
    db.commit()
