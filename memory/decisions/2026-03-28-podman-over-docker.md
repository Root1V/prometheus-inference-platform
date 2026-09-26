# Podman over Docker

**Date**: 2026-03-28  
**Status**: accepted  
**Scope**: all containerised services (gateway, auth-service, observability stack)

---

## Context

The target production environment is **HPE DL380 servers running RHEL 9.7**. Docker is not available on these servers — Red Hat's enterprise policy ships Podman as the default container runtime. Additionally, the organisation's security posture requires daemonless, rootless containers where possible.

On macOS (development), both Docker Desktop and Podman Desktop are available, but they must share the same `podman-compose.yml` and `Dockerfile` syntax to avoid maintaining two separate configurations.

---

## Decision

Use **Podman** as the sole container runtime across all environments.  
Use **podman-compose** (YAML compatible with Docker Compose v2) for multi-service orchestration.  
Never introduce Docker-specific constructs.

---

## Rationale

| Factor | Docker | Podman |
|--------|--------|--------|
| Available on RHEL 9.7 | ✗ (not in default repos) | ✓ (bundled with RHEL) |
| Rootless operation | Limited | Native |
| Daemonless | ✗ (requires `dockerd`) | ✓ |
| Compose YAML compatibility | ✓ | ✓ (podman-compose) |
| OCI image compatibility | ✓ | ✓ |
| macOS support | Docker Desktop | Podman Desktop / podman machine |
| Security surface | Daemon runs as root | No persistent daemon |

Rootless Podman on RHEL 9.7 means compromising a container does not grant root access to the host — a meaningful security improvement for a platform that handles inference traffic.

---

## Consequences

- `host.docker.internal` is **not** available on RHEL rootless Podman. Use `host.containers.internal` instead. The compose file must use `host.containers.internal` for the gateway → llama.cpp connection.
- On macOS, Podman Desktop sets up `host.containers.internal` automatically inside the Podman VM. The same hostname works on both platforms.
- `podman machine start` is required on macOS before running `podman compose`.
- The corporate CA (Zscaler TLS interception) must be injected into the Podman VM before `podman build` can pull packages — Docker Desktop handles this differently via its VM settings.
- Rootless Podman uses a different network stack (pasta / slirp4netns) — port mappings behave the same but the internal bridge IP may differ from Docker's `172.17.0.0/16`.

---

## Rejected alternatives

| Alternative | Reason rejected |
|-------------|----------------|
| Docker + Docker Compose | Not available on RHEL 9.7 production servers |
| `podman play kube` (Kubernetes YAML) | More complex syntax, no added value at this scale |
| Bare Python + systemd units for all services | Loses isolation, complicates dependency management, harder to deploy consistently |

---

## References

- `memory/specs/004-podman-containerization.md` — implementation details
- [memory/wiki/deployment.md]../wiki/deployment.md) — startup procedure
