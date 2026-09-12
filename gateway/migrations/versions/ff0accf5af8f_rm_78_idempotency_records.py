"""RM-78: idempotency records

Revision ID: ff0accf5af8f
Revises: a795f567a4d9
Create Date: 2026-09-12 16:26:21.609271
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "ff0accf5af8f"
down_revision: str | None = "a795f567a4d9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Guarded for the same reason RM-73's column add is: this project adopted
    # Alembic after the fact, so a database older than the baseline has its
    # missing tables filled in by create_all() from today's models, and this
    # one can already be there. See db.py's _migrate_to_head.
    if "idempotency_records" in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        "idempotency_records",
        sa.Column("client_id", sa.String(length=64), nullable=False),
        sa.Column("key", sa.String(length=255), nullable=False),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("status_code", sa.Integer(), nullable=True),
        sa.Column("response_body", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("client_id", "key"),
    )


def downgrade() -> None:
    op.drop_table("idempotency_records")
