"""PRM-100: request_id and cached prompt tokens on usage rows

Revision ID: 8572ddb98a93
Revises: f78b6d4f8ca9
Create Date: 2026-09-14 14:28:40.036563
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "8572ddb98a93"
down_revision: str | None = "f78b6d4f8ca9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Guarded like every migration since RM-68: a database older than the
    # Alembic baseline has its missing pieces filled in by create_all() from
    # today's models, so the columns can already be there.
    columns = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("usage_events")}
    with op.batch_alter_table("usage_events", schema=None) as batch_op:
        if "request_id" not in columns:
            batch_op.add_column(sa.Column("request_id", sa.String(length=64), nullable=True))
        if "cached_prompt_tokens" not in columns:
            batch_op.add_column(
                sa.Column(
                    "cached_prompt_tokens",
                    sa.BigInteger(),
                    server_default=sa.text("0"),
                    nullable=False,
                )
            )


def downgrade() -> None:
    with op.batch_alter_table("usage_events", schema=None) as batch_op:
        batch_op.drop_column("cached_prompt_tokens")
        batch_op.drop_column("request_id")
