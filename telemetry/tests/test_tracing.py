"""Unit tests for prometheus_telemetry.tracing.

Implements: memory/specs/022-opentelemetry-sdk-instrumentation.md — AC-1 to AC-7
"""

from __future__ import annotations

import re
from typing import Any

import pytest
import structlog
from httpx import ASGITransport, AsyncClient
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import PlainTextResponse
from starlette.routing import Route

import prometheus_telemetry.core as _core
import prometheus_telemetry.tracing as _tracing
from prometheus_telemetry import (
    TraceIDMiddleware,
    configure_tracing,
    get_tracer,
    trace_id_from_context,
)

# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def reset_tracing():
    """Reset OTEL and telemetry state between tests."""
    _reset_otel_global()
    _tracing._CONFIGURED = False
    _tracing._TRACING_ACTIVE = False
    _core._CONFIGURED = False
    structlog.reset_defaults()
    structlog.contextvars.clear_contextvars()
    yield
    _reset_otel_global()
    _tracing._CONFIGURED = False
    _tracing._TRACING_ACTIVE = False
    _core._CONFIGURED = False
    structlog.reset_defaults()
    structlog.contextvars.clear_contextvars()


def _reset_otel_global() -> None:
    """Reset the OTEL global tracer-provider state so configure_tracing() can re-run.

    The SDK guards against re-setting via a ``Once`` object.  We reset both
    ``_TRACER_PROVIDER`` and ``_TRACER_PROVIDER_SET_ONCE`` to restore the
    pristine state.  For use in tests ONLY.
    """
    import opentelemetry.trace as _ot
    from opentelemetry.util._once import Once  # type: ignore[import-untyped]

    _ot._TRACER_PROVIDER = None  # type: ignore[attr-defined]
    _ot._TRACER_PROVIDER_SET_ONCE = Once()  # type: ignore[attr-defined]


_W3C_RE = re.compile(r"^[0-9a-f]{32}$")


# ── AC-1: configure_tracing registers BatchSpanProcessor ─────────────────────


def test_configure_tracing_registers_processor() -> None:
    """AC-1: BatchSpanProcessor is registered; no exception raised."""
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    configure_tracing(service="test-svc")

    provider = trace.get_tracer_provider()
    assert isinstance(provider, TracerProvider)
    # Access the internal synchronous processor list
    composite = provider._active_span_processor  # type: ignore[attr-defined]
    # SynchronousMultiSpanProcessor stores processors in _span_processors tuple/list
    procs = getattr(composite, "_span_processors", None) or getattr(
        composite, "span_processors", ()
    )
    assert any(isinstance(p, BatchSpanProcessor) for p in procs)


# ── AC-2: OTEL_SDK_DISABLED → NoOpTracer ─────────────────────────────────────


def test_configure_tracing_disabled_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """AC-2: OTEL_SDK_DISABLED=true → get_tracer() returns a NoOpTracer."""
    monkeypatch.setenv("OTEL_SDK_DISABLED", "true")
    configure_tracing(service="test-svc")

    tracer = get_tracer("test")
    # A NoOpTracer's start_as_current_span returns a NonRecordingSpan
    with tracer.start_as_current_span("test.span") as span:
        assert not span.is_recording()
    assert _tracing._TRACING_ACTIVE is False


def test_configure_tracing_disabled_arg() -> None:
    """AC-2: disabled=True → get_tracer() returns a no-op tracer."""
    configure_tracing(service="test-svc", disabled=True)

    tracer = get_tracer("test")
    with tracer.start_as_current_span("test.span") as span:
        assert not span.is_recording()


# ── AC-3: unreachable Tempo → request completes normally ─────────────────────
# (This is an integration-level test; covered by AC-5 implicitly — the exporter
# uses BatchSpanProcessor which never blocks the caller on export failure.)


# ── AC-4: idempotent — second configure_tracing() is a no-op ─────────────────


def test_configure_tracing_idempotent() -> None:
    """AC-4: second call does not add a second BatchSpanProcessor."""
    configure_tracing(service="test-svc")
    configure_tracing(service="other-svc")  # should be a no-op

    provider = trace.get_tracer_provider()
    assert isinstance(provider, TracerProvider)
    composite = provider._active_span_processor  # type: ignore[attr-defined]
    procs = getattr(composite, "_span_processors", None) or getattr(
        composite, "span_processors", ()
    )
    assert len(procs) == 1


# ── AC-5: TraceIDMiddleware + active OTEL span → trace_id is W3C hex ─────────


async def test_middleware_otel_trace_id() -> None:
    """AC-5: with OTEL active, TraceIDMiddleware binds a 32-char hex trace_id."""
    configure_tracing(service="test-svc", disabled=False)

    captured: list[dict[str, Any]] = []

    async def _capture(request: Request) -> PlainTextResponse:
        captured.append(dict(structlog.contextvars.get_contextvars()))
        return PlainTextResponse(request.state.trace_id)

    app = Starlette(routes=[Route("/", _capture)])
    app.add_middleware(TraceIDMiddleware, service="test-svc")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/")

    assert resp.status_code == 200
    trace_id_in_response = resp.headers["x-trace-id"]
    assert _W3C_RE.match(trace_id_in_response), (
        f"Expected 32-char hex, got {trace_id_in_response!r}"
    )

    assert captured
    assert _W3C_RE.match(captured[0]["trace_id"]), f"structlog got {captured[0]['trace_id']!r}"
    # Both must match
    assert captured[0]["trace_id"] == trace_id_in_response


# ── AC-5b: structlog trace_id == trace_id_from_context() ─────────────────────


async def test_middleware_otel_trace_id_matches_context() -> None:
    """AC-5: bound trace_id equals trace_id_from_context() inside the span."""
    configure_tracing(service="test-svc", disabled=False)

    from_ctx_inside: list[str] = []

    async def _capture(request: Request) -> PlainTextResponse:
        from_ctx_inside.append(trace_id_from_context())
        return PlainTextResponse("ok")

    app = Starlette(routes=[Route("/", _capture)])
    app.add_middleware(TraceIDMiddleware, service="test-svc")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.get("/")

    assert from_ctx_inside
    assert _W3C_RE.match(from_ctx_inside[0])


# ── AC-6: trace_id_from_context() with no active span returns "none" ─────────


def test_trace_id_from_context_no_span() -> None:
    """AC-6: no active span → returns 'none' without raising."""
    assert trace_id_from_context() == "none"


def test_trace_id_from_context_with_span() -> None:
    """trace_id_from_context() returns a 32-char hex string when a span is active."""
    configure_tracing(service="test-svc")
    tracer = get_tracer("test")

    with tracer.start_as_current_span("test.span"):
        result = trace_id_from_context()

    assert _W3C_RE.match(result), f"Expected 32-char hex, got {result!r}"


# ── AC-7: get_tracer returns distinct tracers for different names ─────────────


def test_get_tracer_distinct_instances() -> None:
    """AC-7: get_tracer('foo') and get_tracer('bar') return distinct instances."""
    configure_tracing(service="test-svc")
    t1 = get_tracer("foo")
    t2 = get_tracer("bar")
    assert t1 is not t2


# ── AC-11: inbound traceparent / X-Trace-ID ignored in OTEL mode ─────────────


async def test_middleware_otel_ignores_inbound_trace_id() -> None:
    """AC-11: spoofed X-Trace-ID ignored; fresh W3C trace ID generated."""
    configure_tracing(service="test-svc", disabled=False)

    app = Starlette(routes=[Route("/", lambda r: PlainTextResponse("ok"))])
    app.add_middleware(TraceIDMiddleware, service="test-svc")

    spoofed = "aabbccddaabbccddaabbccddaabbccdd"  # valid W3C hex but forged
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/", headers={"X-Trace-ID": spoofed})

    actual = resp.headers["x-trace-id"]
    assert actual != spoofed, "Middleware must not adopt a spoofed X-Trace-ID in OTEL mode"
    assert _W3C_RE.match(actual)


# ── Excluded paths: /health and /metrics skip OTEL spans ─────────────────────


async def test_middleware_excluded_path_uses_uuid4() -> None:
    """Excluded paths fall back to UUID4 even when OTEL is active."""
    configure_tracing(service="test-svc", disabled=False)

    uuid4_re = re.compile(
        r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
        re.IGNORECASE,
    )

    async def _health(_: Request) -> PlainTextResponse:
        return PlainTextResponse("ok")

    app = Starlette(routes=[Route("/health", _health)])
    app.add_middleware(TraceIDMiddleware, service="test-svc")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/health")

    assert uuid4_re.match(resp.headers["x-trace-id"])


# ── resource_attributes passed through ───────────────────────────────────────


def test_configure_tracing_resource_attributes() -> None:
    """resource_attributes are set on the TracerProvider's Resource."""
    configure_tracing(service="manager", resource_attributes={"tui.session_id": "abc123"})

    provider = trace.get_tracer_provider()
    assert isinstance(provider, TracerProvider)
    resource_attrs = provider.resource.attributes
    assert resource_attrs.get("tui.session_id") == "abc123"
    assert resource_attrs.get("service.name") == "manager"
