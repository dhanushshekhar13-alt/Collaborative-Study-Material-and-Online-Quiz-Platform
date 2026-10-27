"""In-process domain events, so a module can react to another without being imported by it.

The publisher names an event and passes the database session it is working in; every
handler runs synchronously in that same transaction. A handler's writes therefore
commit or roll back together with the change that triggered them.

This keeps the dependency graph pointing one way: quizzes publishes "an attempt was
scored" without knowing analytics exists, and analytics (which may call quizzes)
subscribes to it.
"""

from collections import defaultdict
from collections.abc import Callable
from typing import Any

from sqlalchemy.orm import Session

Handler = Callable[..., None]

# Events another module subscribes to without importing the publisher. Each names its
# payload; handlers receive it as keyword arguments after the session.

# chat: channel_id, channel_name, message_id, sender_id, reply_to_user_id (the author
# of the message replied to, or None) and mentioned_user_ids (members named @username).
CHANNEL_MESSAGE_POSTED = "chat.channel_message_posted"
# channels: channel_id, channel_name, post_id, author_id, member_ids.
CHANNEL_POST_CREATED = "channels.post_created"
# quizzes: quiz_id, title, channel_id, channel_name, creator_id, member_ids.
QUIZ_PUBLISHED = "quizzes.quiz_published"

_handlers: dict[str, list[Handler]] = defaultdict(list)


def subscribe(event: str, handler: Handler) -> None:
    """Call ``handler(db, **payload)`` whenever ``event`` is published (idempotent)."""
    if handler not in _handlers[event]:
        _handlers[event].append(handler)


def publish(event: str, db: Session, **payload: Any) -> None:
    for handler in list(_handlers.get(event, ())):
        handler(db, **payload)
