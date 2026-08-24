# Repository operating rules — Prometheus

## Mission

This repository implements **Prometheus**, a local SLM inference platform running on bare-metal.
Prioritise small, safe, and verifiable changes. Every feature starts with an approved spec.

## Architecture

```
[Client Apps — Podman containers]
        │  REST / SSE
        ▼
[gateway — Podman :8000]     ← JWT validation · scope enforcement · rate limiting · metering
        │
        ▼
[auth-service — Podman :9000] ← client registration · OAuth2 client_credentials · JWKS
[llama.cpp — bare-metal :8080] ← models on host GPU/CPU — NEVER exposed outside 127.0.0.1
```

- Gateway: `gateway/src/prometheus_gateway/`
- Auth service: `auth-service/src/prometheus_auth/`
- Runtime llama.cpp: `runtime/` — bare-metal inference layer, composed of three sub-modules:
  - **Manager TUI** (`runtime/manager/src/prometheus_manager/tui/`) — terminal UI that starts, stops, and monitors llama.cpp inference server processes
  - **Inference API** (`runtime/manager/src/prometheus_manager/api/`) — REST API that exposes all models currently running across the active inference servers
  - **Manager CLI** (`runtime/manager/src/prometheus_manager/cli/`) — command-line interface (`pmgr`) for managing inference servers and models
- Specs (source of truth): `memory/specs/`
- OpenAPI contracts: `gateway/api/`
- Observability stack: `podman-compose.yml` (Loki + Tempo + Grafana + OpenTelemetry)

**Critical constraint**: llama.cpp is NEVER exposed outside `127.0.0.1`. The Gateway is the sole authorised caller.

## Mandatory workflow (Spec Driven Development)

No code without an approved spec. Steps are sequential — never skip or reorder.

```
branch (develop) → spec-writer-agent → [user: approved] → developer-agent → test-agent → security-reviewer-agent → [user: human-approved] → docs-agent → [user: resume-pipeline] → release-agent → closed
```

1. **Branch** from `develop`: `git checkout -b feat/NNN-name`
2. **Write spec** — invoke `spec-writer-agent` agent to generate `memory/specs/NNN-name.md` with `status: draft`. Never write specs manually.
3. **Wait for approval** — user sets `status: approved`. No implementation starts before this gate.
4. **Implement** — invoke `developer-agent` to implement all acceptance criteria from the spec. Never write feature code directly outside of an agent.
5. **Test** — invoke `test-agent` to write/update tests covering every acceptance criterion and to write/update the pre-push hook validations if affected, then run the full pre-push hook suite (lint + format + typecheck + tests). All checks must pass before proceeding.
6. **Security review** — invoke `security-reviewer-agent` agent on all changed files. Fix any findings before proceeding.
7. **Human review** — user reviews the deliverable and sets `status: human-approved`. Iterate back to step 4 if changes are needed.
8. **Document** — invoke `docs-agent` to reconcile the spec and update affected `AGENTS.md` files and `README.md`.
9. **Release** — invoke `release-agent` by running `/resume-pipeline`. Commits, pushes, PRs, merges, tags, and creates GitHub release.

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

| Status | Set by | Meaning |
|--------|--------|---------|
| `draft` | `spec-writer-agent` | Spec being written, awaiting user approval |
| `approved` | **User** | Cleared for implementation — first human gate |
| `implementing` | `developer-agent` | AC implementation in progress |
| `code-complete` | `developer-agent` | All ACs implemented successfully — ready for testing |
| `testing` | `test-agent` | Tests being written and hook suite running |
| `tests-passed` | `test-agent` | All tests written, pre-push hook suite green |
| `reviewing` | `security-reviewer-agent` | Security review in progress |
| `security-approved` | `security-reviewer-agent` | No CRITICAL/HIGH findings — awaiting human review |
| `human-approved` | **User** | Deliverable reviewed and accepted — second human gate. Cleared for docs and release |
| `documenting` | `docs-agent` | Docs and spec content being updated |
| `implemented` | `docs-agent` | Spec reconciled, AGENTS.md and README updated |
| `releasing` | `release-agent` | Commit · push · PRs · merges · tag · release in progress |
| `released` | `release-agent` | Merged to main, tagged, GitHub release created |
| `closed` | **User / ops** | Verified in production |

Each spec also carries `current-agent` (who holds it now) and `pipeline-log` (immutable history of every stage transition). See `memory/specs/NNN-*.md` frontmatter.

## Branch naming

| Prefix | Purpose |
|--------|---------|
| `feat/NNN-title` | New feature — tied to spec number |
| `fix/NNN-title` | Bug fix — tied to spec number |
| `chore/title` | Tooling, dependencies, CI, workflow improvements |
| `docs/title` | Documentation only |

## Rules

### Workflow (non-negotiable)

- Never write feature code, tests, or spec documents directly. Always delegate to the appropriate agent.
- Never invoke `release-agent` without explicit user instruction (run `/resume-pipeline`).
- Never start implementation before `status: approved`.
- Never start documentation before `status: human-approved`.
- Never push directly to `main` or `develop`.
- Steps are sequential — never skip or reorder.

### Agent behaviour (non-negotiable)

- **Never fabricate**: do not invent file paths, tool names, API endpoints, command flags, or configuration values. If information is missing, read the relevant file or ask the user — never guess.
- **Skill invocation**: when a task matches a skill in `.github/skills/`, load the full `SKILL.md` first, then follow its procedure step-by-step. Never skip loading the skill.
- **Structured output**: prefer tables and bullet lists over prose. Always report what was done and what the next step is.
- **Prefer reading over assuming**: before modifying a file, read it. Before claiming a behaviour exists, verify it in the source.

### Coding

- **Language**: Python (gateway, auth-service, manager). Shell/Bash (runtime scripts). YAML (config, OpenAPI).
- **Package manager**: `uv` only. Never use `pip install` directly — `uv.lock` must stay in sync.
- **Secrets**: Always via environment variables. Never hardcoded. `.env` files are gitignored; use `.env.example` as committed template.
- **Error responses**: RFC 9457 Problem Details for all API errors.
- **Logging**: Structured JSON. Always include `request_id`, `user_id`, `model`, `tokens_used` in inference logs.
- **Tracing**: OpenTelemetry SDK — use `tracer.start_as_current_span()`. Service names follow `prometheus-{service}`.
- **Spec references**: Code comments link to the originating spec: `# See memory/specs/NNN-feature.md`.
- **Imports**: prefer absolute imports within each package.

### Security (non-negotiable)

- No unauthenticated endpoints except `/health` and `/metrics`.
- JWT validation order: signature → `exp` → `iss` → `aud` → `sub` → `scope`. Never skip a step.
- Always RS256 — never HS256.
- Rate limiting enforced per `user_id` AND per `client_id` — not just by IP.
- Validate and sanitise all inputs before forwarding to llama.cpp. Strip system-role override attempts.
- Never log Authorization headers or raw JWT strings.
- llama.cpp must always bind to `127.0.0.1`, never `0.0.0.0`.
- No new dependencies without explicit justification.
- Do not modify CI pipelines or secrets without explicit user request.

## Official stack

| Layer | Technology |
|-------|-----------|
| Runtime | Python 3.11+, FastAPI, Uvicorn |
| Auth | OAuth2 Client Credentials, RS256 JWT (python-jose / PyJWT) |
| Inference | llama.cpp (`llama-server`) — bare-metal, Metal GPU on macOS |
| Containers | Podman + podman-compose |
| Observability | OpenTelemetry SDK, Loki, Tempo, Grafana, Promtail |
| Rate limiting | Redis (via podman-compose) |
| Testing | pytest, pytest-asyncio, httpx (AsyncClient) |
| Linting | ruff check + ruff format |
| Type checking | mypy |
| API contracts | OpenAPI 3.1 YAML |

## Validation commands

```bash
# Full validation suite (lint + format + typecheck + tests) — use the /run-validations skill
# or run the pre-push hook directly:
bash .githooks/pre-push

# End-to-end (requires full stack)
uv run validations/e2e_test.py
```

> See `.github/skills/run-validations/SKILL.md` for the per-module command reference.

## Definition of done

A task is complete when ALL of the following are true:
- [ ] Spec status is `implemented` (or `closed` if verified in prod)
- [ ] All acceptance criteria in the spec have a passing test
- [ ] Pre-push hook suite passes: `ruff check`, `ruff format --check`, `mypy`, and all tests
- [ ] `security-reviewer-agent` has been run and all findings addressed
- [ ] `docs-agent` has updated the affected official documents (specs status, README)
- [ ] Structured logging present for new inference paths
- [ ] PR description includes: cause, impact, risks, validations run

## Commit message style

```
<type>(<scope>): <short description>

# Types: feat | fix | chore | docs | test | refactor | style
# Scope: gateway | auth | runtime | manager | observability | ci | spec
# Examples:
feat(gateway): add per-model rate limiting
fix(auth): reject tokens with missing aud claim
chore(ci): add ruff format check to pre-push hook
docs(spec): mark spec-022 as implemented
```

## PR expectations

Every PR description must include:
1. **Root cause** — what problem it solves and why
2. **Impact** — which components change and how it affects the system
3. **Risks** — what could go wrong, external dependencies
4. **Validations run** — test/lint/typecheck commands executed and their results

## Improvement Loop

After completing any task, if the agent identifies a gap, ambiguity, or missing context in a system configuration file, it MUST suggest a concrete improvement.

### What to improve

| File type | Improve when... |
|-----------|----------------|
| `.github/instructions/*.instructions.md` | A rule was ambiguous, missing, or required extra clarification to follow correctly |
| `.github/skills/*/SKILL.md` | A procedure step was incomplete, failed on an edge case, or required a step not in the skill |
| `.github/agents/*.agent.md` | The agent process had a gap, a gate missed a real failure, or a constraint was too broad/narrow |

### What NOT to improve via this loop

- Individual `memory/specs/NNN-*.md` files — these are updated exclusively by `docs-agent` following the SDD reconciliation process.
- Source code, tests, or config files — improvements to those always require an approved spec.

### Format for suggesting improvements

At the end of your response, add an optional section:

```
## Suggested improvement
File: .github/instructions/sdd.instructions.md
Reason: Rule 3 does not cover the case where a spec has no API contract section.
Proposed change: Add "If the spec has no API endpoints, write 'N/A' in the API Contract section."
```

Suggestions are **proposals only** — the user decides whether to apply them.

