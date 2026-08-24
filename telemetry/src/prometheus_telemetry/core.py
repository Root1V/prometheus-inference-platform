"""Shared observability core for the Prometheus platform.

Provides:
  - _SHARED_PROCESSORS  — structlog processor chain
  - configure_logging() — structlog + stdlib bridge, idempotent
  - get_logger()        — bound structlog logger factory
  - TraceIDMiddleware   — ASGI middleware for X-Trace-ID propagation

Key ordering in JSON output:
  timestamp → level → service → component (if present) → event → trace_id → ...

Implements: memory/specs/020-shared-telemetry-package.md
  AC-1 to AC-20
"""

from __future__ import annotations

import contextlib
import logging
import logging.handlers
import os
import re as _re
import stat
import sys
import uuid
from typing import Any

import structlog
from starlette.types import ASGIApp, Receive, Scope, Send

# ── Module-level idempotency guard (AC-5) ─────────────────────────────────────
_CONFIGURED = False

# ── UUID4 validation regex ────────────────────────────────────────────────────
_UUID_RE = _re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    _re.IGNORECASE,
)

# Paths that are never traced (health probes, Prometheus metrics scrape target).
# Avoids flooding Tempo with infrastructure noise.
# See: memory/specs/022-opentelemetry-sdk-instrumentation.md — Non-Goals
_DEFAULT_EXCLUDED_PATHS: frozenset[str] = frozenset({"/health", "/metrics"})


# ── Custom structlog processors ───────────────────────────────────────────────


def _is_valid_uuid4(value: str) -> bool:
    return bool(_UUID_RE.match(value))


def _is_valid_w3c_trace_id(value: str) -> bool:
    """Return True if *value* is a valid W3C 32-char lowercase hex trace ID."""
    return bool(_re.match(r"^[0-9a-f]{32}$", value))


def _ensure_trace_id(logger: Any, method: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    """Inject trace_id="none" when no request/operation context is active."""
    if "trace_id" not in event_dict:
        event_dict["trace_id"] = "none"
    return event_dict


def _order_mandatory_fields(logger: Any, method: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    """Reorder JSON keys so mandatory fields appear first, in spec order.

    Order: timestamp → level → service → component (if present) → event → trace_id → rest
    """
    ordered: dict[str, Any] = {}
    for key in ("timestamp", "level", "service", "component", "event", "trace_id"):
        if key in event_dict:
            ordered[key] = event_dict.pop(key)
    ordered.update(event_dict)
    return ordered


# ── Shared processor chain ────────────────────────────────────────────────────

_SHARED_PROCESSORS: list[Any] = [
    structlog.contextvars.merge_contextvars,
    structlog.stdlib.add_log_level,
    structlog.processors.TimeStamper(fmt="iso", utc=False, key="timestamp"),
    structlog.processors.StackInfoRenderer(),
    structlog.processors.format_exc_info,
    _ensure_trace_id,
    _order_mandatory_fields,
]


# ── configure_logging ─────────────────────────────────────────────────────────


def configure_logging(
    service: str,
    component: str | None = None,
    log_level: str | None = None,
    log_file_path: str | None = None,
    log_max_bytes: int | None = None,
    log_backup_count: int | None = None,
) -> None:
    """Configure structlog + stdlib bridge for a Prometheus service.

    Idempotent — safe to call from multiple entry points and tests.
    Second call is a no-op; tests reset state with:
        import prometheus_telemetry.core as _c; _c._CONFIGURED = False

    Args:
        service:          Service name bound to every log event (required).
        component:        Optional sub-component label (e.g. "api", "tui").
                          When provided, appears between "service" and "event".
                          When absent, the field is not emitted at all.
        log_level:        Logging level string.  Falls back to LOG_LEVEL env var,
                          then "INFO".
        log_file_path:    Optional path for a rotating JSONL file.  Falls back
                          to LOG_FILE_PATH env var.
        log_max_bytes:    Max bytes per log file.  Falls back to LOG_MAX_BYTES
                          env var, then 10 MB.
        log_backup_count: Number of backup files.  Falls back to LOG_BACKUP_COUNT
                          env var, then 5.

    Implements: memory/specs/020-shared-telemetry-package.md — AC-4 to AC-8.
    """
    global _CONFIGURED
    if _CONFIGURED:
        return
    _CONFIGURED = True

    _log_level = log_level or os.environ.get("LOG_LEVEL", "INFO")
    _log_file_path = log_file_path or os.environ.get("LOG_FILE_PATH") or None
    _log_max_bytes = log_max_bytes or int(os.environ.get("LOG_MAX_BYTES", "10485760"))
    _log_backup_count = log_backup_count or int(os.environ.get("LOG_BACKUP_COUNT", "5"))

    numeric_level = getattr(logging, _log_level.upper(), logging.INFO)

    # ── Configure structlog ───────────────────────────────────────────────────
    structlog.configure(
        wrapper_class=structlog.make_filtering_bound_logger(numeric_level),
        processors=[*_SHARED_PROCESSORS, structlog.processors.JSONRenderer()],
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # Bind static fields for every log event in this process
    ctx: dict[str, Any] = {"service": service}
    if component is not None:
        ctx["component"] = component
    structlog.contextvars.bind_contextvars(**ctx)

    # ── Stdlib bridge: uvicorn / httpx / other stdlib logs → JSON ────────────
    formatter = structlog.stdlib.ProcessorFormatter(
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            *_SHARED_PROCESSORS,
            structlog.processors.JSONRenderer(),
        ],
        foreign_pre_chain=_SHARED_PROCESSORS,
    )

    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.setFormatter(formatter)
    handlers: list[logging.Handler] = [stdout_handler]

    # ── Optional rotating file handler ────────────────────────────────────────
    _file_warning: str | None = None
    if _log_file_path:
        import pathlib

        try:
            path = pathlib.Path(_log_file_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            file_handler = logging.handlers.RotatingFileHandler(
                str(path),
                maxBytes=_log_max_bytes,
                backupCount=_log_backup_count,
                encoding="utf-8",
            )
            # AC-Security: 0640 permissions on log file (owner rw, group r)
            with contextlib.suppress(OSError):
                os.chmod(str(path), stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP)
            file_handler.setFormatter(formatter)
            handlers.append(file_handler)
        except (OSError, PermissionError) as exc:
            _file_warning = str(exc)

    # ── Apply to root stdlib logger ───────────────────────────────────────────
    root = logging.getLogger()
    root.setLevel(numeric_level)
    root.handlers.clear()
    for h in handlers:
        root.addHandler(h)

    # Suppress noisy third-party loggers
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)

    # Emit warning after full configuration if file path was unwritable
    if _file_warning is not None:
        log = structlog.get_logger()
        log.warning("log_file_unavailable", path=_log_file_path, reason=_file_warning)


# ── get_logger ────────────────────────────────────────────────────────────────


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """Return a structlog BoundLogger. Drop-in for logging.getLogger(__name__).

    Implements: memory/specs/020-shared-telemetry-package.md — AC-4.
    """
    return structlog.get_logger(name)  # type: ignore[no-any-return]


# ── TraceIDMiddleware ─────────────────────────────────────────────────────────


class TraceIDMiddleware:
    """ASGI middleware: read or generate trace_id; bind to structlog context.

    Legacy mode (OTEL not configured):
    - Adopts X-Trace-ID header when present and a valid UUID4.
    - Generates a new UUID4 otherwise.

    OTEL mode (configure_tracing() has been called with a real provider):
    - Inbound X-Trace-ID and W3C ``traceparent`` headers are ignored — a fresh
      root span is always started to prevent trace context injection (AC-11,
      memory/specs/022 OWASP A03).
    - Creates a root SERVER span for every non-excluded path; the trace ID is
      extracted from the span using ``trace_id_from_context()`` (AC-5).
    - Paths in ``excluded_paths`` (default: /health, /metrics) are skipped —
      no span is created and the legacy UUID4 fallback is used instead.

    In both modes:
    - Returns X-Trace-ID in every response.
    - Sets request.state.trace_id for route handlers.
    - Clears per-request structlog context after response (test isolation).

    Implements: memory/specs/020-shared-telemetry-package.md — AC-9 to AC-13
    Implements: memory/specs/022-opentelemetry-sdk-instrumentation.md — AC-5, AC-11, G-3
    """

    def __init__(
        self,
        app: ASGIApp,
        service: str,
        excluded_paths: frozenset[str] | None = None,
    ) -> None:
        self.app = app
        self._service = service
        self._excluded_paths = (
            excluded_paths if excluded_paths is not None else _DEFAULT_EXCLUDED_PATHS
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        from starlette.requests import Request

        request = Request(scope)

        # Clear stale context from previous requests in this coroutine
        structlog.contextvars.clear_contextvars()

        # Re-bind static fields cleared above
        structlog.contextvars.bind_contextvars(service=self._service)

        # ── Decide which trace-ID strategy to use ─────────────────────────
        from .tracing import _TRACING_ACTIVE, get_tracer, trace_id_from_context

        use_otel = _TRACING_ACTIVE and request.url.path not in self._excluded_paths

        if use_otel:
            # OTEL mode: start a root SERVER span, extract the W3C trace ID.
            # Inbound X-Trace-ID / traceparent headers are intentionally ignored
            # to prevent external injection of forged trace contexts (AC-11).
            from opentelemetry.trace import SpanKind

            tracer = get_tracer("prometheus_telemetry")
            # start_as_current_span propagates context into the awaited call below.
            with tracer.start_as_current_span(
                f"http.{request.method.lower()}",
                kind=SpanKind.SERVER,
            ):
                trace_id = trace_id_from_context()  # AC-5: 32-char hex
                structlog.contextvars.bind_contextvars(trace_id=trace_id)
                request.state.trace_id = trace_id

                async def _send_otel(message: Any) -> None:
                    if message["type"] == "http.response.start":
                        headers = list(message.get("headers", []))
                        headers.append((b"x-trace-id", trace_id.encode()))
                        message = {**message, "headers": headers}
                    await send(message)

                try:
                    await self.app(scope, receive, _send_otel)
                finally:
                    structlog.contextvars.clear_contextvars()
        else:
            # Legacy mode: adopt valid incoming UUID4 or generate a new one.
            raw = request.headers.get("X-Trace-ID", "")
            trace_id = raw if _is_valid_uuid4(raw) else str(uuid.uuid4())
            structlog.contextvars.bind_contextvars(trace_id=trace_id)
            request.state.trace_id = trace_id

            async def _send_legacy(message: Any) -> None:
                if message["type"] == "http.response.start":
                    headers = list(message.get("headers", []))
                    headers.append((b"x-trace-id", trace_id.encode()))
                    message = {**message, "headers": headers}
                await send(message)

            try:
                await self.app(scope, receive, _send_legacy)
            finally:
                structlog.contextvars.clear_contextvars()
