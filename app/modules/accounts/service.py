"""Public interface of the accounts module.

Other modules call these functions; they never query ``users`` directly.
"""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import hash_password
from app.modules.accounts.models import Role, User


@dataclass(frozen=True)
class UserSummary:
    id: int
    display_name: str
    role: Role
    is_suspended: bool


def _summary(user: User) -> UserSummary:
    return UserSummary(user.id, user.display_name, user.role, user.is_suspended)


def create_user(
    db: Session, *, email: str, display_name: str, password: str, role: Role = Role.STUDENT
) -> UserSummary:
    """Create a user with a hashed password. Emails are stored lower-cased."""
    user = User(
        email=email.strip().lower(),
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
