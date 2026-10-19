"""Public interface of the LLM module.

Every call to the provider goes through ``record_request`` so there is an audit trail
and LLM output is never published without review (FR-07). The provider client itself
is added by the LLM stories; with ``LLM_PROVIDER=none`` the feature is disabled.
"""

from typing import Any

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.modules.llm.models import LlmRequest, LlmRequestStatus, LlmTask


def is_enabled() -> bool:
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
    req = db.get(LlmRequest, request_id)
    if req is None:
        raise LookupError(f"llm request {request_id} not found")
    req.output, req.error = output, error
    req.status = LlmRequestStatus.FAILED if error else LlmRequestStatus.SUCCEEDED
