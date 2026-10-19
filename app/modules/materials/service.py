"""Public interface of the materials module."""

from dataclasses import dataclass

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
