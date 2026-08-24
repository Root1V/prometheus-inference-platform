---
id: "021"
title: "Grafana + Loki + Tempo — Ops Observability Stack"
status: closed
current-agent: ""
pipeline-log: []
created: 2026-04-12
updated: 2026-04-18
---

# 021 — Grafana + Loki + Tempo — Ops Observability Stack

## Problem Statement

All four Prometheus platform components emit structured JSON logs today (spec-018,
spec-020):

| Component | Runtime | Log destination |
|-----------|---------|----------------|
| **gateway** | ASGI / Podman :8000 | Podman stdout (structlog JSON) |
| **auth-service** | ASGI / Podman :9000 | Podman stdout (structlog JSON) |
| **manager API** | FastAPI / Podman :8090 | Podman stdout (structlog JSON) |
| **manager TUI** | Textual / bare-metal | `runtime/logs/manager.log` (rotating JSON) |
| **llama-server** | bare-metal :8080 | stdout (unstructured; out of scope) |

Despite rich structured output, there is **no aggregation, no search UI, and no
dashboard**. Operators must run `podman logs` or `tail` individual files to diagnose
incidents. Cross-service correlation (e.g., "show me all log lines with
`trace_id=abc123` across gateway and auth-service") requires manual grep across
separate log streams.

Open questions that cannot be answered today without manual log inspection:
- What is the p95 inference latency over the past hour?
- Which `user_id` is generating the most errors?
- Are gateway errors correlated with auth-service token rejection cascades?
- Is the manager TUI writing logs while the manager API is idle?

This spec introduces a self-hosted **Grafana + Loki + Promtail + Tempo** stack, fully
containerised in Podman, that closes these gaps with **zero changes to any Python source
code**.

## Goals

- [x] **G-1**: Add `loki`, `promtail`, `tempo`, and `grafana` services to
  `podman-compose.yml`; all four start with `podman compose up --build -d` alongside the
  existing services.
- [x] **G-2**: ~~Promtail discovers Podman containers by `prometheus.service` label via Docker SD~~
  **REVISED**: Docker SD is not viable on macOS + Podman Machine (rootless). Log collection
  uses **k8s-file log driver** with virtiofs-shared paths instead. See Implementation Notes.
- [x] **G-3**: Promtail tails `runtime/logs/manager.log` (manager TUI rotating file) via a
  bind-mount and ships its lines to Loki under `service="manager-tui"`.
- [x] **G-4**: Promtail pipeline stages parse the structlog JSON fields (`level`, `service`,
  `trace_id`, `event`, `component`) so they are indexed or stored as structured metadata in
  Loki and queryable via LogQL.
- [x] **G-5**: Grafana starts with Loki and Tempo as pre-provisioned datasources — no manual
  "Add datasource" step required in the UI.
- [x] **G-6**: Grafana validates RS256 JWTs issued by the auth-service (via the JWKS
  endpoint) and derives the Grafana username and role from JWT claims.
- [x] **G-7**: A starter dashboard "Prometheus Ops" is provisioned automatically, showing
  log volume by service, error/warning rate by service, and a live inference request table.
- [x] **G-8**: The observability stack is **optional and additive**: all four platform
  services continue operating normally when Grafana, Loki, Promtail, and Tempo are not running.
- [x] **G-9**: Tempo listens on OTLP/gRPC port 4317 and OTLP/HTTP port 4318 on the host
  loopback — ready for spec-022 (OpenTelemetry SDK) without further infrastructure changes.
- [x] **G-10**: `GRAFANA_SECRET_KEY` and `GRAFANA_ADMIN_PASSWORD` are injected exclusively
  via root `.env` environment variables; their values never appear in any committed file.
- [x] **G-11**: All config files under `observability/` are committed and copy-pasteable
  with no manual editing required for a default dev deployment.
- [x] **G-12**: ~~Zero Python source files are added or modified by this spec.~~
  **REVISED**: Minor Python changes were required as part of implementation fixes (see
  Implementation Notes). The core observability infrastructure (G-1 through G-11) required
  no Python changes; fixes were made to improve log quality.

## Non-Goals

- **Python source code changes** — no changes to gateway, auth-service, manager, or
  `prometheus_telemetry` package source.
- **Cloud-hosted observability** — Grafana Cloud, Datadog, New Relic, etc. are out of scope.
- **Prometheus metrics scraping** — `prometheus_client` / OpenMetrics text exposition is a
  separate concern; this spec covers logs and traces only.
- **Grafana alert rules** — alerting configuration is deferred to a future spec.
- **Langfuse integration** — originally planned for spec-021, now replaced by this spec.
- **OpenTelemetry SDK instrumentation** — Tempo infrastructure is set up here, but OTEL
  span generation in application code is spec-022.
- **Auth-service code changes** — no new endpoints. Grafana JWT auth uses the existing JWKS
  endpoint exposed by spec-005.
- **Production-grade Loki/Tempo storage** — local filesystem volumes are used; multi-replica
  object-store backends are out of scope.
- **TLS for observability endpoints** — Loki, Tempo, and Grafana use plain HTTP within the
  `prometheus_net` network. Placing Grafana behind a TLS reverse proxy is a separate chore.

## Proposed Solution

### Architecture overview

```
[Podman network: prometheus_net]

  gateway   ──┐
  auth-svc  ──┤  stdout logs ──►  promtail (Docker SD, label filter)  ──►  loki :3100
  manager   ──┘

  manager TUI (bare-metal)
    runtime/logs/manager.log  ──►  promtail (static file, bind-mount)  ──►  loki :3100

                                                            ▲
                              grafana :3000  ──── queries ──┤
                                                            ▼
                                                       tempo :3200
                                                    (OTLP :4317/:4318)
                                                         ▲
                                  (spec-022: OTEL SDK sends spans here)

  [Host browser]  ──►  grafana :3000  (JWT auth validated against auth-service JWKS)
```

### Repository layout changes

```
/
├── podman-compose.yml          ← add prometheus.service labels to existing 4 services;
│                                 add loki, promtail, tempo, grafana services + named volumes
├── observability/              ← NEW directory — all observability config
│   ├── loki/
│   │   └── loki-config.yaml
│   ├── promtail/
│   │   └── promtail-config.yaml
│   ├── tempo/
│   │   └── tempo-config.yaml
│   └── grafana/
│       └── provisioning/
│           ├── datasources/
│           │   └── datasources.yaml
│           └── dashboards/
│               ├── dashboards.yaml
│               └── prometheus-ops.json
└── root .env                   ← add GRAFANA_SECRET_KEY, GRAFANA_ADMIN_PASSWORD,
                                   MANAGER_LOG_HOST_PATH
```

> **No `grafana.ini` file is committed.** All Grafana settings are injected via `GF_*`
> environment variables in the compose service definition, keeping the provisioning
> directory free of secrets and fully version-controlled.

---

### Config file: `observability/loki/loki-config.yaml`

Loki runs in single-process mode (`-target=all`) with local filesystem storage.
Suitable for single-host dev/ops. Retention is 30 days.

```yaml
# observability/loki/loki-config.yaml
# See: memory/specs/021-ops-observability-stack.md — AC-1, AC-2, AC-3
auth_enabled: false

server:
  http_listen_port: 3100
  grpc_listen_port: 9096
  log_level: warn

common:
  instance_addr: 127.0.0.1
  path_prefix: /loki
  storage:
    filesystem:
      chunks_directory: /loki/chunks
      rules_directory: /loki/rules
  replication_factor: 1
  ring:
    kvstore:
      store: inmemory

schema_config:
  configs:
    - from: "2024-01-01"
      store: tsdb
      object_store: filesystem
      schema: v13
      index:
        prefix: loki_index_
        period: 24h

limits_config:
  reject_old_samples: true
  reject_old_samples_max_age: 720h   # 30 days
  allow_structured_metadata: true
  volume_enabled: true

compactor:
  working_directory: /loki/compactor
  retention_enabled: true
  retention_delete_delay: 2h
  delete_request_store: filesystem

query_range:
  results_cache:
    cache:
      embedded_cache:
        enabled: true
        max_size_mb: 100
```

---

### Config file: `observability/promtail/promtail-config.yaml`

Promtail scrapes two sources:
1. **Podman container stdout** via Docker-compatible SD, filtered by `prometheus.service` label.
2. **Manager TUI rotating log file** via static scrape, bind-mounted from the host.

```yaml
# observability/promtail/promtail-config.yaml
# See: memory/specs/021-ops-observability-stack.md — AC-1, AC-2, AC-3, AC-10
server:
  http_listen_port: 9080
  grpc_listen_port: 0

positions:
  filename: /tmp/positions.yaml

clients:
  - url: http://loki:3100/loki/api/v1/push

scrape_configs:

  # ── Podman container stdout ──────────────────────────────────────────────────
  # Discovers all containers carrying the label "prometheus.service".
  # Adding a new compose service with this label makes it visible to Loki
  # automatically — no Promtail config change or restart required (AC-10).
  #
  # Podman exposes a Docker-compatible API on the same socket as the Docker daemon.
  # For rootful Podman in the machine VM: /var/run/podman/podman.sock
  # For the Docker-compatible socket alternative: /var/run/docker.sock
  - job_name: podman_containers
    docker_sd_configs:
      - host: "unix:///var/run/podman/podman.sock"
        refresh_interval: 15s
        filters:
          - name: label
            values: ["prometheus.service"]

    relabel_configs:
      # Promote the prometheus.service container label to a Loki stream label.
      # Docker SD converts label dots to underscores in meta-label names:
      # prometheus.service → __meta_docker_container_label_prometheus_service
      - source_labels: [__meta_docker_container_label_prometheus_service]
        target_label: service

      # Strip the leading "/" that Docker includes in container names.
      - source_labels: [__meta_docker_container_name]
        regex: /(.*)
        target_label: container

      # Defensive: drop containers that somehow lack a service label value.
      - source_labels: [service]
        regex: ".+"
        action: keep

    pipeline_stages:
      # Parse the structlog JSON log line.
      - json:
          expressions:
            level: level
            event: event
            trace_id: trace_id
            component: component

      # Promote low-cardinality fields as Loki index labels.
      # WARNING: never add trace_id or event here — too high cardinality!
      - labels:
          level:

      # Promote high-cardinality fields as structured metadata.
      # Structured metadata is queryable via | json but not indexed,
      # avoiding label cardinality explosion.
      - structured_metadata:
          trace_id:
          event:
          component:

      # Use the log line's own timestamp instead of ingest time.
      - timestamp:
          source: timestamp
          format: RFC3339Nano
          fallback_formats:
            - RFC3339
            - UnixMs

  # ── Manager TUI rotating log file ───────────────────────────────────────────
  # The manager TUI runs bare-metal and writes structured JSON to
  # runtime/logs/manager.log. Promtail reads this file via a bind-mount
  # (MANAGER_LOG_HOST_PATH in root .env → /mnt/manager-logs in the container).
  - job_name: manager_tui
    static_configs:
      - targets:
          - localhost
        labels:
          job: manager_tui
          service: manager-tui
          __path__: /mnt/manager-logs/manager.log

    pipeline_stages:
      - json:
          expressions:
            level: level
            event: event
            trace_id: trace_id
            component: component

      - labels:
          level:

      - structured_metadata:
          trace_id:
          event:
          component:

      - timestamp:
          source: timestamp
          format: RFC3339Nano
          fallback_formats:
            - RFC3339
            - UnixMs
```

---

### Config file: `observability/tempo/tempo-config.yaml`

Tempo runs in single-binary mode with local filesystem storage. 72-hour trace
retention. OTLP gRPC (4317) and HTTP (4318) receivers are enabled now — spec-022
can send spans without any infrastructure changes.

```yaml
# observability/tempo/tempo-config.yaml
# See: memory/specs/021-ops-observability-stack.md — AC-9
server:
  http_listen_port: 3200
  log_level: warn

distributor:
  receivers:
    otlp:
      protocols:
        grpc:
          endpoint: 0.0.0.0:4317
        http:
          endpoint: 0.0.0.0:4318

ingester:
  trace_idle_period: 10s
  max_block_bytes: 1000000
  max_block_duration: 5m

compactor:
  compaction:
    compaction_window: 1h
    max_block_bytes: 100000000
    block_retention: 72h
    compacted_block_retention: 10m

storage:
  trace:
    backend: local
    local:
      path: /var/tempo/traces
    wal:
      path: /var/tempo/wal

query_frontend:
  search:
    duration_slo: 5s
    throughput_bytes_slo: 1073741824
  trace_by_id:
    duration_slo: 5s
```

---

### Config file: `observability/grafana/provisioning/datasources/datasources.yaml`

```yaml
# observability/grafana/provisioning/datasources/datasources.yaml
# See: memory/specs/021-ops-observability-stack.md — AC-4
apiVersion: 1

datasources:
  - name: Loki
    type: loki
    uid: loki
    access: proxy
    url: http://loki:3100
    isDefault: true
    version: 1
    editable: false
    jsonData:
      maxLines: 1000
      # Clicking a trace_id value in a log line opens Tempo directly.
      derivedFields:
        - datasourceUid: tempo
          matcherRegex: '"trace_id":"([^"]+)"'
          name: TraceID
          url: "$${__value.raw}"
          urlDisplayLabel: "View in Tempo"

  - name: Tempo
    type: tempo
    uid: tempo
    access: proxy
    url: http://tempo:3200
    isDefault: false
    version: 1
    editable: false
    jsonData:
      tracesToLogsV2:
        datasourceUid: loki
        spanStartTimeShift: "-1m"
        spanEndTimeShift: "1m"
        filterByTraceID: true
        customQuery: false
      nodeGraph:
        enabled: true
      search:
        hide: false
      lokiSearch:
        datasourceUid: loki
```

---

### Config file: `observability/grafana/provisioning/dashboards/dashboards.yaml`

```yaml
# observability/grafana/provisioning/dashboards/dashboards.yaml
# See: memory/specs/021-ops-observability-stack.md — AC-7
apiVersion: 1

providers:
  - name: prometheus-ops
    orgId: 1
    type: file
    disableDeletion: false
    updateIntervalSeconds: 30
    allowUiUpdates: true
    options:
      path: /etc/grafana/provisioning/dashboards
      foldersFromFilesStructure: false
```

---

### Config file: `observability/grafana/provisioning/dashboards/prometheus-ops.json`

Starter dashboard with three panels: log volume by service, error rate by service,
and a live recent-log table. The dashboard auto-refreshes every 30 seconds.

```json
{
  "annotations": { "list": [] },
  "description": "Prometheus SLM Platform \u2014 ops log overview",
  "editable": true,
  "fiscalYearStartMonth": 0,
  "graphTooltip": 1,
  "id": null,
  "links": [],
  "panels": [
    {
      "datasource": { "type": "loki", "uid": "loki" },
      "fieldConfig": {
        "defaults": {
          "color": { "mode": "palette-classic" },
          "custom": { "lineWidth": 1, "spanNulls": false }
        },
        "overrides": []
      },
      "gridPos": { "h": 8, "w": 12, "x": 0, "y": 0 },
      "id": 1,
      "options": {
        "legend": {
          "calcs": [],
          "displayMode": "list",
          "placement": "bottom",
          "showLegend": true
        },
        "tooltip": { "mode": "single", "sort": "none" }
      },
      "targets": [
        {
          "datasource": { "type": "loki", "uid": "loki" },
          "expr": "sum by (service) (rate({job=~\"podman_containers|manager_tui\"} [$__rate_interval]))",
          "legendFormat": "{{service}}",
          "refId": "A"
        }
      ],
      "title": "Log Volume by Service",
      "type": "timeseries"
    },
    {
      "datasource": { "type": "loki", "uid": "loki" },
      "fieldConfig": {
        "defaults": {
          "color": { "mode": "fixed", "fixedColor": "red" },
          "custom": { "lineWidth": 2, "spanNulls": false }
        },
        "overrides": []
      },
      "gridPos": { "h": 8, "w": 12, "x": 12, "y": 0 },
      "id": 2,
      "options": {
        "legend": {
          "calcs": [],
          "displayMode": "list",
          "placement": "bottom",
          "showLegend": true
        },
        "tooltip": { "mode": "single", "sort": "none" }
      },
      "targets": [
        {
          "datasource": { "type": "loki", "uid": "loki" },
          "expr": "sum by (service) (rate({job=~\"podman_containers|manager_tui\", level=\"error\"} [$__rate_interval]))",
          "legendFormat": "{{service}} errors",
          "refId": "A"
        }
      ],
      "title": "Error Rate by Service",
      "type": "timeseries"
    },
    {
      "datasource": { "type": "loki", "uid": "loki" },
      "gridPos": { "h": 12, "w": 24, "x": 0, "y": 8 },
      "id": 3,
      "options": {
        "dedupStrategy": "none",
        "enableLogDetails": true,
        "prettifyLogMessage": true,
        "showCommonLabels": false,
        "showLabels": false,
        "showTime": true,
        "sortOrder": "Descending",
        "wrapLogMessage": true
      },
      "targets": [
        {
          "datasource": { "type": "loki", "uid": "loki" },
          "expr": "{job=~\"podman_containers|manager_tui\"} | json",
          "refId": "A"
        }
      ],
      "title": "Recent Log Lines (all services)",
      "type": "logs"
    }
  ],
  "refresh": "30s",
  "schemaVersion": 38,
  "tags": ["prometheus", "ops"],
  "templating": {
    "list": [
      {
        "current": {},
        "datasource": { "type": "loki", "uid": "loki" },
        "hide": 0,
        "includeAll": true,
        "label": "Service",
        "multi": true,
        "name": "service",
        "options": [],
        "query": "label_values(service)",
        "refresh": 2,
        "regex": "",
        "sort": 1,
        "type": "query"
      }
    ]
  },
  "time": { "from": "now-1h", "to": "now" },
  "timepicker": {},
  "timezone": "America/Lima",
  "title": "Prometheus SLM \u2014 Ops Overview",
  "uid": "prometheus-ops-v1",
  "version": 1
}
```

---

### `podman-compose.yml` — changes

#### Step 1: Add `prometheus.service` labels to existing services

Each existing service that emits structured logs gets a `labels:` block.
Redis is excluded (it emits no structlog JSON lines).

```yaml
# gateway service — add:
labels:
  prometheus.service: gateway

# auth-service — add:
labels:
  prometheus.service: auth-service

# manager service — add:
labels:
  prometheus.service: manager-api
```

#### Step 2: Add new observability services

```yaml
  # ── Loki — log aggregation backend ─────────────────────────────────────────
  # See: memory/specs/021-ops-observability-stack.md — AC-1, AC-2, AC-3
  loki:
    image: docker.io/grafana/loki:3.4.2
    container_name: prometheus-loki
    restart: unless-stopped
    expose:
      - "3100"
    command: -config.file=/etc/loki/loki-config.yaml -target=all
    volumes:
      - type: bind
        source: ./observability/loki/loki-config.yaml
        target: /etc/loki/loki-config.yaml
        read_only: true
      - type: volume
        source: loki_data
        target: /loki
    healthcheck:
      test: ["CMD", "wget", "--quiet", "--tries=1", "--spider",
             "http://localhost:3100/ready"]
      interval: 10s
      timeout: 5s
      retries: 6
      start_period: 30s
    networks:
      - prometheus_net

  # ── Promtail — log shipper ──────────────────────────────────────────────────
  # See: memory/specs/021-ops-observability-stack.md — AC-1, AC-2, AC-3, AC-10
  promtail:
    image: docker.io/grafana/promtail:3.4.2
    container_name: prometheus-promtail
    restart: unless-stopped
    command: -config.file=/etc/promtail/promtail-config.yaml
    volumes:
      - type: bind
        source: ./observability/promtail/promtail-config.yaml
        target: /etc/promtail/promtail-config.yaml
        read_only: true
      # Podman Docker-compatible API socket — used by docker_sd_configs (AC-10).
      # Configurable via PODMAN_SOCK_PATH in root .env (Q-1 resolved).
      - type: bind
        source: ${PODMAN_SOCK_PATH:-/var/run/podman/podman.sock}
        target: /var/run/podman/podman.sock
        read_only: true
      # Manager TUI rotating log file bind-mount (AC-3).
      # MANAGER_LOG_HOST_PATH must be an absolute path in root .env.
      - type: bind
        source: ${MANAGER_LOG_HOST_PATH:-./runtime/logs}
        target: /mnt/manager-logs
        read_only: true
    depends_on:
      loki:
        condition: service_healthy
    networks:
      - prometheus_net

  # ── Tempo — distributed tracing backend ────────────────────────────────────
  # See: memory/specs/021-ops-observability-stack.md — AC-9
  tempo:
    image: docker.io/grafana/tempo:2.7.2
    container_name: prometheus-tempo
    restart: unless-stopped
    command: -config.file=/etc/tempo/tempo-config.yaml
    volumes:
      - type: bind
        source: ./observability/tempo/tempo-config.yaml
        target: /etc/tempo/tempo-config.yaml
        read_only: true
      - type: volume
        source: tempo_data
        target: /var/tempo
    expose:
      - "3200"
    ports:
      # OTLP gRPC — loopback only so bare-metal services can send spans (AC-9).
      - "127.0.0.1:4317:4317"
      # OTLP HTTP
      - "127.0.0.1:4318:4318"
    healthcheck:
      test: ["CMD", "wget", "--quiet", "--tries=1", "--spider",
             "http://localhost:3200/ready"]
      interval: 10s
      timeout: 5s
      retries: 6
      start_period: 30s
    networks:
      - prometheus_net

  # ── Grafana — unified dashboard UI ─────────────────────────────────────────
  # See: memory/specs/021-ops-observability-stack.md — AC-4, AC-5, AC-6, AC-7
  grafana:
    image: docker.io/grafana/grafana:11.6.1
    container_name: prometheus-grafana
    restart: unless-stopped
    ports:
      - "3000:3000"
    environment:
      # Security secrets — AC-11: values come from root .env only, never hardcoded.
      - GF_SECURITY_SECRET_KEY=${GRAFANA_SECRET_KEY}
      - GF_SECURITY_ADMIN_PASSWORD=${GRAFANA_ADMIN_PASSWORD}
      - GF_SECURITY_ADMIN_USER=admin
      - GF_SECURITY_DISABLE_GRAVATAR=true

      # JWT auth — AC-5, AC-6.
      # Grafana validates the X-JWT-Assertion header against the auth-service JWKS.
      # GF_AUTH_JWT_TLS_SKIP_VERIFY_INSECURE is true for the dev self-signed cert;
      # set to false in production with a CA-signed certificate.
      - GF_AUTH_JWT_ENABLED=true
      - GF_AUTH_JWT_HEADER_NAME=X-JWT-Assertion
      - GF_AUTH_JWT_EMAIL_CLAIM=sub
      - GF_AUTH_JWT_USERNAME_CLAIM=sub
      - GF_AUTH_JWT_JWK_SET_URL=https://auth-service:9000/.well-known/jwks.json
      - GF_AUTH_JWT_TLS_SKIP_VERIFY_INSECURE=true
      - GF_AUTH_JWT_CACHE_TTL=60m
      - GF_AUTH_JWT_AUTO_SIGN_UP=true
      # JMESPath role mapping: JWT scope containing "admin" → Grafana Admin role.
      # scope is a space-separated string (confirmed spec-005 / crypto.py:84) — Q-2 resolved.
      - GF_AUTH_JWT_ROLE_ATTRIBUTE_PATH=contains(scope, 'admin') && 'Admin' || 'Viewer'
      # Reject tokens not issued by the auth-service.
      # Must match the JWT_ISSUER env var configured on the auth-service.
      - GF_AUTH_JWT_EXPECT_CLAIMS={"iss":"prometheus-auth"}

      # No anonymous access — only JWT or admin/password login.
      - GF_AUTH_ANONYMOUS_ENABLED=false
      - GF_USERS_ALLOW_SIGN_UP=false

      # Telemetry opt-out.
      - GF_ANALYTICS_REPORTING_ENABLED=false
      - GF_ANALYTICS_CHECK_FOR_UPDATES=false

      # Log only to container stdout at warn level.
      - GF_LOG_MODE=console
      - GF_LOG_LEVEL=warn

    volumes:
      - type: bind
        source: ./observability/grafana/provisioning
        target: /etc/grafana/provisioning
        read_only: true
      - type: volume
        source: grafana_data
        target: /var/lib/grafana

    depends_on:
      loki:
        condition: service_healthy
      tempo:
        condition: service_healthy
      auth-service:
        condition: service_started

    healthcheck:
      test: ["CMD", "wget", "--quiet", "--tries=1", "--spider",
             "http://localhost:3000/api/health"]
      interval: 10s
      timeout: 5s
      retries: 5
      start_period: 30s

    networks:
      - prometheus_net
```

#### Step 3: Add named volumes at the top-level `volumes:` block

```yaml
volumes:
  loki_data:
  tempo_data:
  grafana_data:
```

#### Step 4: Update `AGENTS.md` root `.env` documentation

The following variables must be added to the root `.env` (gitignored) and documented
in the `AGENTS.md` bind-mount variable table:

```bash
# ── Observability stack — memory/specs/021-ops-observability-stack.md ─────────────────
# Grafana security — both are required; generate with: openssl rand -hex 32
GRAFANA_SECRET_KEY=<random-32-char-hex>
GRAFANA_ADMIN_PASSWORD=<strong-password>

# Absolute host path to the directory containing manager.log.
# Must be absolute (Podman bind-mounts ignore relative paths on some platforms).
MANAGER_LOG_HOST_PATH=/absolute/path/to/edge-ai-inference/runtime/logs
```

---

### Key Design Decisions

| Decision | Rationale |
|----------|-----------|
| Docker SD for Podman | Podman exposes a Docker-compatible REST API on the same socket; Promtail's `docker_sd_configs` works without modification |
| Label-based discovery (`prometheus.service`) | Avoids fragile regex on container names; adding a new service is one label addition — zero Promtail config changes (AC-10) |
| `level` as the only Loki index label per stream | `trace_id`, `event`, `component` are high-cardinality — they go to `structured_metadata` to avoid Loki index bloat |
| Grafana JWT auth (not OAuth2 Authorization Code) | The auth-service has no `/authorize` endpoint; JWT auth reuses the existing JWKS endpoint with no server-side changes (AC-12) |
| `GF_*` env vars for all Grafana settings | Keeps the provisioning directory secret-free, read-only, and fully version-controlled |
| Tempo OTLP ports bound to 127.0.0.1 | Prevents LAN exposure; spec-022 OTEL SDKs on localhost or `prometheus_net` can reach Tempo either way |
| Named volumes for Loki/Tempo/Grafana data | Data survives `podman compose down` and `--build` rebuilds; volumes are removed only with `podman compose down -v` |
| Image versions pinned | Reproducibility; upgrades are deliberate, reviewable changes |

## Data Model

### Loki label schema

Loki labels are stored in the inverted index and must be **low-cardinality**. Only the
following fields are promoted to stream labels:

| Label | Source | Cardinality | Notes |
|-------|--------|-------------|-------|
| `job` | Promtail scrape job name | 2 (`podman_containers`, `manager_tui`) | Auto-set by Promtail |
| `service` | `prometheus.service` container label | ~5 (one per component) | Primary filter label |
| `container` | Podman container name | ~5 | Debugging aid; redis has no `prometheus.service` label |
| `level` | Structlog `level` field | ~5 (`debug`, `info`, `warning`, `error`, `critical`) | Enables error-rate queries |

### Loki structured metadata (queryable but not indexed)

| Field | Source | Notes |
|-------|--------|-------|
| `trace_id` | Structlog `trace_id` | High-cardinality; stored as structured metadata via the `structured_metadata` Promtail stage |
| `event` | Structlog `event` | High-cardinality event names such as `request.complete`, `inference.start` |
| `component` | Structlog `component` | `"api"` or `"tui"` for manager logs; absent for other services |

### LogQL query examples

```logql
# All errors in the last 1 hour
{level="error"} | json

# Follow gateway logs with a specific trace_id
{service="gateway"} | json | trace_id="abc-123-xyz"

# Error rate by service (used in dashboard panel)
sum by (service) (rate({level="error"}[5m]))

# Manager TUI logs only
{service="manager-tui"} | json

# Auth-service token issuance events
{service="auth-service"} | json | event="token.issued"
```

### New environment variables (root `.env`)

| Variable | Type | Required | Description |
|----------|------|----------|-------------|
| `GRAFANA_SECRET_KEY` | string | Yes | Grafana session encryption key; minimum 32 chars; generate with `openssl rand -hex 32` |
| `GRAFANA_ADMIN_PASSWORD` | string | Yes | Grafana built-in admin account password |
| `MANAGER_LOG_HOST_PATH` | absolute path | Yes | Host-side directory containing `manager.log`; default `./runtime/logs` (relative paths may fail on some Podman versions) |

## Security Considerations

- **OWASP A01 — Broken Access Control**:
  - `GF_AUTH_JWT_EXPECT_CLAIMS={"iss":"prometheus-auth"}` ensures Grafana only accepts
    tokens issued by the Prometheus auth-service. Tokens from any other issuer are
    rejected at the JWKS validation step.
  - `GF_AUTH_JWT_ROLE_ATTRIBUTE_PATH` maps the JWT `scope` claim to Grafana roles.
    Tokens with no recognised scope default to the `Viewer` role (read-only access).
  - `GF_AUTH_ANONYMOUS_ENABLED=false` prevents unauthenticated access to Grafana. All
    dashboard and API requests require either a valid JWT or the admin password.
  - Loki and Tempo are accessible only on the `prometheus_net` internal network. They
    are declared with `expose:`, not `ports:`, so they are unreachable from the host.

- **OWASP A02 — Cryptographic Failures**:
  - `GRAFANA_SECRET_KEY` must be a CSPRNG-generated string of at least 32 characters.
    It must never appear in committed files, container inspect output, or any log event.
  - Grafana JWT validation uses the auth-service JWKS endpoint over HTTPS internally.
    `GF_AUTH_JWT_TLS_SKIP_VERIFY_INSECURE=true` is acceptable **only** for dev with a
    self-signed certificate. Production deployments must use CA-signed certs and set
    this value to `false`.
  - Tempo OTLP ports (4317, 4318) are bound to `127.0.0.1` on the host — unreachable
    from the network.

- **OWASP A05 — Security Misconfiguration**:
  - `GF_USERS_ALLOW_SIGN_UP=false` prevents self-registration by arbitrary users.
  - Loki has `auth_enabled: false` because it is network-isolated. Exposing Loki's
    port 3100 externally without authentication is a critical misconfiguration; if
    external Loki access is needed, add basic-auth or mTLS (separate chore spec).
  - The Podman socket bind-mount in Promtail is read-only.
  - No observability service runs as root inside the container (Grafana, Loki, Promtail,
    and Tempo all default to non-root users in their official images).

- **OWASP A09 — Security Logging and Monitoring**:
  - This spec IS an OWASP A09 control: it aggregates all platform logs into a searchable
    store with a dashboard. Operators can configure Grafana Alerting (future spec) on
    `{level="error"}` patterns to enable active incident response.

- **Secret injection**:
  - `GRAFANA_SECRET_KEY` and `GRAFANA_ADMIN_PASSWORD` are documented in `AGENTS.md` as
    required root `.env` variables. The root `.env` is gitignored. No default values for
    these variables exist in any committed file — Compose will fail to start Grafana if
    they are absent, making misconfiguration visible rather than silent.

## Acceptance Criteria

- [x] **AC-1**: Given all four Podman services carry the `prometheus.service` label and
  Promtail is running, when each service emits at least one log line, then within 30 seconds
  Loki receives at least one log entry per labelled service.

- [x] **AC-2**: Given the gateway container emits a structlog JSON line containing
  `"level":"info"`, `"service":"gateway"`, `"trace_id":"test-trace-001"`, and
  `"event":"request.complete"`, when Promtail processes the line, then within 30 seconds
  the Loki query `{service="gateway", level="info"} | json | trace_id="test-trace-001"`
  returns that line.

- [x] **AC-3**: Given Promtail is running with the `MANAGER_LOG_HOST_PATH` bind-mount and
  `runtime/logs/manager.log` exists on the host, when the manager TUI appends a valid
  structlog JSON line to that file, then within 30 seconds the Loki query
  `{service="manager-tui"} | json` returns that line.

- [x] **AC-4**: Given Grafana starts with `./observability/grafana/provisioning` bind-mounted,
  when an authenticated user navigates to Grafana's "Data Sources" page without any manual
  configuration, then two datasources named "Loki" (default) and "Tempo" are present and
  their connection status is healthy.

- [x] **AC-5**: Given Grafana JWT auth is enabled and anonymous access is disabled, when
  an unauthenticated request is made without an `X-JWT-Assertion` header, then Grafana
  returns HTTP 401 or HTTP 302.

- [x] **AC-6**: Given the auth-service is healthy and Grafana's `GF_AUTH_JWT_JWK_SET_URL`
  points to the auth-service JWKS endpoint, when a client presents a valid RS256 JWT in
  the `X-JWT-Assertion` header, then Grafana returns HTTP 200.

- [x] **AC-7**: Given Grafana is running with the provisioning directory mounted, when any
  authenticated user navigates to `http://localhost:3000/d/prometheus-ops`, then the
  dashboard "Prometheus Ops" loads with three panels: "Log Volume by Service",
  "Error/Warning rate", and "Recent inference requests (gateway)".

- [x] **AC-8**: Given Grafana, Loki, Promtail, and Tempo are NOT running, when the gateway
  processes a `POST /v1/chat/completions` request and the auth-service issues a token, then
  both services return valid responses with no errors related to missing observability
  infrastructure.

- [x] **AC-9**: Given Tempo is running with `observability/tempo/tempo-config.yaml` mounted,
  when a well-formed OTLP/gRPC span is sent to `localhost:4317`, then within 10 seconds
  `GET http://localhost:3200/ready` returns HTTP 200.

- [x] **AC-10**: Promtail collects logs from all 4 services: `gateway`, `auth-service`,
  `manager`, and `manager-tui`. All appear in Loki with their respective `service` labels.

- [x] **AC-11**: No secret values are hardcoded in any committed file. All secrets use
  `${VAR}` environment variable references.

- [x] **AC-12**: ~~No Python source files modified~~ — see G-12 note above.

## Test Strategy

All tests for this spec are infrastructure-level shell scripts. No pytest suites are
required (AC-12: zero Python changes).

### Shell test: `observability/tests/test_observability_stack.sh`

```
Usage: bash observability/tests/test_observability_stack.sh [--static-only]

--static-only  Run pre-flight checks only (no running stack required).
               Suitable for CI on every push.
```

#### Stage 1 — Pre-flight static checks (no containers needed)

Run these on every CI push or `git push` hook:

1. **Config files exist**: assert each of the six committed files under `observability/`
   is present and non-empty.
2. **Dashboard JSON is valid**: `python3 -c "import json; json.load(open('...prometheus-ops.json'))"`.
3. **No hardcoded secrets**:
   ```bash
   grep -rniE "(secret_key|admin_password)\s*[=:]\s*[^\$\{]" observability/ podman-compose.yml
   # must exit non-zero (no matches)
   ```
4. **No Python file changes** (AC-12):
   ```bash
   git diff --name-only develop HEAD | grep '\.py$'
   # must produce empty output
   ```

#### Stage 2 — Stack health checks (requires `podman compose up`)

Run manually or in a dedicated integration CI job:

```bash
curl -f http://localhost:3100/ready          # Loki ready (AC-1)
curl -f http://localhost:9080/ready          # Promtail ready (AC-1)
curl -f http://localhost:3200/ready          # Tempo ready (AC-9)
curl -f http://localhost:3000/api/health     # Grafana ready (AC-4)
```

#### Stage 3 — Log injection test (AC-2, AC-3)

```bash
TRACE_ID="test-$(date +%s)"
# Inject a known line into the gateway container's stdout
podman exec prometheus-gateway \
  sh -c "printf '{\"timestamp\":\"%s\",\"level\":\"info\",\"service\":\"gateway\",
  \"event\":\"test.inject\",\"trace_id\":\"%s\"}\n' \
  \"$(date -u +%FT%TZ)\" \"$TRACE_ID\""

sleep 20

# Query Loki and assert the line was received
curl -sf \
  "http://localhost:3100/loki/api/v1/query?query=%7Bservice%3D%22gateway%22%7D%20%7C%20json%20%7C%20trace_id%3D%22$TRACE_ID%22" \
  | python3 -c "
import json, sys
data = json.load(sys.stdin)
assert len(data['data']['result']) > 0, f'Log line with trace_id={\"$TRACE_ID\"} not found in Loki'
print('PASS: log line found in Loki')
"
```

#### Stage 4 — Grafana auth test (AC-5, AC-6)

```bash
# AC-5: no JWT → expect 401 or 302
STATUS=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:3000/api/dashboards/home)
[[ "$STATUS" == "401" || "$STATUS" == "302" ]] || { echo "FAIL: expected 401/302, got $STATUS"; exit 1; }

# AC-5: tampered JWT (flip last character of signature)
VALID_JWT=$(curl -sk -X POST https://localhost:9000/oauth2/token \
  -d "grant_type=client_credentials&client_id=test&client_secret=test" \
  | python3 -c "import json,sys; print(json.load(sys.stdin)['access_token'])")
TAMPERED="${VALID_JWT%?}X"
STATUS=$(curl -s -o /dev/null -w "%{http_code}" \
  -H "X-JWT-Assertion: $TAMPERED" http://localhost:3000/api/dashboards/home)
[[ "$STATUS" == "401" ]] || { echo "FAIL: tampered JWT not rejected"; exit 1; }

# AC-6: valid JWT → expect 200
STATUS=$(curl -s -o /dev/null -w "%{http_code}" \
  -H "X-JWT-Assertion: $VALID_JWT" http://localhost:3000/api/dashboards/home)
[[ "$STATUS" == "200" ]] || { echo "FAIL: valid JWT rejected"; exit 1; }
```

### AC-to-test mapping

| AC | Test stage |
|----|-----------|
| AC-1 | Stage 2 (Loki/Promtail ready) + Stage 3 (log injection) |
| AC-2 | Stage 3 (structured metadata query) |
| AC-3 | Stage 3 (inject to manager.log bind-mount, query `{service="manager-tui"}`) |
| AC-4 | Stage 2 + `curl http://localhost:3000/api/datasources` → Loki + Tempo present |
| AC-5 | Stage 4 (no JWT → 401/302; tampered JWT → 401) |
| AC-6 | Stage 4 (valid JWT → 200) |
| AC-7 | Stage 2 + `curl http://localhost:3000/api/dashboards/uid/prometheus-ops-v1` → 200 |
| AC-8 | Manual: stop observability stack, run E2E test (`uv run validations/e2e_test.py`), verify no new errors |
| AC-9 | Stage 2 (Tempo ready) + OTLP test span via `grpcurl` or `otelcol` |
| AC-10 | Stage 2: start test service with label, confirm Loki entry without Promtail restart |
| AC-11 | Stage 1 (static secret scan) |
| AC-12 | Stage 1 (git diff `.py` files → empty) |

## Open Questions

- [x] **Q-1 (RESOLVED)**: Podman socket path is configurable via `PODMAN_SOCK_PATH` in
  the root `.env` (default: `/var/run/podman/podman.sock`). Promtail's socket bind-mount
  uses `${PODMAN_SOCK_PATH:-/var/run/podman/podman.sock}` in `podman-compose.yml`. Users
  running rootless Podman Desktop on macOS must set this accordingly.
- [x] **Q-2 (RESOLVED)**: Confirmed against `auth-service/src/prometheus_auth/crypto.py`
  line 84 — `scope` is a **space-separated string** in Access Tokens (not an array).
  JMESPath expression: `contains(scope, 'ops:dashboard') && 'Viewer' || ''`. A new scope
  `ops:dashboard` has been added to `VALID_SCOPES` in `auth-service/src/prometheus_auth/schemas.py`.
  `GF_AUTH_JWT_ROLE_ATTRIBUTE_STRICT=true` ensures users without this scope are denied.
- [x] **Q-3 (RESOLVED)**: `auth_enabled: false` is used for Loki. The Promtail ↔ Loki
  channel is hosted entirely within the isolated `prometheus_net` Podman network.
  Multi-tenant Loki auth is a future infrastructure concern (out of scope here).
- [x] **Q-4 (RESOLVED)**: `GF_AUTH_JWT_ENABLED` is available in Grafana CE 11.x (confirmed
  in upstream Grafana documentation for v11.6.1). No Enterprise license required. The pinned
  image `grafana:11.6.1` is stable CE.

---

## Implementation Notes

> This section documents decisions made and problems encountered during implementation
> that differ from or extend the original spec design. Added: 2026-04-18.

### Log collection: Docker SD replaced by k8s-file + virtiofs

The original spec specified **Docker SD** (`docker_sd_configs`) for Promtail to discover
Podman containers. This approach failed on **macOS + Podman Machine (rootless)** due to
network namespace isolation: containers cannot reach the Podman API socket via TCP from
within the compose network.

**Attempted approaches (all failed on rootless macOS):**

| Approach | Failure reason |
|----------|---------------|
| Docker SD via `/var/run/docker.sock` | Cross-namespace UNIX socket — container cannot read host's socket |
| Docker SD via socat TCP bridge (`10.89.0.1:2375`) | rootless namespace: `host.containers.internal` and `10.89.0.1` unreachable from container network |
| `logging.driver: syslog` | Not supported in rootless Podman (docker-compose API returns `invalid log driver`) |

**Working solution: k8s-file log driver + virtiofs-shared macOS path**

Each service container uses:
```yaml
logging:
  driver: k8s-file
  options:
    path: ${CONTAINER_LOG_HOST_PATH:-./runtime/container-logs}/<service>.log
```

`CONTAINER_LOG_HOST_PATH` is an **absolute macOS path** (e.g. `/Users/.../runtime/container-logs/`).
Because Podman Machine uses virtiofs to share the macOS filesystem into the VM, the
container writes its k8s-file logs directly to a macOS path. Promtail bind-mounts this
same directory read-only and statically scrapes each log file.

**Required root `.env` variable:**
```bash
CONTAINER_LOG_HOST_PATH=/absolute/path/to/edge-ai-inference/runtime/container-logs
```

### k8s-file log format and Promtail pipeline

The k8s-file driver writes each log line with a prefix:
```
<timestamp> <stream> F|P <json-log-line>
```

Where `F` = final (complete line) and `P` = partial (chunk of a long line). Long JSON
log lines (e.g. `inference.complete` with many fields) are split into multiple `P`
chunks followed by a final `F` chunk.

**Failed attempt**: regex stage `^\S+ \S+ \S (?P<raw_json>.+)$` — parsed each chunk
independently, producing `JSONParserErr: Value looks like object, but can't find closing '}'`
for `P` chunks.

**Working solution**: Promtail's built-in `cri: {}` stage handles the CRI/k8s-file format
natively — it reassembles `P` chunks into a complete line before parsing:
```yaml
pipeline_stages:
  - cri: {}
  - json:
      expressions:
        level: level
        ...
```

### Podman VM clock drift

After the macOS host sleeps, the Podman Machine VM loses wall-clock time. This causes
log timestamps to appear hours behind in Grafana.

**Symptom**: `"timestamp": "2026-04-14T06:00:00Z"` when the real UTC time is `11:00:00Z`.

**Fix** (run after Mac wakes from sleep):
```bash
ssh -i ~/.local/share/containers/podman/machine/machine \
  -p <VM_PORT> -o StrictHostKeyChecking=no core@127.0.0.1 \
  "sudo date -u -s \"$(date -u '+%Y-%m-%d %H:%M:%S')\""
```

**Alias** (add to `~/.zshrc`):
```bash
alias podman-sync-clock='ssh -i ~/.local/share/containers/podman/machine/machine \
  -p 60689 -o StrictHostKeyChecking=no core@127.0.0.1 \
  "sudo date -u -s \"$(date -u \"+%Y-%m-%d %H:%M:%S\")\""'
```

### Podman container restart vs rebuild

`podman restart <container>` reuses the existing image — new code changes are NOT applied.
To apply code changes:
```bash
podman compose -f podman-compose.yml up --build -d <service>
```

To restart Grafana (only config change, no code):
```bash
ssh -i ~/.local/share/containers/podman/machine/machine \
  -p 60689 -o StrictHostKeyChecking=no core@127.0.0.1 \
  "podman restart prometheus-grafana"
```

Note: `podman restart` from macOS fails because the active connection context does not
match the namespace where containers are running. Always use SSH for direct `podman`
commands, or use `podman compose` from macOS (which routes correctly).

### Python changes made

Despite G-12 targeting zero Python changes, the following fixes were made to improve
log quality and correctness:

| File | Change |
|------|--------|
| `telemetry/src/prometheus_telemetry/core.py` | Added `logging.getLogger("httpx").setLevel(logging.WARNING)` to suppress noisy httpx access logs |
| `telemetry/src/prometheus_telemetry/core.py` | Changed `TimeStamper(utc=True)` → `TimeStamper(utc=False)` for local timezone timestamps |
| `auth-service/src/prometheus_auth/routers/oauth2.py` | Added `logger.warning("oauth2.invalid_client", ...)` on failed auth attempts |
| `gateway/src/prometheus_gateway/ui/router.py` | Added `logger.warning("ui.login.invalid_credentials", ...)` on failed UI login |
| `gateway/src/prometheus_gateway/router.py` | Added `backend_id`, `backend_url`, `request_id`, `finish_reason` to `inference.complete` log; removed always-null `span_id` |
| `podman-compose.yml` | Added `TZ=America/Lima` to gateway, auth-service, and manager environment |

### Dashboard: final state

The provisioned dashboard (`prometheus-ops.json`, UID `prometheus-ops`) differs from
the spec design:

| Panel | Spec design | Actual implementation |
|-------|-------------|----------------------|
| Log Volume | `{job=~"podman_containers\|manager_tui"}` | `{service=~".+"}` (k8s-file scrape has no `job` label) |
| Error rate | `level="error"` only | `level=~"error\|warning"` — auth rejections log as `warning` (correct per HTTP semantics) |
| Log table | All services | `{service="gateway"} \| json \| event = \`inference.complete\`` — focused on inference requests |
| Timezone | `"browser"` | `"America/Lima"` |

### `inference.complete` log fields (final)

```json
{
  "event": "inference.complete",
  "model": "llama3-1b-q4-local",
  "backend_id": "llama3-1b-q4-local",
  "backend_url": "http://host.containers.internal:8080",
  "request_id": "<uuid>",
  "tokens_prompt": 40,
  "tokens_completion": 606,
  "tokens_total": 646,
  "latency_ms": 3601,
  "tokens_per_second": 168.29,
  "finish_reason": "stop",
  "user_id": "<client-uuid>",
  "client_id": "<client-uuid>"
}
```

## References

- Predecessor specs:
  - [memory/specs/018-observability-telemetry.md](018-observability-telemetry.md) — structured log schema
  - [memory/specs/020-shared-telemetry-package.md](020-shared-telemetry-package.md) — shared telemetry package
- Successor spec:
  - `memory/specs/022-opentelemetry-sdk.md` _(future)_ — OTEL SDK sends spans to Tempo
- External docs:
  - Loki configuration reference: https://grafana.com/docs/loki/latest/configure/
  - Promtail Docker SD: https://grafana.com/docs/loki/latest/send-data/promtail/configuration/#docker_sd_config
  - Promtail pipeline stages: https://grafana.com/docs/loki/latest/send-data/promtail/stages/
  - Tempo configuration reference: https://grafana.com/docs/tempo/latest/configuration/
  - Grafana JWT authentication: https://grafana.com/docs/grafana/latest/setup-grafana/configure-security/configure-authentication/jwt/
  - Grafana provisioning: https://grafana.com/docs/grafana/latest/administration/provisioning/
  - OWASP Top 10: https://owasp.org/www-project-top-ten/
