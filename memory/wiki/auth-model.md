# Authentication Model

How Prometheus authenticates clients: OAuth2 Client Credentials, RS256 JWT, client roles, scopes, and revocation.

> Sources: `memory/specs/002-jwt-authentication-middleware.md`, `memory/specs/005-auth-service.md`

---

## Overview

Prometheus uses **OAuth2 Client Credentials** — machine-to-machine only, no browser login.

```
[Client App]
    │
    │ ① POST /oauth2/token  (client_id + client_secret)
    ▼
[Auth Service :9000]  — issues RS256 JWT — private key only here
    │
    │ ② Authorization: Bearer <jwt>
    ▼
[Gateway :8000]  — validates JWT (signature, exp, iss, aud, scope)
    │
    ▼
[llama.cpp :8080]  — bare-metal, never directly reachable
```

The gateway **validates** tokens, it never issues them. The auth-service **issues** tokens, it never forwards requests.

---

## Client lifecycle

### Register a client (admin action)

```bash
curl -X POST http://auth-service:9000/admin/clients \
  -H "X-Admin-Key: $AUTH_ADMIN_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"client_name": "my-app", "role": "app", "allowed_scopes": ["inference:read"], "label": "my-team"}'
```

Response includes `client_id` and `client_secret` (shown **once only** — save immediately).  
Secret is prefixed `pmt_live_` for leak detection. Only its bcrypt hash is stored.

**Distributing the secret safely**: use the admin dashboard "Get share link" button to generate a single-use, time-limited URL (`/share/<token>`, default 1 h TTL). The URL can be sent via a secure ephemeral channel (Signal, Slack DM, 1Password Send). **Never transmit `client_secret` by email or persistent chat.** The share link is consumed once — on first view the plaintext is destroyed server-side and subsequent requests return `410 Gone`.

**Client object schema** (returned by `GET /admin/clients` and `PATCH /admin/clients/{id}`):

| Field | Type | Notes |
|-------|------|-------|
| `client_id` | string | UUID, immutable |
| `client_name` | string | Human-readable identifier |
| `label` | string \| null | Free-text tag (e.g. team, component) — nullable, not unique |
| `role` | string | `app` / `agent` / `cognitive` / `admin` |
| `allowed_scopes` | list[string] | Scopes this client may request |
| `token_ttl_seconds` | int | Override default TTL for role (60–86400) |
| `is_active` | bool | `false` after deactivation |
| `created_at` | datetime | ISO 8601 UTC |
| `updated_at` | datetime \| null | Set on every mutation via `PATCH` |

### Obtain a token

```bash
curl -X POST http://auth-service:9000/oauth2/token \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "grant_type=client_credentials&client_id=<id>&client_secret=<secret>&scope=inference:read"
```

### Use the token

```bash
curl -X POST http://gateway:8000/v1/chat/completions \
  -H "Authorization: Bearer <token>" \
  -d '{"model": "llama3-1b-local", "messages": [...]}'
```

### Token expires → re-authenticate

No refresh tokens. When `401 token-expired`, repeat the token request. Client Credentials re-auth is stateless and cheap.

### Update a client

```bash
curl -X PATCH http://auth-service:9000/admin/clients/<client_id> \
  -H "X-Admin-Key: $AUTH_ADMIN_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"label": "gateway-component", "allowed_scopes": ["inference:read"], "token_ttl_seconds": 300}'
```

Only supplied fields are updated (partial update). `allowed_scopes` is a **full replacement**, not an append.

### Deactivate (soft delete) and reactivate a client

```bash
# Soft deactivate — tokens revoked immediately; client row preserved
curl -X DELETE http://auth-service:9000/admin/clients/<client_id> \
  -H "X-Admin-Key: $AUTH_ADMIN_API_KEY"

# Reactivate
curl -X POST http://auth-service:9000/admin/clients/<client_id>/reactivate \
  -H "X-Admin-Key: $AUTH_ADMIN_API_KEY"

# Hard delete (permanent — also writes Redis revocation key for any outstanding tokens)
curl -X DELETE "http://auth-service:9000/admin/clients/<client_id>?permanent=true" \
  -H "X-Admin-Key: $AUTH_ADMIN_API_KEY"
```

Existing tokens stop working **immediately** (within one request cycle) via Redis revocation key.

### If `AUTH_ADMIN_API_KEY` is leaked

Generate a new key, update `auth-service/.env`, restart the container. Existing client credentials and tokens are **not affected** — no clients need to re-register.

```bash
openssl rand -hex 32   # new key
# update AUTH_ADMIN_API_KEY in auth-service/.env
podman compose restart auth-service
```

### Rotate client secret (rotate without creating a new client_id)

The auth service exposes an admin endpoint to rotate a client's secret in-place. The endpoint returns the new plaintext secret exactly once — save it immediately. This is implemented in the router `rotate_secret` handler and exposed to the admin UI.

- Endpoint: `POST /admin/clients/{client_id}/rotate-secret`
- Server-side implementation: [auth-service/src/prometheus_auth/routers/admin.py](auth-service/src/prometheus_auth/routers/admin.py#L130-L236)
- Admin UI form: [auth-service/src/prometheus_auth/routers/admin_ui.py](auth-service/src/prometheus_auth/routers/admin_ui.py#L615-L634)
- Test coverage: `auth-service/tests/test_auth_service.py::test_auth_AC13_rotate_secret`

Example `curl` (replace host and variables as appropriate):

```bash
# simple (prints full JSON)
curl -sS -X POST "https://auth.example.com/admin/clients/<CLIENT_ID>/rotate-secret" \
  -H "X-Admin-Key: $AUTH_ADMIN_API_KEY" \
  -H "Content-Type: application/json"

# extract new secret using jq (recommended to capture and store securely)
NEW_SECRET=$(curl -sS -X POST "https://auth.example.com/admin/clients/$CID/rotate-secret" \
  -H "X-Admin-Key: $AUTH_ADMIN_API_KEY" | jq -r '.client_secret')
echo "New secret saved to variable NEW_SECRET"
```

Notes:
- The endpoint requires the admin key header (`X-Admin-Key`).
- The returned `client_secret` is shown once — the service stores only a bcrypt hash.
- Rotating a secret does not change the `client_id`; existing tokens keep their original expiry but future tokens require the new secret.


---

## Client roles and token TTL

| Role | TTL | Use case |
|------|-----|----------|
| `admin` | 3 hours | Internal tooling, admin scripts |
| `cognitive` | 1 hour | Long-running analysis agents |
| `agent` | 10 minutes | Autonomous agents — short blast radius |
| `app` | 5 minutes | Interactive applications |

Role is embedded in the JWT as a claim. The gateway enforces that `exp - iat ≤ max_ttl_for_role` — prevents forged long-lived tokens even if the private key is compromised.

---

## Scopes

Fixed enum — clients receive only the scopes registered for them:

| Scope | Permission |
|-------|-----------|
| `inference:read` | `POST /v1/chat/completions` (non-streaming), `POST /v1/embeddings` |
| `inference:stream` | `POST /v1/chat/completions` with `stream: true` |
| `admin:read` | Gateway admin endpoints (`GET /v1/backends`, `GET /v1/usage`, `GET /admin/api/*`) |
| `admin:write` | RM-10 — mutating `/admin/api/*` calls (register/deregister/start/stop/restart via the admin dashboard) |
| `admin:models` | Model management endpoints |
| `admin:usage` | `GET /v1/usage` |
| `ui:chat` | Access to `/ui/*` web chat proxy |
| `ops:dashboard` | Grafana ops dashboard |
| `backend-registry:read` | Gateway → Manager API (internal service account, read-only) |
| `backend-registry:write` | RM-10 — Manager API register/deregister/start/stop/restart |

Requesting a scope not in `allowed_scopes` returns `400 invalid_scope`.

### Per-model scopes (RM-07)

`inference:read`/`inference:stream` grant the *ability* to call the inference endpoint;
they no longer grant access to *any specific model* by themselves. A second, additive
scope selects which models: `model:<id>`, where `<id>` matches a `model_id` in
`runtime/manager/registry.yaml` (e.g. `model:llama3-8b-q4-local`). Not part of the fixed
enum above — validated by pattern (`^model:[a-z0-9][a-z0-9_-]*$`) instead, since the set
of model ids is open-ended and lives in the manager, not in auth-service.

`POST /v1/chat/completions` requires **both**:
1. `inference:read` (or `inference:stream` if `"stream": true`), and
2. `model:<the requested model_id>`

**Deny-by-default**: a client with zero `model:*` scopes has no model access at all, even
with `inference:read`. This is a behavior change — before RM-07, `inference:read`/
`inference:stream` were documented but **never actually enforced** on this endpoint (any
valid JWT could call any model), so this closes a real gap rather than just adding
granularity.

**Migration note**: any client registered before RM-07 shipped has no `model:*` scope and
will get `403 forbidden` on every inference call until an admin grants it explicit model
access — via the auth-service admin dashboard's "Model access" field, or
`PATCH /admin/clients/{id}` with `model:<id>` entries added to `allowed_scopes`.

Grant example:
```bash
curl -X PATCH https://auth-service:9000/admin/clients/<client_id> \
  -H "X-Admin-Key: $AUTH_ADMIN_API_KEY" -H "Content-Type: application/json" \
  -d '{"allowed_scopes": ["inference:read", "model:llama3-8b-q4-local", "model:qwen-coder-7b"]}'
```

### Admin dashboard (RM-10)

The gateway admin dashboard (`/admin`, gated by `ADMIN_DASHBOARD_ENABLED`) is a static SPA
served by the gateway. Its login form collects a `client_id`/`client_secret` and POSTs them
to the gateway's own `POST /admin/api/auth/login` — the one `/admin/api/*` route that
doesn't require a Bearer token (obtaining one is the point of calling it). The gateway
proxies that request server-side to its configured `AUTH_SERVICE_TOKEN_URL`
(client_credentials grant, requesting `admin:read admin:write`, same flow as any service
account) and returns the resulting JWT to the SPA. **The SPA never calls the auth-service
directly** — a real cross-origin browser request to it is blocked by CORS (auth-service
sets no CORS headers and doesn't share the gateway's origin); routing the login through the
gateway avoids that entirely and means the SPA never needs to know the auth-service's URL.
Every other `/admin/api/*` call carries the JWT as a normal `Authorization: Bearer` header
— validated by the same `JWTAuthMiddleware` that protects every other gateway endpoint.

`/admin/api/*` proxies to each configured `MANAGER_NODES` entry's manager-api. The
gateway's own `manager_client_id`/`manager_client_secret` service account (already used by
`ManagerRegistrySync` for read-only registry polling — see
[model-registry.md](model-registry.md) RM-08 phase 2) is reused for these calls, now
requesting both `backend-registry:read` and `backend-registry:write`. **Migration note**:
if this service account was registered before RM-10, grant it `backend-registry:write` too
or dashboard mutations (register/start/stop/restart) will fail with `403` even though
read-only sync keeps working:

```bash
curl -X PATCH https://auth-service:9000/admin/clients/<gateway_service_account_id> \
  -H "X-Admin-Key: $AUTH_ADMIN_API_KEY" -H "Content-Type: application/json" \
  -d '{"allowed_scopes": ["backend-registry:read", "backend-registry:write"]}'
```

Register a human operator's dashboard client the normal way, granting `admin:read
admin:write`:
```bash
curl -X POST https://auth-service:9000/admin/clients \
  -H "X-Admin-Key: $AUTH_ADMIN_API_KEY" -H "Content-Type: application/json" \
  -d '{"name":"admin-dashboard-operator","allowed_scopes":["admin:read","admin:write"]}'
```

---

## JWT structure

```json
{
  "iss": "https://prometheus.internal/auth",
  "sub": "<client_id>",
  "aud": "prometheus-gateway",
  "iat": 1743000000,
  "exp": 1743000300,
  "jti": "<uuid-v4>",
  "scope": "inference:read model:llama3-8b-q4-local",
  "role": "app",
  "client_name": "my-app"
}
```

- `aud` is always `prometheus-gateway` — tokens are not reusable in other contexts.
- `jti` is a UUID v4 per token — used for per-token revocation if needed.

---

## Gateway validation order

The gateway validates every claim in sequence — failure at any step returns `401`:

```
① Algorithm = RS256?              → invalid-token  (rejects alg:none and HS256)
② RS256 signature valid?          → invalid-token
③ exp not in the past?            → token-expired  (30 s clock skew allowed)
④ iss matches JWT_ISSUER?         → invalid-token
⑤ aud = "prometheus-gateway"?     → invalid-token
⑥ Redis: revoked:jti:{jti}?       → token-revoked  (per-token revocation)
⑦ Redis: revoked:client:{sub}?    → token-revoked  (per-client revocation)
⑧ exp - iat ≤ max_ttl_for_role?   → invalid-token
```

Exempt paths (no auth check): `/health`, `/metrics`, `/.well-known/jwks.json`.

Token must be in `Authorization: Bearer <token>` header only — `?token=` query param is rejected.

---

## Revocation

Two revocation layers coexist:

| Type | Redis key | When used |
|------|-----------|-----------|
| Per-client | `revoked:client:<client_id>` | Client deactivated via admin API |
| Per-token | `revoked:jti:<jti>` | Individual token revoked (future use) |

Redis key TTL = client's `token_ttl_seconds` — key auto-expires when no token can still be valid.

---

## Bootstrap (first-time setup)

```bash
# 1. Generate admin key
openssl rand -hex 32   # → AUTH_ADMIN_API_KEY

# 2. Generate RS256 key pair
openssl genrsa -out keys/private_2026-q1.pem 2048
openssl rsa -in keys/private_2026-q1.pem -pubout -out keys/public_2026-q1.pem

# 3. Set in auth-service/.env (gitignored)
AUTH_ADMIN_API_KEY=<from step 1>
AUTH_PRIVATE_KEY_FILE=/run/secrets/jwt_private_key.pem
AUTH_PUBLIC_KEY_FILE=/run/secrets/jwt_public_key.pem
AUTH_ACTIVE_KID=2026-q1
AUTH_JWT_ISSUER=prometheus-victor-architecture
```

For key rotation procedure, see [key-rotation.md](key-rotation.md).

---

## Related

- `memory/specs/002-jwt-authentication-middleware.md` — gateway middleware implementation
- `memory/specs/005-auth-service.md` — auth-service design, all endpoints, AC list
- `memory/decisions/2026-03-28-rs256-jwt.md` — why RS256 over HS256
- [key-rotation.md](key-rotation.md) — rotating the signing key pair
