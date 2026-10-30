"""HTTP surface of the moderation queue (S13.2), mounted at ``/api/v1/moderation``.

Reports filed on materials and questions (``POST .../reports``) land here. A moderator
sees them grouped per item, opens one, and either removes the content or clears the
report. Both decisions close every open report on that item and are written to the
moderation action log with the moderator and the time.
"""

from collections.abc import Sequence
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.modules.accounts import service as accounts
from app.modules.accounts.service import UserSummary, current_user
from app.modules.channels import service as channels
from app.modules.llm import service as llm
from app.modules.materials import service as materials
from app.modules.moderation import service as moderation
from app.modules.moderation.service import (
    ModerationActionType,
    ReportReason,
    ReportStatus,
    ReportTarget,
)
from app.modules.moderation_queue import service
from app.modules.questions import service as questions

router = APIRouter(prefix="/moderation", tags=["moderation"])

NOT_A_MODERATOR = "Only moderators can review reports"
CANNOT_MODERATE_CHANNEL = "Only a moderator of this channel can act on its reports"
REPORT_NOT_FOUND = "Report not found"
ALREADY_CLOSED = "This report has already been closed"
CONFIRM_REMOVAL = "Confirm removing this content"
CONTENT_NOT_FOUND = "Content not found"
CANNOT_REMOVE_KIND = "This kind of content cannot be removed from the moderation queue"

# Content the queue can take off the platform, and the API path a moderator opens it at.
# An AI response (NFR-06) is private to its requester, so it has no link: the queue
# shows an excerpt instead, and removing it withdraws the output.
REMOVABLE = {
    ReportTarget.MATERIAL: "materials",
    ReportTarget.QUESTION: "questions",
    ReportTarget.AI_RESPONSE: None,
}


class Person(BaseModel):
    id: int
    name: str | None


class ReportedContent(BaseModel):
    title: str | None = Field(description="Material title or question headline")
    available: bool = Field(description="False once the content has been removed")
    link: str | None
    ai_generated: bool = Field(
        default=False,
        description="True for AI output: an AI response, or a question the LLM generated",
    )
    excerpt: str | None = Field(
        default=None, description="What the AI said, for an AI response (it has no link)"
    )


class QueueReport(BaseModel):
    report_id: int
    reporter: Person
    reason: ReportReason
    details: str | None
    status: ReportStatus
    reported_at: datetime


class QueueItemResponse(BaseModel):
    target_type: ReportTarget
    target_id: int
    channel_id: int | None
    channel_name: str | None
    content: ReportedContent
    report_count: int
    reasons: list[ReportReason]
    reports: list[QueueReport]
    first_reported_at: datetime
    last_reported_at: datetime


class ActionResponse(BaseModel):
    id: int
    action: ModerationActionType
    actor: Person
    report_id: int | None
    note: str | None
    at: datetime


class ReportDetailResponse(QueueItemResponse):
    report_id: int
    status: ReportStatus
    resolved_by: Person | None
    resolved_at: datetime | None
    resolution_note: str | None
    history: list[ActionResponse]


class RemoveRequest(BaseModel):
    confirm: bool = False
    note: str | None = Field(default=None, max_length=1000)


class DismissRequest(BaseModel):
    note: str | None = Field(default=None, max_length=1000)


class MyReportResponse(BaseModel):
    report_id: int
    target_type: ReportTarget
    target_id: int
    reason: ReportReason
    status: ReportStatus
    reported_at: datetime


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


class _Lookup:
    """Names, channels and content titles for a set of queue items, fetched up front.

    The queue grows with the platform, so each kind of lookup is one query for the
    whole page rather than one per report (NFR-05). Anything not prefetched (an
    action's actor on the detail page) is looked up once and cached.
    """

    def __init__(self, db: Session, items: Sequence[moderation.QueueItem] = ()) -> None:
        self.db = db
        people = {r.reporter_id for item in items for r in item.reports}
        self.people = {uid: u.display_name for uid, u in accounts.get_users(db, people).items()}
        self.channels = {
            cid: c.name
            for cid, c in channels.describe_channels(
                db, {i.channel_id for i in items if i.channel_id is not None}
            ).items()
        }
        self.titles: dict[tuple[ReportTarget, int], str] = {}
        wanted = {
            kind: {i.target_id for i in items if i.target_type is kind}
            for kind in (ReportTarget.MATERIAL, ReportTarget.QUESTION)
        }
        for mid, m in materials.get_materials(db, wanted[ReportTarget.MATERIAL]).items():
            self.titles[(ReportTarget.MATERIAL, mid)] = m.title
        self.ai_questions: set[int] = set()
        for qid, q in questions.get_question_owners(db, wanted[ReportTarget.QUESTION]).items():
            self.titles[(ReportTarget.QUESTION, qid)] = q.headline
            if q.ai_generated:
                self.ai_questions.add(qid)
        # Reports on AI responses (NFR-06) are rare next to materials and questions, so
        # each is looked up on its own.
        self.ai_responses = {
            rid: llm.get_response(db, rid)
            for rid in {i.target_id for i in items if i.target_type is ReportTarget.AI_RESPONSE}
        }

    def person(self, user_id: int | None) -> Person | None:
        if user_id is None:
            return None
        if user_id not in self.people:
            user = accounts.get_user(self.db, user_id)
            self.people[user_id] = user.display_name if user else None
        return Person(id=user_id, name=self.people[user_id])

    def channel_name(self, channel_id: int | None) -> str | None:
        return self.channels.get(channel_id) if channel_id is not None else None

    def content(self, kind: ReportTarget, target_id: int) -> ReportedContent:
        if kind is ReportTarget.AI_RESPONSE:
            if target_id not in self.ai_responses:
                self.ai_responses[target_id] = llm.get_response(self.db, target_id)
            found = self.ai_responses[target_id]
            if found is None:
                return ReportedContent(title=None, available=False, link=None, ai_generated=True)
            about = found.topic or found.subject
            return ReportedContent(
                title=f"{found.label}: {about}" if about else found.label,
                available=not found.withdrawn,
                link=None,
                ai_generated=True,
                excerpt=found.excerpt or None,
            )
        if kind not in REMOVABLE:
            return ReportedContent(title=None, available=True, link=None)
        title = self.titles.get((kind, target_id))
        link = f"/api/v1/{REMOVABLE[kind]}/{target_id}" if title else None
        return ReportedContent(
            title=title,
            available=title is not None,
            link=link,
            ai_generated=kind is ReportTarget.QUESTION and target_id in self.ai_questions,
        )


def _item(item: moderation.QueueItem, lookup: _Lookup) -> dict:
    return {
        "target_type": item.target_type,
        "target_id": item.target_id,
        "channel_id": item.channel_id,
        "channel_name": lookup.channel_name(item.channel_id),
        "content": lookup.content(item.target_type, item.target_id),
        "report_count": item.report_count,
        "reasons": list(item.reasons),
        "reports": [
            QueueReport(
                report_id=r.id,
                reporter=lookup.person(r.reporter_id),
                reason=r.reason,
                details=r.details,
                status=r.status,
                reported_at=r.created_at,
            )
            for r in item.reports
        ],
        "first_reported_at": item.reports[0].created_at,
        "last_reported_at": item.reports[-1].created_at,
    }


def _moderated_report(db: Session, report_id: int, user: UserSummary) -> moderation.ReportSummary:
    """The report if the caller may act on it: 404 if missing, 403 if not theirs to moderate."""
    report = moderation.get_report(db, report_id)
    if report is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=REPORT_NOT_FOUND)
    if not service.can_moderate_channel(db, user, report.channel_id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=CANNOT_MODERATE_CHANNEL)
    return report


def _detail(db: Session, report_id: int) -> ReportDetailResponse:
    report = moderation.get_report(db, report_id)
    item = moderation.reports_on(db, report.target_type, report.target_id)
    lookup = _Lookup(db, [item])
    entry = next(r for r in item.reports if r.id == report_id)
    history = moderation.actions_on(db, report.target_type, report.target_id)
    return ReportDetailResponse(
        **_item(item, lookup),
        report_id=report_id,
        status=entry.status,
        resolved_by=lookup.person(entry.resolved_by),
        resolved_at=entry.resolved_at,
        resolution_note=entry.resolution_note,
        history=[
            ActionResponse(
                id=a.id,
                action=a.action,
                actor=lookup.person(a.actor_id),
                report_id=a.report_id,
                note=a.note,
                at=a.created_at,
            )
            for a in history
        ],
    )


def _open(report: moderation.ReportSummary) -> None:
    if report.status is not ReportStatus.OPEN:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=ALREADY_CLOSED)


def _note(text: str | None) -> str | None:
    return (text or "").strip() or None


# --------------------------------------------------------------------------- #
# routes
# --------------------------------------------------------------------------- #


@router.get("/queue", response_model=list[QueueItemResponse])
def report_queue(
    channel_id: int | None = Query(default=None, description="Only this channel's reports"),
    user: UserSummary = Depends(current_user),
    db: Session = Depends(get_db),
) -> list[QueueItemResponse]:
    """Open reports, one entry per reported item, oldest first (IT_25).

    Each entry names the content, its channel, every reporter with their reason, and
    how many reports it has, so duplicate reports show as one item. Admins see the
    whole platform; channel moderators see the channels they moderate. Anyone else
    gets 403.
    """
    scope = service.moderated_channel_ids(db, user)
    if scope is not None and not scope:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=NOT_A_MODERATOR)
    if channel_id is not None:
        if not service.can_moderate_channel(db, user, channel_id):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail=CANNOT_MODERATE_CHANNEL
            )
        scope = [channel_id]
    items = moderation.report_queue(db, channel_ids=scope)
    lookup = _Lookup(db, items)
    return [QueueItemResponse(**_item(item, lookup)) for item in items]


@router.get("/reports/mine", response_model=list[MyReportResponse])
def my_reports(
    user: UserSummary = Depends(current_user), db: Session = Depends(get_db)
) -> list[MyReportResponse]:
    """The caller's own reports, newest first, with their current status.

    This is the reporter's confirmation that a report was filed, and later that it
    was resolved or dismissed.
    """
    return [
        MyReportResponse(
            report_id=r.id,
            target_type=r.target_type,
            target_id=r.target_id,
            reason=r.reason,
            status=r.status,
            reported_at=r.created_at,
        )
        for r in moderation.reports_filed_by(db, user.id)
    ]


@router.get("/reports/{report_id}", response_model=ReportDetailResponse)
def open_report(
    report_id: int, user: UserSummary = Depends(current_user), db: Session = Depends(get_db)
) -> ReportDetailResponse:
    """One report with every other report on the same item and the item's
    moderation history (who did what, when)."""
    _moderated_report(db, report_id, user)
    return _detail(db, report_id)


@router.post("/reports/{report_id}/remove", response_model=ReportDetailResponse)
def remove_content(
    report_id: int,
    payload: RemoveRequest,
    user: UserSummary = Depends(current_user),
    db: Session = Depends(get_db),
) -> ReportDetailResponse:
    """Remove Content (IT_26): hide the reported item and resolve its reports.

    Needs ``confirm: true``. The material or question is taken off the platform (it
    disappears from search, channels and direct links); a reported AI response is
    withdrawn, so its topic quiz and review screen close (NFR-06). Every open report on it
    becomes Resolved, and the removal is logged with the moderator and time.
    """
    report = _moderated_report(db, report_id, user)
    _open(report)
    if report.target_type not in REMOVABLE:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=CANNOT_REMOVE_KIND
        )
    if not payload.confirm:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=CONFIRM_REMOVAL)
    note = _note(payload.note)
    try:
        if report.target_type is ReportTarget.MATERIAL:
            materials.set_removed(db, report.target_id, True)
        elif report.target_type is ReportTarget.AI_RESPONSE:
            llm.set_withdrawn(db, report.target_id, True)
        else:
            questions.set_removed(db, report.target_id, True)
    except LookupError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=CONTENT_NOT_FOUND
        ) from None
    moderation.close_reports_on(
        db,
        target_type=report.target_type,
        target_id=report.target_id,
        moderator_id=user.id,
        dismissed=False,
        note=note,
    )
    moderation.log_action(
        db,
        actor_id=user.id,
        action=ModerationActionType.REMOVE_CONTENT,
        target_type=report.target_type,
        target_id=report.target_id,
        report_id=report_id,
        note=note,
    )
    db.commit()
    return _detail(db, report_id)


@router.post("/reports/{report_id}/dismiss", response_model=ReportDetailResponse)
def dismiss_report(
    report_id: int,
    payload: DismissRequest,
    user: UserSummary = Depends(current_user),
    db: Session = Depends(get_db),
) -> ReportDetailResponse:
    """Clear a report the moderator finds unfounded: the content stays, every open
    report on it becomes Dismissed, and the decision is logged."""
    report = _moderated_report(db, report_id, user)
    _open(report)
    note = _note(payload.note)
    moderation.close_reports_on(
        db,
        target_type=report.target_type,
        target_id=report.target_id,
        moderator_id=user.id,
        dismissed=True,
        note=note,
    )
    moderation.log_action(
        db,
        actor_id=user.id,
        action=ModerationActionType.DISMISS_REPORT,
        target_type=report.target_type,
        target_id=report.target_id,
        report_id=report_id,
        note=note,
    )
    db.commit()
    return _detail(db, report_id)
