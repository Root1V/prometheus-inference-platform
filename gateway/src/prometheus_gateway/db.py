"""Persisted usage tracking — replaces the old Redis daily-TTL counters.

Implements: docs/roadmap.md — RM-32 (persisted history + per-model breakdown),
RM-60 (write-time cost, append-only audit trail, billing config).
Mirrors auth-service's db.py conventions (async SQLAlchemy, SQLite by default).
"""

from __future__ import annotations

import uuid
import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Float,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    case,
    func,
    inspect,
    or_,
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
    # PRM-113: display name at the time, alongside the stable id above.
    model_slug: Mapped[str | None] = mapped_column(String(128), nullable=True)
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


# RM-88: the three ways a request stops producing output. Strings rather than a
# Python enum because they are written to a column, read back by SQL and exported
# to CSV — a plain value crosses all three without a translation layer.
TERMINATION_COMPLETE = "complete"
TERMINATION_UPSTREAM_ERROR = "upstream_error"
TERMINATION_CLIENT_DISCONNECTED = "client_disconnected"

TERMINATION_REASONS = (
    TERMINATION_COMPLETE,
    TERMINATION_UPSTREAM_ERROR,
    TERMINATION_CLIENT_DISCONNECTED,
)


def _interrupted_from(reason: str) -> bool:
    """`interrupted` is whatever is not a whole answer.

    Derived rather than passed in, so the boolean and the reason can never
    disagree on a row — which is the failure mode of keeping both.
    """
    return reason != TERMINATION_COMPLETE


class ManagerCatalogSnapshot(Base):
    """The last catalog the manager successfully reported — RM-99.

    Read in exactly one situation: the gateway starts and cannot reach the
    manager. [[RM-98]] made a *running* gateway survive a manager outage by
    keeping its copy in memory, which leaves the case that matters most
    uncovered — a gateway restarting while the manager is down, and a deploy is
    precisely when someone is already touching the infrastructure.

    Never consulted again once a sync succeeds: the manager is the source of
    truth and this is only what to do when it cannot be asked.
    """

    __tablename__ = "manager_catalog_snapshot"

    # One row. The id exists because a primary key must, not because there is
    # ever more than one snapshot.
    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    written_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # {node_name: [entry, ...]} as the manager reported them.
    payload: Mapped[str] = mapped_column(Text, nullable=False)


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
    # RM-73: which replica actually served this request. The model is what gets
    # billed (RM-69); this is what makes a billing question answerable down to a
    # machine — "why was this one slow", "which node produced this output".
    # Nullable: rows written before RM-73 have no answer to give.
    instance_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # PRM-100: the id handed back to the caller in `x-request-id`. It was the
    # one thing linking a caller to its own row and we were not storing it, so
    # not even an administrator could go from a request identifier to what it
    # was charged. Nullable for rows written before this existed.
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # PRM-100: a subset of prompt_tokens, not a separate bucket — the OpenAI
    # convention, and what the response already reports as
    # prompt_tokens_details.cached_tokens. The row reported none of it, so a
    # caller reconciling its own figure against ours could not have matched
    # whenever the cache was involved.
    cached_prompt_tokens: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default=text("0")
    )
    # RM-88: why this request stopped producing output. Three reasons, because
    # there are three — collapsing them into a boolean threw away the one
    # distinction that matters when a charge is questioned: whether the answer
    # was cut short by us or by the caller.
    #
    #   "complete"           — the model finished; the answer is whole.
    #   "upstream_error"     — our stream broke mid-answer. Our problem.
    #   "client_disconnected"— the caller hung up mid-answer. A chat UI's stop
    #                          button lands here, so it is ordinary, not rare.
    #
    # We bill the tokens actually sent to the caller in every case. That is the
    # narrower of the two defensible measures — the other being everything the
    # GPU produced — and the one we can evidence line by line.
    termination_reason: Mapped[str] = mapped_column(
        String(24), nullable=False, default="complete", server_default=text("'complete'")
    )
    # RM-83, kept as a derived column rather than dropped: it is in the contract
    # we published to SDK clients, and it means what we told them it means —
    # "you were charged for an answer you did not receive whole". Never set
    # independently; see _interrupted_from().
    interrupted: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("0")
    )
    # PRM-113: the catalog id, which never changes. This used to be the slug,
    # which RM-70 lets an operator name once — so naming a model split its
    # billing history in two and lost its pricing.yaml entry at the same time.
    model_slug: Mapped[str | None] = mapped_column(String(128), nullable=True)
    request_kind: Mapped[str] = mapped_column(
        String(16), nullable=False
    )  # "chat" | "embedding" | "image" | "rerank" (PRM-106)
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


class IdempotencyRecord(Base):
    """One client-supplied idempotency key — docs/roadmap.md RM-78.

    In the gateway's own database rather than Redis on purpose. Redis here
    keeps only periodic snapshots, so a restart would drop keys and the retry
    that followed would regenerate and bill twice — precisely the failure this
    table exists to prevent. Volume is low because the key is opt-in.
    """

    __tablename__ = "idempotency_records"

    # Scoped per client: one client's key must never answer another's request.
    client_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    key: Mapped[str] = mapped_column(String(255), primary_key=True)
    # Hash of the request, so reusing a key with different parameters is
    # refused rather than answered with someone else's result.
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    # "in_progress" until the response exists. A duplicate arriving meanwhile
    # gets 409 — without the state, two concurrent retries generate twice and
    # defeat the point.
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # NULL when the response was too large to retain (see _MAX_STORED_BODY).
    # The key is still recorded, so a replay can say the original succeeded
    # instead of silently regenerating.
    response_body: Mapped[str | None] = mapped_column(Text, nullable=True)
    # PRM-100: which request produced the stored result. A replay gets its own
    # request id and records no usage, so without this the id its caller holds
    # leads nowhere; with it, the replay response can name the generation that
    # was actually billed.
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
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


class RateLimitConfig(Base):
    """Admin-configured rate limits — RM-56.

    Persists what used to require editing .env + restarting the gateway.
    Single row (`id` is always 1): its presence means "an operator has set
    limits from the dashboard", and those values replace the .env ones
    wholesale; deleting the row restores whatever .env said, which is why
    create_app snapshots the env values before anything can overwrite them.
    Applied to the live Settings object at startup and on every admin write,
    so a change takes effect on the very next request — the rate-limit
    middleware already re-reads Settings per request.
    """

    __tablename__ = "rate_limit_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    rate_limit_rpm: Mapped[int] = mapped_column(Integer, nullable=False)
    rate_limit_tpm: Mapped[int] = mapped_column(Integer, nullable=False)
    rate_limit_rpm_chat_completions: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rate_limit_tpm_chat_completions: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rate_limit_rpm_admin: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rate_limit_tpm_admin: Mapped[int | None] = mapped_column(Integer, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class CircuitBreakerConfig(Base):
    """Admin-configured circuit-breaker thresholds — RM-67.

    Same single-row shape as RateLimitConfig above, for the same reason:
    presence means "an operator tuned these from the dashboard", absence
    means the .env values stand. Unlike rate limits, applying these needs an
    explicit push into BackendPool — the pool and each live CircuitBreaker
    hold their own copies rather than re-reading Settings per request.
    """

    __tablename__ = "circuit_breaker_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    circuit_breaker_failure_threshold: Mapped[int] = mapped_column(Integer, nullable=False)
    circuit_breaker_recovery_timeout: Mapped[int] = mapped_column(Integer, nullable=False)
    circuit_breaker_success_threshold: Mapped[int] = mapped_column(Integer, nullable=False)
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
    """RM-60 stopgap. Kept only to lift a pre-Alembic database to the RM-68
    baseline; it is never used for schema changes made after that. Idempotent.
    """
    inspector = inspect(sync_conn)
    if "usage_daily" not in inspector.get_table_names():
        return
    existing_columns = {c["name"] for c in inspector.get_columns("usage_daily")}
    for column_name in _USAGE_DAILY_ADDED_COLUMNS:
        if column_name not in existing_columns:
            sync_conn.execute(text(f"ALTER TABLE usage_daily ADD COLUMN {column_name} FLOAT"))


# RM-68: the first Alembic revision, which describes the schema exactly as it
# shipped in v1.4.0 — i.e. what every database created before migrations
# existed already contains.
_BASELINE_REVISION = "4aad3b8652af"
_ALEMBIC_INI = Path(__file__).resolve().parents[2] / "alembic.ini"


def _migrate_to_head(sync_conn: Connection) -> None:
    """Bring one connection's database to the latest revision — RM-68.

    Three cases, and the middle one is why this isn't just `upgrade(head)`:

    * **New database** — no tables at all. Alembic runs every revision from
      scratch, baseline included.
    * **Pre-Alembic database** — has tables but no `alembic_version`. It is
      already at the baseline in everything but name, so running the baseline
      revision would fail on "table already exists". Instead it is lifted to
      exactly the baseline shape (`create_all` fills in any table added between
      its creation and v1.4.0; the RM-60 stopgap fills in the columns
      `create_all` can't), stamped, and then upgraded like any other.
    * **Already managed** — just upgraded to head.

    Without the middle case the first real migration would destroy or refuse to
    touch a database holding live usage and billing rows.
    """
    import logging

    # Alembic announces each autogenerate plugin it loads at INFO — seven lines
    # of noise in the gateway's startup log on every boot, saying nothing an
    # operator needs. Registration happens while `alembic` is imported, so this
    # has to come first to have any effect. Migration events stay visible.
    logging.getLogger("alembic.runtime.plugins").setLevel(logging.WARNING)

    from alembic import command
    from alembic.config import Config

    config = Config(str(_ALEMBIC_INI))
    # Hands env.py the transaction we are already inside, so the schema change
    # and the version bump commit together.
    config.attributes["connection"] = sync_conn

    tables = set(inspect(sync_conn).get_table_names())
    if tables and "alembic_version" not in tables:
        # create_all fills in tables a database older than the baseline never
        # had (one predating RM-60's billing tables, say). It builds them from
        # *today's* models, though, so such a table arrives already carrying
        # columns that post-baseline migrations then try to add — which is why
        # those migrations guard against the column already existing. The
        # alternative, refusing to adopt anything older than the baseline,
        # would strand exactly the databases migrations exist to rescue.
        Base.metadata.create_all(sync_conn)
        _ensure_usage_daily_cost_column(sync_conn)
        command.stamp(config, _BASELINE_REVISION)

    command.upgrade(config, "head")


async def create_tables(engine: AsyncEngine) -> None:
    """Apply pending migrations. Named for its callers, which predate RM-68."""
    async with engine.begin() as conn:
        await conn.run_sync(_migrate_to_head)


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
    model_slug: str,
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
        model_slug=model_slug,
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
            # PRM-113: the day's row keeps the newest name the model answered
            # to. The id it is keyed on has not changed, so this only ever
            # refreshes a label.
            "model_slug": excluded.model_slug,
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
    instance_id: str | None = None,
    termination_reason: str = TERMINATION_COMPLETE,
    request_id: str | None = None,
    cached_prompt_tokens: int = 0,
    # PRM-113: `model_id` is the catalog id and never changes; this is the name
    # it answered to when the row was written. Defaults to model_id so a caller
    # that has only one identifier still writes something truthful.
    model_slug: str | None = None,
    day: date | None = None,
) -> None:
    """Record one request's usage: an immutable `usage_events` row (the audit
    trail, priced at the rate in effect right now) plus an atomic upsert into
    `usage_daily`'s rollup counters. Implements: docs/roadmap.md — RM-60 (#1, #2).
    """
    d = day or datetime.now(tz=timezone.utc).date()
    price_table = pricing.get_pricing_table()
    # PRM-113: by either name. The row is keyed on the catalog id now, but an
    # operator's pricing.yaml may well be written against the slug — that
    # mismatch is exactly what made a named model bill nothing.
    price = price_table.get_price(model_id) or (
        price_table.get_price(model_slug) if model_slug else None
    )

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
        # PRM-118: by both names, like the `price` two branches up. This line
        # resolved by one and so could record NULL for a model the line above
        # had just found a price for — the same defect, one line apart, which
        # is why PRM-118 stopped repeating the rule at each call site and put
        # a test on it instead.
        cost_usd = price_table.estimate_cost_usd(
            model_id, prompt_tokens, completion_tokens, model_slug=model_slug
        )
        image_price_each = None
        image_cost_usd = None

    session_factory = get_session_factory()
    async with session_factory() as session:
        session.add(
            UsageEvent(
                day=d,
                client_id=client_id,
                model_id=model_id,
                model_slug=model_slug or model_id,
                instance_id=instance_id,
                termination_reason=termination_reason,
                interrupted=_interrupted_from(termination_reason),
                request_id=request_id,
                cached_prompt_tokens=cached_prompt_tokens,
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
                model_slug or model_id,
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
                # PRM-119: how many of those requests could not be priced. SUM
                # skips NULLs, so without this a period of entirely unpriced
                # usage is indistinguishable from one that genuinely cost
                # nothing — and the invoice then states a confident zero.
                func.sum(case((UsageEvent.cost_usd.is_(None), 1), else_=0)).label(
                    "unpriced_requests"
                ),
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
                "unpriced_requests": int(row.unpriced_requests or 0),
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
                # PRM-119: how many of those requests could not be priced. SUM
                # skips NULLs, so without this a period of entirely unpriced
                # usage is indistinguishable from one that genuinely cost
                # nothing — and the invoice then states a confident zero.
                func.sum(case((UsageEvent.cost_usd.is_(None), 1), else_=0)).label(
                    "unpriced_requests"
                ),
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
                "unpriced_requests": int(row.unpriced_requests or 0),
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


# ── RM-56: admin-configured rate limits (single row, id=1) ───────────────────

_RATE_LIMIT_CONFIG_ID = 1


async def get_rate_limit_config() -> RateLimitConfig | None:
    """None means no admin override — the .env values are in effect."""
    session_factory = get_session_factory()
    async with session_factory() as session:
        row: RateLimitConfig | None = await session.get(RateLimitConfig, _RATE_LIMIT_CONFIG_ID)
        return row


async def upsert_rate_limit_config(
    *,
    rate_limit_rpm: int,
    rate_limit_tpm: int,
    rate_limit_rpm_chat_completions: int | None,
    rate_limit_tpm_chat_completions: int | None,
    rate_limit_rpm_admin: int | None,
    rate_limit_tpm_admin: int | None,
) -> RateLimitConfig:
    session_factory = get_session_factory()
    async with session_factory() as session:
        row: RateLimitConfig | None = await session.get(RateLimitConfig, _RATE_LIMIT_CONFIG_ID)
        if row is None:
            row = RateLimitConfig(id=_RATE_LIMIT_CONFIG_ID)
            session.add(row)
        row.rate_limit_rpm = rate_limit_rpm
        row.rate_limit_tpm = rate_limit_tpm
        row.rate_limit_rpm_chat_completions = rate_limit_rpm_chat_completions
        row.rate_limit_tpm_chat_completions = rate_limit_tpm_chat_completions
        row.rate_limit_rpm_admin = rate_limit_rpm_admin
        row.rate_limit_tpm_admin = rate_limit_tpm_admin
        await session.commit()
        await session.refresh(row)
        return row


async def delete_rate_limit_config() -> bool:
    """Returns False if no override existed (already on the .env values)."""
    session_factory = get_session_factory()
    async with session_factory() as session:
        row: RateLimitConfig | None = await session.get(RateLimitConfig, _RATE_LIMIT_CONFIG_ID)
        if row is None:
            return False
        await session.delete(row)
        await session.commit()
        return True


# ── RM-67: admin-configured circuit-breaker thresholds (single row, id=1) ────

_CIRCUIT_BREAKER_CONFIG_ID = 1


async def get_circuit_breaker_config() -> CircuitBreakerConfig | None:
    """None means no admin override — the .env values are in effect."""
    session_factory = get_session_factory()
    async with session_factory() as session:
        row: CircuitBreakerConfig | None = await session.get(
            CircuitBreakerConfig, _CIRCUIT_BREAKER_CONFIG_ID
        )
        return row


async def upsert_circuit_breaker_config(
    *,
    circuit_breaker_failure_threshold: int,
    circuit_breaker_recovery_timeout: int,
    circuit_breaker_success_threshold: int,
) -> CircuitBreakerConfig:
    session_factory = get_session_factory()
    async with session_factory() as session:
        row: CircuitBreakerConfig | None = await session.get(
            CircuitBreakerConfig, _CIRCUIT_BREAKER_CONFIG_ID
        )
        if row is None:
            row = CircuitBreakerConfig(id=_CIRCUIT_BREAKER_CONFIG_ID)
            session.add(row)
        row.circuit_breaker_failure_threshold = circuit_breaker_failure_threshold
        row.circuit_breaker_recovery_timeout = circuit_breaker_recovery_timeout
        row.circuit_breaker_success_threshold = circuit_breaker_success_threshold
        await session.commit()
        await session.refresh(row)
        return row


async def delete_circuit_breaker_config() -> bool:
    """Returns False if no override existed (already on the .env values)."""
    session_factory = get_session_factory()
    async with session_factory() as session:
        row: CircuitBreakerConfig | None = await session.get(
            CircuitBreakerConfig, _CIRCUIT_BREAKER_CONFIG_ID
        )
        if row is None:
            return False
        await session.delete(row)
        await session.commit()
        return True


# ── RM-99: the catalog snapshot, for a start with no manager ─────────────────


async def save_catalog_snapshot(payload: dict[str, Any]) -> None:
    """Store the catalog the manager just reported.

    Written on change rather than on every cycle: with a blocking query a sync
    runs whenever the wait expires, and rewriting an identical snapshot a
    thousand times a day would be churn for nothing.
    """
    serialised = json.dumps(payload, sort_keys=True, default=str)
    async with get_session_factory()() as session:
        row = await session.get(ManagerCatalogSnapshot, 1)
        if row is not None and row.payload == serialised:
            return
        if row is None:
            session.add(
                ManagerCatalogSnapshot(
                    id=1, written_at=datetime.now(timezone.utc), payload=serialised
                )
            )
        else:
            row.payload = serialised
            row.written_at = datetime.now(timezone.utc)
        await session.commit()


async def load_catalog_snapshot() -> tuple[dict[str, Any], datetime] | None:
    """The stored catalog and when it was stored, or None if there is none.

    The age comes back with it deliberately. Falling back to a snapshot is worth
    doing and worth saying out loud, and how old it is decides whether an
    operator should trust it or go fix the manager.
    """
    async with get_session_factory()() as session:
        row = await session.get(ManagerCatalogSnapshot, 1)
        if row is None:
            return None
        written = row.written_at
        if written.tzinfo is None:
            written = written.replace(tzinfo=timezone.utc)
        try:
            return json.loads(row.payload), written
        except (TypeError, ValueError):
            return None


async def get_usage_event_for_client(client_id: str, request_id: str) -> UsageEvent | None:
    """One caller's own usage row — PRM-100.

    Filtered by `client_id` inside the query rather than checked afterwards, so
    there is no path where a row belonging to somebody else is read and then
    discarded. The caller sees `None` both for a request that was never made and
    for one that belongs to another client: the endpoint answers 404 either way,
    on Axonium's suggestion, so a probe cannot confirm that an id exists.
    """
    async with get_session_factory()() as session:
        result = await session.execute(
            select(UsageEvent).where(
                UsageEvent.client_id == client_id,
                UsageEvent.request_id == request_id,
            )
        )
        row: UsageEvent | None = result.scalars().first()
        return row


async def count_usage_rows_for_model(model_id: str, model_slug: str | None = None) -> int:
    """How many usage rows already reference this model, under either name.

    PRM-113: one of the three things that make a slug un-renameable. Rows are
    keyed on the catalog id now, but anything written before that carries the
    slug there instead, so both are counted.
    """
    names = [n for n in (model_id, model_slug) if n]
    if not names:
        return 0
    async with get_session_factory()() as session:
        result = await session.execute(
            select(func.count())
            .select_from(UsageEvent)
            .where(or_(UsageEvent.model_id.in_(names), UsageEvent.model_slug.in_(names)))
        )
        return int(result.scalar() or 0)
