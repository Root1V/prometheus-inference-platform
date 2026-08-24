# Prometheus — Agent Guidelines

## Project Context

**Prometheus** is a local-infrastructure SLM (Small Language Model) inference platform.
Run quantized open-source models on bare-metal with llama.cpp and expose them through a
secured API gateway — with authentication, authorization, and consumption tracking.

## Architecture

```
[Client Apps — Podman containers]
        │  REST / SSE
        ▼
[Prometheus Gateway — Podman :8000]   ← JWT validation · scope enforcement · rate limiting · metering
        │
        │  HTTP (internal Podman network only)
        ▼
[Auth Service — Podman :9000]          ← client registration · OAuth2 client_credentials · JWKS

[llama.cpp HTTP server — bare-metal :8080]   ← models on host GPU/CPU — reachable by gateway only
```

**Critical constraint**: llama.cpp is NEVER exposed outside 127.0.0.1.
The Gateway is the single authorised caller. All client traffic goes through the Gateway.

## Repository Layout

```
/
├── AGENTS.md                 # ← you are here — global navigation rules
├── .github/
│   ├── copilot-instructions.md   # Global rules: architecture, workflow, stack, security
│   ├── instructions/             # Scoped rules per context (applyTo globs)
│   │   ├── sdd.instructions.md
│   │   ├── auth.instructions.md
│   │   ├── gateway.instructions.md
│   │   ├── llama-cpp.instructions.md
│   │   ├── manager.instructions.md
│   │   └── testing.instructions.md
│   ├── agents/                   # Agent definitions
│   │   ├── spec-writer-agent.agent.md
│   │   ├── developer-agent.agent.md
│   │   ├── test-agent.agent.md
│   │   ├── security-reviewer-agent.agent.md
│   │   ├── docs-agent.agent.md
│   │   └── release-agent.agent.md
│   ├── skills/                   # Portable on-demand capabilities
│   │   └── run-validations/      # lint + format + typecheck + tests
│   │       └── SKILL.md
│   ├── hooks/                    # Deterministic lifecycle automation
│   │   ├── security.json         # PreToolUse: block dangerous commands
│   │   ├── format.json           # PostToolUse: ruff auto-format on .py edits
│   │   └── scripts/
│   │       ├── block-dangerous.py
│   │       └── ruff-format.sh
│   └── prompts/                  # Orchestration prompts
│       ├── run-pipeline.prompt.md     # Implementation pipeline (steps 4–6) — stops at security-approved
│       └── resume-pipeline.prompt.md  # Release pipeline (steps 8–9) — requires human-approved
├── memory/                          # Knowledge base — specifications, wiki, decisions
│   ├── specs/                    # SDD specifications — source of truth
│   ├── wiki/                     # Living project documentation (Karpathy wiki pattern)
│   │   ├── _index.md             # Content catalog
│   │   ├── _hot.md               # Recent changes and active context
│   │   └── architecture.md       # C4 architecture diagrams + threat model
│   └── decisions/                # Project decisions — date-prefixed (2026-MM-DD-title.md)
├── validations/              # E2E validation scripts — one per spec (run against full stack)
├── data/                     # Runtime SQLite volumes (auth-service/auth.db)
├── telemetry/                # Shared observability package (prometheus-telemetry)
│   ├── AGENTS.md             # Telemetry-local navigation rules
│   └── src/prometheus_telemetry/  # configure_logging, configure_tracing, get_tracer, TraceIDMiddleware
├── gateway/                  # Prometheus API Gateway (Podman :8000)
│   ├── AGENTS.md             # Gateway-local navigation rules
│   ├── api/                  # OpenAPI 3.1 contracts
│   ├── src/prometheus_gateway/
│   └── tests/
├── auth-service/             # OAuth2 auth server (Podman :9000)
│   ├── AGENTS.md             # Auth-service-local navigation rules
│   ├── src/prometheus_auth/
│   └── tests/
├── runtime/                  # llama.cpp bare-metal inference layer
│   ├── scripts/              # install-server.sh · start-server.sh · download-model.sh
│   ├── models/registry.yaml  # Model registry — IDs, paths, context lengths
│   └── manager/              # 3 packages (core/api/tui) — see manager/AGENTS.md, RM-05
│       ├── AGENTS.md         # Manager-local navigation rules
│       ├── core/src/prometheus_manager_core/  # shared domain layer
│       ├── api/src/prometheus_manager_api/    # FastAPI REST API — containerized
│       └── tui/src/prometheus_manager_tui/    # Textual TUI (5 views) + pmgr CLI
├── observability/            # Loki + Tempo + Grafana + Promtail
│   ├── AGENTS.md             # Observability-local navigation rules
│   └── ...                   # loki/, tempo/, grafana/, promtail/ configs
└── podman-compose.yml        # Gateway + Auth Service + Redis + Observability
```

## Mandatory Workflow (SDD)

```
branch (develop) → spec-writer-agent → [user: approved] → developer-agent → test-agent → security-reviewer-agent → [user: human-approved] → docs-agent → [user: resume-pipeline] → release-agent → closed
```

| Step | Agent | Spec status | Gate |
|------|-------|-------------|------|
| 1. Branch from `develop` | — | — | Always from `develop` |
| 2. Write spec (`memory/specs/NNN-name.md`) | `spec-writer-agent` | `draft` | Never write specs manually |
| 3. Wait for approval | **User** | `approved` | **No code without `approved`** |
| 4. Implement all ACs | `developer-agent` | `implementing` → `code-complete` | Never write feature code directly |
| 5. Tests + pre-push hook | `test-agent` | `testing` → `tests-passed` | All checks must pass |
| 6. Security review | `security-reviewer-agent` | `reviewing` → `security-approved` | No CRITICAL/HIGH findings |
| 7. Human review | **User** | `human-approved` | **No docs without `human-approved`** |
| 8. Update docs | `docs-agent` | `documenting` → `implemented` | — |
| 9. Release | `release-agent` | `releasing` → `released` | User must run `/resume-pipeline` |

## Spec Lifecycle

```
draft → approved
  → implementing → code-complete
  → testing → tests-passed
  → reviewing → security-approved
  → [human-approved]
  → documenting → implemented
  → releasing → released
  → closed
```

Each agent sets **two** status values: one on start, one on successful finish.
The next agent in the pipeline verifies the **finish** status of the previous one before starting.

Each spec frontmatter carries:
- `status` — current lifecycle stage
- `current-agent` — agent actively working on it (`""` when idle between stages)
- `pipeline-log` — immutable list of every stage transition with agent + timestamp

**Parallel specs**: multiple specs can be `implementing` simultaneously, each on its own `feat/NNN-*` branch.

## Agents Available

| Agent | Purpose |
|-------|---------|
| `spec-writer-agent` | Write or refine a feature spec |
| `developer-agent` | Implement all ACs from an approved spec |
| `test-agent` | Write/update tests and pre-push hook validations |
| `security-reviewer-agent` | Review changed files for security findings |
| `docs-agent` | Reconcile spec and update AGENTS.md / README after human approval |
| `release-agent` | Commit · push · PR feat→develop · merge · PR develop→main · merge · tag · GitHub release |
| `wiki-sync-agent` | Periodically scan all closed specs and update `memory/wiki/` + `memory/decisions/` with any missing cross-cutting knowledge. Run after a batch of specs close or on a scheduled basis |
| `validation-triage-agent` | Diagnose errors from deployment validation runs (`scripts/validate.sh`, E2E tests, manual checks on RHEL). Classifies root cause (code bug / spec gap / environment / security) and routes to the correct agent. Invoke with 'triage this error', 'validation failed', or paste the error output |
| `spec-writer-agent` includes OpenAPI design | When spec touches API endpoints, step 3 generates `gateway/api/NNN-feature.yaml` |

## Git Branching Policy

**Never push directly to `main` or `develop`.**

```
main          ← production-ready (protected, tagged releases)
  ↑ PR (squash merge, passing CI + 1 approval)
develop       ← integration (protected, always green CI)
  ↑ PR (merge commit, passing CI)
feat/NNN-*    ← one branch per spec, branched from develop
```

| Branch prefix | Purpose |
|---------------|---------|
| `feat/NNN-title` | New feature — tied to a spec number |
| `fix/NNN-title` | Bug fix — tied to a spec number |
| `chore/` | Dependency updates, tooling, workflow improvements |
| `docs/` | Documentation-only (memory/wiki/, memory/decisions/, AGENTS.md, README) |

## Build & Test

```bash
# Install all deps (workspace root)
uv sync

# Run all tests
uv run pytest gateway/tests/ -v
(cd auth-service && uv run pytest tests/ -v)
(cd runtime/manager && uv run pytest tests/ -v)
bash runtime/tests/test_runtime_scripts.sh

# Pre-push hook (lint + format + typecheck + tests)
git config core.hooksPath .githooks   # install once per clone

# End-to-end (requires full stack — see below)
uv run validations/e2e_test.py

```bash
# 1. Start llama-server (bare-metal)
source runtime/mac-llama3-1b.env && bash runtime/scripts/start-server.sh

# 2. Start containers
podman machine start
podman compose -f podman-compose.yml up --build -d

# 3. Run E2E test
uv run validations/e2e_test.py
```

## Podman VM Management

```bash
podman machine start
podman system connection default podman-machine-default-root
podman ps
```

### Corporate TLS interception CA (Zscaler or similar)

Every new Podman VM needs the corporate CA injected before `podman build` can pull packages:

```bash
ssh -i ~/.local/share/containers/podman/machine/machine \
    -p <VM_PORT> -o StrictHostKeyChecking=no root@127.0.0.1

# Inside the VM:
cat > /etc/pki/ca-trust/source/anchors/corporate-tls-interception.pem << 'CERT'
<paste PEM certificate here>
CERT
update-ca-trust && exit
```

### Root `.env` — Compose bind-mount variables

All host-path bind-mounts in `podman-compose.yml` must be declared in the **root `.env`**.
Without them, Compose silently creates a directory at the mount target → `IsADirectoryError`.

```bash
JWT_PUBLIC_KEY_HOST_PATH=/absolute/path/to/public.pem
JWT_PRIVATE_KEY_HOST_PATH=/absolute/path/to/private.pem
TLS_CERT_HOST_PATH=/absolute/path/to/gateway/certs/dev.crt
TLS_KEY_HOST_PATH=/absolute/path/to/gateway/certs/dev.key
AUTH_TLS_CERT_HOST_PATH=/absolute/path/to/auth-service/certs/dev.crt
AUTH_TLS_KEY_HOST_PATH=/absolute/path/to/auth-service/certs/dev.key
CONTAINER_LOG_HOST_PATH=/absolute/path/to/runtime/container-logs
MANAGER_LOG_HOST_PATH=/absolute/path/to/runtime/logs
AUTH_JWT_ISSUER=https://auth.example.com
GRAFANA_SECRET_KEY=replace-with-a-long-random-string
GRAFANA_ADMIN_PASSWORD=replace-with-strong-password
```

**Rule**: every new bind-mount source added to `podman-compose.yml` must have a corresponding entry here.

## Tools Available Locally

| Tool | Path | Notes |
|------|------|-------|
| `llama-server` | `~/.local/bin/llama-server` | Built from source (Metal GPU on macOS) |
| `gh` | `~/.local/bin/gh` | Authenticated against your Git host |
| `cmake` | `uv tool run cmake` | No system cmake needed |
