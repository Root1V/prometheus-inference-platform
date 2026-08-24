---
description: "Use when implementing authentication, authorization, JWT validation, OAuth2 flows, token issuance, API key management, or any security middleware in Prometheus."
applyTo: "gateway/src/prometheus_gateway/auth/**,auth-service/src/prometheus_auth/**"
---

# Auth & Authz — Guidelines

## Auth Strategy

| Flow | Use Case |
|------|----------|
| OAuth2 Client Credentials | Service-to-service (backend apps → gateway) |
| JWT Bearer (RS256) | All authenticated API calls |

**Never** use HS256 (shared secret) in production. Always RS256 with rotating key pairs.

## JWT Validation Checklist

Every JWT must be validated in this order:
1. **Signature** — verify with RS256 public key
2. **`exp`** — must be in the future (reject with 401 if expired)
3. **`iss`** — must match `JWT_ISSUER` setting
4. **`aud`** — must include `"prometheus-gateway"`
5. **`sub`** — extract as `user_id`; must be non-empty
6. **`scope`** — check required scope for the endpoint

```python
# Canonical validation — do not skip any step
def validate_token(token: str) -> Claims:
    try:
        payload = jwt.decode(
            token,
            public_key,
            algorithms=["RS256"],
            audience="prometheus-gateway",
            issuer=settings.jwt_issuer,
        )
    except ExpiredSignatureError:
        raise AuthError(401, "Token expired")
    except InvalidTokenError as e:
        raise AuthError(401, f"Invalid token: {e}")
    return Claims(**payload)
```

## Authorization (Scopes)

Define scopes as constants:
```python
class Scope:
    INFERENCE_READ = "inference:read"      # Can call /v1/chat/completions
    INFERENCE_STREAM = "inference:stream"  # Can use SSE streaming
    ADMIN_MODELS = "admin:models"          # Can list/manage models
    ADMIN_USAGE = "admin:usage"            # Can query usage reports
```

Every endpoint declares its required scopes. Authz middleware enforces it.

## Security Constraints

- **No logging of tokens** — never log Authorization headers or raw JWT strings.
- **No storing passwords** — gateway is stateless; identity is delegated to the OAuth2 server.
- **Clock skew allowance**: max 30 seconds (leeway).
- **Short-lived tokens**: recommend max 1-hour TTL. Gateway does NOT refresh tokens.
- **Revocation**: maintain a token revocation list (Redis) for logout/compromise scenarios.

## Error Responses

| Scenario | HTTP Status | `type` suffix |
|----------|-------------|---------------|
| Missing Authorization header | 401 | `missing-credentials` |
| Invalid/malformed token | 401 | `invalid-token` |
| Expired token | 401 | `token-expired` |
| Insufficient scope | 403 | `insufficient-scope` |

## OWASP API Security Top 10 Checklist

For any auth code change, verify:
- [ ] API1: Broken Object Level Authorization — `user_id` from JWT, never from request body
- [ ] API2: Broken Authentication — full JWT validation chain above
- [ ] API3: Broken Object Property Level Auth — never expose internal fields
- [ ] API8: Security Misconfiguration — no debug endpoints in prod, no default creds
