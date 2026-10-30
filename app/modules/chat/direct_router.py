"""HTTP surface of direct conversations (S11.1), mounted under ``/api/v1``.

A conversation is private to its two participants: anyone else gets 404, so its
existence is not revealed. Blocking is checked on every send, so a block takes effect
on the very next message and lasts until it is lifted. A reported message is filed in
the moderation queue (the moderation module's ``reports``) with its reason.
"""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.modules.accounts import service as accounts
from app.modules.accounts.service import Capability, UserSummary, current_user, require_capability
from app.modules.chat import service, sharing
from app.modules.chat.sharing import SharedCardResponse, SharedResourceRequest
from app.modules.moderation import service as moderation
from app.modules.moderation.service import ReportReason, ReportTarget

router = APIRouter(tags=["direct messages"])

EMPTY_MESSAGE = "Message cannot be empty"
CONVERSATION_NOT_FOUND = "Conversation not found"
USER_NOT_FOUND = "User not found"
MESSAGE_NOT_FOUND = "Message not found"
SELF_CONVERSATION = "You can't start a conversation with yourself"
SELF_BLOCK = "You can't block yourself"
YOU_BLOCKED_THEM = "You have blocked this user. Unblock them to send messages"
THEY_BLOCKED_YOU = "You can no longer send messages to this user"
CONFIRM_BLOCK = "Confirm blocking this user"
REASON_REQUIRED = "Select a reason for reporting this message"
OWN_MESSAGE = "You can't report your own message"
BOTH_CURSORS = "Use either before or after, not both"
REPORT_LOGGED = "Report submitted for moderation"
MAX_MESSAGE_LENGTH = 2000


# --------------------------------------------------------------------------- #
# Schemas
# --------------------------------------------------------------------------- #


class StartConversationRequest(BaseModel):
    user_id: int = Field(ge=1, description="The user to message (the profile's Message button)")


class Participant(BaseModel):
    id: int
    display_name: str | None
    username: str | None


class DirectMessageResponse(BaseModel):
    id: int
    conversation_id: int
    sender_id: int
    sender_name: str | None
    body: str
    created_at: datetime | None
    # The preview card of a shared resource, built from the original on every read.
    shared_resource: SharedCardResponse | None = None


class ConversationResponse(BaseModel):
    id: int
    other_user: Participant
    # "Blocked" indicator (UT_22): either side's block stops messages both ways.
    blocked: bool
    blocked_by_me: bool
    can_send: bool
    last_message: DirectMessageResponse | None
    created_at: datetime | None


class MessageRequest(BaseModel):
    # A missing body is treated like a blank one, so both get the same wording.
    body: str = Field(default="", max_length=MAX_MESSAGE_LENGTH)
    # Share a material, question or quiz as a live link (S11.2); the body may be blank.
    shared_resource: SharedResourceRequest | None = None


class MessagePageResponse(BaseModel):
    items: list[DirectMessageResponse]
    # More messages lie beyond this page in the direction read: older ones when
    # opening the conversation or paging with ``before``, newer ones with ``after``.
    has_more: bool
    limit: int


class BlockRequest(BaseModel):
    confirm: bool = Field(default=False, description="Must be true: the UI's confirm step")
    reason: str | None = Field(default=None, max_length=255)


class BlockResponse(BaseModel):
    user: Participant
    blocked: bool


class MessageReportRequest(BaseModel):
    # Optional in the schema so a missing reason gets REASON_REQUIRED (422).
    reason: ReportReason | None = Field(
        default=None, description='"inappropriate" is the "Inappropriate content" report'
    )
    details: str | None = Field(default=None, max_length=2000)


class MessageReportResponse(BaseModel):
    report_id: int
    message_id: int
    conversation_id: int
    reason: ReportReason
    reporter_id: int
    reported_at: datetime
    status: str
    message: str


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _invalid(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=detail)


def _participant(db: Session, user_id: int) -> Participant:
    return _as_participant(user_id, accounts.get_user(db, user_id))


def _as_participant(user_id: int, user: UserSummary | None) -> Participant:
    if user is None:
        return Participant(id=user_id, display_name=None, username=None)
    return Participant(id=user.id, display_name=user.display_name, username=user.username)


def _target_user(db: Session, user_id: int) -> UserSummary:
    user = accounts.get_user(db, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=USER_NOT_FOUND)
    return user


def _conversation(db: Session, conversation_id: int, user: UserSummary):
    conv = service.get_conversation(db, conversation_id, user.id)
    if conv is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=CONVERSATION_NOT_FOUND)
    return conv


def _message(
    db: Session, m: service.DirectMessageView, names: dict[int, str | None], viewer: UserSummary
) -> DirectMessageResponse:
    if m.sender_id not in names:
        names[m.sender_id] = _participant(db, m.sender_id).display_name
    return DirectMessageResponse(
        id=m.id,
        conversation_id=m.conversation_id,
        sender_id=m.sender_id,
        sender_name=names[m.sender_id],
        body=m.body,
        created_at=m.created_at,
        shared_resource=sharing.message_card(
            db, m.shared_resource_type, m.shared_resource_id, viewer
        ),
    )


def _conversation_response(
    db: Session,
    conv: service.ConversationSummary,
    user: UserSummary,
    *,
    people: dict[int, UserSummary] | None = None,
    blocks: tuple[set[int], set[int]] | None = None,
) -> ConversationResponse:
    """One conversation as the caller sees it.

    A list passes ``people`` and ``blocks`` fetched once for every conversation, so
    listing does not cost queries per conversation (NFR-05).
    """
    if blocks is None:
        by_me = service.has_blocked(db, user.id, conv.other_user_id)
        blocked = by_me or service.has_blocked(db, conv.other_user_id, user.id)
    else:
        by_me = conv.other_user_id in blocks[0]
        blocked = by_me or conv.other_user_id in blocks[1]
    if people is None:
        other = _participant(db, conv.other_user_id)
    else:
        other = _as_participant(conv.other_user_id, people.get(conv.other_user_id))
    names = {} if people is None else {uid: u.display_name for uid, u in people.items()}
    return ConversationResponse(
        id=conv.id,
        other_user=other,
        blocked=blocked,
        blocked_by_me=by_me,
        can_send=not blocked,
        last_message=_message(db, conv.last_message, names, user) if conv.last_message else None,
        created_at=conv.created_at,
    )


# --------------------------------------------------------------------------- #
# Conversations and messages
# --------------------------------------------------------------------------- #


@router.post(
    "/conversations",
    status_code=status.HTTP_201_CREATED,
    response_model=ConversationResponse,
    responses={200: {"description": "The conversation already existed"}},
)
def start_conversation(
    payload: StartConversationRequest,
    response: Response,
    user: UserSummary = Depends(current_user),
    db: Session = Depends(get_db),
) -> ConversationResponse:
    """Open a direct conversation with any user (UT_21): **201** when it is new, **200**
    with the existing one otherwise, so two users always share one conversation."""
    if payload.user_id == user.id:
        raise _invalid(SELF_CONVERSATION)
    other = _target_user(db, payload.user_id)
    existing = service.find_conversation(db, user.id, other.id)
    if existing is not None:
        response.status_code = status.HTTP_200_OK
        conversation_id = existing
    else:
        conversation_id = service.open_conversation(db, user.id, other.id)
        db.commit()
    return _conversation_response(db, _conversation(db, conversation_id, user), user)


@router.get("/conversations", response_model=list[ConversationResponse])
def list_conversations(
    user: UserSummary = Depends(current_user), db: Session = Depends(get_db)
) -> list[ConversationResponse]:
    """The caller's conversations, most recent activity first."""
    conversations = service.conversations_for(db, user.id)
    people = accounts.get_users(
        db,
        {c.other_user_id for c in conversations}
        | {c.last_message.sender_id for c in conversations if c.last_message},
    )
    blocks = service.blocks_involving(db, user.id)
    return [
        _conversation_response(db, c, user, people=people, blocks=blocks) for c in conversations
    ]


@router.get("/conversations/{conversation_id}", response_model=ConversationResponse)
def read_conversation(
    conversation_id: int,
    user: UserSummary = Depends(current_user),
    db: Session = Depends(get_db),
) -> ConversationResponse:
    """One conversation with its blocked indicator. Participants only (others get 404)."""
    return _conversation_response(db, _conversation(db, conversation_id, user), user)


@router.post(
    "/conversations/{conversation_id}/messages",
    status_code=status.HTTP_201_CREATED,
    response_model=DirectMessageResponse,
)
def send_message(
    conversation_id: int,
    payload: MessageRequest,
    user: UserSummary = Depends(current_user),
    db: Session = Depends(get_db),
) -> DirectMessageResponse:
    """Send a direct message (UT_21). The sender and time are the server's.

    Refused with **403** while either participant has blocked the other (UT_22); the
    check runs on every send, so a block takes effect at once.
    """
    conv = _conversation(db, conversation_id, user)
    if service.has_blocked(db, user.id, conv.other_user_id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=YOU_BLOCKED_THEM)
    if service.has_blocked(db, conv.other_user_id, user.id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=THEY_BLOCKED_YOU)
    body = payload.body.strip()
    if not body and payload.shared_resource is None:
        raise _invalid(EMPTY_MESSAGE)
    shared = sharing.checked_share(db, payload.shared_resource, user)
    message_id = service.send_direct_message(
        db, conversation_id=conversation_id, sender_id=user.id, body=body, shared_resource=shared
    )
    db.commit()
    return _message(db, service.get_direct_message(db, message_id), {}, user)


@router.get("/conversations/{conversation_id}/messages", response_model=MessagePageResponse)
def read_messages(
    conversation_id: int,
    limit: int = Query(default=50, ge=1, le=100),
    before: int | None = Query(default=None, ge=1, description="Older than this message id"),
    after: int | None = Query(default=None, ge=0, description="Newer than this message id"),
    user: UserSummary = Depends(current_user),
    db: Session = Depends(get_db),
) -> MessagePageResponse:
    """The conversation's history, oldest first within each page (IT_21).

    With no cursor this is the newest ``limit`` messages. Scroll up with
    ``before=<oldest id shown>`` until ``has_more`` is false; poll for new messages with
    ``after=<newest id shown>``. History stays readable after a block.
    """
    _conversation(db, conversation_id, user)
    if before is not None and after is not None:
        raise _invalid(BOTH_CURSORS)
    messages, has_more = service.direct_messages_page(
        db, conversation_id, limit=limit, before=before, after=after
    )
    names: dict[int, str | None] = {}
    return MessagePageResponse(
        items=[_message(db, m, names, user) for m in messages], has_more=has_more, limit=limit
    )


@router.post(
    "/conversations/{conversation_id}/messages/{message_id}/reports",
    status_code=status.HTTP_201_CREATED,
    response_model=MessageReportResponse,
)
def report_message(
    conversation_id: int,
    message_id: int,
    payload: MessageReportRequest,
    user: UserSummary = Depends(require_capability(Capability.REPORT_SUBMIT)),
    db: Session = Depends(get_db),
) -> MessageReportResponse:
    """Report a received message (IT_22), for example as "Inappropriate content".

    The report is filed as open in the moderation queue with the message's id and the
    reason. Reporting the same message again while that report is open returns it.
    """
    _conversation(db, conversation_id, user)
    message = service.get_direct_message(db, message_id)
    if message is None or message.conversation_id != conversation_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=MESSAGE_NOT_FOUND)
    if message.sender_id == user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=OWN_MESSAGE)
    if payload.reason is None:
        raise _invalid(REASON_REQUIRED)
    report_id = moderation.find_open_report(
        db, reporter_id=user.id, target_type=ReportTarget.DIRECT_MESSAGE, target_id=message_id
    )
    if report_id is None:
        details = (payload.details or "").strip() or None
        report_id = moderation.file_report(
            db,
            reporter_id=user.id,
            target_type=ReportTarget.DIRECT_MESSAGE,
            target_id=message_id,
            reason=payload.reason,
            details=details,
        )
        db.commit()
    report = moderation.get_report(db, report_id)
    return MessageReportResponse(
        report_id=report.id,
        message_id=message_id,
        conversation_id=conversation_id,
        reason=report.reason,
        reporter_id=report.reporter_id,
        reported_at=report.created_at,
        status=report.status.value,
        message=REPORT_LOGGED,
    )


# --------------------------------------------------------------------------- #
# Blocking
# --------------------------------------------------------------------------- #


@router.get("/blocks", response_model=list[Participant])
def list_blocks(
    user: UserSummary = Depends(current_user), db: Session = Depends(get_db)
) -> list[Participant]:
    """The users the caller has blocked."""
    return [_participant(db, uid) for uid in service.blocked_by(db, user.id)]


@router.put("/blocks/{user_id}", response_model=BlockResponse)
def block_user(
    user_id: int,
    payload: BlockRequest,
    user: UserSummary = Depends(current_user),
    db: Session = Depends(get_db),
) -> BlockResponse:
    """Block a user after confirming (UT_22). Stored, so it lasts across sessions;
    blocking someone already blocked changes nothing."""
    if user_id == user.id:
        raise _invalid(SELF_BLOCK)
    other = _target_user(db, user_id)
    if not payload.confirm:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=CONFIRM_BLOCK)
    reason = (payload.reason or "").strip() or None
    if service.block_user(db, user.id, other.id, reason):
        db.commit()
    return BlockResponse(user=_participant(db, other.id), blocked=True)


@router.delete("/blocks/{user_id}", response_model=BlockResponse)
def unblock_user(
    user_id: int,
    user: UserSummary = Depends(current_user),
    db: Session = Depends(get_db),
) -> BlockResponse:
    """Lift a block. Lifting one that doesn't exist changes nothing."""
    other = _target_user(db, user_id)
    if service.unblock_user(db, user.id, other.id):
        db.commit()
    return BlockResponse(user=_participant(db, other.id), blocked=False)
