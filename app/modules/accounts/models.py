"""Accounts data (FR-01): users, roles, profile fields and profile pictures."""

import enum
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, LargeBinary, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.core.schema import CreatedAtMixin, enum_column


class Role(enum.StrEnum):
    STUDENT = "student"
    AUTHORIZED_USER = "authorized_user"
    ADMIN = "admin"


class User(CreatedAtMixin, Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    # The Test Plan logs users in by username (IT_01, IT_02). Nullable in the database
    # so accounts created before S01.1 stay valid; registration always sets it, and
    # those older accounts can still sign in with their email.
    username: Mapped[str | None] = mapped_column(String(50), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(100))
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[Role] = mapped_column(enum_column(Role, "user_role"), default=Role.STUDENT)
    bio: Mapped[str | None] = mapped_column(Text)
    institution: Mapped[str | None] = mapped_column(String(150))
    reputation: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    is_suspended: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")


class ProfilePicture(Base):
    """A user's profile picture (S01.2), at most one per user.

    Kept in the database rather than on local disk so it survives redeploys and every
    API instance serves the same image without a shared volume. Uploads are capped at
    2 MB (``app.modules.accounts.service.MAX_PICTURE_BYTES``), so this stays small.
    """

    __tablename__ = "profile_pictures"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    # Detected from the file's bytes, never taken from the client's Content-Type.
    content_type: Mapped[str] = mapped_column(String(32))
    data: Mapped[bytes] = mapped_column(LargeBinary)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
