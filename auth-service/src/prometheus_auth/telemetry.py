"""Structured observability for the Prometheus Auth Service.

Thin shim over prometheus_telemetry. Re-exports the shared core so that
existing relative imports in routers remain valid unchanged.

Implements: memory/specs/020-shared-telemetry-package.md (migration shim)
Previously: memory/specs/018-observability-telemetry.md
  AC-2, AC-4, AC-5, AC-9, AC-16, AC-17, AC-18, AC-24, AC-25
"""

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
]
