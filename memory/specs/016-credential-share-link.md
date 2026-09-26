---
id: "016"
title: "Credential Share Link — Single-Use Secure Delivery URL"
status: closed
current-agent: ""
pipeline-log: []
created: 2026-04-11
updated: 2026-04-11
---

# 016 — Credential Share Link — Single-Use Secure Delivery URL

## Problem Statement

When a new OAuth2 client is registered via the admin dashboard, the client secret is
displayed once and never again. The admin must then transmit `client_id` + `client_secret`
to the service owner through a separate channel. In practice this happens over email or
chat messages, which are insecure, persistent, and not audited.

This spec introduces a **single-use, time-limited, encrypted secret-delivery URL** that
lets the admin share credentials safely without exposing them in messaging systems. The
URL is consumed once, the plaintext is destroyed, and the event is logged.

## Goals

- [ ] Generate a single-use share URL immediately after client creation (or rotation)
- [ ] Public endpoint serves credentials exactly once, then destroys the plaintext
- [ ] Share token stores the plaintext secret AES-256 encrypted at rest
- [ ] Token expires automatically after a configurable TTL (default 1 h, max 24 h)
- [ ] Dashboard shows share link status for each client (Active / Used / Expired / —)
- [ ] Admin can revoke an active share token before it is consumed
- [ ] Consumer IP + User-Agent logged for audit trail
- [ ] Security guidance displayed to the admin and the recipient

## Non-Goals

- Asymmetric key exchange (Sealed Secrets pattern) — out of scope for this iteration
- Email delivery or push notifications — links are copy-pasted manually
- Share links for credentials that already exist before this feature ships (no backfill)
- Gateway-layer involvement — this is entirely within the Auth Service

## Proposed Solution

A new `CredentialShareToken` table stores one-time-delivery tokens. When the admin
creates or rotates a client, the dashboard offers a **"Get share link"** button. Clicking
it generates a token, stores the encrypted plaintext secret, and returns a URL of the
form:

```
https://<auth-service-host>/share/<token>
```

Anyone with the URL (no authentication required) can view the credentials **exactly
once**. On first load the `used_at` and `used_by_ip` fields are stamped and the
`secret_plaintext_enc` is set to `NULL`. Subsequent requests return `410 Gone`.

```
Admin clicks "Get share link"
        │
        ▼  POST /admin/ui/clients/{id}/share   (admin session + CSRF required)
Auth Service ──► create CredentialShareToken ──► return page with share URL
        │
        │  URL shared via secure channel (Slack, Signal, 1Password Send…)
        │
        ▼  GET /share/{token}    (public, no auth)
Auth Service ──► validate token (exists? not used? not expired?)
             ──► decrypt secret_plaintext_enc
             ──► render credential page (one-time view warning)
             ──► stamp used_at, used_by_ip, clear secret_plaintext_enc
```

### Key Design Decisions

| Decision | Rationale |
|----------|-----------|
| 256-bit token (32 random bytes, URL-safe base64) | Brute-force infeasible; safe in URLs |
| AES-256-GCM for plaintext at rest | Authenticated encryption; GCM tag detects tampering |
| Encryption key from `SHARE_TOKEN_ENCRYPTION_KEY` env var (32-byte hex) | Follows existing env-var secret pattern; never hardcoded |
| Plaintext cleared on first view | Minimises exposure window; secret only survives until consumed |
| 1-hour default TTL, configurable `SHARE_TOKEN_TTL_SECONDS` | Short enough to limit exposure; long enough for async handoff |
| `410 Gone` on used/expired tokens | Distinguishes "consumed" from "never existed" (404) |
| **One active share token per client** (Q1 resolved) | Creating a new token auto-revokes any existing active token; prevents credential confusion |
| **`/share/{token}` uses dashboard CSS palette, no admin nav** (Q2 resolved) | Consistent brand; recipient sees a professional page without admin controls |
| **Rate-limit `/share/{token}` at 10 req/min per IP** (Q3 resolved) | Mitigates token enumeration without impacting legitimate use |
| HTTP → HTTPS redirect on `/share/` (if TLS configured) | Tokens must never travel unencrypted |
| No full token in structured logs | Token logged as first 8 chars + `…` to enable tracing without leaking |

## API Contract

No changes to the OpenAPI gateway contract. New Auth Service routes:

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `POST` | `/admin/ui/clients/{client_id}/share` | Admin session + CSRF | Generate share token; returns share URL in response |
| `DELETE` | `/admin/ui/share/{token_id}/revoke` | Admin session + CSRF | Invalidate an active share token before use |
| `GET` | `/share/{token}` | None (public) | One-time credential view |

## Data Model

### New table: `credential_share_tokens`

```sql
CREATE TABLE credential_share_tokens (
    id               TEXT PRIMARY KEY,           -- UUID4, opaque DB key
    token            TEXT NOT NULL UNIQUE,        -- 32-byte URL-safe base64 (256-bit)
    client_id        TEXT NOT NULL REFERENCES oauth_clients(client_id) ON DELETE CASCADE,
    client_name      TEXT NOT NULL,               -- denormalised snapshot at creation time
    client_id_value  TEXT NOT NULL,               -- the client_id credential (plaintext, not secret)
    secret_plaintext_enc  TEXT,                   -- AES-256-GCM ciphertext (base64); NULL after use
    created_at       DATETIME NOT NULL,
    expires_at       DATETIME NOT NULL,
    used_at          DATETIME,
    used_by_ip       TEXT,
    used_by_ua       TEXT,                        -- User-Agent of consumer (truncated to 256 chars)
    revoked_at       DATETIME,
    revoked_by       TEXT                         -- "admin" if manually revoked
);
```

### Schema changes to `OAuthClient` (none)

The client table is unchanged. Share tokens reference clients via FK but do not alter
the client row itself. Revoking a client (`DELETE`) cascades to its share tokens.

### Encryption scheme

```
key  = bytes.fromhex(SHARE_TOKEN_ENCRYPTION_KEY)   # 32 bytes
iv   = os.urandom(12)                               # 96-bit GCM nonce
ct, tag = AES-256-GCM.encrypt(key, iv, plaintext)
stored  = base64(iv + ct + tag)                     # 12 + len(secret) + 16 bytes
```

Decryption reverses the above and raises `ValueError` on tag mismatch
(tampering or wrong key).

## Security Considerations

### Implementation requirements

- `SHARE_TOKEN_ENCRYPTION_KEY` must be exactly 32 bytes (hex-encoded → 64 hex chars).
  Startup raises `ValueError` if absent or wrong length.
- Tokens are generated with `secrets.token_urlsafe(32)` — not `uuid4`, not `random`.
- The full token value **must not** appear in any structured log line.
  Log only `token_id` (UUID) and first 8 chars + `"…"` for tracing.
- The `/share/{token}` response includes `Cache-Control: no-store, private` and
  `X-Robots-Tag: noindex` to prevent proxies and search engines from caching/indexing.
- On used/expired requests the response body contains no hint about the original secret.

### Operational best practices (displayed in the UI)

The following guidance is shown to the **admin** on the share link generation page
and to the **recipient** on the credential view page:

**For the admin (share link generator):**
> 1. Share this URL only through a secure, ephemeral channel
>    (Signal, Slack DM, 1Password Send, Vault). **Not by email.**
> 2. The link expires in **{TTL}**. Ask the recipient to open it promptly.
> 3. After the recipient confirms they have saved the secret, verify the
>    link is marked "Used" in the dashboard.
> 4. If the link is not used within the TTL or if you suspect it was
>    intercepted, click **Revoke** and rotate the client secret.

**For the recipient (credential view):**
> ⚠️  These credentials will not be shown again after you close this page.
> 1. Copy `client_id` and `client_secret` now.
> 2. Store them in your team's password manager or secret store.
> 3. Never paste secrets into chat, email, or version control.
> 4. Close this browser tab when done.

### Alternative patterns (for teams with stricter requirements)

| Pattern | When to use |
|---------|-------------|
| **Sealed Secrets (asymmetric)** | Recipient generates RSA/ECDH keypair offline; admin encrypts with public key. No server stores the plaintext at all. Recommended for highly sensitive environments. |
| **HashiCorp Vault / AWS Secrets Manager push** | Server writes the secret directly to the team's secret store. No human ever sees the plaintext. Recommended for automated provisioning. |
| **1Password Secrets Automation** | Similar to Vault; integrates with existing 1Password team vaults. |
| **This spec's approach (ephemeral URL)** | Suitable for small teams, low-risk credentials, and situations where a full Vault setup is not yet available. **Not recommended for production secrets with long-lived access.** |

## Acceptance Criteria

### Token generation

- [ ] **AC-1**: Given an admin session, when the admin submits `POST /admin/ui/clients/{id}/share`,
  then a `CredentialShareToken` row is created with a 256-bit URL-safe random token,
  `expires_at = now + TTL`, `secret_plaintext_enc` set (non-null), and the response
  renders a page displaying the full share URL.

- [ ] **AC-2**: Given `SHARE_TOKEN_TTL_SECONDS` is unset, when a share token is generated,
  then `expires_at - created_at` equals 3600 seconds (1 hour default).

- [ ] **AC-3**: Given `SHARE_TOKEN_TTL_SECONDS=7200`, when a share token is generated,
  then `expires_at - created_at` equals 7200 seconds.

- [ ] **AC-4**: Given `SHARE_TOKEN_TTL_SECONDS=90000` (> 86400), when the auth service
  starts, then it raises a `ValueError` and refuses to start.

- [ ] **AC-5**: Given a valid admin session but no CSRF token, when `POST /admin/ui/clients/{id}/share`
  is submitted, then the response is `403 Forbidden`.

- [ ] **AC-6**: Given no admin session, when `POST /admin/ui/clients/{id}/share` is submitted,
  then the response is `401` or redirects to login.

- [ ] **AC-7**: Given an unknown `client_id`, when `POST /admin/ui/clients/{id}/share` is submitted,
  then the response is `404 Not Found`.

### One-time credential view

- [ ] **AC-8**: Given an active, unexpired share token, when `GET /share/{token}` is requested,
  then the response is `200 OK` with `client_id` and `client_secret` visible in the HTML,
  and the response includes `Cache-Control: no-store, private`.

- [ ] **AC-9**: Given a request to `GET /share/{token}` that returns `200 OK`, then
  `used_at` and `used_by_ip` are set on the token row, and `secret_plaintext_enc` is
  set to `NULL` in the database.

- [ ] **AC-10**: Given a token that has already been used (`used_at` is non-null), when
  `GET /share/{token}` is requested, then the response is `410 Gone` and the body
  contains no hint of the original credentials.

- [ ] **AC-11**: Given a token that has expired (`expires_at < now`), when
  `GET /share/{token}` is requested, then the response is `410 Gone`.

- [ ] **AC-12**: Given a non-existent token value, when `GET /share/{token}` is requested,
  then the response is `404 Not Found`.

- [ ] **AC-13**: Given a valid share token, when `GET /share/{token}` is requested,
  then the response headers include `X-Robots-Tag: noindex` and `Referrer-Policy: no-referrer`.

- [ ] **AC-14**: Given the view page is rendered, then it displays a visible one-time warning
  (e.g. "⚠️ Save now — these credentials will not be shown again"), copy buttons for
  both `client_id` and `client_secret`, and the recipient security guidance.

### Encryption at rest

- [ ] **AC-15**: Given `SHARE_TOKEN_ENCRYPTION_KEY` is not set, when the auth service starts,
  then it raises a `ValueError` and refuses to start.

- [ ] **AC-16**: Given `SHARE_TOKEN_ENCRYPTION_KEY` is set to fewer than 64 hex chars (< 32 bytes),
  when the auth service starts, then it raises a `ValueError` and refuses to start.

- [ ] **AC-17**: Given a stored `secret_plaintext_enc` value, when it is decrypted and the
  ciphertext has been tampered with, then `ValueError` is raised (AES-GCM tag mismatch).

- [ ] **AC-18**: Given `secret_plaintext_enc` is `NULL` (token already used), when the
  `GET /share/{token}` endpoint attempts to decrypt it, then the response is `410 Gone`
  without raising an unhandled exception.

### Revocation

- [ ] **AC-19**: Given an active share token, when the admin submits
  `DELETE /admin/ui/share/{token_id}/revoke` with a valid session + CSRF token,
  then `revoked_at` and `revoked_by` are set and `secret_plaintext_enc` is set to `NULL`.

- [ ] **AC-20**: Given a revoked token, when `GET /share/{token}` is requested,
  then the response is `410 Gone`.

- [ ] **AC-21**: Given an already-used token, when the admin tries to revoke it,
  then the response is `409 Conflict` with a message indicating it was already consumed.

### Dashboard integration

- [ ] **AC-22**: Given the admin dashboard is loaded, then the clients table includes a
  "Share" column showing the newest active share token's status for each client:
  **Active** (unexpired + unused), **Used**, **Expired**, or **—** (none created).

- [ ] **AC-23**: Given a client row, when the "Get share link" button is clicked,
  then the form posts to `POST /admin/ui/clients/{id}/share` and the result page
  shows the share URL and admin security guidance.

- [ ] **AC-24**: Given a client with an active share token, when the "Revoke" button
  next to the share status is clicked, then the token is revoked and the dashboard
  reflects the updated status.

### Logging and audit

- [ ] **AC-25**: Given a share token that is consumed, then a structured log line is emitted
  with `event=share_token_used`, `token_id=<uuid>`, `token_prefix=<first8>…`,
  `client_id=<id>`, `used_by_ip=<ip>`, and the full token value is **not present**.

- [ ] **AC-26**: Given a share token generation event, then a structured log line is emitted
  with `event=share_token_created`, `token_id=<uuid>`, `token_prefix=<first8>…`,
  `client_id=<id>`, `expires_at=<iso>`, and the full token value is **not present**.

### Additive DB migration

- [ ] **AC-27**: Given an existing auth-service database without the
  `credential_share_tokens` table, when the service starts, then
  `create_tables()` creates the table without touching existing rows.

### Single active token per client

- [ ] **AC-28**: Given a client that already has an active (unexpired + unused + unrevoked)
  share token, when `POST /admin/ui/clients/{id}/share` is submitted, then the existing
  active token is automatically revoked (`revoked_at` set, `secret_plaintext_enc` cleared)
  before the new token is created, and the response shows only the new share URL.

## Open Questions

- [x] **Q1**: Should a client be allowed to have multiple active share tokens simultaneously?
  **Resolution**: No. Creating a new share token auto-revokes any existing active token
  for that client. This prevents confusion about which link is valid and reduces the
  blast radius if a previous link was intercepted. → Captured in AC-28.

- [x] **Q2**: Should the `/share/{token}` page be styled with the same CSS palette as the
  admin dashboard, or a minimal anonymous view?
  **Resolution**: Same palette, no admin nav. Provides a professional, consistent
  experience for the recipient without exposing any admin controls.

- [x] **Q3**: Rate-limit `/share/{token}` to mitigate enumeration?
  **Resolution**: Yes — 10 req/min per IP, hard-coded in the auth service middleware.
  → Already reflected in the Security Considerations section.

## References

- Related specs: [memory/specs/015-auth-service-dashboard.md](015-auth-service-dashboard.md)
- OWASP: [Secrets Management Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Secrets_Management_Cheat_Sheet.html)
- OWASP: [Credential Stuffing Prevention Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Credential_Stuffing_Prevention_Cheat_Sheet.html)
- Python cryptography: AES-GCM ([`cryptography.hazmat.primitives.ciphers.aead.AESGCM`](https://cryptography.io/en/latest/hazmat/primitives/aead/#cryptography.hazmat.primitives.ciphers.aead.AESGCM))
- `secrets` module: [`secrets.token_urlsafe`](https://docs.python.org/3/library/secrets.html)
