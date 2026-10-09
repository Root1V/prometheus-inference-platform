"""PRM-245: how long it took, on the usage row

Revision ID: b48e2f9c17d3
Revises: c91d7e44b2a8
Create Date: 2026-10-09 00:10:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b48e2f9c17d3"
down_revision: str | None = "c91d7e44b2a8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_TABLE = "usage_events"
_COLUMNS = ("duration_ms", "ttft_ms")


def _existing() -> set[str]:
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns(_TABLE)}


def upgrade() -> None:
    # Guarded, the lesson PRM-160 recorded: the pre-Alembic adoption path runs
    # create_all from today's models before stamping the baseline.
    present = _existing()
    for name in _COLUMNS:
        if name not in present:
            # Nullable with no default. Null means "not timed", which is the
            # honest answer for every row written before this and for any path
            # that does not measure — not zero, which would drag a percentile
            # down with requests that never happened.
            op.add_column(_TABLE, sa.Column(name, sa.Integer(), nullable=True))


def downgrade() -> None:
    present = _existing()
    for name in _COLUMNS:
        if name in present:
            op.drop_column(_TABLE, name)
