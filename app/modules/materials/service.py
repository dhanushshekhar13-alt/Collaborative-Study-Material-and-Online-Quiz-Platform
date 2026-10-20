"""Public interface of the materials module."""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modules.materials.models import ContentStatus, MaterialType, StudyMaterial


@dataclass(frozen=True)
class MaterialSummary:
    id: int
    title: str
    subject: str
    topic: str | None
    material_type: MaterialType
    channel_id: int | None


def _summary(m: StudyMaterial) -> MaterialSummary:
    return MaterialSummary(m.id, m.title, m.subject, m.topic, m.material_type, m.channel_id)


def create_material(
    db: Session,
    *,
    uploader_id: int,
    title: str,
    material_type: MaterialType,
    subject: str,
    topic: str | None = None,
    semester: str | None = None,
    channel_id: int | None = None,
    url: str | None = None,
    file_path: str | None = None,
    description: str | None = None,
    is_official: bool = False,
) -> MaterialSummary:
    material = StudyMaterial(
        uploader_id=uploader_id,
        title=title,
        material_type=material_type,
        subject=subject,
        topic=topic,
        semester=semester,
        channel_id=channel_id,
        url=url,
        file_path=file_path,
        description=description,
        is_official=is_official,
    )
    db.add(material)
    db.flush()
    return _summary(material)


def get_material(db: Session, material_id: int) -> MaterialSummary | None:
    m = db.get(StudyMaterial, material_id)
    return _summary(m) if m and m.status is ContentStatus.ACTIVE else None


def set_removed(db: Session, material_id: int, removed: bool) -> None:
    m = db.get(StudyMaterial, material_id)
    if m is None:
        raise LookupError(f"material {material_id} not found")
    m.status = ContentStatus.REMOVED if removed else ContentStatus.ACTIVE


@dataclass(frozen=True)
class MaterialContribution:
    """One material the user contributed, for their profile's history (S01.2)."""

    id: int
    title: str
    created_at: datetime


def count_uploaded_by(db: Session, user_id: int) -> int:
    """How many materials the user contributed that are still active."""
    return (
        db.scalar(
            select(func.count(StudyMaterial.id)).where(
                StudyMaterial.uploader_id == user_id, StudyMaterial.status == ContentStatus.ACTIVE
            )
        )
        or 0
    )


def recent_uploaded_by(db: Session, user_id: int, limit: int = 10) -> list[MaterialContribution]:
    rows = db.scalars(
        select(StudyMaterial)
        .where(StudyMaterial.uploader_id == user_id, StudyMaterial.status == ContentStatus.ACTIVE)
        .order_by(StudyMaterial.created_at.desc(), StudyMaterial.id.desc())
        .limit(limit)
    )
    return [MaterialContribution(r.id, r.title, r.created_at) for r in rows]
