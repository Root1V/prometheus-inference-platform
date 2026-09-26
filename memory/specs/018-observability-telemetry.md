---
id: "018"
title: "Structured Observability & Telemetry — Phase 1 (File-Based)"
status: closed
current-agent: ""
pipeline-log: []
created: 2026-04-12
updated: 2026-04-12
---

# 018 — Structured Observability & Telemetry — Phase 1 (File-Based)

## Problem Statement

The three Prometheus services (gateway, auth-service, runtime/manager) each use different
logging approaches, producing inconsistent and non-queryable output:

| Service | Current state |
|---------|---------------|
| **gateway** | `structlog` configured in `main.py`; most modules (`router.py`, `rate_limit_middleware.py`, etc.) still use `logging.getLogger(__name__)` yielding plaintext stdlib output |
| **auth-service** | `logging.basicConfig()` in the lifespan hook; `logger.info("event.name", extra={...})` pattern throughout — readable but not JSON |
| **manager** | `logging.getLogger(__name__)` everywhere; `extra={}` dicts but no JSON rendering |

Consequences:

- Log lines from different services cannot be correlated. When a client request fails, there
  is no single field (`trace_id`) to join the gateway log, the auth-service token log, and
  the llama.cpp backend log.
- Inference events exist (see `router.py` `inference.complete`) but carry no `span_id` and
  the schema is not documented — a future Langfuse or OpenTelemetry integration would need
  to rewrite the instrumentation from scratch.
- The `/metrics` path is exempted from auth and rate limiting but no actual metrics endpoint
  is implemented; it returns 404.
- There is no standard for what fields are mandatory, which are optional, and what values
  are forbidden (e.g. raw prompt text, secret values).

## Goals

- [x] Replace all `logging.getLogger(__name__)` calls across gateway, auth-service, and
  manager with `structlog`-based loggers produced by a shared `get_logger()` factory.
- [x] Define and enforce a canonical log event schema (mandatory fields, naming conventions).
- [x] Propagate a `trace_id` from the gateway through every downstream call so a single
  inference request can be traced end-to-end across all log files.
- [x] Write structured JSON logs to rotating per-service files **and** stdout simultaneously.
- [x] Implement a real `GET /metrics` endpoint on the gateway returning in-process counters
  as JSON (no external metrics system required).
- [x] Ensure the log schema is Langfuse-ready: fields map directly to Langfuse trace concepts
  without further transformation (Phase 2 will add the actual exporter).
- [x] Keep sensitive data (secrets, full prompts, JWT payloads) out of logs.

## Non-Goals

- OpenTelemetry SDK integration — deferred to Phase 2 (future spec).
- Langfuse SDK / remote trace export — deferred to Phase 2.
- Prometheus-format `/metrics` (text/plain exposition) — not required; JSON is sufficient.
- Distributed tracing with parent/child span trees — `span_id` is defined in schema but
  population is optional for Phase 1.
- Centralized log aggregation (ELK, Loki, etc.) — operators can tail the JSONL files or
  ship them with any standard forwarder.
- Changes to the `slowapi` rate-limit logs emitted by the auth-service.
- Changes to llama-server's own log output (external binary — not in scope).

## Proposed Solution

### Telemetry module per service

Each service gets a `telemetry.py` module that:

1. Configures `structlog` **once** at import-time (idempotent; safe to call from tests).
2. Exposes a `get_logger(name: str) -> structlog.BoundLogger` factory.
3. Configures two `logging.Handler`s: a `StreamHandler` (stdout) and a
   `RotatingFileHandler` writing to a configurable path (default `./logs/<service>.jsonl`).
4. Sets the static `service` field via `structlog.contextvars.bind_contextvars`.

All existing `logging.getLogger(__name__)` call-sites are replaced with
`get_logger(__name__)`.  The stdlib `logging` bridge (`structlog.stdlib.ProcessorFormatter`)
is used so that third-party libraries (uvicorn, httpx) that emit stdlib logs are also
rendered as JSON.

### Canonical log schema

Every log event **must** contain these fields:

```json
{
  "timestamp": "2026-04-12T10:23:45.123456Z",
  "level":     "info",
  "service":   "gateway",
  "event":     "inference.complete",
  "trace_id":  "4b3f1a2c-8e9d-4f01-b2c3-1d2e3f4a5b6c"
}
```

**Inference events** (`event: "inference.complete"`) additionally carry:

```json
{
  "model":              "llama3-8b-q4-local",
  "tokens_prompt":      142,
  "tokens_completion":  87,
  "tokens_total":       229,
  "latency_ms":         1240,
  "tokens_per_second":  70.16,
  "user_id":            "svc-myapp",
  "client_id":          "client-abc123",
  "span_id":            null
}
```

**Auth events** (`event: "auth.*"`) additionally carry:

```json
{
  "client_id": "client-abc123",
  "action":    "token_issued"
}
```

Valid `action` values: `token_issued`, `client_created`, `client_rotated`,
`share_token_created`, `share_token_used`, `share_token_expired`, `share_token_revoked`.

**Langfuse field mapping** (for Phase 2 exporter):

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

> **Privacy rule**: `input` and `output` summary fields are **optional** and must contain at
> most the first 200 characters of the first user message / assistant response respectively.
> Full prompt text is **never** logged.

### `trace_id` propagation

```
Client → [Gateway]                       generates trace_id = uuid4()
              │ X-Trace-ID: <trace_id>
              ▼
         [llama.cpp :8080]               (logs trace_id if present — best-effort)
              │
         [Gateway logs inference.complete with trace_id]

Client → [Auth Service]                  reads X-Trace-ID from inbound header
              │                          generates own uuid4() if header absent
              ▼
         [Auth logs auth.* with trace_id]
```

- Gateway: `request_id_middleware` (already in `main.py`) is extended to also set
  `trace_id` as a `structlog` context variable (`bind_contextvars(trace_id=...)`).
  If the incoming request carries an `X-Trace-ID` header it is adopted; otherwise a new
  `uuid4()` is generated. The `trace_id` is forwarded to llama.cpp as the
  `X-Trace-ID` header on the proxied request.
- Auth-service: new `trace_id` middleware reads `X-Trace-ID` from the inbound HTTP header
  and binds it as a context variable. Falls back to `uuid4()` if absent.
- Manager: CLI tool — no HTTP server context. Manager generates a per-operation `trace_id`
  at the start of each lifecycle call (start/stop/download) and passes it to all log
  statements in that call.

### Log file configuration

| Env var | Default | Description |
|---------|---------|-------------|
| `LOG_FILE_PATH` | `./logs/<service>.jsonl` | Absolute or relative path for the JSONL log file |
| `LOG_LEVEL` | `info` | Minimum log level (debug / info / warning / error) |
| `LOG_MAX_BYTES` | `10485760` (10 MB) | Rotate when file exceeds this size |
| `LOG_BACKUP_COUNT` | `5` | Number of rotated files to retain |

Both the file handler and stdout handler emit JSON on every log call. Log rotation uses
Python's `logging.handlers.RotatingFileHandler` — no external log rotation daemon required.

When `LOG_FILE_PATH` is empty or the parent directory is not writable, the service logs a
single warning to stdout and continues with stdout-only logging (no startup failure).

### In-process metrics store (`GET /metrics`)

A lightweight in-process counter/histogram store (plain Python `dict` protected by
`asyncio.Lock`) is maintained in the gateway. No external library is required for Phase 1.

The gateway exposes `GET /metrics` returning JSON:

```json
{
  "service": "gateway",
  "uptime_seconds": 3600,
  "inference": {
    "requests_total":        1024,
    "requests_active":       3,
    "tokens_prompt_total":   142000,
    "tokens_completion_total": 87000,
    "errors_total":          12,
    "latency_p50_ms":        980,
    "latency_p95_ms":        2100,
    "latency_p99_ms":        4500
  },
  "auth": {
    "jwt_validations_ok":    1012,
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

The endpoint is unauthenticated (already exempted by `JWTAuthMiddleware`). Percentile
approximations use a circular buffer of the last 1 000 latency samples.

### File layout

```
gateway/src/prometheus_gateway/
└── telemetry.py            ← NEW: structlog config, get_logger(), MetricsStore

auth-service/src/prometheus_auth/
└── telemetry.py            ← NEW: structlog config, get_logger(), trace_id middleware

runtime/manager/src/prometheus_manager/
└── telemetry.py            ← NEW: structlog config, get_logger()
```

All three `telemetry.py` modules share the same processor chain and configuration shape
but are independent — no shared library dependency between services.

### Key Design Decisions

| Decision | Rationale |
|----------|-----------|
| `structlog` (not plain `logging`) as the canonical library | Already used in gateway `main.py`; provides processor chain, context variables, and clean JSON output without monkey-patching |
| stdlib `logging` bridge kept active | Third-party libraries (uvicorn, httpx, SQLAlchemy) emit via stdlib; bridging into structlog ensures unified JSON output |
| Per-service `telemetry.py`, not a shared package | Gateway, auth-service, and manager are independently deployable. A shared package would couple their release cycles |
| `trace_id` via `structlog.contextvars` | Thread/task-safe context propagation without passing logger instances through every function signature |
| `X-Trace-ID` header name (not `traceparent`) | W3C `traceparent` is for OpenTelemetry distributed tracing. Phase 1 uses a simpler header; Phase 2 can add `traceparent` in parallel |
| In-process dict for metrics (not Prometheus client) | Zero new runtime dependencies; sufficient for Phase 1 operational visibility. Phase 2 will expose OTLP |
| `RotatingFileHandler` (not `TimedRotatingFileHandler`) | Size-based rotation avoids unbounded growth regardless of traffic pattern; simpler to reason about in dev |
| Optional `span_id` field (null in Phase 1) | Field is reserved in the schema so the log schema does not change in Phase 2 when spans are populated |
| Prompt/response summary capped at 200 chars | Provides debugging context without exposing potentially sensitive user data or large context windows |

## API Contract

### `GET /metrics` (gateway)

Returns `200 OK` with `Content-Type: application/json`.

No authentication required. No request body.

Response schema: see JSON example in Proposed Solution above.

> No OpenAPI file — the endpoint is operational/diagnostic only, not part of the
> public AI API contract.

## Data Model

No database schema changes. Metrics are ephemeral (in-process only).

## Security Considerations

- **No secrets in logs**: API keys, JWT payloads, RSA key material, share token plaintext,
  and `client_secret` values must never appear in any log event. Log call-sites touching
  these values must be reviewed to confirm only IDs/counts are logged.
- **No full prompts**: The `input`/`output` summary fields are capped at 200 characters and
  are optional. The full message list from a `ChatCompletionRequest` must not be serialised
  into any log event.
- **`trace_id` is not a secret**: It is a UUID used for log correlation only. It is safe to
  expose in HTTP response headers (`X-Trace-ID`) and log files.
- **`GET /metrics` is unauthenticated**: It is already in the `EXEMPT_PATHS` frozenset. The
  endpoint must not expose per-user data — only aggregate counters and backend states.
- **Log file permissions**: The JSONL log file must be created with mode `0640` (owner
  read/write, group read). This is enforced by the `telemetry.py` configuration.
- **Log injection**: All string values written to the JSON log are serialised by
  `structlog`'s `JSONRenderer`, which escapes special characters. No manual string
  concatenation in log events.
- **Rate limiting**: `/metrics` does not count against inference rate limits. It does count
  against the IP-based `slowapi` limiter in the auth-service if that service ever adds a
  similar endpoint.
- **No PII in context variables**: `trace_id` and `service` name are the only values bound
  at the middleware level via `bind_contextvars`. User IDs and client IDs are bound only at
  the event level in the router, never globally.

## Acceptance Criteria

### AC-1 — structlog unification: gateway

- [x] **AC-1**: Given the gateway application starts, when any log event is emitted by any
  module in `prometheus_gateway` (including `router.py`, `rate_limit_middleware.py`,
  `auth/middleware.py`, `auth/jwks.py`, `models/backends.py`), then the event is rendered
  as a single-line JSON object containing at minimum `timestamp`, `level`, `service`, and
  `event` fields. No plaintext `logging.getLogger(__name__)` call-sites remain in
  `prometheus_gateway` except inside `telemetry.py` itself.

### AC-2 — structlog unification: auth-service

- [x] **AC-2**: Given the auth-service application starts, when any log event is emitted by
  any module in `prometheus_auth`, then the event is rendered as a single-line JSON object
  containing at minimum `timestamp`, `level`, `service`, and `event` fields. The
  `logging.basicConfig()` call in `main.py` lifespan is removed and replaced by a call to
  `telemetry.configure_logging()`.

### AC-3 — structlog unification: manager

- [x] **AC-3**: Given the manager performs any lifecycle or download operation, when a log
  event is emitted by `lifecycle.py` or `downloader.py`, then the event is rendered as a
  single-line JSON object containing at minimum `timestamp`, `level`, `service` (`"manager"`),
  and `event` fields. All `logging.getLogger(__name__)` imports in those modules are replaced
  with `telemetry.get_logger(__name__)`.

### AC-4 — `get_logger()` factory

- [x] **AC-4**: Given `telemetry.get_logger("prometheus_gateway.router")` is called, when the
  returned logger emits an event, then the JSON output includes `"service": "gateway"` (static,
  set at configure time) and the `event` field matches the string passed to the log call.

### AC-5 — Mandatory schema fields

- [x] **AC-5**: Given any log event from any service, then the JSON object contains exactly
  these mandatory fields: `timestamp` (ISO 8601 with UTC timezone), `level` (lowercase string),
  `service` (one of `"gateway"`, `"auth-service"`, `"manager"`), `event` (dot-notation string),
  `trace_id` (UUID string or `"none"` when no request context is available).

### AC-6 — `trace_id` generation: gateway

- [x] **AC-6**: Given an inbound HTTP request to the gateway **without** an `X-Trace-ID`
  header, when the request passes through the middleware, then a new UUID4 `trace_id` is
  generated, bound to structlog context variables, and returned in the `X-Trace-ID` response
  header. All log events emitted during that request carry the same `trace_id`.

### AC-7 — `trace_id` adoption: gateway

- [x] **AC-7**: Given an inbound HTTP request to the gateway **with** a valid
  `X-Trace-ID: <uuid>` header, when the request passes through the middleware, then the
  provided value is adopted as the `trace_id` (not replaced). The adopted value must be a
  valid UUID4; an invalid value is rejected and a new UUID4 is generated instead (the
  original invalid value is discarded silently — no error response).

### AC-8 — `trace_id` forwarded to llama.cpp

- [x] **AC-8**: Given the gateway proxies an inference request to a llama.cpp backend, when
  the outbound HTTP request is constructed in `router.py`, then the `X-Trace-ID: <trace_id>`
  header is included in the forwarded request.

### AC-9 — `trace_id` propagation: auth-service

- [x] **AC-9**: Given the auth-service receives an HTTP request with an `X-Trace-ID` header,
  when the request is processed, then the header value is bound as `trace_id` in structlog
  context variables for the duration of that request. If the header is absent, a new UUID4
  is generated.

### AC-10 — Inference event fields

- [x] **AC-10**: Given a successful non-streaming inference request completes in the gateway,
  when the `inference.complete` event is logged, then the JSON event contains:
  `model`, `tokens_prompt`, `tokens_completion`, `tokens_total`, `latency_ms`,
  `tokens_per_second`, `user_id`, `client_id`, `trace_id`. The `tokens_prompt` and
  `tokens_completion` values match the values returned in the llama.cpp response
  `usage` object (or 0 if absent).

### AC-11 — Auth token event fields

- [x] **AC-11**: Given the auth-service issues an OAuth2 token, when the event is logged, then
  the JSON event has `"event": "auth.token_issued"` and includes `client_id` and `trace_id`.
  No JWT payload, no private key reference, no `client_secret` value appears in the log
  event.

### AC-12 — Auth client lifecycle event fields

- [x] **AC-12**: Given the auth-service creates or rotates a client, when the event is logged,
  then the JSON event has `"event": "auth.client_created"` or `"auth.client_rotated"` and
  includes `client_id`. No secret values appear in the log event.

### AC-13 — Share token audit events

- [x] **AC-13**: Given a share token is created, used, expired, or revoked in the auth-service,
  when the event is logged, then the JSON event has an `action` field set to one of
  `share_token_created`, `share_token_used`, `share_token_expired`, `share_token_revoked`
  and includes `client_id`. The raw share token string is **not** logged.

### AC-14 — No secrets in logs

- [x] **AC-14**: Given any log event from any service, then the event does not contain any
  of: JWT token strings, `client_secret` values, RSA private key material, share token
  plaintext, password hashes, or session cookies. A test must assert that the
  `inference.complete` and `auth.token_issued` log events do not contain the strings
  `"secret"`, `"private"`, `"password"`, `"bearer"`, `"-----BEGIN"`.

### AC-15 — No full prompts in logs

- [x] **AC-15**: Given a `ChatCompletionRequest` with messages totalling more than 200
  characters, when the `inference.complete` event is logged, then the log event does not
  contain any message content beyond the optional 200-character summary fields `input` and
  `output`. The full messages list is never serialised into a log event.

### AC-16 — Log file output

- [x] **AC-16**: Given `LOG_FILE_PATH` is set to a writable path (e.g.
  `./logs/gateway.jsonl`), when the service starts and emits at least one log event, then:
  (a) the file is created if it does not exist, (b) each line in the file is a valid
  JSON object, (c) the same events also appear on stdout.

### AC-17 — Log file rotation

- [x] **AC-17**: Given `LOG_MAX_BYTES=1048576` (1 MB) and `LOG_BACKUP_COUNT=3`, when the
  log file grows beyond 1 MB, then it is rotated automatically (`.jsonl.1`, `.jsonl.2`,
  `.jsonl.3`). At most 3 backup files are retained.

### AC-18 — Stdout fallback when file path is unwritable

- [x] **AC-18**: Given `LOG_FILE_PATH` is set to a path whose parent directory does not
  exist and cannot be created, when the service starts, then it emits a single warning to
  stdout (`"log_file_unavailable"`), continues starting normally, and all subsequent log
  events appear on stdout only (no crash / no startup failure).

### AC-19 — `GET /metrics` — basic counters

- [x] **AC-19**: Given the gateway has served at least one successful inference request,
  when `GET /metrics` is called, then the response is `200 OK` with `Content-Type:
  application/json` and the body contains `inference.requests_total >= 1`,
  `inference.tokens_prompt_total >= 1`, `inference.tokens_completion_total >= 1`.

### AC-20 — `GET /metrics` — latency percentiles

- [x] **AC-20**: Given the gateway has served at least 10 inference requests with recorded
  latencies, when `GET /metrics` is called, then the response body contains
  `inference.latency_p50_ms`, `inference.latency_p95_ms`, `inference.latency_p99_ms` as
  non-negative integer values.

### AC-21 — `GET /metrics` — no per-user data

- [x] **AC-21**: Given `GET /metrics` is called by an unauthenticated client, when the
  response is returned, then the body does not contain individual `user_id`, `client_id`,
  or per-request data. Only aggregate counters and named backend states are present.

### AC-22 — `GET /metrics` — backend circuit state

- [x] **AC-22**: Given a backend named `"llama3-8b-q4-local"` is registered and its circuit
  breaker is in state `"closed"`, when `GET /metrics` is called, then the response body
  contains `backends["llama3-8b-q4-local"]["circuit_state"] == "closed"`.

### AC-23 — Langfuse-ready field names

- [x] **AC-23**: Given the `inference.complete` log event schema, then the fields
  `trace_id`, `span_id`, `model`, `tokens_prompt`, `tokens_completion`, `tokens_total`,
  `latency_ms` are present and named exactly as specified (no aliases, no camelCase).
  A Langfuse Phase 2 exporter must be able to map these fields directly to the Langfuse
  API without renaming.

### AC-24 — `telemetry.py` configure idempotency

- [x] **AC-24**: Given `telemetry.configure_logging()` is called twice (e.g. once at app
  startup and once in a test fixture), then the second call is a no-op and does not add
  duplicate handlers or duplicate structlog processors. Log output is not duplicated.

### AC-25 — `structlog` added to auth-service and manager dependencies

- [x] **AC-25**: Given `auth-service/pyproject.toml` and `runtime/manager/pyproject.toml`,
  then both list `structlog>=24.1` as a runtime dependency, and `uv.lock` is updated
  accordingly.

### AC-26 — Existing `request_id` preserved

- [x] **AC-26**: Given the gateway middleware already sets `request.state.request_id` and
  returns `X-Request-ID` in the response, when the `trace_id` middleware is added, then
  `request_id` continues to be set and returned unchanged. The `X-Request-ID` and
  `X-Trace-ID` response headers coexist in every response.

## Open Questions

> All questions resolved — see decisions below.

**Q1 — trace_id in error bodies?** → YES (AC-27 added).
**Q2 — X-Trace-ID to manager REST API?** → YES (AC-28 added).
**Q3 — LOG_INCLUDE_PROMPT_SUMMARY kill-switch?** → YES, default false (AC-29 added).
**Q4 — In-process metrics sufficient?** → Yes for Phase 1.

## Post-Launch Fixes (implemented in the same branch)

### AC-30 — Non-empty error field in background task logs

- [x] **AC-30**: Given `manager_sync` background tasks fail (e.g. `httpx.ConnectError`
  with an empty `str()` representation), when the error is logged, then the `error` field
  is non-empty. Implementation: `str(exc) or repr(exc)` fallback + `exc_type` field added
  to `manager_sync.poll_error` and `manager_sync.initial_sync_failed` events.

### AC-31 — Background task trace_id: meaningful prefix instead of `"none"`

- [x] **AC-31**: Given the gateway emits log events outside an HTTP request context
  (startup, background manager-sync poll), when those events are rendered, then `trace_id`
  is not `"none"` but carries a short prefixed identifier:
  - Startup context: `startup-<8-char-uuid>` (bound once per process start)
  - Per-poll cycle: `poll-<8-char-uuid>` (bound at the start of each `_poll_loop` iteration)
  - Live HTTP request: full UUID4 (set by `TraceIDMiddleware`)
  - Uvicorn stdlib bridge logs: `"none"` (unavoidable — no asyncio context)

### AC-32 — Validation failure logging in UI session

- [x] **AC-32**: Given `_validate_session()` in `ui/router.py` fails for any reason
  (invalid signature, missing `ui:chat` scope, expired token, no key source), when the
  failure occurs, then a structured log event is emitted with the failure reason.
  Events added: `ui.validate_session.no_key_source`, `ui.validate_session.invalid_signature`,
  `ui.validate_session.token_expired`, `ui.validate_session.missing_scope`
  (includes `required`, `actual`, `sub` fields).

### AC-27 — `trace_id` in RFC 9457 error responses

- [x] **AC-27**: Given the gateway returns an RFC 9457 error response (4xx or 5xx), when
  the response body is inspected, then it contains a `trace_id` field matching the
  `trace_id` bound in the structlog context for that request. The field is absent from
  responses to requests that pre-date the middleware (e.g. startup probe errors before
  middleware initialisation).

### AC-28 — `trace_id` propagated to manager REST API

- [x] **AC-28**: Given the gateway calls the manager REST API (e.g. `manager_sync.py`
  `ensure_model_ready`), when the outbound HTTP request is constructed, then the
  `X-Trace-ID: <trace_id>` header is included, and the manager reads the header value and
  binds it as `trace_id` in its structlog context for that operation. The manager REST API
  server forwards the header to its operation log events.

### AC-29 — `LOG_INCLUDE_PROMPT_SUMMARY` kill-switch

- [x] **AC-29**: Given `LOG_INCLUDE_PROMPT_SUMMARY` is not set or is set to `false`,
  when the `inference.complete` event is logged, then the `input` and `output` summary
  fields are absent from the JSON event. Given `LOG_INCLUDE_PROMPT_SUMMARY=true`, then
  the `input` field contains at most the first 200 characters of the first user message
  and the `output` field contains at most the first 200 characters of the assistant
  response. The kill-switch applies to the gateway only; manager and auth-service never
  log message content.

## References

- Related specs: `memory/specs/001-gateway-core.md`, `memory/specs/005-auth-service.md`,
  `memory/specs/007-rate-limiting-and-throughput.md`, `memory/specs/008-llama-server-manager.md`
- `structlog` documentation: https://www.structlog.org/
- Langfuse trace API: https://langfuse.com/docs/tracing
- W3C Trace Context (`traceparent`): https://www.w3.org/TR/trace-context/ (Phase 2 reference)
- RFC 9457 Problem Details: https://www.rfc-editor.org/rfc/rfc9457
