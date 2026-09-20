"""Unit tests for prometheus_telemetry.metrics.

Implements: docs/roadmap.md — PRM-131
"""

from __future__ import annotations

import pytest
from opentelemetry import metrics as om
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader

import prometheus_telemetry.metrics as _metrics
from prometheus_telemetry import configure_metrics


@pytest.fixture(autouse=True)
def reset_metrics(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    monkeypatch.delenv("OTEL_SDK_DISABLED", raising=False)
    monkeypatch.delenv("OTEL_SERVICE_NAME", raising=False)
    _reset_otel_global()
    _metrics._CONFIGURED = False
    _metrics._METRICS_ACTIVE = False
    yield
    _reset_otel_global()
    _metrics._CONFIGURED = False
    _metrics._METRICS_ACTIVE = False


def _reset_otel_global() -> None:
    """Reset the OTEL global meter-provider so configure_metrics() can re-run.

    The SDK guards the setter with a ``Once``; both it and the provider have to
    go back. Tests only — the same shape test_tracing.py uses for the tracer.
    """
    import opentelemetry.metrics._internal as _om_internal
    from opentelemetry.util._once import Once  # type: ignore[import-untyped]

    # The globals live on the _internal module, not on the facade — setting
    # them on `opentelemetry.metrics` creates two unrelated names and the real
    # Once still refuses the second provider.
    _om_internal._METER_PROVIDER = None
    _om_internal._METER_PROVIDER_SET_ONCE = Once()


def test_endpoint_installs_a_periodic_otlp_reader() -> None:
    assert configure_metrics(service="test-svc", endpoint="http://collector.invalid:4318") is True

    provider = om.get_meter_provider()
    assert isinstance(provider, MeterProvider)
    # This provider's own readers. `_all_metric_readers` is a class-level set
    # shared by every provider ever built in the process, so asserting on it
    # would pass on a reader some other test installed.
    readers = provider._sdk_config.metric_readers  # type: ignore[attr-defined]
    assert [type(r) for r in readers] == [PeriodicExportingMetricReader]


def test_disabled_installs_a_noop_provider() -> None:
    assert configure_metrics(service="test-svc", disabled=True) is False
    assert isinstance(om.get_meter_provider(), om.NoOpMeterProvider)


def test_env_disabled_installs_a_noop_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OTEL_SDK_DISABLED", "true")
    assert configure_metrics(service="test-svc", endpoint="http://collector.invalid:4318") is False
    assert isinstance(om.get_meter_provider(), om.NoOpMeterProvider)


def test_no_endpoint_still_installs_a_real_provider() -> None:
    """RM-92: nothing configured means export nowhere, not disable the SDK.

    The provider has to be real regardless — instruments then cost in tests and
    in dev what they will cost in production, instead of being free until the
    day a collector is configured.
    """
    assert configure_metrics(service="test-svc") is False

    provider = om.get_meter_provider()
    assert isinstance(provider, MeterProvider)
    assert provider._sdk_config.metric_readers == ()  # type: ignore[attr-defined]


def test_is_idempotent() -> None:
    assert configure_metrics(service="test-svc", endpoint="http://collector.invalid:4318") is True
    first = om.get_meter_provider()
    # A second call must not replace the provider — the OTEL setter is guarded
    # by a Once and would log an error rather than swap it.
    assert configure_metrics(service="other", endpoint="http://other.invalid:4318") is True
    assert om.get_meter_provider() is first


def test_resource_carries_service_name_and_extras() -> None:
    configure_metrics(
        service="test-svc",
        endpoint="http://collector.invalid:4318",
        resource_attributes={"argus.component.role": "api"},
    )
    attrs = om.get_meter_provider()._sdk_config.resource.attributes  # type: ignore[attr-defined]
    assert attrs["service.name"] == "test-svc"
    assert attrs["argus.component.role"] == "api"


def test_otel_resource_attributes_env_reaches_the_resource(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Resource.create(), not Resource(attributes=...).

    The direct constructor skips OTEL_RESOURCE_ATTRIBUTES, which is how
    deployment injects service.namespace and argus.component.role without
    touching code — and nothing errors when it does, the attributes just never
    appear. Same trap the tracer's own comment documents.
    """
    monkeypatch.setenv(
        "OTEL_RESOURCE_ATTRIBUTES", "service.namespace=prometheus-inference-platform"
    )
    configure_metrics(service="test-svc", endpoint="http://collector.invalid:4318")
    attrs = om.get_meter_provider()._sdk_config.resource.attributes  # type: ignore[attr-defined]
    assert attrs["service.namespace"] == "prometheus-inference-platform"
