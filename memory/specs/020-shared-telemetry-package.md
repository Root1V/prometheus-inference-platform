---
id: "020"
title: "prometheus-telemetry — Shared Observability Package"
status: closed
current-agent: ""
pipeline-log: []
created: 2026-04-12
updated: 2026-04-12
---

# 020 — prometheus-telemetry — Shared Observability Package

## Problem Statement

The platform has four components, each with its own observability needs:

| Component | Runtime | Log destination | Trace propagation |
|-----------|---------|----------------|-------------------|
| **gateway** | ASGI / Podman :8000 | stdout → JSON | `X-Trace-ID` header via `TraceIDMiddleware` |
| **auth-service** | ASGI / Podman :9000 | stdout → JSON | `X-Trace-ID` header via `TraceIDMiddleware` |
| **manager API** | FastAPI / bare-metal :8010 | stdout → JSON | `X-Trace-ID` forwarded from gateway |
| **manager TUI** | Textual / terminal | rotating file (fix/019) | `trace_id` bound per worker thread |

Despite these different runtime profiles, all four components carry a full copy of the
same 9-symbol observability core — duplicated across 819 lines in three independent
`telemetry.py` files (`manager API` and `manager TUI` share one Python package but both
consume the same duplicated code):

```
gateway/src/prometheus_gateway/telemetry.py    350 lines
auth-service/src/prometheus_auth/telemetry.py  184 lines
runtime/manager/src/prometheus_manager/        285 lines
  telemetry.py
```

Consequences:
- A bug fix must be applied **three times**.  The `service` field was absent from all
  manager log events until fix/019 because the manager's copy had silently drifted.
- There is no single place to add a new exporter (Langfuse, OTEL/Tempo).  Each
  integration must be replicated to every copy.
- `TraceIDMiddleware` hard-codes the `service` name inside `__call__` in all three
  files, making the class non-portable and the hardcoding invisible to callers.
- `auth-service` lives outside the uv workspace and maintains a separate `.venv`,
  creating a second install surface to keep in sync.

## Goals

- [x] G-1: Create `telemetry/` as a first-class uv workspace member
  (directory name `telemetry/`, Python package name `prometheus_telemetry`).
- [x] G-2: Migrate the 9 duplicated symbols into `prometheus_telemetry/core.py` with
  unified, improved signatures.
- [x] G-3: Each of the four components replaces duplicated code with imports from
  `prometheus_telemetry`; nothing is duplicated after the migration.
- [x] G-4: Add `auth-service` to the uv workspace so it shares the root `.venv`.
- [x] G-5: All existing tests pass unchanged after the migration (zero regression).
- [x] G-6: The shared package has its own test suite, linting, and type-checking.
- [x] G-7: manager API and manager TUI emit a `component` field (`"api"` / `"tui"`) in
  every log event so they can be filtered independently in dashboards without regex.

## Non-Goals

- **Langfuse integration is deferred to spec-021.**  `telemetry/exporters/` is not
  created in this spec.  The package layout leaves room for it.
- Will not add new API endpoints or change any public HTTP contract.
- Will not change the JSON log format — output is identical before and after migration.
- Will not restructure `runtime/manager/` into multiple packages.
- `MetricsStore` stays in `gateway/src/prometheus_gateway/telemetry.py`.  It is used
  exclusively by the gateway and moving it would not reduce duplication.

## Proposed Solution

### Repository layout changes

```
/                              (root)
├── pyproject.toml             ← add "telemetry" and "auth-service" to workspace members
├── telemetry/                 ← NEW — shared observability package
│   ├── pyproject.toml
│   └── src/
│       └── prometheus_telemetry/
│           ├── __init__.py    ← public re-exports (configure_logging, get_logger,
│           │                     TraceIDMiddleware, bind_contextvars, clear_contextvars)
│           └── core.py        ← all 9 migrated symbols
├── gateway/
│   └── src/prometheus_gateway/
│       └── telemetry.py       ← MODIFIED: thin shim over prometheus_telemetry + MetricsStore
├── auth-service/
│   └── src/prometheus_auth/
│       └── telemetry.py       ← DELETED: callers switch to direct imports from prometheus_telemetry
└── runtime/manager/
    └── src/prometheus_manager/
        └── telemetry.py       ← MODIFIED: thin shim over prometheus_telemetry + TUI helpers
```

### `telemetry/pyproject.toml`

```toml
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "prometheus-telemetry"
version = "0.1.0"
description = "Shared structured observability for the Prometheus platform"
requires-python = ">=3.11"
dependencies = [
    "structlog>=25.5.0",
    "starlette>=0.36",   # ASGIApp, Scope, Receive, Send type aliases
]

[dependency-groups]
dev = [
    "pytest>=8",
    "pytest-asyncio>=0.23",
    "pytest-cov>=5",
    "httpx>=0.27",
    "ruff>=0.4",
    "mypy>=1.10",
]

[tool.setuptools.packages.find]
where = ["src"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
addopts = "--cov=prometheus_telemetry --cov-report=term-missing --cov-fail-under=90"

[tool.ruff]
line-length = 100
target-version = "py311"

[tool.mypy]
python_version = "3.11"
strict = true
```

### Root `pyproject.toml` — workspace members

```toml
[tool.uv.workspace]
members = ["gateway", "auth-service", "telemetry", "runtime/manager"]
```

`auth-service` joins the workspace so it can declare `prometheus-telemetry` as a
workspace dependency (resolved locally, no publish step required).

### `prometheus_telemetry/core.py` — unified symbols

All 9 symbols are migrated here.  Signatures are unified across the three sources,
adopting the best version of each.

#### `configure_logging()` — unified signature

```python
def configure_logging(
    service: str,                     # required — no default (no sensible global default)
    component: str | None = None,     # optional sub-component label (e.g. "api", "tui")
    log_level: str | None = None,     # falls back to LOG_LEVEL env var, then "INFO"
    log_file_path: str | None = None, # falls back to LOG_FILE_PATH env var
    log_max_bytes: int | None = None, # falls back to LOG_MAX_BYTES env var, then 10 MB
    log_backup_count: int | None = 5, # falls back to LOG_BACKUP_COUNT env var, then 5
) -> None:
```

Changes from the per-service originals:
- `service` is **required** (no default) — each caller must be explicit.
- `component` is **new**. When provided, `bind_contextvars(component=component)` is called
  alongside `bind_contextvars(service=service)`.  When absent the field is simply not
  present in log output — `_order_mandatory_fields` skips missing keys gracefully.
  Only the two manager entry points pass this parameter; gateway and auth-service do not.
- `log_level`, `log_max_bytes`, `log_backup_count` support `None` → env var fallback
  (adopted from manager's version; gateway and auth-service gain this for free).
- `uvicorn.access` is suppressed to `WARNING` in all cases — it was done in gateway and
  auth but not in manager API, which also runs under uvicorn.
- `_CONFIGURED` guard is module-level in `prometheus_telemetry.core`.  Behaviour is
  identical to before: second call is a no-op.  Tests that need a fresh state reset
  `import prometheus_telemetry.core as _c; _c._CONFIGURED = False` directly.

#### `TraceIDMiddleware` — parameterised `service`

The single most impactful fix.  Currently each copy hardcodes its service name:

```python
# Before (three copies, three different hardcoded strings)
structlog.contextvars.bind_contextvars(service="gateway")   # gateway copy
structlog.contextvars.bind_contextvars(service="auth-service")  # auth copy
structlog.contextvars.bind_contextvars(service="manager")   # manager copy
```

After migration, the class accepts `service` at construction time:

```python
class TraceIDMiddleware:
    def __init__(self, app: ASGIApp, service: str) -> None:
        self.app = app
        self._service = service
```

This is a **breaking change** — the three `add_middleware` call sites must be updated:

| File | Before | After |
|------|--------|-------|
| `gateway/src/prometheus_gateway/main.py:143` | `app.add_middleware(TraceIDMiddleware)` | `app.add_middleware(TraceIDMiddleware, service="gateway")` |
| `auth-service/src/prometheus_auth/main.py:86` | `app.add_middleware(TraceIDMiddleware)` | `app.add_middleware(TraceIDMiddleware, service="auth-service")` |
| `runtime/manager/src/prometheus_manager/api/app.py:26` | `app.add_middleware(TraceIDMiddleware)` | `app.add_middleware(TraceIDMiddleware, service="manager")` |

Additional improvements in the shared `TraceIDMiddleware`:
- Always stores `trace_id` on `request.state.trace_id` (was only gateway; now all three).
- Always returns `X-Trace-ID` in the response (was only gateway; now all three).

#### `prometheus_telemetry/__init__.py` — public API

```python
from .core import (
    TraceIDMiddleware,
    configure_logging,
    get_logger,
)
from structlog.contextvars import bind_contextvars, clear_contextvars

__all__ = [
    "TraceIDMiddleware",
    "configure_logging",
    "get_logger",
    "bind_contextvars",
    "clear_contextvars",
]
```

`bind_contextvars` and `clear_contextvars` are re-exported from structlog because
callers (manager TUI workers, `on_mount`) use them directly and having them under
one import reduces the number of structlog-internal paths scattered across the codebase.

### Per-component migration

#### `gateway/src/prometheus_gateway/telemetry.py` (shim + MetricsStore)

```python
# After migration — top of file
from prometheus_telemetry import (
    TraceIDMiddleware,
    configure_logging,
    get_logger,
)

# MetricsStore class stays here — gateway-only concern
# metrics_store singleton stays here
```

No changes to `main.py`, `router.py`, or any other caller — they still import from
`prometheus_gateway.telemetry`.

#### `auth-service/src/prometheus_auth/telemetry.py` (3-line shim — not deleted)

The file is kept as a minimal re-export shim so that the four router files
(`admin.py`, `admin_ui.py`, `oauth2.py`, `share.py`) that import via
`from ..telemetry import get_logger` continue to work without change:

```python
from prometheus_telemetry import TraceIDMiddleware, configure_logging, get_logger  # noqa: F401
```

`auth-service/src/prometheus_auth/main.py` is updated to import directly from
`prometheus_telemetry` (and pass `service="auth-service"` to `add_middleware`):

Additionally `auth-service/pyproject.toml` gains a dependency:

```toml
dependencies = [
    ...
    "prometheus-telemetry",   # workspace dependency — resolved locally by uv
]
```

#### `runtime/manager/src/prometheus_manager/telemetry.py` (shim + TUI helpers)

```python
# After migration — top of file
from prometheus_telemetry import (
    TraceIDMiddleware,
    configure_logging,
    get_logger,
)
# Re-export so existing callers (api/app.py, cli/main.py) are unaffected
__all__ = ["TraceIDMiddleware", "configure_logging", "get_logger",
           "new_trace_id", "redirect_logging_for_tui"]

# TUI-specific helpers stay here — they have no meaning for ASGI services
def new_trace_id() -> str: ...
def redirect_logging_for_tui(log_file_path: str | None = None) -> None: ...
def _silence_structlog() -> None: ...
```

`redirect_logging_for_tui` references `_SHARED_PROCESSORS` from the shared package:

```python
from prometheus_telemetry.core import _SHARED_PROCESSORS
```

No changes to `api/app.py`, `cli/main.py`, `tui/app.py` — they still import from
`prometheus_manager.telemetry` (except for the `add_middleware` call site fix above).

Call sites that change — `component` parameter added:

| File | Before | After |
|------|--------|-------|
| `runtime/manager/src/prometheus_manager/api/app.py:16` | `configure_logging(service="manager")` | `configure_logging(service="manager", component="api")` |
| `runtime/manager/src/prometheus_manager/cli/main.py:16` | `configure_logging(service="manager")` | unchanged — component bound per sub-command (see below) |

`component` for the TUI is bound in `tui/app.py` `on_mount()` because the TUI
re-binds the full context on every mount (fix/019 pattern).  `on_mount` is updated
to include `component="tui"`, and `_bind_worker_ctx` is updated to carry it into
each worker thread:

```python
# tui/app.py — on_mount() — before (fix/019)
structlog.contextvars.bind_contextvars(
    service="manager",
    trace_id=f"tui-session-{str(uuid.uuid4())[:8]}"
)

# tui/app.py — on_mount() — after
structlog.contextvars.bind_contextvars(
    service="manager",
    component="tui",
    trace_id=f"tui-session-{str(uuid.uuid4())[:8]}"
)

# tui/app.py — _bind_worker_ctx() — after (add component)
structlog.contextvars.bind_contextvars(
    service="manager",
    component="tui",
    trace_id=f"tui-{action}-{str(uuid.uuid4())[:8]}"
)
```

### Migration steps (safe, ordered)

Execute these in order; run the test suite after each step.

```
Step 0  BASELINE — capture log format before any change.
        With the stack running, collect one representative log line per component
        and save to a scratch file for diffing at Step 8:

          # gateway (podman logs or stdout)
          {"timestamp":"...","level":"info","service":"gateway","event":"...","trace_id":"...",...}
          # auth-service
          {"timestamp":"...","level":"info","service":"auth-service","event":"...","trace_id":"...",...}
          # manager API (pmgr api stdout)
          {"timestamp":"...","level":"info","service":"manager","event":"...","trace_id":"...",...}
          # manager TUI (runtime/logs/manager.log)
          {"timestamp":"...","level":"info","service":"manager","event":"...","trace_id":"...",...}

Step 1  Create telemetry/ package (new code only — no changes to existing files).
        uv sync → verify: python -c "from prometheus_telemetry import configure_logging"

Step 2  Add "auth-service" and "telemetry" to root pyproject.toml workspace members.
        Delete auth-service/.venv/ if present.
        uv sync → verify all packages install; run auth-service tests → 0 failures.

Step 3  Migrate gateway/telemetry.py (shim + MetricsStore).
        Update gateway/main.py: add_middleware(..., service="gateway").
        uv run pytest gateway/tests/ -v → 0 failures.

Step 4  Delete auth-service/telemetry.py.
        Update auth-service/main.py: direct imports + add_middleware service arg.
        Add prometheus-telemetry dep to auth-service/pyproject.toml.
        uv run pytest auth-service/tests/ -v → 0 failures.

Step 5  Migrate runtime/manager/telemetry.py (shim + TUI helpers).
        Update manager/api/app.py: configure_logging(component="api"),
                                    add_middleware(..., service="manager").
        Update manager/tui/app.py: on_mount + _bind_worker_ctx bind component="tui".
        uv run pytest runtime/manager/tests/ -v → 142 tests pass.

Step 6  Write tests for prometheus_telemetry (telemetry/tests/).
        uv run pytest telemetry/tests/ -v → all pass, coverage ≥ 90%.

Step 7  Full lint + typecheck across all packages.
        uv run ruff check telemetry/ gateway/ auth-service/ runtime/manager/
        uv run mypy telemetry/src/ gateway/src/ auth-service/src/ runtime/manager/src/

Step 8  LOG FORMAT VALIDATION — diff against Step 0 baseline.
        Restart each component and collect one new log line per component.
        Verify these invariants hold:

          gateway:       identical to baseline — no field added or removed
          auth-service:  identical to baseline — no field added or removed
          manager API:   identical to baseline EXCEPT "component":"api" appears
                         between "service" and "event" in every line
          manager TUI:   identical to baseline EXCEPT "component":"tui" appears
                         between "service" and "event" in every line

        Any unexpected field change or reordering fails this step.
```

### Test strategy for `telemetry/tests/`

Minimum test cases required (each maps to an AC):

| Test | What it verifies |
|------|-----------------|
| `test_configure_logging_idempotent` | Second call is a no-op; `_CONFIGURED` stays `True` |
| `test_configure_logging_service_field` | Emitted log has `"service": <arg>` field |
| `test_configure_logging_component_field` | `component="api"` → log has `"component": "api"` between `service` and `event` |
| `test_configure_logging_no_component` | No `component` arg → key `"component"` is absent from output (not null) |
| `test_configure_logging_env_fallback` | `LOG_LEVEL=DEBUG` env var → debug events emitted |
| `test_ensure_trace_id_injects_none` | Processor adds `trace_id="none"` when missing |
| `test_ensure_trace_id_preserves_existing` | Processor preserves an existing `trace_id` |
| `test_order_mandatory_fields_with_component` | Keys in order: `timestamp → level → service → component → event → trace_id` |
| `test_order_mandatory_fields_without_component` | Keys in order: `timestamp → level → service → event → trace_id` (no component key) |
| `test_middleware_generates_uuid4` | No `X-Trace-ID` header → response has valid UUID4 |
| `test_middleware_adopts_valid_header` | Valid UUID4 `X-Trace-ID` → same value echoed in response |
| `test_middleware_rejects_invalid_header` | Non-UUID4 header → new UUID4 generated |
| `test_middleware_response_header` | Response always contains `X-Trace-ID` |
| `test_middleware_sets_request_state` | `request.state.trace_id` is set for route handlers |
| `test_middleware_clears_context` | After request, structlog context is empty |
| `test_middleware_service_param` | `TraceIDMiddleware(app, service="X")` → logs have `service="X"` |

## Data Model

### Symbols migrated to `prometheus_telemetry.core`

| Symbol | Type | Unified change vs. originals |
|--------|------|------------------------------|
| `_CONFIGURED` | `bool` | Module-level flag; shared across all importers in the same process (each is a separate OS process in production — no conflict) |
| `_UUID_RE` | `re.Pattern` | Identical in all three; kept as-is |
| `_SHARED_PROCESSORS` | `list[Any]` | Identical in all three; kept as-is |
| `_ensure_trace_id()` | processor fn | Identical in all three; kept as-is |
| `_order_mandatory_fields()` | processor fn | Key order extended: `timestamp → level → service → component → event → trace_id` (component skipped when absent) |
| `_is_valid_uuid4()` | function | Identical in all three; kept as-is |
| `configure_logging()` | function | `service` required; new `component: str \| None` param; env var fallback for `log_level`/`log_max_bytes`/`log_backup_count` (from manager version); `uvicorn.access` suppressed for all |
| `get_logger()` | function | Identical in all three; kept as-is |
| `TraceIDMiddleware` | ASGI class | `service: str` added to `__init__`; `request.state.trace_id` + `X-Trace-ID` response header applied in all services (was gateway-only) |

### Symbols that stay in their component

| Symbol | Lives in | Reason |
|--------|----------|--------|
| `MetricsStore` | `gateway/telemetry.py` | Prometheus-format counters for `/metrics`; gateway-only concern |
| `metrics_store` | `gateway/telemetry.py` | Module-level singleton of `MetricsStore` |
| `redirect_logging_for_tui()` | `manager/telemetry.py` | Textual-specific stdout stripping; meaningless for ASGI services |
| `new_trace_id()` | `manager/telemetry.py` | Convenience wrapper; TUI lifecycle only |
| `_silence_structlog()` | `manager/telemetry.py` | TUI fallback; Textual-specific |

## JSON Log Format Contract

The migration must not alter the log format for components that do not opt into the
`component` field.  The table below is the binding contract verified in Step 8.

### gateway and auth-service — format unchanged

```json
{"timestamp": "2026-04-12T10:00:00.000Z", "level": "info", "service": "gateway",
 "event": "request.start", "trace_id": "4b3c...", "method": "POST", "path": "/v1/chat/completions"}
```

```json
{"timestamp": "2026-04-12T10:00:00.000Z", "level": "info", "service": "auth-service",
 "event": "token.issued", "trace_id": "4b3c...", "client_id": "app-abc"}
```

### manager API — `component` field added (only change vs. v0.8.1)

```json
{"timestamp": "2026-04-12T10:00:00.000Z", "level": "info", "service": "manager",
 "component": "api", "event": "backends.list", "trace_id": "4b3c...", "count": 2}
```

### manager TUI — `component` field added (only change vs. v0.8.1)

```json
{"timestamp": "2026-04-12T10:00:00.000Z", "level": "info", "service": "manager",
 "component": "tui", "event": "lifecycle.start", "trace_id": "tui-start-2469979d",
 "model_id": "llama3-8b-q4-local"}
```

**Key ordering rule** (`_order_mandatory_fields`):

| Position | Field | Present in |
|----------|-------|------------|
| 1 | `timestamp` | all components |
| 2 | `level` | all components |
| 3 | `service` | all components |
| 4 | `component` | manager API + TUI only — **absent** from gateway and auth-service |
| 5 | `event` | all components |
| 6 | `trace_id` | all components |
| 7+ | other fields | event-specific |

## Security Considerations

This spec is a pure refactor — no public interfaces change and no new data is processed.
Security posture is identical before and after migration.  Specific notes:

- The shared package must not introduce any transitive dependency beyond `structlog` and
  `starlette`.  Both are already present in all three services.
- `_CONFIGURED` is not a secret and not accessible outside the process.  Resetting it in
  tests (via direct module attribute access) is acceptable in test code only.
- Log file permissions (`0640`) are enforced in `configure_logging()` — this behaviour
  must be preserved in the migrated version.
- Auth-service joining the uv workspace shares the Python environment only.  The RSA
  private key material remains behind `settings.auth_private_key_file` and is never
  exposed through the shared package.

## Acceptance Criteria

### Package setup

- [x] AC-1: Given `uv sync` is run from the repository root on a clean clone, then
  `python -c "from prometheus_telemetry import configure_logging"` exits 0 without any
  additional `pip install` or `venv activate`.
- [x] AC-2: Given `"auth-service"` is added to the uv workspace members, when `uv sync` is
  run, then `auth-service/src/prometheus_auth/` can `import prometheus_telemetry` and the
  auth-service tests run successfully against the root `.venv`.
- [x] AC-3: Given `uv run ruff check telemetry/` and `uv run mypy telemetry/src/`, when run,
  then zero errors are reported.

### Migrated `configure_logging()`

- [x] AC-4: Given `configure_logging(service="gateway")` is called, when a log event is
  emitted, then the JSON line contains `"service": "gateway"`.
- [x] AC-5: Given `configure_logging(service="gateway")` is called twice, then the second
  call is a no-op and no duplicate handlers are registered on the root logger.
- [x] AC-6: Given `LOG_LEVEL=DEBUG` is set in the environment and `log_level` is not passed
  to `configure_logging()`, when a `debug`-level event is emitted, then it appears in the
  log output.
- [x] AC-7: Given a `log_file_path` that is under a non-existent directory, when
  `configure_logging()` is called, then the directory is created and a log file is written
  with permissions `0640`.
- [x] AC-8: Given a `log_file_path` that is not writable, when `configure_logging()` is
  called, then the service starts successfully with stdout-only logging and emits a
  `log_file_unavailable` warning event.

### Migrated `TraceIDMiddleware`

- [x] AC-9: Given `TraceIDMiddleware(app, service="X")` is mounted and a request arrives
  without `X-Trace-ID`, then a new UUID4 is generated, returned in the response header
  `X-Trace-ID`, and all log events for that request carry `trace_id=<uuid4>` and `service="X"`.
- [x] AC-10: Given a request arrives with a valid UUID4 `X-Trace-ID` header, then that
  same value is used as `trace_id` (not a new one) and returned in the response header.
- [x] AC-11: Given a request arrives with a non-UUID4 `X-Trace-ID` header (e.g. `"hacked"`),
  then a fresh UUID4 is generated and used instead.
- [x] AC-12: Given a request is processed, when the response has been sent, then
  `structlog.contextvars.get_contextvars()` returns an empty dict (context cleared).
- [x] AC-13: Given `TraceIDMiddleware` is mounted in the manager API, when the gateway
  forwards a request with its own `X-Trace-ID`, then the manager's log events for that
  request carry the same `trace_id`, enabling end-to-end trace correlation.

### Zero regression

- [x] AC-14: Given all gateway tests (`uv run pytest gateway/tests/ -v`), when run after
  the migration, then all tests pass and coverage does not drop below its pre-migration
  baseline.
- [x] AC-15: Given all auth-service tests, when run after the migration, then all tests
  pass.
- [x] AC-16: Given all manager tests, when run after the migration, then all 142 tests
  pass.
- [x] AC-17: Given the manager TUI is started (`pmgr tui`) after the migration, when a
  worker action is triggered (start/stop/download), then logs are written to the configured
  file with a unique `trace_id` per action and `"component": "tui"` in every log line —
  `redirect_logging_for_tui` still works because it imports `_SHARED_PROCESSORS` from
  the shared package.
- [x] AC-19: Given the manager API is started (`pmgr api`), when any log event is emitted,
  then the JSON line contains `"component": "api"` positioned between `"service"` and
  `"event"` in the key order.
- [x] AC-20: Given the gateway or auth-service emits a log event, then the JSON line does
  **not** contain a `component` key (field is absent, not null).

### Shared package test suite

- [x] AC-18: Given `uv run pytest telemetry/tests/ -v --cov`, when run, then all 16
  test cases listed in §Test Strategy pass and coverage of `prometheus_telemetry` is ≥ 90%.

## Resolved Decisions

- [x] **D-1 (was Q-1)**: `prometheus-telemetry` pins `structlog>=25.5.0` in
  `telemetry/pyproject.toml`.  This matches the version already required by
  `prometheus-manager` and `prometheus-auth`; `uv.lock` will resolve a single copy.
  No root-level constraint needed.
- [x] **D-2 (was Q-2)**: `component` field is added **in this spec**.
  `configure_logging()` gains `component: str | None = None`.  Manager API passes
  `component="api"` at startup; manager TUI binds `component="tui"` in `on_mount`
  and `_bind_worker_ctx`.  Gateway and auth-service pass no `component` — the field
  is absent (not null) from their logs.  See §JSON Log Format Contract.

## References

- Predecessor spec: [`memory/specs/018-observability-telemetry.md`](018-observability-telemetry.md)
- Related bug fix: `fix/019` — TUI stdout corruption, `trace_id: none` in threaded workers (v0.8.1)
- Successor spec: [`memory/specs/021-ops-observability-stack.md`](021-ops-observability-stack.md)
- uv workspace docs: https://docs.astral.sh/uv/concepts/workspaces/
