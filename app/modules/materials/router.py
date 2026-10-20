"""HTTP surface of the materials module, mounted at ``/api/v1/materials`` (S04.1)."""

from datetime import datetime
from pathlib import PurePosixPath
from urllib.parse import quote

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Response,
    UploadFile,
    status,
)
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.modules.accounts import service as accounts
from app.modules.accounts.service import (
    Capability,
    UserSummary,
    capabilities_for,
    current_user,
    require_capability,
)
from app.modules.channels import service as channels
from app.modules.channels.service import ChannelType
from app.modules.materials import service
from app.modules.materials.models import MaterialType

router = APIRouter(prefix="/materials", tags=["materials"])

# Wording fixed by the Test Plan (UT_08); the other tags follow the same pattern.
SUBJECT_REQUIRED = "Subject is required"
TOPIC_REQUIRED = "Topic is required"
SEMESTER_REQUIRED = "Semester is required"
TYPE_REQUIRED = "Material type is required"
URL_REQUIRED = "Enter the link's URL, starting with http:// or https://"
NOT_FOUND = "Material not found"
CHANNEL_NOT_FOUND = "Channel not found"
MEMBERS_ONLY = "Join this channel to view its content"
MEMBERS_ONLY_UPLOAD = "Join this channel to add materials"
AUTHORIZED_ONLY_UPLOAD = "Only authorized users can add content to Authorized Channels"
NO_FILE = "This material has no file to download"
NOT_PREVIEWABLE = "Preview isn't available for this file type. Download it instead"
EMPTY_STATE = (
    "No materials match your search. Try a broader subject or topic, "
    "or upload the first material for it."
)

TYPE_LABELS = {
    MaterialType.NOTES: "Notes",
    MaterialType.PDF: "PDF",
    MaterialType.PRESENTATION: "Presentation",
    MaterialType.LINK: "Link",
    MaterialType.OTHER: "Other",
}


class MaterialFileResponse(BaseModel):
    filename: str
    content_type: str
    size_bytes: int
    previewable: bool


class MaterialResponse(BaseModel):
    id: int
    title: str
    description: str | None
    subject: str
    topic: str | None
    semester: str | None
    material_type: MaterialType
    type_label: str
    # Every tag in display order, so listings show them all the same way (UT_07).
    tags: list[str]
    channel_id: int | None
    is_official: bool
    uploader_id: int | None
    uploader_name: str | None
    created_at: datetime | None
    url: str | None
    file: MaterialFileResponse | None
    preview_url: str | None
    download_url: str | None


class MaterialListResponse(BaseModel):
    items: list[MaterialResponse]
    total: int
    limit: int
    offset: int
    # A helpful message when nothing matches, so the UI can show an empty state.
    empty_state: str | None


def _is_admin(user: UserSummary) -> bool:
    return Capability.CHANNEL_APPROVE in capabilities_for(user.role)


def _response(
    db: Session, m: service.MaterialSummary, names: dict[int, str] | None = None
) -> MaterialResponse:
    if names is not None and m.uploader_id in names:
        uploader_name = names[m.uploader_id]
    else:
        uploader = accounts.get_user(db, m.uploader_id) if m.uploader_id else None
        uploader_name = uploader.display_name if uploader else None
        if names is not None and m.uploader_id:
            names[m.uploader_id] = uploader_name
    type_label = TYPE_LABELS[m.material_type]
    base = f"/api/v1/materials/{m.id}"
    file = m.file
    return MaterialResponse(
        id=m.id,
        title=m.title,
        description=m.description,
        subject=m.subject,
        topic=m.topic,
        semester=m.semester,
        material_type=m.material_type,
        type_label=type_label,
        tags=[t for t in (m.subject, m.topic, m.semester and f"Semester {m.semester}") if t]
        + [type_label],
        channel_id=m.channel_id,
        is_official=m.is_official,
        uploader_id=m.uploader_id,
        uploader_name=uploader_name,
        created_at=m.created_at,
        url=m.url,
        file=MaterialFileResponse(
            filename=file.filename,
            content_type=file.content_type,
            size_bytes=file.size_bytes,
            previewable=file.previewable,
        )
        if file
        else None,
        preview_url=f"{base}/preview" if file and file.previewable else None,
        download_url=f"{base}/download" if file else None,
    )


def _channel_for(db: Session, channel_id: int, user: UserSummary) -> channels.ChannelSummary:
    """The channel if the caller may see it, else 404 (its existence is not revealed)."""
    summary = channels.describe_channel(db, channel_id)
    if summary is None or not channels.is_visible_to(summary, user.id, _is_admin(user)):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=CHANNEL_NOT_FOUND)
    return summary


def _require_access(db: Session, channel_id: int | None, user: UserSummary) -> None:
    """Channel-scoped materials are for the channel's members (and admins) only."""
    if channel_id is None:
        return
    _channel_for(db, channel_id, user)
    if not channels.is_member(db, channel_id, user.id) and not _is_admin(user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=MEMBERS_ONLY)


def _readable(db: Session, material_id: int, user: UserSummary) -> service.MaterialSummary:
    material = service.describe_material(db, material_id)
    if material is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=NOT_FOUND)
    _require_access(db, material.channel_id, user)
    return material


def _blank(value: str | None) -> str | None:
    return (value or "").strip() or None


def _invalid(message: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=message)


@router.post("", status_code=status.HTTP_201_CREATED, response_model=MaterialResponse)
async def upload_material(
    file: UploadFile | None = File(
        default=None, description="PDF, Word, PowerPoint, text, PNG or JPEG"
    ),
    title: str | None = Form(default=None, max_length=200),
    subject: str | None = Form(default=None, max_length=100),
    topic: str | None = Form(default=None, max_length=100),
    semester: str | None = Form(default=None, max_length=20),
    material_type: str | None = Form(default=None),
    description: str | None = Form(default=None, max_length=5000),
    channel_id: int | None = Form(default=None),
    url: str | None = Form(default=None, max_length=1000),
    user: UserSummary = Depends(require_capability(Capability.MATERIAL_UPLOAD)),
    db: Session = Depends(get_db),
) -> MaterialResponse:
    """Upload a tagged study material (UT_07).

    Subject, topic, semester and material type are all required; a blank one is refused
    with its own message, e.g. ``Subject is required`` (UT_08). Files are checked for a
    supported type and the size limit before anything is saved. A ``link`` material
    carries a URL instead of a file. With ``channel_id`` the material is channel-scoped
    and only that channel's members can see it.
    """
    subject, topic, semester = _blank(subject), _blank(topic), _blank(semester)
    if subject is None:
        raise _invalid(SUBJECT_REQUIRED)
    if topic is None:
        raise _invalid(TOPIC_REQUIRED)
    if semester is None:
        raise _invalid(SEMESTER_REQUIRED)
    try:
        kind = MaterialType((material_type or "").strip().lower())
    except ValueError:
        raise _invalid(TYPE_REQUIRED) from None

    if channel_id is not None:
        channel = _channel_for(db, channel_id, user)
        if not channels.is_member(db, channel_id, user.id) and not _is_admin(user):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=MEMBERS_ONLY_UPLOAD)
        if channel.channel_type is ChannelType.AUTHORIZED and (
            Capability.OFFICIAL_CONTENT_MANAGE not in capabilities_for(user.role)
        ):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail=AUTHORIZED_ONLY_UPLOAD
            )

    link = _blank(url)
    filename = content_type = data = None
    if kind is MaterialType.LINK:
        if link is None or not link.lower().startswith(("http://", "https://")):
            raise _invalid(URL_REQUIRED)
    else:
        link = None
        max_bytes = settings.material_max_upload_mb * 1024 * 1024
        # Read one byte past the limit so an oversized file is caught without loading it all.
        data = await file.read(max_bytes + 1) if file is not None else b""
        try:
            filename, content_type = service.validate_file(
                file.filename if file is not None else None, data, max_bytes
            )
        except service.InvalidMaterialFileError as exc:
            code = (
                status.HTTP_413_CONTENT_TOO_LARGE
                if exc.too_large
                else status.HTTP_422_UNPROCESSABLE_CONTENT
            )
            raise HTTPException(status_code=code, detail=str(exc)) from exc

    name = _blank(title) or (PurePosixPath(filename).stem if filename else link)
    created = service.create_material(
        db,
        uploader_id=user.id,
        title=name[:200],
        material_type=kind,
        subject=subject,
        topic=topic,
        semester=semester,
        channel_id=channel_id,
        url=link,
        description=_blank(description),
    )
    if data:
        service.attach_file(db, created.id, filename=filename, content_type=content_type, data=data)
    db.commit()
    return _response(db, service.describe_material(db, created.id))


@router.get("", response_model=MaterialListResponse)
def search_materials(
    q: str | None = Query(default=None, max_length=100, description="Keyword"),
    subject: str | None = Query(default=None, max_length=100),
    topic: str | None = Query(default=None, max_length=100),
    semester: str | None = Query(default=None, max_length=20),
    material_type: MaterialType | None = Query(default=None, alias="type"),
    channel_id: int | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    user: UserSummary = Depends(current_user),
    db: Session = Depends(get_db),
) -> MaterialListResponse:
    """Search and filter materials (IT_07). Subject and topic together return only
    materials matching both.

    Without ``channel_id`` the search covers general materials and those in the
    caller's channels; with it, that channel only, for its members. An empty result
    carries ``empty_state`` for the UI.
    """
    if channel_id is not None:
        _require_access(db, channel_id, user)
        scope, include_general = [channel_id], False
    elif _is_admin(user):
        scope, include_general = None, True
    else:
        scope = [c.id for c, _ in channels.channels_for_member(db, user.id)]
        include_general = True
    page = service.search_materials(
        db,
        channel_ids=scope,
        include_general=include_general,
        query=q,
        subject=subject,
        topic=topic,
        semester=semester,
        material_type=material_type,
        limit=limit,
        offset=offset,
    )
    names: dict[int, str] = {}
    return MaterialListResponse(
        items=[_response(db, m, names) for m in page.items],
        total=page.total,
        limit=limit,
        offset=offset,
        empty_state=EMPTY_STATE if page.total == 0 else None,
    )


@router.get("/{material_id}", response_model=MaterialResponse)
def read_material(
    material_id: int, user: UserSummary = Depends(current_user), db: Session = Depends(get_db)
) -> MaterialResponse:
    """One material with all its tags and file details."""
    return _response(db, _readable(db, material_id, user))


def _file_response(db: Session, material_id: int, user: UserSummary, *, inline: bool):
    material = _readable(db, material_id, user)
    if material.file is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=NO_FILE)
    if inline and not material.file.previewable:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail=NOT_PREVIEWABLE
        )
    stored = service.get_material_file(db, material_id)
    if stored is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=NO_FILE)
    ascii_name = stored.filename.encode("ascii", "ignore").decode().replace('"', "") or "material"
    disposition = "inline" if inline else "attachment"
    headers = {
        "Content-Disposition": (
            f"{disposition}; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(stored.filename)}"
        ),
        "X-Content-Type-Options": "nosniff",
        "Cache-Control": "private, no-store",
    }
    if stored.content_type != "application/pdf":
        # Images and text never need scripts; the browser's PDF viewer is left alone.
        headers["Content-Security-Policy"] = "default-src 'none'; img-src 'self'"
    return Response(content=stored.data, media_type=stored.content_type, headers=headers)


@router.get(
    "/{material_id}/preview",
    response_class=Response,
    responses={200: {"description": "The file, shown inline"}},
)
def preview_material(
    material_id: int, user: UserSummary = Depends(current_user), db: Session = Depends(get_db)
) -> Response:
    """The file for viewing in the browser (IT_08): PDFs, images and text only."""
    return _file_response(db, material_id, user, inline=True)


@router.get(
    "/{material_id}/download",
    response_class=Response,
    responses={200: {"description": "The file, as an attachment"}},
)
def download_material(
    material_id: int, user: UserSummary = Depends(current_user), db: Session = Depends(get_db)
) -> Response:
    """The file as a download, under its original name (IT_08)."""
    return _file_response(db, material_id, user, inline=False)
