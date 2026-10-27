"""Stored performance figures (S12.1, FR-12).

Tables written only by ``app.modules.analytics.service``:

* ``performance_attempts`` is the ledger: one row per submitted quiz attempt holding
  exactly what that attempt contributed (subject, topic, score, max score, time). It
  also feeds the trend chart.
* ``performance_topics`` is the running aggregate per user, subject and topic: attempt
  count and summed points. Every change to the ledger applies the same delta here, so
  the two always reconcile, and both can be rebuilt from the quizzes module's raw
  attempts.

``performance_attempts.attempt_id`` is a plain integer, not a foreign key: if an
attempt disappears (its quiz is deleted), the ledger row survives long enough for the
next sync to subtract its contribution instead of silently losing it.

* ``recommendation_opens`` (S12.2) records which recommended items the user opened, so
  they are de-prioritised in later recommendations.
"""

from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class PerformanceAttempt(Base):
    __tablename__ = "performance_attempts"

    attempt_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    quiz_id: Mapped[int] = mapped_column(Integer)
    quiz_title: Mapped[str] = mapped_column(String(200))
    subject: Mapped[str] = mapped_column(String(100))
    topic: Mapped[str] = mapped_column(String(100))
    score: Mapped[float] = mapped_column(Float)
    max_score: Mapped[float] = mapped_column(Float)
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class PerformanceTopic(Base):
    __tablename__ = "performance_topics"
    __table_args__ = (UniqueConstraint("user_id", "subject", "topic"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    subject: Mapped[str] = mapped_column(String(100))
    topic: Mapped[str] = mapped_column(String(100))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    total_score: Mapped[float] = mapped_column(Float, default=0.0)
    total_possible: Mapped[float] = mapped_column(Float, default=0.0)
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class RecommendationOpen(Base):
    """A recommended item the user opened (S12.2), so it is de-prioritised next time.

    The item is a type plus an id rather than a foreign key, because it can be a
    material, a question or a quiz, each owned by another module.
    """

    __tablename__ = "recommendation_opens"
    __table_args__ = (UniqueConstraint("user_id", "item_type", "item_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    item_type: Mapped[str] = mapped_column(String(16))
    item_id: Mapped[int] = mapped_column(Integer)
    opened_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
