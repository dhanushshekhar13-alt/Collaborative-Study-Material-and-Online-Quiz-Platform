"""Public interface of the moderation module (reports and admin actions)."""

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
