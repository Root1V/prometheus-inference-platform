# Gateway — Agent Navigation Rules

## What lives here

The Prometheus API Gateway — the **only** component authorised to call llama.cpp.

```
gateway/
├── api/                        # OpenAPI 3.1 contracts (source of truth for API shape)
├── src/prometheus_gateway/
│   ├── auth/                   # JWT middleware, JWKS client, claims validation
│   ├── models/                 # ModelRegistry, request/response schemas
│   ├── ui/                     # Web chat UI proxy
│   ├── main.py                 # FastAPI app, middleware registration
│   ├── router.py               # Inference proxy routes
│   ├── rate_limiter.py         # Per-user + per-client rate limiting (Redis)
│   ├── rate_limit_middleware.py
│   ├── circuit_breaker.py      # llama.cpp circuit breaker
│   └── config.py               # Settings (pydantic)
├── tests/
├── certs/                      # TLS dev certificates (gen-dev-cert.sh)
└── Dockerfile
```

## Exposed Endpoints

| Method | Path | Auth required | Scope | Purpose |
|--------|------|--------------|-------|---------|
| `GET` | `/health` | ❌ None | — | Liveness probe |
| `GET` | `/metrics` | ❌ None | — | In-process operational metrics |
| `GET` | `/v1/models` | ❌ None | — | List active models (those with a backend_url) |
| `GET` | `/v1/backends` | 🔒 Bearer JWT | `admin:read` | List all registered backends + live state |
| `GET` | `/v1/usage` | 🔒 Bearer JWT | `admin:read` | Inference usage report |
| `POST` | `/v1/chat/completions` | 🔒 Bearer JWT | `inference:read` / `inference:stream` | Proxied inference request to llama.cpp |
| `GET` | `/ui/login` | ❌ None | — | Web chat UI login form |
| `POST` | `/ui/login` | ❌ None (validates `ui:chat` scope internally) | — | Web chat UI authentication |
| `POST` | `/ui/logout` | 🍪 Session cookie | — | Web chat UI logout |

**Unauthenticated endpoints**: `/health`, `/metrics`, `/v1/models`, `/ui/login` (GET+POST).

> `/v1/models` is intentionally unauthenticated — it only lists active model IDs, no user data or inference capability.

## Middleware stack order (must be preserved)

```
request → [tracing/request-id] → [auth] → [authz] → [rate-limit] → [router] → [llama-proxy] → [metering] → response
```

Never skip or reorder these layers. When adding new middleware, identify exactly where in this chain it belongs and document the rationale.

## Before starting any task here

1. Check if the change affects a public endpoint — if so, update `gateway/api/` OpenAPI contract first.
2. Identify which middleware layers are affected — preserve the stack order:
   `[tracing] → [auth] → [authz] → [rate-limit] → [router] → [llama-proxy] → [metering]`
3. If adding a new endpoint, add `BearerAuth` security scheme and all response schemas (200, 400, 401, 403, 429, 500, 503).

## Before closing any task here

- [ ] `uv run pytest gateway/tests/ -v --cov=gateway/src --cov-fail-under=80`
- [ ] `uv run ruff check gateway/ && uv run ruff format --check gateway/`
- [ ] `uv run mypy gateway/src/`
- [ ] OpenAPI contract in `gateway/api/` matches the implementation
- [ ] No new endpoint introduced without JWT validation (except `/health`, `/metrics`)

## Key constraints

- Never hardcode `llama.cpp` URL — always use `settings.llama_cpp_url`.
- All errors follow RFC 9457 Problem Details.
- Rate limiting must be enforced per `user_id` AND `client_id` — never IP-only.
- Sanitise messages before forwarding — strip `system` role override attempts.
- Write metering records **after** sending the response — never block the response path.

## RHEL deployment notes

- **RHEL support added**: `scripts/install-rhel.sh` and `scripts/validate.sh` provision and validate RHEL 9.7 hosts for Prometheus. The gateway now includes a RHEL `.env` template at `gateway/.env.podman.example`. See `memory/specs/023-redhat-compatibility.md` for implementation details and operator guidance.
