"""OpenTelemetry SDK setup for the Prometheus platform.

Public symbols:
    configure_tracing       — configure OTEL SDK, BatchSpanProcessor, OTLP/HTTP exporter
    get_tracer              — return a Tracer bound to an instrumentation scope
    trace_id_from_context   — extract the active W3C trace ID (32-char hex) or "none"

Implements: memory/specs/022-opentelemetry-sdk-instrumentation.md — G-1 to G-6, AC-1 to AC-7
"""

from __future__ import annotations

import os
from typing import Any

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

# ── Module-level idempotency guard (AC-4) ────────────────────────────────────
_CONFIGURED = False

# Public flag — TraceIDMiddleware checks this to decide whether to create OTEL spans.
_TRACING_ACTIVE = False

_DEFAULT_ENDPOINT = "http://tempo:4318"


def configure_tracing(
    service: str,
    endpoint: str | None = None,
    disabled: bool = False,
    instrument_httpx: bool = False,
    resource_attributes: dict[str, Any] | None = None,
) -> None:
    """Configure the OTEL SDK, BatchSpanProcessor, and OTLP/HTTP exporter. Idempotent.

    Args:
        service:              Service name bound to every span as ``service.name``.
        endpoint:             OTLP/HTTP base URL. Falls back to
                              ``OTEL_EXPORTER_OTLP_ENDPOINT`` env var, then
                              ``http://tempo:4318``.
        disabled:             When True (or ``OTEL_SDK_DISABLED=true``), install a
                              ``NoOpTracerProvider`` so all span operations are no-ops.
                              No network connections to Tempo are attempted.
        instrument_httpx:     When True, activate
                              ``opentelemetry-instrumentation-httpx`` so that all HTTPX
                              calls automatically carry a ``traceparent`` header and are
                              recorded as child spans.  Only needed by the gateway.
        resource_attributes:  Extra ``Resource`` attributes merged onto every span
                              (e.g. ``{"tui.session_id": uuid4_str}``).

    Implements: memory/specs/022-opentelemetry-sdk-instrumentation.md — AC-1, AC-2, AC-4, G-4
    """
    global _CONFIGURED, _TRACING_ACTIVE
    if _CONFIGURED:
        return  # AC-4: idempotent — second call is a no-op
    _CONFIGURED = True

    _disabled = disabled or os.environ.get("OTEL_SDK_DISABLED", "false").lower() == "true"

    if _disabled:
        # AC-2: disabled → NoOpTracerProvider, no network connections
        trace.set_tracer_provider(trace.NoOpTracerProvider())  # type: ignore[arg-type]
        return

    # Build Resource (service.name + optional extras)
    service_name = os.environ.get("OTEL_SERVICE_NAME", service)
    attrs: dict[str, Any] = {"service.name": service_name}
    if resource_attributes:
        attrs.update(resource_attributes)
    resource = Resource(attributes=attrs)

    # Build OTLP/HTTP exporter
    _endpoint = (
        endpoint
        or os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
        or _DEFAULT_ENDPOINT
    )
    otlp_url = f"{_endpoint.rstrip('/')}/v1/traces"
    exporter = OTLPSpanExporter(endpoint=otlp_url)

    # Build TracerProvider with BatchSpanProcessor (AC-1, G-4)
    # BatchSpanProcessor exports off the critical path — never blocks requests (G-5).
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)

    # Optional: activate httpx auto-instrumentation for outbound calls (G-10)
    if instrument_httpx:
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

        HTTPXClientInstrumentor().instrument()

    _TRACING_ACTIVE = True


def get_tracer(name: str = "prometheus") -> trace.Tracer:
    """Return a tracer bound to the given instrumentation scope.

    Implements: memory/specs/022-opentelemetry-sdk-instrumentation.md — AC-7
    """
    return trace.get_tracer(name)


def trace_id_from_context() -> str:
    """Return the active span's W3C trace ID (32-character lowercase hex).

    Returns ``"none"`` when no active span exists (background tasks, tests, TUI
    actions before configure_tracing() is called).

    Implements: memory/specs/022-opentelemetry-sdk-instrumentation.md — AC-5, AC-6
    """
    span = trace.get_current_span()
    ctx = span.get_span_context()
    if ctx is not None and ctx.is_valid:
        return format(ctx.trace_id, "032x")
    return "none"
