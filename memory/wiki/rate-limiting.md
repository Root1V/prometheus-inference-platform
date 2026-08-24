# Rate Limiting

How the gateway enforces request and token-per-minute limits per client, and how Redis backs the state.

> Source: `memory/specs/007-rate-limiting-and-throughput.md`

---

## Two limits enforced simultaneously

Every inference request is checked against two independent sliding-window counters:

| Limit | Scope | Default | Env var | Reject condition |
|-------|-------|---------|---------|-----------------|
| RPM (requests/min) | per `client_id` AND per `user_id` | 60 | `RATE_LIMIT_RPM` | request count > limit |
| TPM (tokens/min) | per `client_id` AND per `user_id` | 40 000 | `RATE_LIMIT_TPM` | `max_tokens` would exceed remaining budget |

Both `client_id` and `user_id` are checked — whichever is exhausted first applies.

---

## Redis key layout

```
prometheus:rate:rpm:<client_id>          TTL = 60 s
prometheus:rate:rpm:<user_id>            TTL = 60 s
prometheus:rate:tpm:<client_id>          TTL = 60 s
prometheus:rate:tpm:<user_id>            TTL = 60 s
prometheus:jwks:<url_hash>               TTL = 300 s  (shared JWKS cache)
```

Counters are incremented atomically via Redis `INCR` + `EXPIRE` pipeline — no race conditions.

---

## Response headers

Every response (allowed and rejected) includes:

```
X-RateLimit-Limit: 60
X-RateLimit-Remaining: 42
X-RateLimit-Reset: 1746316800
```

Rejected requests also include:

```
HTTP/1.1 429 Too Many Requests
Retry-After: 23
```

---

## Context window guard (pre-flight)

Before forwarding to llama.cpp, the gateway rejects requests that exceed the model's context length:

- If `max_tokens` > model's `context_length` in `registry.yaml` → `400 Bad Request`
- If total estimated token count of `messages` > `context_length` (approx: 4 chars ≈ 1 token) → `400 context-exceeded`

This prevents wasting GPU time on requests that would fail at the backend anyway.

---

## Redis unavailability modes

Controlled by `RATE_LIMIT_STRICT` (default: `true`):

| Mode | Behaviour when Redis is down |
|------|------------------------------|
| `RATE_LIMIT_STRICT=true` | Reject all requests with `503 Rate Limiting Unavailable` |
| `RATE_LIMIT_STRICT=false` | Log a warning, allow the request (fail-open) |

Choose `strict=false` only for development environments where Redis may not always be running.

---

## Circuit breaker

Prevents cascading failures when a llama-server backend is overloaded or crashing.

```
States:  CLOSED → OPEN → HALF-OPEN → CLOSED
                    ↑
         failures > threshold within window
```

- **CLOSED**: requests flow normally
- **OPEN**: fast-fail with `503 backend-unavailable` — no connection attempt
- **HALF-OPEN**: one probe request allowed; if it succeeds, circuit closes

Without a circuit breaker, a single overloaded backend would hold all connection slots open for ~30 s each, producing cascading `502` errors for all clients.

---

## Throughput observability

Every completed inference request emits these structured log fields:

```json
{
  "tokens_per_second": 42.3,
  "backend_latency_ms": 1820,
  "queue_wait_ms": 12,
  "model": "llama3-8b-q4-local",
  "client_id": "...",
  "user_id": "...",
  "tokens_used": 312
}
```

`GET /v1/backends` (scope: `admin:models`) includes a `requests_last_minute` field per model read from Redis counters.

`GET /v1/usage` (scope: `admin:usage`) returns aggregate token consumption per `client_id` for the current UTC day, read from Redis sorted sets.

---

## Retry logic for transient backend failures

When a backend returns a transient error (e.g. 503 from a restarting llama-server), the gateway retries **once** before returning the error to the client. This prevents surfacing momentary restart blips as client failures. A second consecutive failure is returned immediately without further retry.

---

## Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `RATE_LIMIT_RPM` | `60` | Max requests per minute per client/user |
| `RATE_LIMIT_TPM` | `40000` | Max tokens per minute per client/user |
| `RATE_LIMIT_STRICT` | `true` | Block all requests when Redis is down |

---

## Related

- `memory/specs/007-rate-limiting-and-throughput.md` — full AC list
- `memory/decisions/2026-03-28-redis-for-state.md` — why Redis for rate-limit counters
- [auth-model.md](auth-model.md) — `client_id` and `user_id` extraction from JWT
