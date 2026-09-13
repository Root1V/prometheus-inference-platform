"""RM-88: why a streamed request stopped

Revision ID: 88e6d866d34c
Revises: 3ca3d0262b60
Create Date: 2026-09-13 08:37:31.586432
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "88e6d866d34c"
down_revision: str | None = "3ca3d0262b60"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Guarded like every migration since RM-68: this project adopted Alembic
    # after the fact, so a database older than the baseline has its missing
    # pieces filled in by create_all() from today's models, and the column can
    # already be there. See db.py's _migrate_to_head.
    columns = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("usage_events")}
    if "termination_reason" not in columns:
        with op.batch_alter_table("usage_events", schema=None) as batch_op:
            batch_op.add_column(
                sa.Column(
                    "termination_reason",
                    sa.String(length=24),
                    server_default=sa.text("'complete'"),
                    nullable=False,
                )
            )

    # Existing rows carry the answer already, in the boolean this column
    # replaces: `interrupted` meant "our stream broke" and nothing else, because
    # a caller hanging up was the case that wrote no row at all before RM-87.
    # Reading it that way is exact rather than a guess.
    op.execute(
        "UPDATE usage_events SET termination_reason = 'upstream_error' "
        "WHERE interrupted = 1 AND termination_reason = 'complete'"
    )


def downgrade() -> None:
    with op.batch_alter_table("usage_events", schema=None) as batch_op:
        batch_op.drop_column("termination_reason")
