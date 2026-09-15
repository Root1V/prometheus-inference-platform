"""The SERVER span, and what it actually carries.

Argus A-19: our server spans were named `http.get` and carried no attributes at
all. For auth-service and manager-api that meant Argus knew how many requests
arrived and nothing else — no route, no status code, no latency per endpoint,
so no RED metrics and no per-endpoint SLO.

These tests assert the attributes, not the span count. A span that exists and
says nothing is the exact failure being fixed, and counting spans would pass
through it.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from prometheus_telemetry import TraceIDMiddleware, instrument_fastapi


@pytest.fixture
def spans(monkeypatch):
    """A real provider whose spans land in memory, with tracing marked active."""
    from prometheus_telemetry import tracing as _tracing

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    previous = trace.get_tracer_provider()
    trace._TRACER_PROVIDER = provider  # type: ignore[attr-defined]
    monkeypatch.setattr(_tracing, "_TRACING_ACTIVE", True)
    monkeypatch.setattr(_tracing, "_ASGI_INSTRUMENTED", False)
    try:
        yield exporter
    finally:
        trace._TRACER_PROVIDER = previous  # type: ignore[attr-defined]


def _app() -> FastAPI:
    app = FastAPI()

    @app.get("/v1/things/{thing_id}")
    async def read_thing(thing_id: str) -> dict[str, str]:
        return {"id": thing_id}

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    app.add_middleware(TraceIDMiddleware, service="test-service")
    return app


def _server_spans(exporter):
    return [s for s in exporter.get_finished_spans() if s.kind == trace.SpanKind.SERVER]


async def _get(app, path, **kw):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        return await c.get(path, **kw)


# ── What Argus was looking at ──────────────────────────────────────────────


async def test_without_instrumentation_the_server_span_says_nothing(spans):
    """The behaviour being fixed, pinned so the fix cannot be mistaken for
    something that was already working."""
    app = _app()  # no instrument_fastapi()
    await _get(app, "/v1/things/abc")

    server = _server_spans(spans)
    assert len(server) == 1
    assert server[0].name == "http.get"
    assert dict(server[0].attributes or {}) == {}


# ── After A-19 ─────────────────────────────────────────────────────────────


async def test_the_server_span_carries_route_method_and_status(spans):
    app = _app()
    instrument_fastapi(app)
    resp = await _get(app, "/v1/things/abc")
    assert resp.status_code == 200

    server = _server_spans(spans)
    assert len(server) == 1, "exactly one SERVER span — not one nested in another"
    attrs = dict(server[0].attributes or {})

    # The templated route, not the concrete path: "/v1/things/abc" as a span
    # name would make every id its own endpoint and no aggregate possible.
    assert attrs.get("http.route") == "/v1/things/{thing_id}"
    assert "abc" not in server[0].name
    assert attrs.get("http.response.status_code") == 200 or attrs.get("http.status_code") == 200
    assert attrs.get("http.request.method") == "GET" or attrs.get("http.method") == "GET"


async def test_an_error_status_reaches_the_span(spans):
    """Without this there is no way to see a route degrading."""
    app = _app()
    instrument_fastapi(app)
    resp = await _get(app, "/v1/nope")
    assert resp.status_code == 404

    attrs = dict(_server_spans(spans)[0].attributes or {})
    assert attrs.get("http.response.status_code") == 404 or attrs.get("http.status_code") == 404


async def test_trace_id_header_still_comes_back(spans):
    """TraceIDMiddleware's own job. It stops opening a span but must keep
    returning the id, which is what every log line is correlated by."""
    app = _app()
    instrument_fastapi(app)
    resp = await _get(app, "/v1/things/abc")

    trace_id = resp.headers["x-trace-id"]
    assert len(trace_id) == 32
    assert trace_id == format(_server_spans(spans)[0].context.trace_id, "032x")


async def test_health_still_produces_no_span(spans):
    """RM-95/P-13: probe traffic was 57% of everything we sent Argus. Handing
    the span to the ASGI instrumentation must not quietly bring that back."""
    app = _app()
    instrument_fastapi(app)
    await _get(app, "/health")

    assert _server_spans(spans) == []


# ── The guarantee A-19 asked us to leave intact ────────────────────────────


async def test_an_inbound_traceparent_is_still_ignored(spans):
    """AC-11 / OWASP A03. The ASGI instrumentation adopts a caller's context
    through the global propagator by default, so instrument_fastapi() installs
    a no-op one. Without that, anyone could hand us a trace id and graft their
    own spans onto our traces.
    """
    app = _app()
    instrument_fastapi(app)
    forged = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
    await _get(app, "/v1/things/abc", headers={"traceparent": forged})

    span = _server_spans(spans)[0]
    assert format(span.context.trace_id, "032x") != "4bf92f3577b34da6a3ce929d0e0e4736"
    assert span.parent is None, "a fresh root, not a child of whatever the caller claimed"
