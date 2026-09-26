---
id: "015"
title: "Auth Service Admin Dashboard — Client registry UI with CRUD, labelling, and secret rotation"
status: closed
current-agent: ""
pipeline-log: []
created: 2026-04-12
updated: 2026-04-11
---

# 015 — Auth Service Admin Dashboard

## Problem Statement

The auth-service exposes a REST admin API (`POST /admin/clients`, `DELETE /admin/clients/{id}`, etc.) but has no web interface. Operators must use `curl` or Postman to manage OAuth clients, with no way to see all clients at a glance, tag them by owning component or person, or rotate secrets through a safe UI that shows the new secret exactly once.

Three concrete pain points:

1. **No visibility**: There is no table view of all registered clients — their roles, scopes, TTLs, and active/revoked status.
2. **No labelling**: `OAuthClient` has no `label` field. It is impossible to record which component, service, or human each client belongs to without external bookkeeping.
3. **Unsafe secret management**: Rotating a secret today requires a raw `POST` curl call with no confirmation dialog and no visual "copy-before-close" safety net.

## Goals

- [x] Add a `label` free-text column to `OAuthClient` (DB migration + schema update).
- [x] Add `PATCH /admin/clients/{client_id}` to update `client_name`, `label`, `allowed_scopes`, and `token_ttl_seconds` for an existing client.
- [x] Add `POST /admin/clients/{client_id}/reactivate` to reverse a deactivation.
- [x] Mount an admin web UI on auth-service at `/admin/ui/` — login page + dashboard.
- [x] Dashboard login: HTML form that validates the `AUTH_ADMIN_API_KEY` and sets a signed session cookie (1-hour expiry). Same brand palette and dark-mode toggle as spec-014.
- [x] Dashboard: client table with columns `label`, `name`, `client_id`, `role`, `scopes`, `TTL`, `status`, `created_at`, `updated_at`, and per-row actions.
- [x] Dashboard actions: **Create**, **Edit** (name/label/scopes/TTL), **Deactivate**, **Reactivate**, **Rotate secret** (shows new secret once), **Delete** (hard delete with confirmation).
- [x] No external CSS framework or JavaScript library dependencies (self-contained, inline or bundled).
- [x] Responsive design on dashboard (table columns hidden at ≤900 px and ≤600 px) and on sub-pages (edit, secret reveal).

## Non-Goals

- **Per-token TTL countdown**: JWTs are stateless and not stored server-side. It is not possible to list "active tokens" with remaining TTL without a token issuance store. Out of scope for this spec.
- **Token usage metrics, rate-limit history, or consumption charts**: covered by a future observability spec.
- **Changes to the Gateway or llama.cpp**: this spec is entirely within the auth-service boundary.
- **Multi-admin or role-based access to the dashboard**: the single `AUTH_ADMIN_API_KEY` is sufficient for now.
- **Export / import of the client registry**.

## Proposed Solution

### New files

```
auth-service/src/prometheus_auth/
├── routers/
│   ├── admin.py          # existing — add PATCH + reactivate endpoints
│   └── admin_ui.py       # NEW — login + dashboard HTML routes
├── templates/
│   ├── admin_login.html  # NEW — Jinja2 login page (same brand pattern as spec-014)
│   └── admin_dashboard.html  # NEW — Jinja2 dashboard page
└── static/
    └── admin.css         # NEW — brand palette CSS (same tokens as login.css)
```

Templates and static files follow the exact same file-separation pattern established in spec-014:

- Python handlers never contain HTML strings.
- All colours come from CSS custom properties defined in `admin.css`.
- No hex value outside the approved palette appears in the stylesheet (see Design Tokens below).

### Session mechanism

The dashboard is browser-facing. The existing `X-Admin-Key: <key>` header-based auth is not usable for browsers. A lightweight signed-cookie session is introduced:

- **Login form** (`POST /admin/ui/login`) validates the submitted `api_key` field against `settings.auth_admin_api_key` using `secrets.compare_digest`.
- On success, a `Set-Cookie: admin_session=<signed_token>; HttpOnly; SameSite=Lax; Path=/admin/ui` header is returned.
- The cookie is signed with [`itsdangerous.URLSafeTimedSerializer`](https://itsdangerous.palletsprojects.com/en/stable/) keyed on `AUTH_ADMIN_API_KEY` with a 3600-second max-age.
- Dashboard routes (`GET /admin/ui/dashboard`, `POST /admin/ui/…`) verify the cookie via a `_require_session` FastAPI dependency. Expired or tampered cookies redirect to `/admin/ui/login`.
- No session state is stored server-side — the signed cookie is the entire session.

### Key Design Decisions

| Decision | Rationale |
|----------|-----------|
| Signed cookie (itsdangerous) instead of server-side sessions | Zero storage overhead; consistent with the stateless architecture; `itsdangerous` is already a transitive dependency of Starlette/FastAPI |
| `PATCH /admin/clients/{id}` for updates | Partial update semantics — only supplied fields are changed; avoids resetting unrelated fields |
| Hard-delete endpoint (`DELETE /admin/clients/{id}?permanent=true`) | Soft-delete already exists (deactivate). Hard delete is an admin escape hatch behind an explicit query param |
| `label` as plain `TEXT`, nullable, no unique constraint | Labels are informational only; multiple clients can share the same label (e.g. "local-dev") |
| Reactivate as `POST /admin/clients/{id}/reactivate` | Symmetric with the existing implicit deactivate pattern; avoids overloading `PATCH` with state machine transitions |
| Same CSS token pattern as spec-014 | Consistent brand; reuse `:root {} / html.dark {}` pattern; no new colour system to maintain |

### Dashboard UX flow

```
GET /admin/ui/                      → redirect to /admin/ui/login
GET /admin/ui/login                 → login page (brand palette, dark toggle)
POST /admin/ui/login                → validate key → set cookie → redirect to /admin/ui/dashboard
GET /admin/ui/dashboard             → client table + navigation
POST /admin/ui/clients              → create (form submit) → redirect to dashboard
POST /admin/ui/clients/{id}/edit    → update name/label/scopes/ttl → redirect
POST /admin/ui/clients/{id}/deactivate  → soft delete → redirect
POST /admin/ui/clients/{id}/reactivate  → undo deactivation → redirect
POST /admin/ui/clients/{id}/rotate-secret → rotate → show new secret on confirmation page
POST /admin/ui/clients/{id}/delete  → hard delete (requires confirmation field) → redirect
GET  /admin/ui/logout               → clear cookie → redirect to login
```

All mutating dashboard routes use `POST` (HTML forms cannot issue `PATCH`/`DELETE`). The underlying REST API endpoints remain unchanged and continue to accept `DELETE` / `PATCH` / `POST` for programmatic use.

### REST API additions

#### `PATCH /admin/clients/{client_id}`

```
Authorization: X-Admin-Key: <key>
Content-Type: application/json

{
  "client_name": "updated-name",       # optional
  "label": "gateway-component",        # optional
  "allowed_scopes": ["inference:read"],# optional — full replacement, not append
  "token_ttl_seconds": 600             # optional
}
```

Response `200`:
```json
{
  "client_id": "...",
  "client_name": "...",
  "label": "...",
  "role": "app",
  "allowed_scopes": ["inference:read"],
  "token_ttl_seconds": 600,
  "is_active": true,
  "created_at": "2026-04-12T10:00:00Z"
}
```

Errors: `404` if client not found.

#### `POST /admin/clients/{client_id}/reactivate`

```
Authorization: X-Admin-Key: <key>
```

Response `200`:
```json
{ "client_id": "...", "is_active": true }
```

Sets `is_active = True` and clears `revoked_at`. Also removes the Redis revocation key if Redis is configured. Errors: `404` if client not found, `409` if client is already active.

#### Hard delete via existing `DELETE /admin/clients/{client_id}`

Extend the existing deactivate endpoint with an optional `?permanent=true` query parameter. When `permanent=true`:
- Row is **hard-deleted** from the database.
- Redis revocation key is written (to reject any outstanding tokens that were issued before deletion).
- Response: `204 No Content`.

When `permanent` is absent (or `false`), behaviour is unchanged (soft deactivate).

### Data Model

#### Migration: add `label` and `updated_at` columns to `oauth_clients`

```python
# New columns on OAuthClient
label:      Mapped[str | None]      = mapped_column(Text, nullable=True, default=None)
updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, default=None)
```
# New column on OAuthClient
label: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
```

No existing data is affected (column is nullable with no default). Migration is handled by the `create_tables()` call at startup using SQLAlchemy's `checkfirst=True` approach — or via an explicit Alembic migration if preferred.

**Updated `ClientListItem` schema:**

```python
class ClientListItem(BaseModel):
    client_id: str
    client_name: str
    label: str | None = None    # NEW
    role: str
    allowed_scopes: list[str]
    token_ttl_seconds: int
    is_active: bool
    created_at: datetime
    updated_at: datetime | None = None  # NEW — set on every mutation

class UpdateClientRequest(BaseModel):     # NEW
    client_name: str | None = None
    label: str | None = None
    allowed_scopes: list[str] | None = None
    token_ttl_seconds: int | None = None
```

### Design Tokens (CSS)

All colours must come from the approved Prometheus palette. Same token naming as `login.css`.

**Light mode (`:root`)**

| Token | Value | Usage |
|-------|-------|-------|
| `--color-primary` | `#001391` | Button bg, links, focus ring |
| `--color-primary-label` | `#F7F8F8` | Button text on primary bg |
| `--color-accent` | `#85C8FF` | Active badge, hover state |
| `--color-bg` | `#F7F8F8` | Page background |
| `--color-surface` | `#FFFFFF` (use `#F7F8F8` as fallback) | Card / table bg |
| `--color-border` | `#CAD1D8` | Table lines, input borders |
| `--color-text` | `#060E46` | Body text |
| `--color-muted` | `#46536D` | Secondary text, sub-labels |
| `--color-warn` | `#FFB56B` | Warning badge (Mandarin) |
| `--color-danger` | `#001391` | Danger actions (use Electric Blue outline variant) |
| `--color-success` | `#88E783` | Active status badge |

**Dark mode (`html.dark`)**

| Token | Value | Usage |
|-------|-------|-------|
| `--color-primary` | `#85C8FF` | Serene Blue |
| `--color-primary-label` | `#060E46` | Midnight — button text on Serene Blue bg |
| `--color-accent` | `#9694FF` | Purple highlight |
| `--color-bg` | `#000519` | Grey-5 page background |
| `--color-surface` | `#060E46` | Midnight — card/table bg |
| `--color-border` | `#46536D` | Grey-4 |
| `--color-text` | `#F7F8F8` | Sand — body text |
| `--color-muted` | `#ADB8C2` | Grey-3 |
| `--color-warn` | `#FFE761` | Canary |
| `--color-success` | `#88E783` | Lime |

> No hex literal may appear in any CSS rule body — only `var(--color-*)` tokens.

## Security Considerations

- **Session cookie**: `HttpOnly` (no JS access), `SameSite=Lax` (CSRF mitigation for same-origin form posts), `Path=/admin/ui` (not sent to other paths), max-age 3600 s. Sign with `URLSafeTimedSerializer` keyed on `AUTH_ADMIN_API_KEY`.
- **Login rate limiting**: the `POST /admin/ui/login` route is covered by the existing `auth_rate_limit_rpm` SlowAPI limiter (keyed on remote IP).
- **Constant-time comparison**: login uses `secrets.compare_digest` — no timing oracle on the key comparison.
- **Hard delete confirmation**: the dashboard hard-delete form requires typing the `client_id` into a confirmation field before submission. The server re-validates `client_id` in the POST body matches the path parameter.
- **CSRF**: all mutating form actions include a hidden `_csrf_token` field signed with `URLSafeTimedSerializer`. The `_require_session` dependency validates it on every mutating POST.
- **Input validation**: `UpdateClientRequest` validates `allowed_scopes` against `VALID_SCOPES`; `token_ttl_seconds` must be 60–86400.
- **No secret display in logs**: `rotate-secret` response page renders the plaintext secret in HTML; it is never logged.
- **`label` field**: no SQL injection risk — persisted only via SQLAlchemy ORM parameterised queries, never interpolated into raw SQL.

### Acceptance Criteria

### Data model

- [x] **AC-1**: Given the auth-service starts with a fresh or existing database, the `oauth_clients` table has a nullable `label TEXT` column.
- [x] **AC-2**: Given a `ClientListItem` response from `GET /admin/clients`, the JSON includes a `label` field (null if unset).

### REST API — PATCH update

- [x] **AC-3**: Given an active client, when `PATCH /admin/clients/{id}` is called with a valid `X-Admin-Key` and `{"label": "gateway"}`, then the response is `200` and contains `"label": "gateway"`. The DB row is updated.
- [x] **AC-4**: Given `PATCH /admin/clients/{id}` with `{"allowed_scopes": ["unknown:scope"]}`, then the response is `422` (invalid scope).
- [x] **AC-5**: Given `PATCH /admin/clients/{unknown_id}`, then the response is `404`.
- [x] **AC-6**: Given `PATCH /admin/clients/{id}` without `X-Admin-Key`, then the response is `401`.

### REST API — Reactivate

- [x] **AC-7**: Given a previously deactivated client, when `POST /admin/clients/{id}/reactivate` is called with a valid admin key, then `is_active` becomes `true`, `revoked_at` is cleared, and the response is `200`.
- [x] **AC-8**: Given an already-active client, when `POST /admin/clients/{id}/reactivate` is called, then the response is `409`.
- [x] **AC-9**: Given a Redis revocation key exists for the client, when the client is reactivated, then the revocation key is deleted from Redis.

### REST API — Hard delete

- [x] **AC-10**: Given `DELETE /admin/clients/{id}?permanent=true` with a valid admin key, then the client row is removed from the database and the response is `204`.
- [x] **AC-11**: Given `DELETE /admin/clients/{id}` (no `permanent` param), behaviour is unchanged — soft deactivate only.

### Admin UI — Login

- [x] **AC-12**: Given `GET /admin/ui/login`, the response is `200` with a login page that uses the approved brand palette and has a dark/light mode toggle.
- [x] **AC-13**: Given the correct `AUTH_ADMIN_API_KEY` is submitted via the login form, then a signed `admin_session` cookie is set and the browser is redirected to `/admin/ui/dashboard`.
- [x] **AC-14**: Given an incorrect key is submitted, then no cookie is set, the login page is re-rendered with an error message, and the HTTP status is `401`.
- [x] **AC-15**: Given a request to `GET /admin/ui/dashboard` without a valid session cookie, then the response redirects (`302`) to `/admin/ui/login`.
- [x] **AC-16**: Given a valid session cookie that is expired (>3600 s old), then `GET /admin/ui/dashboard` redirects to `/admin/ui/login`.

### Admin UI — Dashboard

- [x] **AC-17**: Given a valid session, `GET /admin/ui/dashboard` returns `200` with an HTML page listing all registered clients in a table.
- [x] **AC-18**: Given a client with `label = "my-client"`, the dashboard table row displays that label.
- [x] **AC-19**: Given the "Create" form is submitted with valid fields (name, role, scopes, optional label), a new client is created and the dashboard reloads with the new row visible (including the plaintext client secret shown once).
- [x] **AC-20**: Given the "Edit" form for an existing client is submitted with a new label and scopes, the client record is updated and the dashboard row reflects the changes.
- [x] **AC-21**: Given the "Deactivate" button is clicked for an active client, the client's status becomes `revoked` and the row is marked accordingly.
- [x] **AC-22**: Given the "Reactivate" button is clicked for a deactivated client, the client's status returns to `active`.
- [x] **AC-23**: Given the "Rotate secret" action is submitted, a confirmation page shows the new secret once. Navigating away from that page does not re-display the secret.
- [x] **AC-24**: Given the "Delete" hard-delete form is submitted with the correct `client_id` confirmation, the client row is permanently removed and no longer appears in the dashboard.
- [x] **AC-25**: Given the "Logout" link is clicked, the session cookie is cleared and the browser is redirected to `/admin/ui/login`.

### CSS / brand palette

- [x] **AC-26**: `admin.css` contains no hex literal in any CSS rule body — only `var(--color-*)` tokens. All `--color-*` tokens in `:root {}` and `html.dark {}` use only approved palette hex values.

### Updated timestamp

- [x] **AC-27**: `OAuthClient` has a nullable `updated_at` column. Given any mutation (PATCH, deactivate, reactivate, rotate-secret), `updated_at` is set to the current UTC timestamp and is returned in `ClientListItem`.
- [x] **AC-28**: The dashboard table shows an "Updated" column. Rows that have never been mutated display `—`; mutated rows show the date in `YYYY-MM-DD` format.

### Responsive layout

- [x] **AC-29**: At viewport width ≤900 px, the dashboard table hides the Scopes, TTL, and Updated columns. At ≤600 px it additionally hides Label and Role, keeping only Status, Name/ID, and Actions.
- [x] **AC-30**: The Edit-client page and Secret-reveal page render correctly at viewport widths down to 320 px — form inputs are full-width, the secret copy button wraps below the value, and action buttons wrap if needed.

### UX improvements (post-implementation)

- [x] **AC-31**: Scope selector renders as a responsive card grid (3-col ≥900 px, 2-col ≤900 px, 1-col ≤560 px). Each card shows the scope name in monospace Electric Blue and its description in muted grey below.
- [x] **AC-32**: Creating a client without selecting any scope shows an inline error message and re-opens the form panel with previously entered Name, Role, and Label values preserved.
- [x] **AC-33**: Input fields and scope cards have a distinct light-grey background (`--color-input-bg`) that contrasts with the whitish panel surface in light mode.
- [x] **AC-34**: The Name and Label field labels include inline contextual descriptions; Name has placeholder `e.g. pipeline-agent-001` and Label has placeholder `e.g. equipo-data, prod`.

## Open Questions

- [x] **Q1**: `itsdangerous` added as an explicit dependency in `auth-service/pyproject.toml`.
- [x] **Q2**: Additive migration implemented in `create_tables()` — runs `ALTER TABLE … ADD COLUMN` on startup (idempotent, errors swallowed). Alembic migration deferred.
- [x] **Q3**: `GET /admin/ui/` tries session first, redirects to dashboard if valid, otherwise to login.

## References

- Related specs: `memory/specs/005-auth-service.md` (original auth-service), `memory/specs/014-login-page-ux-redesign.md` (brand palette pattern)
- `auth-service/src/prometheus_auth/routers/admin.py` — existing admin REST endpoints
- `auth-service/src/prometheus_auth/db.py` — `OAuthClient` model
- `auth-service/src/prometheus_auth/config.py` — `Settings.auth_admin_api_key`
- `gateway/src/prometheus_gateway/ui/static/login.css` — reference CSS token pattern
- [itsdangerous docs](https://itsdangerous.palletsprojects.com/en/stable/) — `URLSafeTimedSerializer`
