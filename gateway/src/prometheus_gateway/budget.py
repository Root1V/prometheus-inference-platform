"""Per-client monthly $ spend cap (hard) + alert thresholds (soft) — RM-60.

Mirrors rate_limiter.py's atomic pipelined-INCR shape (never a separate
read-then-later-write, unlike check_tpm_budget/increment_tpm's known race).
Because completion-token cost isn't known until generation finishes, callers
reserve a conservative worst-case estimate before forwarding a request, then
settle with the real cost once the response completes.

Implements: docs/roadmap.md — RM-60 (#6, #7).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from . import db
from .telemetry import get_logger

logger = get_logger(__name__)

_SPEND_KEY = "prometheus:budget:spend:{client_id}:{period}"
_NOTIFIED_KEY = "prometheus:budget:notified:{client_id}:{period}"
_KEY_TTL_SECONDS = 40 * 24 * 3600  # ~40 days, safely covers a full calendar month

_SETTINGS_CACHE_TTL_S = 30.0
_settings_cache: dict[str, tuple[float, "db.ClientBillingSettings | None"]] = {}


def _current_period() -> str:
    return datetime.now(tz=timezone.utc).strftime("%Y-%m")


def _to_micros(usd: float) -> int:
    return int(round(usd * 1_000_000))


def _to_usd(micros: int) -> float:
    return micros / 1_000_000


def parse_thresholds(raw: str | None, default_raw: str) -> list[int]:
    """Parse a "50,80,100"-style string into sorted unique ints. Falls back to
    the platform default when a per-client override isn't set.
    """
    source = raw if raw else default_raw
    thresholds: list[int] = []
    for part in source.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            thresholds.append(int(float(part)))
        except ValueError:
            continue
    return sorted(set(thresholds))


_tier_cache: dict[str, tuple[float, "db.RateLimitTier | None"]] = {}


async def get_rate_limit_tier_cached(name: str) -> "db.RateLimitTier | None":
    """A tier's ceilings, cached like the spend cap beside it — PRM-228.

    Same 30-second window and the same reason: this is read on the hot
    inference path, and a tier change taking up to half a minute to apply is a
    better trade than a database round trip per request. Same failure mode
    too — a read error is "no tier", never a reason to refuse inference.
    """
    now = time.monotonic()
    cached = _tier_cache.get(name)
    if cached and now - cached[0] < _SETTINGS_CACHE_TTL_S:
        return cached[1]
    try:
        tier = await db.get_rate_limit_tier(name)
    except Exception as exc:
        logger.warning("rate_limit_tier.read_error", tier=name, error=str(exc))
        return None
    _tier_cache[name] = (now, tier)
    return tier


async def resolve_client_limits(client_id: str) -> "db.RateLimitTier | None":
    """The tier a client is on, or None for the platform defaults — PRM-228.

    Two cached reads at worst and usually zero: the settings row is already
    fetched on this path for the spend cap, and a client with no tier costs
    nothing beyond what RM-60 already pays.
    """
    settings = await get_client_billing_settings_cached(client_id)
    if settings is None or settings.tier is None:
        return None
    return await get_rate_limit_tier_cached(settings.tier)


async def get_client_billing_settings_cached(client_id: str) -> "db.ClientBillingSettings | None":
    """A cap change via the admin UI takes up to _SETTINGS_CACHE_TTL_S to apply
    on the hot path — a deliberate latency/freshness tradeoff, not real-time.

    Fails open: a billing-settings read error is treated the same as "no
    settings row" (no cap enforced), never as a reason to fail the inference
    request — same "never block inference on a secondary DB write/read"
    philosophy as usage recording (db.record_usage's caller swallows errors).
    Not cached, so the next request retries rather than being stuck on None.
    """
    now = time.monotonic()
    cached = _settings_cache.get(client_id)
    if cached and now - cached[0] < _SETTINGS_CACHE_TTL_S:
        return cached[1]
    try:
        settings = await db.get_client_billing_settings(client_id)
    except Exception as exc:
        logger.warning("billing.settings_read_error", client_id=client_id, error=str(exc))
        return None
    _settings_cache[client_id] = (now, settings)
    return settings


def invalidate_client_billing_settings_cache(client_id: str) -> None:
    """Called by the admin billing-settings PUT endpoint so a saved change is
    visible immediately instead of waiting out the cache TTL.
    """
    _settings_cache.pop(client_id, None)


def invalidate_rate_limit_tier_cache(name: str | None = None) -> None:
    """Same contract as the one above, for tiers — PRM-228.

    Clears everything when no name is given, because editing a tier changes
    the limits of every client on it and there is no index from tier back to
    client here. The cache holds at most a handful of rows, so the blunt
    version costs one re-read each rather than a lookup table to maintain.
    """
    if name is None:
        _tier_cache.clear()
    else:
        _tier_cache.pop(name, None)


def _reset_cache_for_testing() -> None:
    """Reset module-level cache state. For use in test fixtures only —
    mirrors auth/jwks.py's _reset_cache_for_testing().

    PRM-231: the tier cache too. PRM-228 added it and left this function
    clearing only one of the two, so a tier name reused across tests would be
    served from an earlier test's database — the exact leak the docstring
    above describes, reintroduced one cache later.
    """
    _settings_cache.clear()
    _tier_cache.clear()


@dataclass
class BudgetReservation:
    allowed: bool
    reserved_usd: float
    total_spend_usd: float
    cap_usd: float | None
    period: str
    crossed_thresholds: list[int] = field(default_factory=list)


class BudgetTracker:
    """Atomic, Redis-backed per-client monthly spend tracker."""

    def __init__(self, redis_client: Any) -> None:
        self._redis = redis_client

    async def reserve(
        self,
        client_id: str,
        estimated_cost_usd: float,
        *,
        cap_usd: float | None,
        alert_thresholds_percent: list[int],
    ) -> BudgetReservation:
        period = _current_period()
        key = _SPEND_KEY.format(client_id=client_id, period=period)
        delta = _to_micros(estimated_cost_usd)

        pipe = self._redis.pipeline()
        pipe.incrby(key, delta)
        pipe.ttl(key)
        total_micros, ttl = await pipe.execute()
        if ttl < 0:
            await self._redis.expire(key, _KEY_TTL_SECONDS)

        total_spend_usd = _to_usd(total_micros)
        if cap_usd is not None and total_spend_usd > cap_usd:
            # Roll back — the request never proceeds, so it must not count.
            await self._redis.incrby(key, -delta)
            return BudgetReservation(
                allowed=False,
                reserved_usd=estimated_cost_usd,
                total_spend_usd=total_spend_usd - estimated_cost_usd,
                cap_usd=cap_usd,
                period=period,
                crossed_thresholds=[],
            )

        crossed = await self._check_thresholds(
            client_id, period, total_spend_usd, cap_usd, alert_thresholds_percent
        )
        return BudgetReservation(
            allowed=True,
            reserved_usd=estimated_cost_usd,
            total_spend_usd=total_spend_usd,
            cap_usd=cap_usd,
            period=period,
            crossed_thresholds=crossed,
        )

    async def settle(
        self,
        client_id: str,
        reserved_usd: float,
        actual_cost_usd: float,
        *,
        cap_usd: float | None,
        alert_thresholds_percent: list[int],
    ) -> list[int]:
        period = _current_period()
        key = _SPEND_KEY.format(client_id=client_id, period=period)
        delta = _to_micros(actual_cost_usd) - _to_micros(reserved_usd)
        if delta != 0:
            pipe = self._redis.pipeline()
            pipe.incrby(key, delta)
            pipe.ttl(key)
            total_micros, ttl = await pipe.execute()
            if ttl < 0:
                await self._redis.expire(key, _KEY_TTL_SECONDS)
        else:
            raw = await self._redis.get(key)
            total_micros = int(raw) if raw else 0
        return await self._check_thresholds(
            client_id, period, _to_usd(total_micros), cap_usd, alert_thresholds_percent
        )

    async def get_spend(self, client_id: str, period: str | None = None) -> float:
        period = period or _current_period()
        raw = await self._redis.get(_SPEND_KEY.format(client_id=client_id, period=period))
        return _to_usd(int(raw)) if raw else 0.0

    async def get_notified_thresholds(self, client_id: str, period: str | None = None) -> list[int]:
        period = period or _current_period()
        members = await self._redis.smembers(
            _NOTIFIED_KEY.format(client_id=client_id, period=period)
        )
        return sorted(int(m) for m in members)

    async def _check_thresholds(
        self,
        client_id: str,
        period: str,
        total_spend_usd: float,
        cap_usd: float | None,
        thresholds_percent: list[int],
    ) -> list[int]:
        if cap_usd is None or cap_usd <= 0:
            return []
        notified_key = _NOTIFIED_KEY.format(client_id=client_id, period=period)
        percent = (total_spend_usd / cap_usd) * 100
        newly_crossed: list[int] = []
        for threshold in sorted(thresholds_percent):
            if percent >= threshold:
                if await self._redis.sadd(notified_key, str(threshold)):
                    await self._redis.expire(notified_key, _KEY_TTL_SECONDS)
                    newly_crossed.append(threshold)
        return newly_crossed
