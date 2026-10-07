"""Sliding-window rate limiter backed by Redis.

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
_COUNTER_TTL = 90  # seconds — covers current + previous minute


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
