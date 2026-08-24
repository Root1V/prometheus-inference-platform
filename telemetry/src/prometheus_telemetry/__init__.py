"""prometheus-telemetry — shared structured observability for the Prometheus platform.

Public API:
    configure_logging       — structlog + stdlib bridge, idempotent
    get_logger              — returns a bound structlog logger
    TraceIDMiddleware       — ASGI middleware: inject / echo X-Trace-ID header
    bind_contextvars        — re-export from structlog.contextvars
    clear_contextvars       — re-export from structlog.contextvars
    configure_tracing       — OTEL SDK setup (BatchSpanProcessor + OTLP/HTTP)
    get_tracer              — return a Tracer bound to an instrumentation scope
    trace_id_from_context   — extract the active W3C trace ID (32-char hex) or "none"

Implements: memory/specs/020-shared-telemetry-package.md
Implements: memory/specs/022-opentelemetry-sdk-instrumentation.md — G-1, G-2
"""

from structlog.contextvars import bind_contextvars, clear_contextvars

from .core import (
    TraceIDMiddleware,
    configure_logging,
    get_logger,
)
from .tracing import (
    configure_tracing,
    get_tracer,
    trace_id_from_context,
)

__all__ = [
    "TraceIDMiddleware",
    "configure_logging",
    "get_logger",
    "bind_contextvars",
    "clear_contextvars",
    "configure_tracing",
    "get_tracer",
    "trace_id_from_context",
]
