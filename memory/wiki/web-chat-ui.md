# Web Chat UI — Browser Authentication & Reverse Proxy

> Spec: `memory/specs/013-web-chat-ui-proxy.md`  
> Feature flag: `UI_ENABLED=true` (default `false`). All `/ui/*` routes return `404` when disabled.

---

## Overview

The gateway proxies the llama-server built-in chat UI through `/ui/<model_id>/`, gating it behind a browser session authenticated via the auth-service OAuth2 `client_credentials` grant. This preserves the critical constraint — llama.cpp is never exposed outside `127.0.0.1`.

---

## Request flow

```
Browser  GET /ui/<model_id>/
           │
           ├─ prometheus_session cookie valid (JWT + ui:chat scope)?
           │     └─ YES → strip /ui/<model_id> → proxy to model.backend_url/
           │
           └─ NO → 302 /ui/login?next=<encoded-path>
                         │
             GET /ui/login — HTML form (model combobox = discovery:true models)
                         │
             POST /ui/login  {client_id, client_secret, model_id, next}
                         │
             Gateway → POST auth-service /oauth2/token
                       (grant_type=client_credentials, scope=ui:chat)
                         │
              ┌──────────┴──────────┐
            401 / error           200 + JWT
              │                       │
         re-render form           validate ui:chat scope
         with error msg           Set-Cookie: prometheus_session
                                  302 → /ui/<model_id>/
```

---

## Session cookie

| Attribute | Value |
|-----------|-------|
| Name | `prometheus_session` (configurable via `UI_SESSION_COOKIE_NAME`) |
| Content | Raw RS256 JWT issued by auth-service |
| HttpOnly | Yes — JS cannot read it |
| Secure | Always set — browser rejects it over plain HTTP (intentional fail-safe) |
| SameSite | Lax — blocks cross-site POST; allows same-site navigation |
| Lifetime | Equals JWT `exp`; override with `UI_SESSION_COOKIE_MAX_AGE` (seconds) |

The session carries no server-side state. Revocation follows the existing JWT revocation path (Redis `jti` blocklist, see `memory/wiki/rate-limiting.md`).

---

## Required scope

`ui:chat` — must be assigned to the client in the auth-service. It is NOT implied by any other scope. A valid JWT without `ui:chat` is rejected; the browser is redirected to the login page with "access not granted".

---

## TLS requirement

The `Secure` cookie flag is always set. Without HTTPS the browser silently drops the cookie, making login impossible — this is the intended fail-safe.

Configure TLS via environment variables:

| Variable | Required | Description |
|----------|----------|-------------|
| `GATEWAY_TLS_CERT_FILE` | Yes (prod) | Path to PEM certificate |
| `GATEWAY_TLS_KEY_FILE` | Yes (prod) | Path to PEM private key |

Both must be set together; setting only one causes the gateway to refuse startup.

**Dev helper**: `gateway/certs/gen-dev-cert.sh` generates a self-signed cert (`dev.crt` / `dev.key`) with 10-year validity, `CN=localhost`, SANs `DNS:localhost,IP:127.0.0.1`. Add `dev.crt` to the OS/browser trust store once.

---

## Security rules

| Rule | Detail |
|------|--------|
| Open-redirect prevention | `next` parameter must start with `/ui/`; any other value is silently replaced with `/ui/` |
| Headers stripped before forwarding | `Cookie`, `Authorization`, `Host` — never reach llama-server logs |
| Credential stuffing defence | `/ui/login` rate-limited to `UI_LOGIN_RATE_LIMIT_RPM` requests per source IP per minute (default 10); returns `429` with `Retry-After` |
| No token in URL | JWT is set only via `Set-Cookie`; never placed in `Location` header or query parameter |
| Auth-service unreachable | Login returns generic "service unavailable" — no internal details exposed |
| `client_secret` transmission | POST body over TLS only; never in query string or log line |

---

## Gateway environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `UI_ENABLED` | `false` | Feature flag |
| `UI_SESSION_COOKIE_NAME` | `prometheus_session` | Cookie name |
| `UI_SESSION_COOKIE_MAX_AGE` | `0` | Override lifetime (s); `0` = follow JWT `exp` |
| `UI_LOGIN_RATE_LIMIT_RPM` | `10` | Max login attempts per IP per minute |
| `AUTH_SERVICE_TOKEN_URL` | _(required when enabled)_ | Full URL of auth-service token endpoint |
| `AUTH_SERVICE_TLS_VERIFY` | `true` | Set `false` for dev self-signed certs |
| `GATEWAY_TLS_CERT_FILE` | `None` | PEM certificate for TLS termination |
| `GATEWAY_TLS_KEY_FILE` | `None` | PEM private key for TLS termination |

---

## Related

- `memory/wiki/auth-model.md` — OAuth2 flow, scopes, JWT validation order
- `memory/wiki/deployment.md` — TLS cert generation, env files, stack startup
- `memory/wiki/model-registry.md` — `discovery` flag (controls model combobox population)
- `memory/specs/013-web-chat-ui-proxy.md` — full acceptance criteria
