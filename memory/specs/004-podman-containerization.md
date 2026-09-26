---
id: "004"
title: "Podman Containerization of the Gateway"
status: closed
current-agent: ""
pipeline-log: []
created: 2026-03-28
updated: 2026-03-28
---

# 004 — Podman Containerization of the Gateway

## Problem Statement

The Prometheus Gateway currently runs as a bare Python process via `uvicorn` directly on the
developer's machine. There is no container image, no compose file, and no repeatable way to
deploy the gateway to the HPE DL380 test servers — engineers must manually install Python,
`uv`, and all dependencies on each target host.

Additionally, the organisation's policy mandates **Podman** instead of Docker as the container
runtime. Docker is not available on the HPE RHEL 9.7 servers. Any containerisation solution
must work with Podman (rootless where possible) and Podman Compose or `podman play kube` for
multi-container orchestration.

## Goals

- [ ] Provide a `gateway/Dockerfile` that builds a minimal, production-ready gateway image
- [ ] Provide a `runtime/manager/Dockerfile` that builds a minimal Manager REST API image
- [ ] Provide a `podman-compose.yml` at repo root for local and server deployment
- [ ] Gateway container reaches `llama-server` on the host via the correct Podman host gateway address
- [ ] Manager container reaches llama-server health endpoints via `host.containers.internal`
- [ ] Manager container reads `registry.yaml` via a bind-mount from the bare-metal host
- [ ] Secrets (JWT public key, env vars) injected via Podman secrets / env file — never baked into the image
- [ ] Image runs as a non-root user inside the container
- [ ] Gateway starts and passes health check within 30 seconds
- [ ] Document the full deployment procedure for the HPE RHEL 9.7 servers
- [ ] Provide an optional `runtime/systemd/` unit file for auto-starting the gateway on RHEL boot (rootless user service)

## Non-Goals

- Containerising llama.cpp — it remains bare-metal (see ADR-001)
- Containerising the Manager TUI — `pmgr tui` runs bare-metal (requires terminal and process access)
- Kubernetes / OpenShift deployment (out of scope for v0.x)
- CI/CD pipeline for building and pushing the image to a registry
- Docker compatibility — Podman is the only supported runtime

## Proposed Solution

### Container Images

**`gateway/Dockerfile`** — Multi-stage build using the official Python 3.13 slim image:

1. **Build stage**: install `uv`, sync dependencies into a virtual environment.
2. **Runtime stage**: copy only the venv and application source; run as non-root `prometheus` user (uid 1000).

**`runtime/manager/Dockerfile`** — Same multi-stage pattern:

1. **Build stage**: install `uv`, sync only the `prometheus-manager` package and its deps.
2. **Runtime stage**: run as non-root `pmgr` user (uid 1001). Runs `pmgr serve` only — no TUI, no psutil-based process scanning. The `PMGR_PROXY_HOST` env var enables HTTP health probing of llama-server via `host.containers.internal`.

The image exposes port `8000` and starts `uvicorn prometheus_gateway.asgi:app`.

### Compose File (`podman-compose.yml`)

Two services:

| Service | Image | Purpose |
|---------|-------|---------|
| `gateway` | built locally from `gateway/Dockerfile` | Prometheus API Gateway |
| `redis` | `docker.io/redis:7-alpine` | Rate-limit counters + JWT revocation cache |

The gateway reaches `llama-server` on the host via:
- **macOS (dev)**: `host.docker.internal:8080` (Podman Desktop sets this up automatically)
- **RHEL 9.7 (rootless Podman)**: `host.containers.internal:8080` (Podman's equivalent)

The `LLAMA_CPP_URL` env var in the compose file must be set per environment.

### Podman-specific Considerations

| Docker concept | Podman equivalent |
|---------------|------------------|
| `docker compose` | `podman compose` (wrapper) or `podman-compose` |
| `host.docker.internal` | `host.containers.internal` (rootless) |
| Docker secrets | Podman secrets (`podman secret create`) or `--env-file` |
| `docker run --user` | Identical syntax in Podman |
| `systemd` service | `podman generate systemd` → rootless user unit in `~/.config/systemd/user/`; requires `loginctl enable-linger <user>` |

### Key Design Decisions

| Decision | Rationale |
|----------|-----------|
| Multi-stage build | Keeps runtime image lean (no build tools in production layer) |
| Non-root user (`prometheus`, uid 1000) | Principle of least privilege; required for rootless Podman on RHEL |
| `host.containers.internal` on RHEL | Podman rootless does not support `host.docker.internal`; `host.containers.internal` is the standard hostname |
| Env file for secrets | Podman secrets API requires additional tooling; `--env-file` with filesystem permissions is simpler for v0.x |
| Redis in compose | Required by JWT revocation (spec 002) and rate limiting (spec coming); minimal footprint with `redis:7-alpine` |

## API Contract

No new gateway API endpoints. The containerised gateway exposes the same API as the bare-metal
process. Health check endpoint used by Podman: `GET /health` → `{"status":"ok"}`.

## Data Model

No new data models. Configuration continues to be driven by environment variables as defined
in `gateway/.env.example`. A new `gateway/.env.podman.example` will document the
Podman-specific overrides (e.g. `LLAMA_CPP_URL=http://host.containers.internal:8080`).

## Security Considerations

- **Non-root container**: the `prometheus` user (uid 1000) owns the app files; `uvicorn` binds
  to port 8000 (unprivileged).
- **No secrets in image**: JWT public key is mounted at runtime via `--secret` or volume mount;
  never copied into the image layer.
- **No privileged mode**: the container must not require `--privileged` or `CAP_SYS_ADMIN`.
- **Read-only filesystem**: the runtime container should use `--read-only` where possible;
  only `/tmp` needs to be writable (uvicorn temp files).
- **Network exposure**: only port 8000 is exposed. Redis is internal-only (no host port binding).
- **llama.cpp unreachable from container network**: `host.containers.internal` resolves to the
  host loopback — llama.cpp still binds to `127.0.0.1`, so this does not change the security
  posture established in ADR-001.

## Acceptance Criteria

### Dockerfile

- [ ] **AC-1**: Given the repo root, when `podman build -f gateway/Dockerfile -t prometheus-gateway .`
  is run, then the build completes without errors and the image is present in the local store.
  *Verify*: `podman images | grep prometheus-gateway`.

- [ ] **AC-2**: Given the built image, when `podman inspect prometheus-gateway` is run, then
  the image runs as user `prometheus` (uid 1000) and not as `root` (uid 0).
  *Verify*: `podman inspect prometheus-gateway --format '{{.Config.User}}'` → `prometheus`.

- [ ] **AC-3**: Given the built image, when `podman run --rm prometheus-gateway pip list` is
  executed, then `pip`, `setuptools`, build tools, and `uv` are NOT present in the runtime
  layer (multi-stage build working correctly).
  *Verify*: command exits non-zero or returns empty (pip not in PATH in runtime stage).

### podman-compose.yml

- [ ] **AC-4**: Given a correctly configured `gateway/.env`, when `podman compose up -d` is
  run from the repo root, then `gateway`, `manager`, `auth-service`, and `redis` containers
  reach `running` / healthy state within 60 seconds.
  *Verify*: `podman compose ps` shows all four as `Up`.

- [ ] **AC-5**: Given both containers running, when `curl -s http://localhost:8000/health`
  is called from the host, then the response is HTTP 200 with body `{"status":"ok"}`.
  *Verify*: `curl -sf http://localhost:8000/health` exits 0.

- [ ] **AC-6**: Given both containers running and `llama-server` running on the host at
  `127.0.0.1:8080`, when the gateway container executes a connectivity check to
  `${LLAMA_CPP_URL}`, then it receives HTTP 200 from llama-server's `/health` endpoint.
  *Verify*: `podman exec prometheus-gateway-gateway-1 curl -s ${LLAMA_CPP_URL}/health`.

### Security

- [ ] **AC-7**: Given the running gateway container, when `podman exec` runs `whoami`, then
  the output is `prometheus` (not `root`).
  *Verify*: `podman exec prometheus-gateway-gateway-1 whoami` → `prometheus`.

- [ ] **AC-8**: Given the `podman-compose.yml` source, when it is inspected, then Redis has
  no host port binding (`- "6379:6379"` must NOT appear) — Redis is internal-only.
  *Verify*: `grep '6379:6379' podman-compose.yml` returns nothing.

- [ ] **AC-9**: Given the built image layers, when `podman history prometheus-gateway` is
  inspected, then no JWT key material, `.env` content, or credentials appear in any layer.
  *Verify*: `podman history --no-trunc prometheus-gateway | grep -iE 'key|secret|password|token'`
  returns nothing referencing actual secrets.

### RHEL 9.7 Deployment

- [ ] **AC-10**: Given an HPE DL380 with RHEL 9.7, Podman ≥ 4.x installed, and
  `llama-server` running on `127.0.0.1:8080`, when the deployment procedure in the runbook
  is followed, then the gateway is reachable at `http://<server-ip>:8000/health`.
  *Verify*: `curl -s http://<server-ip>:8000/health` from another machine → `{"status":"ok"}`.

- [ ] **AC-11**: Given the Manager API container is running with a bind-mounted `registry.yaml`,
  when a TUI operator toggles `discovery` on a model, then the next `GET /v1/backends` call
  returns the updated state without restarting the container.
  *Verify*: edit `discovery: true` in `runtime/manager/registry.yaml` on the host, wait ≤1 s,
  then `curl .../v1/backends` returns the updated entry.

- [x] **Q1**: On the HPE DL380 servers, should the gateway run as a rootless Podman container
  (user service) or as a rootful container (root service managed by systemd)?
  **Answer**: Rootless (`loginctl enable-linger` will be configured on the target user).

- [x] **Q2**: Is there a private container registry available in the environment, or should
  the image be built directly on each target host from source?
  **Answer**: No registry — image is built directly on each target host from source.

- [x] **Q3**: Should a `podman generate systemd` unit file be committed to the repo for
  auto-start of the gateway on RHEL server boot?
  **Answer**: Yes, but optional — ship it in `runtime/systemd/` with clear instructions;
  operators choose whether to install it.

## References

- Related specs: [memory/specs/001-gateway-core.md](001-gateway-core.md), [memory/specs/002-jwt-authentication-middleware.md](002-jwt-authentication-middleware.md)
- Decision: [memory/decisions/2026-03-28-llama-cpp-bare-metal.md]../decisions/2026-03-28-llama-cpp-bare-metal.md)
- Podman documentation: https://docs.podman.io
- Podman Compose: https://github.com/containers/podman-compose
- RHEL 9 container guide: https://access.redhat.com/documentation/en-us/red_hat_enterprise_linux/9/html/building_running_and_managing_containers
