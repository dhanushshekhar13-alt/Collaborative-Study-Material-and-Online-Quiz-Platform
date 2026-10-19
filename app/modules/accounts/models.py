"""Accounts data (FR-01): users, roles and profile fields."""

import enum

from sqlalchemy import Boolean, Integer, String, Text
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
