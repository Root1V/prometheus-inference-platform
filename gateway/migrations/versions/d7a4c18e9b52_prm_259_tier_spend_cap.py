"""PRM-259: a tier carries its monthly spend cap

Revision ID: d7a4c18e9b52
Revises: b48e2f9c17d3
Create Date: 2026-10-13 14:05:00.000000

The money a tier carries in this industry is a ceiling on spend, not a price.
OpenAI's three tiers cap at $500 / $5,000 / $200,000 a month and Anthropic's
at $500 / $1,000 / $200,000; neither changes the per-token price between them.
A client's own cap still wins where they have one — the tier supplies the
default for everyone else on it.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d7a4c18e9b52"
down_revision: str | None = "b48e2f9c17d3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "rate_limit_tiers"
_COLUMN = "monthly_spend_cap_usd"


def _columns() -> set[str]:
    inspector = sa.inspect(op.get_bind())
    if _TABLE not in inspector.get_table_names():
        return set()
    return {c["name"] for c in inspector.get_columns(_TABLE)}


def upgrade() -> None:
    if _TABLE not in sa.inspect(op.get_bind()).get_table_names():
        return
    if _COLUMN in _columns():
        return
    op.add_column(_TABLE, sa.Column(_COLUMN, sa.Float(), nullable=True))


def downgrade() -> None:
    if _COLUMN not in _columns():
        return
    op.drop_column(_TABLE, _COLUMN)
