---
id: "017"
title: "TLS Termination for Auth Service"
status: closed
current-agent: ""
pipeline-log: []
created: 2026-04-12
updated: 2026-04-12
---

# 017 — TLS Termination for Auth Service

## Problem Statement

The auth service is designed to be deployable on a machine that is physically separate
from both the API gateway and the llama.cpp inference servers. In that topology, every
request from the gateway to the auth service (token validation, JWKS, client registration)
crosses a real network link rather than a Podman virtual bridge.

Currently, the auth service has no TLS support:

- `podman-compose.yml` binds port 9000 to **all network interfaces** — making the admin
  dashboard (login page, client credentials, share links) reachable over plain HTTP from
  any host on the network.
- The `Dockerfile` `CMD` starts uvicorn directly without an entrypoint script, so there
  is no hook to inject TLS arguments at container-startup time.
- There is no `start.sh` for bare-metal local development, unlike the gateway which has
  `gateway/start.sh`.

The gateway solves this by providing `gateway/docker-entrypoint.sh` and `gateway/start.sh`
that read `GATEWAY_TLS_CERT_FILE` / `GATEWAY_TLS_KEY_FILE` and pass `--ssl-certfile` /
`--ssl-keyfile` to uvicorn when both are set. This same pattern must be replicated for
the auth service with `AUTH_TLS_CERT_FILE` / `AUTH_TLS_KEY_FILE`.

In dev (same machine), TLS must remain **optional**: when the env vars are absent the
service starts in HTTP mode so local dev workflows are not broken.

## Goals

- [x] Add `auth-service/docker-entrypoint.sh` mirroring the gateway TLS pattern
- [x] Add `auth-service/start.sh` for bare-metal development (mirrors `gateway/start.sh`)
- [x] New optional config vars `AUTH_TLS_CERT_FILE` / `AUTH_TLS_KEY_FILE`
- [x] Document new vars in `auth-service/.env.example`
- [x] Update `podman-compose.yml`: cert bind-mounts, environment overrides, port restriction,
  and conditional healthcheck URL (http → https)
- [x] Update `auth-service/Dockerfile`: use entrypoint script, mirror gateway HEALTHCHECK
  python one-liner that switches http↔https based on env var
- [x] Provide dev cert generation script at `auth-service/certs/gen-dev-cert.sh`
- [x] Bash tests for the new entrypoint logic
- [x] Persist SQLite DB across container restarts via bind-mount (`AUTH_DB_HOST_PATH`)

## Non-Goals

- Mutual TLS (mTLS) between gateway and auth service — out of scope for this iteration
- Automatic certificate rotation / ACME / Let's Encrypt integration
- Changing the auth service port (remains 9000)
- TLS for the llama.cpp inference server (managed by `memory/specs/003`)
- Production certificate provisioning guidance (handled in runbooks)

## Proposed Solution

Replicate the gateway's TLS opt-in pattern exactly, substituting `AUTH_TLS_CERT_FILE` /
`AUTH_TLS_KEY_FILE` for `GATEWAY_TLS_CERT_FILE` / `GATEWAY_TLS_KEY_FILE`.

```
auth-service/
├── docker-entrypoint.sh     ← NEW: starts uvicorn with optional --ssl-* flags
├── start.sh                 ← NEW: bare-metal dev launcher (reads auth-service/.env)
├── certs/
│   └── gen-dev-cert.sh      ← NEW: self-signed cert for local dev (mirrors gateway/certs/)
├── .env.example             ← UPDATED: document AUTH_TLS_CERT_FILE / AUTH_TLS_KEY_FILE
├── Dockerfile               ← UPDATED: COPY + chmod entrypoint; update CMD→ENTRYPOINT; update HEALTHCHECK
└── src/prometheus_auth/
    └── config.py            ← UPDATED: add optional AUTH_TLS_CERT_FILE / AUTH_TLS_KEY_FILE fields

data/auth-service/           ← NEW (host): persistent SQLite DB directory (gitignored)
```

`podman-compose.yml` changes (auth-service service block):

```
ports:
  - "127.0.0.1:9000:9000"   ← restrict to loopback (was "9000:9000")

volumes:
  - type: bind
    source: ${AUTH_TLS_CERT_HOST_PATH:-./auth-service/certs/dev.crt}
    target: /run/secrets/auth.crt
    read_only: true
  - type: bind
    source: ${AUTH_TLS_KEY_HOST_PATH:-./auth-service/certs/dev.key}
    target: /run/secrets/auth.key
    read_only: true

environment:
  - AUTH_TLS_CERT_FILE=/run/secrets/auth.crt
  - AUTH_TLS_KEY_FILE=/run/secrets/auth.key

healthcheck:
  test: ["CMD", "python", "-c",
         "import urllib.request, ssl, os; url = ('https' if os.getenv('AUTH_TLS_CERT_FILE') else 'http') + '://localhost:9000/health'; ctx = ssl.create_default_context(); ctx.check_hostname = False; ctx.verify_mode = ssl.CERT_NONE; urllib.request.urlopen(url, context=ctx)"]
```

Root `.env` must be extended with `AUTH_TLS_CERT_HOST_PATH` / `AUTH_TLS_KEY_HOST_PATH`
(gitignored, same pattern as `TLS_CERT_HOST_PATH` / `TLS_KEY_HOST_PATH` for the gateway)
and `AUTH_DB_HOST_PATH` pointing to the host directory where `auth.db` will be persisted.

### Key Design Decisions

| Decision | Rationale |
|----------|-----------|
| Mirror gateway pattern exactly | Single mental model for TLS across all services; reduces operator error |
| Optional env vars (no startup failure if absent) | Local dev requires zero new steps by default |
| `127.0.0.1:9000:9000` default port binding | Reduces attack surface even before TLS is enabled; gateway consumes auth-service via internal Podman network regardless |
| `AUTH_BIND_HOST` escape hatch | Operators who need dashboard access from another machine during bootstrap can set `AUTH_BIND_HOST=0.0.0.0`; SSH port-forwarding is the recommended default |
| Separate `AUTH_TLS_*` host-path vars in root `.env` | Compose variable interpolation cannot read `auth-service/.env`; same pattern as gateway |
| HEALTHCHECK switches http↔https via env var in Python one-liner | Mirrors exact gateway Dockerfile pattern; no extra shell scripts in the image |
| Dev cert script in `auth-service/certs/` | Keeps dev certs scoped to the service; same self-signed approach as gateway |
| SQLite bind-mount to `data/auth-service/` | DB survives container restarts and `podman compose up --build`; `data/` is gitignored |
| mTLS / cert-pinning gateway→auth-service | Out of scope — deferred to a future spec; operator-managed CA trust is sufficient for now |

## API Contract

No changes to the auth service API surface. TLS is transport-layer only.

## Data Model

No changes to the database schema.

## Security Considerations

- Admin dashboard routes (`/admin/*`, `/login`, `/share/*`) must never be reachable
  over plain HTTP when the service is network-exposed. TLS is mandatory for cross-machine
  deployments.
- Dev self-signed certs generated by `gen-dev-cert.sh` must be scoped to `localhost` /
  `127.0.0.1`; subjects must include `CN=localhost` and a SAN for `127.0.0.1`.
- The private key file (`auth.key`) must have mode `0600` inside the container. The
  entrypoint script must not log the key path at `INFO` level (cert path only).
- The compose bind-mount defaults (`./auth-service/certs/dev.crt` / `dev.key`) must be
  listed in `.gitignore` so self-signed keys are never committed.
- `127.0.0.1:9000:9000` port binding means the admin dashboard is no longer reachable
  from other hosts on the same LAN. In a cross-machine deployment the operator is expected
  to set up a reverse proxy or VPN and remove this binding constraint explicitly.
- `AUTH_TLS_CERT_FILE` and `AUTH_TLS_KEY_FILE` are optional — absent values mean HTTP
  mode, which is acceptable **only** on a same-machine loopback deployment.
- Do not add `AUTH_TLS_CERT_FILE` / `AUTH_TLS_KEY_FILE` to the startup `ValueError`
  validators; they are intentionally optional.

## Acceptance Criteria

### docker-entrypoint.sh

- [x] **AC-1**: Given both `AUTH_TLS_CERT_FILE` and `AUTH_TLS_KEY_FILE` are set in the
  container environment, when `docker-entrypoint.sh` is executed, then uvicorn is started
  with `--ssl-certfile <AUTH_TLS_CERT_FILE> --ssl-keyfile <AUTH_TLS_KEY_FILE>` and a log
  line `TLS enabled: <cert-path>` is printed to stdout.

- [x] **AC-2**: Given `AUTH_TLS_CERT_FILE` is unset (or empty), when `docker-entrypoint.sh`
  is executed, then uvicorn is started **without** any `--ssl-*` flags (HTTP mode) and no
  TLS log line is printed.

- [x] **AC-3**: Given only one of the two env vars is set (the other empty), when
  `docker-entrypoint.sh` is executed, then uvicorn starts in HTTP mode (same behaviour as
  AC-2 — both vars are required to activate TLS).

### start.sh (bare-metal)

- [x] **AC-4**: Given `AUTH_TLS_CERT_FILE` and `AUTH_TLS_KEY_FILE` are set in
  `auth-service/.env` (or already exported), and the referenced files exist, when
  `bash auth-service/start.sh` is run, then uvicorn starts with
  `--ssl-certfile` / `--ssl-keyfile` and logs `TLS enabled: <cert-path>`.

- [x] **AC-5**: Given `AUTH_TLS_CERT_FILE` and `AUTH_TLS_KEY_FILE` are not set, when
  `bash auth-service/start.sh` is run, then uvicorn starts in HTTP mode with no
  `--ssl-*` flags (dev fallback — no breaking change for existing local setups).

- [x] **AC-6**: Given `AUTH_TLS_CERT_FILE` is set to a path that does not exist, when
  `bash auth-service/start.sh` is run, then the script prints an error message to stderr
  and exits with a non-zero status without starting uvicorn.

- [x] **AC-7**: Given `AUTH_TLS_KEY_FILE` is set to a path that does not exist, when
  `bash auth-service/start.sh` is run, then the script prints an error message to stderr
  and exits with a non-zero status without starting uvicorn.

### Config variables

- [x] **AC-8**: Given `AUTH_TLS_CERT_FILE` and `AUTH_TLS_KEY_FILE` are not set, when the
  auth service starts, then `Settings` initialises successfully and the service is fully
  operational (no `ValueError` for absent TLS vars).

- [x] **AC-9**: Given `auth-service/.env.example`, then it contains commented-out entries
  for `AUTH_TLS_CERT_FILE` and `AUTH_TLS_KEY_FILE` with a usage comment referencing this
  spec and the `gen-dev-cert.sh` script.

### Dockerfile

- [x] **AC-10**: Given the `auth-service/Dockerfile`, then it `COPY`s
  `auth-service/docker-entrypoint.sh` into the image with execute permissions, and the
  final `CMD` is replaced with `CMD ["sh", "/app/docker-entrypoint.sh"]`.

- [x] **AC-11**: Given the `auth-service/Dockerfile`, then the `HEALTHCHECK` instruction
  uses the same Python one-liner as the gateway: it reads `AUTH_TLS_CERT_FILE` at runtime
  to determine `http` vs `https` and disables cert verification for the localhost check.

### podman-compose.yml

- [x] **AC-12**: Given the `auth-service` service block in `podman-compose.yml`, then the
  `ports` entry is `"127.0.0.1:9000:9000"` (bound to loopback only, not `0.0.0.0`).

- [x] **AC-13**: Given the `auth-service` service block in `podman-compose.yml`, then two
  read-only bind-mount volumes are declared for the TLS cert and key, with host paths
  defaulting to `./auth-service/certs/dev.crt` and `./auth-service/certs/dev.key` via
  `${AUTH_TLS_CERT_HOST_PATH:-...}` / `${AUTH_TLS_KEY_HOST_PATH:-...}`.

- [x] **AC-14**: Given the `auth-service` service block in `podman-compose.yml`, then the
  `environment` block overrides `AUTH_TLS_CERT_FILE=/run/secrets/auth.crt` and
  `AUTH_TLS_KEY_FILE=/run/secrets/auth.key`, pointing to the container-internal paths.

- [x] **AC-15**: Given the `auth-service` service block in `podman-compose.yml`, then the
  `healthcheck.test` command uses a Python one-liner that reads `AUTH_TLS_CERT_FILE`
  from the container environment to select `https://` or `http://` for
  `localhost:9000/health`, with certificate verification disabled.

### Dev cert generation

- [x] **AC-16**: Given `auth-service/certs/gen-dev-cert.sh` is executed, then it generates
  a self-signed certificate (`dev.crt`) and private key (`dev.key`) in
  `auth-service/certs/`, with `CN=localhost` and a SAN for `127.0.0.1`, valid for 365
  days, using RSA-2048 or stronger.

- [x] **AC-17**: Given `auth-service/certs/dev.crt` and `auth-service/certs/dev.key`
  already exist, when `gen-dev-cert.sh` is run again, then the script exits without
  overwriting the files (idempotent) and prints a notice.

### Port-bind override

- [x] **AC-20**: Given `AUTH_BIND_HOST` is set to `0.0.0.0` in the root `.env`, when
  `podman-compose.yml` is evaluated, then the auth-service `ports` entry resolves to
  `"0.0.0.0:9000:9000"` instead of `"127.0.0.1:9000:9000"`, allowing dashboard access
  from other hosts. The default value is `127.0.0.1` (loopback). The variable is
  documented in root `.env.example` with a warning: only set to `0.0.0.0` combined with
  TLS enabled.

- [x] **AC-21**: Given the `auth-service` service block in `podman-compose.yml`, then a
  bind-mount maps `${AUTH_DB_HOST_PATH:-./data/auth-service}` on the host to `/data`
  inside the container. This ensures `auth.db` (at `AUTH_DB_URL=sqlite+aiosqlite:////data/auth.db`)
  survives container restarts and `podman compose up --build`. The `data/` directory is
  listed in `.gitignore`. `AUTH_DB_HOST_PATH` is documented in `AGENTS.md` root `.env`
  section as a required absolute path on first deploy.

### .gitignore

- [x] **AC-18**: Given the repository `.gitignore`, then `auth-service/certs/*.crt` and
  `auth-service/certs/*.key` are listed (or matched by an existing pattern) so that
  self-signed dev certs are never committed.

### Tests

- [x] **AC-19**: Given `auth-service/tests/test_auth_entrypoint.sh` (a new bash test file),
  when run with `bash auth-service/tests/test_auth_entrypoint.sh`, then:
  - A test case verifies that `docker-entrypoint.sh` with both vars set produces a uvicorn
    command containing `--ssl-certfile` and `--ssl-keyfile` (dry-run / source the script
    logic, do not start a real server).
  - A test case verifies that `docker-entrypoint.sh` with no vars set produces a uvicorn
    command **without** `--ssl-*` flags.
  - A test case verifies that setting only one var also produces no `--ssl-*` flags.
  - All test cases exit `0` on pass and print a `PASS` / `FAIL` summary, mirroring the
    style of `runtime/tests/test_runtime_scripts.sh`.

## Open Questions

- [x] **Q1**: Separate `auth-service/tests/test_auth_entrypoint.sh` — **resolved: separate file**.

- [x] **Q2**: mTLS / cert-pinning gateway→auth-service — **resolved: deferred to a future spec**.
  Operator-managed CA trust is sufficient for now.

- [x] **Q3**: Port-bind escape hatch — **resolved: add `AUTH_BIND_HOST` variable** (see AC-20).

## Post-Close Fix (detected during spec-018 implementation)

### AC-22 — Internal callers migrate to HTTPS + TLS-verify flag

- [x] **AC-22**: Given the auth-service now serves HTTPS exclusively (AC-1), when the
  gateway and manager containers call it over the internal Podman network (`prometheus_net`),
  then all internal callers use `https://auth-service:9000/...` — not `http://`. A new
  `AUTH_SERVICE_TLS_VERIFY` env var (gateway) and `PMGR_JWKS_TLS_VERIFY` env var (manager)
  allow disabling TLS certificate verification for self-signed dev certs while preventing
  plaintext connections. Both default to `true`; the dev/Podman stack sets both to `false`
  in `podman-compose.yml`.

  **Why post-close**: this retro-compatibility requirement should have been part of spec-017
  acceptance criteria. It was discovered and corrected during spec-018 implementation.
  Affected files: `gateway/config.py`, `gateway/src/prometheus_gateway/auth/jwks.py`,
  `auth/middleware.py`, `models/manager_sync.py`, `ui/router.py`,
  `runtime/manager/config.py`, `runtime/manager/api/auth.py`,
  `gateway/.env`, `podman-compose.yml`.

## References

- Implements pattern from: `memory/specs/013-web-chat-ui-proxy.md` (gateway TLS — AC-14, AC-15, AC-16)
- Auth service origin: `memory/specs/005-auth-service.md`
- Credential share links (transport security dependency): `memory/specs/016-credential-share-link.md`
- Gateway entrypoint reference: `gateway/docker-entrypoint.sh`
- Gateway start script reference: `gateway/start.sh`
- Dev cert generation reference: `gateway/certs/gen-dev-cert.sh`
