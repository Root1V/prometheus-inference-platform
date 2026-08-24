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
        tokens: int,
    ) -> None:
        """Increment the TPM counter after a successful response.

        Implements: memory/specs/007-rate-limiting-and-throughput.md — AC-2, AC-3
        """
        bucket = self._bucket()
        key = _TPM_KEY.format(identity=identity, endpoint=endpoint, bucket=bucket)

        pipe = self._redis.pipeline()
        pipe.incrby(key, tokens)
        pipe.ttl(key)
        results = await pipe.execute()
        if results[1] < 0:
            await self._redis.expire(key, _COUNTER_TTL)

    async def get_rpm_count(self, identity: str, endpoint: str) -> int:
        """Return current RPM count for the given identity+endpoint (for /v1/backends).

        Implements: memory/specs/007-rate-limiting-and-throughput.md — AC-10
        """
        bucket = self._bucket()
        key = _RPM_KEY.format(identity=identity, endpoint=endpoint, bucket=bucket)
        raw = await self._redis.get(key)
        return int(raw) if raw else 0
