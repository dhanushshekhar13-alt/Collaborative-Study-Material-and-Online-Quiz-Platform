"""Public interface of the moderation module (reports and admin actions)."""

from collections.abc import Collection
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select
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
    """Record an open report on any content (type plus id); returns its id. Caller
    commits."""
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
    """The report with this id, or ``None``."""
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
    """Ids of open reports, oldest first, optionally in one channel."""
    stmt = select(Report.id).where(Report.status == ReportStatus.OPEN)
    if channel_id is not None:
        stmt = stmt.where(Report.channel_id == channel_id)
    return list(db.scalars(stmt.order_by(Report.created_at)))


def resolve_report(
    db: Session, report_id: int, *, moderator_id: int, dismissed: bool, note: str | None = None
) -> None:
    """Close one report as resolved or dismissed by ``moderator_id``."""
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
    """Write an entry to the moderation audit log; returns its id."""
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


# --------------------------------------------------------------------------- #
# S14.2 - admin review queue summary and audit log
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class FlaggedTarget:
    """One reported item in the admin queue: every open report on it, grouped."""

    target_type: ReportTarget
    target_id: int
    channel_id: int | None
    report_count: int
    reasons: tuple[ReportReason, ...]
    first_reported_at: datetime
    last_reported_at: datetime


def flagged_targets(db: Session, target_type: ReportTarget | None = None) -> list[FlaggedTarget]:
    """Open reports grouped by what they report, most-reported first (ST_28)."""
    stmt = select(Report).where(Report.status == ReportStatus.OPEN)
    if target_type is not None:
        stmt = stmt.where(Report.target_type == target_type)
    grouped: dict[tuple[ReportTarget, int], list[Report]] = {}
    for r in db.scalars(stmt.order_by(Report.created_at, Report.id)):
        grouped.setdefault((r.target_type, r.target_id), []).append(r)
    flagged = [
        FlaggedTarget(
            target_type=kind,
            target_id=target_id,
            channel_id=reports[0].channel_id,
            report_count=len(reports),
            reasons=tuple(dict.fromkeys(r.reason for r in reports)),
            first_reported_at=reports[0].created_at,
            last_reported_at=reports[-1].created_at,
        )
        for (kind, target_id), reports in grouped.items()
    ]
    return sorted(flagged, key=lambda f: (-f.report_count, f.first_reported_at))


def count_open_reports(db: Session) -> int:
    """How many reports are still open."""
    return db.scalar(select(func.count(Report.id)).where(Report.status == ReportStatus.OPEN)) or 0


@dataclass(frozen=True)
class AuditEntry:
    """One admin action as the audit log records it: actor, action, target and time."""

    id: int
    actor_id: int
    action: ModerationActionType
    target_type: ReportTarget
    target_id: int
    report_id: int | None
    note: str | None
    created_at: datetime


def audit_log(
    db: Session,
    *,
    limit: int = 50,
    before: int | None = None,
    target_type: ReportTarget | None = None,
    target_id: int | None = None,
) -> tuple[list[AuditEntry], bool]:
    """Admin actions newest first, paged with ``before=<oldest id shown>``.

    Returns the page and whether older entries remain.
    """
    stmt = select(ModerationAction)
    if before is not None:
        stmt = stmt.where(ModerationAction.id < before)
    if target_type is not None:
        stmt = stmt.where(ModerationAction.target_type == target_type)
    if target_id is not None:
        stmt = stmt.where(ModerationAction.target_id == target_id)
    rows = list(db.scalars(stmt.order_by(ModerationAction.id.desc()).limit(limit + 1)))
    entries = [
        AuditEntry(
            a.id,
            a.actor_id,
            a.action,
            a.target_type,
            a.target_id,
            a.report_id,
            a.note,
            a.created_at,
        )
        for a in rows[:limit]
    ]
    return entries, len(rows) > limit
