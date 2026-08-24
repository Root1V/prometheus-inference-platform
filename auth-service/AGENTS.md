# Auth Service — Agent Navigation Rules

## What lives here

The OAuth2 authorization server — issues RS256 JWTs to registered clients.

```
auth-service/
├── src/prometheus_auth/
│   ├── main.py         # FastAPI app entry point
│   ├── asgi.py         # ASGI app factory
│   ├── config.py       # Settings (pydantic) — JWT_ISSUER, key paths, etc.
│   ├── crypto.py       # RSA key loading, JWT signing, JWKS generation
│   ├── db.py           # Client store (SQLite)
│   ├── schemas.py      # Pydantic models for request/response
│   └── routers/        # OAuth2 routes: /register, /token, /v1/jwks
├── tests/
└── Dockerfile
```

## Before starting any task here

1. Confirm whether the change touches the JWT structure (claims, issuer, audience) — if so, coordinate with `gateway/src/prometheus_gateway/auth/` which validates those claims.
2. RSA keys are loaded from paths set in `config.py` — never inline key material.
3. JWKS endpoint (`/v1/jwks`) must always reflect the current active public key.

## Before closing any task here

- [ ] `(cd auth-service && uv run pytest tests/ -v --cov=src --cov-fail-under=80)`
- [ ] `(cd auth-service && uv run ruff check src/ && uv run ruff format --check src/ && uv run mypy src/)`
- [ ] JWT claims match what the gateway validates: `iss`, `aud`, `sub`, `scope`, `exp`
- [ ] No new endpoint introduced without authentication (except `/health`, `/v1/jwks`)

## Exposed Endpoints

| Method | Path | Auth required | Purpose |
|--------|------|--------------|---------|
| `GET` | `/health` | ❌ None | Liveness probe |
| `GET` | `/.well-known/jwks.json` | ❌ None | Public JWKS — gateway fetches this to verify JWTs |
| `POST` | `/oauth2/token` | 🔑 `client_id` + `client_secret` | Issue JWT (OAuth2 client_credentials) |
| `POST` | `/admin/clients` | 🔒 Admin token | Register a new client |
| `GET` | `/admin/clients` | 🔒 Admin token | List all registered clients |
| `PATCH` | `/admin/clients/{client_id}` | 🔒 Admin token | Update client scopes / TTL |
| `DELETE` | `/admin/clients/{client_id}` | 🔒 Admin token | Deactivate a client |
| `POST` | `/admin/clients/{client_id}/rotate-secret` | 🔒 Admin token | Rotate client secret |
| `GET` | `/share/{token}` | ❌ None (token is the credential) | Credential share link — memory/specs/016 |
| `GET` | `/admin/ui/` | 🍪 Session cookie | Admin dashboard (web UI) |
| `GET/POST` | `/admin/ui/login` | ❌ None (login form) | Admin UI login |
| `GET` | `/admin/ui/logout` | 🍪 Session cookie | Admin UI logout |
| `GET` | `/admin/ui/dashboard` | 🍪 Session cookie | Client list view |
| `POST` | `/admin/ui/clients` | 🍪 Session cookie | Create client from UI |
| `GET/POST` | `/admin/ui/clients/{client_id}/edit` | 🍪 Session cookie | Edit client from UI |

**Unauthenticated endpoints** (no token required): `/health`, `/.well-known/jwks.json`, `/oauth2/token` (uses client credentials instead), `/admin/ui/login`, `/share/{token}`.

## OAuth2 Roles & Scopes

These are the canonical values defined in `src/prometheus_auth/schemas.py` and `db.py`.
Any change here requires updating `VALID_SCOPES` in `schemas.py`.

### Client Roles (`ClientRole` enum)

| Role | JWT TTL | Intended for |
|------|---------|-------------|
| `admin` | 3h | Internal tooling |
| `cognitive` | 1h | Long-running pipelines |
| `agent` | 10m | Autonomous agents |
| `app` | 5m | Interactive applications |

### Valid Scopes (`VALID_SCOPES`)

| Scope | Grants access to | Defined in |
|-------|-----------------|------------|
| `inference:read` | `POST /v1/chat/completions` | memory/specs/005 |
| `inference:stream` | SSE streaming completions | memory/specs/005 |
| `admin:read` | `GET /v1/backends`, `GET /v1/usage` (gateway admin) | memory/specs/005 |
| `admin:models` | Model management endpoints | memory/specs/005 |
| `admin:usage` | Usage report endpoints | memory/specs/005 |
| `backend-registry:read` | Manager API — `GET /v1/backends` | memory/specs/008 |
| `ui:chat` | Web Chat UI access | memory/specs/013 |
| `ops:dashboard` | Grafana ops dashboard | memory/specs/021 |

**Rule**: `VALID_SCOPES` is the single source of truth — never hardcode scope strings outside `schemas.py`. When adding a new scope, add it here first and update this table.

## Key constraints

- Always RS256 — never HS256.
- JWT validation order in gateway must match token structure issued here: signature → `exp` → `iss` → `aud` → `sub` → `scope`.
- Never log token values, client secrets, or raw JWTs.
- Clock skew allowance: max 30 seconds leeway.
- Short-lived tokens: max 1-hour TTL.

## RHEL deployment notes

- **RHEL support added**: `scripts/install-rhel.sh` and `scripts/validate.sh` provision and validate RHEL 9.7 hosts. The auth-service `.env` template was adapted for RHEL at `auth-service/.env.example`. See `memory/specs/023-redhat-compatibility.md` for implementation details and operator guidance.
