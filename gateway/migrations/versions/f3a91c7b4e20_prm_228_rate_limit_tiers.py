"""PRM-228: rate-limit tiers

Revision ID: f3a91c7b4e20
Revises: e42ed3dc8f09
Create Date: 2026-10-07 16:20:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f3a91c7b4e20"
down_revision: str | None = "e42ed3dc8f09"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_TABLE = "rate_limit_tiers"
_SETTINGS = "client_billing_settings"


def _has_table() -> bool:
    return _TABLE in sa.inspect(op.get_bind()).get_table_names()


def _has_tier_column() -> bool:
    cols = sa.inspect(op.get_bind()).get_columns(_SETTINGS)
    return any(c["name"] == "tier" for c in cols)


def upgrade() -> None:
    # Guarded both ways, the lesson PRM-160 recorded and PRM-157 learned the
    # hard way: the pre-Alembic adoption path runs create_all from today's
    # models before stamping the baseline, so a database adopted after this was
    # written already has both.
    if not _has_table():
        op.create_table(
            _TABLE,
            sa.Column("name", sa.String(length=32), primary_key=True),
            sa.Column("description", sa.String(length=256), nullable=True),
            # Nullable throughout, and null means "the platform default for
            # this dimension" rather than "unlimited" — a tier that omits `ipm`
            # says nothing about images, it does not grant them freely.
            sa.Column("rpm", sa.Integer(), nullable=True),
            sa.Column("tpm", sa.Integer(), nullable=True),
            sa.Column("tpm_input", sa.Integer(), nullable=True),
            sa.Column("tpm_output", sa.Integer(), nullable=True),
            sa.Column("rpd", sa.Integer(), nullable=True),
            sa.Column("tpd", sa.Integer(), nullable=True),
            sa.Column("ipm", sa.Integer(), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        )
    if not _has_tier_column():
        # Nullable with no default: an existing client is on no tier, which is
        # the platform defaults, which is exactly what it had yesterday.
        op.add_column(_SETTINGS, sa.Column("tier", sa.String(length=32), nullable=True))


def downgrade() -> None:
    if _has_tier_column():
        op.drop_column(_SETTINGS, "tier")
    if _has_table():
        op.drop_table(_TABLE)
