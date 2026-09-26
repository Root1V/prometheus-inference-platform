---
id: "005"
title: "Authentication & Authorization Service"
status: closed
current-agent: ""
pipeline-log: []
created: 2026-03-28
updated: 2026-03-28
---

# 005 — Authentication & Authorization Service

## Problem Statement

The Prometheus Gateway validates JWTs (spec 002) but has no component that **issues** them.
Client applications currently have no standard way to authenticate and obtain a token: there
is no OAuth2 server, no client registry, and no key-management lifecycle. This means that
every integration test requires a manually signed token, and a production deployment would
need an external identity provider with no Prometheus-specific configuration.

Without an auth service, the platform cannot:
- Register and manage client applications (service accounts)
- Issue short-lived access tokens scoped to specific capabilities (e.g. `inference:read`)
- Enforce token TTL and revocation policies uniformly
- Run entirely air-gapped (no dependency on an external IdP)

## Goals

- [ ] Implement an OAuth2 Authorization Server supporting the **Client Credentials** grant
- [ ] Issue RS256-signed JWTs compatible with the gateway's existing validation middleware
- [ ] Assign a **role** to each client (`admin`, `app`, `agent`, `cognitive`) with a different default TTL per role
- [ ] Provide a client registry (create / rotate / revoke client credentials)
- [ ] Support **immediate revocation**: deactivating a client invalidates all its existing tokens at the gateway without waiting for `exp`
- [ ] Share the RS256 key pair with the gateway via a mounted secret (same PEM files)
- [ ] Expose a JWKS endpoint (`/.well-known/jwks.json`) so the gateway can rotate keys without restart
- [ ] Run as an independent container beside the gateway in `podman-compose.yml`
- [ ] Expose an admin API (protected by a separate admin credential) for client management

## Non-Goals

- Browser-based login flows (Authorization Code, PKCE) — this is service-to-service only
- User identity management (passwords, MFA, SSO) — clients are machine accounts
- Token refresh — client credentials flow re-issues tokens directly
- Fine-grained RBAC beyond scopes — scope list is fixed at token issuance time

## Proposed Solution

A standalone FastAPI application (`auth-service/`) that implements a minimal OAuth2 Authorization
Server. It is the **only** component that holds the RS256 private key. The gateway only ever
needs the public key.

---

## Workflow — How it all works (step by step)

> This section is for first-time users of an OAuth2 Authorization Server.
> OAuth2 Client Credentials is a machine-to-machine flow: no browser, no human login.

### Step 0 — Bootstrap (operator action, done once before the stack starts)

`AUTH_ADMIN_API_KEY` is a pre-shared secret you generate as the operator before starting the
stack. It is the equivalent of a database root password: it is not issued by the auth service
itself — it exists *before* the auth service is running, so you can administer it.

```bash
# 1. Generate a cryptographically random key (32 bytes, hex-encoded)
openssl rand -hex 32
# → e3b0c44298fc1c149afb4c8996fb92427ae41e4649b934ca495991b7852b855

# 2. Also generate the RSA key pair used to sign JWTs
openssl genrsa -out keys/private_2026-q1.pem 2048
openssl rsa -in keys/private_2026-q1.pem -pubout -out keys/public_2026-q1.pem

# 3. Set these in auth-service/.env (gitignored)
AUTH_ADMIN_API_KEY=e3b0c44298fc1c149afb4c8996fb92427ae41e4649b934ca495991b7852b855
AUTH_PRIVATE_KEY_FILE=/run/secrets/jwt_private_key.pem
AUTH_PUBLIC_KEY_FILE=/run/secrets/jwt_public_key.pem
AUTH_ACTIVE_KID=2026-q1
AUTH_JWT_ISSUER=https://prometheus.internal/auth

# 4. Set the matching variable in root .env for Podman Compose bind-mounts
JWT_PRIVATE_KEY_HOST_PATH=/absolute/path/to/keys/private_2026-q1.pem
JWT_PUBLIC_KEY_HOST_PATH=/absolute/path/to/keys/public_2026-q1.pem

# 5. Start the stack
podman compose -f podman-compose.yml up --build -d
```

Once the stack is running, use the admin key only to register the first clients (Step 1 below).
After that, clients authenticate via OAuth2 and the admin key is stored safely, not used in
day-to-day operations.

> **If `AUTH_ADMIN_API_KEY` is leaked**: generate a new one, update auth-service/.env,
> restart the auth-service container. Existing client credentials and tokens are unaffected.
> No clients need to re-register.

### Step 1 — Register a client (admin action, done once)

An admin calls the admin API to create a new *client* (= a service account that represents
one application or agent). This is equivalent to "creating a user" in a traditional auth system,
except the identity is a machine, not a person.

```bash
curl -X POST http://auth-service:9000/admin/clients \
  -H "X-Admin-Key: $AUTH_ADMIN_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "client_name": "my-rag-app",
    "role": "app",
    "allowed_scopes": ["inference:read"]
  }'
```

Response (shown **once only** — save the secret immediately):

```json
{
  "client_id": "a3f7c2d1-...",
  "client_secret": "pmt_live_xxxxxxxxxxxxxxxxxxxxxxxx",
  "role": "app",
  "allowed_scopes": ["inference:read"],
  "token_ttl_seconds": 300
}
```

The `client_secret` is never stored — only its bcrypt hash is saved. If lost, rotate it.

### Step 2 — Obtain a token (done by the application, before each call or when token expires)

The application exchanges its credentials for a short-lived JWT:

```bash
curl -X POST http://auth-service:9000/oauth2/token \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "grant_type=client_credentials"\
  -d "client_id=a3f7c2d1-..."\
  -d "client_secret=pmt_live_xxxxxxx"\
  -d "scope=inference:read"
```

Response:

```json
{
  "access_token": "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9...",
  "token_type": "bearer",
  "expires_in": 300,
  "scope": "inference:read"
}
```

### Step 3 — Call the Gateway with the token

```bash
curl -X POST http://gateway:8000/v1/chat/completions \
  -H "Authorization: Bearer eyJhbGci..." \
  -H "Content-Type: application/json" \
  -d '{"model": "llama3-1b-local", "messages": [{"role": "user", "content": "Hello"}]}'
```

The gateway validates the JWT (signature, expiry, issuer, audience) and forwards the request
to llama.cpp. The application never talks to llama.cpp directly.

### Step 4 — Token expires → request a new one

When `expires_in` seconds have passed, the token is rejected by the gateway with `401 token-expired`.
The application simply repeats Step 2 to get a fresh token. There is no refresh token in
Client Credentials — re-authentication is cheap and stateless.

### Step 5 — Revoke a client (when credentials may be compromised)

If a `client_secret` is leaked or shared:

```bash
# Immediate revocation — existing tokens stop working within seconds
curl -X DELETE http://auth-service:9000/admin/clients/a3f7c2d1-... \
  -H "X-Admin-Key: $AUTH_ADMIN_API_KEY"
```

This writes `revoked:client:<client_id>` to Redis with a TTL equal to the role's max token TTL.
The gateway's auth middleware checks this key on every request. Existing tokens are invalidated
immediately (not just prevented from renewing).

After revoking, create a new client for the same application and hand out new credentials.

---

### Architecture

```
[Client App]
    │
    │  1. POST /oauth2/token  →  gets JWT
    │  2. POST /v1/chat/completions  Authorization: Bearer <jwt>
    ▼
[Auth Service :9000] ──── Redis ────── [Gateway :8000]
    (issues tokens)    (revocation)      (validates tokens)
         │                                      │
         │  private key (secret)                │  public key only
         │  /run/secrets/jwt_private_key.pem    │  /run/secrets/jwt_public_key.pem
         │                                      │
         └── GET /.well-known/jwks.json  ←──────┘  (key rotation)

[llama.cpp :8080]  ← bare-metal, 127.0.0.1 only (gateway caller only)
```

---

### Client Roles and Token TTL

Each client is assigned a **role** at registration time. The role determines the default
token TTL. TTLs can also be overridden per-client.

| Role | Default TTL | Use case |
|------|-------------|----------|
| `admin` | **3 hours** (10800 s) | Internal tooling, admin scripts — long sessions acceptable |
| `cognitive` | **1 hour** (3600 s) | Analysis agents that run long pipelines |
| `agent` | **10 minutes** (600 s) | Autonomous agents — short window limits blast radius |
| `app` | **5 minutes** (300 s) | Interactive applications — token rotated frequently |

The `role` is embedded in the JWT as a claim. The gateway can enforce additional
authorization logic based on role (e.g. only `admin` can call model-management endpoints).

### Key Design Decisions

| Decision | Rationale |
|----------|-----------|
| Standalone container, not embedded in gateway | Separation of concerns: issuing ≠ validating. Allows independent scaling and key rotation without restarting the gateway. |
| RS256 with shared key pair via Podman secret | Gateway already uses RS256. Private key stays in the auth service only; gateway receives public key via bind-mount (`/run/secrets/jwt_public_key.pem`). |
| Client credentials stored hashed (bcrypt) | `client_secret` is never stored in plaintext. Only the hash is persisted. |
| **Role-based TTL instead of a global TTL** | Blast radius of a leaked token is bounded by the role's sensitivity. An `app` token valid for 5 min is far less dangerous than one valid for 1 h. |
| **Immediate revocation via Redis** | Soft-deleting in the DB prevents new tokens but leaves existing ones valid until `exp`. Writing `revoked:client:<id>` to Redis makes the gateway invalidate all tokens for that client within one request cycle — no waiting. |
| SQLite for client registry (dev) / Postgres (prod) | Zero-dependency local dev; upgradeable to Postgres via SQLAlchemy without code changes. |
| `aud` claim fixed to `prometheus-gateway` | Tokens issued by this service are only valid for the gateway. Prevents token reuse in other contexts. |
| Admin API protected by `X-Admin-Key` header | Separate credential from OAuth2 clients. Passed via env var / Podman secret. Never exposed in tokens. |
| JWKS endpoint unauthenticated | Standard per RFC 7517. Only contains the public key — no secrets exposed. |
| `client_secret` prefixed with `pmt_live_` | Makes secrets identifiable in logs/config and easier to detect leaks with secret scanners. |

### Token Structure

```json
{
  "iss": "https://prometheus.internal/auth",
  "sub": "<client_id>",
  "aud": "prometheus-gateway",
  "iat": 1743000000,
  "exp": 1743000300,
  "jti": "<uuid-v4>",
  "scope": "inference:read",
  "role": "app",
  "client_name": "my-rag-app"
}
```

`exp - iat` always equals the role's TTL (or the per-client override). The gateway rejects
tokens where `exp` exceeds `iat + max_ttl_for_role` to prevent forged long-lived tokens.

### Client Registry Schema

```python
class ClientRole(str, Enum):
    admin     = "admin"      # TTL: 3h
    cognitive = "cognitive"  # TTL: 1h
    agent     = "agent"      # TTL: 10min
    app       = "app"        # TTL: 5min

@dataclass
class OAuthClient:
    client_id: str              # UUID v4, generated at registration
    client_name: str            # human-readable label
    client_secret_hash: str     # bcrypt cost-12 hash
    role: ClientRole            # determines default TTL and gateway authz
    allowed_scopes: list[str]   # fixed subset of platform Scope constants
    token_ttl_seconds: int      # role default, overridable per client
    created_at: datetime
    is_active: bool             # False = soft-deleted, cannot issue tokens
    revoked_at: datetime | None # set when deactivated, used for Redis TTL calc
```

### Revocation Flow (immediate)

```
Admin calls DELETE /admin/clients/{client_id}
    │
    ├─ 1. Set is_active=False, revoked_at=now() in DB
    │
    └─ 2. Write to Redis:
           SET revoked:client:<client_id> 1
           EX <role_ttl_seconds>        ← key auto-expires when no token can be valid

Next request to Gateway with a token where sub=<client_id>
    │
    ├─ JWT signature valid, exp in future  ← would normally pass
    │
    └─ Check Redis: GET revoked:client:<sub>
           └─ key exists → 401 token-revoked
```

Note: spec 002 (gateway JWT middleware) must be updated to check `revoked:client:{sub}`
in addition to the existing `revoked:jti:{jti}` check.

## API Contract

OpenAPI contract: `auth-service/api/005-auth-service.yaml` *(to be created during implementation)*

### Endpoints summary

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `POST` | `/oauth2/token` | Client credentials | Issue access token |
| `GET` | `/.well-known/jwks.json` | None | Public key for JWT verification |
| `GET` | `/health` | None | Liveness probe |
| `POST` | `/admin/clients` | `X-Admin-Key` | Register new client |
| `GET` | `/admin/clients` | `X-Admin-Key` | List all clients |
| `DELETE` | `/admin/clients/{client_id}` | `X-Admin-Key` | Deactivate client |
| `POST` | `/admin/clients/{client_id}/rotate-secret` | `X-Admin-Key` | Rotate client secret |

### `POST /oauth2/token` — Request

```
Content-Type: application/x-www-form-urlencoded

grant_type=client_credentials
&client_id=<uuid>
&client_secret=<secret>
&scope=inference:read inference:stream
```

### `POST /oauth2/token` — Response `200`

```json
{
  "access_token": "<jwt>",
  "token_type": "bearer",
  "expires_in": 3600,
  "scope": "inference:read inference:stream"
}
```

### Error responses (RFC 6749 §5.2 + RFC 9457)

```json
{ "error": "invalid_client", "error_description": "..." }
{ "error": "invalid_scope",  "error_description": "..." }
{ "error": "unauthorized_client", "error_description": "..." }
```

## Data Model

### `oauth_clients` table

| Column | Type | Notes |
|--------|------|-------|
| `client_id` | `VARCHAR(36)` PK | UUID v4 |
| `client_name` | `VARCHAR(255)` | Human label |
| `client_secret_hash` | `VARCHAR(60)` | bcrypt, cost 12 |
| `role` | `VARCHAR(20)` | `admin` \| `cognitive` \| `agent` \| `app` |
| `allowed_scopes` | `TEXT` | Space-separated scope string |
| `token_ttl_seconds` | `INTEGER` | Defaults to role TTL; overridable |
| `created_at` | `TIMESTAMP` | UTC |
| `is_active` | `BOOLEAN` | False = cannot issue tokens |
| `revoked_at` | `TIMESTAMP` nullable | Set on deactivation; used to compute Redis key TTL |

## Security Considerations

- **Private key never leaves the auth-service container.** It is bind-mounted via Podman secret
  onto the auth-service only. The gateway receives only the public key.
- **`client_secret` stored as bcrypt hash** (cost 12). Plain-text is shown exactly once at
  registration and never persisted.
- **`client_secret` prefixed with `pmt_live_`** — makes it identifiable in config leaks and
  detectable by secret scanners (e.g. GitHub secret scanning).
- **Admin API key** (`AUTH_ADMIN_API_KEY`) must be set via env var / Podman secret.
  Requests without a valid key return `403`. Never logged.
- **Token `jti` (JWT ID)**: each token gets a UUID v4. Embedded in the token for auditability.
- **Immediate revocation via Redis**: deactivating a client writes `revoked:client:<id>` to
  Redis. Gateway checks this key on every request — no waiting for `exp`. The gateway's
  auth middleware (spec 002) must be updated to check `revoked:client:{sub}`.
- **`exp` cap enforcement**: the gateway rejects tokens where `exp - iat > max_ttl_for_role`.
  This prevents a compromised private key from issuing arbitrarily long-lived tokens.
- **Role-based TTL limits blast radius**: `app` tokens last 5 min — even if leaked, the
  window of exploitation is minimal.
- **Scope validation**: requested scopes are intersected against `allowed_scopes` for the
  client. Requesting an unauthorized scope returns `invalid_scope`.
- **No token logging**: `access_token` values are never written to logs at any level.
- **HTTPS in production**: service should be placed behind a TLS-terminating proxy.
  In local dev, HTTP on the internal Podman network is acceptable.
- **Rate limiting on `/oauth2/token`**: brute-force protection — max 10 req/min per IP.
- OWASP API2 (Broken Authentication): full bcrypt comparison, constant-time to prevent timing attacks.
- OWASP API8 (Security Misconfiguration): no debug endpoints; `AUTH_ADMIN_API_KEY` required at startup.

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `AUTH_PRIVATE_KEY_FILE` | Yes | Path to RS256 private key PEM (Podman secret, auth-service only) |
| `AUTH_PUBLIC_KEY_FILE` | Yes | Path to RS256 public key PEM (Podman secret, shared with gateway) |
| `AUTH_JWT_ISSUER` | Yes | `iss` claim value (e.g. `https://prometheus.internal/auth`) |
| `AUTH_ADMIN_API_KEY` | Yes | Secret for `/admin/*` endpoints (Podman secret) |
| `AUTH_DB_URL` | No | SQLAlchemy DB URL (default: `sqlite:///./auth.db`) |
| `AUTH_ACTIVE_KID` | No | `kid` tag of the signing key (default: filename without extension of `AUTH_PRIVATE_KEY_FILE`) |
| `AUTH_REVOCATION_REDIS_URL` | No | Redis URL for immediate client revocation (default: same as gateway Redis) |
| `AUTH_RATE_LIMIT_RPM` | No | Token endpoint rate limit per IP (default: `10`) |
| `AUTH_TTL_ADMIN_SECONDS` | No | Token TTL for `admin` role (default: `10800` — 3 h) |
| `AUTH_TTL_COGNITIVE_SECONDS` | No | Token TTL for `cognitive` role (default: `3600` — 1 h) |
| `AUTH_TTL_AGENT_SECONDS` | No | Token TTL for `agent` role (default: `600` — 10 min) |
| `AUTH_TTL_APP_SECONDS` | No | Token TTL for `app` role (default: `300` — 5 min) |
| `LOG_LEVEL` | No | `INFO` |

## Acceptance Criteria

### Token issuance

- [ ] **AC-1**: Given a registered client, when `POST /oauth2/token` is called with valid
  `client_id`, `client_secret`, and `grant_type=client_credentials`, then the response is
  `200` with a valid RS256 JWT containing `sub`, `aud=prometheus-gateway`, `scope`, `role`,
  `exp`, `jti`, and `client_name`.

- [ ] **AC-2**: Given a valid token issued by the auth service, when the gateway validates it,
  then the request is forwarded to llama.cpp (end-to-end integration test).

- [ ] **AC-3**: Given an invalid `client_secret`, when `POST /oauth2/token` is called,
  then the response is `401` with `error: invalid_client`.

- [ ] **AC-4**: Given a scope not in the client's `allowed_scopes`, when `POST /oauth2/token`
  is called requesting that scope, then the response is `400` with `error: invalid_scope`.

- [ ] **AC-5**: Given a deactivated client (`is_active=false`), when `POST /oauth2/token`
  is called, then the response is `401` with `error: unauthorized_client`.

### Role-based TTL

- [ ] **AC-6**: Given a client with `role=admin`, when a token is issued, then `exp - iat = 10800` (3 h).

- [ ] **AC-7**: Given a client with `role=cognitive`, when a token is issued, then `exp - iat = 3600` (1 h).

- [ ] **AC-8**: Given a client with `role=agent`, when a token is issued, then `exp - iat = 600` (10 min).

- [ ] **AC-9**: Given a client with `role=app`, when a token is issued, then `exp - iat = 300` (5 min).

- [ ] **AC-10**: Given a token where `exp - iat` exceeds the maximum TTL for its `role` claim,
  when the gateway validates it, then the request is rejected with `401 invalid-token`
  (prevents forged long-lived tokens even if the private key is compromised).

### Client management

- [ ] **AC-11**: Given a valid admin key, when `POST /admin/clients` is called with `client_name`,
  `role`, and `allowed_scopes`, then a new client is created and the response includes
  `client_id`, `client_secret` (prefixed `pmt_live_`, shown once only), `role`, and `token_ttl_seconds`.

- [ ] **AC-12**: Given a valid admin key, when `GET /admin/clients` is called,
  then the response lists all clients with `client_id`, `client_name`, `role`,
  `allowed_scopes`, `is_active`, and `created_at` — never `client_secret_hash`.

- [ ] **AC-13**: Given a valid admin key, when `POST /admin/clients/{client_id}/rotate-secret`
  is called, then a new `client_secret` is generated and returned (shown once only).
  The old secret is immediately invalidated.

- [ ] **AC-14**: Given an invalid or missing admin key, when any `/admin/*` endpoint is called,
  then the response is `403`.

### Immediate revocation

- [ ] **AC-15**: Given a valid admin key, when `DELETE /admin/clients/{client_id}` is called,
  then: (a) `is_active=false` in DB, (b) `revoked:client:<client_id>` is written to Redis
  with TTL equal to the client's `token_ttl_seconds`.

- [ ] **AC-16**: Given a token that was valid at issuance, when the issuing client has been
  deactivated and `revoked:client:<client_id>` exists in Redis, then the gateway rejects
  the token with `401 token-revoked` — even if the token's `exp` is still in the future.

### Infrastructure

- [ ] **AC-17**: Given any request, when `GET /.well-known/jwks.json` is called,
  then the response contains the RS256 public key in JWK format with `kid`, `kty`, `use`, `n`, `e`.

- [ ] **AC-18**: Given more than 10 token requests per minute from the same IP, when the
  11th request arrives, then the response is `429`.

- [ ] **AC-19**: Given the auth service is started with `AUTH_ADMIN_API_KEY` unset,
  then startup fails with a clear error message referencing the missing variable.

- [ ] **AC-20**: Given a token exchange occurs, the `client_secret` must not appear in any
  log output at any log level.

- [ ] **AC-21**: Given `GET /health` is called, then the response is `200 {"status":"ok"}`
  with no authentication required.

## Open Questions

- [x] **Q1**: ~~Should the auth service support token revocation?~~ **Resolved**: Yes, included
  in this spec. Immediate revocation via `revoked:client:<id>` in Redis (AC-15, AC-16).
  The gateway middleware (spec 002) must also be updated to check this key.

- [x] **Q2**: Key rotation workflow — **Resolved**: use JWKS multi-key with `kid` tags.
  The gateway must be configured with `JWT_JWKS_URL=https://auth-service:9000/.well-known/jwks.json`
  (HTTPS — auth-service serves TLS since spec-017) and `AUTH_SERVICE_TLS_VERIFY=false` when using
  a self-signed development certificate.
  (not a static `JWT_PUBLIC_KEY_FILE`) so it fetches the current key set dynamically and zero-downtime
  rotation is achieved automatically.

  **How the JWKS multi-key rotation works:**

  The JWKS endpoint always returns all active public keys as an array. Each key has a unique `kid`
  (key ID). When the auth service issues a token, it includes the `kid` of the signing key in the
  JWT header. The gateway looks up the matching public key from the JWKS array — no configuration
  change or restart required.

  **Rotation procedure** (runbook to be added to `memory/wiki/key-rotation.md` at implementation time):

  ```
  Step 1 — Generate new key pair (while the old one is still active)
      openssl genrsa -out keys/private_YYYY-QN.pem 2048
      openssl rsa -in keys/private_YYYY-QN.pem -pubout -out keys/public_YYYY-QN.pem

  Step 2 — Add new key to auth service (no downtime)
      Set AUTH_ACTIVE_KID=YYYY-QN in auth service env and restart it.
      → auth-service now issues new tokens signed with new kid.
      → JWKS endpoint serves both old and new public keys.
      → Gateway validates old tokens with old key, new tokens with new key.
      Both work simultaneously.

  Step 3 — Wait for the transition window to pass
      Wait max(token_ttl) = 3 hours (admin role TTL).
      After this, all tokens signed with the old key have expired.

  Step 4 — Remove the old key
      Delete the old public key from the JWKS key store.
      Delete the old private key file.
      Restart auth-service to reload key set.
      → JWKS now only serves the new key.

  Step 5 — Verify
      curl http://auth-service:9000/.well-known/jwks.json | jq '.keys | length'
      # should return 1 (only the new key)
  ```

  **Implication for the gateway configuration**: `JWT_JWKS_URL` must be preferred over
  `JWT_PUBLIC_KEY_FILE` in production. The gateway's JWKS client (spec 002) already handles
  this. The `gateway/.env` for production deployments must set `JWT_JWKS_URL`, not
  `JWT_PUBLIC_KEY_FILE`.

- [x] **Q3**: ~~Should `allowed_scopes` be a fixed enum or arbitrary strings?~~ **Resolved**:
  Fixed enum aligned with `Scope` constants in spec 002:
  `inference:read`, `inference:stream`, `admin:models`, `admin:usage`.
  The admin API validates that `allowed_scopes` is a subset of this enum at client creation time.

> All open questions are resolved. Status can be moved to `review`.

## References

- Related specs: `memory/specs/002-jwt-authentication-middleware.md`
- OAuth2 Client Credentials: RFC 6749 §4.4
- JWT: RFC 7519
- JWK: RFC 7517
- Problem Details: RFC 9457
- OWASP API Security Top 10: https://owasp.org/API-Security/
