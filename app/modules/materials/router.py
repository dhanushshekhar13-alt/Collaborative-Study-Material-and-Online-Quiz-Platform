"""HTTP surface of the materials module, mounted at ``/api/v1/materials``.

S04.1 upload, search and download; S04.2 rate, review and report; S03.1 official
content in Authorized Channels (tagging, edit and remove).
"""

import logging
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
from pydantic import BaseModel, Field
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
from app.modules.moderation import service as moderation
from app.modules.moderation.service import ReportReason, ReportTarget

logger = logging.getLogger(__name__)
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
FILE_DAMAGED = (
    "This file is damaged and cannot be downloaded. The problem has been logged for an "
    "administrator to restore it."
)
NOT_PREVIEWABLE = "Preview isn't available for this file type. Download it instead"
RATING_REQUIRED = "Select a rating from 1 to 5 stars"
REASON_REQUIRED = "Select a reason for reporting this material"
OWN_MATERIAL = "You can't rate or mark your own material as helpful"
REPORT_LOGGED = "Report submitted for moderation"
# S03.1. "Insufficient permissions" is the Test Plan wording (UT_06).
INSUFFICIENT_PERMISSIONS = "Insufficient permissions"
OFFICIAL_ONLY_IN_AUTHORIZED = "Official content can only be published in an Authorized Channel"
NOTHING_TO_CHANGE = "Change at least one detail"
OFFICIAL_LABEL = "Official/Verified"
PEER_LABEL = "Peer"
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
    sha256: str | None = Field(
        default=None,
        description="SHA-256 of the stored bytes, so the uploader can confirm the file "
        "arrived intact (NFR-04); downloads are checked against it",
    )


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
    # S03.1: shown the same way on every endpoint so official content always stands
    # out from peer content. The UI maps ``content_style`` to its styling.
    content_label: str
    content_style: str
    # Whether the caller may edit and remove this material (hidden for regular
    # members on official content; the API refuses them the same way).
    can_manage: bool
    uploader_id: int | None
    uploader_name: str | None
    created_at: datetime | None
    url: str | None
    file: MaterialFileResponse | None
    preview_url: str | None
    download_url: str | None
    # Ratings and the caller's own feedback (S04.2). The UI shows one decimal.
    rating_average: float | None
    rating_count: int
    review_count: int
    helpful_count: int
    my_rating: int | None
    my_review: str | None
    marked_helpful: bool


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
    db: Session,
    m: service.MaterialSummary,
    names: dict[int, str] | None = None,
    feedback: service.MaterialFeedback | None = None,
    can_manage: bool = False,
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
    feedback = feedback or service.MaterialFeedback()
    return MaterialResponse(
        rating_average=feedback.rating_average,
        rating_count=feedback.rating_count,
        review_count=feedback.review_count,
        helpful_count=feedback.helpful_count,
        my_rating=feedback.my_rating,
        my_review=feedback.my_review,
        marked_helpful=feedback.marked_helpful,
        id=m.id,
        title=m.title,
        description=m.description,
        subject=m.subject,
        topic=m.topic,
        semester=m.semester,
        material_type=m.material_type,
        type_label=type_label,
        tags=([OFFICIAL_LABEL] if m.is_official else [])
        + [t for t in (m.subject, m.topic, m.semester and f"Semester {m.semester}") if t]
        + [type_label],
        channel_id=m.channel_id,
        is_official=m.is_official,
        content_label=OFFICIAL_LABEL if m.is_official else PEER_LABEL,
        content_style="content-official" if m.is_official else "content-peer",
        can_manage=can_manage,
        uploader_id=m.uploader_id,
        uploader_name=uploader_name,
        created_at=m.created_at,
        url=m.url,
        file=MaterialFileResponse(
            filename=file.filename,
            content_type=file.content_type,
            size_bytes=file.size_bytes,
            previewable=file.previewable,
            sha256=file.sha256,
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


def _manages_official(
    db: Session,
    channel_id: int | None,
    user: UserSummary,
    cache: dict[int, bool] | None = None,
) -> bool:
    """Whether the caller manages official content in this channel (S03.1)."""
    if channel_id is None:
        return False
    if cache is not None and channel_id in cache:
        return cache[channel_id]
    summary = channels.describe_channel(db, channel_id)
    result = summary is not None and channels.manages_official_content(
        summary,
        is_member=channels.is_member(db, channel_id, user.id),
        has_capability=Capability.OFFICIAL_CONTENT_MANAGE in capabilities_for(user.role),
        is_admin=_is_admin(user),
    )
    if cache is not None:
        cache[channel_id] = result
    return result


def _can_manage(
    db: Session,
    m: service.MaterialSummary,
    user: UserSummary,
    cache: dict[int, bool] | None = None,
) -> bool:
    """Official content: authorized users of its channel (and admins). Peer content:
    its uploader (and admins); S03.2 covers peer contribution in Normal Channels."""
    if m.is_official:
        return _manages_official(db, m.channel_id, user, cache)
    return m.uploader_id == user.id or _is_admin(user)


def _detail(db: Session, material_id: int, user: UserSummary) -> MaterialResponse:
    material = service.describe_material(db, material_id)
    feedback = service.feedback_for(db, [material_id], user.id)[material_id]
    return _response(db, material, feedback=feedback, can_manage=_can_manage(db, material, user))


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
    is_official: bool | None = Form(
        default=None, description="Mark as official; Authorized Channels only (S03.1)"
    ),
    user: UserSummary = Depends(require_capability(Capability.MATERIAL_UPLOAD)),
    db: Session = Depends(get_db),
) -> MaterialResponse:
    """Upload a tagged study material (UT_07).

    Subject, topic, semester and material type are all required; a blank one is refused
    with its own message, e.g. ``Subject is required`` (UT_08). Files are checked for a
    supported type and the size limit before anything is saved. A ``link`` material
    carries a URL instead of a file. With ``channel_id`` the material is channel-scoped
    and only that channel's members can see it.

    Everything published in an Authorized Channel is official: only authorized users
    may add it, and it is tagged ``Official/Verified`` (UT_05). ``is_official`` outside
    an Authorized Channel is refused.
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

    official = False
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
        official = channel.channel_type is ChannelType.AUTHORIZED
    if is_official and not official:
        raise _invalid(OFFICIAL_ONLY_IN_AUTHORIZED)

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
        is_official=official,
    )
    if data:
        service.attach_file(db, created.id, filename=filename, content_type=content_type, data=data)
    db.commit()
    return _detail(db, created.id, user)


@router.get("", response_model=MaterialListResponse)
def search_materials(
    q: str | None = Query(default=None, max_length=100, description="Keyword"),
    subject: str | None = Query(default=None, max_length=100),
    topic: str | None = Query(default=None, max_length=100),
    semester: str | None = Query(default=None, max_length=20),
    material_type: MaterialType | None = Query(default=None, alias="type"),
    channel_id: int | None = Query(default=None),
    official: bool | None = Query(
        default=None, description="true: official only; false: peer only (S03.1)"
    ),
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
        is_official=official,
        limit=limit,
        offset=offset,
    )
    uploaders = accounts.get_users(db, {m.uploader_id for m in page.items if m.uploader_id})
    names: dict[int, str] = {uid: u.display_name for uid, u in uploaders.items()}
    managed: dict[int, bool] = {}
    feedback = service.feedback_for(db, [m.id for m in page.items], user.id)
    return MaterialListResponse(
        items=[
            _response(db, m, names, feedback[m.id], _can_manage(db, m, user, managed))
            for m in page.items
        ],
        total=page.total,
        limit=limit,
        offset=offset,
        empty_state=EMPTY_STATE if page.total == 0 else None,
    )


@router.get("/{material_id}", response_model=MaterialResponse)
def read_material(
    material_id: int, user: UserSummary = Depends(current_user), db: Session = Depends(get_db)
) -> MaterialResponse:
    """One material with all its tags, file details and ratings."""
    _readable(db, material_id, user)
    return _detail(db, material_id, user)


def _file_response(db: Session, material_id: int, user: UserSummary, *, inline: bool):
    material = _readable(db, material_id, user)
    if material.file is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=NO_FILE)
    if inline and not material.file.previewable:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail=NOT_PREVIEWABLE
        )
    try:
        stored = service.get_material_file(db, material_id)
    except service.DamagedFileError:
        logger.error("material %s: stored file failed its integrity check", material_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=FILE_DAMAGED
        ) from None
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
        "X-Content-SHA256": stored.sha256,
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


# --------------------------------------------------------------------------- #
# S04.2 - rate, review, mark helpful and report
# --------------------------------------------------------------------------- #


class ReviewRequest(BaseModel):
    # Optional in the schema so a missing rating gets the plain message below
    # rather than a generic validation error.
    rating: int | None = Field(default=None, description="1 to 5 stars")
    review: str | None = Field(default=None, max_length=2000, description="Optional text")


class ReviewResponse(BaseModel):
    id: int
    author_id: int
    author_name: str | None
    rating: int | None
    review: str
    marked_helpful: bool
    created_at: datetime | None


class ReviewListResponse(BaseModel):
    items: list[ReviewResponse]
    total: int
    limit: int
    offset: int


class HelpfulResponse(BaseModel):
    material_id: int
    helpful_count: int
    marked_helpful: bool


class MaterialReportRequest(BaseModel):
    # Optional in the schema so a missing reason gets REASON_REQUIRED (422).
    reason: ReportReason | None = Field(
        default=None, description='"duplicate" is the "Duplicate content" report'
    )
    details: str | None = Field(default=None, max_length=2000)


class MaterialReportResponse(BaseModel):
    report_id: int
    material_id: int
    reason: ReportReason
    reporter_id: int
    reported_at: datetime
    status: str
    message: str


def _not_own(material: service.MaterialSummary, user: UserSummary) -> None:
    if material.uploader_id == user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=OWN_MATERIAL)


@router.put("/{material_id}/review", response_model=MaterialResponse)
def review_material(
    material_id: int,
    payload: ReviewRequest,
    user: UserSummary = Depends(require_capability(Capability.MATERIAL_RATE)),
    db: Session = Depends(get_db),
) -> MaterialResponse:
    """Rate a material 1-5 stars, optionally with review text (ST_07).

    The response carries the updated ``rating_average``. Rating again replaces the
    caller's earlier rating and review, so each user counts once. Uploaders cannot
    rate their own material.
    """
    material = _readable(db, material_id, user)
    _not_own(material, user)
    if payload.rating is None or not 1 <= payload.rating <= 5:
        raise _invalid(RATING_REQUIRED)
    service.rate_material(db, material_id, user.id, payload.rating, payload.review)
    db.commit()
    return _detail(db, material_id, user)


@router.get("/{material_id}/reviews", response_model=ReviewListResponse)
def list_material_reviews(
    material_id: int,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    user: UserSummary = Depends(current_user),
    db: Session = Depends(get_db),
) -> ReviewListResponse:
    """Written reviews, newest first, each with its author's name."""
    _readable(db, material_id, user)
    entries, total = service.list_reviews(db, material_id, limit=limit, offset=offset)
    names: dict[int, str | None] = {}
    items = []
    for e in entries:
        if e.user_id not in names:
            author = accounts.get_user(db, e.user_id)
            names[e.user_id] = author.display_name if author else None
        items.append(
            ReviewResponse(
                id=e.id,
                author_id=e.user_id,
                author_name=names[e.user_id],
                rating=e.rating,
                review=e.review,
                marked_helpful=e.marked_helpful,
                created_at=e.created_at,
            )
        )
    return ReviewListResponse(items=items, total=total, limit=limit, offset=offset)


def _helpful(db: Session, material_id: int, user: UserSummary, helpful: bool) -> HelpfulResponse:
    material = _readable(db, material_id, user)
    _not_own(material, user)
    feedback = service.set_helpful(db, material_id, user.id, helpful)
    db.commit()
    return HelpfulResponse(
        material_id=material_id,
        helpful_count=feedback.helpful_count,
        marked_helpful=feedback.marked_helpful,
    )


@router.put("/{material_id}/helpful", response_model=HelpfulResponse)
def mark_helpful(
    material_id: int,
    user: UserSummary = Depends(require_capability(Capability.MATERIAL_RATE)),
    db: Session = Depends(get_db),
) -> HelpfulResponse:
    """Mark a material helpful (ST_07). Repeating it never adds more than one."""
    return _helpful(db, material_id, user, True)


@router.delete("/{material_id}/helpful", response_model=HelpfulResponse)
def unmark_helpful(
    material_id: int,
    user: UserSummary = Depends(require_capability(Capability.MATERIAL_RATE)),
    db: Session = Depends(get_db),
) -> HelpfulResponse:
    """Take back the caller's helpful mark."""
    return _helpful(db, material_id, user, False)


@router.post(
    "/{material_id}/reports",
    status_code=status.HTTP_201_CREATED,
    response_model=MaterialReportResponse,
)
def report_material(
    material_id: int,
    payload: MaterialReportRequest,
    user: UserSummary = Depends(require_capability(Capability.REPORT_SUBMIT)),
    db: Session = Depends(get_db),
) -> MaterialReportResponse:
    """Report a material (for example "Duplicate content") to moderation (ST_08).

    A reason is required. The report is recorded with its timestamp and reporter and
    filed as open in the moderation queue, scoped to the material's channel. Reporting
    the same material again while the first report is open returns that report.
    """
    material = _readable(db, material_id, user)
    if payload.reason is None:
        raise _invalid(REASON_REQUIRED)
    report_id = moderation.find_open_report(
        db, reporter_id=user.id, target_type=ReportTarget.MATERIAL, target_id=material_id
    )
    if report_id is None:
        report_id = moderation.file_report(
            db,
            reporter_id=user.id,
            target_type=ReportTarget.MATERIAL,
            target_id=material_id,
            reason=payload.reason,
            details=_blank(payload.details),
            channel_id=material.channel_id,
        )
        db.commit()
    report = moderation.get_report(db, report_id)
    return MaterialReportResponse(
        report_id=report.id,
        material_id=material_id,
        reason=report.reason,
        reporter_id=report.reporter_id,
        reported_at=report.created_at,
        status=report.status.value,
        message=REPORT_LOGGED,
    )


# --------------------------------------------------------------------------- #
# S03.1 - edit and remove (official content in Authorized Channels)
# --------------------------------------------------------------------------- #


class MaterialUpdate(BaseModel):
    """Only the fields sent are changed. Subject, topic and semester cannot be blanked."""

    title: str | None = Field(default=None, max_length=200)
    description: str | None = Field(default=None, max_length=5000)
    subject: str | None = Field(default=None, max_length=100)
    topic: str | None = Field(default=None, max_length=100)
    semester: str | None = Field(default=None, max_length=20)
    url: str | None = Field(default=None, max_length=1000)


def _managed(db: Session, material_id: int, user: UserSummary) -> service.MaterialSummary:
    """The material if the caller may manage it, else 403 ``Insufficient permissions``."""
    material = _readable(db, material_id, user)
    if not _can_manage(db, material, user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=INSUFFICIENT_PERMISSIONS)
    return material


@router.patch("/{material_id}", response_model=MaterialResponse)
def edit_material(
    material_id: int,
    payload: MaterialUpdate,
    user: UserSummary = Depends(current_user),
    db: Session = Depends(get_db),
) -> MaterialResponse:
    """Edit a material's details (S03.1).

    Official content can be edited by the authorized users of its Authorized Channel
    (and admins); a regular member gets **403** ``Insufficient permissions`` (UT_06).
    Peer content can be edited by its uploader. The official tag cannot be changed.
    """
    material = _managed(db, material_id, user)
    sent = payload.model_fields_set
    if not sent:
        raise _invalid(NOTHING_TO_CHANGE)
    changes: dict[str, str | None] = {}
    for field, message in (
        ("subject", SUBJECT_REQUIRED),
        ("topic", TOPIC_REQUIRED),
        ("semester", SEMESTER_REQUIRED),
    ):
        if field in sent:
            value = _blank(getattr(payload, field))
            if value is None:
                raise _invalid(message)
            changes[field] = value
    if "title" in sent:
        title = _blank(payload.title)
        if title is None:
            raise _invalid("Enter a title")
        changes["title"] = title
    if "description" in sent:
        changes["description"] = _blank(payload.description)
    if "url" in sent:
        link = _blank(payload.url)
        if material.material_type is not MaterialType.LINK:
            raise _invalid("Only link materials have a URL")
        if link is None or not link.lower().startswith(("http://", "https://")):
            raise _invalid(URL_REQUIRED)
        changes["url"] = link
    service.update_material(db, material_id, **changes)
    db.commit()
    return _detail(db, material_id, user)


@router.delete("/{material_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_material(
    material_id: int, user: UserSummary = Depends(current_user), db: Session = Depends(get_db)
) -> None:
    """Remove a material (S03.1): it is no longer listed, shown or downloadable.

    Same rule as editing: official content by its channel's authorized users (and
    admins), peer content by its uploader; anyone else gets ``Insufficient permissions``.
    """
    _managed(db, material_id, user)
    service.set_removed(db, material_id, True)
    db.commit()
