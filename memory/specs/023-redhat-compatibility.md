---
id: "023"
title: "Red Hat Enterprise Linux Compatibility"
status: closed
current-agent: ""
created: 2026-05-03
updated: 2026-05-09
pipeline-log:
  - agent: spec-writer-agent
    status: draft
    timestamp: 2026-05-03
  - agent: spec-writer-agent
    status: draft
    timestamp: 2026-05-09
  - agent: developer-agent
    status: implementing
    timestamp: 2026-05-09
  - agent: developer-agent
    status: code-complete
    timestamp: 2026-05-09
  - agent: test-agent
    status: testing
    timestamp: 2026-05-09
  - agent: test-agent
    status: tests-passed
    timestamp: 2026-05-09
  - agent: security-reviewer-agent
    status: reviewing
    timestamp: 2026-05-09
  - agent: test-agent
    status: testing
    timestamp: 2026-05-09
  - agent: test-agent
    status: tests-passed
    timestamp: 2026-05-09
  - agent: security-reviewer-agent
    status: reviewing
    timestamp: 2026-05-09
  - agent: security-reviewer-agent
    status: security-approved
    timestamp: 2026-05-09
  - agent: spec-writer-agent
    status: draft
    timestamp: 2026-05-09
  - agent: developer-agent
    status: implementing
    timestamp: 2026-05-09
  - agent: developer-agent
    status: code-complete
    timestamp: 2026-05-09
  - agent: test-agent
    status: testing
    timestamp: 2026-05-09
  - agent: test-agent
    status: tests-passed
    timestamp: 2026-05-09
  - agent: security-reviewer-agent
    status: reviewing
    timestamp: 2026-05-09
  - agent: security-reviewer-agent
    status: security-approved
    timestamp: 2026-05-09
  - agent: spec-writer-agent
    status: draft
    timestamp: 2026-05-09
  - agent: developer-agent
    status: implementing
    timestamp: 2026-05-09
  - agent: developer-agent
    status: code-complete
    timestamp: 2026-05-09
  - agent: test-agent
    status: testing
    timestamp: 2026-05-09
  - agent: test-agent
    status: tests-passed
    timestamp: 2026-05-09
  - agent: security-reviewer-agent
    status: reviewing
    timestamp: 2026-05-09
  - agent: security-reviewer-agent
    status: security-approved
    timestamp: 2026-05-09
  - agent: test-agent
    status: testing
    timestamp: 2026-05-09
  - agent: test-agent
    status: tests-passed
    timestamp: 2026-05-09
  - agent: security-reviewer-agent
    status: reviewing
    timestamp: 2026-05-09
  - agent: security-reviewer-agent
    status: security-approved
    timestamp: 2026-05-09
  - agent: docs-agent
    status: documenting
    timestamp: 2026-05-09
  - agent: docs-agent
    status: implemented
    timestamp: 2026-05-09
  - agent: release-agent
    status: releasing
    timestamp: 2026-05-09
  - agent: release-agent
    status: released
    timestamp: 2026-05-09
  - agent: release-agent
    status: closed
    timestamp: 2026-05-09
---

<!-- Scope: Full-stack RHEL 9.7 compatibility for Prometheus. Target: 2× bare-metal servers, Intel Xeon Gold 6334 (32 threads, AVX512+VNNI), 251 GB RAM, no GPU, kernel 5.14, x86_64. Expanded 2026-05-09 to include end-to-end installer and validation scripts under scripts/ (repo root). Amended 2026-05-09: added git clone step (Step 1), canonical project dir /opt/prometheus-ai-inference/, TOTAL_STEPS 9→10. Amended 2026-05-09: early proxy export before git clone; --git-credentials=PATH flag for non-interactive GitHub Enterprise auth via ~/.netrc. -->

# 023 — Red Hat Enterprise Linux Compatibility

## Problem Statement

Prometheus currently runs on macOS (Metal GPU, Podman Desktop). The target deployment is two bare-metal RHEL 9.7 servers with the following profile:

| Property | Value |
|----------|-------|
| OS | Red Hat Enterprise Linux 9.7, kernel 5.14, x86_64 |
| CPU | Intel Xeon Gold 6334 @ 3.6 GHz · 2 sockets × 8 cores = 32 threads |
| ISA | AVX2 · AVX512F · AVX512_VNNI |
| RAM | 251 GB |
| GPU | None — CPU-only inference |
| NUMA | 2 nodes |

AVX512_VNNI enables hardware-accelerated INT8 dot-products, significantly boosting throughput for Q4/Q8 quantised models on this CPU. Multiple layers of the stack are macOS-specific and must be adapted for RHEL. Without an automated installer, each deployment requires an operator to manually execute ~50 shell commands from `memory/wiki/deployment.md` — error-prone and difficult to repeat consistently across two servers.

## Goals

- [ ] Provide RHEL-specific `.env` templates (root, gateway, auth-service) and a `manager.toml` RHEL snippet.
- [ ] Create `scripts/install-rhel.sh` — idempotent, end-to-end installer that provisions a fresh RHEL 9.7 host from zero (git clone → packages → llama-server build → uv/deps → host dirs → certs/keys → secrets → `.env` setup) using `/opt/prometheus-ai-inference/` as the canonical project directory.
- [ ] Create `scripts/validate.sh` — post-install smoke-test that checks binary health, `.env` completeness, container readiness, and a full OAuth2 round-trip.

## Non-Goals

- Does not cover CUDA/ROCm — target servers are CPU-only.
- No interactive password management or ncurses wizard.
- Does not modify application code.
- `scripts/install-rhel.sh` does not run `podman compose up` or start inference servers — it only prepares the host; the operator starts the stack manually (or via systemd).
- No NFS or shared storage setup — each server manages its own local model storage under `/srv/prometheus/models`.

## Proposed Solution

### `scripts/install-rhel.sh`

Idempotent Bash script (safe to re-run) implementing steps 1–7 of `memory/wiki/deployment.md` for RHEL 9.7. Accepts optional flags:

| Flag | Description |
|------|-------------|
| `--proxy=http://host:port` | Export proxy to current shell session immediately AND write to `/etc/environment` and `.env` files in STEP 10. When set, proxy is active for git clone, dnf, and all subsequent network operations. |
| `--git-credentials=PATH` | Path to a credentials file with format `machine github.com login USER password PAT` (standard netrc format). The script writes this entry into `~/.netrc` with `chmod 600` before the git clone. If not provided and no existing `~/.netrc` entry exists, git will prompt interactively. |
| `--project-dir=PATH` | Repository root path (default: `/opt/prometheus-ai-inference/`) |
| `--user=NAME` | `llmops` user to create/use (default: `llmops`) — script must be run as `llmops`, using `sudo` only for privileged operations |
| `--skip-llama-build` | Skip llama-server compilation if binary already installed |

Steps in order:
1. Clone or update repository: configure `~/.netrc` from `--git-credentials=PATH` if provided; apply proxy to shell session if `--proxy=` is set; then `git clone https://github.com/<your-username>/prometheus-ai-inference.git /opt/prometheus-ai-inference/` if the directory does not exist; `git -C /opt/prometheus-ai-inference pull --ff-only` if it already is a git repo
2. Install system packages: `cmake gcc gcc-c++ openblas-devel python3 python3-pip git podman podman-compose` (note: `python3-venv` is a Debian name; on RHEL 9 venv is bundled in `python3`)
3. Create `llmops` user and ensure ownership of `/opt/prometheus-ai-inference/`
4. Install `uv` and run `uv sync` from the project root
5. Build and install `llama-server` with `-DGGML_BLAS=ON -DGGML_BLAS_VENDOR=OpenBLAS -DCMAKE_BUILD_TYPE=Release` (delegates to `runtime/scripts/install-server.sh`)
6. Create host directories with correct ownership and SELinux labels (implements §7 of `deployment.md`)
7. Generate RSA key pair + self-signed TLS certificate under `/etc/prometheus/{keys,certs}/`
8. Copy `.env` templates only if the destination does not exist (idempotent):
   - `.env.redhat.example` → `.env`
   - `gateway/.env.podman.example` → `gateway/.env`
   - `auth-service/.env.example` → `auth-service/.env`
9. Generate secrets with `openssl rand` and inject them into the corresponding `.env` files (replace placeholders)
10. Print a summary of remaining manual steps (adjust paths, register gateway client, start the stack)

**Logging**: every step prints a timestamped header to stdout (`[STEP N/10] YYYY-MM-DD HH:MM:SS — <description>`) and appends the same output to `<project-dir>/logs/install-rhel.log`. On completion, the script prints the full log path. On failure, it prints `ERROR: step N failed — see <log-path>` and the last 20 lines of the log.

### `scripts/validate.sh`

Smoke-test Bash script. Prints a PASS/FAIL table for each check and exits with code 1 if any check fails.

| Check | What it verifies |
|-------|------------------|
| binary | `llama-server --version` runs without error |
| env-files | `.env`, `gateway/.env`, `auth-service/.env` exist and contain no `<placeholder>` patterns |
| llama-health | `curl http://127.0.0.1:8080/health` → `{"status":"ok"}` |
| gateway-health | `curl https://localhost:8000/health` → `{"status":"ok"}` |
| auth-health | `curl https://localhost:9000/health` → `{"status":"ok"}` |
| oauth2 | Obtains a token from `/oauth2/token` and calls `GET /v1/models` with it → HTTP 200 |

### Key Design Decisions

| Decision | Rationale |
|----------|-----------|
| Canonical project dir `/opt/prometheus-ai-inference/` | Predictable location on all RHEL servers; no path inference from script location |
| Git clone as Step 1 (idempotent: `pull --ff-only` if already cloned) | Self-contained deployment — operator only needs to run the installer, not clone manually |
| Proxy exported to shell immediately after arg parsing | git clone and dnf both need the proxy before STEP 10 writes it to files |
| Git credentials via `~/.netrc` (netrc format, chmod 600) | Standard git mechanism; credentials never passed as command-line args; git reads netrc automatically for HTTPS |
| Idempotent: copy `.env` only if destination does not exist | Avoids overwriting existing configuration on re-runs |
| Delegate build to `runtime/scripts/install-server.sh` | Reuses existing logic — single source of truth |
| Separate install and validate into two scripts | Operator can re-run `validate.sh` at any time without risk |
| Secrets generated inside the install script | Eliminates manual step; never hardcoded in templates |
| Proxy via flag, not environment variable | Prevents proxy leaking into non-interactive sessions |
| Step logging to stdout + `logs/install-rhel.log` | Operator sees real-time progress and has a persistent record for debugging |

### Implementation notes (post-implementation)

- Default idempotency semantics: the installer skips steps already completed; pass `--force` to re-run all steps and regenerate secrets where applicable.
- A lightweight Git ownership guard is applied before git operations to avoid "dubious ownership" failures (the implementation sets `git config --global --add safe.directory "${PROJECT_DIR}"`).
- Git updates use `git stash` / `git pull --ff-only` / `git stash pop` to handle transient local changes safely.
- `~/.netrc` is written with a tempfile+`chmod 600` then moved into place to avoid a permissions race.
- `chown` operations are conditional: the script checks for the existence of the `llmops` user before changing ownership to avoid errors on systems where the user is not present.
- The `python3-venv` package was removed from the install list for RHEL (RHEL 9 bundles `venv` with `python3`).
- Tests: `scripts/tests/test_scripts_023.sh` was added/updated to cover the 10-step validator, repo/clone checks, and `--force` semantics.

### RHEL `.env` Templates (already implemented)

- `.env.redhat.example` (root): `host.containers.internal`, `PROMETHEUS_GPU_LAYERS=0`, `PROMETHEUS_THREADS=32`, RHEL CA bundle, Linux paths
- `gateway/.env.podman.example`: `JWT_JWKS_URL` with `host.containers.internal`, RHEL paths
- `auth-service/.env.example`: `REQUESTS_CA_BUNDLE`, `AUTH_BIND_HOST=127.0.0.1`, no hardcoded secrets

## API Contract

N/A

## Data Model

| File | Change |
|------|--------|
| `scripts/install-rhel.sh` (new) | End-to-end RHEL installer; implements steps 1–7 of `deployment.md` |
| `scripts/validate.sh` (new) | Post-install smoke-test; PASS/FAIL table; exits with code 1 on any failure |
| `.env.redhat.example` (new) | RHEL root template: `host.containers.internal`, Linux paths, CA bundle, perf tuning |
| `gateway/.env.podman.example` (modified) | JWKS URL with `host.containers.internal`, Linux paths |
| `auth-service/.env.example` (modified) | `REQUESTS_CA_BUNDLE`, `AUTH_BIND_HOST=127.0.0.1`, no hardcoded secrets |

## Security Considerations

- `scripts/install-rhel.sh` generates secrets via `openssl rand -hex 32` and writes them **directly** into the host `.env` files — never printed to stdout or logged.
- RSA private keys are created under `/etc/prometheus/keys/` with `chmod 600`; the script does not modify permissions of pre-existing keys (safe idempotence).
- The script uses `sudo` only for operations that require it (`dnf`, `mkdir /etc/...`, `chown`); it does not assume execution as root.
- Self-signed TLS certificates are valid for development/test only; the script displays a prominent warning if the hostname is not `*.internal` or `localhost`.
- `scripts/validate.sh` uses `--cacert` for self-signed certificates; never uses `-k`/`--insecure`.
- No script accepts passwords or secrets as command-line arguments (susceptible to `ps aux` exposure).
- Git credentials file (`--git-credentials=PATH`) is written to `~/.netrc` with `chmod 600`; the source file must already have mode 600 or the script warns and aborts.
- Proxy URL is passed via `--proxy=` flag (not as an env var) to prevent leaking into non-interactive sessions; it is exported to the shell session in memory only for the duration of the script.

## Acceptance Criteria

- [x] AC-1: Given the RHEL `.env` templates (`.env.redhat.example`, `gateway/.env.podman.example`, `auth-service/.env.example`), when inspected, then each contains `host.containers.internal` (not `host.docker.internal`), `PROMETHEUS_GPU_LAYERS=0`, `PROMETHEUS_THREADS=32`, `REQUESTS_CA_BUNDLE=/etc/pki/tls/certs/ca-bundle.crt`, and Linux absolute paths throughout.
- [x] AC-2: Given any RHEL template, when inspected, then `AUTH_JWT_ISSUER` / `JWT_ISSUER` are set to `https://prometheus-victor.internal`; no secret field contains a hardcoded value — only `<replace-with-openssl-rand-hex-32>` comments or blank.
- [x] AC-3: Given `scripts/install-rhel.sh`, when executed on a fresh RHEL 9.7 host, then it configures `~/.netrc` from `--git-credentials=PATH` (if provided), exports the proxy to the shell session (if `--proxy=` is set), clones the repository from `https://github.com/<your-username>/prometheus-ai-inference.git` into `/opt/prometheus-ai-inference/` (or runs `git pull --ff-only` if already cloned), installs system packages (`cmake`, `gcc`, `gcc-c++`, `openblas-devel`, `python3`, `python3-pip`, `podman`), creates the `llmops` user, exits 0, and each of the 10 steps prints a timestamped `[STEP N/10]` header to stdout and appends it to `logs/install-rhel.log`.
- [x] AC-4: Given `scripts/install-rhel.sh`, when executed, then it builds and installs `llama-server` via `runtime/scripts/install-server.sh` with OpenBLAS flags (`-DGGML_BLAS=ON -DGGML_BLAS_VENDOR=OpenBLAS`) and places the binary in `~/.local/bin`.
- [x] AC-5: Given `scripts/install-rhel.sh`, when executed, then it installs `uv` and runs `uv sync` from the project root, resulting in a populated `.venv`.
- [x] AC-6: Given `scripts/install-rhel.sh`, when executed, then all host directories required by `podman-compose.yml` exist (`/etc/prometheus/{keys,certs}`, `/var/lib/prometheus/auth-service`, `/var/log/prometheus/*`, `/var/run/prometheus/runtime/run`) with correct UIDs, `chmod 750`, and `chcon container_file_t` applied.
- [x] AC-7: Given `scripts/install-rhel.sh`, when executed, then RSA keypair `private_2026-q1.pem` / `public_2026-q1.pem` exists under `/etc/prometheus/keys/` with permissions 600/644, and a self-signed TLS cert/key pair exists under `/etc/prometheus/certs/`.
- [x] AC-8: Given `scripts/install-rhel.sh`, when executed and the target `.env` files do not yet exist, then it copies `.env.redhat.example` → `.env`, `gateway/.env.podman.example` → `gateway/.env`, and `auth-service/.env.example` → `auth-service/.env`; when re-executed with the files already present, it does NOT overwrite them.
- [x] AC-9: Given `scripts/install-rhel.sh`, when executed, then it generates `AUTH_ADMIN_API_KEY`, `SHARE_TOKEN_ENCRYPTION_KEY`, `GRAFANA_SECRET_KEY`, and `GRAFANA_ADMIN_PASSWORD` via `openssl rand` and writes them into the corresponding `.env` files without printing them to stdout.
- [x] AC-10: Given `scripts/install-rhel.sh --proxy=http://proxy.example.com:80`, when executed, then the proxy is exported to the current shell session immediately (before STEP 1 git clone), `http_proxy`, `https_proxy`, `HTTP_PROXY`, `HTTPS_PROXY`, and `NO_PROXY` are written into `/etc/environment` and the root `.env` in STEP 10; the `NO_PROXY` value includes `localhost,127.0.0.1,.internal,gateway,manager,auth-service,redis,loki,promtail,tempo,grafana,10.89.0.1`.
- [x] AC-11: Given `scripts/install-rhel.sh`, when any step fails (e.g. `dnf install` non-zero), then the script exits immediately with a non-zero code, prints `ERROR: step N failed — see <log-path>` to stderr, and appends the last 20 lines of the failed step's output to `logs/install-rhel.log`.
- [x] AC-12: Given `scripts/validate.sh`, when executed after a successful install, then it checks `llama-server --version` and prints `PASS` for the `binary` check; prints `FAIL` (and exits 1) if the binary is missing.
- [x] AC-13: Given `scripts/validate.sh`, when executed, then it inspects `.env`, `gateway/.env`, and `auth-service/.env` for remaining `<placeholder>` or `<replace-` patterns; prints `PASS` if none found, `FAIL` with the offending file and line if any remain.
- [x] AC-14: Given `scripts/validate.sh`, when the Podman containers are running, then it calls `GET /health` on both `https://localhost:8000` (gateway) and `https://localhost:9000` (auth-service) and prints `PASS`/`FAIL` per endpoint.
- [x] AC-15: Given `scripts/validate.sh`, when a valid `AUTH_ADMIN_API_KEY` is set in `auth-service/.env`, then it performs a full OAuth2 smoke-test (register test client → get token → `GET /v1/models` → delete test client) and prints `PASS` on HTTP 200 or `FAIL` with the HTTP status code.

## E2E Validation

> Script: `validations/023-redhat-compatibility.py`
> Run against the full RHEL stack after executing `scripts/install-rhel.sh` and starting all services.
> May be deferred until the stack is running on the target RHEL server.

## Open Questions

- [x] Q1: Will `scripts/install-rhel.sh` be run as `root` or as `llmops` with `sudo`? **Resolved: run as `llmops` with `sudo` for privileged operations.**
- [x] Q2: Do the two RHEL servers share an NFS mount for `/srv/prometheus/models`, or does each server have its own local storage? **Resolved: each server has its own local storage — no NFS.**

## References

- Deployment steps: `memory/wiki/deployment.md` §1–7
- Hardware: Intel Xeon Gold 6334, RHEL 9.7, kernel 5.14, x86_64, ×2 servidores, CPU-only, 251 GB RAM, AVX512+VNNI
- Existing install script: `runtime/scripts/install-server.sh`
- Existing permissions helper: `scripts/ensure_auth_permissions.sh`
- Existing templates: `gateway/.env.example`, `auth-service/.env.example`, `.env.redhat.example`
