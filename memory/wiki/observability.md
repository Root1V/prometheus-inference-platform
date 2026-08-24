# Observability — Structured Logging & Metrics

> Specs: `memory/specs/018-observability-telemetry.md` (Phase 1 — file-based) · `memory/specs/022-opentelemetry-sdk-instrumentation.md` (Phase 2 — OTEL spans)

---

## Overview

All three services (gateway, auth-service, manager) emit **structured JSON logs** via `structlog`. Every log line shares a canonical schema so events can be correlated across services using `trace_id`.

The observability core lives in the shared package `prometheus-telemetry` (`telemetry/src/prometheus_telemetry/`). Gateway, auth-service, and manager all import from it — no duplicated code. The gateway adds a `MetricsStore` on top; the manager adds TUI-specific helpers.

Log output goes to two destinations simultaneously:
- **stdout** — consumed by Podman/systemd journal
- **rotating JSONL file** — path configurable via `LOG_FILE_PATH`

---

## Canonical log schema

Every log event must contain:

```json
{
  "timestamp": "2026-04-12T10:23:45.123456Z",
  "level":     "info",
  "service":   "gateway",
  "event":     "inference.complete",
  "trace_id":  "4b3f1a2c8e9d4f01b2c31d2e3f4a5b6c"
}
```

> **`trace_id` format**: 32-character lowercase hex (W3C TraceContext format), matching the OTEL trace ID indexed in Tempo. Clicking a `trace_id` in a Grafana Loki log panel navigates directly to the matching trace waterfall in Tempo.

### Inference events (`event: "inference.complete"`)

Additional fields on gateway inference log lines:

| Field | Type | Notes |
|-------|------|-------|
| `model` | string | Registry model ID |
| `tokens_prompt` | int | Input tokens |
| `tokens_completion` | int | Output tokens |
| `tokens_total` | int | Sum |
| `latency_ms` | int | End-to-end latency |
| `tokens_per_second` | float | Throughput |
| `user_id` | string | JWT `sub` |
| `client_id` | string | JWT `client_id` claim |
| `span_id` | null | Reserved for Phase 2 |

> **`component` field** (manager only): manager API logs carry `"component": "api"` and manager TUI logs carry `"component": "tui"` so both can be filtered independently in dashboards without regex.

### Auth events (`event: "auth.*"`)

| `action` value | Meaning |
|----------------|---------|
| `token_issued` | OAuth2 token issued |
| `client_created` | New client registered |
| `client_rotated` | Secret rotated |
| `share_token_created` | Share link generated |
| `share_token_used` | Share link consumed |
| `share_token_expired` | Share link TTL exceeded |
| `share_token_revoked` | Share link manually revoked |

---

## Privacy rules

- **Full prompt text is never logged** — only an optional summary of ≤ 200 chars of the first user message / assistant response.
- JWT payloads, `Authorization` headers, and `client_secret` values must never appear in any log line.
- `share_token` full value must not appear in logs — log only `token_id` (UUID) + first 8 chars + `"…"`.

---

## `trace_id` propagation

```
Client → Gateway          generates trace_id = uuid4()
                          (or adopts incoming X-Trace-ID header)
           │ X-Trace-ID: <trace_id>
           ▼
      llama.cpp            logs trace_id if header present (best-effort)

Client → Auth Service     reads X-Trace-ID from inbound header
                          generates uuid4() if absent
```

- Header name: `X-Trace-ID` (carries the W3C hex trace ID; `traceparent` is also propagated outbound from gateway to llama.cpp)
- Gateway binds `trace_id` as a `structlog` context variable for the request lifetime; value comes from the active OTEL span context
- Manager (CLI): generates a per-operation `trace_id` at the start of each lifecycle call
- Manager TUI: each TUI session has a `tui.session_id` resource attribute (UUID4) on all its spans, enabling grouping in Tempo

---

## Log configuration (all services)

| Env var | Default | Description |
|---------|---------|-------------|
| `LOG_FILE_PATH` | `./logs/<service>.jsonl` | JSONL log file path |
| `LOG_LEVEL` | `info` | Minimum level (`debug` / `info` / `warning` / `error`) |
| `LOG_MAX_BYTES` | `10485760` (10 MB) | Rotate when file exceeds this size |
| `LOG_BACKUP_COUNT` | `5` | Rotated files to retain |

---

## OpenTelemetry tracing

All services export spans to Tempo via OTLP/HTTP. When Tempo is unreachable the SDK fails silently — services continue operating normally.

| Env var | Default | Description |
|---------|---------|-------------|
| `OTEL_EXPORTER_OTLP_ENDPOINT` | `http://tempo:4318` | OTLP/HTTP endpoint |

### Span names by domain

| Domain | Span name | Service |
|--------|-----------|--------|
| Inference | `inference.request` (root) · `auth.validate` · `llama.forward` | gateway |
| Models list | `models.list` | gateway |
| Usage query | `usage.query` | gateway |
| Gateway backends | `gateway.backends.list` | gateway |
| Token issuance | `token.issuance` | auth-service |
| Client CRUD | `client.create` · `client.list` · `client.update` · `client.deactivate` · `client.reactivate` · `client.rotate_secret` | auth-service |
| Admin UI | `admin.ui.login` · `admin.ui.client.*` · `admin.ui.share.*` | auth-service |
| Manager backends | `backend.list` · `backend.get` · `backend.probe` | manager API |
| Model lifecycle | `model.start` · `model.stop` · `model.download` | manager TUI |

If `LOG_FILE_PATH` is empty or the directory is not writable, the service warns to stdout and continues with stdout-only logging (no startup failure).

---

## `GET /metrics` (gateway only)

Unauthenticated. Returns `application/json`. In-process counters — no external metrics system required for Phase 1.

```json
{
  "service": "gateway",
  "uptime_seconds": 3600,
  "inference": {
    "requests_total": 1024,
    "requests_active": 3,
    "tokens_prompt_total": 142000,
    "tokens_completion_total": 87000,
    "errors_total": 12,
    "latency_p50_ms": 980,
    "latency_p95_ms": 2100,
    "latency_p99_ms": 4500
  },
  "auth": {
    "jwt_validations_ok": 1012,
    "jwt_validations_failed": 12
  },
  "backends": {
    "llama3-8b-q4-local": {
      "circuit_state": "closed",
      "requests_total": 512
    }
  }
}
```

Latency percentiles use a circular buffer of the last 1 000 samples.

---

## Ops observability stack (Grafana + Loki + Promtail + Tempo)

The stack runs in Podman alongside the platform services. It is **optional and additive** — gateway, auth-service, and manager operate normally without it.

### Components and ports

| Service | Port | Role |
|---------|------|------|
| Loki | `:3100` | Log aggregation (internal) |
| Promtail | `:9080` | Log collector |
| Tempo | `:3200` OTLP `:4317`/`:4318` | Distributed tracing backend |
| Grafana | `:3000` | UI — logs, traces, dashboards |

### Log collection

Promtail collects from two sources:
1. **Podman container stdout** — any compose service carrying the label `prometheus.service=<name>` is discovered automatically. Adding a new service with this label requires no Promtail config change.
2. **Manager TUI rotating file** — `runtime/logs/manager.log` bind-mounted into Promtail.

**Label cardinality rule**: only `level` and `service` are promoted as Loki index labels. `trace_id`, `event`, and `component` are stored as **structured metadata** (queryable via `| json` in LogQL) — never as labels. Adding high-cardinality fields as labels will degrade Loki performance.

### Grafana authentication

Grafana validates RS256 JWTs issued by the auth-service via its JWKS endpoint. No separate Grafana password is needed for JWT-authenticated users. `GRAFANA_ADMIN_PASSWORD` is for the built-in `admin` account only.

### Tempo — OTLP endpoints

Tempo listens on OTLP/gRPC `:4317` and OTLP/HTTP `:4318` on the host loopback. These are ready for spec-022 (OpenTelemetry SDK instrumentation) without any infrastructure change.

### Config files

All config lives under `observability/` and is fully version-controlled:
```
observability/
├── loki/loki-config.yaml          # single-process, local storage, 30-day retention
├── promtail/promtail-config.yaml  # Docker SD + static file scrape
├── tempo/tempo-config.yaml        # OTLP receiver, local storage
└── grafana/provisioning/
    ├── datasources/datasources.yaml  # Loki + Tempo pre-provisioned
    └── dashboards/prometheus-ops.json  # starter dashboard
```

No `grafana.ini` is committed — all Grafana settings are injected via `GF_*` environment variables in `podman-compose.yml`.

---

## Langfuse field mapping (Phase 2 reference)

| Log field | Langfuse concept |
|-----------|-----------------|
| `trace_id` | `trace.id` |
| `span_id` | `span.id` |
| `model` | `generation.model` |
| `tokens_prompt` | `usage.input` |
| `tokens_completion` | `usage.output` |
| `tokens_total` | `usage.total` |
| `latency_ms` | `generation.completion_start_time` delta |
| Prompt summary (≤ 200 chars) | `generation.input` |
| Response summary (≤ 200 chars) | `generation.output` |

---

## Related

- `memory/wiki/rate-limiting.md` — observability fields in rate-limit log events
- `memory/wiki/deployment.md` — log file paths, container log bind-mounts
- `memory/specs/018-observability-telemetry.md` — full implementation details
