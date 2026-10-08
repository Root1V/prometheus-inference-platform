"""PRM-232: the eleven limits become editable

Revision ID: a7c25f1d3b84
Revises: f3a91c7b4e20
Create Date: 2026-10-07 20:10:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a7c25f1d3b84"
down_revision: str | None = "f3a91c7b4e20"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_TABLE = "rate_limit_config"

# The eleven PRM-224 through PRM-228 added to Settings and left `.env`-only.
# PRM-182 recorded why they waited: every name in `rate_limits.RATE_LIMIT_FIELDS`
# is read off this row with `getattr` at startup, so a name without a column is
# an AttributeError on exactly those deployments that have ever saved limits
# from the dashboard. This is the migration that makes the list safe to grow,
# and it is one change rather than eleven for the same reason.
_COLUMNS = (
    "rate_limit_rpm_platform",
    "rate_limit_tpm_platform",
    "rate_limit_rpm_client",
    "rate_limit_tpm_client",
    "rate_limit_tpm_input",
    "rate_limit_tpm_output",
    "rate_limit_rpd",
    "rate_limit_tpd",
    "rate_limit_ipm",
    "rate_limit_rpm_predict",
    "rate_limit_tpm_predict",
)


def _existing() -> set[str]:
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns(_TABLE)}


def upgrade() -> None:
    # Guarded, the lesson PRM-160 recorded: the pre-Alembic adoption path runs
    # create_all from today's models before stamping the baseline, so a
    # database adopted after this was written already has them.
    present = _existing()
    for name in _COLUMNS:
        if name not in present:
            # Nullable with no default. Null here means "nothing saved for this
            # dimension", which `apply_limits` writes onto Settings as None —
            # and None on these means what it already meant to the middleware:
            # no ceiling for the platform ones, the derived default for the
            # client pair, the global value for the per-endpoint ones. An
            # existing row keeps behaving exactly as it did yesterday.
            op.add_column(_TABLE, sa.Column(name, sa.Integer(), nullable=True))


def downgrade() -> None:
    present = _existing()
    for name in _COLUMNS:
        if name in present:
            op.drop_column(_TABLE, name)
