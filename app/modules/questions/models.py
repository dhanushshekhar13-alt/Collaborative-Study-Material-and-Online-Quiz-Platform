"""Question bank data (FR-05, FR-07): peer and LLM-generated questions and ratings."""

import enum
from typing import Any

from sqlalchemy import JSON, CheckConstraint, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.core.schema import CreatedAtMixin, enum_column


class Difficulty(enum.StrEnum):
    EASY = "easy"
    MEDIUM = "medium"
    HARD = "hard"


class QuestionType(enum.StrEnum):
    MULTIPLE_CHOICE = "multiple_choice"
    TRUE_FALSE = "true_false"
    SHORT_ANSWER = "short_answer"
    DESCRIPTIVE = "descriptive"


class QuestionSource(enum.StrEnum):
    PEER = "peer"
    LLM = "llm"


class QuestionStatus(enum.StrEnum):
    DRAFT = "draft"  # LLM output awaiting review (FR-07)
    PUBLISHED = "published"
    REMOVED = "removed"


class Question(CreatedAtMixin, Base):
    __tablename__ = "questions"

    id: Mapped[int] = mapped_column(primary_key=True)
    channel_id: Mapped[int | None] = mapped_column(
        ForeignKey("channels.id", ondelete="CASCADE"), index=True
    )
    author_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    subject: Mapped[str] = mapped_column(String(100), index=True)
    topic: Mapped[str] = mapped_column(String(100), index=True)
    difficulty: Mapped[Difficulty] = mapped_column(enum_column(Difficulty, "question_difficulty"))
    question_type: Mapped[QuestionType] = mapped_column(enum_column(QuestionType, "question_type"))
    text: Mapped[str] = mapped_column(Text)
    options: Mapped[list[str] | None] = mapped_column(JSON)
    correct_answer: Mapped[str | None] = mapped_column(Text)
    explanation: Mapped[str | None] = mapped_column(Text)
    expected_concepts: Mapped[list[str] | None] = mapped_column(JSON)  # descriptive (FR-08)
    source: Mapped[QuestionSource] = mapped_column(
        enum_column(QuestionSource, "question_source"), default=QuestionSource.PEER
    )
    status: Mapped[QuestionStatus] = mapped_column(
        enum_column(QuestionStatus, "question_status"), default=QuestionStatus.PUBLISHED
    )
    extra: Mapped[dict[str, Any] | None] = mapped_column(JSON)


class QuestionRating(CreatedAtMixin, Base):
    __tablename__ = "question_ratings"
    __table_args__ = (
        UniqueConstraint("question_id", "user_id", name="uq_question_rating"),
        CheckConstraint("rating BETWEEN 1 AND 5", name="ck_question_rating_value"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    question_id: Mapped[int] = mapped_column(ForeignKey("questions.id", ondelete="CASCADE"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    rating: Mapped[int] = mapped_column(Integer)
