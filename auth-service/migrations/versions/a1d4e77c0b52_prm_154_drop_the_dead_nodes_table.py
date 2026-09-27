"""PRM-154: drop the dead `nodes` table

The first revision in this service that removes something, and the reason PRM-153
existed: the mechanism it replaced could add a column and nothing else, so the
table PRM-134 left behind could not be taken out even once everyone agreed it
should be.

PRM-134 moved the fleet's node list to the coordinator manager-api and left this
table and its rows in place as the rollback path, deliberately. The rows were
copied by scripts/migrate_node_registry.py, which deleted nothing. The window is
closed: the coordinator has owned the registry since 2026-09-26, PRM-151 and
PRM-152 have both built on top of it, and reverting to a registry that has not
been written to since would now lose more than it restored.

Revision ID: a1d4e77c0b52
Revises: f7060a106063
Create Date: 2026-09-27 07:10:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a1d4e77c0b52"
down_revision: str | None = "f7060a106063"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_TABLE = "nodes"


def _has_table() -> bool:
    return _TABLE in sa.inspect(op.get_bind()).get_table_names()


def upgrade() -> None:
    # Guarded, and for the mirror image of the usual reason. The adoption path
    # runs `create_all` from today's models before stamping the baseline, and
    # today's models no longer define `Node` — so a database created from here on
    # never has this table and the drop would fail on a fresh install.
    if not _has_table():
        return
    op.drop_table(_TABLE)


def downgrade() -> None:
    """Recreates the table, empty.

    The shape comes back; the rows do not. They were never this revision's to
    restore — a downgrade cannot know what was in a table it dropped, and the
    copy that matters has been the coordinator's `fleet.db` since PRM-134.
    """
    if _has_table():
        return
    op.create_table(
        _TABLE,
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("manager_url", sa.String(length=512), nullable=False),
        sa.Column("node_type", sa.Enum("mac", "nvidia", "other", name="nodetype"), nullable=False),
        sa.Column("tag", sa.Text(), nullable=True),
        sa.Column("hourly_cost_usd", sa.Float(), nullable=True),
        sa.Column("hardware_amortization_usd_per_hour", sa.Float(), nullable=False),
        sa.Column("electricity_usd_per_hour", sa.Float(), nullable=False),
        sa.Column("price_margin_multiplier", sa.Float(), nullable=False),
        sa.Column("engines", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )
