"""Add a SHA-256 checksum to material files (NFR-04).

Every stored upload gets the checksum of its bytes, so a download can tell an intact
file from a damaged one. Existing files are backfilled from their stored bytes before
the column becomes NOT NULL.

Revision ID: 0013
Revises: 0012
Create Date: 2026-10-29
"""

import hashlib
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0013"
down_revision: Union[str, None] = "0012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_files = sa.table(
    "material_files",
    sa.column("material_id", sa.Integer),
    sa.column("data", sa.LargeBinary),
    sa.column("sha256", sa.String(64)),
)


def upgrade() -> None:
    with op.batch_alter_table("material_files") as batch_op:
        batch_op.add_column(sa.Column("sha256", sa.String(length=64), nullable=True))
    bind = op.get_bind()
    for material_id, data in bind.execute(sa.select(_files.c.material_id, _files.c.data)):
        bind.execute(
            _files.update()
            .where(_files.c.material_id == material_id)
            .values(sha256=hashlib.sha256(data).hexdigest())
        )
    with op.batch_alter_table("material_files") as batch_op:
        batch_op.alter_column("sha256", existing_type=sa.String(length=64), nullable=False)


def downgrade() -> None:
    with op.batch_alter_table("material_files") as batch_op:
        batch_op.drop_column("sha256")
