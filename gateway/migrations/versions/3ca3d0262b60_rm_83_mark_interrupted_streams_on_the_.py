"""RM-83: mark interrupted streams on the usage row

Revision ID: 3ca3d0262b60
Revises: ff0accf5af8f
Create Date: 2026-09-12 20:02:06.278025
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "3ca3d0262b60"
down_revision: str | None = "ff0accf5af8f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Guarded like every post-baseline migration here: a database older than the
    # baseline has its missing tables filled in by create_all() from today's
    # models, so this column can already exist. See db.py's _migrate_to_head.
    if "interrupted" in {c["name"] for c in sa.inspect(op.get_bind()).get_columns("usage_events")}:
        return
    with op.batch_alter_table("usage_events", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("interrupted", sa.Boolean(), server_default=sa.text("0"), nullable=False)
        )


def downgrade() -> None:
    with op.batch_alter_table("usage_events", schema=None) as batch_op:
        batch_op.drop_column("interrupted")
