---
id: "025"
title: "install-rhel.sh idempotency fixes — TLS cert ownership, bind-mount env paths, and auth-service config"
status: security-approved
current-agent: security-reviewer-agent
created: 2026-05-11T00:00:00Z
updated: 2026-05-12T01:13:20Z
pipeline-log: 
  - agent: spec-writer-agent
    status: draft
    timestamp: 2026-05-11T00:00:00Z
  - agent: developer-agent
    status: implementing
    timestamp: 2026-05-11T09:00:00Z
  - agent: developer-agent
    status: code-complete
    timestamp: 2026-05-11T09:00:00Z
  - agent: test-agent
    status: testing
    timestamp: 2026-05-11T18:45:00Z
  - agent: test-agent
    status: tests-passed
    timestamp: 2026-05-11T18:55:00Z
  - agent: security-reviewer-agent
    status: reviewing
    timestamp: 2026-05-11T19:10:00Z
  - agent: security-reviewer-agent
    status: security-approved
    timestamp: 2026-05-11T19:10:00Z
  - agent: spec-writer-agent
    status: draft
    timestamp: 2026-05-11T20:00:00Z
  - agent: developer-agent
    status: implementing
    timestamp: 2026-05-11T23:33:12Z
  - agent: developer-agent
    status: code-complete
    timestamp: 2026-05-11T23:33:12Z
  - agent: test-agent
    status: tests-passed
    timestamp: 2026-05-11T23:38:52Z
  - agent: security-reviewer-agent
    status: reviewing
    timestamp: 2026-05-11T23:46:28Z
  - agent: security-reviewer-agent
    status: security-approved
    timestamp: 2026-05-11T23:46:28Z
  - agent: spec-writer-agent
    status: draft
    timestamp: 2026-05-12T00:32:03Z
  - agent: developer-agent
    status: implementing
    timestamp: 2026-05-12T00:35:07Z
  - agent: developer-agent
    status: code-complete
    timestamp: 2026-05-12T00:35:07Z
  - agent: test-agent
    status: testing
    timestamp: 2026-05-12T01:00:20Z
  - agent: test-agent
    status: tests-passed
    timestamp: 2026-05-12T01:13:20Z
  - agent: security-reviewer-agent
    status: reviewing
    timestamp: 2026-05-12T01:13:20Z
  - agent: security-reviewer-agent
    status: security-approved
    timestamp: 2026-05-12T01:13:20Z
  - agent: test-agent
    status: tests-passed
    timestamp: 2026-05-12T01:13:20Z
---

<!-- Hotfix triggered by two production-impacting issues on auth-service startup.
     Root cause identified by validation-triage-agent (2026-05-11).
     References released spec: memory/specs/023-redhat-compatibility.md (AC-7, AC-8).
     Amended 2026-05-11: scope extended to include bind-mount env path injection (AC-4)
     after second production failure: sqlite3.OperationalError on auth.db.
     Amended 2026-05-12: scope extended to include auth-service/.env fixed-var injection (AC-5)
     after third production failure: auth-service/.env on server had all variables commented out. -->

# 025 — install-rhel.sh idempotency fixes — TLS cert ownership, bind-mount env paths, and auth-service config

## Problem Statement

Two independent production failures were identified on the same RHEL host after running
`install-rhel.sh` on an existing installation.

### Issue 1 — TLS cert ownership not corrected on re-run

After running `install-rhel.sh` on a host where TLS certificates at `/etc/prometheus/certs/`
already existed (generated previously by a root process), the auth-service container fails
to start with:

```
PermissionError: [Errno 13] Permission denied
```

Uvicorn cannot load `auth_tls.key` (bind-mounted from `/etc/prometheus/certs/auth.key`)
because the file is owned by `root:root 600`, and the container process runs as UID 1001
(`prometheus-auth`).

**Root cause**: In STEP 7 of `install-rhel.sh`, the `chown` and `restorecon` calls for
cert/key files are inside the `else` branch (cert generation only). When the idempotency
guard skips regeneration (`cert already exists → skip`), ownership is never corrected.

### Issue 2 — Bind-mount env paths not injected on re-run

After `install-rhel.sh` runs on a host where `.env` already existed, the auth-service
container fails with:

```
sqlite3.OperationalError: unable to open database file
```

The auth-service tries to open `AUTH_DB_URL=sqlite+aiosqlite:////data/auth.db`, which
maps to the bind-mount at `AUTH_DB_HOST_PATH`. STEP 8 copies `.env` from the example
template (which has `AUTH_DB_HOST_PATH=/var/lib/prometheus/auth-service`) but skips
the copy if `.env` already exists (idempotency guard). The `_set_env_var` calls that
follow the copy only inject key/cert paths — **not** `AUTH_DB_HOST_PATH` or any
other bind-mount path. `podman-compose` falls back to `./data/auth-service` (relative,
not created by STEP 6, no permissions for UID 1001) → database cannot be opened.

**Root cause**: STEP 8 of `install-rhel.sh` calls `_set_env_var` for JWT key and TLS
cert paths, but not for `AUTH_DB_HOST_PATH`, `CONTAINER_LOG_HOST_PATH`,
`MANAGER_LOG_HOST_PATH`, `MANAGER_PID_ROOT`, and `MANAGER_LOG_ROOT`. These are only
set when `.env` is freshly copied; on idempotent re-runs they are never enforced.

**Combined impact**: auth-service is completely down. No OAuth2 token issuance.
All API calls fail JWT validation. Production is non-functional.

### Issue 3 — auth-service/.env fixed variables not enforced on re-run

On the RHEL server, `auth-service/.env` has all non-secret variables commented out
(e.g. `#AUTH_DB_URL=sqlite+aiosqlite:////data/auth.db`, `#AUTH_PRIVATE_KEY_FILE=...`).
This happens because `_copy_env` skips the file when it already exists, and no
`_set_env_var` calls enforce the fixed (non-secret) values afterwards. When those
variables are commented, the auth-service reads them as unset and fails to start.

The local `.env.example` copy works because it is freshly copied on first install.
The server copy diverges on any subsequent run of the installer.

**Root cause**: STEP 8 only uses `_copy_env` for `auth-service/.env`. Fixed variables
that never change between environments (`AUTH_DB_URL`, `AUTH_PRIVATE_KEY_FILE`,
`AUTH_PUBLIC_KEY_FILE`, `AUTH_REVOCATION_REDIS_URL`, `AUTH_RATE_LIMIT_RPM`) are never
injected unconditionally, so a stale or manually-edited file silently breaks the service.

## Goals

- [ ] `install-rhel.sh` always enforces correct cert/key ownership, even when certs are not regenerated.
- [ ] `install-rhel.sh` always injects the correct absolute RHEL host paths for all bind-mount variables into root `.env`, even when `.env` already exists.
- [ ] `install-rhel.sh` always injects fixed (non-secret) configuration variables into `auth-service/.env`, even when the file already exists.
- [ ] Auth-service container starts successfully after a plain re-run of the installer on an existing host.

## Non-Goals

- Not in scope: changing the UID values (1000/1001) or the directory structure.
- Not in scope: refactoring STEP 7 or STEP 8 beyond the targeted fixes.
- Not in scope: any change to gateway, auth-service source code, or Dockerfiles.
- Not in scope: adding new bind-mount paths not already present in `.env.redhat.example`.
- Not in scope: injecting secret variables (`AUTH_ADMIN_API_KEY`, `SHARE_TOKEN_ENCRYPTION_KEY`) via `_set_env_var` — those are already handled by `_inject_secret` in STEP 9.
- Not in scope: managing `AUTH_JWT_ISSUER` or `AUTH_ACTIVE_KID` — operator-specific values set once at install time.

## Proposed Solution

**Fix 1 (AC-1, AC-2):** Move the `chown` + `restorecon` block outside the `else` branch
in the STEP 7 cert loop so they execute unconditionally for every cert/key pair regardless
of whether the cert was just generated or already existed.

**Fix 2 (AC-4):** In STEP 8, after the existing `_set_env_var` calls for key/cert paths,
add `_set_env_var` calls for all bind-mount host paths that are created in STEP 6:
`AUTH_DB_HOST_PATH`, `CONTAINER_LOG_HOST_PATH`, `MANAGER_LOG_HOST_PATH`,
`MANAGER_PID_ROOT`, `MANAGER_LOG_ROOT`. `_set_env_var` always overwrites, so this
behaves correctly whether `.env` was freshly copied or already existed.

**Fix 3 (AC-5):** In STEP 8, after the `_copy_env` call for `auth-service/.env`, add
`_set_env_var` calls for the five fixed (non-secret) variables that must always be
active in the auth-service container:
- `AUTH_DB_URL=sqlite+aiosqlite:////data/auth.db`
- `AUTH_PRIVATE_KEY_FILE=/run/secrets/jwt_private_key.pem`
- `AUTH_PUBLIC_KEY_FILE=/run/secrets/jwt_public_key.pem`
- `AUTH_REVOCATION_REDIS_URL=redis://redis:6379/0`
- `AUTH_RATE_LIMIT_RPM=10`

These values are identical in every Podman deployment and must not be overridable by
a stale or manually-edited `.env` file.

### Key Design Decisions

| Decision | Rationale |
|----------|-----------|
| Apply `chown` unconditionally (not only on generation) | Correct ownership must survive re-runs; idempotent by nature (same owner set again is a no-op cost). |
| Keep `restorecon` alongside `chown` | SELinux label loss is the same class of problem — fix both in the same pass. |
| Use `_set_env_var` (not `_copy_env`) for bind-mount paths | `_set_env_var` always overwrites; `_copy_env` skips if file exists — the latter is the root cause of the issue. |
| Inject fixed auth-service vars unconditionally | Container-internal paths (`/run/secrets/...`, `/data`) are constant across all Podman deployments; never rely on the copied template remaining intact. |
| Do not inject secrets via `_set_env_var` | Secrets are handled by `_inject_secret` (STEP 9), which only injects when value is a placeholder — preserving operator-set values. |
| Branch: `hotfix/025-tls-cert-ownership` from `main` | Production hotfix — base on `main` per hotfix flow rules. |

## API Contract

N/A — no API changes.

## Data Model

N/A.

## Security Considerations

- The ownership fix enforces **least-privilege**: cert/key files are owned by the service
  UID that reads them, never by root. This is a security improvement, not a regression.
- Private keys (`*.key`) retain `chmod 600` — readable only by the owning UID.
- Public certs (`*.crt`) retain `chmod 644`.
- `restorecon` ensures SELinux `container_file_t` labels are applied, required for
  Podman bind-mount access on RHEL with SELinux enforcing.
- The bind-mount path injection writes only absolute paths already present in
  `.env.redhat.example`; no new secrets, credentials, or sensitive values are written.
- The auth-service fixed-var injection writes only container-internal paths and a rate
  limit integer; none of these are secrets. `AUTH_DB_URL` contains no credentials.
- Secret variables (`AUTH_ADMIN_API_KEY`, `SHARE_TOKEN_ENCRYPTION_KEY`) are explicitly
  excluded from `_set_env_var` injection and remain under `_inject_secret` control.
- No secrets are exposed; no logging changes.

## Acceptance Criteria

- [x] AC-1: Given TLS cert files at `/etc/prometheus/certs/` exist and are owned by
  `root:root`, when `install-rhel.sh` runs without `--force`, then `auth.crt` and
  `auth.key` ownership is updated to `1001:1001` and `gateway.crt` / `gateway.key`
  ownership is updated to `1000:1000`.

- [x] AC-2: Given the STEP 7 cert loop runs (certs exist or are regenerated), when
  the loop completes, then `restorecon` is applied to each cert/key pair unconditionally
  (not only when newly generated).

- [x] AC-3: Given ownership is corrected by the installer, when the auth-service
  container starts with its bind-mounted `/etc/prometheus/certs/auth.key`, then
  uvicorn loads the TLS cert chain without `PermissionError: [Errno 13]`.

- [x] AC-4: Given the root `.env` file already exists when `install-rhel.sh` runs
  without `--force`, when STEP 8 completes, then `AUTH_DB_HOST_PATH`,
  `CONTAINER_LOG_HOST_PATH`, `MANAGER_LOG_HOST_PATH`, `MANAGER_PID_ROOT`, and
  `MANAGER_LOG_ROOT` are all set to their correct absolute RHEL paths in root `.env`
  (unconditionally, regardless of whether `.env` was freshly copied or pre-existing).

- [x] AC-5: Given `auth-service/.env` already exists when `install-rhel.sh` runs
  without `--force`, when STEP 8 completes, then `AUTH_DB_URL`,
  `AUTH_PRIVATE_KEY_FILE`, `AUTH_PUBLIC_KEY_FILE`, `AUTH_REVOCATION_REDIS_URL`, and
  `AUTH_RATE_LIMIT_RPM` are all present and uncommented with their correct values in
  `auth-service/.env` (unconditionally, regardless of whether the file was freshly
  copied or pre-existing with variables commented out).

## E2E Validation

> Script: `scripts/validate.sh` (existing — extend step-7/keys-certs and step-8/env-files checks)
> Run on the RHEL host after applying the fix: `bash scripts/validate.sh`.
>
> **step-7/cert-ownership** (AC-1, AC-2, AC-3):
> - `auth.crt` and `auth.key` are owned by UID 1001
> - `gateway.crt` and `gateway.key` are owned by UID 1000
> - `auth.key` and `gateway.key` have mode 600
> - `auth.crt` and `gateway.crt` have mode 644
>
> **step-8/env-files** (AC-4) — extend existing check to verify:
> - `AUTH_DB_HOST_PATH=/var/lib/prometheus/auth-service` is present and correct in root `.env`
> - `CONTAINER_LOG_HOST_PATH=/var/log/prometheus` is present and correct in root `.env`
>
> **step-8/auth-env** (AC-5) — add new check to verify in `auth-service/.env`:
> - `AUTH_DB_URL=sqlite+aiosqlite:////data/auth.db` is present and uncommented
> - `AUTH_PRIVATE_KEY_FILE=/run/secrets/jwt_private_key.pem` is present and uncommented
> - `AUTH_PUBLIC_KEY_FILE=/run/secrets/jwt_public_key.pem` is present and uncommented
> - `AUTH_REVOCATION_REDIS_URL=redis://redis:6379/0` is present and uncommented
> - `AUTH_RATE_LIMIT_RPM=10` is present and uncommented
>
> No new validation file is created — `validate.sh` is the canonical post-install checker.

## Open Questions

- None.

## References

- Related spec: `memory/specs/023-redhat-compatibility.md` (AC-7 — TLS cert generation, AC-8 — env copy)
- Affected file: `scripts/install-rhel.sh` — STEP 7 (lines ~642–669), STEP 8 (lines ~698–706)
- Affected file: `scripts/validate.sh` — step-7/keys-certs, step-8/env-files checks
- Affected file: `scripts/tests/test_scripts_025.sh` — AC-4 tests added; AC-5 tests to be added
- Affected file: `auth-service/.env.example` — source of correct values for AC-5
- Hotfix branch: `hotfix/025-tls-cert-ownership`
