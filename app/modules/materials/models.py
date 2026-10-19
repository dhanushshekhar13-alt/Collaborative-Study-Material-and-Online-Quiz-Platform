"""Study material data (FR-04): shared resources and their ratings/reviews."""

import enum

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.core.schema import CreatedAtMixin, enum_column


class MaterialType(enum.StrEnum):
    NOTES = "notes"
    PDF = "pdf"
    PRESENTATION = "presentation"
    LINK = "link"
    OTHER = "other"


class ContentStatus(enum.StrEnum):
    ACTIVE = "active"
    REMOVED = "removed"


class StudyMaterial(CreatedAtMixin, Base):
    __tablename__ = "study_materials"

    id: Mapped[int] = mapped_column(primary_key=True)
    channel_id: Mapped[int | None] = mapped_column(
        ForeignKey("channels.id", ondelete="CASCADE"), index=True
    )
    uploader_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text)
    material_type: Mapped[MaterialType] = mapped_column(enum_column(MaterialType, "material_type"))
    subject: Mapped[str] = mapped_column(String(100), index=True)
    topic: Mapped[str | None] = mapped_column(String(100), index=True)
    semester: Mapped[str | None] = mapped_column(String(20))
    file_path: Mapped[str | None] = mapped_column(String(500))
    url: Mapped[str | None] = mapped_column(String(1000))
    is_official: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    status: Mapped[ContentStatus] = mapped_column(
        enum_column(ContentStatus, "material_status"), default=ContentStatus.ACTIVE
    )


class MaterialReview(CreatedAtMixin, Base):
    __tablename__ = "material_reviews"
    __table_args__ = (
        UniqueConstraint("material_id", "user_id", name="uq_material_review"),
        CheckConstraint("rating BETWEEN 1 AND 5", name="ck_material_review_rating"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    material_id: Mapped[int] = mapped_column(ForeignKey("study_materials.id", ondelete="CASCADE"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    rating: Mapped[int] = mapped_column(Integer)
    review: Mapped[str | None] = mapped_column(Text)
    is_helpful: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
