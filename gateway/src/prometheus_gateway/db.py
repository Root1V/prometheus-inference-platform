"""Persisted usage tracking — replaces the old Redis daily-TTL counters.

Implements: docs/roadmap.md — RM-32 (persisted history + per-model breakdown),
RM-60 (write-time cost, append-only audit trail, billing config).
Mirrors auth-service's db.py conventions (async SQLAlchemy, SQLite by default).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import (
    BigInteger,
    Date,
    DateTime,
    Float,
    Index,
    String,
    UniqueConstraint,
    case,
    func,
    inspect,
    select,
    text,
)
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from . import pricing


class Base(DeclarativeBase):
    pass


class UsageDaily(Base):
    """One row per (day, client, model) — token/request counters, incremented in place.

    Fast-path rollup for the live "today" dashboard poll (`GET /v1/usage`).
    `usage_events` (below) is the append-only source of truth for CSV export,
    period summaries, and the billing trend chart — RM-60.
    """

    __tablename__ = "usage_daily"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    day: Mapped[date] = mapped_column(Date, nullable=False)
    client_id: Mapped[str] = mapped_column(String(64), nullable=False)
    model_id: Mapped[str] = mapped_column(String(128), nullable=False)
    prompt_tokens: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    completion_tokens: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    request_count: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    # RM-60: cost at the rate in effect when each contributing request was made
    # (never recomputed later against a changed pricing table). NULL iff every
    # contributing request was unpriced — never a silent $0.
    cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    # RM-60 follow-up: cost_usd broken into its components, so the Usage page
    # can show what's being paid for input vs. inference vs. images
    # separately, not just the combined total. Same write-time-only,
    # never-recomputed, never-a-silent-$0 rules as cost_usd.
    prompt_cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    completion_cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    image_cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)

    __table_args__ = (
        UniqueConstraint("day", "client_id", "model_id", name="uq_usage_daily_day_client_model"),
        Index("ix_usage_daily_day", "day"),
    )


class UsageEvent(Base):
    """Append-only audit trail — one row per request, never mutated.

    Implements: docs/roadmap.md — RM-60 (#2). Source of truth for CSV export,
    period summaries, and the billing trend chart; `usage_daily` is a
    rebuildable rollup of this table kept only for the live-poll fast path.
    """

    __tablename__ = "usage_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    day: Mapped[date] = mapped_column(Date, nullable=False)
    client_id: Mapped[str] = mapped_column(String(64), nullable=False)
    model_id: Mapped[str] = mapped_column(String(128), nullable=False)
    request_kind: Mapped[str] = mapped_column(
        String(16), nullable=False
    )  # "chat" | "embedding" | "image"
    prompt_tokens: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    completion_tokens: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    image_count: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    prompt_price_per_1m: Mapped[float | None] = mapped_column(Float, nullable=True)
    completion_price_per_1m: Mapped[float | None] = mapped_column(Float, nullable=True)
    image_price_each: Mapped[float | None] = mapped_column(Float, nullable=True)
    cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)

    __table_args__ = (
        Index("ix_usage_events_client_day", "client_id", "day"),
        Index("ix_usage_events_day", "day"),
    )


class ClientBillingSettings(Base):
    """Per-client billing config — RM-60. Lives in the gateway's own DB (not
    auth-service's Principal) since the hard spend cap must be checked on the
    hot inference path without a cross-service call.
    """

    __tablename__ = "client_billing_settings"

    client_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    monthly_spend_cap_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    alert_thresholds_percent: Mapped[str | None] = mapped_column(String(64), nullable=True)
    tax_rate_percent: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    preferred_currency: Mapped[str] = mapped_column(String(3), default="USD", nullable=False)
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, onupdate=lambda: datetime.now(timezone.utc)
    )


class CurrencyRate(Base):
    """Admin-configured static exchange rate — RM-60. No USD row (identity)."""

    __tablename__ = "currency_rates"

    currency_code: Mapped[str] = mapped_column(String(3), primary_key=True)
    units_per_usd: Mapped[float] = mapped_column(Float, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class ModelPriceConfig(Base):
    """Admin-configured per-model price — RM-60 follow-up.

    Persists what used to require hand-editing gateway/pricing.yaml +
    restart. Loaded into the in-memory PricingTable at startup and on every
    admin write (see billing.py's reload helpers) so a change takes effect
    immediately, without a restart. pricing.yaml still seeds the table at
    first boot; a row here overrides the YAML value for that model_id.
    """

    __tablename__ = "model_price_config"

    model_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    prompt_price_per_1m: Mapped[float | None] = mapped_column(Float, nullable=True)
    completion_price_per_1m: Mapped[float | None] = mapped_column(Float, nullable=True)
    image_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


_engine: AsyncEngine | None = None
_session_factory: sessionmaker | None = None  # type: ignore[type-arg]


def init_db_engine(db_url: str) -> AsyncEngine:
    global _engine, _session_factory
    _engine = create_async_engine(db_url, echo=False)
    _session_factory = sessionmaker(_engine, class_=AsyncSession, expire_on_commit=False)  # type: ignore[call-overload]
    return _engine


def get_engine() -> AsyncEngine:
    if _engine is None:
        raise RuntimeError("Database engine not initialised. Call init_db_engine() first.")
    return _engine


def get_session_factory() -> sessionmaker:  # type: ignore[type-arg]
    if _session_factory is None:
        raise RuntimeError("Database engine not initialised. Call init_db_engine() first.")
    return _session_factory


# Nullable FLOAT columns added to usage_daily after its first release —
# `create_all()` only creates missing TABLES, never ALTERs existing ones (this
# project has no Alembic), so each needs a one-time idempotent ALTER TABLE for
# a gateway.db that predates it. A brand-new DB gets them for free via the
# model above.
_USAGE_DAILY_ADDED_COLUMNS = (
    "cost_usd",
    "prompt_cost_usd",
    "completion_cost_usd",
    "image_cost_usd",
)


def _ensure_usage_daily_cost_column(sync_conn: Connection) -> None:
    """RM-60 stopgap, not a real migration tool. Idempotent, safe to run on
    every startup.
    """
    inspector = inspect(sync_conn)
    if "usage_daily" not in inspector.get_table_names():
        return
    existing_columns = {c["name"] for c in inspector.get_columns("usage_daily")}
    for column_name in _USAGE_DAILY_ADDED_COLUMNS:
        if column_name not in existing_columns:
            sync_conn.execute(text(f"ALTER TABLE usage_daily ADD COLUMN {column_name} FLOAT"))


async def create_tables(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_ensure_usage_daily_cost_column)


def _accumulate_nullable_cost(existing_col: Any, new_col: Any) -> Any:
    """NULL+NULL must stay NULL (still "no price configured" — never a
    silent $0); otherwise sum treating a NULL side as 0.
    """
    return case(
        (existing_col.is_(None) & new_col.is_(None), None),
        else_=func.coalesce(existing_col, 0.0) + func.coalesce(new_col, 0.0),
    )


def _usage_daily_upsert_stmt(
    dialect_name: str,
    day: date,
    client_id: str,
    model_id: str,
    prompt_tokens: int,
    completion_tokens: int,
    cost_usd: float | None,
    prompt_cost_usd: float | None,
    completion_cost_usd: float | None,
    image_cost_usd: float | None,
) -> Any:
    insert_fn = sqlite_insert if dialect_name == "sqlite" else pg_insert
    stmt = insert_fn(UsageDaily).values(
        id=str(uuid.uuid4()),
        day=day,
        client_id=client_id,
        model_id=model_id,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        request_count=1,
        cost_usd=cost_usd,
        prompt_cost_usd=prompt_cost_usd,
        completion_cost_usd=completion_cost_usd,
        image_cost_usd=image_cost_usd,
    )
    excluded = stmt.excluded
    return stmt.on_conflict_do_update(
        index_elements=["day", "client_id", "model_id"],
        set_={
            "prompt_tokens": UsageDaily.prompt_tokens + prompt_tokens,
            "completion_tokens": UsageDaily.completion_tokens + completion_tokens,
            "request_count": UsageDaily.request_count + 1,
            "cost_usd": _accumulate_nullable_cost(UsageDaily.cost_usd, excluded.cost_usd),
            "prompt_cost_usd": _accumulate_nullable_cost(
                UsageDaily.prompt_cost_usd, excluded.prompt_cost_usd
            ),
            "completion_cost_usd": _accumulate_nullable_cost(
                UsageDaily.completion_cost_usd, excluded.completion_cost_usd
            ),
            "image_cost_usd": _accumulate_nullable_cost(
                UsageDaily.image_cost_usd, excluded.image_cost_usd
            ),
        },
    )


async def record_usage(
    client_id: str,
    model_id: str,
    prompt_tokens: int,
    completion_tokens: int,
    *,
    request_kind: str = "chat",
    image_count: int = 0,
    day: date | None = None,
) -> None:
    """Record one request's usage: an immutable `usage_events` row (the audit
    trail, priced at the rate in effect right now) plus an atomic upsert into
    `usage_daily`'s rollup counters. Implements: docs/roadmap.md — RM-60 (#1, #2).
    """
    d = day or datetime.now(tz=timezone.utc).date()
    price_table = pricing.get_pricing_table()
    price = price_table.get_price(model_id)

    prompt_cost_usd: float | None
    completion_cost_usd: float | None
    image_cost_usd: float | None

    if request_kind == "image":
        image_price_each = price.image_price if price else None
        image_cost_usd = image_price_each * image_count if image_price_each is not None else None
        prompt_price_per_1m = completion_price_per_1m = None
        prompt_cost_usd = completion_cost_usd = None
        cost_usd = image_cost_usd
    else:
        prompt_price_per_1m = price.prompt_price_per_1m if price else None
        completion_price_per_1m = price.completion_price_per_1m if price else None
        if prompt_price_per_1m is not None and completion_price_per_1m is not None:
            prompt_cost_usd = prompt_tokens * prompt_price_per_1m / 1_000_000
            completion_cost_usd = completion_tokens * completion_price_per_1m / 1_000_000
        else:
            prompt_cost_usd = completion_cost_usd = None
        cost_usd = price_table.estimate_cost_usd(model_id, prompt_tokens, completion_tokens)
        image_price_each = None
        image_cost_usd = None

    session_factory = get_session_factory()
    async with session_factory() as session:
        session.add(
            UsageEvent(
                day=d,
                client_id=client_id,
                model_id=model_id,
                request_kind=request_kind,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                image_count=image_count,
                prompt_price_per_1m=prompt_price_per_1m,
                completion_price_per_1m=completion_price_per_1m,
                image_price_each=image_price_each,
                cost_usd=cost_usd,
            )
        )
        await session.execute(
            _usage_daily_upsert_stmt(
                session.bind.dialect.name,
                d,
                client_id,
                model_id,
                prompt_tokens,
                completion_tokens,
                cost_usd,
                prompt_cost_usd,
                completion_cost_usd,
                image_cost_usd,
            )
        )
        await session.commit()


async def query_usage_day(day: date) -> list[UsageDaily]:
    """Return every (client, model) row recorded for `day`."""
    session_factory = get_session_factory()
    async with session_factory() as session:
        result = await session.execute(select(UsageDaily).where(UsageDaily.day == day))
        return list(result.scalars().all())


async def query_usage_events_range(
    start: date, end: date, client_id: str | None = None
) -> list[UsageEvent]:
    """Every event in [start, end] inclusive, ordered for a stable CSV export."""
    session_factory = get_session_factory()
    async with session_factory() as session:
        stmt = select(UsageEvent).where(UsageEvent.day >= start, UsageEvent.day <= end)
        if client_id is not None:
            stmt = stmt.where(UsageEvent.client_id == client_id)
        stmt = stmt.order_by(UsageEvent.day, UsageEvent.recorded_at)
        result = await session.execute(stmt)
        return list(result.scalars().all())


async def query_daily_cost_range(
    start: date, end: date, client_id: str | None = None
) -> list[dict[str, Any]]:
    """Per-day totals for [start, end] inclusive — used by the billing trend
    chart. Grouped in SQL rather than pulling every raw event into Python.
    """
    session_factory = get_session_factory()
    async with session_factory() as session:
        stmt = (
            select(
                UsageEvent.day,
                func.sum(UsageEvent.cost_usd).label("cost_usd"),
                func.sum(UsageEvent.prompt_tokens + UsageEvent.completion_tokens).label("tokens"),
                func.count().label("request_count"),
            )
            .where(UsageEvent.day >= start, UsageEvent.day <= end)
            .group_by(UsageEvent.day)
            .order_by(UsageEvent.day)
        )
        if client_id is not None:
            stmt = stmt.where(UsageEvent.client_id == client_id)
        result = await session.execute(stmt)
        return [
            {
                "day": row.day,
                "cost_usd": row.cost_usd,
                "tokens": int(row.tokens or 0),
                "request_count": int(row.request_count or 0),
            }
            for row in result
        ]


async def query_model_cost_range(
    start: date, end: date, client_id: str | None = None
) -> list[dict[str, Any]]:
    """Per-model totals for [start, end] inclusive — used by the billing
    dashboard's breakdown-by-model widget. Grouped in SQL, mirrors
    query_daily_cost_range's shape but by model_id instead of day.
    """
    session_factory = get_session_factory()
    async with session_factory() as session:
        stmt = (
            select(
                UsageEvent.model_id,
                func.sum(UsageEvent.cost_usd).label("cost_usd"),
                func.sum(UsageEvent.prompt_tokens + UsageEvent.completion_tokens).label("tokens"),
                func.count().label("request_count"),
            )
            .where(UsageEvent.day >= start, UsageEvent.day <= end)
            .group_by(UsageEvent.model_id)
            .order_by(UsageEvent.model_id)
        )
        if client_id is not None:
            stmt = stmt.where(UsageEvent.client_id == client_id)
        result = await session.execute(stmt)
        return [
            {
                "model_id": row.model_id,
                "cost_usd": row.cost_usd,
                "tokens": int(row.tokens or 0),
                "request_count": int(row.request_count or 0),
            }
            for row in result
        ]


async def get_client_billing_settings(client_id: str) -> ClientBillingSettings | None:
    session_factory = get_session_factory()
    async with session_factory() as session:
        row: ClientBillingSettings | None = await session.get(ClientBillingSettings, client_id)
        return row


async def upsert_client_billing_settings(
    client_id: str,
    *,
    monthly_spend_cap_usd: float | None,
    alert_thresholds_percent: str | None,
    tax_rate_percent: float,
    preferred_currency: str,
) -> ClientBillingSettings:
    session_factory = get_session_factory()
    async with session_factory() as session:
        row: ClientBillingSettings | None = await session.get(ClientBillingSettings, client_id)
        if row is None:
            row = ClientBillingSettings(client_id=client_id)
            session.add(row)
        row.monthly_spend_cap_usd = monthly_spend_cap_usd
        row.alert_thresholds_percent = alert_thresholds_percent
        row.tax_rate_percent = tax_rate_percent
        row.preferred_currency = preferred_currency
        await session.commit()
        await session.refresh(row)
        return row


async def list_client_billing_settings_with_cap() -> list[ClientBillingSettings]:
    session_factory = get_session_factory()
    async with session_factory() as session:
        result = await session.execute(
            select(ClientBillingSettings).where(
                ClientBillingSettings.monthly_spend_cap_usd.is_not(None)
            )
        )
        return list(result.scalars().all())


async def list_currency_rates() -> list[CurrencyRate]:
    session_factory = get_session_factory()
    async with session_factory() as session:
        result = await session.execute(select(CurrencyRate))
        return list(result.scalars().all())


async def upsert_currency_rate(currency_code: str, units_per_usd: float) -> CurrencyRate:
    session_factory = get_session_factory()
    async with session_factory() as session:
        row: CurrencyRate | None = await session.get(CurrencyRate, currency_code)
        if row is None:
            row = CurrencyRate(currency_code=currency_code, units_per_usd=units_per_usd)
            session.add(row)
        else:
            row.units_per_usd = units_per_usd
        await session.commit()
        await session.refresh(row)
        return row


async def list_model_price_configs() -> list[ModelPriceConfig]:
    session_factory = get_session_factory()
    async with session_factory() as session:
        result = await session.execute(select(ModelPriceConfig))
        return list(result.scalars().all())


async def upsert_model_price_config(
    model_id: str,
    *,
    prompt_price_per_1m: float | None,
    completion_price_per_1m: float | None,
    image_price: float | None,
) -> ModelPriceConfig:
    session_factory = get_session_factory()
    async with session_factory() as session:
        row: ModelPriceConfig | None = await session.get(ModelPriceConfig, model_id)
        if row is None:
            row = ModelPriceConfig(model_id=model_id)
            session.add(row)
        row.prompt_price_per_1m = prompt_price_per_1m
        row.completion_price_per_1m = completion_price_per_1m
        row.image_price = image_price
        await session.commit()
        await session.refresh(row)
        return row


async def delete_model_price_config(model_id: str) -> bool:
    """Returns False if no row existed for model_id (nothing to delete)."""
    session_factory = get_session_factory()
    async with session_factory() as session:
        row: ModelPriceConfig | None = await session.get(ModelPriceConfig, model_id)
        if row is None:
            return False
        await session.delete(row)
        await session.commit()
        return True
