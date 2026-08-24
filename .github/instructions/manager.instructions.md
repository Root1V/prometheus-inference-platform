---
description: "Use when building the Prometheus Manager: TUI views, widgets, CLI commands, or the Manager REST API that exposes running inference backends."
applyTo: "runtime/manager/**"
---

# Manager — Development Guidelines

## Module Structure

```
runtime/manager/src/prometheus_manager/
├── tui/          ← Textual TUI app (5 views, bindings, widgets)
│   ├── app.py    ← Main App class, theme, key bindings
│   ├── views/    ← One file per view: dashboard, registry, instances, downloads, discovery
│   └── widgets/  ← Reusable Textual widgets (resource_bar, model_detail, …)
├── api/          ← FastAPI REST API exposing registered backends
│   ├── app.py    ← FastAPI app, middleware registration, error handlers
│   ├── routes.py ← GET /health, GET /v1/backends, GET /v1/backends/{id}
│   └── auth.py   ← JWT dependency (require_backend_registry_read)
├── cli/          ← Click-based CLI (pmgr) — thin wrappers over lifecycle/registry
│   └── main.py
├── lifecycle.py  ← start/stop/pause/resume/restart/deregister inference servers
├── registry.py   ← Registry + RegistryEntry — source of truth for model instances
├── scanner.py    ← Probe running llama-server processes (psutil + HTTP health)
├── config.py     ← ManagerConfig (pydantic) — loaded from manager.toml
├── telemetry.py  ← structlog + OpenTelemetry setup (configure_logging, get_tracer)
└── downloader.py ← GGUF download with progress (HTTPS only)
```

## TUI Conventions (Textual)

- Each view is a standalone `Widget` subclass in `tui/views/<name>.py`.
- Use `@work` for any async operation that touches the filesystem or network — never block the event loop.
- Update UI state via `call_from_thread` or `post_message` — never mutate widget state directly from a worker thread.
- Key bindings are declared on the `App` class as `BINDINGS: ClassVar[list[Binding]]`.
- Use `ContentSwitcher` to switch between views — do not mount/unmount views dynamically.
- CSS lives inline as `DEFAULT_CSS` on the widget class — no external `.css` files.
- Use `get_tracer("manager.tui")` for OpenTelemetry spans on user-initiated actions.

## Manager API Conventions (FastAPI)

- The API is **internal only** — never expose it outside `127.0.0.1`.
- All endpoints except `/health` require JWT validation via `require_backend_registry_read`.
- Error responses follow RFC 9457 Problem Details (same as gateway).
- `app.state.registry` holds the shared `Registry` instance — inject via `Request`.
- Use `TraceIDMiddleware` from `telemetry.py` to propagate `X-Trace-ID` from the gateway.
- Probe llama-server health with a short HTTP timeout (`_HTTP_PROBE_TIMEOUT = 2.0s`) — never block on unresponsive backends.

## Registry & Lifecycle

- `Registry` is the single source of truth — always read/write through it, never parse `registry.yaml` directly.
- `RegistryEntry.discovery` must be `True` for a model to appear in `/v1/backends`.
- Lifecycle functions (`start_instance`, `stop_instance`, …) manage PID files under `config.server.pid_dir`.
- Never hardcode paths — always read from `ManagerConfig`.

## CLI Conventions (Click / pmgr)

- CLI commands are thin wrappers — business logic lives in `lifecycle.py` or `registry.py`, not in `cli/main.py`.
- Use `structlog` for all output — no bare `print()` statements.
- Inject the OS native trust store via `truststore.inject_into_ssl()` at startup for corporate CA environments.

## Spec References

Every function/class that originates from a spec must include:
```python
# Implements: memory/specs/008-llama-server-manager.md — AC-N
```
