"""Tests for prometheus_telemetry.core.

Implements: memory/specs/020-shared-telemetry-package.md — AC-18
Covers all 16 test cases defined in §Test Strategy.
"""

from __future__ import annotations

import json
import logging
from io import StringIO
from typing import Any

import pytest
import structlog
from httpx import ASGITransport, AsyncClient
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import PlainTextResponse
from starlette.routing import Route

import prometheus_telemetry.core as _core
from prometheus_telemetry import TraceIDMiddleware, configure_logging, get_logger
from prometheus_telemetry.core import (
    _ensure_trace_id,
    _order_mandatory_fields,
)

# ── Helpers ───────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def reset_telemetry():
    """Reset module-level idempotency guard and structlog config between tests."""
    _core._CONFIGURED = False
    structlog.reset_defaults()
    structlog.contextvars.clear_contextvars()
    yield
    _core._CONFIGURED = False
    structlog.reset_defaults()
    structlog.contextvars.clear_contextvars()


def _capture_output(monkeypatch: pytest.MonkeyPatch) -> StringIO:
    """Redirect stdout to a StringIO buffer and return it."""
    buf = StringIO()
    monkeypatch.setattr("sys.stdout", buf)
    return buf


def _last_json(buf: StringIO) -> dict[str, Any]:
    """Return the last JSON line emitted to the buffer."""
    lines = [line for line in buf.getvalue().splitlines() if line.strip()]
    assert lines, "No log output captured"
    return json.loads(lines[-1])


# ── configure_logging ─────────────────────────────────────────────────────────


def test_configure_logging_service_field(monkeypatch: pytest.MonkeyPatch) -> None:
    """AC-4: emitted log has "service": <arg>."""
    buf = _capture_output(monkeypatch)
    configure_logging(service="test-svc")
    log = get_logger("test")
    log.info("hello")
    data = _last_json(buf)
    assert data["service"] == "test-svc"


def test_configure_logging_component_field(monkeypatch: pytest.MonkeyPatch) -> None:
    """AC-19 / test_configure_logging_component_field: component="api" appears in output."""
    buf = _capture_output(monkeypatch)
    configure_logging(service="manager", component="api")
    log = get_logger("test")
    log.info("hello")
    data = _last_json(buf)
    assert data["component"] == "api"
    # Must appear between service and event
    keys = list(data.keys())
    assert keys.index("component") == keys.index("service") + 1


def test_configure_logging_no_component(monkeypatch: pytest.MonkeyPatch) -> None:
    """AC-20: when no component arg, key must be absent (not null) from output."""
    buf = _capture_output(monkeypatch)
    configure_logging(service="gateway")
    log = get_logger("test")
    log.info("hello")
    data = _last_json(buf)
    assert "component" not in data


def test_configure_logging_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    """AC-5: second call is a no-op; no duplicate handlers registered."""
    _capture_output(monkeypatch)
    configure_logging(service="svc")
    root_handlers_after_first = len(logging.getLogger().handlers)
    configure_logging(service="svc")
    root_handlers_after_second = len(logging.getLogger().handlers)
    assert root_handlers_after_second == root_handlers_after_first


def test_configure_logging_env_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-6: LOG_LEVEL=DEBUG env var → debug events emitted."""
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    buf = _capture_output(monkeypatch)
    configure_logging(service="svc")
    log = get_logger("test")
    log.debug("debug-event")
    data = _last_json(buf)
    assert data["level"] == "debug"
    assert data["event"] == "debug-event"


def test_configure_logging_log_file(tmp_path: Any) -> None:
    """AC-7: log file created with correct permissions under a new directory."""
    import os
    import stat

    log_path = tmp_path / "sub" / "test.log"
    configure_logging(service="svc", log_file_path=str(log_path))
    log = get_logger("test")
    log.info("file-event")
    assert log_path.exists()
    mode = oct(stat.S_IMODE(os.stat(str(log_path)).st_mode))
    assert mode == oct(0o640)


def test_configure_logging_unwritable_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    """AC-8: unwritable log_file_path → service starts with stdout-only; warns."""
    buf = _capture_output(monkeypatch)
    bad_path = "/dev/null/cannot/write/here.log"
    configure_logging(service="svc", log_file_path=bad_path)
    log = get_logger("test")
    log.info("after-bad-path")
    output = buf.getvalue()
    assert "log_file_unavailable" in output


# ── _ensure_trace_id processor ────────────────────────────────────────────────


def test_ensure_trace_id_injects_none() -> None:
    """Processor adds trace_id="none" when the key is missing."""
    event_dict: dict[str, Any] = {"event": "test"}
    result = _ensure_trace_id(None, "info", event_dict)
    assert result["trace_id"] == "none"


def test_ensure_trace_id_preserves_existing() -> None:
    """Processor preserves an existing trace_id."""
    event_dict: dict[str, Any] = {"event": "test", "trace_id": "abc-123"}
    result = _ensure_trace_id(None, "info", event_dict)
    assert result["trace_id"] == "abc-123"


# ── _order_mandatory_fields processor ────────────────────────────────────────


def test_order_mandatory_fields_with_component() -> None:
    """Keys in order: timestamp → level → service → component → event → trace_id."""
    event_dict: dict[str, Any] = {
        "event": "test",
        "trace_id": "x",
        "service": "svc",
        "component": "api",
        "level": "info",
        "timestamp": "2026-01-01T00:00:00Z",
        "extra": "val",
    }
    result = _order_mandatory_fields(None, "info", event_dict)
    keys = list(result.keys())
    assert keys[:6] == ["timestamp", "level", "service", "component", "event", "trace_id"]


def test_order_mandatory_fields_without_component() -> None:
    """Keys in order: timestamp → level → service → event → trace_id (no component)."""
    event_dict: dict[str, Any] = {
        "event": "test",
        "trace_id": "x",
        "service": "svc",
        "level": "info",
        "timestamp": "2026-01-01T00:00:00Z",
    }
    result = _order_mandatory_fields(None, "info", event_dict)
    keys = list(result.keys())
    assert keys[:5] == ["timestamp", "level", "service", "event", "trace_id"]
    assert "component" not in keys


# ── TraceIDMiddleware ─────────────────────────────────────────────────────────


def _make_app(service: str) -> Starlette:
    async def _homepage(request: Request) -> PlainTextResponse:
        return PlainTextResponse(
            f"trace={request.state.trace_id}",
            headers={"X-Captured-Trace-ID": request.state.trace_id},
        )

    app = Starlette(routes=[Route("/", _homepage)])
    app.add_middleware(TraceIDMiddleware, service=service)
    return app


@pytest.fixture()
def app() -> Starlette:
    return _make_app("test-svc")


async def test_middleware_generates_uuid4(app: Starlette) -> None:
    """AC-9: no X-Trace-ID header → response contains a valid UUID4."""
    import re

    uuid4_re = re.compile(
        r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
        re.IGNORECASE,
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/")
    assert resp.status_code == 200
    assert uuid4_re.match(resp.headers["x-trace-id"])


async def test_middleware_adopts_valid_header(app: Starlette) -> None:
    """AC-10: valid UUID4 X-Trace-ID header → same value echoed in response."""
    tid = "12345678-1234-4234-8234-123456789abc"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/", headers={"X-Trace-ID": tid})
    assert resp.headers["x-trace-id"] == tid
    assert tid in resp.text


async def test_middleware_rejects_invalid_header(app: Starlette) -> None:
    """AC-11: non-UUID4 header → new UUID4 generated."""
    import re

    uuid4_re = re.compile(
        r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
        re.IGNORECASE,
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/", headers={"X-Trace-ID": "hacked"})
    assert uuid4_re.match(resp.headers["x-trace-id"])
    assert resp.headers["x-trace-id"] != "hacked"


async def test_middleware_response_header(app: Starlette) -> None:
    """AC: response always contains x-trace-id header."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/")
    assert "x-trace-id" in resp.headers


async def test_middleware_sets_request_state(app: Starlette) -> None:
    """AC: request.state.trace_id is accessible to route handlers."""
    import re

    uuid4_re = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/")
    # Response text is "trace=<trace_id>" — verify it looks like a UUID4
    tid_from_body = resp.text.replace("trace=", "")
    assert uuid4_re.match(tid_from_body)


async def test_middleware_clears_context(app: Starlette) -> None:
    """AC-12: after request completes, structlog context is empty."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.get("/")
    ctx = structlog.contextvars.get_contextvars()
    assert ctx == {}


async def test_middleware_service_param() -> None:
    """AC-9 / test_middleware_service_param: service="X" → logs carry service="X"."""
    captured: list[dict[str, Any]] = []

    async def _capture(request: Request) -> PlainTextResponse:
        captured.append(dict(structlog.contextvars.get_contextvars()))
        return PlainTextResponse("ok")

    svc_app = Starlette(routes=[Route("/", _capture)])
    svc_app.add_middleware(TraceIDMiddleware, service="my-svc")

    async with AsyncClient(transport=ASGITransport(app=svc_app), base_url="http://test") as client:
        await client.get("/")

    assert captured
    assert captured[0]["service"] == "my-svc"
