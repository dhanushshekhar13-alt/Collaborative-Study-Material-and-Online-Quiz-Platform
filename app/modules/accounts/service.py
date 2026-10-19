"""Public interface of the accounts module.

Other modules call these functions; they never query ``users`` directly. Functions
return plain dataclasses, never ORM objects.

Route guards for other modules' endpoints are exported here too, so protecting a route
only needs this module's public interface::

    from fastapi import Depends

    from app.modules.accounts.service import Capability, UserSummary, require_capability

    @router.post("/materials")
    def upload(user: UserSummary = Depends(require_capability(Capability.MATERIAL_UPLOAD))):
        ...
"""

from dataclasses import dataclass
from datetime import datetime
from functools import cache

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.security import hash_password, verify_password
from app.modules.accounts.models import Role, User


@dataclass(frozen=True)
class UserSummary:
    id: int
    display_name: str
    role: Role
    is_suspended: bool
    username: str | None = None


@dataclass(frozen=True)
class AccountDetails:
    """What the account owner, or an admin, may see about an account."""

    id: int
    username: str | None
    email: str
    display_name: str
    role: Role
    created_at: datetime


class DuplicateAccountError(Exception):
    """A username or email is already registered."""

    def __init__(self, field: str) -> None:
        self.field = field
        super().__init__(f"This {field} is already registered")


def _summary(user: User) -> UserSummary:
    return UserSummary(user.id, user.display_name, user.role, user.is_suspended, user.username)


def _details(user: User) -> AccountDetails:
    return AccountDetails(
        id=user.id,
        username=user.username,
        email=user.email,
        display_name=user.display_name,
        role=user.role,
        created_at=user.created_at,
    )


def create_user(
    db: Session,
    *,
    email: str,
    display_name: str,
    password: str,
    role: Role = Role.STUDENT,
    username: str | None = None,
) -> UserSummary:
    """Create a user with a hashed password. Emails and usernames are stored lower-cased."""
    user = User(
        email=email.strip().lower(),
        username=username.strip().lower() if username else None,
        display_name=display_name,
        password_hash=hash_password(password),
        role=role,
    )
    db.add(user)
    db.flush()
    return _summary(user)


def get_user(db: Session, user_id: int) -> UserSummary | None:
    user = db.get(User, user_id)
    return _summary(user) if user else None


def get_user_by_email(db: Session, email: str) -> UserSummary | None:
    user = db.scalar(select(User).where(User.email == email.strip().lower()))
    return _summary(user) if user else None


def set_suspended(db: Session, user_id: int, suspended: bool) -> None:
    user = db.get(User, user_id)
    if user is None:
        raise LookupError(f"user {user_id} not found")
    user.is_suspended = suspended


def add_reputation(db: Session, user_id: int, points: int) -> None:
    """Adjust the cached reputation total (the reputation module owns the event log)."""
    user = db.get(User, user_id)
    if user is None:
        raise LookupError(f"user {user_id} not found")
    user.reputation += points


# --------------------------------------------------------------------------- #
# S01.1 - registration, sign-in and account views
# --------------------------------------------------------------------------- #


def register_account(
    db: Session, *, display_name: str, username: str, email: str, password: str
) -> AccountDetails:
    """Register a Student, rejecting a username or email that is already in use.

    Self-registration always produces a Student; elevated roles are granted by an
    admin (FR-14), never chosen at sign-up. The caller commits.
    """
    username, email = username.strip().lower(), email.strip().lower()
    if db.scalar(select(User.id).where(User.username == username)) is not None:
        raise DuplicateAccountError("username")
    if db.scalar(select(User.id).where(User.email == email)) is not None:
        raise DuplicateAccountError("email")

    summary = create_user(
        db, email=email, display_name=display_name, password=password, username=username
    )
    return _details(db.get(User, summary.id))


@cache
def _decoy_hash() -> str:
    return hash_password("decoy password for unknown accounts")


def authenticate(db: Session, identifier: str, password: str) -> UserSummary | None:
    """Return the user if ``identifier`` (username or email) and ``password`` match.

    An unknown account, a wrong password and a suspended account all return ``None``
    and take the same time, so sign-in cannot be used to find out which accounts exist.
    """
    needle = identifier.strip().lower()
    user = db.scalar(select(User).where(or_(User.username == needle, User.email == needle)))
    if user is None:
        verify_password(password, _decoy_hash())
        return None
    if not verify_password(password, user.password_hash) or user.is_suspended:
        return None
    return _summary(user)


def get_account(db: Session, user_id: int) -> AccountDetails | None:
    user = db.get(User, user_id)
    return _details(user) if user else None


def list_accounts(db: Session) -> list[AccountDetails]:
    """Every account, oldest first. Callers must hold ``Capability.USER_MANAGE``."""
    return [_details(user) for user in db.scalars(select(User).order_by(User.id))]


def count_accounts(db: Session) -> int:
    return db.scalar(select(func.count(User.id))) or 0


# Route guards and permissions, re-exported so other modules need only this interface.
from app.modules.accounts.auth import (  # noqa: E402
    current_user,
    require_capability,
    require_roles,
)
from app.modules.accounts.permissions import Capability, capabilities_for  # noqa: E402

__all__ = [
    "AccountDetails",
    "Capability",
    "DuplicateAccountError",
    "UserSummary",
    "add_reputation",
    "authenticate",
    "capabilities_for",
    "count_accounts",
    "create_user",
    "current_user",
    "get_account",
    "get_user",
    "get_user_by_email",
    "list_accounts",
    "register_account",
    "require_capability",
    "require_roles",
    "set_suspended",
]
