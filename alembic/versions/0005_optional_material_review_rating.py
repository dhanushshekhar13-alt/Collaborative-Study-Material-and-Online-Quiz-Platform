"""Let a material review row carry a helpful mark without a rating (S04.2).

``material_reviews`` holds one row per (material, user) with that user's rating,
review text and helpful mark. Marking a material helpful must not force a star rating,
so ``rating`` becomes nullable; the 1-5 check still applies whenever it is set, and
AVG/COUNT(rating) skip the NULLs.

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-21
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("material_reviews") as batch:
        batch.alter_column("rating", existing_type=sa.Integer(), nullable=True)


def downgrade() -> None:
    # Helpful-only rows have no rating to keep; the helpful mark is lost with them.
    op.execute("DELETE FROM material_reviews WHERE rating IS NULL")
    with op.batch_alter_table("material_reviews") as batch:
        batch.alter_column("rating", existing_type=sa.Integer(), nullable=False)
