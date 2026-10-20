"""Add uploaded files for study materials (S04.1).

Each study material can carry one uploaded file. It is stored in the database, like
profile pictures, so it survives redeploys without a file volume. Size and type are
validated by the materials service before anything is written.

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-20
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "material_files",
        sa.Column("material_id", sa.Integer(), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("content_type", sa.String(length=100), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("data", sa.LargeBinary(), nullable=False),
        sa.CheckConstraint("size_bytes > 0", name="ck_material_file_size"),
        sa.ForeignKeyConstraint(["material_id"], ["study_materials.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("material_id"),
    )


def downgrade() -> None:
    op.drop_table("material_files")
