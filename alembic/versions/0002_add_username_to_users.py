"""Add a unique username to users (S01.1).

The Test Plan signs users in by username (IT_01, IT_02) and S01.1 requires duplicate
usernames to be rejected. The column is nullable so rows created before this
migration (seeded users, for example) remain valid; registration always sets it.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-19
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.add_column(sa.Column("username", sa.String(length=50), nullable=True))
        batch_op.create_index(batch_op.f("ix_users_username"), ["username"], unique=True)


def downgrade() -> None:
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_users_username"))
        batch_op.drop_column("username")
