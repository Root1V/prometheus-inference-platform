"""Our log lines leave the process — PRM-171.

Implements: docs/roadmap.md — PRM-171.

Argus measured it and told us (`A-35`): **zero log records in seven days**, against
45,066 spans in the same window. The audit *event* arrived — it hangs off a span —
and the audit **line** did not.

Their hypothesis was right and the cause is one line of our own configuration:
`configure_logging` uses structlog's `PrintLoggerFactory`, so our `logger.info()`
calls are written straight to stdout and never pass through stdlib `logging`. There
was nothing for a handler to attach to, and their agent has no file receiver — so
the line existed, was correct, and lived in the terminal of whoever started the
process.

That emptied something we had just built. PRM-164 put the correlation ids on that
line precisely so it could be joined to a trace, and PRM-162 wrote down why the two
payloads differ: *"the event hangs off its span, the log line reaches no span"*. A
line that reaches no span and no collector reaches nobody. The row in the gateway's
own table is still the record of truth (PRM-157 settled that, and it has not
changed); what was missing is the half Argus is for — search, correlation and
alerting on patterns.

## Why a structlog processor and not the SDK's `init()`

They offered `argus.init()`, which installs a stdlib logging handler. Two reasons it
is not what this does:

* **It would see nothing.** With `PrintLoggerFactory` our own events never reach
  stdlib logging, so a handler there captures uvicorn and httpx and none of ours.
* **It configures providers we already configure.** `configure_tracing` and
  `configure_metrics` install their own, by hand and deliberately (RM-95: emitted
  by hand rather than taking a dependency). A second opinionated setup is how two
  providers start disagreeing about which one is installed.

So the export is a **processor in the chain that already exists**. It sees the final
event dict, emits it, and returns it untouched — stdout keeps its exact format and
the rotating file handler keeps its exact content. Nothing about what we log
changes; only where a copy of it goes.

## Through `LoggingHandler`, not a hand-built record

The first version constructed `sdk._logs.LogRecord` directly and exported nothing:
that class is not in the public surface of `opentelemetry-sdk` 1.44 — it lives under
`_internal` — so the import raised, the processor swallowed it, and zero records
left the process while `configure_logs` reported success. The swallow was right for
a log line and wrong for a setup failure; the measurement is what found it, which is
the only thing that would have.

So the export goes through `LoggingHandler`, which *is* public: it builds the record,
maps the severity and attaches the current span's trace and span ids. A dedicated
stdlib logger carries it with `propagate=False`, so nothing reaches the root handlers
and stdout is written exactly once, by structlog, as before.

## Correlation comes free

The record is emitted inside the request's span context, so the OTel log record
carries the real `trace_id` and `span_id` — which is what makes a log line joinable
to a trace in their store, and is the thing `A-35` said the audit trail needs to be
useful at all.

## Off unless a collector is configured

Same gate as tracing: no `OTEL_EXPORTER_OTLP_ENDPOINT`, no exporter, and the
processor becomes a pass-through. A process with no collector behaves exactly as it
did — which is what RM-94 and the runbook fix (PRM-165's neighbour) made load-bearing.
"""

from __future__ import annotations

import logging
import os
from typing import Any

_CONFIGURED = False
_LOGS_ACTIVE = False
_logger_provider: Any = None
_bridge: logging.Logger | None = None

# structlog's method name -> stdlib level. `LoggingHandler` maps the rest.
_LEVELS = {
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "warning": logging.WARNING,
    "warn": logging.WARNING,
    "error": logging.ERROR,
    "exception": logging.ERROR,
    "critical": logging.CRITICAL,
    "fatal": logging.CRITICAL,
}

# Keys that would collide with a stdlib LogRecord's own attributes. Passing one in
# `extra` raises, so they are prefixed rather than dropped — the value is still a
# fact about the event and losing it silently is what this module exists to stop.
_RESERVED = frozenset(
    {
        "args",
        "asctime",
        "created",
        "exc_info",
        "exc_text",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "module",
        "msecs",
        "msg",
        "message",
        "name",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "stack_info",
        "taskName",
        "thread",
        "threadName",
    }
)


def configure_logs(
    service: str,
    endpoint: str | None = None,
    resource_attributes: dict[str, Any] | None = None,
    disabled: bool = False,
) -> bool:
    """Install the OTLP log exporter. Returns True when logs will be exported.

    Idempotent, like the other two. A no-op without an endpoint, so a process with
    no collector keeps behaving exactly as before.

    **Setup failures are not swallowed.** The per-event export is best-effort — a
    line that cannot be exported must still be printed — but a bridge that cannot be
    built at all is the thing Argus measured as silence for seven days, so it raises
    rather than reporting success it did not achieve.
    """
    global _CONFIGURED, _LOGS_ACTIVE, _logger_provider, _bridge
    if _CONFIGURED:
        return _LOGS_ACTIVE
    _CONFIGURED = True

    if disabled or os.environ.get("OTEL_SDK_DISABLED", "").lower() == "true":
        return False
    _endpoint = endpoint or os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
    if not _endpoint:
        return False

    from opentelemetry._logs import set_logger_provider
    from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
    from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
    from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
    from opentelemetry.sdk.resources import Resource

    attrs: dict[str, Any] = {"service.name": service}
    if resource_attributes:
        attrs.update(resource_attributes)

    _logger_provider = LoggerProvider(resource=Resource.create(attrs))
    _logger_provider.add_log_record_processor(
        BatchLogRecordProcessor(OTLPLogExporter(endpoint=f"{_endpoint.rstrip('/')}/v1/logs"))
    )
    set_logger_provider(_logger_provider)

    # A logger of our own, so the handler sees our events and nothing else, and
    # `propagate=False` so they never reach the root handlers — stdout is written
    # once, by structlog, exactly as before.
    bridge = logging.getLogger("prometheus_telemetry.otel_bridge")
    bridge.setLevel(logging.DEBUG)
    bridge.propagate = False
    bridge.handlers = [LoggingHandler(level=logging.DEBUG, logger_provider=_logger_provider)]
    _bridge = bridge

    _LOGS_ACTIVE = True
    return True


def logs_active() -> bool:
    return _LOGS_ACTIVE


def force_flush(timeout_millis: int = 5000) -> None:
    """Flush pending records — for tests and for a process shutting down."""
    if _logger_provider is not None:
        _logger_provider.force_flush(timeout_millis)


def export_to_otel(logger: Any, method_name: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    """structlog processor: emit a copy of this event to the collector.

    Returns `event_dict` untouched — stdout and the rotating file keep their exact
    format. A failure here is swallowed on purpose: telemetry never costs a log
    line, and a line that cannot be exported is still a line that must be printed.
    """
    if not _LOGS_ACTIVE or _bridge is None:
        return event_dict
    try:
        extra = {
            (f"attr.{k}" if k in _RESERVED else k): (
                v if isinstance(v, (str, bool, int, float)) else str(v)
            )
            for k, v in event_dict.items()
            if k != "event"
        }
        _bridge.log(
            _LEVELS.get(method_name, logging.INFO),
            str(event_dict.get("event", "")),
            extra=extra,
        )
    except Exception:  # noqa: BLE001 — see the docstring
        logging.getLogger(__name__).debug("otel log export failed", exc_info=True)
    return event_dict
