"""Public interface of the moderation module (reports and admin actions)."""

from collections.abc import Collection
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.moderation.models import (
    ModerationAction,
    ModerationActionType,
    Report,
    ReportReason,
    ReportStatus,
    ReportTarget,
)


def file_report(
    db: Session,
    *,
    reporter_id: int,
    target_type: ReportTarget,
    target_id: int,
    reason: ReportReason,
    details: str | None = None,
    channel_id: int | None = None,
) -> int:
    report = Report(
        reporter_id=reporter_id,
        target_type=target_type,
        target_id=target_id,
        reason=reason,
        details=details,
        channel_id=channel_id,
    )
    db.add(report)
    db.flush()
    return report.id


@dataclass(frozen=True)
class ReportSummary:
    """A report as filed: what was reported, by whom, why and when."""

    id: int
    reporter_id: int
    target_type: ReportTarget
    target_id: int
    reason: ReportReason
    details: str | None
    channel_id: int | None
    status: ReportStatus
    created_at: datetime


def get_report(db: Session, report_id: int) -> ReportSummary | None:
    r = db.get(Report, report_id)
    if r is None:
        return None
    return ReportSummary(
        r.id,
        r.reporter_id,
        r.target_type,
        r.target_id,
        r.reason,
        r.details,
        r.channel_id,
        r.status,
        r.created_at,
    )


def find_open_report(
    db: Session, *, reporter_id: int, target_type: ReportTarget, target_id: int
) -> int | None:
    """The reporter's still-open report on this target, if any (avoids duplicates)."""
    return db.scalar(
        select(Report.id).where(
            Report.reporter_id == reporter_id,
            Report.target_type == target_type,
            Report.target_id == target_id,
            Report.status == ReportStatus.OPEN,
        )
    )


def open_reports(db: Session, channel_id: int | None = None) -> list[int]:
    stmt = select(Report.id).where(Report.status == ReportStatus.OPEN)
    if channel_id is not None:
        stmt = stmt.where(Report.channel_id == channel_id)
    return list(db.scalars(stmt.order_by(Report.created_at)))


def resolve_report(
    db: Session, report_id: int, *, moderator_id: int, dismissed: bool, note: str | None = None
) -> None:
    report = db.get(Report, report_id)
    if report is None:
        raise LookupError(f"report {report_id} not found")
    report.status = ReportStatus.DISMISSED if dismissed else ReportStatus.RESOLVED
    report.resolved_by, report.resolution_note = moderator_id, note
    report.resolved_at = datetime.now(UTC)


def log_action(
    db: Session,
    *,
    actor_id: int,
    action: ModerationActionType,
    target_type: ReportTarget,
    target_id: int,
    report_id: int | None = None,
    note: str | None = None,
) -> int:
    entry = ModerationAction(
        actor_id=actor_id,
        action=action,
        target_type=target_type,
        target_id=target_id,
        report_id=report_id,
        note=note,
    )
    db.add(entry)
    db.flush()
    return entry.id


# --------------------------------------------------------------------------- #
# S13.2 - report queue and moderator actions
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ReportEntry:
    """One report in the queue: who filed it, why, and how it was closed."""

    id: int
    reporter_id: int
    reason: ReportReason
    details: str | None
    status: ReportStatus
    created_at: datetime
    resolved_by: int | None = None
    resolved_at: datetime | None = None
    resolution_note: str | None = None


def _entry(r: Report) -> ReportEntry:
    return ReportEntry(
        r.id,
        r.reporter_id,
        r.reason,
        r.details,
        r.status,
        r.created_at,
        r.resolved_by,
        r.resolved_at,
        r.resolution_note,
    )


@dataclass(frozen=True)
class QueueItem:
    """Every open report on one piece of content, grouped so duplicates show once."""

    target_type: ReportTarget
    target_id: int
    channel_id: int | None
    reports: tuple[ReportEntry, ...]

    @property
    def report_count(self) -> int:
        return len(self.reports)

    @property
    def reasons(self) -> tuple[ReportReason, ...]:
        return tuple(dict.fromkeys(r.reason for r in self.reports))


def report_queue(db: Session, *, channel_ids: Collection[int] | None = None) -> list[QueueItem]:
    """Open reports grouped by what they report, oldest first.

    ``channel_ids=None`` is the whole platform (admins); otherwise only reports scoped
    to those channels (what a channel moderator may act on).
    """
    stmt = select(Report).where(Report.status == ReportStatus.OPEN)
    if channel_ids is not None:
        if not channel_ids:
            return []
        stmt = stmt.where(Report.channel_id.in_(set(channel_ids)))
    grouped: dict[tuple[ReportTarget, int], list[Report]] = {}
    for r in db.scalars(stmt.order_by(Report.created_at, Report.id)):
        grouped.setdefault((r.target_type, r.target_id), []).append(r)
    return [
        QueueItem(kind, target_id, rows[0].channel_id, tuple(_entry(r) for r in rows))
        for (kind, target_id), rows in grouped.items()
    ]


def reports_on(db: Session, target_type: ReportTarget, target_id: int) -> QueueItem:
    """Every report on one item, open or closed, oldest first."""
    rows = list(
        db.scalars(
            select(Report)
            .where(Report.target_type == target_type, Report.target_id == target_id)
            .order_by(Report.created_at, Report.id)
        )
    )
    channel_id = rows[0].channel_id if rows else None
    return QueueItem(target_type, target_id, channel_id, tuple(_entry(r) for r in rows))


def close_reports_on(
    db: Session,
    *,
    target_type: ReportTarget,
    target_id: int,
    moderator_id: int,
    dismissed: bool,
    note: str | None = None,
) -> list[int]:
    """Resolve (or dismiss) every open report on an item at once; returns their ids.

    Duplicate reports describe one problem, so one moderator decision closes them all.
    """
    ids = list(
        db.scalars(
            select(Report.id).where(
                Report.status == ReportStatus.OPEN,
                Report.target_type == target_type,
                Report.target_id == target_id,
            )
        )
    )
    for report_id in ids:
        resolve_report(db, report_id, moderator_id=moderator_id, dismissed=dismissed, note=note)
    db.flush()
    return ids


def reports_filed_by(db: Session, reporter_id: int, limit: int = 50) -> list[ReportSummary]:
    """A reporter's own reports, newest first, so they can follow what happened."""
    rows = db.scalars(
        select(Report)
        .where(Report.reporter_id == reporter_id)
        .order_by(Report.created_at.desc(), Report.id.desc())
        .limit(limit)
    )
    return [get_report(db, r.id) for r in rows]


@dataclass(frozen=True)
class ActionEntry:
    """One recorded moderation action: who did what to which item, and when."""

    id: int
    actor_id: int
    action: ModerationActionType
    target_type: ReportTarget
    target_id: int
    report_id: int | None
    note: str | None
    created_at: datetime


def actions_on(db: Session, target_type: ReportTarget, target_id: int) -> list[ActionEntry]:
    """The moderation history of one item, oldest first."""
    rows = db.scalars(
        select(ModerationAction)
        .where(
            ModerationAction.target_type == target_type,
            ModerationAction.target_id == target_id,
        )
        .order_by(ModerationAction.id)
    )
    return [
        ActionEntry(
            a.id,
            a.actor_id,
            a.action,
            a.target_type,
            a.target_id,
            a.report_id,
            a.note,
            a.created_at,
        )
        for a in rows
    ]
