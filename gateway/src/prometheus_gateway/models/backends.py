"""Shared httpx.AsyncClient pool — one client per backend URL.

Implements: memory/specs/006-multi-model-gateway.md — AC-15
Implements: memory/specs/007-rate-limiting-and-throughput.md — AC-14, AC-17, AC-18
Implements: memory/specs/018-observability-telemetry.md — AC-1 (structlog migration)
"""

from __future__ import annotations

import asyncio
import contextlib
import random
from collections.abc import Iterator, Sequence
from typing import Any

import httpx

from ..circuit_breaker import CircuitBreaker
from ..telemetry import get_logger

logger = get_logger(__name__)

# HTTP status codes that indicate a transient backend fault — safe to retry
_TRANSIENT_STATUS_CODES: frozenset[int] = frozenset({502, 503, 504})

# RM-52: was 120.0 — too short for a real FLUX.1-dev generation (~150-500s at
# 20 steps on Metal, confirmed empirically), which read-timed-out and then
# got retried into an even longer wait since sd-server keeps computing a
# request server-side even after the client gives up. A higher ceiling here
# doesn't slow down already-fast chat/embeddings responses — it only matters
# when a backend is legitimately still working.
_BACKEND_REQUEST_TIMEOUT_S = 600.0


class _TransientBackendError(Exception):
    """Raised internally when a backend returns a transient 5xx response."""

    def __init__(self, status_code: int) -> None:
        self.status_code = status_code
        super().__init__(f"Transient backend error: HTTP {status_code}")


class BackendPool:
    """One shared httpx.AsyncClient per backend URL with circuit breaker and retry.

    Created at application startup and reused across all requests.
    Eliminates per-request TCP handshake overhead.

    Implements: memory/specs/006-multi-model-gateway.md — AC-15
    Implements: memory/specs/007-rate-limiting-and-throughput.md — AC-14, AC-17, AC-18
    """

    def __init__(
        self,
        redis_client: Any = None,
        *,
        failure_threshold: int = 5,
        recovery_timeout: int = 30,
        success_threshold: int = 2,
        retry_max: int = 2,
        retry_backoff_base_ms: int = 200,
    ) -> None:
        self._clients: dict[str, httpx.AsyncClient] = {}
        # RM-72: per-backend in-flight count, the signal least-loaded routing uses.
        self._in_flight: dict[str, int] = {}
        self._circuit_breakers: dict[str, CircuitBreaker] = {}
        self._redis = redis_client
        self._failure_threshold = failure_threshold
        self._recovery_timeout = recovery_timeout
        self._success_threshold = success_threshold
        self._retry_max = retry_max
        self._retry_backoff_base_ms = retry_backoff_base_ms

    def in_flight(self, backend_id: str) -> int:
        """Requests this backend is handling right now — RM-72."""
        return self._in_flight.get(backend_id, 0)

    def acquire(self, backend_id: str) -> None:
        """Claim a slot on *backend_id*. Pair with release()."""
        self._in_flight[backend_id] = self._in_flight.get(backend_id, 0) + 1

    def release(self, backend_id: str) -> None:
        remaining = self._in_flight.get(backend_id, 1) - 1
        if remaining > 0:
            self._in_flight[backend_id] = remaining
        else:
            self._in_flight.pop(backend_id, None)

    @contextlib.contextmanager
    def track(self, backend_id: str) -> Iterator[None]:
        """Count a request against *backend_id* for its whole lifetime.

        Counted here rather than read from the engine because it has to work
        for every backend: sd.cpp exposes no metrics endpoint at all (verified
        against a running sd-server), so anything derived from the engine's own
        numbers would silently stop balancing image generation.

        Synchronous on purpose — it only mutates a dict, and making it async
        would add an await point between the check and the increment that the
        event loop could interleave, which is exactly the race it exists to
        avoid.
        """
        self.acquire(backend_id)
        try:
            yield
        finally:
            self.release(backend_id)

    def get(self, backend_url: str) -> httpx.AsyncClient:
        """Return the shared client for *backend_url*, creating it on first access."""
        if backend_url not in self._clients:
            self._clients[backend_url] = httpx.AsyncClient(timeout=_BACKEND_REQUEST_TIMEOUT_S)
        return self._clients[backend_url]

    def get_circuit_breaker(self, backend_id: str) -> CircuitBreaker | None:
        """Return the CircuitBreaker for this backend (if Redis is configured).

        Implements: memory/specs/007-rate-limiting-and-throughput.md — AC-14, AC-16
        """
        if self._redis is None:
            return None
        if backend_id not in self._circuit_breakers:
            self._circuit_breakers[backend_id] = CircuitBreaker(
                backend_id=backend_id,
                redis_client=self._redis,
                failure_threshold=self._failure_threshold,
                recovery_timeout=self._recovery_timeout,
                success_threshold=self._success_threshold,
            )
        return self._circuit_breakers[backend_id]

    def update_circuit_breaker_settings(
        self,
        *,
        failure_threshold: int,
        recovery_timeout: int,
        success_threshold: int,
    ) -> None:
        """Re-tune circuit breakers without a restart — RM-67.

        Two layers hold their own copies of these numbers: this pool (used as
        the defaults for breakers created later) and every CircuitBreaker
        already built for a backend. Both are updated here, otherwise a saved
        change would only reach models that happen to be touched for the
        first time afterwards.

        Note this reaches currently-open circuits too: `recovery_at` is derived
        as `opened_at + recovery_timeout` each time state is read, not frozen
        when the circuit tripped — so shortening the timeout brings an
        already-open backend back sooner, and lengthening it defers the probe.
        """
        self._failure_threshold = failure_threshold
        self._recovery_timeout = recovery_timeout
        self._success_threshold = success_threshold
        for breaker in self._circuit_breakers.values():
            breaker.update_settings(
                failure_threshold=failure_threshold,
                recovery_timeout=recovery_timeout,
                success_threshold=success_threshold,
            )

    async def forward(
        self,
        backend_id: str,
        client: httpx.AsyncClient,
        url: str,
        payload: dict[str, Any],
        *,
        stream: bool = False,
        extra_headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        """Forward a non-streaming request with circuit breaker + retry logic.

        Implements: memory/specs/007-rate-limiting-and-throughput.md — AC-17, AC-17b
        Implements: memory/specs/018-observability-telemetry.md — AC-8 (X-Trace-ID forwarded)
        Circuit breaker checks are handled by the router before calling this method.
        """
        cb = self.get_circuit_breaker(backend_id)
        last_exc: Exception | None = None
        headers = extra_headers or {}

        for attempt in range(self._retry_max + 1):
            if attempt > 0:
                wait_ms = self._retry_backoff_base_ms * (2 ** (attempt - 1))
                # ±20% jitter — AC-17
                jitter = random.uniform(0.8, 1.2)
                sleep_s = (wait_ms * jitter) / 1000.0
                logger.warning(
                    "backend.retry",
                    backend_id=backend_id,
                    attempt=attempt,
                    wait_ms=round(wait_ms * jitter),
                    error=str(last_exc),
                )
                await asyncio.sleep(sleep_s)

            try:
                resp = await client.post(
                    url, json=payload, timeout=_BACKEND_REQUEST_TIMEOUT_S, headers=headers
                )

                if resp.status_code in _TRANSIENT_STATUS_CODES:
                    raise _TransientBackendError(resp.status_code)

                # Success — reset circuit breaker
                if cb:
                    await cb.record_success()
                return resp

            except (
                httpx.ConnectError,
                httpx.ConnectTimeout,
                httpx.RemoteProtocolError,
                _TransientBackendError,
            ) as exc:
                last_exc = exc
                if cb:
                    await cb.record_failure()

        # All retries exhausted — AC-17b
        logger.error(
            "backend.retries_exhausted",
            backend_id=backend_id,
            attempts=self._retry_max + 1,
            error=str(last_exc),
        )
        raise last_exc  # type: ignore[misc]

    async def forward_with_failover(
        self,
        candidates: Sequence[tuple[str, str]],
        path: str,
        payload: dict[str, Any],
        *,
        extra_headers: dict[str, str] | None = None,
    ) -> tuple[httpx.Response, str]:
        """Forward to the first candidate that answers — RM-69 (finding B).

        `forward()` retries the *same* backend, which is right for a transient
        blip but useless when the instance is simply gone: all three attempts
        went to the same dead process, and the only thing they achieved was
        opening its circuit faster. With replicas there is somewhere else to go.

        `candidates` is (backend_id, backend_origin) in preference order.
        Returns the response together with the backend that produced it, since
        metrics and the circuit breaker belong to the replica that did the work.
        """
        # RM-72: ordered here, not by the caller, because this is the last
        # moment before the count is taken. The router selects a candidate well
        # before forwarding — budget reservation and validation sit in between —
        # so concurrent requests all finished selecting before any of them had
        # incremented anything, and every one of them picked the same replica.
        # Confirmed live: six concurrent requests to a two-replica model all
        # landed on the same instance. Sorting and tracking with no await
        # between them is what makes the count mean something.
        ordered = sorted(candidates, key=lambda c: self.in_flight(c[0]))

        last_exc: Exception | None = None
        for index, (backend_id, origin) in enumerate(ordered):
            url = f"{origin.rstrip('/')}{path}"
            try:
                with self.track(backend_id):
                    response = await self.forward(
                        backend_id,
                        self.get(origin),
                        url,
                        payload,
                        extra_headers=extra_headers,
                    )
                if index > 0:
                    logger.info(
                        "backend.failover_succeeded",
                        backend_id=backend_id,
                        after_attempts=index,
                    )
                return response, backend_id
            except (
                httpx.ConnectError,
                httpx.ConnectTimeout,
                httpx.RemoteProtocolError,
                _TransientBackendError,
            ) as exc:
                last_exc = exc
                logger.warning(
                    "backend.failing_over",
                    backend_id=backend_id,
                    remaining=len(ordered) - index - 1,
                    error=str(exc),
                )

        assert last_exc is not None  # candidates is never empty at the call site
        raise last_exc

    async def aclose(self) -> None:
        """Close all pooled clients. Called on application shutdown."""
        for client in self._clients.values():
            await client.aclose()
        self._clients.clear()
