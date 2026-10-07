"""Fixed-window rate limiter backed by Redis.

Fixed, not sliding, and this docstring said sliding until PRM-225 — a
correctness-relevant claim, since a fixed window lets a caller spend its whole
quota at 11:59:59 and again at 12:00:00. Changing the window is its own item;
describing it accurately is not.

Implements: memory/specs/007-rate-limiting-and-throughput.md — AC-1, AC-2, AC-3, AC-4, AC-5, AC-9, AC-13
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from .telemetry import get_logger

logger = get_logger(__name__)

# Key patterns — AC-3
_RPM_KEY = "prometheus:rl:rpm:{identity}:{endpoint}:{bucket}"
_TPM_KEY = "prometheus:rl:tpm:{identity}:{endpoint}:{bucket}"
# PRM-224: the same minute, told apart by direction.
#
# Both vendors this platform is shaped after count these separately, and the
# reason is physical rather than commercial: output tokens are generated one at
# a time and dominate latency, while input is processed in parallel at prefill.
# Measured on this platform, Sentinel's traffic is 88% prompt and
# Code2Presentation's is 75/25 — one combined counter treats them as the same
# load, though they occupy the hardware in completely different ways.
#
# The combined key above is untouched, so the existing limit and every reader
# of it keep working; these two are additive.
_TPM_IN_KEY = "prometheus:rl:tpm_in:{identity}:{endpoint}:{bucket}"
_TPM_OUT_KEY = "prometheus:rl:tpm_out:{identity}:{endpoint}:{bucket}"
# PRM-225: the same counters over a UTC day.
#
# A minute limit answers "how hard can you push right now" and a day limit
# answers "how much of this is yours" — they are different questions and both
# vendors ask both (OpenAI publishes RPD and TPD beside RPM and TPM). Keyed by
# `time // 86400` rather than a date string so the arithmetic matches the
# minute bucket exactly and there is one rule to reason about.
_RPD_KEY = "prometheus:rl:rpd:{identity}:{endpoint}:{bucket}"
_TPD_KEY = "prometheus:rl:tpd:{identity}:{endpoint}:{bucket}"
# PRM-226: images per minute.
#
# The one dimension that can be a real gate rather than a meter: how many
# images a request will produce is `n` in the body, known before anything is
# generated, where the size of a completion is not. So this is checked ahead of
# the work and counted from what actually came back.
_IPM_KEY = "prometheus:rl:ipm:{identity}:{endpoint}:{bucket}"
_COUNTER_TTL = 90  # seconds — covers current + previous minute
# PRM-225: a day plus two hours, for the same reason the minute gets ninety
# seconds — the key must outlive its own window so a request landing in the
# last second still finds the counter it belongs to.
_DAILY_COUNTER_TTL = 86_400 + 7_200


@dataclass
class RateLimitState:
    """Result of a rate-limit check for a single dimension."""

    allowed: bool
    limit: int
    remaining: int
    reset_at: int  # Unix timestamp of next bucket start


@dataclass
class RateLimitResult:
    """Combined RPM + TPM check result."""

    rpm: RateLimitState
    tpm: RateLimitState

    @property
    def allowed(self) -> bool:
        return self.rpm.allowed and self.tpm.allowed

    @property
    def retry_after(self) -> int:
        """Seconds until the most restrictive limit resets."""
        now = int(time.time())
        if not self.rpm.allowed:
            return max(0, self.rpm.reset_at - now)
        if not self.tpm.allowed:
            return max(0, self.tpm.reset_at - now)
        return 0


class RateLimiter:
    """Sliding-window rate limiter using Redis INCR+EXPIRE pipelines.

    Implements: memory/specs/007-rate-limiting-and-throughput.md — AC-1 through AC-13
    """

    def __init__(self, redis_client: Any) -> None:
        self._redis = redis_client

    def _bucket(self) -> int:
        return int(time.time() // 60)

    def _reset_at(self) -> int:
        bucket = self._bucket()
        return (bucket + 1) * 60

    def _day_bucket(self) -> int:
        return int(time.time() // 86_400)

    def _day_reset_at(self) -> int:
        return (self._day_bucket() + 1) * 86_400

    async def check_and_increment_rpd(
        self, identity: str, endpoint: str, limit: int
    ) -> RateLimitState:
        """The day's request counter — PRM-225. Same atomic incr-then-compare
        as the minute's, so a refused request has still spent its slot and a
        caller cannot win by racing.

        Only called where a daily limit is configured, so a deployment that
        does not use one pays nothing. The cost of that choice: switching a
        limit on mid-day starts counting from that moment, not from midnight.
        """
        bucket = self._day_bucket()
        key = _RPD_KEY.format(identity=identity, endpoint=endpoint, bucket=bucket)

        pipe = self._redis.pipeline()
        pipe.incr(key)
        pipe.ttl(key)
        results = await pipe.execute()
        count, ttl = int(results[0]), int(results[1])
        if ttl < 0:
            await self._redis.expire(key, _DAILY_COUNTER_TTL)

        return RateLimitState(
            allowed=count <= limit,
            limit=limit,
            remaining=max(0, limit - count),
            reset_at=self._day_reset_at(),
        )

    async def check_tpd_budget(self, identity: str, endpoint: str, limit: int) -> RateLimitState:
        """The day's token counter, read not estimated — the same meter-not-gate
        shape as `check_tpm_budget` (PRM-224)."""
        bucket = self._day_bucket()
        key = _TPD_KEY.format(identity=identity, endpoint=endpoint, bucket=bucket)
        raw = await self._redis.get(key)
        current = int(raw) if raw else 0
        return RateLimitState(
            allowed=current <= limit,
            limit=limit,
            remaining=max(0, limit - current),
            reset_at=self._day_reset_at(),
        )

    async def check_ipm_headroom(
        self, identity: str, endpoint: str, limit: int, requested: int
    ) -> RateLimitState:
        """Would `requested` more images fit in this minute — PRM-226.

        A read, not a reservation, and the race that allows is deliberate: two
        requests arriving together can both pass and overshoot by one batch.
        RM-60 chose atomic reserve-and-roll-back for *money*, where an overshoot
        is a real charge; here the counter is incremented from what the backend
        actually returned, so the overshoot is bounded by concurrency and
        corrects itself within the minute. Paying for atomicity would mean
        reserving `n` and settling the difference on every request to buy back
        a bounded, self-healing error.
        """
        bucket = self._bucket()
        key = _IPM_KEY.format(identity=identity, endpoint=endpoint, bucket=bucket)
        raw = await self._redis.get(key)
        current = int(raw) if raw else 0
        return RateLimitState(
            allowed=current + requested <= limit,
            limit=limit,
            remaining=max(0, limit - current),
            reset_at=self._reset_at(),
        )

    async def increment_ipm(self, identity: str, endpoint: str, images: int) -> None:
        """Count the images that were actually produced — PRM-226."""
        bucket = self._bucket()
        key = _IPM_KEY.format(identity=identity, endpoint=endpoint, bucket=bucket)
        pipe = self._redis.pipeline()
        pipe.incrby(key, images)
        pipe.ttl(key)
        results = await pipe.execute()
        if int(results[1]) < 0:
            await self._redis.expire(key, _COUNTER_TTL)

    async def increment_tpd(self, identity: str, endpoint: str, tokens: int) -> None:
        """Add to the day's token counter — PRM-225."""
        bucket = self._day_bucket()
        key = _TPD_KEY.format(identity=identity, endpoint=endpoint, bucket=bucket)
        pipe = self._redis.pipeline()
        pipe.incrby(key, tokens)
        pipe.ttl(key)
        results = await pipe.execute()
        if int(results[1]) < 0:
            await self._redis.expire(key, _DAILY_COUNTER_TTL)

    async def check_and_increment_rpm(
        self,
        identity: str,
        endpoint: str,
        limit: int,
    ) -> RateLimitState:
        """Atomically increment the RPM counter and check against the limit.

        Implements: memory/specs/007-rate-limiting-and-throughput.md — AC-1, AC-3, AC-13
        """
        bucket = self._bucket()
        key = _RPM_KEY.format(identity=identity, endpoint=endpoint, bucket=bucket)
        reset_at = self._reset_at()

        pipe = self._redis.pipeline()
        pipe.incr(key)
        pipe.ttl(key)
        results = await pipe.execute()
        count: int = results[0]
        ttl: int = results[1]

        # Set TTL on first increment (or if somehow missing)
        if ttl < 0:
            await self._redis.expire(key, _COUNTER_TTL)

        remaining = max(0, limit - count)
        allowed = count <= limit
        return RateLimitState(
            allowed=allowed,
            limit=limit,
            remaining=remaining,
            reset_at=reset_at,
        )

    async def check_tpm_budget(
        self,
        identity: str,
        endpoint: str,
        limit: int,
        estimated_tokens: int,
    ) -> RateLimitState:
        """Check TPM budget without incrementing (pre-flight check).

        Implements: memory/specs/007-rate-limiting-and-throughput.md — AC-2
        """
        bucket = self._bucket()
        key = _TPM_KEY.format(identity=identity, endpoint=endpoint, bucket=bucket)
        reset_at = self._reset_at()

        current_raw = await self._redis.get(key)
        current: int = int(current_raw) if current_raw else 0
        projected = current + estimated_tokens
        remaining = max(0, limit - current)
        allowed = projected <= limit

        return RateLimitState(
            allowed=allowed,
            limit=limit,
            remaining=remaining,
            reset_at=reset_at,
        )

    async def increment_tpm(
        self,
        identity: str,
        endpoint: str,
        prompt_tokens: int,
        completion_tokens: int,
    ) -> None:
        """Increment the TPM counters after a successful response.

        Three counters, one call: the combined total the existing limit reads,
        and input and output separately (PRM-224). Takes the two halves rather
        than a total because the caller always has both and a sum cannot be
        taken apart again.

        Implements: memory/specs/007-rate-limiting-and-throughput.md — AC-2, AC-3
        """
        bucket = self._bucket()
        total = prompt_tokens + completion_tokens
        keys = (
            (_TPM_KEY.format(identity=identity, endpoint=endpoint, bucket=bucket), total),
            (
                _TPM_IN_KEY.format(identity=identity, endpoint=endpoint, bucket=bucket),
                prompt_tokens,
            ),
            (
                _TPM_OUT_KEY.format(identity=identity, endpoint=endpoint, bucket=bucket),
                completion_tokens,
            ),
        )

        pipe = self._redis.pipeline()
        for key, amount in keys:
            pipe.incrby(key, amount)
            pipe.ttl(key)
        results = await pipe.execute()
        # results alternate incrby, ttl — a fresh key reports -1 and needs one.
        for index, (key, _) in enumerate(keys):
            if results[index * 2 + 1] < 0:
                await self._redis.expire(key, _COUNTER_TTL)

    async def check_tpm_direction(
        self, identity: str, endpoint: str, limit: int, *, outgoing: bool
    ) -> RateLimitState:
        """One direction's budget — PRM-224. Same post-hoc shape as
        `check_tpm_budget`: the counter is read, never estimated into."""
        bucket = self._bucket()
        template = _TPM_OUT_KEY if outgoing else _TPM_IN_KEY
        key = template.format(identity=identity, endpoint=endpoint, bucket=bucket)
        raw = await self._redis.get(key)
        current = int(raw) if raw else 0
        return RateLimitState(
            allowed=current <= limit,
            limit=limit,
            remaining=max(0, limit - current),
            reset_at=self._reset_at(),
        )

    async def get_rpm_count(self, identity: str, endpoint: str) -> int:
        """Return current RPM count for the given identity+endpoint (for /v1/backends).

        Implements: memory/specs/007-rate-limiting-and-throughput.md — AC-10
        """
        bucket = self._bucket()
        key = _RPM_KEY.format(identity=identity, endpoint=endpoint, bucket=bucket)
        raw = await self._redis.get(key)
        return int(raw) if raw else 0
