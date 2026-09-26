---
id: "013"
title: "Web Chat UI — Browser Authentication & Reverse Proxy"
status: closed
current-agent: ""
pipeline-log: []
created: 2026-04-06
updated: 2026-04-06
---

# 013 — Web Chat UI — Browser Authentication & Reverse Proxy

## Problem Statement

The llama-server exposes a built-in web chat UI accessible at its root (`/`).
Currently this interface is only reachable on the bare-metal host itself and has
**zero authentication** — anyone with network access to port 8080 can use the model
indefinitely.

The security constraint that defines this platform states:
> "llama.cpp is NEVER exposed outside 127.0.0.1. The Gateway is the single authorised caller."

There is currently no way to give a human operator or authorised user access to the
chat UI through a browser without violating that constraint. Operators who want to
experiment with a deployed model interactively have no secure, network-accessible path
to do so.

## Goals

- [ ] Proxy the llama-server built-in web chat UI through the gateway under the `/ui/<model_id>/` path prefix
- [ ] Gate all `/ui/*` requests behind a browser session authenticated via the auth-service
- [ ] Provide a gateway-served HTML login page (`GET /ui/login`) that collects OAuth2 `client_id`, `client_secret`, and a model selector
- [ ] Populate the model selector from the manager's `GET /v1/backends` endpoint, which returns only models with `discovery: true` in the registry (spec 008 / spec 010)
- [ ] On successful login, exchange credentials for a JWT via the auth-service OAuth2 `client_credentials` grant and redirect to the selected model's proxy path
- [ ] Store the resulting JWT in an HTTP-only, Secure, SameSite=Lax session cookie
- [ ] Validate the session cookie on every `/ui/*` request; redirect to `/ui/login` on invalid or missing cookie
- [ ] Provide a `POST /ui/logout` endpoint that clears the session cookie
- [ ] Introduce a dedicated scope `ui:chat` in the auth-service that is required for UI access
- [ ] Protect the login endpoint with a source-IP rate limiter to prevent credential stuffing
- [ ] Make the feature toggleable with a `UI_ENABLED` flag (default `false`)
- [ ] Terminate TLS at the gateway using operator-supplied certificate files (`GATEWAY_TLS_CERT_FILE`, `GATEWAY_TLS_KEY_FILE`)
- [ ] Provide a dev helper script to generate a self-signed certificate for local use

## Non-Goals

- User account management (passwords, MFA, SSO) — clients are still machine accounts from spec 005
- OAuth2 Authorization Code / PKCE browser flow — deferred to a future spec
- Persisting or exporting chat history across sessions — frontend-only concern, out of scope
- WebSocket proxying — confirmed not needed; llama-server UI uses SSE exclusively (see resolved Q1)
- Mobile native apps
- TLS certificate issuance / ACME / Let's Encrypt — operators provide their own cert files; dev cert generation is in scope

## Proposed Solution

The gateway adds a `/ui/` sub-application (a FastAPI `APIRouter` or mounted sub-app) that:

1. **Login page** (`GET /ui/login`): returns a minimal, self-contained HTML form (no external JS
   dependencies) with `client_id`, `client_secret`, and a `<select name="model_id">` combobox.
   The combobox is populated server-side by calling the manager's `GET /v1/backends` endpoint
   (at `MANAGER_URL`) and including only the entries returned — those are guaranteed to have
   `discovery: true` and a `backend_url`. If `MANAGER_URL` is not configured, the gateway
   falls back to its own synced registry and filters entries where `discovery: true`. A hidden
   `next` field holds the originally requested path for post-login redirect.

2. **Login handler** (`POST /ui/login`): receives `client_id`, `client_secret`, `model_id`, and
   `next` from the form body. Validates that `model_id` names a registry entry with
   `discovery: true` (using the same source as the login page). POSTs
   `grant_type=client_credentials&scope=ui:chat` to the auth-service token
   endpoint. On success, sets `Set-Cookie: prometheus_session=<jwt>; HttpOnly; Secure;
   SameSite=Lax` and issues a `302` redirect to `/ui/<model_id>/`. On failure, re-renders the
   login form with an error message (no stack traces exposed to the browser).

3. **UI auth guard** (dependency or middleware scoped to `/ui/*`): reads the
   `prometheus_session` cookie, validates the JWT using the same RS256 key material and
   validation logic already used by `JWTAuthMiddleware` (spec 002). Checks for the `ui:chat`
   scope. On failure redirects to `/ui/login?next=<encoded-path>` instead of returning a 401
   (browser users cannot present `Authorization:` headers).

4. **Reverse proxy handler** (`GET`, `POST`, `PUT`, `DELETE`, `OPTIONS` on
   `/ui/{model_id}/{path:path}`): looks up `model_id`; returns `404` if unknown or if the
   model has `discovery: false` (not publicly visible). Strips the `/ui/<model_id>` prefix
   and forwards the
   request to `<model.backend_url>/{path}` via the existing `httpx.AsyncClient` pool. Request
   body and query parameters are forwarded unchanged. Streaming responses (SSE — confirmed as
   the only streaming mechanism used by the llama-server UI; it uses `fetch` with `stream: true`
   against the `/completion` endpoint, returning `text/event-stream`) are forwarded with
   `StreamingResponse`. **Sensitive headers are stripped before forwarding** (see Security
   Considerations).

5. **Logout** (`POST /ui/logout`): sets `Set-Cookie: prometheus_session=; Max-Age=0` and
   redirects the browser to `/ui/login`.

6. **TLS termination**: when `GATEWAY_TLS_CERT_FILE` and `GATEWAY_TLS_KEY_FILE` are set, the
   gateway's uvicorn process is started with `ssl_certfile` and `ssl_keyfile`. All traffic is
   HTTPS. A dev helper script `gateway/certs/gen-dev-cert.sh` generates a self-signed cert
   (10-year validity, `localhost` + `127.0.0.1` SANs) using `openssl` for local development.
   If `UI_ENABLED=true` and TLS environment variables are not set, the gateway logs a warning
   at startup; the `Secure` cookie flag is still set and browsers will reject the cookie
   over plain HTTP — operators must configure TLS for the UI to work end-to-end.

### Request Flow

```
Browser  GET /ui/llama3-8b-q4-local/
           │
           ├─ prometheus_session cookie present & JWT valid with ui:chat scope?
           │     └─ YES ──► strip /ui/<model_id> ──► reverse-proxy to model.backend_url /
           │
           └─ NO ──► 302 /ui/login?next=%2Fui%2Fllama3-8b-q4-local%2F
                           │
               GET /ui/login — serve HTML form
                             (model combobox populated from registry,
                              only models with backend_url shown)
                           │
               User selects model + enters client_id + client_secret
                           │
               POST /ui/login  (form body: client_id, client_secret, model_id, next)
                           │
               Validate model_id exists in registry with backend_url
                           │
               Gateway  POST auth-service /oauth2/token
               (grant_type=client_credentials, scope=ui:chat)
                           │
                ┌──────────┴──────────┐
              401 / network err      200 + JWT
                │                       │
           re-render form           validate scope
           with error msg           set prometheus_session cookie (HttpOnly; Secure; Lax)
                                    302 → /ui/<model_id>/
```

### Key Design Decisions

| Decision | Rationale |
|----------|-----------|
| Cookie value = raw JWT | Reuses existing JWT validation infrastructure (spec 002); eliminates a server-side session table and Redis session dependency |
| Dedicated `ui:chat` scope | Decouples UI credentials from API credentials; an API service-account JWT cannot silently gain chat UI access |
| `/ui/<model_id>/` path prefix carries the model target | Stateless routing — no separate session cookie needed for model selection; the URL itself is the routing key; shareable links work |
| Login combobox filtered to `discovery: true` models only | Reuses the platform-standard visibility flag (spec 010 / spec 008); models with `discovery: false` are intentionally hidden from external consumers; a `discovery: true` entry in the manager registry always has a `backend_url` |
| `/ui/` prefix isolation | Clean boundary from REST API paths; easy to rate-limit, disable, or move independently |
| Redirect on auth failure (not 401) | Browser clients cannot act on a `WWW-Authenticate` challenge — a redirect to the login page is the correct UX |
| Plain HTML login form | Zero frontend build dependencies; avoids HTMX or React complexity for a gateway-layer concern |
| `next` param sanitisation | Prevents open-redirect by allowing only relative paths under `/ui/` |
| No CSRF token on login form | The form only submits credentials; the resulting cookie is HTTP-only (XSS cannot read it) and SameSite=Lax (cross-site POST is blocked by the browser) |
| Strip `Cookie` before forwarding | Prevents the session cookie from leaking into llama-server's request logs |
| TLS at uvicorn level, not reverse-proxy | Keeps TLS termination inside the container, reducing the need for an external proxy (Nginx/Traefik) for single-node deployments |
| Dev cert via `gen-dev-cert.sh` | Enables HTTPS in local dev without a trusted CA; `Secure` cookies work in the browser with self-signed certs if the cert is added to the system trust store |

## API Contract

> No OpenAPI file required — these are browser-facing HTML/redirect endpoints, not JSON API endpoints.

| Method | Path | Auth | Response |
|--------|------|------|----------|
| `GET` | `/ui/login` | None | `200 text/html` — login form with model combobox |
| `POST` | `/ui/login` | None (credentials in body) | `302` to `/ui/<model_id>/` + `Set-Cookie`, or `200 text/html` form with error |
| `POST` | `/ui/logout` | Session cookie | `302 /ui/login` + clears cookie |
| `GET … DELETE` | `/ui/{model_id}/{path:path}` | Session cookie | Proxied response from `model.backend_url/{path}` |

### New Auth-Service Scope

| Scope | Purpose |
|-------|---------|
| `ui:chat` | Grants browser-session access to the proxied llama-server chat UI |

This scope must be registrable via the auth-service admin API (spec 005) and must be
assignable to any client. It must NOT be implied by any other existing scope.

## Data Model

No new persistent data. The entire session state is encoded in the JWT stored in the cookie.

### New Gateway Environment Variables

| Variable | Default | Required | Description |
|----------|---------|----------|-------------|
| `UI_ENABLED` | `false` | No | Feature flag — when `false` all `/ui/*` routes return `404` |
| `UI_SESSION_COOKIE_NAME` | `prometheus_session` | No | Name of the HTTP-only session cookie |
| `UI_SESSION_COOKIE_MAX_AGE` | `0` | No | Override cookie lifetime (seconds); `0` = follow JWT `exp` |
| `UI_LOGIN_RATE_LIMIT_RPM` | `10` | No | Max login attempts per source IP per minute |
| `AUTH_SERVICE_TOKEN_URL` | _(required when UI_ENABLED=true)_ | Conditional | Full URL of auth-service token endpoint, e.g. `https://auth-service:9000/oauth2/token` (use `https://` in Podman stack — auth-service serves TLS since spec-017) |
| `AUTH_SERVICE_TLS_VERIFY` | `true` | Optional | Set to `false` when using self-signed certificates (Podman dev stack). Passed to `httpx.AsyncClient(verify=...)`. |
| `GATEWAY_TLS_CERT_FILE` | `None` | Conditional | Path to PEM certificate file for TLS termination (required for production; enables HTTPS) |
| `GATEWAY_TLS_KEY_FILE` | `None` | Conditional | Path to PEM private key file for TLS termination |

> `AUTH_SERVICE_TOKEN_URL` re-uses or extends the pattern established in podman-compose.yml
> where the gateway already reaches the auth-service for JWKS (`JWT_JWKS_URL`).

> When `GATEWAY_TLS_CERT_FILE` and `GATEWAY_TLS_KEY_FILE` are both set, the gateway starts
> uvicorn with TLS. When only one is set, the gateway refuses to start (configuration error).
> The `Secure` cookie attribute is always set on `prometheus_session` regardless of TLS — this
> ensures a misconfigured HTTP deployment is immediately visible (cookies are rejected by the
> browser) rather than silently insecure.

### Dev TLS Helper

`gateway/certs/gen-dev-cert.sh` — generates `gateway/certs/dev.crt` and `gateway/certs/dev.key`
using `openssl req` with a 10-year validity, subject `CN=localhost`, and SANs
`DNS:localhost,IP:127.0.0.1`. These files are gitignored. Operators add `dev.crt` to their
browser/OS trust store once for the self-signed cert to be accepted.

## Security Considerations

- **TLS at the gateway**: `GATEWAY_TLS_CERT_FILE` + `GATEWAY_TLS_KEY_FILE` enable uvicorn TLS
  termination. Without TLS the `Secure` cookie flag causes the browser to silently drop the
  session cookie, making authentication impossible — this is intentional fail-safe behaviour.
  The dev cert script (`gateway/certs/gen-dev-cert.sh`) enables HTTPS locally without
  external tooling.
- **Cookie flags**: HTTP-only (no JS access), `Secure` (always set — requires HTTPS;
  see TLS section above), `SameSite=Lax` (blocks cross-site POST, allows same-site navigation).
- **Session lifetime**: cookie lifetime equals JWT `exp` issued by auth-service; no server-side session
  table or revocation needed for the cookie itself — existing JWT revocation (spec 007) applies.
- **Credential transmission**: `client_secret` travels in the POST body over TLS — never in a query
  string, URL path, or log line.
- **Open-redirect prevention**: the `next` parameter is validated to be a relative path starting with
  `/ui/`; any other value is silently replaced with `/ui/`.
- **Scope enforcement**: the `ui:chat` scope is checked after JWT signature and expiry validation;
  a valid, non-expired JWT without the scope is rejected and redirects to the login page with an
  "access not granted" message.
- **Header stripping before forwarding to llama-server**:
  - `Cookie` — session cookie must not reach llama-server logs
  - `Authorization` — avoid leaking any bearer token
  - `X-Forwarded-For` — do not expose internal IP topology
  - `Host` — replaced with the configured backend host
- **Credential stuffing defence**: login endpoint is rate-limited to `UI_LOGIN_RATE_LIMIT_RPM`
  requests per source IP per minute; returns `429 Too Many Requests` on excess.
- **Auth-service reachability**: if `AUTH_SERVICE_TOKEN_URL` is unreachable, the login handler
  returns the form with a generic "service unavailable, try again later" message — no internal
  error details exposed.
- **No token in URL**: the JWT is set only via `Set-Cookie`; it is never placed in a redirect URL,
  query parameter, or `Location` header.
- **OWASP A01 / A03**: the login form performs no database queries in the gateway layer;
  all credential validation is delegated to the auth-service via its OAuth2 token endpoint.

## Acceptance Criteria

- [ ] **AC-1**: Given `UI_ENABLED=false` (or unset), when any request arrives for `/ui/login`, `/ui/logout`, or `/ui/some/path`, then the gateway returns `404 Not Found`.

- [ ] **AC-2**: Given `UI_ENABLED=true` and no `prometheus_session` cookie, when a browser sends `GET /ui/llama3-8b-q4-local/`, then the response is `302` redirect to `/ui/login?next=%2Fui%2Fllama3-8b-q4-local%2F`.

- [ ] **AC-3**: Given `UI_ENABLED=true`, when a browser sends `GET /ui/login`, then the response is `200 text/html` containing a `<form>` with `name="client_id"`, `name="client_secret"`, a `<select name="model_id">` listing only models returned by `GET /v1/backends` (i.e., those with `discovery: true`), and a hidden `name="next"` field.

- [ ] **AC-4**: Given a `POST /ui/login` with valid `client_id`, `client_secret`, and a `model_id` whose registry entry has `discovery: true`, and the auth-service returns a valid JWT with `ui:chat` scope, then the response is `302` to `/ui/<model_id>/` with a `Set-Cookie` header setting `prometheus_session` as `HttpOnly; Secure; SameSite=Lax`.

- [ ] **AC-5**: Given a `POST /ui/login` with invalid credentials, when the auth-service returns `401`, then the gateway returns `200 text/html` with the login form re-rendered and a visible human-readable error message (no stack trace, no internal URL disclosed).

- [ ] **AC-6**: Given a `POST /ui/login` where the auth-service returns a JWT that does not contain the `ui:chat` scope, then the gateway returns `200 text/html` with the login form and an error message indicating the client does not have UI access.

- [ ] **AC-7**: Given a valid `prometheus_session` cookie with a JWT that includes the `ui:chat` scope, when `GET /ui/llama3-8b-q4-local/index.html` is requested, then the gateway looks up `llama3-8b-q4-local` in the registry, forwards the request to `<model.backend_url>/index.html`, and returns its response to the browser.

- [ ] **AC-8**: Given a `prometheus_session` cookie containing an expired JWT, when `GET /ui/llama3-8b-q4-local/` is requested, then the gateway responds `302` to `/ui/login?next=%2Fui%2Fllama3-8b-q4-local%2F` (not a 401 JSON error).

- [ ] **AC-9**: Given a valid `prometheus_session` cookie, when `POST /ui/logout` is called, then the response sets `Set-Cookie: prometheus_session=; Max-Age=0` and redirects `302` to `/ui/login`.

- [ ] **AC-10**: Given a `POST /ui/login` with `next=https://evil.com`, when credentials are valid, then the gateway redirects to `/ui/` and does not redirect to `https://evil.com`.

- [ ] **AC-11**: Given a source IP that has sent ≥ `UI_LOGIN_RATE_LIMIT_RPM` POST requests to `/ui/login` within 60 seconds, when the next login attempt arrives, then the gateway returns `429 Too Many Requests` with a `Retry-After` header.

- [ ] **AC-12**: Given any successfully proxied `/ui/{path}` request, then the `Cookie`, `Authorization`, and `Host` headers from the browser are NOT forwarded to the llama-server backend.

- [ ] **AC-13**: Given `AUTH_SERVICE_TOKEN_URL` is unreachable, when a login form is submitted, then the gateway returns the login form with a generic error message and does not expose the internal service URL or exception details.

- [ ] **AC-14**: Given `GATEWAY_TLS_CERT_FILE` and `GATEWAY_TLS_KEY_FILE` are both set and the files exist, when the gateway starts, then it listens on HTTPS and the `Set-Cookie` header on successful login includes the `Secure` attribute.

- [ ] **AC-15**: Given only one of `GATEWAY_TLS_CERT_FILE` / `GATEWAY_TLS_KEY_FILE` is set (incomplete config), when the gateway starts, then it exits with a configuration error before accepting any connections.

- [ ] **AC-16**: Given no `GATEWAY_TLS_CERT_FILE` / `GATEWAY_TLS_KEY_FILE` and `UI_ENABLED=true`, when the gateway starts, then it logs a warning that the `Secure` cookie flag will cause browsers to reject the session cookie over HTTP.

- [ ] **AC-17**: Given a `POST /ui/login` with a `model_id` that exists in the registry but has `discovery: false`, then the gateway returns the login form with an error message stating the model is not available for UI access.

- [ ] **AC-18**: Given a valid session cookie, when `GET /ui/nonexistent-model/` is requested, then the gateway returns `404 Not Found`. Given a valid session cookie and a model that exists but has `discovery: false`, when `GET /ui/<that-model-id>/` is requested, then the gateway also returns `404 Not Found`.

- [ ] **AC-19**: Given no registry models have `discovery: true`, when `GET /ui/login` is requested, then the page renders with an empty model combobox and a visible notice informing the operator that no models are currently discoverable.

- [ ] **AC-20**: Given `gateway/certs/gen-dev-cert.sh` is executed, then it produces `gateway/certs/dev.crt` and `gateway/certs/dev.key`, the cert has `CN=localhost`, and `openssl x509 -text` shows `DNS:localhost` and `IP:127.0.0.1` in the Subject Alternative Names.

## Open Questions

- [x] **Q1 — RESOLVED**: The llama-server built-in UI does NOT use WebSockets. It uses the
  standard `fetch` API with `stream: true` against the `/completion` endpoint, which returns
  `text/event-stream` (SSE). `httpx` + FastAPI `StreamingResponse` handle this natively.
  No WebSocket proxy needed.

- [x] **Q2 — RESOLVED**: The login page includes a model combobox populated from the registry
  (filtered to models with `backend_url`). After login, the gateway redirects to
  `/ui/<selected_model_id>/` and the proxy routes each request to `model.backend_url`.
  Multi-model UI selection is in scope for this spec.

- [x] **Q3 — RESOLVED**: The gateway terminates TLS using `GATEWAY_TLS_CERT_FILE` +
  `GATEWAY_TLS_KEY_FILE`. A dev helper script generates a self-signed cert for local use.
  `UI_SESSION_COOKIE_SECURE=false` is NOT an option — the `Secure` flag is always set and TLS
  is the enforcement mechanism. See AC-14, AC-15, AC-16 and AC-20.

## References

- Related specs: `memory/specs/002-jwt-authentication-middleware.md`, `memory/specs/005-auth-service.md`, `memory/specs/006-multi-model-gateway.md`, `memory/specs/007-rate-limiting-and-throughput.md`
- llama.cpp server built-in UI: served at the server root `/`; includes `/index.html` and bundled JS assets
- OWASP Open Redirect: https://cheatsheetseries.owasp.org/cheatsheets/Unvalidated_Redirects_and_Forwards_Cheat_Sheet.html
- RFC 6749 §4.4 — OAuth2 Client Credentials Grant
