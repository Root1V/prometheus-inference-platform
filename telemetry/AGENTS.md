# Telemetry — Agent Navigation Rules

## What lives here

`prometheus-telemetry` — the shared structured observability package used by all Prometheus services.

```
telemetry/
├── src/prometheus_telemetry/
│   ├── __init__.py   # Public API — all symbols re-exported here
│   ├── core.py       # configure_logging(), get_logger(), TraceIDMiddleware
│   └── tracing.py    # configure_tracing(), get_tracer(), trace_id_from_context()
└── tests/
    ├── test_core.py
    └── test_tracing.py
```

Each service imports from this package. The per-service `telemetry.py` files in gateway, auth-service, and manager are **migration shims** — they re-export from `prometheus_telemetry` for backward compatibility.

## Public API

| Symbol | Module | Purpose |
|--------|--------|---------|
| `configure_logging(service, component)` | `core` | structlog + stdlib bridge — call once at startup, idempotent |
| `get_logger()` | `core` | Returns a bound structlog logger |
| `TraceIDMiddleware` | `core` | ASGI middleware — injects / echoes `X-Trace-ID` header |
| `bind_contextvars` / `clear_contextvars` | `core` | structlog context propagation (re-exported from structlog) |
| `configure_tracing(service)` | `tracing` | OTEL SDK setup — `BatchSpanProcessor` + OTLP/HTTP exporter |
| `get_tracer(scope)` | `tracing` | Returns a `Tracer` bound to an instrumentation scope |
| `trace_id_from_context()` | `tracing` | Extracts the active W3C trace ID (32-char hex) or `"none"` |

## Usage Pattern (all services follow this)

```python
# 1. At module startup — before any other imports that log
from prometheus_telemetry import configure_logging, configure_tracing
configure_logging(service="gateway")        # or "auth", "manager"
configure_tracing(service="gateway")        # sends spans to Tempo via OTLP/HTTP

# 2. In ASGI app setup
from prometheus_telemetry import TraceIDMiddleware
app.add_middleware(TraceIDMiddleware, service="gateway")

# 3. In business logic
from prometheus_telemetry import get_tracer
_tracer = get_tracer("gateway.router")      # scope = "<service>.<component>"

with _tracer.start_as_current_span("inference.request") as span:
    span.set_attribute("model", model_id)
```

## Span Naming Convention

Span names follow `<domain>.<action>` — all lowercase, dot-separated:

| Service | Span name | Scope |
|---------|-----------|-------|
| gateway | `inference.request` | `gateway.router` |
| gateway | `models.list` | `gateway.router` |
| gateway | `auth.validate` | `gateway.auth` |
| auth | `token.issue` | `auth.oauth2` |
| manager | `lifecycle.start` | `manager.lifecycle` |
| manager | `registry.list` | `manager.registry` |

Never use uppercase or spaces in span names — they must be consistent across services for Tempo queries.

## Excluded paths (never traced)

`/health` and `/metrics` are excluded from tracing by default — they generate noise in Tempo without value. Do not add inference endpoints to this list.

## Before starting any task here

1. This package is a **shared library** — any breaking change to the public API affects gateway, auth-service, and manager simultaneously.
2. Check all three services' `telemetry.py` shims before renaming or removing a public symbol.
3. `configure_logging()` and `configure_tracing()` are **idempotent** — they must remain safe to call multiple times.

## Before closing any task here

- [ ] `(cd telemetry && uv run pytest tests/ -v --cov=src --cov-fail-under=80)`
- [ ] `(cd telemetry && uv run ruff check src/ && uv run ruff format --check src/ && uv run mypy src/)`
- [ ] Public API in `__init__.py` is up to date with any new symbols
- [ ] All three service shims (`gateway/telemetry.py`, `auth-service/telemetry.py`, `manager/telemetry.py`) still import correctly
- [ ] Span naming convention followed — lowercase, dot-separated

## Key constraints

- Never add service-specific logic to this package — it must remain domain-agnostic.
- `configure_logging()` must be called **before** any other import that logs — order matters at startup.
- OTLP endpoint defaults to `http://tempo:4318` — overridable via `OTEL_EXPORTER_OTLP_ENDPOINT`.
- See memory/specs/020 for the shared telemetry package spec and memory/specs/022 for OTEL SDK instrumentation.
