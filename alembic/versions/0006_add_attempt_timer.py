"""Add the attempt timer to quiz attempts (S06.1).

``expires_at`` is fixed when an attempt starts (start time + the quiz duration) so a
later change to the quiz cannot lengthen or shorten a running attempt.
``auto_submitted`` records that the server closed the attempt at its time limit.

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-21
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("quiz_attempts") as batch:
        batch.add_column(sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(
            sa.Column("auto_submitted", sa.Boolean(), server_default="0", nullable=False)
        )


def downgrade() -> None:
    with op.batch_alter_table("quiz_attempts") as batch:
        batch.drop_column("auto_submitted")
        batch.drop_column("expires_at")
