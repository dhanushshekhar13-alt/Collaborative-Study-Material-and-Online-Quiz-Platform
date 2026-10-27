"""Public interface of the chat module (channel threads and direct messages)."""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modules.chat.models import (
    ChannelMessage,
    Conversation,
    ConversationParticipant,
    DirectMessage,
    SharedResourceType,
    UserBlock,
)


@dataclass(frozen=True)
class MessageView:
    id: int
    sender_id: int
    body: str
    parent_id: int | None
    shared_resource_type: SharedResourceType | None
    shared_resource_id: int | None
    created_at: datetime


def post_channel_message(
    db: Session,
    *,
    channel_id: int,
    sender_id: int,
    body: str,
    parent_id: int | None = None,
    shared_resource: tuple[SharedResourceType, int] | None = None,
) -> int:
    msg = ChannelMessage(channel_id=channel_id, sender_id=sender_id, body=body, parent_id=parent_id)
    if shared_resource:
        msg.shared_resource_type, msg.shared_resource_id = shared_resource
    db.add(msg)
    db.flush()
    return msg.id


def channel_history(db: Session, channel_id: int, limit: int = 50) -> list[MessageView]:
    rows = db.scalars(
        select(ChannelMessage)
        .where(ChannelMessage.channel_id == channel_id, ChannelMessage.is_deleted.is_(False))
        .order_by(ChannelMessage.created_at, ChannelMessage.id)
        .limit(limit)
    )
    return [_view(m) for m in rows]


def _view(m: ChannelMessage) -> MessageView:
    return MessageView(
        m.id,
        m.sender_id,
        m.body,
        m.parent_id,
        m.shared_resource_type,
        m.shared_resource_id,
        m.created_at,
    )


def get_channel_message(db: Session, channel_id: int, message_id: int) -> MessageView | None:
    """One message of a channel's chat, or None if it is not there (or was deleted)."""
    m = db.get(ChannelMessage, message_id)
    if m is None or m.channel_id != channel_id or m.is_deleted:
        return None
    return _view(m)


@dataclass(frozen=True)
class ThreadSummary:
    """What the main stream shows under a message that has replies (S10.2)."""

    reply_count: int
    last_reply_at: datetime | None


def thread_root(db: Session, channel_id: int, message_id: int) -> int | None:
    """The message a reply to ``message_id`` belongs under, or None if it can't be replied to.

    Threads are one level deep: replying to a reply joins the thread of its parent, so
    a conversation never disappears into nested sub-threads. A missing, deleted or
    other-channel message (or a reply whose parent was deleted) cannot be replied to.
    """
    target = get_channel_message(db, channel_id, message_id)
    if target is None:
        return None
    if target.parent_id is None:
        return target.id
    root = get_channel_message(db, channel_id, target.parent_id)
    return root.id if root is not None else None


def thread_summaries(db: Session, message_ids: list[int]) -> dict[int, ThreadSummary]:
    """Visible reply count and latest reply time for each message that has replies."""
    if not message_ids:
        return {}
    rows = db.execute(
        select(
            ChannelMessage.parent_id,
            func.count(ChannelMessage.id),
            func.max(ChannelMessage.created_at),
        )
        .where(
            ChannelMessage.parent_id.in_(message_ids),
            ChannelMessage.is_deleted.is_(False),
        )
        .group_by(ChannelMessage.parent_id)
    )
    return {parent: ThreadSummary(count, last) for parent, count, last in rows}


def channel_messages_page(
    db: Session,
    channel_id: int,
    *,
    limit: int = 50,
    before: int | None = None,
    after: int | None = None,
    thread_of: int | None = None,
) -> tuple[list[MessageView], bool]:
    """A page of a channel's chat in the order the server accepted the messages (S10.1).

    Order is the message id, which the database assigns in commit order, so two
    messages sent within the same second never swap places the way a timestamp sort
    could. Every page is returned oldest first, ready to render top to bottom.

    * neither cursor: the newest ``limit`` messages (opening the chat);
    * ``before``: the ``limit`` messages just older than that id (scrolling up);
    * ``after``: the ``limit`` messages just newer than that id (catching up).

    The flag says whether more messages lie beyond the page in the direction read:
    older ones for the first two, newer ones for ``after``.

    Without ``thread_of`` the page is the main stream: top-level messages only (S10.2).
    With ``thread_of=<message id>`` it is that message's replies, paged the same way.
    """
    if before is not None and after is not None:
        raise ValueError("before and after cannot be combined")
    query = select(ChannelMessage).where(
        ChannelMessage.channel_id == channel_id,
        ChannelMessage.is_deleted.is_(False),
        # The main stream holds top-level messages only; a thread is read on its own.
        ChannelMessage.parent_id.is_(None)
        if thread_of is None
        else ChannelMessage.parent_id == thread_of,
    )
    if after is not None:
        query = query.where(ChannelMessage.id > after).order_by(ChannelMessage.id)
    else:
        if before is not None:
            query = query.where(ChannelMessage.id < before)
        query = query.order_by(ChannelMessage.id.desc())
    rows = list(db.scalars(query.limit(limit + 1)))
    has_more = len(rows) > limit
    rows = rows[:limit]
    if after is None:
        rows.reverse()
    return [_view(m) for m in rows], has_more


def is_blocked(db: Session, sender_id: int, recipient_id: int) -> bool:
    """True if either user has blocked the other."""
    return (
        db.scalar(
            select(UserBlock.id).where(
                ((UserBlock.blocker_id == sender_id) & (UserBlock.blocked_id == recipient_id))
                | ((UserBlock.blocker_id == recipient_id) & (UserBlock.blocked_id == sender_id))
            )
        )
        is not None
    )


def open_conversation(db: Session, user_a: int, user_b: int) -> int:
    """Return the id of the one-to-one conversation between two users, creating it."""
    a_convs = select(ConversationParticipant.conversation_id).where(
        ConversationParticipant.user_id == user_a
    )
    existing = db.scalar(
        select(ConversationParticipant.conversation_id).where(
            ConversationParticipant.user_id == user_b,
            ConversationParticipant.conversation_id.in_(a_convs),
        )
    )
    if existing is not None:
        return existing
    conv = Conversation()
    db.add(conv)
    db.flush()
    db.add_all(
        [
            ConversationParticipant(conversation_id=conv.id, user_id=user_a),
            ConversationParticipant(conversation_id=conv.id, user_id=user_b),
        ]
    )
    db.flush()
    return conv.id


def send_direct_message(db: Session, *, conversation_id: int, sender_id: int, body: str) -> int:
    msg = DirectMessage(conversation_id=conversation_id, sender_id=sender_id, body=body)
    db.add(msg)
    db.flush()
    return msg.id
