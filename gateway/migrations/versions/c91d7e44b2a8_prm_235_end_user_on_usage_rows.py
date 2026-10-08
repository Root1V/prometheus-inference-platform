"""PRM-235: who the caller says is behind the request

Revision ID: c91d7e44b2a8
Revises: a7c25f1d3b84
Create Date: 2026-10-08 00:20:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c91d7e44b2a8"
down_revision: str | None = "a7c25f1d3b84"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_TABLE = "usage_events"
_COLUMN = "end_user"
_INDEX = "ix_usage_events_end_user"


def _has_column() -> bool:
    return any(c["name"] == _COLUMN for c in sa.inspect(op.get_bind()).get_columns(_TABLE))


def upgrade() -> None:
    # Guarded, the lesson PRM-160 recorded: the pre-Alembic adoption path runs
    # create_all from today's models before stamping the baseline.
    if _has_column():
        return
    # Nullable with no default, and null is the normal case rather than a hole
    # to backfill: every row written before this, and every row from a caller
    # that identifies nobody, is a request with no end user — which is an
    # answer, not a gap.
    op.add_column(_TABLE, sa.Column(_COLUMN, sa.String(length=128), nullable=True))
    # Indexed because the Activity page groups by it, and a grouping key with
    # no index is a full scan of the largest table here (14,822 rows on the
    # deployment this was written against, and that is the small one).
    op.create_index(_INDEX, _TABLE, [_COLUMN])


def downgrade() -> None:
    if not _has_column():
        return
    op.drop_index(_INDEX, table_name=_TABLE)
    op.drop_column(_TABLE, _COLUMN)
