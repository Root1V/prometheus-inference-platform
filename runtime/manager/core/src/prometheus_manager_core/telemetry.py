"""Structured observability for the Prometheus Manager.

Thin shim over prometheus_telemetry. Re-exports the shared core so existing
callers (prometheus_manager_api, prometheus_manager_tui) import from this
module unchanged.

Implements: memory/specs/020-shared-telemetry-package.md (migration shim)
Previously: memory/specs/018-observability-telemetry.md
  AC-3, AC-4, AC-5, AC-16, AC-18, AC-24, AC-25, AC-28
"""

from __future__ import annotations

import uuid

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
    "new_trace_id",
    "trace_id_from_context",
]


def new_trace_id() -> str:
    """Generate a new UUID4 to use as trace_id for a CLI lifecycle operation."""
    return str(uuid.uuid4())
