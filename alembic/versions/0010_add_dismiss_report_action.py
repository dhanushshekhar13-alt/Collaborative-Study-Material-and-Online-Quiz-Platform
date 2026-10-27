"""Allow "dismiss_report" in the moderation action log (S13.2).

A moderator who clears an unfounded report now leaves a ``moderation_actions`` row
like every other decision. Enum columns are VARCHAR plus a CHECK constraint, so this
only widens the ``moderation_action_type`` constraint; no data changes.

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-27
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0010"
down_revision: Union[str, None] = "0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_BEFORE = (
    "remove_content",
    "restore_content",
    "suspend_user",
    "unsuspend_user",
    "approve_channel",
    "reject_channel",
    "warn_user",
)
_AFTER = _BEFORE + ("dismiss_report",)


def _enum(values: tuple[str, ...], name: str) -> sa.Enum:
    return sa.Enum(*values, name=name, native_enum=False, create_constraint=True, length=32)


def _check(values: tuple[str, ...]) -> str:
    return "action IN ({})".format(", ".join(f"'{v}'" for v in values))


def _set_actions(values: tuple[str, ...]) -> None:
    if op.get_context().dialect.name == "sqlite":
        # SQLite cannot alter a constraint: rebuild the table with the new column type.
        # The rebuild reflects the other constraints and indexes, so they are kept.
        with op.batch_alter_table("moderation_actions", recreate="always") as batch_op:
            batch_op.alter_column(
                "action",
                existing_type=sa.String(length=32),
                type_=_enum(values, "moderation_action_type"),
                existing_nullable=False,
            )
    else:
        op.drop_constraint("moderation_action_type", "moderation_actions", type_="check")
        op.create_check_constraint("moderation_action_type", "moderation_actions", _check(values))


def upgrade() -> None:
    _set_actions(_AFTER)


def downgrade() -> None:
    op.execute("DELETE FROM moderation_actions WHERE action = 'dismiss_report'")
    _set_actions(_BEFORE)
