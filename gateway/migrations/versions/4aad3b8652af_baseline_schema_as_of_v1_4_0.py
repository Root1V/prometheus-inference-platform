"""baseline: schema as of v1.4.0

The starting point for Alembic — docs/roadmap.md RM-68. Everything here already
exists in any database created before migrations were adopted, so `create_tables()`
brings such a database up to this exact shape and then stamps this revision
instead of running it. A brand-new database runs it normally.

Revision ID: 4aad3b8652af
Revises:
Create Date: 2026-09-11 02:14:17.694066
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "4aad3b8652af"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "circuit_breaker_config",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("circuit_breaker_failure_threshold", sa.Integer(), nullable=False),
        sa.Column("circuit_breaker_recovery_timeout", sa.Integer(), nullable=False),
        sa.Column("circuit_breaker_success_threshold", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "client_billing_settings",
        sa.Column("client_id", sa.String(length=64), nullable=False),
        sa.Column("monthly_spend_cap_usd", sa.Float(), nullable=True),
        sa.Column("alert_thresholds_percent", sa.String(length=64), nullable=True),
        sa.Column("tax_rate_percent", sa.Float(), nullable=False),
        sa.Column("preferred_currency", sa.String(length=3), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("client_id"),
    )
    op.create_table(
        "currency_rates",
        sa.Column("currency_code", sa.String(length=3), nullable=False),
        sa.Column("units_per_usd", sa.Float(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("currency_code"),
    )
    op.create_table(
        "model_price_config",
        sa.Column("model_id", sa.String(length=128), nullable=False),
        sa.Column("prompt_price_per_1m", sa.Float(), nullable=True),
        sa.Column("completion_price_per_1m", sa.Float(), nullable=True),
        sa.Column("image_price", sa.Float(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("model_id"),
    )
    op.create_table(
        "rate_limit_config",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("rate_limit_rpm", sa.Integer(), nullable=False),
        sa.Column("rate_limit_tpm", sa.Integer(), nullable=False),
        sa.Column("rate_limit_rpm_chat_completions", sa.Integer(), nullable=True),
        sa.Column("rate_limit_tpm_chat_completions", sa.Integer(), nullable=True),
        sa.Column("rate_limit_rpm_admin", sa.Integer(), nullable=True),
        sa.Column("rate_limit_tpm_admin", sa.Integer(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "usage_daily",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("client_id", sa.String(length=64), nullable=False),
        sa.Column("model_id", sa.String(length=128), nullable=False),
        sa.Column("prompt_tokens", sa.BigInteger(), nullable=False),
        sa.Column("completion_tokens", sa.BigInteger(), nullable=False),
        sa.Column("request_count", sa.BigInteger(), nullable=False),
        sa.Column("cost_usd", sa.Float(), nullable=True),
        sa.Column("prompt_cost_usd", sa.Float(), nullable=True),
        sa.Column("completion_cost_usd", sa.Float(), nullable=True),
        sa.Column("image_cost_usd", sa.Float(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("day", "client_id", "model_id", name="uq_usage_daily_day_client_model"),
    )
    with op.batch_alter_table("usage_daily", schema=None) as batch_op:
        batch_op.create_index("ix_usage_daily_day", ["day"], unique=False)

    op.create_table(
        "usage_events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("client_id", sa.String(length=64), nullable=False),
        sa.Column("model_id", sa.String(length=128), nullable=False),
        sa.Column("request_kind", sa.String(length=16), nullable=False),
        sa.Column("prompt_tokens", sa.BigInteger(), nullable=False),
        sa.Column("completion_tokens", sa.BigInteger(), nullable=False),
        sa.Column("image_count", sa.BigInteger(), nullable=False),
        sa.Column("prompt_price_per_1m", sa.Float(), nullable=True),
        sa.Column("completion_price_per_1m", sa.Float(), nullable=True),
        sa.Column("image_price_each", sa.Float(), nullable=True),
        sa.Column("cost_usd", sa.Float(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("usage_events", schema=None) as batch_op:
        batch_op.create_index("ix_usage_events_client_day", ["client_id", "day"], unique=False)
        batch_op.create_index("ix_usage_events_day", ["day"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("usage_events", schema=None) as batch_op:
        batch_op.drop_index("ix_usage_events_day")
        batch_op.drop_index("ix_usage_events_client_day")

    op.drop_table("usage_events")
    with op.batch_alter_table("usage_daily", schema=None) as batch_op:
        batch_op.drop_index("ix_usage_daily_day")

    op.drop_table("usage_daily")
    op.drop_table("rate_limit_config")
    op.drop_table("model_price_config")
    op.drop_table("currency_rates")
    op.drop_table("client_billing_settings")
    op.drop_table("circuit_breaker_config")
