"""Public interface of the LLM module.

Every call to the provider goes through ``record_request`` so there is an audit trail
and LLM output is never published without review (FR-07). The provider client itself
is added by the LLM stories; with ``LLM_PROVIDER=none`` the feature is disabled.

Every AI output a user sees (an explanation, a generated batch or topic quiz, a
descriptive evaluation) is one ``llm_requests`` row, so that row's id is what a user
reports as incorrect or inappropriate AI content and what moderation looks up and, if
the report is upheld, withdraws (NFR-06).
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.modules.llm.models import LlmRequest, LlmRequestStatus, LlmTask
from app.modules.questions import service as questions


def is_enabled() -> bool:
    """Return whether an LLM provider is configured (``fake`` counts; ``none`` does not)."""
    return get_settings().llm_enabled


def record_request(
    db: Session,
    *,
    user_id: int,
    task: LlmTask,
    prompt: str,
    channel_id: int | None = None,
    subject: str | None = None,
    topic: str | None = None,
) -> int:
    """Record a pending LLM request for auditing and reporting; returns its id."""
    req = LlmRequest(
        user_id=user_id,
        task=task,
        prompt=prompt,
        channel_id=channel_id,
        subject=subject,
        topic=topic,
    )
    db.add(req)
    db.flush()
    return req.id


def complete_request(
    db: Session, request_id: int, *, output: dict[str, Any] | None = None, error: str | None = None
) -> None:
    """Store a request's output or error and mark it succeeded or failed."""
    req = db.get(LlmRequest, request_id)
    if req is None:
        raise LookupError(f"llm request {request_id} not found")
    req.output, req.error = output, error
    req.status = LlmRequestStatus.FAILED if error else LlmRequestStatus.SUCCEEDED


# --------------------------------------------------------------------------- #
# NFR-06 - AI output as reportable content
# --------------------------------------------------------------------------- #

# What each task's output is called in the moderation queue.
TASK_LABELS = {
    LlmTask.GENERATE_QUESTIONS: "AI-generated questions",
    LlmTask.ADAPT_DIFFICULTY: "AI difficulty choice",
    LlmTask.EVALUATE_ANSWER: "AI answer evaluation",
    LlmTask.EXPLAIN: "AI explanation",
    LlmTask.GENERATE_QUIZ: "AI topic quiz",
}
EXCERPT_LENGTH = 300


@dataclass(frozen=True)
class AiResponseView:
    """One AI output as moderation sees it: who asked, about what, and what it said."""

    id: int
    user_id: int
    channel_id: int | None
    task: LlmTask
    label: str
    subject: str | None
    topic: str | None
    excerpt: str
    withdrawn: bool
    created_at: datetime


def _excerpt(db: Session, output: dict[str, Any] | None) -> str:
    """A short human-readable part of the output, for the moderator to judge."""
    out = output or {}
    text = ""
    for key in ("explanation", "summary", "text"):
        value = out.get(key)
        if isinstance(value, str) and value.strip():
            text = " ".join(value.split())
            break
    else:
        ids = [int(i) for i in out.get("question_ids") or []]
        found = questions.get_questions(db, ids)
        text = " / ".join(found[i].text for i in ids if i in found)
    if len(text) > EXCERPT_LENGTH:
        text = text[: EXCERPT_LENGTH - 1].rstrip() + "\u2026"
    return text


def get_response(db: Session, request_id: int) -> AiResponseView | None:
    """A completed AI output, or None if it does not exist or produced nothing."""
    req = db.get(LlmRequest, request_id)
    if req is None or req.status is not LlmRequestStatus.SUCCEEDED:
        return None
    return AiResponseView(
        id=req.id,
        user_id=req.user_id,
        channel_id=req.channel_id,
        task=req.task,
        label=TASK_LABELS.get(req.task, "AI response"),
        subject=req.subject,
        topic=req.topic,
        excerpt=_excerpt(db, req.output),
        withdrawn=bool(req.withdrawn),
        created_at=req.created_at,
    )


def set_withdrawn(db: Session, request_id: int, withdrawn: bool) -> None:
    """Withdraw an AI output after an upheld report (or put it back)."""
    req = db.get(LlmRequest, request_id)
    if req is None:
        raise LookupError(f"llm request {request_id} not found")
    req.withdrawn = withdrawn
    db.flush()


def is_withdrawn(db: Session, request_id: int) -> bool:
    """Return whether moderation withdrew this AI output (an upheld report)."""
    req = db.get(LlmRequest, request_id)
    return bool(req is not None and req.withdrawn)
