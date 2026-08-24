---
id: "002"
title: "JWT Authentication Middleware"
status: closed
current-agent: ""
pipeline-log: []
created: 2026-03-28
updated: 2026-03-28
---

# 002 — JWT Authentication Middleware

## Problem Statement

The Prometheus Gateway currently has no authentication layer. Any caller — authenticated
or not — can reach the llama.cpp proxy and consume inference capacity. Without a validated
identity on every request, there is no basis for authorization, rate limiting, or consumption
metering (specs 003 and 004 depend on `user_id` and `client_id` extracted from a trusted token).

## Goals

- [ ] Validate RS256-signed JWTs on every request except explicitly exempt endpoints
- [ ] Reject requests with missing, malformed, expired, or wrongly-issued tokens
- [ ] Extract and propagate `user_id`, `client_id`, and `scope` claims for downstream middleware
- [ ] Expose a token verification public-key rotation mechanism (JWKS endpoint support)
- [ ] Support OAuth2 Client Credentials flow as the canonical way to obtain tokens

## Non-Goals

- Token issuance — the gateway validates tokens, it does not mint them (delegated to an OAuth2 server)
- User-facing login / browser flows — this is service-to-service auth only
- Authorization / scope enforcement — covered in `003-rate-limiting.md` and future authz spec
- Token refresh — clients obtain new tokens via their OAuth2 server

## Proposed Solution

A FastAPI middleware that runs before all business logic. It extracts the `Authorization: Bearer <token>`
header, performs the full JWT validation chain, and attaches a `Claims` object to the request state.
Downstream middleware and route handlers read `request.state.claims` — they never re-validate the token.

Public key material is loaded from either:
1. A static RS256 public key file (env: `JWT_PUBLIC_KEY_FILE`) — for simple deployments
2. A remote JWKS endpoint (env: `JWT_JWKS_URL`) — for production with key rotation

### Request Flow with Auth Middleware

```
Incoming request
  │
  ├─ path in EXEMPT_PATHS (/health, /metrics)?
  │     └─ YES → pass through (no auth check)
  │
  └─ NO → extract Bearer token from Authorization header
              │
              ├─ missing/malformed header? → 401 missing-credentials
              │
              └─ validate JWT
                    ├─ signature invalid?  → 401 invalid-token
                    ├─ exp in the past?    → 401 token-expired
                    ├─ iss mismatch?       → 401 invalid-token
                    ├─ aud missing?        → 401 invalid-token
                    └─ valid → attach Claims to request.state → next()
```

### Claims Data Model

```python
@dataclass
class Claims:
    user_id: str       # from JWT `sub`
    client_id: str     # from JWT `client_id` (custom claim)
    scope: str         # from JWT `scope` (space-separated)
    expires_at: datetime
    issued_at: datetime
    issuer: str
```

### Key Design Decisions

| Decision | Rationale |
|----------|-----------|
| RS256 only, no HS256 | Asymmetric key — gateway needs only the public key, no shared secret |
| JWKS URL preferred over static key | Supports zero-downtime key rotation in production |
| Claims attached to `request.state` | Single validation point; downstream code trusts the state |
| Clock skew of ≤ 30 seconds allowed | Prevents false positives from minor NTP drift between services |
| Exempt paths as an explicit allowlist | Safe default: everything is authenticated unless explicitly listed |
| Token revocation via Redis blocklist | Supports immediate revocation on compromise without waiting for expiry |

## API Contract

> No new endpoints added by this spec. The middleware is transparent to the API surface.
> The `/health` endpoint defined in `memory/specs/001-gateway-core.md` remains unauthenticated.

HTTP header consumed:
```
Authorization: Bearer <jwt>
```

Error responses follow RFC 9457 (`gateway/api/001-gateway-core.yaml` — `ProblemDetail` schema).

## Data Model

### Token Revocation Store (Redis)

```
Key:   prometheus:revoked:<jti>
Value: "1"
TTL:   set to (token exp - now), so entries auto-expire
```

All validation must check this store after signature verification.

### Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `JWT_ISSUER` | Yes | Expected `iss` claim value |
| `JWT_AUDIENCE` | Yes | Expected `aud` claim value (e.g. `prometheus-gateway`) |
| `JWT_PUBLIC_KEY_FILE` | One of these | Path to RS256 public key PEM file |
| `JWT_JWKS_URL` | One of these | URL of the OAuth2 server JWKS endpoint |
| `JWT_CLOCK_SKEW_SECONDS` | No (default: 30) | Allowed clock skew in seconds |
| `JWT_REVOCATION_REDIS_URL` | No | Redis URL for token revocation list; if unset, revocation is disabled |

## Security Considerations

- **Never log tokens**: The raw `Authorization` header and JWT string must never appear in logs. Log only `user_id` and `jti`.
- **Algorithm pinning**: Accept only `RS256`. Reject tokens with `alg: none` or symmetric algorithms.
- **Rotation support**: When `JWT_JWKS_URL` is set, cache keys with a TTL of 5 minutes and invalidate on signature failure (retry-once pattern).
- **Revocation check**: After signature validation, check Redis for a revoked `jti` before accepting the token.
- **Short-lived tokens recommended**: Document that clients should use tokens with max 1-hour TTL.
- **No token in query string**: Reject any request that passes a token via `?token=` query parameter to prevent server-side logging of tokens in access logs.

## Acceptance Criteria

- [x] **AC-1**: Given a request with a valid RS256 JWT (correct `iss`, `aud`, non-expired), when the middleware processes it, then the request proceeds and `request.state.claims` contains the correct `user_id`, `client_id`, and `scope`.
- [x] **AC-2**: Given a request with no `Authorization` header, when the middleware processes it, then it returns HTTP 401 with `ProblemDetail` type `missing-credentials` and does not forward to llama.cpp.
- [x] **AC-3**: Given a request with a JWT signed with the wrong private key, when the middleware validates the signature, then it returns HTTP 401 with `ProblemDetail` type `invalid-token`.
- [x] **AC-4**: Given a request with an expired JWT (`exp` in the past beyond clock skew), when the middleware validates it, then it returns HTTP 401 with `ProblemDetail` type `token-expired`.
- [x] **AC-5**: Given a request with a JWT containing the wrong `iss` claim, when the middleware validates it, then it returns HTTP 401 with `ProblemDetail` type `invalid-token`.
- [x] **AC-6**: Given a request with a JWT containing the wrong `aud` claim, when the middleware validates it, then it returns HTTP 401 with `ProblemDetail` type `invalid-token`.
- [x] **AC-7**: Given a request to `GET /health`, when the middleware processes it, then the request passes through without requiring an `Authorization` header.
- [x] **AC-8**: Given a JWT whose `jti` is present in the Redis revocation list, when the middleware checks revocation, then it returns HTTP 401 with `ProblemDetail` type `token-revoked`.
- [x] **AC-9**: Given a JWT with `alg: none` or a symmetric algorithm (`HS256`), when the middleware validates it, then it returns HTTP 401 with `ProblemDetail` type `invalid-token` (algorithm pinning).
- [x] **AC-10**: Given a request that passes a `?token=<jwt>` query parameter instead of the `Authorization` header, when the middleware processes it, then it returns HTTP 401 with `ProblemDetail` type `missing-credentials` (no query-string token support).
- [x] **AC-11**: Given the middleware processes any request (success or failure), when the response is sent, then the raw JWT string does not appear anywhere in the structured log output.
- [x] **AC-12**: Given `JWT_JWKS_URL` is configured and the remote JWKS endpoint returns a new key set, when the cached keys are stale (TTL elapsed), then the middleware fetches and caches the updated key set within 5 minutes.

## Open Questions

- [ ] Q1: Should `client_id` be a standard JWT claim (`azp` / authorized party) or a Prometheus-specific custom claim? Recommendation: use `azp` for OAuth2 standard compliance.
- [ ] Q2: Should the revocation check be mandatory (fail-closed) or optional (fail-open when Redis is unavailable)? Recommendation: fail-closed in production, configurable via `JWT_REVOCATION_STRICT` env var.
- [ ] Q3: Do we need to support multiple simultaneous issuers (e.g. internal IdP + external partner IdP)? Out of scope for this spec, but `JWT_ISSUER` could accept a comma-separated list in a future revision.

## References

- Related specs: `memory/specs/001-gateway-core.md`
- Auth guidelines: `.github/instructions/auth.instructions.md`
- RFC 7519 — JSON Web Token: https://www.rfc-editor.org/rfc/rfc7519
- RFC 7517 — JSON Web Key Set (JWKS): https://www.rfc-editor.org/rfc/rfc7517
- RFC 9457 — Problem Details: https://www.rfc-editor.org/rfc/rfc9457
- OWASP API Security Top 10: https://owasp.org/API-Security/
