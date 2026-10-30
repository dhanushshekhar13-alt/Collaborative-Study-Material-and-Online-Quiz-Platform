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

from collections.abc import Collection
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import cache

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.security import hash_password, verify_password
from app.modules.accounts.models import ProfilePicture, Role, User


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
    is_suspended: bool = False


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
        is_suspended=bool(user.is_suspended),
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


def get_users(db: Session, user_ids: Collection[int]) -> dict[int, UserSummary]:
    """Several users at once, keyed by id; unknown ids are left out (S13.1 leaderboard)."""
    ids = set(user_ids)
    if not ids:
        return {}
    return {u.id: _summary(u) for u in db.scalars(select(User).where(User.id.in_(ids)))}


def get_user_by_email(db: Session, email: str) -> UserSummary | None:
    user = db.scalar(select(User).where(User.email == email.strip().lower()))
    return _summary(user) if user else None


def get_users_by_username(db: Session, usernames: Collection[str]) -> dict[str, UserSummary]:
    """Users by username (case-insensitive), keyed by the lower-cased name (S14.1 mentions)."""
    names = {n.strip().lower() for n in usernames if n.strip()}
    if not names:
        return {}
    rows = db.scalars(select(User).where(User.username.in_(names)))
    return {u.username: _summary(u) for u in rows}


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


def count_suspended_accounts(db: Session) -> int:
    """How many accounts are suspended, counted in the database (NFR-05)."""
    return db.scalar(select(func.count(User.id)).where(User.is_suspended.is_(True))) or 0


# --------------------------------------------------------------------------- #
# S01.2 - profile fields and profile picture
# --------------------------------------------------------------------------- #

MAX_PICTURE_BYTES = 2 * 1024 * 1024

# Wording shown to the user when an upload is refused.
PICTURE_EMPTY_MESSAGE = "Choose an image to upload"
PICTURE_TOO_LARGE_MESSAGE = "Profile picture must be 2 MB or smaller"
PICTURE_WRONG_TYPE_MESSAGE = "Profile picture must be a JPEG, PNG, GIF or WebP image"


@dataclass(frozen=True)
class ProfileDetails:
    """The editable, publicly visible part of an account. Email is not included."""

    id: int
    username: str | None
    display_name: str
    role: Role
    bio: str | None
    institution: str | None
    created_at: datetime
    picture_updated_at: datetime | None


@dataclass(frozen=True)
class ProfilePictureData:
    content_type: str
    data: bytes
    updated_at: datetime


class InvalidPictureError(Exception):
    """An uploaded profile picture was refused; ``str(exc)`` is the user-facing reason."""

    def __init__(self, message: str, *, too_large: bool = False) -> None:
        self.too_large = too_large
        super().__init__(message)


def _profile(db: Session, user: User) -> ProfileDetails:
    picture_updated_at = db.scalar(
        select(ProfilePicture.updated_at).where(ProfilePicture.user_id == user.id)
    )
    return ProfileDetails(
        id=user.id,
        username=user.username,
        display_name=user.display_name,
        role=user.role,
        bio=user.bio,
        institution=user.institution,
        created_at=user.created_at,
        picture_updated_at=picture_updated_at,
    )


def get_profile(db: Session, user_id: int) -> ProfileDetails | None:
    user = db.get(User, user_id)
    return _profile(db, user) if user else None


_UNCHANGED = object()


def update_profile(
    db: Session,
    user_id: int,
    *,
    display_name: str | object = _UNCHANGED,
    bio: str | None | object = _UNCHANGED,
    institution: str | None | object = _UNCHANGED,
) -> ProfileDetails:
    """Change only the fields passed. Blank bio or institution clears it. Caller commits."""
    user = db.get(User, user_id)
    if user is None:
        raise LookupError(f"user {user_id} not found")
    if display_name is not _UNCHANGED:
        user.display_name = display_name.strip()
    if bio is not _UNCHANGED:
        user.bio = (bio or "").strip() or None
    if institution is not _UNCHANGED:
        user.institution = (institution or "").strip() or None
    db.flush()
    return _profile(db, user)


def detect_image_type(data: bytes) -> str | None:
    """The image type from the file's leading bytes, or ``None`` if it isn't one we accept.

    The file's name and the client's Content-Type are ignored: a renamed script stays a
    script. SVG is deliberately not accepted because it can carry JavaScript.
    """
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def set_profile_picture(db: Session, user_id: int, data: bytes) -> ProfileDetails:
    """Validate and store ``data`` as the user's picture, replacing any earlier one."""
    if not data:
        raise InvalidPictureError(PICTURE_EMPTY_MESSAGE)
    if len(data) > MAX_PICTURE_BYTES:
        raise InvalidPictureError(PICTURE_TOO_LARGE_MESSAGE, too_large=True)
    content_type = detect_image_type(data)
    if content_type is None:
        raise InvalidPictureError(PICTURE_WRONG_TYPE_MESSAGE)

    user = db.get(User, user_id)
    if user is None:
        raise LookupError(f"user {user_id} not found")
    picture = db.get(ProfilePicture, user_id)
    if picture is None:
        picture = ProfilePicture(user_id=user_id)
        db.add(picture)
    picture.content_type = content_type
    picture.data = data
    picture.updated_at = datetime.now(UTC)
    db.flush()
    return _profile(db, user)


def remove_profile_picture(db: Session, user_id: int) -> ProfileDetails:
    user = db.get(User, user_id)
    if user is None:
        raise LookupError(f"user {user_id} not found")
    picture = db.get(ProfilePicture, user_id)
    if picture is not None:
        db.delete(picture)
        db.flush()
    return _profile(db, user)


def get_profile_picture(db: Session, user_id: int) -> ProfilePictureData | None:
    picture = db.get(ProfilePicture, user_id)
    if picture is None:
        return None
    return ProfilePictureData(picture.content_type, picture.data, picture.updated_at)


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
    "InvalidPictureError",
    "MAX_PICTURE_BYTES",
    "PICTURE_EMPTY_MESSAGE",
    "PICTURE_TOO_LARGE_MESSAGE",
    "PICTURE_WRONG_TYPE_MESSAGE",
    "ProfileDetails",
    "ProfilePictureData",
    "UserSummary",
    "add_reputation",
    "authenticate",
    "capabilities_for",
    "count_accounts",
    "count_suspended_accounts",
    "create_user",
    "current_user",
    "detect_image_type",
    "get_account",
    "get_profile",
    "get_profile_picture",
    "get_user",
    "get_user_by_email",
    "get_users_by_username",
    "get_users",
    "list_accounts",
    "register_account",
    "remove_profile_picture",
    "require_capability",
    "require_roles",
    "set_profile_picture",
    "set_suspended",
    "update_profile",
]
