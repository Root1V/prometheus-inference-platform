# Redis for shared runtime state

**Date**: 2026-03-28  
**Status**: accepted  
**Scope**: rate-limit counters, token revocation, JWKS cache

---

## Context

The Prometheus stack runs multiple Uvicorn workers in production (RHEL 9.7) and at least two containerised services (gateway, auth-service) that need to share state at sub-second latency. Three specific state problems required a solution:

1. **Rate-limit counters** must be shared across all gateway worker processes. In-process counters reset on worker restart and cannot be shared across processes — a single client could bypass per-minute limits by hitting different workers.
2. **Token revocation** (immediate `DELETE /admin/clients/{id}`) must propagate to the gateway within the same request cycle. A database query on every request was considered but rejected due to latency.
3. **JWKS key cache** must be shared across gateway workers. Without sharing, each worker independently fetches the JWKS endpoint, amplifying load on the auth-service proportionally to the worker count.

---

## Decision

Use **Redis** as the shared state store for all runtime state that must be consistent across processes and containers.

Redis is already deployed in `podman-compose.yml` as a first-class service. No additional infrastructure is introduced.

---

## State stored in Redis

| Key pattern | TTL | Purpose |
|-------------|-----|---------|
| `prometheus:rate:rpm:<id>` | 60 s | Sliding-window RPM counter per client/user |
| `prometheus:rate:tpm:<id>` | 60 s | Sliding-window TPM counter per client/user |
| `revoked:client:<client_id>` | role TTL | Immediate client revocation signal |
| `revoked:jti:<jti>` | token TTL | Per-token revocation (future use) |
| `prometheus:jwks:<url_hash>` | 300 s | Shared JWKS public key cache |

All keys use TTLs — no manual cleanup required. Keys auto-expire when they can no longer affect correctness.

---

## Rationale

| Requirement | Redis | In-process dict | Database (SQLite) |
|-------------|-------|-----------------|-------------------|
| Shared across workers | ✓ | ✗ | ✓ |
| Shared across containers | ✓ | ✗ | ✗ (file-based) |
| Sub-millisecond reads | ✓ | ✓ | ✗ |
| Atomic increment (`INCR`) | ✓ | ✗ (requires lock) | ✗ |
| Auto-expiring keys (TTL) | ✓ | ✗ | ✗ |
| Already in the stack | ✓ | — | ✗ |
| Persistence required | ✗ | — | ✓ |

Redis wins on every runtime-state requirement. Persistence is intentionally not required — rate-limit counters and revocation signals are ephemeral by design. A gateway restart correctly resets all in-flight counters.

---

## Availability and fail-open policy

Redis is a runtime dependency, not a hard startup dependency. The gateway handles Redis unavailability via `RATE_LIMIT_STRICT`:

| `RATE_LIMIT_STRICT` | Redis down behaviour |
|--------------------|---------------------|
| `true` (default) | Reject all requests with `503 Rate Limiting Unavailable` |
| `false` | Log warning, allow requests (fail-open) |

Revocation checks: if Redis is unreachable, revocation checks are skipped and the error is logged at `WARNING`. This is a deliberate trade-off — a Redis outage should not take the inference platform offline, but the risk of accepting a revoked token during a brief outage is accepted.

JWKS cache: falls back to per-process in-memory cache on Redis unavailability. No service disruption.

---

## Consequences

- Redis must be running before the gateway starts in strict mode.
- The root `.env` does not need a `REDIS_HOST_PATH` bind-mount — Redis runs entirely inside the Podman network with no host filesystem access.
- Redis is not configured with persistence (`appendonly no`, `save ""`) — state is ephemeral. A Redis restart resets all counters, which is acceptable: the worst case is a brief window where rate limits are not enforced.
- The connection pool is configured with automatic reconnect — a transient Redis restart does not require a gateway restart.

---

## Rejected alternatives

| Alternative | Reason rejected |
|-------------|----------------|
| In-process state per worker | Cannot share across workers or containers |
| SQLite for rate counters | No atomic increment; file locking under concurrent writes; latency too high |
| Memcached | No native atomic increment; no pub/sub for future use; less ecosystem support |
| Sticky sessions (route client to same worker) | Breaks horizontal scaling; not viable with Podman networking |

---

## References

- `memory/specs/007-rate-limiting-and-throughput.md` — rate limiting implementation (AC-3, AC-4, AC-7)
- `memory/specs/005-auth-service.md` — immediate revocation via Redis (AC-15, AC-16)
- [memory/wiki/rate-limiting.md]../wiki/rate-limiting.md) — Redis key layout and fail-open behaviour
- [memory/wiki/auth-model.md]../wiki/auth-model.md) — revocation flow
