# Observability — Agent Navigation Rules

## What lives here

The Prometheus observability stack — logs, traces, and dashboards for all services.

```
observability/
├── loki/
│   └── loki-config.yaml       # Log aggregation — receives structured JSON logs from Promtail
├── promtail/
│   └── promtail-config.yaml   # Log shipper — scrapes k8s-file log files → pushes to Loki
├── tempo/
│   └── tempo-config.yaml      # Distributed traces — OTLP gRPC (:4317) + HTTP (:4318)
├── grafana/
│   └── provisioning/
│       ├── dashboards/        # Auto-provisioned dashboards (prometheus-ops.json)
│       └── datasources/       # Auto-provisioned datasources (Loki + Tempo)
└── tests/
    └── test_observability_stack.sh  # Smoke tests for the running stack
```

All components are wired together in `podman-compose.yml` — the stack is started/stopped with the rest of the services.

## Stack Architecture

```
[gateway]        ─┐
[auth-service]   ─┼─ k8s-file log driver → CONTAINER_LOG_HOST_PATH/<service>.log
[manager API]    ─┘                                │
                                                   ▼
[Manager TUI]  → MANAGER_LOG_HOST_PATH/manager.log ─► Promtail → Loki ← Grafana
                                                                          ▲
[gateway]  ─┐                                                             │
[auth]     ─┼─ OTEL SDK → OTLP/HTTP → Tempo (:4318) ──────────────── Grafana
[manager]  ─┘
```

## Log Collection: k8s-file + virtiofs (macOS Podman Machine constraint)

On macOS + Podman Machine (rootless), Docker SD and syslog log drivers are not viable.
Log collection uses the **k8s-file log driver** writing to a virtiofs-shared directory:

- Each service container writes to `CONTAINER_LOG_HOST_PATH/<service>.log`
- `CONTAINER_LOG_HOST_PATH` is an absolute macOS path declared in the root `.env`
- Promtail bind-mounts that directory and scrapes each file with a static job

**When adding a new containerised service:**
1. Add `logging: driver: k8s-file` with `path: ${CONTAINER_LOG_HOST_PATH}/<service>.log` in `podman-compose.yml`
2. Add a new `scrape_configs` job in `promtail/promtail-config.yaml` with label `service: <service>`
3. Add the variable to the root `.env` if a new host path is needed
4. Run `bash observability/tests/test_observability_stack.sh` to validate

## Distributed Tracing (OTEL → Tempo)

Services send spans via OTLP/HTTP to `http://tempo:4318`. Configured via environment variable:
```
OTEL_EXPORTER_OTLP_ENDPOINT=http://tempo:4318
```

Tempo is configured in single-binary mode with 72h trace retention (`tempo-config.yaml`).
Grafana correlates log lines that contain a `trace_id` field directly to Tempo spans via the `derivedFields` rule in `datasources.yaml`.

**When adding OTEL instrumentation to a new service**, add `OTEL_EXPORTER_OTLP_ENDPOINT` to its entry in `podman-compose.yml` — no Tempo config changes needed.

## Before starting any task here

1. Check if the change adds a new service — if so, follow the log collection steps above.
2. Dashboard changes: edit `grafana/provisioning/dashboards/prometheus-ops.json` directly — Grafana auto-provisions on restart.
3. Datasource changes: edit `grafana/provisioning/datasources/datasources.yaml`.

## Before closing any task here

- [ ] `bash observability/tests/test_observability_stack.sh` passes
- [ ] Any new `CONTAINER_LOG_HOST_PATH` or `MANAGER_LOG_HOST_PATH` variable is documented in root `AGENTS.md` and the root `.env.example`
- [ ] New Promtail job has `service: <name>` label matching the structured log field `service` emitted by the app
- [ ] Tempo trace retention (`block_retention`) has not been reduced below 72h without discussion

## Key constraints

- Loki, Tempo, Grafana, and Promtail run in containers — never bare-metal.
- `CONTAINER_LOG_HOST_PATH` must be an **absolute macOS path** — relative paths break virtiofs sharing.
- Never expose Grafana on `0.0.0.0` in production — default is `127.0.0.1` only.
- Grafana admin credentials come from root `.env` (`GRAFANA_SECRET_KEY`, `GRAFANA_ADMIN_PASSWORD`) — never hardcode.
- See memory/specs/021 for the full observability spec and memory/specs/022 for OTEL SDK instrumentation.
