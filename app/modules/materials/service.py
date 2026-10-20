"""Public interface of the materials module."""

import unicodedata
from collections.abc import Collection
from dataclasses import dataclass
from datetime import datetime
from pathlib import PurePosixPath, PureWindowsPath

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.modules.materials.models import ContentStatus, MaterialFile, MaterialType, StudyMaterial


@dataclass(frozen=True)
class MaterialFileInfo:
    """What is known about a material's file without loading its bytes."""

    filename: str
    content_type: str
    size_bytes: int

    @property
    def previewable(self) -> bool:
        return self.content_type in PREVIEWABLE_TYPES


@dataclass(frozen=True)
class MaterialSummary:
    id: int
    title: str
    subject: str
    topic: str | None
    material_type: MaterialType
    channel_id: int | None
    semester: str | None = None
    description: str | None = None
    uploader_id: int | None = None
    created_at: datetime | None = None
    is_official: bool = False
    url: str | None = None
    file: MaterialFileInfo | None = None


def _summary(m: StudyMaterial, file: MaterialFileInfo | None = None) -> MaterialSummary:
    return MaterialSummary(
        m.id,
        m.title,
        m.subject,
        m.topic,
        m.material_type,
        m.channel_id,
        semester=m.semester,
        description=m.description,
        uploader_id=m.uploader_id,
        created_at=m.created_at,
        is_official=bool(m.is_official),
        url=m.url,
        file=file,
    )


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


# --------------------------------------------------------------------------- #
# S04.1 - upload, search, preview and download
# --------------------------------------------------------------------------- #

# Extension -> (content type served back, signature the bytes must start with).
# ``None`` as the signature means plain UTF-8 text. Legacy .doc/.ppt share the OLE
# signature; .docx/.pptx are ZIP containers.
_OLE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
_ZIP = b"PK\x03\x04"
SUPPORTED_FILE_TYPES: dict[str, tuple[str, bytes | None]] = {
    ".pdf": ("application/pdf", b"%PDF-"),
    ".doc": ("application/msword", _OLE),
    ".docx": (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        _ZIP,
    ),
    ".ppt": ("application/vnd.ms-powerpoint", _OLE),
    ".pptx": (
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        _ZIP,
    ),
    ".txt": ("text/plain; charset=utf-8", None),
    ".md": ("text/markdown; charset=utf-8", None),
    ".png": ("image/png", b"\x89PNG\r\n\x1a\n"),
    ".jpg": ("image/jpeg", b"\xff\xd8\xff"),
    ".jpeg": ("image/jpeg", b"\xff\xd8\xff"),
}

# Types a browser can show inline, so the UI can offer "View" before "Download".
PREVIEWABLE_TYPES = frozenset(
    {
        "application/pdf",
        "image/png",
        "image/jpeg",
        "text/plain; charset=utf-8",
        "text/markdown; charset=utf-8",
    }
)

FILE_EMPTY_MESSAGE = "Choose a file to upload"
FILE_TYPE_MESSAGE = (
    "This file type is not supported. Upload a PDF, Word, PowerPoint, text, PNG or JPEG file"
)
FILE_CONTENT_MESSAGE = "The file's contents do not match its type"


def file_too_large_message(max_bytes: int) -> str:
    return f"File must be {max_bytes // (1024 * 1024)} MB or smaller"


class InvalidMaterialFileError(Exception):
    """An uploaded file was refused; ``str(exc)`` is the user-facing reason."""

    def __init__(self, message: str, *, too_large: bool = False) -> None:
        self.too_large = too_large
        super().__init__(message)


def clean_filename(filename: str | None) -> str:
    """The file's base name with any client path and control characters removed."""
    name = PureWindowsPath(PurePosixPath(filename or "").name).name
    name = "".join(ch for ch in name if unicodedata.category(ch)[0] != "C").strip()
    if len(name) > 255:
        stem, dot, ext = name.rpartition(".")
        name = (stem[: 254 - len(ext)] + dot + ext) if dot else name[:255]
    return name


def validate_file(filename: str | None, data: bytes, max_bytes: int) -> tuple[str, str]:
    """Check an upload's size and type; return its clean name and content type.

    The type is decided by the extension and confirmed against the file's first bytes,
    so a renamed executable or HTML page is refused rather than served back.
    """
    name = clean_filename(filename)
    if not data or not name:
        raise InvalidMaterialFileError(FILE_EMPTY_MESSAGE)
    if len(data) > max_bytes:
        raise InvalidMaterialFileError(file_too_large_message(max_bytes), too_large=True)
    extension = PurePosixPath(name).suffix.lower()
    if extension not in SUPPORTED_FILE_TYPES:
        raise InvalidMaterialFileError(FILE_TYPE_MESSAGE)
    content_type, signature = SUPPORTED_FILE_TYPES[extension]
    if signature is None:
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            raise InvalidMaterialFileError(FILE_CONTENT_MESSAGE) from None
        if "\x00" in text:
            raise InvalidMaterialFileError(FILE_CONTENT_MESSAGE)
    elif not data.startswith(signature):
        raise InvalidMaterialFileError(FILE_CONTENT_MESSAGE)
    return name, content_type


def attach_file(
    db: Session, material_id: int, *, filename: str, content_type: str, data: bytes
) -> MaterialFileInfo:
    """Store a validated file for a material, replacing any earlier one. Caller commits."""
    existing = db.get(MaterialFile, material_id)
    if existing is not None:
        db.delete(existing)
        db.flush()
    db.add(
        MaterialFile(
            material_id=material_id,
            filename=filename,
            content_type=content_type,
            size_bytes=len(data),
            data=data,
        )
    )
    db.flush()
    return MaterialFileInfo(filename, content_type, len(data))


def _file_info(db: Session, material_ids: Collection[int]) -> dict[int, MaterialFileInfo]:
    if not material_ids:
        return {}
    rows = db.execute(
        select(
            MaterialFile.material_id,
            MaterialFile.filename,
            MaterialFile.content_type,
            MaterialFile.size_bytes,
        ).where(MaterialFile.material_id.in_(material_ids))
    )
    return {r.material_id: MaterialFileInfo(r.filename, r.content_type, r.size_bytes) for r in rows}


def describe_material(db: Session, material_id: int) -> MaterialSummary | None:
    """An active material with its tags and file details, or ``None``."""
    m = db.get(StudyMaterial, material_id)
    if m is None or m.status is not ContentStatus.ACTIVE:
        return None
    return _summary(m, _file_info(db, [m.id]).get(m.id))


@dataclass(frozen=True)
class MaterialFileData:
    filename: str
    content_type: str
    data: bytes


def get_material_file(db: Session, material_id: int) -> MaterialFileData | None:
    """The bytes of an active material's file, or ``None`` if it has no file."""
    row = db.execute(
        select(MaterialFile.filename, MaterialFile.content_type, MaterialFile.data)
        .join(StudyMaterial, StudyMaterial.id == MaterialFile.material_id)
        .where(
            MaterialFile.material_id == material_id,
            StudyMaterial.status == ContentStatus.ACTIVE,
        )
    ).first()
    return MaterialFileData(row.filename, row.content_type, row.data) if row else None


@dataclass(frozen=True)
class MaterialPage:
    items: list[MaterialSummary]
    total: int


def search_materials(
    db: Session,
    *,
    channel_ids: Collection[int] | None = None,
    include_general: bool = True,
    query: str | None = None,
    subject: str | None = None,
    topic: str | None = None,
    semester: str | None = None,
    material_type: MaterialType | None = None,
    limit: int = 20,
    offset: int = 0,
) -> MaterialPage:
    """Active materials matching every filter given, newest first.

    Scope: ``include_general`` covers materials outside any channel; ``channel_ids``
    lists the channels whose materials may be returned (``None`` means every channel,
    for admins). Subject, topic and semester match exactly, ignoring case, so filtering
    by subject and topic returns only materials matching both (IT_07); ``query`` is a
    keyword over title, description, subject and topic.
    """
    scope = []
    if include_general:
        scope.append(StudyMaterial.channel_id.is_(None))
    if channel_ids is None:
        scope.append(StudyMaterial.channel_id.is_not(None))
    elif channel_ids:
        scope.append(StudyMaterial.channel_id.in_(list(channel_ids)))
    if not scope:
        return MaterialPage([], 0)

    conditions = [StudyMaterial.status == ContentStatus.ACTIVE, or_(*scope)]
    for column, value in (
        (StudyMaterial.subject, subject),
        (StudyMaterial.topic, topic),
        (StudyMaterial.semester, semester),
    ):
        if value and value.strip():
            conditions.append(func.lower(column) == value.strip().lower())
    if material_type is not None:
        conditions.append(StudyMaterial.material_type == material_type)
    if query and query.strip():
        needle = f"%{query.strip().lower()}%"
        conditions.append(
            or_(
                func.lower(StudyMaterial.title).like(needle),
                func.lower(StudyMaterial.description).like(needle),
                func.lower(StudyMaterial.subject).like(needle),
                func.lower(StudyMaterial.topic).like(needle),
            )
        )
    where = and_(*conditions)
    total = db.scalar(select(func.count(StudyMaterial.id)).where(where)) or 0
    rows = list(
        db.scalars(
            select(StudyMaterial)
            .where(where)
            .order_by(StudyMaterial.created_at.desc(), StudyMaterial.id.desc())
            .limit(limit)
            .offset(offset)
        )
    )
    files = _file_info(db, [m.id for m in rows])
    return MaterialPage([_summary(m, files.get(m.id)) for m in rows], total)
