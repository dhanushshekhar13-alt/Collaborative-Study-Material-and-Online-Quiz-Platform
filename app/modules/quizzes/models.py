"""Quiz data (FR-05, FR-06, FR-07): quizzes, their questions, attempts and answers."""

import enum
from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.core.schema import CreatedAtMixin, enum_column


class QuizStatus(enum.StrEnum):
    DRAFT = "draft"
    PUBLISHED = "published"
    REMOVED = "removed"


class AttemptStatus(enum.StrEnum):
    IN_PROGRESS = "in_progress"
    SUBMITTED = "submitted"


class Quiz(CreatedAtMixin, Base):
    __tablename__ = "quizzes"

    id: Mapped[int] = mapped_column(primary_key=True)
    channel_id: Mapped[int | None] = mapped_column(
        ForeignKey("channels.id", ondelete="CASCADE"), index=True
    )
    creator_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    subject: Mapped[str] = mapped_column(String(100), index=True)
    topic: Mapped[str] = mapped_column(String(100), index=True)
    difficulty: Mapped[str] = mapped_column(String(16))
    duration_minutes: Mapped[int] = mapped_column(Integer)
    is_adaptive: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    status: Mapped[QuizStatus] = mapped_column(
        enum_column(QuizStatus, "quiz_status"), default=QuizStatus.DRAFT
    )


class QuizQuestion(Base):
    __tablename__ = "quiz_questions"
    __table_args__ = (UniqueConstraint("quiz_id", "question_id", name="uq_quiz_question"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    quiz_id: Mapped[int] = mapped_column(ForeignKey("quizzes.id", ondelete="CASCADE"))
    question_id: Mapped[int] = mapped_column(ForeignKey("questions.id"))
    position: Mapped[int] = mapped_column(Integer)
    points: Mapped[float] = mapped_column(Float, default=1.0, server_default="1")


class QuizAttempt(Base):
    __tablename__ = "quiz_attempts"

    id: Mapped[int] = mapped_column(primary_key=True)
    quiz_id: Mapped[int] = mapped_column(ForeignKey("quizzes.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    status: Mapped[AttemptStatus] = mapped_column(
        enum_column(AttemptStatus, "attempt_status"), default=AttemptStatus.IN_PROGRESS
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    score: Mapped[float | None] = mapped_column(Float)
    max_score: Mapped[float | None] = mapped_column(Float)
    # S06.1: the time limit fixed when the attempt starts (null = untimed), and whether
    # the server closed the attempt because that limit passed.
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    auto_submitted: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")


class AttemptAnswer(Base):
    __tablename__ = "attempt_answers"
    __table_args__ = (UniqueConstraint("attempt_id", "question_id", name="uq_attempt_answer"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    attempt_id: Mapped[int] = mapped_column(ForeignKey("quiz_attempts.id", ondelete="CASCADE"))
    question_id: Mapped[int] = mapped_column(ForeignKey("questions.id"))
    response: Mapped[str | None] = mapped_column(Text)
    is_correct: Mapped[bool | None] = mapped_column(Boolean)
    score: Mapped[float | None] = mapped_column(Float)
    difficulty_served: Mapped[str | None] = mapped_column(String(16))  # adaptive (FR-07)
    feedback: Mapped[dict[str, Any] | None] = mapped_column(JSON)  # descriptive (FR-08)
