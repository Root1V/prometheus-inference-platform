"""Per-backend circuit breaker with Redis-persisted state.

Implements: memory/specs/007-rate-limiting-and-throughput.md — AC-14, AC-15, AC-16, AC-18, AC-20
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from .telemetry import get_logger

logger = get_logger(__name__)

# Redis key patterns — AC-16
_CB_STATE_KEY = "prometheus:cb:{backend_id}:state"
_CB_FAILURES_KEY = "prometheus:cb:{backend_id}:failures"
_CB_OPENED_AT_KEY = "prometheus:cb:{backend_id}:opened_at"
_CB_PROBE_LOCK_KEY = "prometheus:cb:{backend_id}:probe_lock"

# States
_STATE_OPEN = "open"
_STATE_HALF_OPEN = "half-open"
# CLOSED is the absence of a state key

# In-process L1 cache TTL (AC-16 design decision: 2 s to avoid per-request Redis hit)
_L1_TTL = 2.0


@dataclass
class CircuitState:
    """Snapshot of circuit breaker state for a single backend."""

    state: str  # "closed" | "open" | "half-open"
    consecutive_failures: int
    opened_at: float | None  # Unix timestamp
    recovery_at: float | None  # Unix timestamp when probe is allowed

    @property
    def is_closed(self) -> bool:
        return self.state == "closed"

    @property
    def is_open(self) -> bool:
        return self.state == "open"

    @property
    def is_half_open(self) -> bool:
        return self.state == "half-open"


@dataclass
class _L1Entry:
    state: CircuitState
    cached_at: float = field(default_factory=time.monotonic)


class CircuitBreaker:
    """Three-state circuit breaker (CLOSED / OPEN / HALF-OPEN) per backend.

    State is persisted in Redis (AC-16) and cached in-process for _L1_TTL seconds.

    Implements: memory/specs/007-rate-limiting-and-throughput.md — AC-14, AC-15, AC-16, AC-18, AC-20
    """

    def __init__(
        self,
        backend_id: str,
        redis_client: Any,
        failure_threshold: int = 5,
        recovery_timeout: int = 30,
        success_threshold: int = 2,
    ) -> None:
        self.backend_id = backend_id
        self._redis = redis_client
        self._failure_threshold = failure_threshold
        self._recovery_timeout = recovery_timeout
        self._success_threshold = success_threshold
        self._l1: _L1Entry | None = None
        # Tracks successes in HALF-OPEN before closing
        self._half_open_successes: int = 0

    # ── Redis key helpers ──────────────────────────────────────────────────

    def _state_key(self) -> str:
        return _CB_STATE_KEY.format(backend_id=self.backend_id)

    def _failures_key(self) -> str:
        return _CB_FAILURES_KEY.format(backend_id=self.backend_id)

    def _opened_at_key(self) -> str:
        return _CB_OPENED_AT_KEY.format(backend_id=self.backend_id)

    def _probe_lock_key(self) -> str:
        return _CB_PROBE_LOCK_KEY.format(backend_id=self.backend_id)

    # ── State reading ──────────────────────────────────────────────────────

    async def get_state(self) -> CircuitState:
        """Return current circuit state — L1 cache first, then Redis.

        Implements: memory/specs/007-rate-limiting-and-throughput.md — AC-16, AC-18
        """
        now = time.monotonic()
        if self._l1 and (now - self._l1.cached_at) < _L1_TTL:
            return self._l1.state

        try:
            state_val, failures_val, opened_at_val = await self._redis.mget(
                self._state_key(),
                self._failures_key(),
                self._opened_at_key(),
            )
        except Exception as exc:
            logger.warning(
                "circuit_breaker.redis_read_error",
                extra={"backend_id": self.backend_id, "error": str(exc)},
            )
            # Fail-open: if Redis is unavailable, treat circuit as closed
            closed = CircuitState(
                state="closed", consecutive_failures=0, opened_at=None, recovery_at=None
            )
            return closed

        state = state_val.decode() if state_val else "closed"
        failures = int(failures_val) if failures_val else 0
        opened_at = float(opened_at_val) if opened_at_val else None
        recovery_at = (opened_at + self._recovery_timeout) if opened_at else None

        circuit_state = CircuitState(
            state=state,
            consecutive_failures=failures,
            opened_at=opened_at,
            recovery_at=recovery_at,
        )
        self._l1 = _L1Entry(state=circuit_state)
        return circuit_state

    def _invalidate_l1(self) -> None:
        self._l1 = None

    # ── Transition helpers ─────────────────────────────────────────────────

    async def allow_request(self) -> bool:
        """Return True if a request should be forwarded to the backend.

        If the circuit is OPEN and the recovery timeout has passed, transitions
        to HALF-OPEN and allows exactly one probe through (using Redis SETNX).

        Implements: memory/specs/007-rate-limiting-and-throughput.md — AC-14, AC-15
        """
        state = await self.get_state()

        if state.is_closed:
            return True

        if state.is_open:
            now = time.time()
            if state.recovery_at and now >= state.recovery_at:
                # Transition to HALF-OPEN — use SETNX as a distributed semaphore
                # so only one worker probes concurrently (AC-14b / security note)
                try:
                    acquired = await self._redis.set(
                        self._probe_lock_key(),
                        "1",
                        nx=True,
                        ex=self._recovery_timeout,
                    )
                except Exception:
                    return False

                if acquired:
                    try:
                        await self._redis.set(self._state_key(), _STATE_HALF_OPEN)
                    except Exception:
                        pass
                    self._l1 = None
                    self._half_open_successes = 0
                    logger.info("circuit_breaker.half_open", extra={"backend_id": self.backend_id})
                    return True
                # Another worker is probing — fast-fail this one
                return False
            # Recovery timeout not yet reached
            return False

        if state.is_half_open:
            # Only one probe at a time; check if we hold the probe lock
            try:
                lock_val = await self._redis.get(self._probe_lock_key())
            except Exception:
                return False
            return lock_val is not None

        return True  # defensive default

    async def record_success(self) -> None:
        """Record a successful backend response.

        In HALF-OPEN: counts successes toward success_threshold; closes circuit when reached.
        In CLOSED: no-op (failure counter is already 0).

        Implements: memory/specs/007-rate-limiting-and-throughput.md — AC-14c
        """
        try:
            state = await self.get_state()
            if state.is_half_open:
                self._half_open_successes += 1
                if self._half_open_successes >= self._success_threshold:
                    await self._close_circuit()
            elif state.is_closed:
                # Reset failures on success when closed (normal operation)
                await self._redis.set(self._failures_key(), 0)
                self._invalidate_l1()
        except Exception as exc:
            logger.warning(
                "circuit_breaker.record_success_error",
                extra={"backend_id": self.backend_id, "error": str(exc)},
            )

    async def record_failure(self) -> None:
        """Record a transient backend failure and open the circuit if threshold is crossed.

        Implements: memory/specs/007-rate-limiting-and-throughput.md — AC-14, AC-14d, AC-17b
        """
        try:
            count_raw = await self._redis.incr(self._failures_key())
            count = int(count_raw)
            self._invalidate_l1()

            if count >= self._failure_threshold:
                state_val = await self._redis.get(self._state_key())
                current_state = state_val.decode() if state_val else "closed"
                if current_state != _STATE_OPEN:
                    now = time.time()
                    pipe = self._redis.pipeline()
                    pipe.set(self._state_key(), _STATE_OPEN)
                    pipe.set(self._opened_at_key(), str(now))
                    # Release probe lock if held in HALF-OPEN
                    pipe.delete(self._probe_lock_key())
                    await pipe.execute()
                    self._invalidate_l1()
                    logger.warning(
                        "circuit_breaker.opened",
                        extra={"backend_id": self.backend_id, "failures": count},
                    )
        except Exception as exc:
            logger.warning(
                "circuit_breaker.record_failure_error",
                extra={"backend_id": self.backend_id, "error": str(exc)},
            )

    async def _close_circuit(self) -> None:
        """Transition to CLOSED and clear all Redis CB keys.

        Implements: memory/specs/007-rate-limiting-and-throughput.md — AC-14c
        """
        try:
            pipe = self._redis.pipeline()
            pipe.delete(self._state_key())
            pipe.delete(self._failures_key())
            pipe.delete(self._opened_at_key())
            pipe.delete(self._probe_lock_key())
            await pipe.execute()
            self._invalidate_l1()
            self._half_open_successes = 0
            logger.info("circuit_breaker.closed", extra={"backend_id": self.backend_id})
        except Exception as exc:
            logger.warning(
                "circuit_breaker.close_error",
                extra={"backend_id": self.backend_id, "error": str(exc)},
            )
