---
id: "007"
title: "Rate Limiting & Throughput Optimisation"
status: closed
current-agent: ""
pipeline-log: []
created: 2026-03-28
updated: 2026-03-29
---

# 007 — Rate Limiting & Throughput Optimisation

## Problem Statement

The Prometheus Gateway today has **no enforcement of request rate limits**. Redis is already
deployed in the compose stack and the architecture documentation already describes rate limiting
as a gateway responsibility — but no middleware implements it. Additionally, several throughput
bottlenecks exist that limit the platform's effective capacity:

1. **No rate limiting**: a misbehaving or compromised client can saturate the inference engine,
   starving all other clients. There is no per-client, per-user, or per-endpoint throttle.
2. **No token counting before forwarding**: requests that exceed a model's context window are
   forwarded to llama.cpp and fail at that layer, wasting GPU time and connection slots.
3. **JWT JWKS in-process cache only**: the JWKS key cache lives in a single worker process.
   Under uvicorn with multiple workers (production RHEL deployment), each worker fetches JWKS
   independently, amplifying load on the auth-service.
4. **No request queue or concurrency cap per backend**: a slow 8B inference request can consume
   the only httpx connection while fast 1B requests queue behind it — even though both are
   available as separate backends.
5. **No `Retry-After` header**: clients that hit rate limits receive a 429 with no guidance on
   when to retry, causing aggressive retry storms.
6. **Structured logging lacks throughput observability fields**: `tokens_per_second`,
   `queue_wait_ms`, and `backend_latency_ms` are not emitted, making SLA monitoring impossible.
7. **No circuit breaker on inference backends**: when llama-server is overloaded or crashing,
   the gateway keeps forwarding requests until each one times out (~30 s), occupying connection
   slots and producing cascading 502 errors for all clients. There is no fast-fail mechanism.
8. **No retry logic for transient backend failures**: a single 503 from a restarting llama-server
   is returned immediately to the client even if the server would recover within milliseconds.
9. **In-process state is lost on gateway restart**: circuit breaker state and in-flight counter
   corrections exist only in memory. A gateway pod restart resets all operational state,
   potentially allowing burst traffic to bypass limits immediately after restart.
10. **Redis restart breaks the gateway**: if Redis is restarted independently, the gateway's
    persistent connection pool becomes stale and does not reconnect automatically without
    an explicit health-check strategy.

Without these controls the platform cannot safely serve multiple clients simultaneously, cannot
fulfil the SLA commitments described in the architecture, and cannot be monitored effectively.

## Goals

- [ ] **AC-1** Enforce a sliding-window RPM (requests per minute) limit per `client_id`,
      configurable via `RATE_LIMIT_RPM` env var (default: 60). Reject excess requests with
      `429 Too Many Requests` + `Retry-After` header.
- [ ] **AC-2** Enforce a sliding-window TPM (tokens per minute) limit per `client_id`,
      configurable via `RATE_LIMIT_TPM` env var (default: 40 000). Reject requests whose
      `max_tokens` would exceed the remaining TPM budget with `429 Too Many Requests`.
- [ ] **AC-3** Rate limit counters stored in Redis with sliding-window TTL; counter increments
      are atomic (Redis `INCR` + `EXPIRE` via pipeline).
- [ ] **AC-4** When Redis is unavailable and `RATE_LIMIT_STRICT=true` (default), reject all
      requests with `503 Rate Limiting Unavailable`. When `RATE_LIMIT_STRICT=false`, log a
      warning and allow the request (fail-open).
- [ ] **AC-5** Include `X-RateLimit-Limit`, `X-RateLimit-Remaining`, and `X-RateLimit-Reset`
      headers on every response (both allowed and rejected).
- [ ] **AC-6** Enforce a hard `max_tokens` cap per request: if `max_tokens` exceeds the
      model's `context_length` in `registry.yaml`, reject with `400 Bad Request` before
      forwarding to the backend.
- [ ] **AC-7** Share the JWKS key cache across uvicorn workers using Redis
      (`prometheus:jwks:<url_hash>` key, TTL = 5 min). Fall back to per-process in-memory
      cache if Redis is unavailable.
- [ ] **AC-8** Emit `tokens_per_second`, `backend_latency_ms`, and `queue_wait_ms` as
      structured log fields on every completed inference request (streaming and non-streaming).
- [ ] **AC-9** Rate limit counters are scoped per `client_id` AND per `user_id` (the `sub`
      claim). Whichever limit is exhausted first applies.
- [ ] **AC-10** The `GET /v1/backends` admin response includes a `requests_last_minute` field
      per model, read from Redis counters, giving operators real-time load visibility.
- [ ] **AC-11** A `GET /v1/usage` endpoint (scope: `admin:read`) returns aggregate token
      consumption per `client_id` for the current UTC day, read from Redis sorted sets.
- [ ] **AC-12** `POST /v1/chat/completions` rejects requests where `messages` total exceeds
      the model's `context_length` (estimated via token count approximation: 4 chars ≈ 1 token)
      before forwarding, returning `400 context-exceeded`.
- [ ] **AC-13** Each inference endpoint has its own RPM/TPM bucket. Default limits apply
      globally; per-endpoint overrides are configured via env vars
      (e.g. `RATE_LIMIT_RPM_CHAT_COMPLETIONS=30`). Rate limit headers reflect the
      per-endpoint limit actually applied.
- [ ] **AC-14** The gateway implements a per-backend circuit breaker with three states:
      `CLOSED` (normal), `OPEN` (fast-fail), `HALF-OPEN` (probe). After
      `CIRCUIT_BREAKER_FAILURE_THRESHOLD` consecutive failures the circuit opens. After
      `CIRCUIT_BREAKER_RECOVERY_TIMEOUT` seconds the circuit moves to HALF-OPEN; one
      probe request is forwarded. On success → `CLOSED`; on failure → `OPEN` again.
- [ ] **AC-15** While a backend circuit is `OPEN`, the gateway returns `503 Backend
      Unavailable` immediately (no backend call), with a `Retry-After` header set to the
      remaining recovery timeout. The response body includes the `backend_id` and
      `circuit_recovery_at` timestamp.
- [ ] **AC-16** Circuit breaker state is stored in Redis (`prometheus:cb:{backend_id}:*`
      keys) so that state survives a gateway pod restart and is shared across uvicorn workers.
- [ ] **AC-17** On transient backend errors (HTTP 502, 503, 504, or connection timeout),
      the gateway retries the request up to `BACKEND_RETRY_MAX` times (default: 2) with
      exponential backoff (`BACKEND_RETRY_BACKOFF_BASE_MS` × 2^attempt, jittered ±20%).
      Each failed attempt increments the circuit breaker failure counter. Retries are NOT
      applied to client errors (4xx) or streaming requests already acknowledged.
- [ ] **AC-18** On gateway startup (or restart), the gateway reads existing circuit breaker
      state and rate limit counters from Redis before serving traffic. No warmup period is
      required — the first request benefits from pre-existing Redis state immediately.
- [ ] **AC-19** When Redis is restarted independently, the gateway detects the stale
      connection, reconnects automatically (via connection-pool health checks), and resumes
      normal operation. A warning is logged per reconnection attempt. No gateway restart
      is required.
- [ ] **AC-20** The `GET /v1/backends` admin endpoint exposes circuit breaker state per
      backend: `circuit_state`, `consecutive_failures`, `circuit_opened_at`, and
      `circuit_recovery_at` (null when closed).

## Non-Goals

- Distributed rate limiting across multiple gateway instances (single-node only for v1)
- Hard monthly quota enforcement (requires persistent storage, deferred to a billing spec)
- Token counting using a real tokenizer (approximation is sufficient for this spec)
- Redis Cluster or Redis Sentinel — single Redis node only
- UI dashboard for rate limit / usage metrics
- Circuit breaker across multiple gateway instances (per-worker Redis state is sufficient)
- Retrying non-idempotent mutating requests that partially succeeded

## Proposed Solution

### Rate Limiting Middleware

A new `RateLimitMiddleware` ASGI class inserted **after** `JWTAuthMiddleware` in the stack
(so `request.state.claims` is available). It uses Redis pipelines for atomic counter
operations with sliding-window semantics.

```
[JWTAuthMiddleware]  →  [RateLimitMiddleware]  →  [Router → BackendPool → CircuitBreaker]
         ↓                         ↓                               ↓
  reads claims.client_id    Redis INCR + EXPIRE             retry + CB state
  reads claims.user_id      pipeline (atomic)
```

**Sliding-window counter pattern** (per-minute bucket, per-endpoint):

```
Key:  prometheus:rl:rpm:{client_id}:{endpoint_slug}:{minute_bucket}
      prometheus:rl:tpm:{client_id}:{endpoint_slug}:{minute_bucket}
      prometheus:rl:rpm:{user_id}:{endpoint_slug}:{minute_bucket}
TTL:  90 seconds (covers current + previous minute for sliding accuracy)
```

The `endpoint_slug` is derived from the matched route name (e.g., `chat_completions`,
`embeddings`). The minute bucket is `int(time.time() // 60)`. On each request:
1. Resolve the effective RPM/TPM limit: check for a per-endpoint env override first,
   fall back to the global `RATE_LIMIT_RPM` / `RATE_LIMIT_TPM`.
2. `INCR` the RPM counter atomically.
3. If counter == 1, set `EXPIRE 90`.
4. Check against limit; if exceeded → return 429 with endpoint-aware message.
5. After a successful response (streaming complete), `INCRBY` TPM counter by actual
   `completion_tokens + prompt_tokens`.

### Circuit Breaker

A `CircuitBreaker` class is attached to each backend entry in `BackendPool`. State is
persisted in Redis so it survives gateway pod restarts and is shared across uvicorn workers.

**State machine:**

```
   CLOSED ──(failures ≥ threshold)──► OPEN
     ▲                                   │
     │                            (recovery timeout)
     │                                   │
     └──(probe success)──── HALF-OPEN ◄──┘
                    │
             (probe failure)
                    │
                    └──────────────────► OPEN
```

**Redis keys per backend:**

```
prometheus:cb:{backend_id}:state      String  — "open" | "half-open" (absent = closed)
prometheus:cb:{backend_id}:failures   String  — consecutive failure count (int)
prometheus:cb:{backend_id}:opened_at  String  — Unix timestamp when circuit opened
```

On each backend call:
1. Read state from Redis (L1 in-process cache with 2 s TTL to avoid per-request Redis hit).
2. If `OPEN`: check `opened_at + CIRCUIT_BREAKER_RECOVERY_TIMEOUT`; if not expired → fast-fail 503.
   If expired → transition to `HALF-OPEN` (atomic `SET ... NX`).
3. If `HALF-OPEN`: allow exactly one probe (semaphore in Redis `SETNX`); others fast-fail.
4. On success: `DEL` all CB keys (→ CLOSED). Log `circuit.closed` event.
5. On transient failure: `INCR` failures; if ≥ threshold → set `state=open`, `opened_at=now`.
   Log `circuit.opened` or `circuit.failure` event.

### Retry Logic

Wraps the httpx backend call inside `BackendPool.forward()`:

```
attempt = 0
while attempt ≤ BACKEND_RETRY_MAX:
    try:
        response = await client.send(request)
        if response.status_code in {502, 503, 504}:
            raise TransientBackendError(response.status_code)
        return response
    except (TransientBackendError, httpx.ConnectError, httpx.TimeoutException):
        cb.record_failure()
        if attempt == BACKEND_RETRY_MAX raise
        wait = jitter(BACKEND_RETRY_BACKOFF_BASE_MS * 2**attempt)
        await asyncio.sleep(wait / 1000)
        attempt += 1
```

Retries are skipped for:
- Client errors (4xx responses)
- Streaming requests where the response headers have already been sent to the client
- Circuits that are `OPEN` (fast-fail immediately)

Each retry attempt is logged with `attempt`, `wait_ms`, `backend_id`, and `error`.

### Startup & Restart Resilience

**Gateway restart:**
On `startup` lifespan event the gateway:
1. Creates the Redis connection pool with `health_check_interval=15` (aioredis default) and
   `socket_keepalive=True`.
2. Reads all existing `prometheus:cb:*` keys to pre-populate the in-process CB cache.
3. Begins serving immediately — existing Redis rate limit counters are automatically honoured
   because keys are read atomically on each request.
4. No grace period or warmup flag is needed.

**Redis restart:**
The `redis.asyncio.ConnectionPool` with `health_check_interval=15` detects broken connections
and replaces them transparently on the next operation. The gateway catches `redis.RedisError`
on every Redis call, logs a `redis.reconnect.attempt` warning, and applies the configured
strictness policy (`RATE_LIMIT_STRICT`, `JWT_REVOCATION_STRICT`). No gateway restart is needed.

Redis key persistence after a Redis restart:
- If Redis is configured with `appendonly yes` (AOF), all unexpired keys — including rate
  limit counters, circuit breaker state, usage day totals, and the JWKS cache — survive.
- If Redis restarts without persistence (default dev config), counters reset to zero and
  JWKS is re-fetched on the next request. This is safe: the worst case is a short burst
  above the rate limit after restart until counters rebuild (~1 minute).
- Circuit breaker state loss on Redis restart without persistence causes all circuits to reset
  to CLOSED. This is acceptable (fail-open for CB) and documented as an operational trade-off.

### JWKS Redis Cache

Extend `fetch_jwks_keys()` to check a Redis key first:

```
Key:  prometheus:jwks:{sha256(jwks_url)[:16]}
TTL:  300 seconds (5 minutes — same as current in-process TTL)
Value: JSON-serialised JWKS keys array
```

The in-process cache remains as a local L1 (30-second TTL) to avoid Redis round-trips on
every request. Redis is L2 (5-minute TTL) shared across workers.

### max_tokens / context_length validation

In the router, before forwarding:

```python
if request.max_tokens and entry.context_length:
    if request.max_tokens > entry.context_length:
        return 400 context-exceeded
```

### Token approximation for TPM pre-check

Before forwarding, estimate input tokens as `len(all_message_text) // 4` and add
`max_tokens`. If this estimate exceeds the remaining TPM budget → 429.

After the response, the actual `usage.prompt_tokens + usage.completion_tokens` is used
to increment the TPM counter (deducting the estimate and applying the real value).

### Response headers

All `POST /v1/chat/completions` responses include:

```
X-RateLimit-Limit-Requests: 60
X-RateLimit-Remaining-Requests: 42
X-RateLimit-Reset-Requests: 1743200400
X-RateLimit-Limit-Tokens: 40000
X-RateLimit-Remaining-Tokens: 38500
X-RateLimit-Reset-Tokens: 1743200400
```

### Structured log fields (AC-8)

Add to the existing inference log entry:

```json
{
  "event": "inference.complete",
  "model": "llama3-8b-q4-local",
  "backend_url": "http://host.containers.internal:8086",
  "prompt_tokens": 60,
  "completion_tokens": 120,
  "backend_latency_ms": 4320,
  "tokens_per_second": 27.8,
  "queue_wait_ms": 12,
  "client_id": "de0abce3-...",
  "user_id": "de0abce3-..."
}
```

### Key Design Decisions

| Decision | Option Chosen | Rationale |
|----------|---------------|-----------|
| Sliding window vs token bucket | Sliding-window (minute buckets) | Simpler Redis implementation, matches OpenAI's published rate limit semantics |
| Where to place rate limit middleware | After JWT auth, before router | Requires validated `claims` for per-client/per-user scoping; must run before backend call |
| TPM pre-check vs post-charge only | Both: pre-check estimate + post-charge actual | Prevents obvious overruns while keeping accounting accurate |
| JWKS Redis cache TTL | 30 s L1 (in-process) + 300 s L2 (Redis) | Avoids per-request Redis hit; Redis coordinates across workers |
| `RATE_LIMIT_STRICT` default | `true` (fail-closed) | Consistent with `JWT_REVOCATION_STRICT` convention; safety > availability for rate limiting |
| `GET /v1/usage` time window | Current UTC day | Simplest window for billing visibility without a persistent DB |
| Token approximation ratio | 4 chars ≈ 1 token | Conservative underestimate acceptable for pre-flight check; actual tokens used for accounting |
| Rate limit scope | Per `client_id` AND per `user_id` AND per endpoint | Prevents credential abuse; allows different limits per operation type |
| Circuit breaker state storage | Redis (not in-process) | Survives gateway pod restarts; shared across uvicorn workers |
| CB in-process L1 cache TTL | 2 seconds | Avoids per-request Redis read for CB state without risking stale OPEN/CLOSED for too long |
| CB fail-open vs fail-closed | Fail-open on Redis unavailability (CB resets to CLOSED) | Rate limiting (strict) already handles overload; CB is a backend-health signal, not a security control |
| Retry scope | Transient 5xx + connection errors only | 4xx are client errors; retrying them would mask bugs and waste backend resources |
| Retry not applied to streaming | Skip retry if response headers already sent | Cannot restart a partially-consumed SSE stream without corrupting the client |
| Redis reconnection | aioredis ConnectionPool with health_check_interval=15 | Transparent reconnection without gateway restart; standard production pattern |

## API Contract

### New / modified endpoints

#### `POST /v1/chat/completions` — new response headers

All responses now include rate limit headers (see Proposed Solution above).

New error cases:

| Condition | HTTP | `type` suffix | `Retry-After` |
|-----------|------|---------------|---------------|
| RPM limit exceeded (endpoint-scoped) | 429 | `rate-limit-exceeded-requests` | seconds until next minute bucket |
| TPM limit exceeded (endpoint-scoped) | 429 | `rate-limit-exceeded-tokens` | seconds until next minute bucket |
| `max_tokens` > `context_length` | 400 | `context-exceeded` | — |
| `messages` estimated tokens > `context_length` | 400 | `context-exceeded` | — |
| Rate limit store unavailable (strict mode) | 503 | `rate-limiting-unavailable` | — |
| Backend circuit OPEN | 503 | `backend-unavailable` | seconds until circuit recovery |
| All retries exhausted (transient 5xx) | 502 | `upstream-error` | — |

Example 429 body (RFC 9457):

```json
{
  "type": "https://prometheus.internal/errors/rate-limit-exceeded-requests",
  "title": "Rate Limit Exceeded",
  "status": 429,
  "detail": "Client 'de0abce3' has exceeded the request rate limit of 60 RPM. Reset in 23 seconds.",
  "instance": "/v1/chat/completions",
  "request_id": "b3d2a1e0-...",
  "retry_after": 23
}
```

#### `GET /v1/usage` (new)

**Auth**: `admin:read` scope required.

```
GET /v1/usage
Authorization: Bearer <admin-JWT>
```

Response `200 OK`:

```json
{
  "object": "list",
  "window": "2026-03-28",
  "data": [
    {
      "client_id": "de0abce3-...",
      "client_name": "e2e-stack-test",
      "prompt_tokens": 1200,
      "completion_tokens": 4800,
      "total_tokens": 6000,
      "request_count": 42
    }
  ]
}
```

#### `GET /v1/backends` — extended (AC-10, AC-20)

Existing backend entry gains operational state fields:

```json
{
  "id": "llama3-8b-q4-local",
  "backend_url": "http://host.containers.internal:8086",
  "status": "circuit-open",
  "circuit_state": "open",
  "consecutive_failures": 7,
  "circuit_opened_at": "2026-03-29T10:00:00Z",
  "circuit_recovery_at": "2026-03-29T10:00:30Z",
  "requests_last_minute": 0
}
```

When the circuit is `CLOSED`, `circuit_state` is `"closed"` and `circuit_opened_at`,
`circuit_recovery_at` are `null`.

## Data Model

### New Redis key schema

| Key pattern | Type | TTL | Written by | Read by |
|-------------|------|-----|------------|---------|
| `prometheus:rl:rpm:{client_id}:{endpoint}:{bucket}` | String (int) | 90 s | Gateway RateLimitMiddleware | Gateway |
| `prometheus:rl:rpm:{user_id}:{endpoint}:{bucket}` | String (int) | 90 s | Gateway RateLimitMiddleware | Gateway |
| `prometheus:rl:tpm:{client_id}:{endpoint}:{bucket}` | String (int) | 90 s | Gateway RateLimitMiddleware | Gateway |
| `prometheus:rl:tpm:{user_id}:{endpoint}:{bucket}` | String (int) | 90 s | Gateway RateLimitMiddleware | Gateway |
| `prometheus:usage:day:{date}:{client_id}:prompt` | String (int) | 25 h | Gateway | GET /v1/usage |
| `prometheus:usage:day:{date}:{client_id}:completion` | String (int) | 25 h | Gateway | GET /v1/usage |
| `prometheus:usage:day:{date}:{client_id}:requests` | String (int) | 25 h | Gateway | GET /v1/usage |
| `prometheus:jwks:{url_hash}` | String (JSON) | 300 s | Gateway | Gateway workers |
| `prometheus:cb:{backend_id}:state` | String | no TTL | Gateway BackendPool | Gateway |
| `prometheus:cb:{backend_id}:failures` | String (int) | no TTL | Gateway BackendPool | Gateway |
| `prometheus:cb:{backend_id}:opened_at` | String (unix ts) | no TTL | Gateway BackendPool | Gateway |

### New / modified config (`gateway/.env`)

| Variable | Default | Description |
|----------|---------|-------------|
| `RATE_LIMIT_RPM` | `60` | Global max requests per minute per client_id / user_id |
| `RATE_LIMIT_TPM` | `40000` | Global max tokens per minute per client_id / user_id |
| `RATE_LIMIT_STRICT` | `true` | Fail-closed when Redis unavailable |
| `RATE_LIMIT_REDIS_URL` | same as `JWT_REVOCATION_REDIS_URL` | Redis URL for rate limit counters (can share the same Redis instance) |
| `RATE_LIMIT_RPM_CHAT_COMPLETIONS` | (unset → uses `RATE_LIMIT_RPM`) | Per-endpoint RPM override for `/v1/chat/completions` |
| `RATE_LIMIT_TPM_CHAT_COMPLETIONS` | (unset → uses `RATE_LIMIT_TPM`) | Per-endpoint TPM override for `/v1/chat/completions` |
| `CIRCUIT_BREAKER_FAILURE_THRESHOLD` | `5` | Consecutive failures before opening the circuit |
| `CIRCUIT_BREAKER_RECOVERY_TIMEOUT` | `30` | Seconds the circuit stays OPEN before probing |
| `CIRCUIT_BREAKER_SUCCESS_THRESHOLD` | `2` | Successful probes required to close the circuit |
| `BACKEND_RETRY_MAX` | `2` | Max retry attempts on transient backend failure |
| `BACKEND_RETRY_BACKOFF_BASE_MS` | `200` | Base backoff (ms) for exponential retry: `base × 2^attempt ± 20% jitter` |

> **Implementation note**: `RATE_LIMIT_REDIS_URL` defaults to the same value as
> `JWT_REVOCATION_REDIS_URL` to avoid requiring a second Redis connection parameter in
> simple deployments. They can point to different Redis instances if needed.

> **Redis persistence note**: production deployments should enable Redis AOF persistence
> (`appendonly yes`) to ensure rate limit counters, usage totals, and circuit breaker state
> survive a Redis restart. Without persistence, a Redis restart resets all counters (safe
> but allows a short burst above limits) and clears circuit breaker state (all circuits reset
> to CLOSED).

## Security Considerations

- Rate limit keys are keyed on validated JWT `client_id` and `user_id` claims — never on IP
  address (proxies invalidate IP-based limits) and never on request body content.
- Redis pipeline operations are atomic; no TOCTOU race on counter check-and-increment.
- `RATE_LIMIT_STRICT=true` ensures Redis unavailability cannot be used as a DoS bypass.
- `Retry-After` values are derived from server-side clock only; client-supplied values ignored.
- JWKS Redis cache stores only the public key material (already public); no private data cached.
- Usage counters (`GET /v1/usage`) are admin-scoped — clients cannot read each other's usage.
- Token approximation intentionally underestimates to avoid false positives; worst-case
  over-spend is one request's `max_tokens` beyond the TPM limit.
- Circuit breaker state stored in Redis does not contain PII or secrets — only backend URLs
  (already known to operators) and failure counts.
- Retry logic must not forward request bodies that have already been partially consumed;
  the original `httpx.Request` object is cloned before each attempt.
- The circuit breaker HALF-OPEN probe gate uses Redis `SET NX` to ensure only one probe is
  forwarded concurrently — preventing thundering-herd on circuit recovery.
- Backend `backend_id` used in Redis CB keys is a stable slug derived from `registry.yaml`
  (never from user input), preventing key injection attacks.

## Acceptance Criteria

| ID | Given | When | Then |
|----|-------|------|------|
| AC-1 | Client has sent 60 requests this minute | Client sends 61st request | Gateway returns 429 with `Retry-After` header |
| AC-2 | Client has consumed 39 900 tokens this minute, `max_tokens=200` | Client sends inference request | Gateway returns 429 (estimated tokens would exceed 40 000) |
| AC-3 | Two concurrent requests from same client | Both arrive simultaneously | Exactly one succeeds; counters are consistent (no double-decrement) |
| AC-4 | Redis is down, `RATE_LIMIT_STRICT=true` | Client sends request | Gateway returns 503 rate-limiting-unavailable |
| AC-4b | Redis is down, `RATE_LIMIT_STRICT=false` | Client sends request | Request forwarded, warning logged |
| AC-5 | Any request to `/v1/chat/completions` | Response returned | Response includes all 6 `X-RateLimit-*` headers |
| AC-6 | Model `context_length=8192`, request `max_tokens=10000` | Client sends request | Gateway returns 400 context-exceeded before contacting backend |
| AC-7 | Two uvicorn workers, JWKS not cached | First request per worker | Only one JWKS fetch to auth-service; second worker uses Redis cache |
| AC-8 | Successful non-streaming inference | Response returned | Log entry includes `tokens_per_second`, `backend_latency_ms`, `queue_wait_ms` |
| AC-8b | Successful streaming inference | Final SSE chunk sent | Same fields emitted after stream completes |
| AC-9 | User has hit RPM limit, different client same user | New request | 429 — user-level limit applies even across clients |
| AC-10 | GET /v1/backends with admin token | Response returned | Each backend entry includes `requests_last_minute` |
| AC-11 | GET /v1/usage with admin token | Response returned | Returns per-client token usage for current UTC day |
| AC-12 | Model `context_length=4096`, messages estimated at 5000 tokens | Client sends request | Gateway returns 400 context-exceeded |
| AC-13 | `RATE_LIMIT_RPM_CHAT_COMPLETIONS=30` set, global RPM=60 | Client sends 31st chat/completions request | 429 with endpoint-specific limit cited in detail |
| AC-13b | No per-endpoint override, client sends 61st request | Any endpoint | 429 using global limit |
| AC-14 | Backend returns 503 five times consecutively | 6th request arrives | Circuit opens; request fast-fails with 503 backend-unavailable + Retry-After |
| AC-14b | Circuit is OPEN, 30 s passes | Next request arrives | Circuit moves to HALF-OPEN; probe is forwarded |
| AC-14c | Probe succeeds | Next request | Circuit closes; normal forwarding resumes |
| AC-14d | Probe fails | Next request | Circuit re-opens; Retry-After reset |
| AC-15 | Circuit is OPEN | Client sends request | Response includes `circuit_recovery_at` timestamp in body |
| AC-16 | Gateway pod restarts, Redis has CB state | First request after restart | CB state loaded from Redis; open circuits remain open |
| AC-17 | Backend returns 503 on attempt 0 and 1, then 200 | Single client request | Client receives 200; gateway logged 2 retry attempts with backoff |
| AC-17b | Backend returns 503 on all 3 attempts (0, 1, 2) | Single client request | Client receives 502 upstream-error; circuit failure count incremented by 3 |
| AC-17c | Backend returns 200 with streaming headers already sent | Backend then returns 503 | No retry; error logged; stream terminates with error event |
| AC-18 | Gateway restarts, Redis has live rate limit counters | First request after restart | Existing counters honoured; client near RPM limit is still throttled |
| AC-19 | Redis is restarted while gateway is running | Client sends request after Redis recovers | Gateway reconnects automatically; no warning to client; reconnect logged |
| AC-20 | GET /v1/backends, one backend has open circuit | Response returned | Entry shows `circuit_state=open`, `consecutive_failures`, `circuit_opened_at`, `circuit_recovery_at` |

## Open Questions

_None — all questions resolved during spec authoring._
