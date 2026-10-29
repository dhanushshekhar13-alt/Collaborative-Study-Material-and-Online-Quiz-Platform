"""Public interface of the admin module (S14.2): enforcement that is accountable.

Every action here is written to the admin audit log (``moderation.log_action``) with
actor, target and time, in the same transaction as the change it records. Callers
commit. Functions return dataclasses and raise ``AdminActionError`` subclasses whose
message is the wording shown to the admin.
"""

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.modules.accounts import service as accounts
from app.modules.accounts.service import AccountDetails, Role
from app.modules.channels import service as channels
from app.modules.materials import service as materials
from app.modules.moderation import service as moderation
from app.modules.moderation.service import ModerationActionType, ReportTarget
from app.modules.notifications import service as notifications
from app.modules.notifications.service import NotificationKind
from app.modules.questions import service as questions

# Wording shown to the admin.
USER_NOT_FOUND = "User not found"
SELF_SUSPEND = "You cannot suspend your own account"
ADMIN_SUSPEND = "An admin account cannot be suspended"
ALREADY_SUSPENDED = "This account is already suspended"
NOT_SUSPENDED = "This account is not suspended"
CONTENT_NOT_FOUND = "Content not found"

# The kinds of content an admin can remove from the platform.
REMOVABLE = (ReportTarget.MATERIAL, ReportTarget.QUESTION)


class AdminActionError(Exception):
    """An admin action that cannot be carried out; ``str(error)`` is the wording."""


class NotFound(AdminActionError):
    pass


class NotAllowed(AdminActionError):
    pass


class Conflict(AdminActionError):
    pass


# --------------------------------------------------------------------------- #
# Users: suspend and reinstate (IT_27)
# --------------------------------------------------------------------------- #


def _account(db: Session, user_id: int) -> AccountDetails:
    account = accounts.get_account(db, user_id)
    if account is None:
        raise NotFound(USER_NOT_FOUND)
    return account


def suspend_user(
    db: Session, *, admin_id: int, user_id: int, reason: str | None = None
) -> AccountDetails:
    """Suspend an account: it can no longer log in, and its open sessions stop working.

    An admin cannot suspend themselves (no lock-out) or another admin.
    """
    account = _account(db, user_id)
    if user_id == admin_id:
        raise NotAllowed(SELF_SUSPEND)
    if account.role is Role.ADMIN:
        raise NotAllowed(ADMIN_SUSPEND)
    if account.is_suspended:
        raise Conflict(ALREADY_SUSPENDED)
    accounts.set_suspended(db, user_id, True)
    moderation.log_action(
        db,
        actor_id=admin_id,
        action=ModerationActionType.SUSPEND_USER,
        target_type=ReportTarget.USER,
        target_id=user_id,
        note=reason,
    )
    db.flush()
    return _account(db, user_id)


def reinstate_user(
    db: Session, *, admin_id: int, user_id: int, reason: str | None = None
) -> AccountDetails:
    """Lift a suspension; the user can log in again."""
    account = _account(db, user_id)
    if not account.is_suspended:
        raise Conflict(NOT_SUSPENDED)
    accounts.set_suspended(db, user_id, False)
    moderation.log_action(
        db,
        actor_id=admin_id,
        action=ModerationActionType.UNSUSPEND_USER,
        target_type=ReportTarget.USER,
        target_id=user_id,
        note=reason,
    )
    db.flush()
    return _account(db, user_id)


# --------------------------------------------------------------------------- #
# Content removal (ST_28)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class RemovedContent:
    target_type: ReportTarget
    target_id: int
    title: str
    poster_id: int | None
    channel_id: int | None
    resolved_report_ids: tuple[int, ...]
    notified: bool
    audit_entry_id: int


@dataclass(frozen=True)
class _Content:
    title: str
    poster_id: int | None
    channel_id: int | None
    noun: str


def _content(db: Session, target_type: ReportTarget, target_id: int) -> _Content:
    if target_type is ReportTarget.MATERIAL:
        material = materials.get_material(db, target_id)
        if material is not None:
            return _Content(material.title, material.uploader_id, material.channel_id, "material")
    elif target_type is ReportTarget.QUESTION:
        question = questions.get_question_owner(db, target_id)
        if question is not None:
            return _Content(question.headline, question.author_id, question.channel_id, "question")
    raise NotFound(CONTENT_NOT_FOUND)


def remove_content(
    db: Session, *, admin_id: int, target_type: ReportTarget, target_id: int, reason: str
) -> RemovedContent:
    """Remove a material or question platform-wide for a rule violation.

    The item disappears from search, channels and direct links; the open reports on it
    are resolved; the poster is notified with the reason; and the removal is written to
    the audit log.
    """
    content = _content(db, target_type, target_id)
    if target_type is ReportTarget.MATERIAL:
        materials.set_removed(db, target_id, True)
    else:
        questions.set_removed(db, target_id, True)
    resolved = moderation.close_reports_on(
        db,
        target_type=target_type,
        target_id=target_id,
        moderator_id=admin_id,
        dismissed=False,
        note=reason,
    )
    audit_id = moderation.log_action(
        db,
        actor_id=admin_id,
        action=ModerationActionType.REMOVE_CONTENT,
        target_type=target_type,
        target_id=target_id,
        report_id=resolved[0] if resolved else None,
        note=reason,
    )
    notified = False
    if content.poster_id is not None and content.poster_id != admin_id:
        notified = (
            notifications.notify(
                db,
                user_id=content.poster_id,
                kind=NotificationKind.MODERATION,
                title=f'Your {content.noun} "{content.title}" was removed',
                body=(
                    f"An admin removed your {content.noun} for violating the platform "
                    f"rules. Reason: {reason}"
                ),
            )
            is not None
        )
    db.flush()
    return RemovedContent(
        target_type=target_type,
        target_id=target_id,
        title=content.title,
        poster_id=content.poster_id,
        channel_id=content.channel_id,
        resolved_report_ids=tuple(resolved),
        notified=notified,
        audit_entry_id=audit_id,
    )


# --------------------------------------------------------------------------- #
# Dashboard
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class DashboardSummary:
    """What the admin dashboard opens with: the work waiting for an admin."""

    total_users: int
    suspended_users: int
    pending_channel_requests: int
    open_reports: int
    flagged_items: int


def dashboard(db: Session) -> DashboardSummary:
    # Counted in the database rather than by loading every account (NFR-05).
    return DashboardSummary(
        total_users=accounts.count_accounts(db),
        suspended_users=accounts.count_suspended_accounts(db),
        pending_channel_requests=len(channels.pending_requests(db)),
        open_reports=moderation.count_open_reports(db),
        flagged_items=len(moderation.flagged_targets(db)),
    )
