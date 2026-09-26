---
id: "006"
title: "Multi-Model Gateway"
status: closed
current-agent: ""
pipeline-log: []
created: 2026-03-28
updated: 2026-03-29
---

# 006 — Multi-Model Gateway

## Problem Statement

The Prometheus Gateway currently proxies **all** inference requests to a single llama.cpp
server instance identified by the `LLAMA_CPP_URL` environment variable. The model is chosen
by the client via the standard `"model"` field, but the gateway ignores this field for routing
— every request lands on the same backend regardless of which model was asked for.

This blocks two critical production requirements:

1. **Latency tiers**: low-priority, interactive tasks (e.g., autocomplete, short Q&A) should
   go to a small fast model (1B params), while complex reasoning tasks should go to a capable
   large model (8B params). Today both compete for the same GPU resources.
2. **Resource isolation**: requests that saturate the large model's context window must not
   delay fast-path traffic to the small model.

Without multi-model routing the platform cannot fulfil SLA commitments for latency-sensitive
clients, and operators cannot run more than one model at a time on the same host.

## Goals

- [ ] Route each inference request to the llama-server instance serving the requested model
- [ ] Extend `runtime/models/registry.yaml` to declare which backend URL serves each model
- [ ] Advertise only **active** (loaded) models via `GET /v1/models`
- [ ] Return HTTP 503 `model-not-loaded` when a registered model has no running backend
- [ ] Remove the single `LLAMA_CPP_URL` env var; backend URLs live in `registry.yaml`
- [ ] Validate that all configured backend URLs resolve to loopback or the Docker-internal hostname
- [ ] Provide `runtime/scripts/start-all-servers.sh` to launch multiple llama-server instances
- [ ] Include `model` and `backend_url` in all structured inference log entries
- [ ] Expose `GET /v1/backends` (admin-scoped) listing all registered models with their `backend_url` and activation status
- [ ] Use a shared `httpx.AsyncClient` connection pool per backend URL to reduce connection overhead
- [ ] `start-all-servers.sh` auto-writes `backend_url` into `registry.yaml` after launching each instance

## Non-Goals

- Dynamic model loading/unloading at runtime without a process restart
- Load balancing multiple instances of the same model (horizontal scaling)
- Health-check polling of backends on a schedule (liveness checked per-request only)
- GPU memory reservation or admission control across model processes
- Any changes to authentication, JWT validation, or rate limiting middleware
- Changes to the `POST /v1/chat/completions` request or response schema

## Proposed Solution

Extend `registry.yaml` with an optional `backend_url` field per model. A model is **active**
when `backend_url` is set; it is **registered-but-inactive** when `backend_url` is absent.

At startup the gateway loads the registry and builds a `model_id → backend_url` routing table.
On every inference request:

1. Look up `model_id` in the routing table.
2. If absent from the registry entirely → 400 `unknown-model`.
3. If registered but no `backend_url` → 503 `model-not-loaded`.
4. If `backend_url` present → forward the request to `{backend_url}/v1/chat/completions`.
5. If the forward fails (connection refused, timeout) → 503 `backend-unavailable`.

`GET /v1/models` returns only models that have `backend_url` set.

The runtime side uses one llama-server process per model, each bound to a unique loopback port.
`start-server.sh` remains single-model. A new `start-all-servers.sh` wrapper launches multiple
instances from separate env files.

```
[Client]
   │  POST /v1/chat/completions  {"model": "llama3-1b-q4-local", ...}
   ▼
[Prometheus Gateway :8000]
   │  registry.lookup("llama3-1b-q4-local") → http://127.0.0.1:8080
   ├──→ [llama-server :8080]  (Llama 3.2 1B — fast path)
   │
   │  POST /v1/chat/completions  {"model": "llama3-8b-q4-local", ...}
   └──→ [llama-server :8081]  (Llama 3 8B — capable path)
```

### Key Design Decisions

| Decision | Option Chosen | Rationale |
|----------|---------------|-----------|
| One process per model vs. single multi-model process | One process per model | llama.cpp loads exactly one model per process; this is a hard architectural constraint |
| How the gateway discovers backend URLs | Optional `backend_url` field in `registry.yaml` | Keeps model metadata and deployment config co-located in one file; no extra config surface |
| Separate env var per model vs. `registry.yaml` | `registry.yaml` | Avoids unbounded env var sprawl; single source of truth already owned by the registry |
| Active vs. inactive distinction | `backend_url` absent = inactive | Opt-in activation; models can be registered without a running process (e.g., staged for future deployment) |
| `LLAMA_CPP_URL` backward compat | Breaking change — env var removed | The old var is meaningless in multi-model mode; keeping it creates ambiguity about which model it applies to. Migration path: set `backend_url` in registry.yaml |
| Backend URL security constraint | Must be `127.0.0.1`, `::1`, or `host.docker.internal` | llama-server must never be reachable from outside the host; gateway enforces this at load time |
| Unreachable backend vs. inactive model | Distinct error codes | `model-not-loaded` (503) = operator intent: model not started. `backend-unavailable` (503) = unexpected: model should be running but isn't |
| `start-all-servers.sh` design | Thin wrapper: sources env files, calls `start-server.sh` in background per model | Re-uses all existing per-model validation logic; no duplication; each instance gets its own PID file |
| `GET /v1/backends` admin endpoint | New endpoint returning all registered models + `backend_url` + active/inactive status | Operators diagnose routing issues without reading YAML; admin-scoped to prevent information leakage |
| Connection pool per backend | Shared `httpx.AsyncClient` per `backend_url`, created at startup | Avoids TCP handshake overhead on every inference request; one client per active model |
| `start-all-servers.sh` auto-updates `registry.yaml` | Script writes `backend_url` in-place after starting each instance | One-step dev workflow; eliminates manual port synchronisation between env files and registry |

## API Contract

No new endpoints are introduced. Two existing endpoints change behaviour:

### `GET /v1/models`

**Before**: returns all models in `registry.yaml`.  
**After**: returns only models whose `backend_url` is set (active models).

Response schema is unchanged:

```json
{
  "object": "list",
  "data": [
    {
      "id": "llama3-1b-q4-local",
      "object": "model",
      "owned_by": "prometheus",
      "context_length": 8192,
      "family": "llama3",
      "quantization": "Q4_0"
    }
  ]
}
```

### `POST /v1/chat/completions` — new error cases

| Condition | HTTP Status | `type` suffix | `title` |
|-----------|-------------|---------------|---------|
| Model not in registry | 400 | `unknown-model` | Unknown Model *(unchanged)* |
| Model registered, no `backend_url` | 503 | `model-not-loaded` | Model Not Loaded |
| Backend URL unreachable | 503 | `backend-unavailable` | Backend Unavailable *(unchanged)* |

New 503 `model-not-loaded` response body (RFC 9457):

```json
{
  "type": "https://prometheus.internal/errors/model-not-loaded",
  "title": "Model Not Loaded",
  "status": 503,
  "detail": "Model 'llama3-8b-q4-local' is registered but has no active backend. Contact the platform operator.",
  "instance": "/v1/chat/completions",
  "request_id": "b3d2a1e0-..."
}
```

> No OpenAPI file exists yet for the gateway. If one is introduced in a future spec it should
> document these error responses. The authoritative contract for now is this spec and the
> existing test suite.

### `GET /v1/backends` — admin diagnostics endpoint

**Auth**: requires `admin:read` scope.

```
GET /v1/backends
Authorization: Bearer <admin-JWT>
```

Response `200 OK`:

```json
{
  "object": "list",
  "data": [
    {
      "id": "llama3-1b-q4-local",
      "backend_url": "http://127.0.0.1:8080",
      "status": "active"
    },
    {
      "id": "llama3-8b-q4-local",
      "backend_url": "http://127.0.0.1:8081",
      "status": "active"
    },
    {
      "id": "mistral-7b-v02-q4-local",
      "backend_url": null,
      "status": "inactive"
    }
  ]
}
```

`status` values:
- `active` — `backend_url` is set and passed loopback validation
- `inactive` — registered in `registry.yaml` but no `backend_url`
- `invalid` — `backend_url` set but failed loopback validation at load time

## Data Model

### `registry.yaml` — extended model entry

Add an **optional** `backend_url` field. Absent = inactive; present = active.

```yaml
models:
  - id: "llama3-1b-q4-local"
    path: "/Users/.../Llama-3.2-1B-Instruct-Q4_0.gguf"
    context_length: 8192
    family: llama3
    quantization: Q4_0
    backend_url: "http://127.0.0.1:8080"      # ← new optional field

  - id: "llama3-8b-q4-local"
    path: "/Users/.../Meta-Llama-3-8B-Instruct.Q4_0.gguf"
    context_length: 8192
    family: llama3
    quantization: Q4_0
    backend_url: "http://127.0.0.1:8081"      # ← new optional field

  - id: "mistral-7b-v02-q4-local"
    path: "/Users/.../mistral-7b-instruct-v0.2.Q4_0.gguf"
    context_length: 32768
    family: mistral
    quantization: Q4_0
    # no backend_url → registered but not loaded; absent from GET /v1/models
```

### `ModelEntry` dataclass (`gateway/src/prometheus_gateway/models/registry.py`)

Add one field:

```python
@dataclass(frozen=True)
class ModelEntry:
    id: str
    path: str
    context_length: int
    family: str
    quantization: str
    backend_url: str | None = None   # ← new field; None = inactive
```

### `ModelRegistry` — new method

```python
def list_active_models(self) -> list[ModelEntry]:
    """Return only entries with backend_url set."""
    return [m for m in self._models.values() if m.backend_url is not None]
```

`list_models()` is preserved for internal use (e.g., validation); `list_active_models()` drives
`GET /v1/models` and the router's routing table.

### `Settings` (`gateway/src/prometheus_gateway/config.py`)

Remove:
```python
llama_cpp_url: str = "http://host.docker.internal:8080"
```

Add nothing — backend URLs are sourced entirely from `registry.yaml`. If `LLAMA_CPP_URL` is
present in the environment at startup, the gateway logs a deprecation warning and ignores it.

### `BackendPool` (`gateway/src/prometheus_gateway/models/backends.py` — new module)

One `httpx.AsyncClient` per active `backend_url`, created at startup and reused for all requests:

```python
class BackendPool:
    """Shared httpx.AsyncClient per backend URL. Created at startup, closed on shutdown."""
    def __init__(self) -> None:
        self._clients: dict[str, httpx.AsyncClient] = {}

    def get(self, backend_url: str) -> httpx.AsyncClient:
        if backend_url not in self._clients:
            self._clients[backend_url] = httpx.AsyncClient(
                base_url=backend_url, timeout=120.0
            )
        return self._clients[backend_url]

    async def aclose(self) -> None:
        for client in self._clients.values():
            await client.aclose()
```

`BackendPool` is instantiated once in `create_app()` and stored on `app.state`. The lifespan
context manager closes all clients on shutdown. `create_router` receives the pool and uses
`pool.get(entry.backend_url)` instead of creating a new client per request.

### `create_router` signature (`gateway/src/prometheus_gateway/router.py`)

```python
# Before
def create_router(registry: ModelRegistry, llama_cpp_url: str) -> APIRouter:

# After
def create_router(registry: ModelRegistry) -> APIRouter:
```

The function uses `entry.backend_url` for routing; `llama_cpp_url` is removed.

### `runtime/scripts/start-all-servers.sh` — new script

```
Usage: bash runtime/scripts/start-all-servers.sh <env-file-1> [env-file-2 ...]

Each env file must set:
  PROMETHEUS_MODEL_PATH   — path to .gguf file
  PROMETHEUS_MODEL_ALIAS  — model id (must match registry.yaml id)
  PROMETHEUS_LLAMA_PORT   — unique port for this instance (e.g. 8080, 8081)

The script:
  1. Validates that PROMETHEUS_LLAMA_PORT is unique across all supplied env files.
  2. For each env file: sources it and runs start-server.sh in the background.
  3. Writes a PID file to /tmp/prometheus-<PROMETHEUS_MODEL_ALIAS>.pid per instance.
  4. Updates `backend_url: http://127.0.0.1:<PORT>` in-place in `registry.yaml` for each
     successfully started model (Python one-liner via `uv run python` to avoid sed portability
     issues across macOS/RHEL).
  5. On SIGTERM/SIGINT, kills all child PIDs cleanly.
```

## Security Considerations

- **Loopback-only enforcement**: At registry load time, the gateway rejects any `backend_url`
  whose hostname is not `127.0.0.1`, `::1`, or `host.docker.internal`. An invalid entry is
  logged as an error and treated as inactive (not surfaced in `GET /v1/models` or routable).
  The gateway does not fail to start — other valid models remain available.

- **No SSRF via `backend_url`**: Because `backend_url` comes from the operator-controlled
  `registry.yaml` (not from client request data), it is not a client-controlled SSRF vector.
  The loopback enforcement is a defence-in-depth measure against misconfiguration or
  supply-chain tampering of the config file.

- **Model field not sanitized for routing**: The `"model"` field from the client body is used
  as a lookup key in the in-memory registry map. No file-system path construction or shell
  interpolation occurs from this value — registry lookup is a pure dict access.

- **Backwards-compatibility break**: Removing `LLAMA_CPP_URL` means that a deployment that
  relied on this variable but has not set `backend_url` in `registry.yaml` will start
  successfully but serve zero active models. `GET /v1/models` returns an empty list and every
  inference request returns 503. Operators are warned by the startup deprecation log line.

- **Log hygiene**: `backend_url` is logged on inference requests. Ensure no credentials are
  ever embedded in `backend_url` (it should be a plain `http://host:port` with no query
  string or auth fragment).

- **Auth and rate limiting**: Unchanged. All existing JWT validation, scope enforcement, and
  rate limiting applies equally regardless of which backend is targeted.

## Acceptance Criteria

- [ ] **AC-1**: Given `registry.yaml` has exactly two models with `backend_url` set and one
  model without, when `GET /v1/models` is called (no auth required), then the response contains
  exactly the two active models and the inactive model is absent.

- [ ] **AC-2**: Given model `llama3-1b-q4-local` has `backend_url: http://127.0.0.1:8080` and
  model `llama3-8b-q4-local` has `backend_url: http://127.0.0.1:8081`, when a valid
  `POST /v1/chat/completions` request with `"model": "llama3-1b-q4-local"` is received, then
  the gateway forwards the request to `http://127.0.0.1:8080/v1/chat/completions` and NOT to
  `:8081`.

- [ ] **AC-3**: Given model `llama3-8b-q4-local` has `backend_url: http://127.0.0.1:8081`,
  when a valid `POST /v1/chat/completions` request with `"model": "llama3-8b-q4-local"` is
  received, then the gateway forwards to `http://127.0.0.1:8081/v1/chat/completions` and returns
  the backend's response to the client.

- [ ] **AC-4**: Given a model that exists in `registry.yaml` but has no `backend_url` field,
  when `POST /v1/chat/completions` is called for that model, then the response is HTTP 503 with
  `Content-Type: application/problem+json` and `"type"` ending in `model-not-loaded`.

- [ ] **AC-5**: Given a model ID that is not present in `registry.yaml` at all, when
  `POST /v1/chat/completions` is called, then the response is HTTP 400 with `"type"` ending in
  `unknown-model` (existing behaviour preserved).

- [ ] **AC-6**: Given a model with `backend_url: http://127.0.0.1:8080` where no process is
  listening on port 8080, when `POST /v1/chat/completions` is called for that model, then the
  response is HTTP 503 with `"type"` ending in `backend-unavailable`.

- [ ] **AC-7**: Given `"stream": true` is set in the request for an active model, when
  `POST /v1/chat/completions` is called, then the gateway streams SSE events received from the
  correct backend to the client without buffering.

- [ ] **AC-8**: Given any successful inference request, when the request completes, then the
  structured log entry contains the fields `model` (the requested model ID) and `backend_url`
  (the URL the request was forwarded to).

- [ ] **AC-9**: Given `registry.yaml` contains a model with `backend_url: http://192.168.1.10:8080`
  (a non-loopback, non-`host.docker.internal` hostname), when the gateway starts and loads the
  registry, then that model is not included in `GET /v1/models`, an error-level log entry is
  emitted naming the invalid model, and the gateway continues serving other valid active models.

- [ ] **AC-10**: Given the `LLAMA_CPP_URL` environment variable is set, when the gateway starts,
  then a deprecation warning is logged (level WARN, message includes `LLAMA_CPP_URL` and
  `deprecated`) and the variable is otherwise ignored — routing is driven solely by `backend_url`
  in `registry.yaml`.

- [ ] **AC-11**: Given `runtime/scripts/start-all-servers.sh` is called with two env files that
  set different `PROMETHEUS_LLAMA_PORT` values, then two llama-server processes are launched in
  the background, each writes its own PID file to `/tmp/prometheus-<ALIAS>.pid`, and the script
  exits 0.

- [ ] **AC-12**: Given `start-all-servers.sh` is called with two env files that have the same
  `PROMETHEUS_LLAMA_PORT` value, then the script exits 1 with an error message identifying the
  port conflict before launching any processes.

- [ ] **AC-13**: Given `create_router` is called without a `llama_cpp_url` argument, when
  the gateway application is created via `create_app()`, then no `AttributeError` or import
  error occurs and the app starts successfully.

- [ ] **AC-14**: Given a valid admin-scoped JWT, when `GET /v1/backends` is called, then the
  response is HTTP 200 with a JSON body listing all registered models, each with `id`,
  `backend_url` (or `null`), and `status` (`active`, `inactive`, or `invalid`).

- [ ] **AC-15**: Given two simultaneous `POST /v1/chat/completions` requests for the same
  model, when both complete successfully, then only one `httpx.AsyncClient` instance exists
  for that backend's URL (shared pool, not two separate clients).

## Open Questions

- [x] **Q1**: Should the gateway expose a private `GET /v1/backends` admin endpoint?
  **Resolved: YES.** Endpoint added to API Contract above. Requires `admin:read` scope.
  Returns all registered models with `backend_url` and `active`/`inactive`/`invalid` status.

- [x] **Q2**: Should a shared connection pool per backend be introduced?
  **Resolved: YES.** `BackendPool` module added to Data Model above. One
  `httpx.AsyncClient` per `backend_url`, created at startup, reused across all requests.

- [x] **Q3**: Should `start-all-servers.sh` auto-update `registry.yaml`?
  **Resolved: YES.** Script writes `backend_url` in-place after each successful launch.
  One-step dev workflow: run the script → registry updated → gateway ready.

## Implementation Notes

### Files to modify

| File | Change |
|------|--------|
| `runtime/models/registry.yaml` | Add `backend_url` field to desired model entries |
| `gateway/src/prometheus_gateway/models/registry.py` | Add `backend_url: str \| None = None` to `ModelEntry`; add `list_active_models()` to `ModelRegistry`; validate host on load |
| `gateway/src/prometheus_gateway/router.py` | Remove `llama_cpp_url` param from `create_router`; accept `BackendPool`; look up `entry.backend_url`; add `model-not-loaded` 503 branch; add `backend_url` to log; add `GET /v1/backends` route |
| `gateway/src/prometheus_gateway/config.py` | Remove `llama_cpp_url` field; add startup check for deprecated env var |
| `gateway/src/prometheus_gateway/main.py` | Instantiate `BackendPool`; store on `app.state`; lifespan closes pool; update `create_router(registry, pool)` call |
| `gateway/tests/test_gateway_core.py` | Update fixtures; add AC-1 through AC-15 test cases |

### Files to create

| File | Purpose |
|------|----------|
| `gateway/src/prometheus_gateway/models/backends.py` | `BackendPool` class (AC-15) |
| `runtime/scripts/start-all-servers.sh` | Launch multiple llama-server instances; auto-update `registry.yaml` (AC-11, AC-12, Q3) |

### Suggested test fixture pattern

```python
# conftest.py — inject a two-model registry with mock backend URLs
@pytest.fixture()
def multi_model_registry(tmp_path):
    yaml_content = """
models:
  - id: small-model
    path: /dev/null
    context_length: 4096
    family: llama3
    quantization: Q4_0
    backend_url: http://127.0.0.1:18080
  - id: large-model
    path: /dev/null
    context_length: 8192
    family: llama3
    quantization: Q4_0
    backend_url: http://127.0.0.1:18081
  - id: inactive-model
    path: /dev/null
    context_length: 4096
    family: llama3
    quantization: Q4_0
"""
    registry_file = tmp_path / "registry.yaml"
    registry_file.write_text(yaml_content)
    return ModelRegistry(registry_file)
```

### Migration guide (for existing deployments)

1. Remove `LLAMA_CPP_URL` from `gateway/.env`.
2. In `registry.yaml`, add `backend_url: http://127.0.0.1:8080` to the model currently being served.
3. Restart the gateway.

### References

- Related specs: [memory/specs/001-gateway-core.md](001-gateway-core.md), [memory/specs/003-llama-cpp-runtime.md](003-llama-cpp-runtime.md)
- llama.cpp single-model-per-process constraint: [memory/decisions/2026-03-28-llama-cpp-bare-metal.md]../decisions/2026-03-28-llama-cpp-bare-metal.md)
- OpenAPI error format: RFC 9457 Problem Details for HTTP APIs
