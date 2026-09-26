---
id: "022"
title: "OpenTelemetry SDK — Distributed Tracing (Spans & Trace Propagation)"
status: closed
current-agent: ""
pipeline-log: []
created: 2026-04-18
updated: 2026-04-19
---

# 022 — OpenTelemetry SDK — Distributed Tracing (Spans & Trace Propagation)

## Problem Statement

The Prometheus platform already ships structured JSON logs with a `trace_id` field on
every log line (spec-018, spec-020), and Grafana has a Loki datasource with a
`TraceID → Tempo` derived-field link already wired (spec-021). Tempo is running and
healthy at `localhost:3200`, accepting OTLP on ports `:4317` (gRPC) and `:4318`
(HTTP). Despite this ready infrastructure:

- **No OpenTelemetry SDK is installed** in any service. Tempo receives zero traces.
- **`trace_id` in logs is a home-grown UUID4**, not an OTEL W3C TraceContext-format
  trace ID (32-character lowercase hex / 16-byte). Grafana's derived-field link
  therefore never resolves — clicking a `trace_id` in a Loki log line returns
  "Trace not found" in Tempo.
- **No spans exist for any operation**. The platform runs four services covering
  four operational domains — inference, user management, LLM instance management,
  and gateway administration — and none of them produces a span tree. Debugging any
  failure requires grep-correlating log timestamps across services manually.
- **No `traceparent` header** is propagated from the gateway to llama.cpp. Even if
  traces existed, they would appear as disconnected root spans.

As a result, operators and users cannot answer any of the following questions without
manual log correlation:

| Question | Domain |
|----------|--------|
| Which stage of inference is slow — JWT decode, rate-limit check, or llama.cpp? | Inference |
| Did a `client.create` operation fail because of a DB write or an admin key check? | User management |
| Why is `GET /v1/backends` from the gateway returning stale data? | LLM instance mgmt |
| How long does token issuance take per client tier? | User management |
| Which model start/stop took longest in this TUI session? | LLM instance mgmt |

This spec closes the gap by instrumenting **all four operational domains** of the
platform with OTEL spans, making every operation observable as a trace waterfall in
Tempo.

## Goals

### Foundation
- [x] **G-1**: Install the OpenTelemetry SDK (`opentelemetry-sdk`,
  `opentelemetry-exporter-otlp-proto-http`, `opentelemetry-instrumentation-httpx`,
  `opentelemetry-instrumentation-fastapi`) exclusively in the `prometheus_telemetry`
  shared package so all services benefit from a single install surface.
- [x] **G-2**: The shared package exports three new public symbols:
  `configure_tracing()`, `get_tracer()`, `trace_id_from_context()`.
- [x] **G-3**: `TraceIDMiddleware` replaces the home-grown UUID4 `trace_id` with the
  active W3C TraceContext trace ID (32-character lowercase hex). Log lines emitted
  during a request carry exactly the same `trace_id` value that Tempo indexes.
- [x] **G-4**: Spans are exported to Tempo via OTLP/HTTP at the address configured by
  `OTEL_EXPORTER_OTLP_ENDPOINT` (default `http://tempo:4318`).
- [x] **G-5**: When Tempo is unreachable, the SDK fails silently — all services
  continue operating normally (consistent with spec-021 G-8).
- [x] **G-6**: Clicking a `trace_id` value in a Grafana Loki log panel navigates
  directly to the matching trace waterfall in Tempo with no Grafana config changes.

### Domain A — Inference management (gateway)
- [x] **G-7**: `POST /v1/chat/completions` produces a root span `inference.request`
  with child spans `auth.validate` (JWT validation) and `llama.forward` (HTTP
  forward to llama.cpp via auto-instrumented HTTPX).
- [x] **G-8**: `GET /v1/models` produces a root span `models.list`.
- [x] **G-9**: `GET /v1/usage` produces a root span `usage.query`.
- [x] **G-10**: The W3C `traceparent` header is propagated outbound from the gateway
  to llama.cpp. No llama.cpp changes required.

### Domain B — User management (auth-service REST API)
- [x] **G-11**: `POST /oauth2/token` produces a root span `token.issuance`.
- [x] **G-12**: `POST /clients` produces a root span `client.create`.
- [x] **G-13**: `GET /clients` produces a root span `client.list`.
- [x] **G-14**: `DELETE /clients/{client_id}` produces a root span `client.deactivate`.
- [x] **G-15**: `PATCH /clients/{client_id}` produces a root span `client.update`.
- [x] **G-16**: `POST /clients/{client_id}/rotate-secret` produces a root span
  `client.rotate_secret`.
- [x] **G-17**: `POST /clients/{client_id}/reactivate` produces a root span
  `client.reactivate`.

### Domain B2 — User management (auth-service admin UI)
- [x] **G-25**: `POST /admin/login` produces a root span `admin.ui.login` with
  `auth_result` (`ok` | `fail`).
- [x] **G-26**: `GET /admin/logout` produces a root span `admin.ui.logout`.
- [x] **G-27**: `POST /admin/clients` produces a root span `admin.ui.client.create`.
- [x] **G-28**: `POST /admin/clients/{client_id}/edit` produces a root span
  `admin.ui.client.update`.
- [x] **G-29**: `POST /admin/clients/{client_id}/deactivate` produces a root span
  `admin.ui.client.deactivate`.
- [x] **G-30**: `POST /admin/clients/{client_id}/reactivate` produces a root span
  `admin.ui.client.reactivate`.
- [x] **G-31**: `POST /admin/clients/{client_id}/rotate-secret` produces a root span
  `admin.ui.client.rotate_secret`.
- [x] **G-32**: `POST /admin/clients/{client_id}/delete` (hard delete) produces a
  root span `admin.ui.client.delete`.
- [x] **G-33**: `POST /admin/clients/{client_id}/share` produces a root span
  `admin.ui.share.create`.
- [x] **G-34**: `POST /admin/share/{token_id}/revoke` produces a root span
  `admin.ui.share.revoke`.
- [x] **G-35**: `GET /admin/secret-revealed` produces a root span
  `admin.ui.share.reveal` with `token_used=true`.

### Domain C — LLM instance management (manager API)
- [x] **G-18**: `GET /v1/backends` on the manager API produces a root span
  `backend.list`. When the registry size is ≤ `OTEL_BACKEND_PROBE_SPAN_THRESHOLD`
  (default 10), a child span `backend.probe` is created per probed model. When
  the count exceeds the threshold, a single `backend.probe.batch` span with
  `model_count=N` replaces the N individual child spans.
- [x] **G-19**: `GET /v1/backends/{model_id}` produces a root span `backend.get`.
- [x] **G-20**: The manager TUI instruments model lifecycle actions — start, stop, and
  download — as non-ASGI spans (`model.start`, `model.stop`, `model.download`)
  exported directly to Tempo from the bare-metal process.
- [x] **G-36**: Every span created by the manager TUI carries a `tui.session_id`
  resource attribute (UUID4 generated once at TUI startup), enabling all actions
  from one TUI session to be grouped and queried together in Tempo without relying
  on a long-lived root span.

### Domain D — Gateway admin
- [x] **G-21**: `GET /v1/backends` on the gateway produces a root span
  `gateway.backends.list`.

### Zero regression
- [x] **G-22**: The existing log schema is unchanged — `trace_id` is the only field
  whose value format changes (UUID4 → 32-character hex). No field is added or removed.
- [x] **G-23**: All existing tests pass after the migration, with UUID4-format
  assertions updated to match the new W3C hex format.
- [x] **G-24**: The new symbols are covered by unit tests in `telemetry/tests/`
  reaching ≥90 % coverage.

## Non-Goals

- **Langfuse integration** — deferred to a future spec.
- **Metrics / Prometheus-format exposition** — out of scope; covered by a separate spec.
- **llama.cpp server-side instrumentation** — external binary, not modifiable.
- **Auth-service JWKS endpoint** (`GET /.well-known/jwks.json`) — read-only, public,
  no business logic requiring a span.
- **Auth-service admin UI page renders** — `GET /admin/login`, `GET /admin/dashboard`,
  `GET /admin/clients/{id}/edit`, `GET /admin/` (redirect) produce no spans. Only
  state-changing operations are instrumented (see Domain B, admin UI goals).
- **Changes to Grafana, Loki, Tempo configuration** — all infra is already in place
  from spec-021. Zero `observability/` file changes.
- **Sampling configuration** — head-based sampling at 100 % is acceptable for a dev
  platform. Tail sampling and `ParentBasedTraceIdRatio` are deferred.
- **`/health` and `/metrics` endpoints** — infrastructure probes; excluded from
  tracing to avoid noise in Tempo.

## Proposed Solution

All OpenTelemetry SDK setup is centralised in `prometheus_telemetry`. Services call
`configure_tracing()` once at startup (alongside the existing `configure_logging()`),
then obtain a tracer via `get_tracer()` and create spans with
`with tracer.start_as_current_span(...)`.

`TraceIDMiddleware` is updated so that, when an active OTEL span exists for the
current request, it calls `trace_id_from_context()` to extract the 32-character W3C
trace ID and binds it into structlog context variables — replacing the previous
`uuid.uuid4().hex` call. This single change makes `trace_id` in all downstream log
events identical to the value Tempo stores.

The OTLP/HTTP exporter targets `OTEL_EXPORTER_OTLP_ENDPOINT` (default
`http://tempo:4318`). The exporter is initialised with a `BatchSpanProcessor` so that
export failures never block request handling.

`opentelemetry-instrumentation-httpx` is activated in the gateway's HTTPX client so
that outbound calls to llama.cpp automatically carry a `traceparent` header and are
recorded as child spans of `inference.request`.

### Domain A — Inference management (gateway)

```
POST /v1/chat/completions
  │
  └── [root] inference.request          service=gateway
        attributes: http.method, http.route, http.status_code,
                    user_id, model, client_id
        │
        ├── [child] auth.validate        ← JWT validation
        │     attributes: jwt.issuer, jwt.subject, validation.result
        │
        └── [child] llama.forward        ← auto via httpx instrumentation
              attributes: http.method, http.url, http.status_code, llama.model

GET /v1/models
  └── [root] models.list                service=gateway
        attributes: http.status_code, model_count

GET /v1/usage
  └── [root] usage.query               service=gateway
        attributes: http.status_code, user_id
```

### Domain B — User management (auth-service REST API)

```
POST /oauth2/token
  └── [root] token.issuance             service=auth-service
        attributes: grant_type, client_id, scope, http.status_code

POST /clients
  └── [root] client.create              service=auth-service
        attributes: client_id (of new client), scopes, http.status_code

GET /clients
  └── [root] client.list                service=auth-service
        attributes: http.status_code, client_count

DELETE /clients/{client_id}
  └── [root] client.deactivate          service=auth-service
        attributes: target_client_id, http.status_code

PATCH /clients/{client_id}
  └── [root] client.update              service=auth-service
        attributes: target_client_id, updated_fields, http.status_code

POST /clients/{client_id}/rotate-secret
  └── [root] client.rotate_secret       service=auth-service
        attributes: target_client_id, http.status_code

POST /clients/{client_id}/reactivate
  └── [root] client.reactivate          service=auth-service
        attributes: target_client_id, http.status_code
```

### Domain B2 — User management (auth-service admin UI)

Only state-changing operations produce spans. Pure page renders (`GET /admin/`,
`GET /admin/login`, `GET /admin/dashboard`, `GET /admin/clients/{id}/edit`) are
excluded — they carry no business logic beyond the session check.

```
POST /admin/login
  └── [root] admin.ui.login              service=auth-service
        attributes: auth_result (ok|fail)
        Note: no admin key or password value in any attribute.

GET /admin/logout
  └── [root] admin.ui.logout             service=auth-service
        attributes: http.status_code

POST /admin/clients
  └── [root] admin.ui.client.create      service=auth-service
        attributes: client_id (new), scopes, http.status_code

POST /admin/clients/{client_id}/edit
  └── [root] admin.ui.client.update      service=auth-service
        attributes: target_client_id, updated_fields, http.status_code

POST /admin/clients/{client_id}/deactivate
  └── [root] admin.ui.client.deactivate  service=auth-service
        attributes: target_client_id, http.status_code

POST /admin/clients/{client_id}/reactivate
  └── [root] admin.ui.client.reactivate  service=auth-service
        attributes: target_client_id, http.status_code

POST /admin/clients/{client_id}/rotate-secret
  └── [root] admin.ui.client.rotate_secret  service=auth-service
        attributes: target_client_id, http.status_code
        Note: new secret value never stored in any attribute.

POST /admin/clients/{client_id}/delete
  └── [root] admin.ui.client.delete      service=auth-service
        attributes: target_client_id, http.status_code

POST /admin/clients/{client_id}/share
  └── [root] admin.ui.share.create       service=auth-service
        attributes: target_client_id, share_ttl_seconds, http.status_code

POST /admin/share/{token_id}/revoke
  └── [root] admin.ui.share.revoke       service=auth-service
        attributes: token_id, http.status_code

GET /admin/secret-revealed
  └── [root] admin.ui.share.reveal       service=auth-service
        attributes: token_used=true, http.status_code
        Note: the revealed secret value is never stored in any attribute.
```

### Domain C — LLM instance management (manager API + TUI)

```
GET /v1/backends                        ← manager API
  └── [root] backend.list               service=manager, component=api
        attributes: http.status_code, backend_count
        │
        ├── [child] backend.probe       ← one per model (registry ≤ THRESHOLD)
        │     attributes: model_id, probe_result (running|stopped|unknown)
        │
        └── [child] backend.probe.batch ← single span (registry > THRESHOLD)
              attributes: model_count=N

GET /v1/backends/{model_id}             ← manager API
  └── [root] backend.get                service=manager, component=api
        attributes: model_id, http.status_code, backend_state

── TUI actions (bare-metal, non-ASGI spans) ──────────────────────────────────
All TUI spans carry tui.session_id as a resource attribute (UUID4 set once at
TUI startup) — enables querying the entire session in Tempo by attribute.

User triggers "Start" in TUI
  └── [root] model.start                service=manager, component=tui
        attributes: model_id, llama_pid (on success)

User triggers "Stop" in TUI
  └── [root] model.stop                 service=manager, component=tui
        attributes: model_id, exit_code

User triggers "Download" in TUI
  └── [root] model.download             service=manager, component=tui
        attributes: model_id, model_size_bytes, download_url_host
```

> **TUI spans**: The manager TUI is a bare-metal Textual process. It calls
> `configure_tracing()` in `cli/main.py` before launching the TUI app (same startup
> path as `configure_logging()`). Spans are created manually inside the worker
> action callbacks (`_run_action`) and exported via the same `BatchSpanProcessor`.
> No ASGI middleware is involved — `TraceIDMiddleware` does not apply to the TUI.
> The TUI's `trace_id` in logs continues to be set via
> `bind_contextvars(trace_id=trace_id_from_context())` at the start of each action.
> `configure_tracing()` is called with a `tui.session_id` resource attribute
> (UUID4 generated at startup) so that every span the TUI emits shares the value.

### Domain D — Gateway admin

```
GET /v1/backends                        ← gateway admin endpoint
  └── [root] gateway.backends.list      service=gateway
        attributes: http.status_code, backend_count
```

### Repository layout changes

```
telemetry/
  pyproject.toml                ← add opentelemetry-* dependencies
  src/
    prometheus_telemetry/
      __init__.py               ← add configure_tracing, get_tracer, trace_id_from_context
      core.py                   ← update TraceIDMiddleware to use trace_id_from_context()
      tracing.py                ← NEW — OTEL SDK setup and public helpers
  tests/
    test_tracing.py             ← NEW — unit tests for tracing symbols

gateway/src/prometheus_gateway/
  router.py                     ← add spans for inference.request, models.list,
  │                               usage.query, gateway.backends.list
  auth/middleware.py            ← add auth.validate child span

auth-service/src/prometheus_auth/
  main.py                       ← configure_tracing(service="auth-service") in lifespan
  routers/oauth2.py             ← token.issuance span
  routers/admin.py              ← client.* spans (REST API)
  routers/admin_ui.py           ← admin.ui.* spans (state-changing operations only)

runtime/manager/src/prometheus_manager/
  api/app.py                    ← configure_tracing(service="manager", component="api")
  api/routes.py                 ← backend.list + backend.probe, backend.get spans
  cli/main.py                   ← configure_tracing(service="manager", component="tui")
  tui/app.py                    ← model.start, model.stop, model.download spans
```

### Key Design Decisions

| Decision | Rationale |
|----------|-----------|
| OTLP/HTTP over OTLP/gRPC | HTTP needs no `grpcio` binary dependency; simpler virtualenv, less container weight. Tempo accepts both. |
| SDK installed only in `prometheus_telemetry` | Single install surface; all services import from the shared package — consistent with the spec-020 consolidation principle. |
| `BatchSpanProcessor` (not `SimpleSpanProcessor`) | Export happens off the critical path; request latency is unaffected when Tempo is slow or unreachable. |
| `trace_id_from_context()` replaces UUID4 in `TraceIDMiddleware` | Single point of change; `trace_id` in logs becomes identical to the Tempo-indexed value with no schema migration. |
| `configure_tracing()` is a no-op when `OTEL_SDK_DISABLED=true` | Allows individual services to opt out without code changes; consistent with spec-021 G-8 optional observability contract. |
| `opentelemetry-instrumentation-httpx` for `llama.forward` | Auto-instrumentation of all HTTPX calls means `traceparent` propagation requires zero call-site changes in the gateway. |
| W3C `traceparent` header outbound only | Gateway ignores inbound `traceparent` from external clients — prevents trace context injection attacks. |
| Manual spans for all domain operations (not FastAPI auto-instrumentation) | FastAPI auto-instrumentation creates a span for every route including `/health`, `/metrics`, and static files — too noisy. Manual spans cover exactly the 15 named operations across the four domains. |
| TUI uses `BatchSpanProcessor` directly (no ASGI) | The Textual TUI is a bare-metal process. `configure_tracing()` sets up the same SDK provider; spans are created manually inside worker callbacks. |
| `backend.probe` as a child of `backend.list` | The manager loops over all registry entries and probes each one's live state. One child span per probe makes the latency breakdown per model visible in the Tempo waterfall. |
| `updated_fields` attribute on `client.update` | A list of field names (not values) updated in the PATCH. Allows operators to see what changed without exposing the new value of sensitive fields like `scopes`. |
| `backend.probe.batch` when registry exceeds `OTEL_BACKEND_PROBE_SPAN_THRESHOLD` | Emitting N individual child spans for large registries (e.g. 50+ models) produces an unusably wide trace waterfall. A single `backend.probe.batch` span with `model_count=N` summarises the batch while keeping the trace readable. Default threshold is 10; configurable via env var. |
| `tui.session_id` resource attribute (not a long-lived root span) | A root span covering the entire TUI session (minutes to hours) would distort latency analytics and consume Tempo index space indefinitely. A UUID4 resource attribute achieves equivalent session-grouping via Tempo attribute search with no span-duration side-effects. |
| `SpanKind.INTERNAL` for all route-handler spans | `TraceIDMiddleware` creates the single root `SpanKind.SERVER` span per HTTP request (named `http.{method}`, e.g. `http.post`). All spans created inside route handlers (`inference.request`, `token.issuance`, `client.*`, `admin.ui.*`, `backend.list`, etc.) must use `SpanKind.INTERNAL`. Using `SERVER` for child spans causes Tempo to render each one as a separate trace root, producing N disconnected rows instead of a waterfall tree. Discovered and fixed during Tempo validation (commit `aae875e`). |

## API Contract

No new HTTP endpoints are added or modified. The only observable public-API change is
the format of the `X-Trace-ID` response header returned by all three ASGI services
(gateway, auth-service, manager API):

| Before | After |
|--------|-------|
| `X-Trace-ID: 550e8400-e29b-41d4-a716-446655440000` (UUID4, 36 chars) | `X-Trace-ID: 4bf92f3577b34da6a3ce929d0e0e4736` (W3C hex, 32 chars) |

The header name is unchanged. The response header is informational only and is not
validated by any internal component.

## Data Model

### Log event — `trace_id` field change

```jsonc
// Before (spec-020 / UUID4)
{
  "timestamp": "2026-04-19T10:00:00.000000",
  "level": "info",
  "service": "gateway",
  "event": "inference.complete",
  "trace_id": "550e8400-e29b-41d4-a716-446655440000"  // UUID4, 36 chars, hyphenated
}

// After (spec-022 / W3C TraceContext)
{
  "timestamp": "2026-04-19T10:00:00.000000",
  "level": "info",
  "service": "gateway",
  "event": "inference.complete",
  "trace_id": "4bf92f3577b34da6a3ce929d0e0e4736"  // 32-char lowercase hex, no hyphens
}
```

All other fields are unchanged. The `trace_id` key name remains `trace_id`.

### Complete span catalogue

#### Domain A — Inference management (gateway)

| Span name | Kind | Parent | Key attributes |
|-----------|------|--------|----------------|
| `inference.request` | `INTERNAL` | `http.post` | `http.method`, `http.route`, `http.status_code`, `user_id`, `model`, `client_id` |
| `auth.validate` | `INTERNAL` | `inference.request` | `jwt.issuer`, `jwt.subject`, `validation.result` (`ok`\|`fail`) |
| `llama.forward` | `CLIENT` | `inference.request` | `http.method`, `http.url`, `http.status_code`, `llama.model` |
| `models.list` | `INTERNAL` | `http.get` | `http.status_code`, `model_count` |
| `usage.query` | `INTERNAL` | `http.get` | `http.status_code`, `user_id` |
| `gateway.backends.list` | `INTERNAL` | `http.get` | `http.status_code`, `backend_count` |

#### Domain B — User management REST API (auth-service)

| Span name | Kind | Parent | Key attributes |
|-----------|------|--------|----------------|
| `token.issuance` | `INTERNAL` | `http.post` | `grant_type`, `client_id`, `scope`, `http.status_code` |
| `client.create` | `INTERNAL` | `http.post` | `client_id` (new), `scopes`, `http.status_code` |
| `client.list` | `INTERNAL` | `http.get` | `http.status_code`, `client_count` |
| `client.deactivate` | `INTERNAL` | `http.delete` | `target_client_id`, `http.status_code` |
| `client.update` | `INTERNAL` | `http.patch` | `target_client_id`, `updated_fields`, `http.status_code` |
| `client.rotate_secret` | `INTERNAL` | `http.post` | `target_client_id`, `http.status_code` |
| `client.reactivate` | `INTERNAL` | `http.post` | `target_client_id`, `http.status_code` |

#### Domain B2 — User management admin UI (auth-service)

| Span name | Kind | Parent | Key attributes |
|-----------|------|--------|----------------|
| `admin.ui.login` | `INTERNAL` | `http.post` | `auth_result` (`ok`\|`fail`) |
| `admin.ui.logout` | `INTERNAL` | `http.get` | `http.status_code` |
| `admin.ui.client.create` | `INTERNAL` | `http.post` | `client_id` (new), `scopes`, `http.status_code` |
| `admin.ui.client.update` | `INTERNAL` | `http.post` | `target_client_id`, `updated_fields`, `http.status_code` |
| `admin.ui.client.deactivate` | `INTERNAL` | `http.post` | `target_client_id`, `http.status_code` |
| `admin.ui.client.reactivate` | `INTERNAL` | `http.post` | `target_client_id`, `http.status_code` |
| `admin.ui.client.rotate_secret` | `INTERNAL` | `http.post` | `target_client_id`, `http.status_code` |
| `admin.ui.client.delete` | `INTERNAL` | `http.post` | `target_client_id`, `http.status_code` |
| `admin.ui.share.create` | `INTERNAL` | `http.post` | `target_client_id`, `share_ttl_seconds`, `http.status_code` |
| `admin.ui.share.revoke` | `INTERNAL` | `http.post` | `token_id`, `http.status_code` |
| `admin.ui.share.reveal` | `INTERNAL` | `http.get` | `token_used=true`, `http.status_code` |

#### Domain C — LLM instance management (manager)

| Span name | Kind | Parent | Key attributes |
|-----------|------|--------|----------------|
| `backend.list` | `INTERNAL` | `http.get` | `http.status_code`, `backend_count` |
| `backend.probe` | `INTERNAL` | `backend.list` | `model_id`, `probe_result` (`running`\|`stopped`\|`unknown`) |
| `backend.probe.batch` | `INTERNAL` | `backend.list` | `model_count` (emitted when registry size > `OTEL_BACKEND_PROBE_SPAN_THRESHOLD`) |
| `backend.get` | `INTERNAL` | `http.get` | `model_id`, `http.status_code`, `backend_state` |
| `model.start` | `INTERNAL` | none (TUI) | `model_id`, `llama_pid` |
| `model.stop` | `INTERNAL` | none (TUI) | `model_id`, `exit_code` |
| `model.download` | `INTERNAL` | none (TUI) | `model_id`, `model_size_bytes`, `download_url_host` |

> **`download_url_host`**: only the hostname portion of the download URL (e.g.
> `huggingface.co`). Full URLs are never stored in span attributes.

> **TUI resource attribute**: All three TUI spans additionally carry
> `tui.session_id` as a **resource** attribute (UUID4 generated once at TUI
> startup on the `TracerProvider`'s `Resource`). Resource attributes appear on
> every span the process emits and enable Tempo attribute search to group the
> full TUI session without a long-lived root span.

#### Domain D — Gateway admin

| Span name | Kind | Parent | Key attributes |
|-----------|------|--------|----------------|
| `gateway.backends.list` | `INTERNAL` | `http.get` | `http.status_code`, `backend_count` |

### New public symbols — `prometheus_telemetry`

```python
def configure_tracing(
    service: str,
    endpoint: str | None = None,              # OTEL_EXPORTER_OTLP_ENDPOINT or "http://tempo:4318"
    disabled: bool = False,                    # OTEL_SDK_DISABLED env var; True → NoOpTracerProvider
    resource_attributes: dict | None = None,   # Extra Resource attributes (e.g. tui.session_id)
) -> None:
    """Configure the OTEL SDK, BatchSpanProcessor, and OTLP/HTTP exporter. Idempotent."""

def get_tracer(name: str = "prometheus") -> opentelemetry.trace.Tracer:
    """Return a tracer bound to the given instrumentation scope."""

def trace_id_from_context() -> str:
    """Return the active span's W3C trace ID (32-char hex).
    Returns 'none' when no active span exists (background tasks, tests).
    """
```

### Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `OTEL_EXPORTER_OTLP_ENDPOINT` | `http://tempo:4318` | OTLP/HTTP base URL for span export |
| `OTEL_SDK_DISABLED` | `false` | Set `true` to replace all OTEL calls with no-ops |
| `OTEL_SERVICE_NAME` | value of `service` arg to `configure_tracing()` | Overrides the `service.name` resource attribute |
| `OTEL_BACKEND_PROBE_SPAN_THRESHOLD` | `10` | Max registered models before `backend.probe` child spans are collapsed into a single `backend.probe.batch` span |

> **Manager TUI (`pmgr tui`)**: The TUI process runs bare-metal (not in a container)
> and does not share the container environment. Its tracing endpoint is configured
> via the `[tracing]` section in `manager.toml` (not via `OTEL_EXPORTER_OTLP_ENDPOINT`):
>
> ```toml
> [tracing]
> otlp_endpoint = "http://localhost:4318"  # bare-metal; containers use http://tempo:4318
> disabled = false
> ```
>
> The `http://localhost:4318` address is the Tempo OTLP/HTTP port forwarded by
> the Podman VM to the host on macOS dev.

## Security Considerations

### Span attribute allowlist (OWASP A01 / A02)

Span attributes **must only** contain the fields listed in the Data Model span
catalogue. The following are explicitly forbidden in any span attribute:

| Forbidden data | Example field to NOT include |
|---------------|------------------------------|
| Raw prompt / completion text | `prompt`, `content`, `messages` |
| JWT token strings | `Authorization` header value |
| Client secret or API key values | `client_secret` |
| Admin key | `admin_key`, `X-Admin-Key` header value |
| Password or hash | `password_hash` |
| Share token value | `share_token`, `token_value` |
| Full download URL | `model_url` (use `download_url_host` instead) |
| New field values on edit / update | `new_scopes`, `new_label` (use `updated_fields=["scopes"]`) |

`target_client_id`, `client_id`, and `user_id` are identifiers already present in
logs; adding them to spans does not expand the sensitive-data surface.

### Trace data confinement

- Tempo listens only on the internal Podman network (`tempo:4318` / `tempo:4317`) and
  is never exposed on the host beyond `127.0.0.1:3200` (Grafana query port). Span
  data does not leave the local machine.
- The manager TUI exports spans from the bare-metal process directly to
  `OTEL_EXPORTER_OTLP_ENDPOINT`. On macOS dev this resolves to `localhost:4318`
  which is bound to `127.0.0.1` — loopback only, as set in spec-021.

### `traceparent` header injection (OWASP A03)

- The gateway **ignores** any inbound `traceparent` header from external HTTP clients.
  A new root span is always created at the gateway boundary. This prevents a malicious
  client from injecting a forged trace context to pollute Tempo data or guess
  trace IDs from other users.
- The auth-service and manager API similarly create root spans regardless of inbound
  headers — they are internal services not reachable from external networks, but the
  rule is applied consistently.
- The `traceparent` header is propagated **outbound only** — from gateway to llama.cpp.
  llama.cpp silently ignores unknown headers; no attack surface is added.

### Admin operation spans (OWASP A01)

- REST API spans (`client.*`) are opened only inside routes that have already passed
  `_require_admin()` authentication. A rejected admin request never produces a
  successful span.
- Admin UI spans (`admin.ui.*`) are opened **after** session validation. An
  unauthenticated UI request returns HTTP 302 before any span is created, except for
  `admin.ui.login` which is always created (with `auth_result="fail"` on bad key).
- `admin.ui.client.rotate_secret` and `client.rotate_secret` record `target_client_id`
  and `http.status_code` only. The new secret value is never stored.
- `admin.ui.share.reveal` records `token_used=true` only. The revealed plaintext
  secret is never stored in any span attribute.
- `client.update` and `admin.ui.client.update` record `updated_fields` (a list of
  key names) but never the new field values.

### SDK dependency supply-chain (OWASP A06)

- OpenTelemetry Python SDK packages are from the official `opentelemetry-python`
  project. All new dependencies must be pinned in `uv.lock` and reviewed for known
  CVEs before the spec is marked `approved`.

### Denial of service via span flooding (OWASP A05)

- `BatchSpanProcessor` uses the SDK's default queue size (2048 spans) and drops
  excess spans rather than blocking. A high-request-rate scenario cannot cause
  unbounded memory growth in the exporter queue.
- The SDK opens no new unauthenticated network listener endpoints.

## Acceptance Criteria

Each item maps 1-to-1 with a test case.

### SDK foundation

- [x] **AC-1**: Given `configure_tracing(service="gateway")` is called, when the
  OTLP exporter is initialised, then a `BatchSpanProcessor` is registered and no
  exception is raised.

- [x] **AC-2**: Given `OTEL_SDK_DISABLED=true` is set, when `configure_tracing()` is
  called, then `get_tracer()` returns a `NoOpTracer` and no network connections to
  Tempo are attempted.

- [x] **AC-3**: Given Tempo is unreachable (connection refused), when any instrumented
  request is processed, then the request completes successfully and no exception
  surfaces to the client.

- [x] **AC-4**: Given `configure_tracing()` is called twice, then the second call is
  a no-op — only one `TracerProvider` and one `BatchSpanProcessor` are registered.

- [x] **AC-5**: Given an active OTEL span exists during a request, when
  `TraceIDMiddleware` binds `trace_id` to structlog context variables, then the value
  equals `trace_id_from_context()` — a 32-character lowercase hex string, no hyphens.

- [x] **AC-6**: Given `trace_id_from_context()` is called with no active span, then
  it returns `"none"` without raising an exception.

- [x] **AC-7**: Given `configure_tracing()` has been called, when `get_tracer("foo")`
  and `get_tracer("bar")` are called, then each returns a distinct `Tracer` instance.

### Domain A — Inference management

- [x] **AC-8**: Given a valid JWT, when `POST /v1/chat/completions` is handled, then
  a root span `inference.request` is created with `http.method="POST"`,
  `http.route="/v1/chat/completions"`, `user_id`, `model`, and `client_id` attributes.

- [x] **AC-9**: Given an active `inference.request` span, when JWT validation runs,
  then a child span `auth.validate` is created with `validation.result="ok"` for a
  valid token and `validation.result="fail"` for an invalid one.

- [x] **AC-10**: Given an active `inference.request` span, when the gateway forwards
  to llama.cpp, then a child span `llama.forward` is created and the outbound HTTPX
  request carries a valid W3C `traceparent` header.

- [x] **AC-11**: Given a spoofed `traceparent` header in an inbound `POST
  /v1/chat/completions` request, then the gateway ignores it and creates a new root
  span (inbound trace context injection is prevented).

- [x] **AC-12**: Given `GET /v1/models` is called, then a root span `models.list` is
  created with `http.status_code` and `model_count` attributes.

- [x] **AC-13**: Given `GET /v1/usage` is called, then a root span `usage.query` is
  created with `http.status_code` and `user_id` attributes.

- [x] **AC-14**: Given `GET /v1/backends` on the gateway is called (admin), then a
  root span `gateway.backends.list` is created with `http.status_code` and
  `backend_count` attributes.

### Domain B — User management (REST API)

- [x] **AC-15**: Given a valid `client_credentials` grant, when `POST /oauth2/token`
  is handled, then a root span `token.issuance` is created with
  `grant_type="client_credentials"`, `client_id`, `scope`, and `http.status_code=200`.

- [x] **AC-16**: Given an authenticated admin request, when `POST /clients` is
  handled, then a root span `client.create` is created with the new `client_id`,
  `scopes`, and `http.status_code=201`.

- [x] **AC-17**: Given an authenticated admin request, when `GET /clients` is
  handled, then a root span `client.list` is created with `http.status_code` and
  `client_count`.

- [x] **AC-18**: Given an authenticated admin request, when `DELETE
  /clients/{client_id}` is handled, then a root span `client.deactivate` is created
  with `target_client_id` and `http.status_code=204`.

- [x] **AC-19**: Given an authenticated admin request, when `PATCH
  /clients/{client_id}` is handled, then a root span `client.update` is created with
  `target_client_id`, `updated_fields` (list of key names only — no values), and
  `http.status_code`.

- [x] **AC-20**: Given an authenticated admin request, when `POST
  /clients/{client_id}/rotate-secret` is handled, then a root span
  `client.rotate_secret` is created with `target_client_id` and `http.status_code`.
  The new secret value does NOT appear in any span attribute.

- [x] **AC-21**: Given an authenticated admin request, when `POST
  /clients/{client_id}/reactivate` is handled, then a root span `client.reactivate`
  is created with `target_client_id` and `http.status_code`.

### Domain B2 — User management (admin UI)

- [x] **AC-33**: Given an admin submits the login form with the correct key, when
  `POST /admin/login` is handled, then a root span `admin.ui.login` is created with
  `auth_result="ok"`. The admin key value does NOT appear in any span attribute.

- [x] **AC-34**: Given an admin submits the login form with an incorrect key, then a
  root span `admin.ui.login` is created with `auth_result="fail"` and `span.status`
  is `ERROR`.

- [x] **AC-35**: Given a logged-in admin navigates to `GET /admin/logout`, then a
  root span `admin.ui.logout` is created with `http.status_code`.

- [x] **AC-36**: Given a logged-in admin submits the "Create client" form, when
  `POST /admin/clients` is handled, then a root span `admin.ui.client.create` is
  created with the new `client_id`, `scopes`, and `http.status_code=303`.

- [x] **AC-37**: Given a logged-in admin submits the edit form for a client, then a
  root span `admin.ui.client.update` is created with `target_client_id`,
  `updated_fields` (key names only, no values), and `http.status_code`.

- [x] **AC-38**: Given a logged-in admin deactivates a client via the UI, then a
  root span `admin.ui.client.deactivate` is created with `target_client_id` and
  `http.status_code`.

- [x] **AC-39**: Given a logged-in admin reactivates a client via the UI, then a
  root span `admin.ui.client.reactivate` is created with `target_client_id` and
  `http.status_code`.

- [x] **AC-40**: Given a logged-in admin rotates the secret of a client via the UI,
  then a root span `admin.ui.client.rotate_secret` is created with `target_client_id`
  and `http.status_code`. The new secret value does NOT appear in any attribute.

- [x] **AC-41**: Given a logged-in admin hard-deletes a client via the UI, then a
  root span `admin.ui.client.delete` is created with `target_client_id` and
  `http.status_code`.

- [x] **AC-42**: Given a logged-in admin generates a share link for a client, when
  `POST /admin/clients/{client_id}/share` is handled, then a root span
  `admin.ui.share.create` is created with `target_client_id`, `share_ttl_seconds`,
  and `http.status_code`. The share token value does NOT appear in any attribute.

- [x] **AC-43**: Given a logged-in admin revokes a share token, then a root span
  `admin.ui.share.revoke` is created with `token_id` and `http.status_code`.

- [x] **AC-44**: Given a one-time secret-reveal link is accessed, when
  `GET /admin/secret-revealed` is handled, then a root span `admin.ui.share.reveal`
  is created with `token_used=true` and `http.status_code`. The revealed secret
  value does NOT appear in any span attribute.

### Domain C — LLM instance management

- [x] **AC-22**: Given `GET /v1/backends` on the manager API is called and the
  registry has ≤ `OTEL_BACKEND_PROBE_SPAN_THRESHOLD` models, then a root span
  `backend.list` is created with a child span `backend.probe` per probed model,
  each carrying `model_id` and `probe_result`.

- [x] **AC-23**: Given `GET /v1/backends/{model_id}` is called, then a root span
  `backend.get` is created with `model_id`, `http.status_code`, and `backend_state`.

- [x] **AC-24**: Given the manager TUI is running and the user triggers a model start
  action, then a span `model.start` is created with `model_id` and (on success)
  `llama_pid`, and the span status is `OK`.

- [x] **AC-25**: Given the user triggers a model stop action in the TUI, then a span
  `model.stop` is created with `model_id` and `exit_code`.

- [x] **AC-26**: Given the user triggers a model download action in the TUI, then a
  span `model.download` is created with `model_id`, `model_size_bytes`, and
  `download_url_host` (hostname only — no full URL).

- [x] **AC-45**: Given `GET /v1/backends` on the manager API is called and the
  registry has more than `OTEL_BACKEND_PROBE_SPAN_THRESHOLD` models (default 10),
  then a single child span `backend.probe.batch` is created under `backend.list`
  with `model_count=N`, and no individual `backend.probe` child spans are emitted.

- [x] **AC-46**: Given the TUI is started and the user performs multiple actions
  (e.g. model start then model stop), when spans are exported to Tempo, then all
  TUI spans carry the same `tui.session_id` resource attribute value (a UUID4
  generated once at TUI startup).

### Security

- [x] **AC-27**: Given any span is inspected across all four domains (including
  admin UI spans), then none of the following appear in span attributes: raw prompt
  text, JWT token string, admin key, client secret, password hash, share token
  value, full download URL, new field values on edit/update operations.

- [x] **AC-28**: Given an `auth.validate` span ends with an exception (e.g. expired
  JWT), then `span.status` is `ERROR`, the exception type is recorded as a span
  event, and the JWT payload string does not appear in any attribute.

### Zero regression

- [x] **AC-29**: Given all gateway tests (`uv run pytest gateway/tests/ -v`), when
  run after this spec is implemented, then all tests pass with coverage ≥ 80 %.

- [x] **AC-30**: Given all auth-service tests, when run, then ≥ 80 % coverage and
  zero test failures.

- [x] **AC-31**: Given `uv run pytest telemetry/tests/ -v --cov`, then all tracing
  tests pass and `prometheus_telemetry` coverage remains ≥ 90 %.

- [x] **AC-32**: Given `uv run mypy telemetry/src/ gateway/src/ auth-service/src/
  runtime/manager/src/`, then no new type errors are introduced.

## Open Questions

- [x] **Q-1 (RESOLVED)**: Manual span creation is used instead of FastAPI
  auto-instrumentation. Auto-instrumentation produces a span for every route
  (including `/health`, `/metrics`, static files) cluttering Tempo. Manual spans
  cover exactly the 15 named operations in the four domains.
- [x] **Q-2 (RESOLVED)**: 100 % head-based sampling. For a dev platform this is
  acceptable. `ParentBasedTraceIdRatio` sampling is deferred to a future spec.
- [x] **Q-3 (RESOLVED)**: Manager API and TUI are fully in scope in this spec (G-18,
  G-19, G-20). Both `configure_tracing()` calls are added at startup.
- [x] **Q-4 (RESOLVED)**: `backend.probe` child spans are suppressed when the
  registry size exceeds `OTEL_BACKEND_PROBE_SPAN_THRESHOLD` (default 10). A single
  `backend.probe.batch` span with `model_count=N` is emitted instead. See G-18,
  AC-22, AC-45, and the Key Design Decisions table.
- [x] **Q-5 (RESOLVED)**: TUI action spans carry a `tui.session_id` resource
  attribute (UUID4 generated once at TUI startup) rather than a long-lived root
  span. This groups all actions from one TUI session in Tempo attribute search
  without distorting latency metrics. See G-36, AC-46, and the Key Design
  Decisions table.

## References

- Predecessor infrastructure: [memory/specs/021-ops-observability-stack.md](021-ops-observability-stack.md) — Tempo running on `:4317`/`:4318`; Grafana Loki `TraceID → Tempo` derived-field link wired.
- Shared telemetry package: [memory/specs/020-shared-telemetry-package.md](020-shared-telemetry-package.md) — `prometheus_telemetry` package, `TraceIDMiddleware`, `configure_logging()`.
- Structured log schema: [memory/specs/018-observability-telemetry.md](018-observability-telemetry.md) — canonical `trace_id` position in log events.
- OpenTelemetry Python SDK: https://opentelemetry-python.readthedocs.io/
- W3C TraceContext specification: https://www.w3.org/TR/trace-context/
- OTLP/HTTP specification: https://opentelemetry.io/docs/memory/specs/otlp/
- Grafana Tempo OTLP ingestion: https://grafana.com/docs/tempo/latest/configuration/
