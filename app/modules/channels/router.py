"""HTTP surface of the channels module, mounted at ``/api/v1/channels`` (S02.1)."""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, model_validator
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.modules.accounts.service import (
    Capability,
    UserSummary,
    capabilities_for,
    current_user,
    require_capability,
)
from app.modules.channels import service
from app.modules.channels.models import ApprovalStatus, ChannelType

router = APIRouter(prefix="/channels", tags=["channels"])

# Wording fixed by the Test Plan (UT_04).
AUTHORIZED_ONLY_MESSAGE = "Only authorized users/admins can create Authorized Channels"
SUBJECT_OR_TOPIC_MESSAGE = "Enter a subject or topic"
NOT_FOUND_MESSAGE = "Channel not found"


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


def _response(summary: service.ChannelSummary, my_role: str | None = None) -> ChannelResponse:
    authorized = summary.channel_type is ChannelType.AUTHORIZED
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
    return _response(service.describe_channel(db, created.id), my_role="owner")


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
    return [_response(s, _role(db, s.id, user.id)) for s in summaries]


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
    return _response(summary, _role(db, channel_id, user.id))


def _decide(db: Session, channel_id: int, decision: ApprovalStatus) -> ChannelResponse:
    summary = service.describe_channel(db, channel_id)
    if summary is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=NOT_FOUND_MESSAGE)
    if summary.approval_status is not ApprovalStatus.PENDING:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"This channel is already {summary.approval_status.value}",
        )
    service.set_approval(db, channel_id, decision)
    db.commit()
    return _response(service.describe_channel(db, channel_id))


@router.post(
    "/{channel_id}/approve",
    response_model=ChannelResponse,
    dependencies=[Depends(require_capability(Capability.CHANNEL_APPROVE))],
)
def approve_channel(channel_id: int, db: Session = Depends(get_db)) -> ChannelResponse:
    """Approve a pending Authorized Channel; it becomes visible platform-wide (ST_27)."""
    return _decide(db, channel_id, ApprovalStatus.APPROVED)


@router.post(
    "/{channel_id}/reject",
    response_model=ChannelResponse,
    dependencies=[Depends(require_capability(Capability.CHANNEL_APPROVE))],
)
def reject_channel(channel_id: int, db: Session = Depends(get_db)) -> ChannelResponse:
    """Reject a pending Authorized Channel; it stays visible only to its requester."""
    return _decide(db, channel_id, ApprovalStatus.REJECTED)


def _role(db: Session, channel_id: int, user_id: int) -> str | None:
    role = service.member_role(db, channel_id, user_id)
    return role.value if role else None
