# Manager owns the model registry

**Date**: 2026-03-29  
**Status**: accepted  
**Scope**: model registry, gateway routing, Manager API

---

## Context

In specs 001–006 the gateway owned the model registry: it read `runtime/models/registry.yaml` directly at startup and built its routing table from that static file. The gateway was a containerised service (Podman) trying to read a YAML file that lives on the bare-metal host — a leaky abstraction that mixed infrastructure knowledge into the application layer.

Three concrete problems emerged:

1. **Wrong layer owns the data**: which models are available and running is a bare-metal concern (the host knows which processes are alive). The gateway is a network proxy — it should not need to know about GGUF paths, ports, or process state.
2. **Static registry**: gateway reads registry once at startup. Adding or removing a model requires restarting the gateway container, even though the inference server change happens on the host.
3. **No live state**: the file had no concept of "is this instance actually running right now?" — the gateway would try to forward to a `backend_url` that might point to a stopped process.

---

## Decision

The **Manager** (`runtime/manager/`) owns `registry.yaml` and is the single source of truth for model availability.

The **gateway** exposes a public discovery endpoint `GET /v1/models` for clients. Internally the Gateway authenticates to the Manager REST API to retrieve authoritative model data; the Manager exposes an admin endpoint `GET /v1/backends` which requires a service account JWT with scope `backend-registry:read`.

```
[Gateway :8000]         ← public discovery endpoint `/v1/models`
    │  GET /v1/models      (unauthenticated, discovery)
    │
    │  (internally, gateway queries Manager for authoritative data)
    │  GET /v1/backends    (service account JWT with scope: backend-registry:read)
    ▼
[Manager API :8090]     ← reads registry.yaml, returns discovery:true entries (admin: `/v1/backends`)
    │
    ▼
[registry.yaml]         ← owned by Manager, on bare-metal host
```

---

## Rationale

| Concern | Before (gateway owns registry) | After (manager owns registry) |
|---------|-------------------------------|-------------------------------|
| Live state | None — static file | Manager knows which processes are running |
| Adding a model | Edit YAML + restart gateway | Edit via TUI/CLI — gateway picks up on next request |
| Layer responsibility | Gateway knows about GGUF paths and ports | Gateway knows only model IDs and backend URLs |
| Security | Gateway container needs read access to host filesystem | Gateway authenticates to Manager API with JWT |
| Single source of truth | `runtime/models/registry.yaml` (read by gateway) | `runtime/manager/registry.yaml` (read by Manager only) |

---

## Consequences

- The `LLAMA_CPP_URL` single-backend env var is removed. Backend URLs now come from the Manager API response.
The gateway requires a service account JWT with scope `backend-registry:read` to call the Manager API's admin endpoint (`GET /v1/backends`). This client is registered in the `auth-service` like any other machine account. The Gateway then exposes a filtered, public discovery response at `GET /v1/models` for clients.
- The Manager API binds to `0.0.0.0:8090` so the gateway container can reach it via `host.containers.internal:8090`. JWT authentication is the security boundary — the API is not accessible without a valid token.
- `registry.yaml` moves from `runtime/models/registry.yaml` to `runtime/manager/registry.yaml`. The Manager is the only component that reads or writes it.
- The gateway's routing table is refreshed on every request (or cached with a short TTL) — no restart required when models are added or removed.

---

## Rejected alternatives

| Alternative | Reason rejected |
|-------------|----------------|
| Keep gateway reading registry.yaml directly | Requires bind-mounting host path into gateway container; couples gateway to bare-metal layout |
| Shared database (SQLite/Postgres) as registry | Operational overhead; the manager already has a well-defined API surface |
| Gateway polls a static URL on the host | Same coupling problem; no live state awareness |

---

## References

- `memory/specs/008-llama-server-manager.md` — Manager API design and implementation
- `memory/specs/006-multi-model-gateway.md` — per-model routing that motivated this change
- `memory/specs/010-registry-view-redesign.md` — discovery flag and registry ownership
- [memory/wiki/model-registry.md]../wiki/model-registry.md) — registry schema and routing behaviour
