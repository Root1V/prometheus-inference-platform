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
from collections.abc import Sequence
from typing import Any

# ── Re-export shared observability core ──────────────────────────────────────
from prometheus_telemetry import (  # noqa: F401  (re-exported for callers)
    TraceIDMiddleware,
    configure_logging,
    configure_metrics,
    configure_logs,
    configure_tracing,
    get_logger,
    get_tracer,
    instrument_fastapi,
    trace_id_from_context,
)

__all__ = [
    "TraceIDMiddleware",
    "configure_logging",
    "configure_metrics",
    "configure_logs",
    "configure_tracing",
    "get_logger",
    "get_tracer",
    "instrument_fastapi",
    "trace_id_from_context",
    "MetricsStore",
    "metrics_store",
    "ActivityTracker",
    "activity_tracker",
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
        # RM-73: which model each backend serves, for the per-model rollup.
        self._backend_model: dict[str, str] = {}
        # RM-46: per-backend samples, same sliding-window approach as the
        # global _latencies deque above. ttft/inter_token are sparse — only
        # streaming requests report ttft, and only llama.cpp-family backends
        # report inter_token (from their `timings` object) — so these deques
        # only grow when a backend actually reports the corresponding value.
        self._backend_latencies: dict[str, deque[int]] = {}
        self._backend_ttft: dict[str, deque[int]] = {}
        self._backend_inter_token: dict[str, deque[float]] = {}
        # RM-46 follow-up: throughput — confirmed via research as one of the
        # field's own "core four" metrics (TTFT, inter-token, throughput,
        # e2e latency) alongside the three above. Populated whenever
        # completion_tokens > 0, unlike ttft/inter_token which depend on
        # streaming or a specific backend's `timings` object.
        self._backend_tps: dict[str, deque[float]] = {}
        # RM-46 follow-up: images/second — the throughput analog for image-
        # generation backends, which have no token concept at all (tps stays
        # unused for them; this is the field embeddings/images each get one
        # of — tps doubles as "output tok/s" for chat and "input tok/s" for
        # embeddings, since both are genuinely tokens/second either way).
        self._backend_ips: dict[str, deque[float]] = {}
        # RM-62: prefill (prompt-processing) throughput — distinct from
        # `_backend_tps`, which is decode-phase for chat. llama.cpp-family
        # backends' `timings` object reports this directly
        # (`prompt_per_second`); other backends never populate it, same
        # sparse-population convention as ttft/inter_token above.
        self._backend_prompt_tps: dict[str, deque[float]] = {}

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
        model_id: str | None = None,
        error: bool = False,
        ttft_ms: int | None = None,
        inter_token_ms: float | None = None,
        tokens_per_second: float | None = None,
        images_per_second: float | None = None,
        prompt_tokens_per_second: float | None = None,
    ) -> None:
        async with self._lock:
            self._tokens_prompt_total += prompt_tokens
            self._tokens_completion_total += completion_tokens
            self._latencies.append(latency_ms)
            if error:
                self._errors_total += 1
            if backend_id not in self._backends:
                self._backends[backend_id] = {"requests_total": 0}
                self._backend_latencies[backend_id] = deque(maxlen=self._MAX_LATENCY_SAMPLES)
                self._backend_ttft[backend_id] = deque(maxlen=self._MAX_LATENCY_SAMPLES)
                self._backend_inter_token[backend_id] = deque(maxlen=self._MAX_LATENCY_SAMPLES)
                self._backend_tps[backend_id] = deque(maxlen=self._MAX_LATENCY_SAMPLES)
                self._backend_ips[backend_id] = deque(maxlen=self._MAX_LATENCY_SAMPLES)
                self._backend_prompt_tps[backend_id] = deque(maxlen=self._MAX_LATENCY_SAMPLES)
            if model_id:
                # RM-73: remembered per backend rather than accumulated in a
                # parallel set of deques — the rollup below re-derives itself
                # from the same samples, so a model's percentiles come from
                # real observations instead of an average of averages.
                self._backend_model[backend_id] = model_id
            self._backends[backend_id]["requests_total"] += 1
            self._backend_latencies[backend_id].append(latency_ms)
            if ttft_ms is not None:
                self._backend_ttft[backend_id].append(ttft_ms)
            if inter_token_ms is not None:
                self._backend_inter_token[backend_id].append(inter_token_ms)
            if tokens_per_second is not None:
                self._backend_tps[backend_id].append(tokens_per_second)
            if images_per_second is not None:
                self._backend_ips[backend_id].append(images_per_second)
            if prompt_tokens_per_second is not None:
                self._backend_prompt_tps[backend_id].append(prompt_tokens_per_second)

    async def inc_jwt_ok(self) -> None:
        async with self._lock:
            self._jwt_ok += 1

    async def inc_jwt_failed(self) -> None:
        async with self._lock:
            self._jwt_failed += 1

    async def model_latency_p95_ms(self, model_id: str) -> float | None:
        """This model's p95 latency across its replicas — RM-81.

        Pooled from the same samples the /metrics rollup uses, so a request
        already in flight can be given a real estimate of how long it has left
        instead of a guess. None when nothing has been observed yet: these
        counters live in process memory and reset with the gateway, so the
        caller needs a fallback.
        """
        async with self._lock:
            members = [b for b, m in self._backend_model.items() if m == model_id]
            samples = [s for b in members for s in self._backend_latencies.get(b, [])]
        return self._percentile(samples, 95) if samples else None

    def _percentile(self, samples: Sequence[float], pct: float) -> float:
        if not samples:
            return 0
        sorted_samples = sorted(samples)
        idx = max(0, int(len(sorted_samples) * pct / 100) - 1)
        return sorted_samples[min(idx, len(sorted_samples) - 1)]

    def uptime_seconds(self) -> int:
        """How long this process has been up — PRM-238.

        Public because the Activity page needs it for a different reason than
        the metrics endpoint: when the gateway started less than fifteen
        minutes ago, its in-memory view of who did what covers a shorter span
        than the table around it, and that is worth saying rather than leaving
        a reader to notice the numbers are thin.
        """
        return int(time.monotonic() - self._start_time)

    async def snapshot(self, pool: Any | None = None) -> dict[str, Any]:
        """Return a JSON-serialisable metrics snapshot.

        AC-21: no per-user data — only aggregate counters and named backend states.
        AC-22: includes circuit_state per backend.
        """
        async with self._lock:
            latencies = list(self._latencies)
            backends_copy = dict(self._backends)
            backend_latencies_copy = {k: list(v) for k, v in self._backend_latencies.items()}
            backend_ttft_copy = {k: list(v) for k, v in self._backend_ttft.items()}
            backend_inter_token_copy = {k: list(v) for k, v in self._backend_inter_token.items()}
            backend_tps_copy = {k: list(v) for k, v in self._backend_tps.items()}
            backend_ips_copy = {k: list(v) for k, v in self._backend_ips.items()}
            backend_prompt_tps_copy = {k: list(v) for k, v in self._backend_prompt_tps.items()}
            backend_model_copy = dict(self._backend_model)

        uptime = self.uptime_seconds()
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
            # RM-46: per-model performance metrics — latency mirrors the global
            # p50/p95/p99 above but scoped to this backend; ttft/inter_token
            # only populate once a request has actually reported them (ttft
            # needs streaming, inter_token needs a llama.cpp-family backend's
            # `timings` object), hence the None default rather than 0 — a
            # backend with no samples yet shouldn't look like a real 0ms.
            backend_latency_samples = backend_latencies_copy.get(bid, [])
            entry["latency_p50_ms"] = self._percentile(backend_latency_samples, 50)
            entry["latency_p95_ms"] = self._percentile(backend_latency_samples, 95)
            ttft_samples = backend_ttft_copy.get(bid, [])
            entry["ttft_p50_ms"] = self._percentile(ttft_samples, 50) if ttft_samples else None
            inter_token_samples = backend_inter_token_copy.get(bid, [])
            entry["inter_token_ms_avg"] = (
                round(sum(inter_token_samples) / len(inter_token_samples), 2)
                if inter_token_samples
                else None
            )
            tps_samples = backend_tps_copy.get(bid, [])
            entry["tokens_per_second_avg"] = (
                round(sum(tps_samples) / len(tps_samples), 2) if tps_samples else None
            )
            # RM-46 follow-up: images/second — the throughput analog for
            # image-generation backends (no token concept applies to them).
            ips_samples = backend_ips_copy.get(bid, [])
            entry["images_per_second_avg"] = (
                round(sum(ips_samples) / len(ips_samples), 3) if ips_samples else None
            )
            # RM-62: prefill throughput — separate from tokens_per_second_avg
            # (decode-phase for chat), sparse (llama.cpp-family backends only).
            prompt_tps_samples = backend_prompt_tps_copy.get(bid, [])
            entry["prompt_tokens_per_second_avg"] = (
                round(sum(prompt_tps_samples) / len(prompt_tps_samples), 2)
                if prompt_tps_samples
                else None
            )
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

        # RM-73: the same numbers grouped by model. With replicas, per-backend
        # rows alone force whoever is looking to add up N instances by hand to
        # answer "how is this model doing" — which is the only unit a consumer
        # of the model cares about. Percentiles are recomputed from the pooled
        # samples rather than averaged across backends, which would weight a
        # replica that served three requests the same as one that served a
        # thousand.
        models_out: dict[str, Any] = {}
        for model_id in sorted(set(backend_model_copy.values())):
            member_ids = [b for b, m in backend_model_copy.items() if m == model_id]
            pooled_latency = [s for b in member_ids for s in backend_latencies_copy.get(b, [])]
            pooled_ttft = [s for b in member_ids for s in backend_ttft_copy.get(b, [])]
            pooled_tps = [s for b in member_ids for s in backend_tps_copy.get(b, [])]
            models_out[model_id] = {
                "instances": len(member_ids),
                "instance_ids": sorted(member_ids),
                "requests_total": sum(
                    int(backends_copy.get(b, {}).get("requests_total", 0)) for b in member_ids
                ),
                "latency_p50_ms": self._percentile(pooled_latency, 50),
                "latency_p95_ms": self._percentile(pooled_latency, 95),
                "ttft_p50_ms": self._percentile(pooled_ttft, 50) if pooled_ttft else None,
                "tokens_per_second_avg": (
                    round(sum(pooled_tps) / len(pooled_tps), 2) if pooled_tps else None
                ),
            }

        return {
            "service": "gateway",
            "uptime_seconds": uptime,
            "inference": inference,
            "auth": {
                "jwt_validations_ok": self._jwt_ok,
                "jwt_validations_failed": self._jwt_failed,
            },
            "backends": backends_out,
            "models": models_out,
        }


# Module-level singleton — injected into the FastAPI app at startup
metrics_store = MetricsStore()


# ── In-process ActivityTracker — docs/roadmap.md RM-23 ───────────────────────


# PRM-236: what a caller was doing, not just that it was here.
#
# One label per kind of thing a credential can do through this gateway, from
# the path. The set is closed and small on purpose: these become per-identity
# counters in memory, and a label taken from the URL unbounded is an unbounded
# dictionary. Anything unrecognised lands in `other` rather than minting a key.
ACTION_LABELS: dict[str, str] = {
    "chat": "Chat completions",
    "embeddings": "Embeddings",
    "rerank": "Rerank",
    "predict": "Model pass-through",
    "images": "Image generation",
    "models.list": "Listed its models",
    "backends": "Read backend status",
    "usage": "Read its own usage",
    "playground": "Playground",
    "dashboard": "Admin dashboard",
    "other": "Other",
}

# The dashboard tells us when a call is the Playground rather than an SDK —
# both are the same credential hitting the same route, so the path cannot
# separate them. Self-reported, and only ever used as a label.
SOURCE_HEADER = "x-prometheus-source"


def classify_action(path: str, source: str | None = None) -> str:
    """Which of `ACTION_LABELS` this request is."""
    if source == "playground":
        return "playground"
    if path.startswith("/admin/api"):
        return "dashboard"
    if path == "/v1/models":
        return "models.list"
    if path == "/v1/backends":
        return "backends"
    if path.startswith("/v1/usage"):
        return "usage"
    if path == "/v1/chat/completions":
        return "chat"
    if path == "/v1/embeddings":
        return "embeddings"
    if path == "/v1/rerank":
        return "rerank"
    if path == "/v1/images/generations":
        return "images"
    if path.startswith("/v1/models/") and path.endswith("/predict"):
        return "predict"
    return "other"


class ActivityTracker:
    """Who is here, and what they are doing — PRM-236.

    Not a session registry: JWTs are stateless and there is no server-side
    session object to track. What this does have is every authenticated
    request, so it can answer the question the old version could not — an
    operator looking at a credential wants to know whether it ran inference,
    listed its models, or opened the dashboard, and "connection_type: api"
    answered none of that because it was the URL prefix of whichever request
    happened to be last.

    Still single-process memory, like MetricsStore, so a restart empties it.
    The Activity page is built knowing that: Redis and the usage rows carry
    what survives, and this carries what is happening *now*.

    Bounded by construction — one entry per identity seen in the window, each
    holding at most one counter per label in `ACTION_LABELS`.
    """

    _STALE_AFTER_S = 15 * 60
    _WINDOW_BUCKETS = _STALE_AFTER_S // 60

    @classmethod
    def window_started_at(cls, now: float | None = None) -> float:
        """The instant the live window opens — PRM-238.

        One definition, because two tables now count the same window from two
        stores: these minute buckets, and `usage_events` filtered by
        `recorded_at`. A rolling 900 seconds would include requests the buckets
        exclude, which is how the page would show a detail that does not add up
        to the summary above it.
        """
        bucket = int((now if now is not None else time.time()) // 60)
        return (bucket - cls._WINDOW_BUCKETS + 1) * 60.0

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._entries: dict[str, dict[str, Any]] = {}

    async def touch(
        self,
        client_id: str,
        user_id: str,
        connection_type: str,
        action: str = "other",
    ) -> None:
        now = time.time()
        async with self._lock:
            entry = self._entries.get(client_id)
            if entry is None:
                entry = self._entries[client_id] = {
                    "client_id": client_id,
                    "user_id": user_id,
                    "actions": {},
                }
            entry["user_id"] = user_id
            entry["connection_type"] = connection_type
            entry["last_seen"] = now
            # PRM-237: counted in one-minute buckets, and the snapshot sums the
            # ones inside the window. The first version kept a running total
            # per action, which made the page say "284" under a heading that
            # said "in the last 15 minutes" — a cumulative figure wearing a
            # window's label. Fifteen ints per action per identity is the price
            # of the two agreeing.
            counter = entry["actions"].setdefault(action, {"buckets": {}, "last_seen": now})
            counter["last_seen"] = now
            bucket = int(now // 60)
            counter["buckets"][bucket] = counter["buckets"].get(bucket, 0) + 1
            oldest = bucket - self._WINDOW_BUCKETS
            for stale in [b for b in counter["buckets"] if b < oldest]:
                del counter["buckets"][stale]

    # A caller can ask for any string, so this is capped per identity. Twenty
    # distinct (model, reason) pairs is far more than a working integration
    # produces and still bounds the memory a typo loop can cost.
    _MAX_REFUSALS = 20

    async def refuse(self, client_id: str, model: str | None, reason: str) -> None:
        """A request that named a model and never reached one — PRM-253.

        Counted here because nothing else counts it. A refusal writes no usage
        row (nothing was consumed), does not touch `errors_total` (the backend
        was never asked), and so leaves the Activity page showing only the
        models that *worked* — which is how a consumer calling two models
        appears to be calling one. Measured on this deployment: one consumer
        with twenty chat completions in the window and four usage rows all day.
        """
        now = time.time()
        async with self._lock:
            entry = self._entries.get(client_id)
            if entry is None:
                return
            refusals = entry.setdefault("refusals", {})
            key = (model or "unnamed", reason)
            counter = refusals.get(key)
            if counter is None:
                if len(refusals) >= self._MAX_REFUSALS:
                    return
                counter = refusals[key] = {"buckets": {}}
            bucket = int(now // 60)
            counter["buckets"][bucket] = counter["buckets"].get(bucket, 0) + 1
            oldest = bucket - self._WINDOW_BUCKETS
            for stale in [b for b in counter["buckets"] if b < oldest]:
                del counter["buckets"][stale]

    async def snapshot(self) -> list[dict[str, Any]]:
        """Active entries (seen in the last 15 min), most recent first.

        Prunes anything older than that while it's already got the lock.
        """
        now = time.time()
        async with self._lock:
            stale_ids = [
                cid
                for cid, e in self._entries.items()
                if now - e["last_seen"] > self._STALE_AFTER_S
            ]
            for cid in stale_ids:
                del self._entries[cid]
            active = [
                {
                    **e,
                    "last_seen_ago_s": int(now - e["last_seen"]),
                    "actions": [
                        counted
                        for counted in (
                            {
                                "action": action,
                                "label": ACTION_LABELS.get(action, action),
                                "count": sum(
                                    n
                                    for b, n in c["buckets"].items()
                                    if b * 60 >= self.window_started_at(now)
                                ),
                                "last_seen_ago_s": int(now - c["last_seen"]),
                            }
                            for action, c in sorted(
                                e["actions"].items(), key=lambda kv: -kv[1]["last_seen"]
                            )
                        )
                        # An action whose every bucket has aged out is one this
                        # identity is no longer doing. Reporting it at zero
                        # would read as "did this, nil times".
                        if counted["count"] > 0
                    ],
                    # PRM-253: models this credential asked for and was refused
                    # inside the window. Same bucket arithmetic as the actions,
                    # and the same reason for it: a count under a heading that
                    # says "last 15 minutes" has to mean that.
                    "refusals": [
                        counted
                        for counted in (
                            {
                                "model": model,
                                "reason": reason,
                                "count": sum(
                                    n
                                    for b, n in c["buckets"].items()
                                    if b * 60 >= self.window_started_at(now)
                                ),
                            }
                            for (model, reason), c in e.get("refusals", {}).items()
                        )
                        if counted["count"] > 0
                    ],
                }
                for e in self._entries.values()
            ]
        active.sort(key=lambda e: e["last_seen_ago_s"])
        return active


activity_tracker = ActivityTracker()
