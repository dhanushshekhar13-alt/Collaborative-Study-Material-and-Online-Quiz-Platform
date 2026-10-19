"""LLM interaction data (FR-07, FR-08, FR-09): an audit record of every LLM request."""

import enum
from typing import Any

from sqlalchemy import JSON, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.core.schema import CreatedAtMixin, enum_column


class LlmTask(enum.StrEnum):
    GENERATE_QUESTIONS = "generate_questions"
    ADAPT_DIFFICULTY = "adapt_difficulty"
    EVALUATE_ANSWER = "evaluate_answer"
    EXPLAIN = "explain"
    GENERATE_QUIZ = "generate_quiz"


class LlmRequestStatus(enum.StrEnum):
    PENDING = "pending"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class LlmRequest(CreatedAtMixin, Base):
    __tablename__ = "llm_requests"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    channel_id: Mapped[int | None] = mapped_column(ForeignKey("channels.id", ondelete="SET NULL"))
    task: Mapped[LlmTask] = mapped_column(enum_column(LlmTask, "llm_task"))
    subject: Mapped[str | None] = mapped_column(String(100))
    topic: Mapped[str | None] = mapped_column(String(100))
    prompt: Mapped[str] = mapped_column(Text)
    status: Mapped[LlmRequestStatus] = mapped_column(
        enum_column(LlmRequestStatus, "llm_request_status"), default=LlmRequestStatus.PENDING
    )
    output: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(Text)
