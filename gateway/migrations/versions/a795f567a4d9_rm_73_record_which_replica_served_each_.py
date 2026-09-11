"""RM-73: record which replica served each usage event

Revision ID: a795f567a4d9
Revises: 4aad3b8652af
Create Date: 2026-09-11 17:27:45.983235
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a795f567a4d9"
down_revision: str | None = "4aad3b8652af"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Guarded because this project adopted Alembic after the fact: a database
    # older than the baseline has its missing tables filled in by create_all()
    # from today's models, so usage_events can already arrive with this column.
    # See db.py's _migrate_to_head.
    inspector = sa.inspect(op.get_bind())
    if "instance_id" in {c["name"] for c in inspector.get_columns("usage_events")}:
        return
    with op.batch_alter_table("usage_events", schema=None) as batch_op:
        batch_op.add_column(sa.Column("instance_id", sa.String(length=128), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("usage_events", schema=None) as batch_op:
        batch_op.drop_column("instance_id")
