"""LLM interaction data (FR-07, FR-08, FR-09): an audit record of every LLM request."""

import enum
from typing import Any

from sqlalchemy import JSON, Boolean, ForeignKey, Integer, String, Text
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
    channel_id: Mapped[int | None] = mapped_column(
        ForeignKey("channels.id", ondelete="SET NULL"), index=True
    )
    task: Mapped[LlmTask] = mapped_column(enum_column(LlmTask, "llm_task"))
    subject: Mapped[str | None] = mapped_column(String(100))
    topic: Mapped[str | None] = mapped_column(String(100))
    prompt: Mapped[str] = mapped_column(Text)
    status: Mapped[LlmRequestStatus] = mapped_column(
        enum_column(LlmRequestStatus, "llm_request_status"), default=LlmRequestStatus.PENDING
    )
    output: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(Text)
    # NFR-06: a moderator who upholds a report on this AI output withdraws it, so its
    # topic quiz or review screen can no longer be opened. The row itself is kept.
    withdrawn: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")


class AdaptiveSession(CreatedAtMixin, Base):
    """One adaptive quiz run (S07.2): the selected topic, its length and where it started.

    The questions served, the answers and their difficulty live in the quizzes module
    (``quiz_questions``, ``attempt_answers.difficulty_served``); the current difficulty
    is recomputed from that history, so there is no second copy to drift.
    """

    __tablename__ = "adaptive_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    quiz_id: Mapped[int] = mapped_column(ForeignKey("quizzes.id", ondelete="CASCADE"), index=True)
    attempt_id: Mapped[int] = mapped_column(
        ForeignKey("quiz_attempts.id", ondelete="CASCADE"), unique=True
    )
    channel_id: Mapped[int | None] = mapped_column(
        ForeignKey("channels.id", ondelete="SET NULL"), index=True
    )
    subject: Mapped[str] = mapped_column(String(100))
    topic: Mapped[str] = mapped_column(String(100))
    start_difficulty: Mapped[str] = mapped_column(String(16))
    question_count: Mapped[int] = mapped_column(Integer)
