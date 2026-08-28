"""Persisted usage tracking — replaces the old Redis daily-TTL counters.

Implements: docs/roadmap.md — RM-32 (persisted history + per-model breakdown).
Mirrors auth-service's db.py conventions (async SQLAlchemy, SQLite by default).
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import date, datetime, timezone

from sqlalchemy import BigInteger, Date, Index, String, UniqueConstraint, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker


class Base(DeclarativeBase):
    pass


class UsageDaily(Base):
    """One row per (day, client, model) — token/request counters, incremented in place.

    Aggregate-only by design (not a per-request audit log): daily granularity is
    what a usage/spend dashboard needs, and keeps the table small regardless of
    request volume.
    """

    __tablename__ = "usage_daily"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    day: Mapped[date] = mapped_column(Date, nullable=False)
    client_id: Mapped[str] = mapped_column(String(64), nullable=False)
    model_id: Mapped[str] = mapped_column(String(128), nullable=False)
    prompt_tokens: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    completion_tokens: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    request_count: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)

    __table_args__ = (
        UniqueConstraint("day", "client_id", "model_id", name="uq_usage_daily_day_client_model"),
        Index("ix_usage_daily_day", "day"),
    )


_engine: AsyncEngine | None = None
_session_factory: sessionmaker | None = None  # type: ignore[type-arg]

# Guards the read-modify-write increment below. A single process only needs this
# to be correct for concurrent requests within that process — matches the
# asyncio.Lock MetricsStore already uses for its own in-memory counters; this
# assumes the gateway runs as a single worker process, same as today.
_write_lock = asyncio.Lock()


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


async def create_tables(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def record_usage(
    client_id: str,
    model_id: str,
    prompt_tokens: int,
    completion_tokens: int,
    *,
    day: date | None = None,
) -> None:
    """Increment today's (or `day`'s) counters for this client+model."""
    d = day or datetime.now(tz=timezone.utc).date()
    session_factory = get_session_factory()
    async with _write_lock, session_factory() as session:
        result = await session.execute(
            select(UsageDaily).where(
                UsageDaily.day == d,
                UsageDaily.client_id == client_id,
                UsageDaily.model_id == model_id,
            )
        )
        row = result.scalar_one_or_none()
        if row is None:
            session.add(
                UsageDaily(
                    day=d,
                    client_id=client_id,
                    model_id=model_id,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    request_count=1,
                )
            )
        else:
            row.prompt_tokens += prompt_tokens
            row.completion_tokens += completion_tokens
            row.request_count += 1
        await session.commit()


async def query_usage_day(day: date) -> list[UsageDaily]:
    """Return every (client, model) row recorded for `day`."""
    session_factory = get_session_factory()
    async with session_factory() as session:
        result = await session.execute(select(UsageDaily).where(UsageDaily.day == day))
        return list(result.scalars().all())
