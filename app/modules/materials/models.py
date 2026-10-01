from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class StudyMaterial(Base):
    __tablename__ = "study_materials"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(200), index=True)
    description: Mapped[str | None] = mapped_column(Text(), nullable=True)
    subject: Mapped[str] = mapped_column(String(120), index=True)
    topic: Mapped[str] = mapped_column(String(120), index=True)
    semester: Mapped[str | None] = mapped_column(String(40), nullable=True)
    material_type: Mapped[str] = mapped_column(String(40))
    source_url: Mapped[str] = mapped_column(String(2048))
    uploader_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    channel_id: Mapped[int | None] = mapped_column(
        ForeignKey("channels.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class MaterialRating(Base):
    __tablename__ = "material_ratings"

    id: Mapped[int] = mapped_column(primary_key=True)
    material_id: Mapped[int] = mapped_column(ForeignKey("study_materials.id", ondelete="CASCADE"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    rating: Mapped[int] = mapped_column(Integer)
    review: Mapped[str | None] = mapped_column(Text(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
