"""PRM-113: bill against a stable model id, keep the slug for display

Revision ID: b41c7e9d2a05
Revises: 9c4e1a77b210
Create Date: 2026-09-15 21:40:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b41c7e9d2a05"
down_revision: str | None = "9c4e1a77b210"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = ("usage_events", "usage_daily")


def upgrade() -> None:
    # Usage rows were keyed by the model's *slug*, which RM-70 lets an operator
    # set once. Naming a model therefore split its billing history in two — this
    # deployment has qwen3-0.6b (85 rows) and qwen3-0-6b-iq4-nl-local-2 (37)
    # sitting side by side, one model in two buckets — and made it miss its
    # pricing.yaml entry, so it silently billed nothing from that point on.
    #
    # model_id becomes the catalog id, which never changes. model_slug carries
    # the name in force when the row was written, because an invoice should say
    # what the thing was called at the time.
    #
    # Guarded like every migration since RM-68.
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    for table in _TABLES:
        columns = {c["name"] for c in inspector.get_columns(table)}
        if "model_slug" not in columns:
            with op.batch_alter_table(table, schema=None) as batch_op:
                batch_op.add_column(sa.Column("model_slug", sa.String(length=128), nullable=True))
        # Every existing row was written with the slug in model_id, so that is
        # exactly what the display name was at the time. Copy, do not guess.
        op.execute(
            sa.text(f"UPDATE {table} SET model_slug = model_id WHERE model_slug IS NULL")  # noqa: S608
        )


def downgrade() -> None:
    for table in _TABLES:
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.drop_column("model_slug")
