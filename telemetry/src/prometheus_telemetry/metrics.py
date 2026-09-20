"""OpenTelemetry metrics setup for the Prometheus platform.

Public symbols:
    configure_metrics   — configure the MeterProvider, PeriodicExportingMetricReader
                          and OTLP/HTTP metric exporter

The sibling of ``tracing.configure_tracing``, and deliberately its mirror image:
same idempotency guard, same ``OTEL_SDK_DISABLED`` handling, same "no endpoint
configured means export nowhere" convention (RM-92).

Implements: docs/roadmap.md — PRM-131
"""

from __future__ import annotations

import os
from typing import Any

from opentelemetry import metrics
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource

_CONFIGURED = False

# True once metric points actually leave this process. The mirror of
# tracing._TRACING_ACTIVE, and false for the same two reasons: the SDK is
# disabled, or no collector is configured.
_METRICS_ACTIVE = False


def configure_metrics(
    service: str,
    endpoint: str | None = None,
    disabled: bool = False,
    resource_attributes: dict[str, Any] | None = None,
) -> bool:
    """Install a MeterProvider exporting over OTLP/HTTP. Idempotent.

    Args:
        service:              Service name bound to every metric as ``service.name``.
        endpoint:             OTLP/HTTP base URL. Falls back to
                              ``OTEL_EXPORTER_OTLP_ENDPOINT``; unset means export
                              nowhere.
        disabled:             When True (or ``OTEL_SDK_DISABLED=true``), install a
                              ``NoOpMeterProvider`` — no instruments, no connections.
        resource_attributes:  Extra ``Resource`` attributes merged onto every metric.

    Returns True when points will actually be exported.
    """
    global _CONFIGURED, _METRICS_ACTIVE
    if _CONFIGURED:
        return _METRICS_ACTIVE
    _CONFIGURED = True

    if disabled or os.environ.get("OTEL_SDK_DISABLED", "false").lower() == "true":
        metrics.set_meter_provider(metrics.NoOpMeterProvider())
        return False

    # Resource.create(), not Resource(attributes=...) — the direct constructor
    # skips OTEL_RESOURCE_ATTRIBUTES, which is how deployment injects
    # service.namespace and argus.component.role. Same reasoning, and the same
    # silent failure mode, as the comment in tracing.configure_tracing().
    service_name = os.environ.get("OTEL_SERVICE_NAME", service)
    attrs: dict[str, Any] = {"service.name": service_name}
    if resource_attributes:
        attrs.update(resource_attributes)
    resource = Resource.create(attrs)

    _endpoint = endpoint or os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT") or ""
    if not _endpoint:
        # No collector. The provider is still installed so instruments are real
        # and recording costs what it will cost in production, but nothing
        # ships. Not OTEL_SDK_DISABLED, which replaces the provider itself.
        metrics.set_meter_provider(MeterProvider(resource=resource))
        return False

    reader = PeriodicExportingMetricReader(
        OTLPMetricExporter(endpoint=f"{_endpoint.rstrip('/')}/v1/metrics")
    )
    metrics.set_meter_provider(MeterProvider(resource=resource, metric_readers=[reader]))
    _METRICS_ACTIVE = True
    return True
