"""Structured observability for the Prometheus Gateway.

Thin shim over prometheus_telemetry: re-exports the shared core and adds
the gateway-specific MetricsStore (Prometheus-format counters for GET /metrics).

Implements: memory/specs/020-shared-telemetry-package.md (migration shim)
Previously: memory/specs/018-observability-telemetry.md
  AC-1, AC-4, AC-5, AC-6, AC-7, AC-8, AC-16, AC-17, AC-18, AC-19, AC-20,
  AC-21, AC-22, AC-24, AC-26
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from typing import Any

# ── Re-export shared observability core ──────────────────────────────────────
from prometheus_telemetry import (  # noqa: F401  (re-exported for callers)
    TraceIDMiddleware,
    configure_logging,
    configure_tracing,
    get_logger,
    get_tracer,
    trace_id_from_context,
)

__all__ = [
    "TraceIDMiddleware",
    "configure_logging",
    "configure_tracing",
    "get_logger",
    "get_tracer",
    "trace_id_from_context",
    "MetricsStore",
    "metrics_store",
]


# ── In-process MetricsStore (AC-19, AC-20, AC-21, AC-22) ─────────────────────


class MetricsStore:
    """Thread-safe in-process counter store for GET /metrics.

    Uses asyncio.Lock — safe for concurrent coroutines on a single event loop.
    Implements: memory/specs/018-observability-telemetry.md — AC-19, AC-20, AC-21, AC-22.
    """

    _MAX_LATENCY_SAMPLES = 1_000

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._start_time = time.monotonic()
        # Inference counters
        self._requests_total: int = 0
        self._requests_active: int = 0
        self._tokens_prompt_total: int = 0
        self._tokens_completion_total: int = 0
        self._errors_total: int = 0
        # Sliding window for percentile approximation
        self._latencies: deque[int] = deque(maxlen=self._MAX_LATENCY_SAMPLES)
        # Auth counters
        self._jwt_ok: int = 0
        self._jwt_failed: int = 0
        # Per-backend counters: {backend_id: {"requests_total": int}}
        self._backends: dict[str, dict[str, Any]] = {}

    async def inc_requests_active(self) -> None:
        async with self._lock:
            self._requests_active += 1
            self._requests_total += 1

    async def dec_requests_active(self) -> None:
        async with self._lock:
            self._requests_active = max(0, self._requests_active - 1)

    async def record_inference(
        self,
        *,
        prompt_tokens: int,
        completion_tokens: int,
        latency_ms: int,
        backend_id: str,
        error: bool = False,
    ) -> None:
        async with self._lock:
            self._tokens_prompt_total += prompt_tokens
            self._tokens_completion_total += completion_tokens
            self._latencies.append(latency_ms)
            if error:
                self._errors_total += 1
            if backend_id not in self._backends:
                self._backends[backend_id] = {"requests_total": 0}
            self._backends[backend_id]["requests_total"] += 1

    async def inc_jwt_ok(self) -> None:
        async with self._lock:
            self._jwt_ok += 1

    async def inc_jwt_failed(self) -> None:
        async with self._lock:
            self._jwt_failed += 1

    def _percentile(self, samples: list[int], pct: float) -> int:
        if not samples:
            return 0
        sorted_samples = sorted(samples)
        idx = max(0, int(len(sorted_samples) * pct / 100) - 1)
        return sorted_samples[min(idx, len(sorted_samples) - 1)]

    async def snapshot(self, pool: Any | None = None) -> dict[str, Any]:
        """Return a JSON-serialisable metrics snapshot.

        AC-21: no per-user data — only aggregate counters and named backend states.
        AC-22: includes circuit_state per backend.
        """
        async with self._lock:
            latencies = list(self._latencies)
            backends_copy = dict(self._backends)

        uptime = int(time.monotonic() - self._start_time)
        inference: dict[str, Any] = {
            "requests_total": self._requests_total,
            "requests_active": self._requests_active,
            "tokens_prompt_total": self._tokens_prompt_total,
            "tokens_completion_total": self._tokens_completion_total,
            "errors_total": self._errors_total,
            "latency_p50_ms": self._percentile(latencies, 50),
            "latency_p95_ms": self._percentile(latencies, 95),
            "latency_p99_ms": self._percentile(latencies, 99),
        }

        # AC-22: enrich with circuit state from BackendPool if available
        backends_out: dict[str, Any] = {}
        for bid, counters in backends_copy.items():
            entry: dict[str, Any] = dict(counters)
            if pool is not None:
                cb = pool.get_circuit_breaker(bid)
                if cb is not None:
                    try:
                        cb_state = await cb.get_state()
                        entry["circuit_state"] = cb_state.state
                    except Exception:
                        entry["circuit_state"] = "unknown"
                else:
                    entry["circuit_state"] = "closed"
            backends_out[bid] = entry

        return {
            "service": "gateway",
            "uptime_seconds": uptime,
            "inference": inference,
            "auth": {
                "jwt_validations_ok": self._jwt_ok,
                "jwt_validations_failed": self._jwt_failed,
            },
            "backends": backends_out,
        }


# Module-level singleton — injected into the FastAPI app at startup
metrics_store = MetricsStore()
