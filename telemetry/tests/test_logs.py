"""Our log lines leave the process — PRM-171.

Argus measured zero log records in seven days against 45,066 spans (A-35). The
audit *event* reached them because it hangs off a span; the audit *line* did not,
because `configure_logging` uses structlog's `PrintLoggerFactory` — our events go
straight to stdout and never pass through stdlib `logging`, so there was nothing
for a handler to attach to.

These pin the two halves that failed: that the bridge is built at all, and that
building it never costs a log line.
"""

from __future__ import annotations

import logging

import pytest

from prometheus_telemetry import logs


@pytest.fixture(autouse=True)
def _reset():
    logs._CONFIGURED = False
    logs._LOGS_ACTIVE = False
    logs._logger_provider = None
    logs._bridge = None
    yield
    logs._CONFIGURED = False
    logs._LOGS_ACTIVE = False
    logs._logger_provider = None
    logs._bridge = None


def test_no_collector_means_no_exporter(monkeypatch):
    """Same gate as tracing: a process with no collector behaves exactly as it did,
    which is what RM-94 and the runbook fix made load-bearing."""
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    assert logs.configure_logs(service="t") is False
    assert logs.logs_active() is False


def test_disabled_sdk_is_respected(monkeypatch):
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://127.0.0.1:4318")
    monkeypatch.setenv("OTEL_SDK_DISABLED", "true")
    assert logs.configure_logs(service="t") is False


def test_an_endpoint_builds_the_bridge(monkeypatch):
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://127.0.0.1:4318")
    assert logs.configure_logs(service="t") is True
    assert logs.logs_active() is True
    # Its own logger, not the root: stdout is written once, by structlog.
    assert logs._bridge is not None
    assert logs._bridge.propagate is False
    assert len(logs._bridge.handlers) == 1


def test_the_processor_is_a_pass_through_when_inactive():
    event = {"event": "x", "service": "t"}
    assert logs.export_to_otel(None, "info", event) is event


def test_the_processor_returns_the_event_untouched(monkeypatch):
    """stdout and the rotating file keep their exact format; only a copy leaves."""
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://127.0.0.1:4318")
    logs.configure_logs(service="t")
    event = {"event": "audit.admin_action", "status_code": 200, "trace_id": "abc"}
    before = dict(event)
    assert logs.export_to_otel(None, "info", event) == before


def test_what_the_bridge_receives(monkeypatch):
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://127.0.0.1:4318")
    logs.configure_logs(service="t")
    captured: list[logging.LogRecord] = []

    class _Capture(logging.Handler):
        def emit(self, record):
            captured.append(record)

    logs._bridge.handlers = [_Capture()]
    logs.export_to_otel(
        None,
        "warning",
        {"event": "audit.admin_action", "status_code": 403, "argus.outcome": "error"},
    )

    assert len(captured) == 1
    rec = captured[0]
    # The event name is the body, which is where a reader looks for it.
    assert rec.getMessage() == "audit.admin_action"
    assert rec.levelno == logging.WARNING
    assert rec.status_code == 403
    assert getattr(rec, "argus.outcome") == "error"


def test_a_reserved_key_is_prefixed_not_dropped(monkeypatch):
    """`extra` raises on a key that collides with a stdlib LogRecord attribute, and
    the value is still a fact about the event — losing it silently is what this
    module exists to stop."""
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://127.0.0.1:4318")
    logs.configure_logs(service="t")
    captured: list[logging.LogRecord] = []

    class _Capture(logging.Handler):
        def emit(self, record):
            captured.append(record)

    logs._bridge.handlers = [_Capture()]
    logs.export_to_otel(None, "info", {"event": "e", "message": "collides", "module": "also"})

    rec = captured[0]
    assert rec.__dict__["attr.message"] == "collides"
    assert rec.__dict__["attr.module"] == "also"


def test_an_export_failure_never_costs_the_line(monkeypatch):
    """Best-effort per event, deliberately — the opposite of the setup path, which
    raises rather than reporting a success it did not achieve."""
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://127.0.0.1:4318")
    logs.configure_logs(service="t")

    class _Explode(logging.Handler):
        def emit(self, record):
            raise RuntimeError("collector down")

    logs._bridge.handlers = [_Explode()]
    logs._bridge.raiseExceptions = False
    event = {"event": "audit.admin_action"}
    assert logs.export_to_otel(None, "info", event) == {"event": "audit.admin_action"}


def test_the_processor_is_in_the_shared_chain():
    """The guard for the actual regression: a chain without it is seven days of
    silence that nothing reports."""
    from prometheus_telemetry import core

    assert logs.export_to_otel in core._SHARED_PROCESSORS
    # Last, so what is exported is the finished event — trace id and field order
    # included.
    assert core._SHARED_PROCESSORS[-1] is logs.export_to_otel
