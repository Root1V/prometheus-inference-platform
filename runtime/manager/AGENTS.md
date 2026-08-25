# Manager — Agent Navigation Rules

## What lives here

The Prometheus Manager — bare-metal inference layer, split into three independently
packaged `uv` workspace members (docs/roadmap.md — RM-05). This split exists so the
API can be built into a small container image with no Textual/Rich, and the CLI+TUI can
eventually be packaged as a standalone binary for any machine, with no fastapi/uvicorn.

```
runtime/manager/
├── core/                              # prometheus-manager-core — shared domain layer
│   └── src/prometheus_manager_core/
│       ├── lifecycle.py               # start/stop/pause/resume/restart/deregister
│       ├── registry.py                # Registry + RegistryEntry — source of truth
│       ├── scanner.py                 # Probe running llama-server processes
│       ├── config.py                  # ManagerConfig — loaded from manager.toml
│       ├── telemetry.py               # structlog + OpenTelemetry re-exports
│       └── downloader.py              # GGUF download (HTTPS only)
├── api/                                # prometheus-manager-api — containerized, no TUI deps
│   ├── Dockerfile                     # builds ONLY core + api (no Textual/Rich)
│   └── src/prometheus_manager_api/
│       ├── app.py                     # FastAPI app, middleware
│       ├── routes.py                  # GET /health, GET /v1/backends, GET /v1/backends/{id}
│       ├── auth.py                    # JWT dependency (require_backend_registry_read)
│       └── cli.py                     # pmgr-api — entrypoint, uvicorn.run()
├── tui/                                 # prometheus-manager-tui — bare-metal only, no fastapi
│   └── src/prometheus_manager_tui/
│       ├── cli.py                     # Click CLI (pmgr) — thin wrappers over core
│       ├── app.py                     # Textual App, key bindings, theme
│       ├── logging_setup.py           # redirect_logging_for_tui (stdout → file)
│       ├── views/                     # One file per view (dashboard, registry, instances, downloads, discovery)
│       └── widgets/                   # Reusable widgets (resource_bar, model_detail)
├── registry.yaml                      # Active model registry (runtime state)
└── manager.toml                       # Manager configuration
```

Each package has its own `pyproject.toml`, `tests/`, and version. `core` has zero
dependency on `api` or `tui`; `api` and `tui` both depend on `core` but never on each
other.

## Before starting any task here

1. Check `registry.yaml` and `manager.toml` for the current runtime state before modifying lifecycle or registry logic.
2. Domain logic (lifecycle, registry, scanner, capacity, downloader, config) goes in `core/` — never duplicate it into `api/` or `tui/`.
3. TUI changes: identify which view is affected (`tui/src/prometheus_manager_tui/views/<name>.py`) and whether any shared widget needs updating.
4. API changes: check if `/v1/backends` response shape changes — coordinate with gateway consumers.
5. CLI changes: logic belongs in `core/`, not in `tui/src/prometheus_manager_tui/cli.py`.
6. Don't add a `core` → `api` or `core` → `tui` import — that direction breaks the whole point of the split.

## Before closing any task here

- [ ] For each touched package: `(cd runtime/manager/<pkg> && uv run pytest tests/ -v)`
- [ ] For each touched package: `(cd runtime/manager/<pkg> && uv run ruff check src/ && uv run ruff format --check src/ && uv run mypy src/)`
- [ ] `RegistryEntry.discovery=True` for any model that should appear in `/v1/backends`
- [ ] TUI workers use `@work` — no blocking calls in the event loop
- [ ] API endpoint `/health` has no auth; all others require JWT

## Key constraints

- Manager API binds to `127.0.0.1` only — never `0.0.0.0`.
- `Registry` is the single source of truth — never parse `registry.yaml` directly.
- Never hardcode paths — always read from `ManagerConfig`.
- Use `get_tracer("manager.<component>")` for OpenTelemetry spans.
- CLI must use `structlog` for output — no bare `print()` statements.
