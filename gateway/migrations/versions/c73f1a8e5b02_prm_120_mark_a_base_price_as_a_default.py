"""PRM-120: mark a base price as a default, not as a decision

Revision ID: c73f1a8e5b02
Revises: b41c7e9d2a05
Create Date: 2026-09-17 06:55:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c73f1a8e5b02"
down_revision: str | None = "b41c7e9d2a05"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "model_price_config"
_COLUMN = "is_default"


def _has_column() -> bool:
    inspector = sa.inspect(op.get_bind())
    if _TABLE not in inspector.get_table_names():
        return True  # nothing to alter; create_all will build it with the column
    return _COLUMN in {c["name"] for c in inspector.get_columns(_TABLE)}


def upgrade() -> None:
    """RM-89's rule, applied to money: a default that cannot be told apart from
    a choice is a bug. Every price in this table used to be one an operator
    typed; PRM-120 seeds one for every model, so the two now have to be
    distinguishable — in the UI, and to anyone auditing why a client was
    charged what they were charged.

    Existing rows are all operator-set by definition, so they default to 0.
    """
    if _has_column():
        return
    op.add_column(
        _TABLE,
        sa.Column(_COLUMN, sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if _TABLE in inspector.get_table_names() and _COLUMN in {
        c["name"] for c in inspector.get_columns(_TABLE)
    }:
        op.drop_column(_TABLE, _COLUMN)
