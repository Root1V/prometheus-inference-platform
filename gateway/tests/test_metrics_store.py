"""Tests for RM-46 — per-backend performance metrics (latency, TTFT,
inter-token, throughput).

Uses a fresh MetricsStore() per test rather than the module-level singleton,
to avoid state bleeding between tests.
"""

from __future__ import annotations

from prometheus_gateway.telemetry import MetricsStore


async def test_backend_with_no_samples_has_none_ttft_and_inter_token():
    """A freshly-seen backend shouldn't report ttft/inter_token as 0 — that
    would look like a real (impossibly fast) measurement rather than "no
    data yet"."""
    store = MetricsStore()
    await store.record_inference(
        prompt_tokens=10, completion_tokens=5, latency_ms=100, backend_id="b1"
    )
    snap = await store.snapshot()
    entry = snap["backends"]["b1"]
    assert entry["ttft_p50_ms"] is None
    assert entry["inter_token_ms_avg"] is None
    assert entry["tokens_per_second_avg"] is None
    assert entry["images_per_second_avg"] is None
    assert entry["latency_p50_ms"] == 100
    assert entry["latency_p95_ms"] == 100


async def test_backend_latency_percentiles_are_scoped_per_backend():
    """Two backends' latency samples must not mix."""
    store = MetricsStore()
    for ms in (100, 200, 300):
        await store.record_inference(
            prompt_tokens=1, completion_tokens=1, latency_ms=ms, backend_id="fast"
        )
    for ms in (1000, 2000, 3000):
        await store.record_inference(
            prompt_tokens=1, completion_tokens=1, latency_ms=ms, backend_id="slow"
        )
    snap = await store.snapshot()
    # Matches the existing _percentile() formula (unchanged by RM-46): for 3
    # samples, p50 lands on the smallest, not a true median — that's a
    # pre-existing approximation, not something this test is re-deciding.
    assert snap["backends"]["fast"]["latency_p50_ms"] == 100
    assert snap["backends"]["slow"]["latency_p50_ms"] == 1000
    # global latency percentiles remain the combined/all-backend figure
    assert snap["inference"]["latency_p50_ms"] in (100, 200, 300, 1000, 2000, 3000)


async def test_ttft_and_inter_token_recorded_when_provided():
    store = MetricsStore()
    await store.record_inference(
        prompt_tokens=10,
        completion_tokens=20,
        latency_ms=500,
        backend_id="b1",
        ttft_ms=42,
        inter_token_ms=8.5,
    )
    snap = await store.snapshot()
    entry = snap["backends"]["b1"]
    assert entry["ttft_p50_ms"] == 42
    assert entry["inter_token_ms_avg"] == 8.5


async def test_inter_token_ms_avg_is_the_mean_across_requests():
    store = MetricsStore()
    for value in (10.0, 20.0, 30.0):
        await store.record_inference(
            prompt_tokens=1,
            completion_tokens=1,
            latency_ms=100,
            backend_id="b1",
            inter_token_ms=value,
        )
    snap = await store.snapshot()
    assert snap["backends"]["b1"]["inter_token_ms_avg"] == 20.0


async def test_ttft_only_recorded_for_requests_that_report_it():
    """A backend that never reports ttft (e.g. every request was
    non-streaming) should still keep reporting None, not average in
    missing samples as 0."""
    store = MetricsStore()
    await store.record_inference(
        prompt_tokens=1, completion_tokens=1, latency_ms=100, backend_id="b1"
    )
    await store.record_inference(
        prompt_tokens=1, completion_tokens=1, latency_ms=100, backend_id="b1"
    )
    snap = await store.snapshot()
    assert snap["backends"]["b1"]["ttft_p50_ms"] is None


async def test_tokens_per_second_avg_is_the_mean_across_requests():
    store = MetricsStore()
    for value in (10.0, 20.0, 30.0):
        await store.record_inference(
            prompt_tokens=1,
            completion_tokens=1,
            latency_ms=100,
            backend_id="b1",
            tokens_per_second=value,
        )
    snap = await store.snapshot()
    assert snap["backends"]["b1"]["tokens_per_second_avg"] == 20.0


async def test_tokens_per_second_none_when_not_provided():
    """Same rationale as ttft/inter_token: a request with no completion
    tokens (e.g. an error) shouldn't drag the average toward 0."""
    store = MetricsStore()
    await store.record_inference(
        prompt_tokens=1, completion_tokens=0, latency_ms=100, backend_id="b1"
    )
    snap = await store.snapshot()
    assert snap["backends"]["b1"]["tokens_per_second_avg"] is None


async def test_images_per_second_avg_is_the_mean_across_requests():
    """Same idea as tokens_per_second, but for image-generation backends —
    the metric applies to a workload with no token concept at all."""
    store = MetricsStore()
    for value in (0.1, 0.2, 0.3):
        await store.record_inference(
            prompt_tokens=0,
            completion_tokens=0,
            latency_ms=5000,
            backend_id="b1",
            images_per_second=value,
        )
    snap = await store.snapshot()
    assert snap["backends"]["b1"]["images_per_second_avg"] == 0.2


async def test_images_per_second_none_when_not_provided():
    store = MetricsStore()
    await store.record_inference(
        prompt_tokens=0, completion_tokens=0, latency_ms=100, backend_id="b1"
    )
    snap = await store.snapshot()
    assert snap["backends"]["b1"]["images_per_second_avg"] is None
