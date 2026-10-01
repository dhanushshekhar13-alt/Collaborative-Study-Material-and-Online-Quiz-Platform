from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class Question(Base):
    __tablename__ = "questions"

    id: Mapped[int] = mapped_column(primary_key=True)
    prompt: Mapped[str] = mapped_column(Text())
    options: Mapped[str] = mapped_column(
        Text(), doc="Serialized options; replace with structured JSON in API contract."
    )
    correct_answer: Mapped[str] = mapped_column(String(500))
    explanation: Mapped[str | None] = mapped_column(Text(), nullable=True)
    subject: Mapped[str] = mapped_column(String(120), index=True)
    topic: Mapped[str] = mapped_column(String(120), index=True)
    difficulty: Mapped[str] = mapped_column(String(20))
    question_type: Mapped[str] = mapped_column(String(30))
    author_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    channel_id: Mapped[int | None] = mapped_column(
        ForeignKey("channels.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
