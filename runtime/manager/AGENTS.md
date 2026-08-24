# Manager — Agent Navigation Rules

## What lives here

The Prometheus Manager — bare-metal inference layer with three sub-modules.

```
runtime/manager/
├── src/prometheus_manager/
│   ├── tui/            # Textual TUI app (5 views: dashboard, registry, instances, downloads, discovery)
│   │   ├── app.py      # Main App, key bindings, theme
│   │   ├── views/      # One file per view
│   │   └── widgets/    # Reusable widgets (resource_bar, model_detail)
│   ├── api/            # FastAPI REST API exposing active backends
│   │   ├── app.py      # FastAPI app, middleware
│   │   ├── routes.py   # GET /health, GET /v1/backends, GET /v1/backends/{id}
│   │   └── auth.py     # JWT dependency (require_backend_registry_read)
│   ├── cli/            # Click CLI (pmgr) — thin wrappers over lifecycle/registry
│   ├── lifecycle.py    # start/stop/pause/resume/restart/deregister
│   ├── registry.py     # Registry + RegistryEntry — source of truth
│   ├── scanner.py      # Probe running llama-server processes
│   ├── config.py       # ManagerConfig (pydantic) — loaded from manager.toml
│   ├── telemetry.py    # structlog + OpenTelemetry setup
│   └── downloader.py   # GGUF download (HTTPS only)
├── tests/
├── registry.yaml       # Active model registry (runtime state)
└── manager.toml        # Manager configuration
```

## Before starting any task here

1. Check `registry.yaml` and `manager.toml` for the current runtime state before modifying lifecycle or registry logic.
2. TUI changes: identify which view is affected (`views/<name>.py`) and whether any shared widget needs updating.
3. API changes: check if `/v1/backends` response shape changes — coordinate with gateway consumers.
4. CLI changes: logic belongs in `lifecycle.py` or `registry.py`, not in `cli/main.py`.

## Before closing any task here

- [ ] `(cd runtime/manager && uv run pytest tests/ -v --cov=src --cov-fail-under=80)`
- [ ] `(cd runtime/manager && uv run ruff check src/ && uv run ruff format --check src/ && uv run mypy src/)`
- [ ] `RegistryEntry.discovery=True` for any model that should appear in `/v1/backends`
- [ ] TUI workers use `@work` — no blocking calls in the event loop
- [ ] API endpoint `/health` has no auth; all others require JWT

## Key constraints

- Manager API binds to `127.0.0.1` only — never `0.0.0.0`.
- `Registry` is the single source of truth — never parse `registry.yaml` directly.
- Never hardcode paths — always read from `ManagerConfig`.
- Use `get_tracer("manager.<component>")` for OpenTelemetry spans.
- CLI must use `structlog` for output — no bare `print()` statements.
