"""PRM-100: request_id on idempotency records

Revision ID: 9c4e1a77b210
Revises: 8572ddb98a93
Create Date: 2026-09-14 14:45:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "9c4e1a77b210"
down_revision: str | None = "8572ddb98a93"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Guarded like every migration since RM-68: a database older than the
    # Alembic baseline has its missing pieces filled in by create_all() from
    # today's models, so the column can already be there.
    columns = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("idempotency_records")}
    if "request_id" in columns:
        return
    with op.batch_alter_table("idempotency_records", schema=None) as batch_op:
        batch_op.add_column(sa.Column("request_id", sa.String(length=64), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("idempotency_records", schema=None) as batch_op:
        batch_op.drop_column("request_id")
