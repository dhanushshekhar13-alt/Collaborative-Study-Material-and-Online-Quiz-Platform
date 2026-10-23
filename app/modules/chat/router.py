"""HTTP surface of the chat module: channel chat (S10.1), mounted under ``/api/v1``.

The chat lives at ``/channels/{id}/messages`` so a client reaches it from the channel
page, but the messages are chat's own table (``channel_messages``); channels is only
asked whether the caller can see the channel and belongs to it.
"""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.modules.accounts import service as accounts
from app.modules.accounts.service import UserSummary, current_user
from app.modules.channels import service as channels
from app.modules.chat import service

router = APIRouter(prefix="/channels", tags=["chat"])

# Wording fixed by the Test Plan: UT_20 (blank message) and IT_20 (non-member).
EMPTY_MESSAGE = "Message cannot be empty"
MEMBERS_ONLY_MESSAGE = "Join the channel to view this chat"
SEND_MEMBERS_ONLY_MESSAGE = "Join the channel to send messages"
NOT_FOUND_MESSAGE = "Channel not found"
BOTH_CURSORS_MESSAGE = "Use either before or after, not both"
MAX_MESSAGE_LENGTH = 2000


class MessageRequest(BaseModel):
    # A missing body is treated like a blank one, so both get UT_20's wording.
    body: str = Field(default="", max_length=MAX_MESSAGE_LENGTH)


class MessageResponse(BaseModel):
    id: int
    channel_id: int
    sender_id: int
    sender_name: str | None
    body: str
    created_at: datetime | None


class MessagePageResponse(BaseModel):
    items: list[MessageResponse]
    # More messages lie beyond this page in the direction read: older ones when
    # opening the chat or paging with ``before``, newer ones when paging with ``after``.
    has_more: bool
    limit: int


def _member_channel(db: Session, channel_id: int, user: UserSummary, refusal: str) -> None:
    """The chat is open to the channel's members only, checked on every request.

    A channel the caller cannot see is 404, so a pending Authorized Channel's existence
    is not revealed; a visible channel the caller hasn't joined is 403.
    """
    summary = channels.describe_channel(db, channel_id)
    if summary is None or not channels.is_visible_to(summary, user.id, viewer_is_admin=False):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=NOT_FOUND_MESSAGE)
    if not channels.is_member(db, channel_id, user.id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=refusal)


def _response(
    db: Session, channel_id: int, m: service.MessageView, names: dict[int, str | None]
) -> MessageResponse:
    if m.sender_id not in names:
        sender = accounts.get_user(db, m.sender_id)
        names[m.sender_id] = sender.display_name if sender else None
    return MessageResponse(
        id=m.id,
        channel_id=channel_id,
        sender_id=m.sender_id,
        sender_name=names[m.sender_id],
        body=m.body,
        created_at=m.created_at,
    )


@router.post(
    "/{channel_id}/messages",
    status_code=status.HTTP_201_CREATED,
    response_model=MessageResponse,
)
def send_message(
    channel_id: int,
    payload: MessageRequest,
    user: UserSummary = Depends(current_user),
    db: Session = Depends(get_db),
) -> MessageResponse:
    """Send a message to the channel chat (UT_19). Members only.

    The sender is always the signed-in user and the timestamp is the server's, so
    every member sees the same sender and time. A blank message is refused (UT_20).
    """
    _member_channel(db, channel_id, user, SEND_MEMBERS_ONLY_MESSAGE)
    body = payload.body.strip()
    if not body:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=EMPTY_MESSAGE)
    message_id = service.post_channel_message(
        db, channel_id=channel_id, sender_id=user.id, body=body
    )
    db.commit()
    return _response(db, channel_id, service.get_channel_message(db, channel_id, message_id), {})


@router.get("/{channel_id}/messages", response_model=MessagePageResponse)
def read_messages(
    channel_id: int,
    limit: int = Query(default=50, ge=1, le=100),
    before: int | None = Query(default=None, ge=1, description="Older than this message id"),
    after: int | None = Query(default=None, ge=0, description="Newer than this message id"),
    user: UserSummary = Depends(current_user),
    db: Session = Depends(get_db),
) -> MessagePageResponse:
    """The channel chat, oldest first within each page (ST_19, ST_20).

    With no cursor this is the newest ``limit`` messages. Scroll up with
    ``before=<oldest id shown>`` until ``has_more`` is false; poll for new messages
    with ``after=<newest id shown>``. Messages are stored, so they survive logout.
    """
    _member_channel(db, channel_id, user, MEMBERS_ONLY_MESSAGE)
    if before is not None and after is not None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=BOTH_CURSORS_MESSAGE
        )
    messages, has_more = service.channel_messages_page(
        db, channel_id, limit=limit, before=before, after=after
    )
    names: dict[int, str | None] = {}
    return MessagePageResponse(
        items=[_response(db, channel_id, m, names) for m in messages],
        has_more=has_more,
        limit=limit,
    )
