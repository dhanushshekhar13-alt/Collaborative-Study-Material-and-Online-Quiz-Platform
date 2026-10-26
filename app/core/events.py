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

_handlers: dict[str, list[Handler]] = defaultdict(list)


def subscribe(event: str, handler: Handler) -> None:
    """Call ``handler(db, **payload)`` whenever ``event`` is published (idempotent)."""
    if handler not in _handlers[event]:
        _handlers[event].append(handler)


def publish(event: str, db: Session, **payload: Any) -> None:
    for handler in list(_handlers.get(event, ())):
        handler(db, **payload)
