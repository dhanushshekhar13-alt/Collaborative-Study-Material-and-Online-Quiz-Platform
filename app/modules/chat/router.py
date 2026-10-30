"""HTTP surface of the chat module: channel chat (S10.1) and its threads (S10.2).

Mounted under ``/api/v1``.

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
from app.modules.chat import service, sharing
from app.modules.chat.sharing import SharedCardResponse, SharedResourceRequest

router = APIRouter(prefix="/channels", tags=["chat"])

# Wording fixed by the Test Plan: UT_20 (blank message) and IT_20 (non-member).
EMPTY_MESSAGE = "Message cannot be empty"
MEMBERS_ONLY_MESSAGE = "Join the channel to view this chat"
SEND_MEMBERS_ONLY_MESSAGE = "Join the channel to send messages"
NOT_FOUND_MESSAGE = "Channel not found"
BOTH_CURSORS_MESSAGE = "Use either before or after, not both"
MESSAGE_NOT_FOUND = "Message not found"
MAX_MESSAGE_LENGTH = 2000


class MessageRequest(BaseModel):
    # A missing body is treated like a blank one, so both get UT_20's wording.
    body: str = Field(default="", max_length=MAX_MESSAGE_LENGTH)
    # Set to reply in a thread (S10.2). Replying to a reply joins its parent's thread.
    parent_id: int | None = Field(default=None, ge=1)
    # Share a material, question or quiz as a live link (S11.2). The body may then be
    # blank: the card is the message.
    shared_resource: SharedResourceRequest | None = None


class MessageResponse(BaseModel):
    id: int
    channel_id: int
    sender_id: int
    sender_name: str | None
    body: str
    created_at: datetime | None
    # The message this one replies to; null for a top-level message.
    parent_id: int | None = None
    # Visible replies under a top-level message, and when the latest one was sent.
    reply_count: int = 0
    last_reply_at: datetime | None = None
    # The preview card of a shared resource, built from the original on every read.
    shared_resource: SharedCardResponse | None = None


class MessagePageResponse(BaseModel):
    items: list[MessageResponse]
    # More messages lie beyond this page in the direction read: older ones when
    # opening the chat or paging with ``before``, newer ones when paging with ``after``.
    has_more: bool
    limit: int


class ThreadPageResponse(MessagePageResponse):
    """A thread: the message replied to, then a page of its replies oldest first."""

    parent: MessageResponse


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
    db: Session,
    channel_id: int,
    m: service.MessageView,
    names: dict[int, str | None],
    viewer: UserSummary,
    threads: dict[int, service.ThreadSummary] | None = None,
) -> MessageResponse:
    if m.sender_id not in names:
        sender = accounts.get_user(db, m.sender_id)
        names[m.sender_id] = sender.display_name if sender else None
    thread = (threads or {}).get(m.id)
    return MessageResponse(
        id=m.id,
        channel_id=channel_id,
        sender_id=m.sender_id,
        sender_name=names[m.sender_id],
        body=m.body,
        created_at=m.created_at,
        parent_id=m.parent_id,
        reply_count=thread.reply_count if thread else 0,
        last_reply_at=thread.last_reply_at if thread else None,
        shared_resource=sharing.message_card(
            db, m.shared_resource_type, m.shared_resource_id, viewer
        ),
    )


def _responses(
    db: Session, channel_id: int, messages: list[service.MessageView], viewer: UserSummary
) -> list[MessageResponse]:
    """Responses for a page, with sender names and reply counts read once per page."""
    senders = accounts.get_users(db, {m.sender_id for m in messages})
    names: dict[int, str | None] = {uid: u.display_name for uid, u in senders.items()}
    threads = service.thread_summaries(db, [m.id for m in messages if m.parent_id is None])
    return [_response(db, channel_id, m, names, viewer, threads) for m in messages]


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
    """Send a message to the channel chat (UT_19), or a reply in a thread (IT_19).

    Members only. The sender is always the signed-in user and the timestamp is the
    server's, so every member sees the same sender and time. A blank message is
    refused (UT_20). With ``parent_id`` the message is a reply stored with a link to
    the message it answers; replying to a reply joins that reply's thread.
    """
    _member_channel(db, channel_id, user, SEND_MEMBERS_ONLY_MESSAGE)
    body = payload.body.strip()
    if not body and payload.shared_resource is None:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=EMPTY_MESSAGE)
    shared = sharing.checked_share(db, payload.shared_resource, user)
    parent_id = None
    if payload.parent_id is not None:
        parent_id = service.thread_root(db, channel_id, payload.parent_id)
        if parent_id is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=MESSAGE_NOT_FOUND)
    message_id = service.post_channel_message(
        db,
        channel_id=channel_id,
        sender_id=user.id,
        body=body,
        parent_id=parent_id,
        shared_resource=shared,
    )
    db.commit()
    message = service.get_channel_message(db, channel_id, message_id)
    return _response(db, channel_id, message, {}, user)


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

    The main stream holds top-level messages; replies are shown under their parent,
    which carries ``reply_count`` and ``last_reply_at`` (S10.2).
    """
    _member_channel(db, channel_id, user, MEMBERS_ONLY_MESSAGE)
    if before is not None and after is not None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=BOTH_CURSORS_MESSAGE
        )
    messages, has_more = service.channel_messages_page(
        db, channel_id, limit=limit, before=before, after=after
    )
    return MessagePageResponse(
        items=_responses(db, channel_id, messages, user), has_more=has_more, limit=limit
    )


@router.get("/{channel_id}/messages/{message_id}/replies", response_model=ThreadPageResponse)
def read_thread(
    channel_id: int,
    message_id: int,
    limit: int = Query(default=50, ge=1, le=100),
    before: int | None = Query(default=None, ge=1, description="Older than this reply id"),
    after: int | None = Query(default=None, ge=0, description="Newer than this reply id"),
    user: UserSummary = Depends(current_user),
    db: Session = Depends(get_db),
) -> ThreadPageResponse:
    """A message's thread: the message itself, then its replies oldest first (IT_19).

    Paged like the main stream. Members only, checked on every request (IT_20). A
    reply's id opens the thread it belongs to.
    """
    _member_channel(db, channel_id, user, MEMBERS_ONLY_MESSAGE)
    if before is not None and after is not None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=BOTH_CURSORS_MESSAGE
        )
    root_id = service.thread_root(db, channel_id, message_id)
    root = service.get_channel_message(db, channel_id, root_id) if root_id else None
    if root is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=MESSAGE_NOT_FOUND)
    replies, has_more = service.channel_messages_page(
        db, channel_id, limit=limit, before=before, after=after, thread_of=root.id
    )
    parent, *items = _responses(db, channel_id, [root, *replies], user)
    return ThreadPageResponse(parent=parent, items=items, has_more=has_more, limit=limit)
