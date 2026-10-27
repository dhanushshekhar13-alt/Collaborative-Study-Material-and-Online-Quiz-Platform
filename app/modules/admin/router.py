"""HTTP surface of the admin module (S14.2), mounted at ``/api/v1/admin``.

Every route is guarded on the server by a capability only admins hold, so a student
who opens an admin URL directly gets 403 ``Unauthorized`` and no admin data (IT_28),
whatever the UI shows. Channel approval itself stays at
``POST /api/v1/channels/{id}/approve|reject``; this module lists the requests.
"""

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.modules.accounts import service as accounts
from app.modules.accounts.service import (
    AccountDetails,
    Capability,
    Role,
    UserSummary,
    require_capability,
)
from app.modules.admin import service
from app.modules.channels import service as channels
from app.modules.channels.service import ApprovalStatus, ChannelType
from app.modules.materials import service as materials
from app.modules.moderation import service as moderation
from app.modules.moderation.service import ModerationActionType, ReportReason, ReportTarget
from app.modules.questions import service as questions

router = APIRouter(prefix="/admin", tags=["admin"])

CONFIRM_SUSPEND = "Confirm suspending this account"
REASON_REQUIRED = "Give a reason for removing this content"

_ERRORS = {
    service.NotFound: status.HTTP_404_NOT_FOUND,
    service.NotAllowed: status.HTTP_400_BAD_REQUEST,
    service.Conflict: status.HTTP_409_CONFLICT,
}


def _fail(error: service.AdminActionError) -> HTTPException:
    return HTTPException(status_code=_ERRORS[type(error)], detail=str(error))


def _names(db: Session, ids: set[int]) -> dict[int, UserSummary]:
    return accounts.get_users(db, ids)


# --------------------------------------------------------------------------- #
# Dashboard (IT_28)
# --------------------------------------------------------------------------- #


class DashboardResponse(BaseModel):
    total_users: int
    suspended_users: int
    pending_channel_requests: int
    open_reports: int
    flagged_items: int


@router.get("/dashboard", response_model=DashboardResponse)
def admin_dashboard(
    _: UserSummary = Depends(require_capability(Capability.ADMIN_DASHBOARD)),
    db: Session = Depends(get_db),
) -> DashboardResponse:
    """The admin dashboard: counts of the work waiting for an admin.

    Admins only; anyone else gets 403 ``Unauthorized`` and nothing of the dashboard.
    """
    return DashboardResponse(**service.dashboard(db).__dict__)


# --------------------------------------------------------------------------- #
# User management (IT_27)
# --------------------------------------------------------------------------- #


class AdminUserResponse(BaseModel):
    id: int
    username: str | None
    email: str
    display_name: str
    role: Role
    status: Literal["Active", "Suspended"]
    created_at: datetime | None


def _user(details: AccountDetails) -> AdminUserResponse:
    return AdminUserResponse(
        id=details.id,
        username=details.username,
        email=details.email,
        display_name=details.display_name,
        role=details.role,
        status="Suspended" if details.is_suspended else "Active",
        created_at=details.created_at,
    )


@router.get("/users", response_model=list[AdminUserResponse])
def list_users(
    q: str | None = Query(default=None, max_length=100, description="Name, username or email"),
    status_filter: Literal["active", "suspended"] | None = Query(default=None, alias="status"),
    _: UserSummary = Depends(require_capability(Capability.USER_MANAGE)),
    db: Session = Depends(get_db),
) -> list[AdminUserResponse]:
    """Admin > User Management: every account with its status, oldest first."""
    needle = (q or "").strip().lower()
    users = []
    for details in accounts.list_accounts(db):
        if status_filter and (status_filter == "suspended") != details.is_suspended:
            continue
        haystack = f"{details.display_name} {details.username or ''} {details.email}".lower()
        if needle and needle not in haystack:
            continue
        users.append(_user(details))
    return users


class SuspendRequest(BaseModel):
    confirm: bool = False
    reason: str | None = Field(default=None, max_length=500)


class ReinstateRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=500)


def _reason(text: str | None) -> str | None:
    return (text or "").strip() or None


@router.post("/users/{user_id}/suspend", response_model=AdminUserResponse)
def suspend_user(
    user_id: int,
    payload: SuspendRequest,
    admin: UserSummary = Depends(require_capability(Capability.USER_MANAGE)),
    db: Session = Depends(get_db),
) -> AdminUserResponse:
    """Suspend an account (IT_27): status becomes Suspended and the user cannot log in.

    Needs ``confirm: true`` (the UI's confirmation step). Written to the audit log.
    """
    if not payload.confirm:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=CONFIRM_SUSPEND)
    try:
        details = service.suspend_user(
            db, admin_id=admin.id, user_id=user_id, reason=_reason(payload.reason)
        )
    except service.AdminActionError as error:
        db.rollback()
        raise _fail(error) from None
    db.commit()
    return _user(details)


@router.post("/users/{user_id}/reinstate", response_model=AdminUserResponse)
def reinstate_user(
    user_id: int,
    payload: ReinstateRequest | None = None,
    admin: UserSummary = Depends(require_capability(Capability.USER_MANAGE)),
    db: Session = Depends(get_db),
) -> AdminUserResponse:
    """Lift a suspension; the user can log in again. Written to the audit log."""
    try:
        details = service.reinstate_user(
            db, admin_id=admin.id, user_id=user_id, reason=_reason(payload and payload.reason)
        )
    except service.AdminActionError as error:
        db.rollback()
        raise _fail(error) from None
    db.commit()
    return _user(details)


# --------------------------------------------------------------------------- #
# Channel requests (ST_27)
# --------------------------------------------------------------------------- #


class PersonResponse(BaseModel):
    id: int
    display_name: str | None
    username: str | None


class ChannelRequestResponse(BaseModel):
    id: int
    name: str
    channel_type: ChannelType
    approval_status: ApprovalStatus
    subject: str | None
    topic: str | None
    description: str | None
    requested_at: datetime | None
    requester: PersonResponse | None


def _person(user_id: int | None, names: dict[int, UserSummary]) -> PersonResponse | None:
    if user_id is None:
        return None
    user = names.get(user_id)
    return PersonResponse(
        id=user_id,
        display_name=user.display_name if user else None,
        username=user.username if user else None,
    )


@router.get("/channel-requests", response_model=list[ChannelRequestResponse])
def list_channel_requests(
    _: UserSummary = Depends(require_capability(Capability.CHANNEL_APPROVE)),
    db: Session = Depends(get_db),
) -> list[ChannelRequestResponse]:
    """Admin > Channel Requests: pending Authorized Channels with their requester.

    Decide with ``POST /api/v1/channels/{id}/approve`` or ``/reject``.
    """
    pending = channels.pending_requests(db)
    names = _names(db, {c.created_by for c in pending if c.created_by is not None})
    return [
        ChannelRequestResponse(
            id=c.id,
            name=c.name,
            channel_type=c.channel_type,
            approval_status=c.approval_status,
            subject=c.subject,
            topic=c.topic,
            description=c.description,
            requested_at=c.created_at,
            requester=_person(c.created_by, names),
        )
        for c in pending
    ]


# --------------------------------------------------------------------------- #
# Review queue and content removal (ST_28)
# --------------------------------------------------------------------------- #


class FlaggedItemResponse(BaseModel):
    target_type: ReportTarget
    target_id: int
    title: str | None
    poster: PersonResponse | None
    channel_id: int | None
    report_count: int
    reasons: list[ReportReason]
    first_reported_at: datetime
    last_reported_at: datetime
    removable: bool


def _describe(db: Session, kind: ReportTarget, target_id: int) -> tuple[str | None, int | None]:
    if kind is ReportTarget.MATERIAL:
        m = materials.get_material(db, target_id)
        return (m.title, m.uploader_id) if m else (None, None)
    if kind is ReportTarget.QUESTION:
        q = questions.get_question_owner(db, target_id)
        return (q.headline, q.author_id) if q else (None, None)
    return None, None


@router.get("/reports", response_model=list[FlaggedItemResponse])
def review_queue(
    target_type: ReportTarget | None = None,
    _: UserSummary = Depends(require_capability(Capability.REPORT_RESOLVE)),
    db: Session = Depends(get_db),
) -> list[FlaggedItemResponse]:
    """Flagged content: open reports grouped by item, most-reported first."""
    flagged = moderation.flagged_targets(db, target_type)
    described = {
        (f.target_type, f.target_id): _describe(db, f.target_type, f.target_id) for f in flagged
    }
    names = _names(db, {poster for _, poster in described.values() if poster is not None})
    items = []
    for f in flagged:
        title, poster = described[(f.target_type, f.target_id)]
        items.append(
            FlaggedItemResponse(
                target_type=f.target_type,
                target_id=f.target_id,
                title=title,
                poster=_person(poster, names),
                channel_id=f.channel_id,
                report_count=f.report_count,
                reasons=list(f.reasons),
                first_reported_at=f.first_reported_at,
                last_reported_at=f.last_reported_at,
                removable=f.target_type in service.REMOVABLE and title is not None,
            )
        )
    return items


class RemoveContentRequest(BaseModel):
    reason: str = Field(default="", max_length=500)


class RemovedContentResponse(BaseModel):
    target_type: ReportTarget
    target_id: int
    title: str
    poster_id: int | None
    resolved_reports: int
    poster_notified: bool
    audit_entry_id: int
    message: str


@router.post("/content/{target_type}/{target_id}/remove", response_model=RemovedContentResponse)
def remove_content(
    target_type: Literal["material", "question"],
    target_id: int,
    payload: RemoveContentRequest,
    admin: UserSummary = Depends(require_capability(Capability.REPORT_RESOLVE)),
    db: Session = Depends(get_db),
) -> RemovedContentResponse:
    """Remove Content — Rule Violation (ST_28).

    The material or question is removed platform-wide, its open reports are resolved,
    the poster is notified with the reason, and the action goes to the audit log.
    """
    reason = _reason(payload.reason)
    if reason is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=REASON_REQUIRED
        )
    try:
        removed = service.remove_content(
            db,
            admin_id=admin.id,
            target_type=ReportTarget(target_type),
            target_id=target_id,
            reason=reason,
        )
    except service.AdminActionError as error:
        db.rollback()
        raise _fail(error) from None
    db.commit()
    return RemovedContentResponse(
        target_type=removed.target_type,
        target_id=removed.target_id,
        title=removed.title,
        poster_id=removed.poster_id,
        resolved_reports=len(removed.resolved_report_ids),
        poster_notified=removed.notified,
        audit_entry_id=removed.audit_entry_id,
        message="Content removed",
    )


# --------------------------------------------------------------------------- #
# Audit log
# --------------------------------------------------------------------------- #


class AuditEntryResponse(BaseModel):
    id: int
    actor: PersonResponse
    action: ModerationActionType
    target_type: ReportTarget
    target_id: int
    report_id: int | None
    note: str | None
    created_at: datetime


class AuditLogResponse(BaseModel):
    items: list[AuditEntryResponse]
    has_more: bool
    limit: int


@router.get("/audit-log", response_model=AuditLogResponse)
def audit_log(
    limit: int = Query(default=50, ge=1, le=100),
    before: int | None = Query(default=None, ge=1, description="Older than this entry id"),
    target_type: ReportTarget | None = None,
    target_id: int | None = Query(default=None, ge=1),
    _: UserSummary = Depends(require_capability(Capability.ADMIN_DASHBOARD)),
    db: Session = Depends(get_db),
) -> AuditLogResponse:
    """Every admin action, newest first: actor, action, target and timestamp."""
    entries, has_more = moderation.audit_log(
        db, limit=limit, before=before, target_type=target_type, target_id=target_id
    )
    names = _names(db, {e.actor_id for e in entries})
    return AuditLogResponse(
        items=[
            AuditEntryResponse(
                id=e.id,
                actor=_person(e.actor_id, names),
                action=e.action,
                target_type=e.target_type,
                target_id=e.target_id,
                report_id=e.report_id,
                note=e.note,
                created_at=e.created_at,
            )
            for e in entries
        ],
        has_more=has_more,
        limit=limit,
    )
