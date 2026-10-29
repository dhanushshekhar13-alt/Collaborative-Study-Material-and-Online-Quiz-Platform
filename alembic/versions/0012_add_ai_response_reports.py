"""Make AI output reportable and withdrawable (NFR-06).

* ``reports.target_type`` and ``moderation_actions.target_type`` accept
  ``ai_response`` (an ``llm_requests`` row), so a report on an AI explanation,
  generated batch, topic quiz or evaluation is captured and routed to moderation.
  Enum columns are VARCHAR plus a CHECK constraint, so only the constraints widen.
* ``llm_requests.withdrawn`` records that a moderator upheld such a report.

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-29
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0012"
down_revision: Union[str, None] = "0011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_BEFORE = (
    "material",
    "question",
    "quiz",
    "channel_message",
    "direct_message",
    "user",
    "channel",
)
_AFTER = _BEFORE + ("ai_response",)
_TABLES = (("reports", "report_target"), ("moderation_actions", "moderation_action_target"))


def _enum(values: tuple[str, ...], name: str) -> sa.Enum:
    return sa.Enum(*values, name=name, native_enum=False, create_constraint=True, length=32)


def _check(values: tuple[str, ...]) -> str:
    return "target_type IN ({})".format(", ".join(f"'{v}'" for v in values))


def _set_targets(values: tuple[str, ...]) -> None:
    for table, constraint in _TABLES:
        if op.get_context().dialect.name == "sqlite":
            # SQLite cannot alter a constraint: rebuild the table with the new type.
            with op.batch_alter_table(table, recreate="always") as batch_op:
                batch_op.alter_column(
                    "target_type",
                    existing_type=sa.String(length=32),
                    type_=_enum(values, constraint),
                    existing_nullable=False,
                )
        else:
            op.drop_constraint(constraint, table, type_="check")
            op.create_check_constraint(constraint, table, _check(values))


def upgrade() -> None:
    _set_targets(_AFTER)
    with op.batch_alter_table("llm_requests") as batch_op:
        batch_op.add_column(
            sa.Column("withdrawn", sa.Boolean(), server_default="0", nullable=False)
        )


def downgrade() -> None:
    with op.batch_alter_table("llm_requests") as batch_op:
        batch_op.drop_column("withdrawn")
    op.execute("DELETE FROM moderation_actions WHERE target_type = 'ai_response'")
    op.execute("DELETE FROM reports WHERE target_type = 'ai_response'")
    _set_targets(_BEFORE)
