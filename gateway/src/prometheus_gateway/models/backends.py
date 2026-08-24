"""Shared httpx.AsyncClient pool — one client per backend URL.

Implements: memory/specs/006-multi-model-gateway.md — AC-15
Implements: memory/specs/007-rate-limiting-and-throughput.md — AC-14, AC-17, AC-18
Implements: memory/specs/018-observability-telemetry.md — AC-1 (structlog migration)
"""

from __future__ import annotations

import asyncio
import random
from typing import Any

import httpx

from ..circuit_breaker import CircuitBreaker
from ..telemetry import get_logger

logger = get_logger(__name__)

# HTTP status codes that indicate a transient backend fault — safe to retry
_TRANSIENT_STATUS_CODES: frozenset[int] = frozenset({502, 503, 504})


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
        self._circuit_breakers: dict[str, CircuitBreaker] = {}
        self._redis = redis_client
        self._failure_threshold = failure_threshold
        self._recovery_timeout = recovery_timeout
        self._success_threshold = success_threshold
        self._retry_max = retry_max
        self._retry_backoff_base_ms = retry_backoff_base_ms

    def get(self, backend_url: str) -> httpx.AsyncClient:
        """Return the shared client for *backend_url*, creating it on first access."""
        if backend_url not in self._clients:
            self._clients[backend_url] = httpx.AsyncClient(timeout=120.0)
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
                resp = await client.post(url, json=payload, timeout=120.0, headers=headers)

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

    async def aclose(self) -> None:
        """Close all pooled clients. Called on application shutdown."""
        for client in self._clients.values():
            await client.aclose()
        self._clients.clear()
