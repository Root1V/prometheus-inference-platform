# Key Rotation — RS256 JWT Signing Keys

Runbook for rotating the RS256 key pair used to sign JWTs in Prometheus.
Zero downtime — old and new keys coexist during the transition window.

> Source: `memory/specs/005-auth-service.md` (Q2)

---

## How JWKS multi-key rotation works

The auth-service JWKS endpoint (`/.well-known/jwks.json`) returns **all active public keys** as an array. Each key has a unique `kid` (key ID). When a token is issued, the `kid` of the signing key is embedded in the JWT header. The gateway fetches the matching public key from the array — no restart or config change required.

```
Gateway                          Auth Service
  │                                   │
  │  GET /.well-known/jwks.json        │
  │ ─────────────────────────────────► │
  │  { keys: [ {kid: "2026-q1", ...},  │
  │             {kid: "2026-q2", ...} ] }
  │ ◄───────────────────────────────── │
  │                                   │
  │  validate token header.kid         │
  │  → pick matching public key        │
```

**Prerequisite**: the gateway must be configured with `JWT_JWKS_URL`, not `JWT_PUBLIC_KEY_FILE`.

```bash
# gateway/.env (production)
JWT_JWKS_URL=https://auth-service:9000/.well-known/jwks.json
AUTH_SERVICE_TLS_VERIFY=false   # only for self-signed dev certificates
```

---

## Rotation procedure

### Step 1 — Generate new key pair (old key still active)

```bash
openssl genrsa -out keys/private_YYYY-QN.pem 2048
openssl rsa -in keys/private_YYYY-QN.pem -pubout -out keys/public_YYYY-QN.pem
```

Example for Q2 2026:
```bash
openssl genrsa -out keys/private_2026-q2.pem 2048
openssl rsa -in keys/private_2026-q2.pem -pubout -out keys/public_2026-q2.pem
```

### Step 2 — Activate new key (no downtime)

Update `AUTH_ACTIVE_KID` in the auth-service env and restart only that container:

```bash
# auth-service/.env
AUTH_ACTIVE_KID=2026-q2
JWT_PRIVATE_KEY_HOST_PATH=/absolute/path/to/keys/private_2026-q2.pem
JWT_PUBLIC_KEY_HOST_PATH=/absolute/path/to/keys/public_2026-q2.pem
```

```bash
podman compose restart auth-service
```

After restart:
- Auth-service issues new tokens signed with `kid: 2026-q2`
- JWKS endpoint serves **both** `2026-q1` and `2026-q2` public keys
- Gateway validates old tokens with old key, new tokens with new key — both work simultaneously

### Step 3 — Wait for the transition window

```
Wait = max(token_ttl) = 3 hours  (admin role TTL — longest possible token lifetime)
```

After 3 hours, all tokens signed with the old key have expired naturally.

### Step 4 — Remove old key

```bash
# Remove old public key from JWKS key store (auth-service config or key store dir)
# Remove old private key file
rm keys/private_2026-q1.pem
rm keys/public_2026-q1.pem

# Restart to reload key set
podman compose restart auth-service
```

### Step 5 — Verify

```bash
curl https://auth-service:9000/.well-known/jwks.json | jq '.keys | length'
# Expected: 1  (only the new key)

curl https://auth-service:9000/.well-known/jwks.json | jq '.keys[0].kid'
# Expected: "2026-q2"
```

---

## Token TTLs (max transition wait time)

| Role | TTL | Impact on rotation window |
|------|-----|--------------------------|
| `admin` | 3 hours | Sets the maximum wait in Step 3 |
| `cognitive` | 1 hour | — |
| `agent` | 10 minutes | — |
| `app` | 5 minutes | — |

---

## If the private key is compromised

**Immediate action** — skip the transition window entirely:

1. Generate new key pair (Step 1)
2. Activate new key (Step 2)
3. **Immediately** remove old key from JWKS — do NOT wait 3 hours
4. Restart auth-service
5. All tokens signed with the old key will now fail validation (unknown `kid`)
6. All clients must re-authenticate — this is expected and acceptable

> Revoke affected client credentials via `DELETE /admin/clients/{client_id}` if specific clients are suspected compromised (`memory/specs/005-auth-service.md` AC-15, AC-16).

---

## Related

- `memory/specs/005-auth-service.md` — auth-service design, JWKS endpoint (AC-17)
- `memory/specs/002-jwt-authentication-middleware.md` — gateway JWT validation
- `memory/decisions/2026-03-28-rs256-jwt.md` — why RS256 over HS256
