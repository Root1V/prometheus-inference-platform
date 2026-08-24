# Prometheus — Roadmap / Backlog

Living backlog of improvements and new features. Unlike `memory/specs/`, items here are
**not** run through the full SDD pipeline (spec-writer → developer → test →
security-reviewer → human-approved → docs → release) — that process is kept for
already-shipped, security-critical work. Backlog items below are implemented directly,
**one branch per item**, to move faster and spend fewer tokens per change.

**The only non-negotiable rule carried over from SDD**: every branch that closes an item
must update `README.md` and the relevant page(s) under `memory/wiki/` in the same PR/commit
set — this file is not a substitute for real docs, it's a queue.

Branch naming: `feat/RM-<id>-<slug>` (e.g. `feat/RM-05-manager-tui-api-split`).

Status legend: `todo` · `in-progress` · `blocked` · `done`

---

## Priority order and rationale

Ordered so that foundational/refactor work and cheap risk-reducing research come first,
features that depend on them come after, and pure-polish items come last. Items marked
**(added)** were not in the original request — they came out of the repo audit and are
folded in here per your "muchos más que vayas encontrando."

| # | Item | Status | Depends on |
|---|------|--------|------------|
| [RM-01](#rm-01-restore-ci-now-that-the-repo-is-public-added) | Restore CI on GitHub Actions (added) | done | — |
| [RM-02](#rm-02-extend-pre-push-hook-to-managertelemetry-added) | Extend pre-push hook to `manager`/`telemetry` (added) | done | RM-01 |
| [RM-03](#rm-03-pick-a-real-license-added) | Pick a real LICENSE (added) | done | — |
| [RM-04](#rm-04-dependency-vulnerability-scanning-added) | Dependency vulnerability scanning (added) | done | RM-01 |
| [RM-05](#rm-05-split-manager-tui-from-its-rest-api-item-4) | Split manager's TUI from its REST API (item 4) | done | — |
| [RM-06](#rm-06-research-the-best-inference-serving-stack-item-7) | Research best inference-serving stack per hardware (item 7) | done | — |
| [RM-07](#rm-07-fine-grained-per-model-authorization-scopes-item-2) | Fine-grained per-model authorization scopes (item 2) | todo | — |
| [RM-08](#rm-08-distributed-inference-across-multiple-hosts-item-5) | Distributed inference across multiple hosts (item 5) | todo | RM-05, RM-06 |
| [RM-09](#rm-09-multi-modal-model-support-item-6) | Multi-modal model support: VLM/audio/image/video/embeddings (item 6) | todo | RM-05, RM-06 |
| [RM-10](#rm-10-gateway-admin-dashboard-item-3) | Gateway admin dashboard (item 3) | todo | RM-05 |
| [RM-11](#rm-11-auth-module-dashboard-redesign-item-1) | Auth module dashboard redesign (item 1) | todo | RM-07 (do together) |
| [RM-12](#rm-12-e2e-llm-tracing-with-langfuse-item-8) | E2E LLM tracing with Langfuse (item 8) | todo | — |

Why this order, briefly:
- **RM-01 to RM-04** are cheap, low-risk, and matter more now that this moved from an
  internal GHE repo to a public one (no CI was running at all; no license; no dependency
  scanning on security-sensitive code like the gateway/auth-service).
- **RM-05** (manager TUI/API split) blocks RM-08, RM-09, and RM-10 — building distributed
  support, new modalities, or a gateway dashboard on top of the current mixed
  TUI+API module means redoing that work later.
- **RM-06** (engine research) is cheap (mostly investigation) and should inform how RM-08
  and RM-09 are designed, so it goes before them even though it was item 7 in your list.
- **RM-07** (fine-grained scopes) is independent but should land before RM-11 (auth
  dashboard redesign) so the new UI is built once against the final permission model
  instead of twice.
- **RM-11** (auth UI) and **RM-10** (gateway dashboard) are placed after their backend
  dependencies so they're not rebuilt.
- **RM-12** (Langfuse) is purely additive on top of the existing OTel/Tempo pipeline —
  lowest urgency, do whenever.

---

## RM-01 — Restore CI on GitHub Actions (added) — `done`

**Why**: GitHub Actions workflows existed (`ci-pr.yml`, `cd-develop.yml`, `cd-main.yml`)
but were deleted because Actions was disabled on the old internal GHE instance this project
originated on. That
constraint no longer applies on the new public GitHub repo. Right now nothing runs
server-side on a PR — only the local `.githooks/pre-push`, which is opt-in and skippable.

**Done**: added `.github/workflows/ci.yml` — it runs `bash .githooks/pre-push` on every
PR and on push to `main`, so local and CI checks can't drift apart (single source of
truth, no duplicated step list). Scope intentionally kept to exactly what the hook
already does — gateway + auth-service (lint/format/mypy/pytest) + the two bash test
suites. Extending it to `runtime/manager`/`telemetry` is RM-02, on purpose, so that
change and the formatting fixes it needs land together.

Running the hook end-to-end to validate this surfaced two pre-existing, unrelated bugs,
fixed in the same branch since they blocked CI going green:
- `runtime/tests/test_runtime_scripts.sh` AC-11 called bare `python3 -c "import yaml..."`
  — fails on any machine/runner without a global `pyyaml` (this repo has no top-level
  Python env, only per-package `uv` venvs). Fixed to `uv run --with pyyaml python3 ...`.
- `scripts/tests/test_scripts_023.sh` AC-1 asserted `gateway/.env.podman.example` must
  have an active (uncommented) RHEL-path `REQUESTS_CA_BUNDLE` — stale since the earlier
  Ubuntu/DGX work correctly commented it out there (RHEL and Debian/Ubuntu CA bundle
  paths differ, and `gateway/.env.podman.example` is now a shared, not RHEL-only,
  template). Narrowed the check to `.env.redhat.example` and `auth-service/.env.example`.

## RM-02 — Extend pre-push hook to `manager`/`telemetry` (added) — `done`

**Why**: `.githooks/pre-push` only lints/type-checks/tests `gateway/` and
`auth-service/`. `runtime/manager` has its own `pyproject.toml` and a 9-file test suite
that nothing currently enforces — confirmed drift already exists (`ruff format --check
runtime/manager/` currently fails on 12 test files). `telemetry/` isn't covered either.

**Done**: added lint + format + pytest for `runtime/manager`, and lint + format + mypy +
pytest for `telemetry` (both to `.githooks/pre-push`; `ci.yml` inherits them for free
since it just runs the hook). Fixed the pre-existing drift to get there: 78 ruff findings
and 12 unformatted files in `manager` (all mechanical — unused imports, line length,
`contextlib.suppress`, combined `with` statements, one `noqa: E402` for two imports that
must stay after an intentional early `configure_logging()`/`truststore` setup), plus 6
ruff findings and 1 stale `type: ignore` in `telemetry`. All 142 manager tests and 30
telemetry tests still pass after the fixes.

**Deliberately not done**: `mypy` for `runtime/manager`. Its `pyproject.toml` already
declares `strict = true` but it was never actually enforced — running it surfaced ~86
pre-existing errors, mostly missing type annotations on the `pmgr` CLI's click commands
in `cli/main.py`. Fixing that now would mean deep-editing a process-lifecycle-controlling
module outside this change's scope, so it's carried forward as required scope for
**RM-05** (which restructures these exact files) instead of being fixed twice.

## RM-03 — Pick a real LICENSE (added) — `done`

**Why**: README currently says `TBD` after the original internal-use notice was removed. A
repo without a license is "all rights reserved" by default, which may not be what you want.

**Done**: Apache-2.0 (user's choice — explicit patent grant vs MIT). Added `LICENSE` at
the repo root, updated the README section to link it. Per-package `pyproject.toml` files
(`gateway`, `auth-service`, `telemetry`, `runtime/manager`) don't declare a `license`
field — left alone, out of scope here; add if any of them ever get published to PyPI.

## RM-04 — Dependency vulnerability scanning (added) — `done`

**Why**: no SCA tool runs today. `gateway`/`auth-service` sit directly in the security
path (JWT, crypto, bcrypt) and are now publicly visible.

**Done**: both. `.github/dependabot.yml` — weekly `uv` ecosystem updates against the
single workspace `uv.lock` (root, covers all four packages), plus weekly
`github-actions` updates. And a `pip-audit` step in `ci.yml`, run as
`uv run --with pip-audit pip-audit -l` (audits the already-synced workspace venv
in place — no separate `uv export`/ephemeral-venv dance needed). Currently reports
no known vulnerabilities. Kept out of `.githooks/pre-push` deliberately: it calls
PyPI/OSV over the network, which shouldn't be able to block a local `git push`.

## RM-05 — Split manager's TUI from its REST API (item 4) — `done`

**Why (revised from the original write-up)**: initial code review found that module-level
separation (`api/`, `tui/`, `cli/`) already existed and imports were already clean — the
real problem the user meant was **packaging**: one `uv` package/`pyproject.toml` for
everything meant the API's container image installed Textual/Rich for nothing, and there
was no way to package the CLI+TUI as a standalone binary without also dragging in
fastapi/uvicorn/python-jose. As distributed hosts (RM-08) and new model modalities (RM-09)
get added, this would only get worse.

**Done**: split `runtime/manager` into three separate `uv` workspace members, each with
its own `pyproject.toml` and version:
- `runtime/manager/core` → `prometheus-manager-core` — domain layer (config, registry,
  scanner, lifecycle, capacity, downloader, telemetry re-exports). Zero dependency on
  fastapi, click, or textual.
- `runtime/manager/api` → `prometheus-manager-api` — FastAPI app + routes + auth, plus a
  new thin `pmgr-api` CLI entrypoint (moved out of the old `pmgr serve` command). This is
  what `runtime/manager/api/Dockerfile` now builds — Textual/Rich never enter the image.
- `runtime/manager/tui` → `prometheus-manager-tui` — the Textual app/views/widgets plus
  the `pmgr` CLI (status/start/stop/pause/resume/restart/register/unregister/download/
  deregister/tui). No fastapi/uvicorn dependency at all — ready to be packaged as a
  standalone binary later without pulling in API-only deps.

All 142 pre-existing tests still pass, split 94/10/38 across core/api/tui. `.githooks/
pre-push` and `ci.yml` (which just runs the hook) now lint/format/type-check/test all
three independently.

Also paid down the ~86 pre-existing `mypy --strict` errors surfaced during RM-02 (mostly
missing annotations in the old `cli/main.py`, plus a handful in `config.py`/`downloader.py`/
`auth.py`/`routes.py`/every TUI view) — `mypy --strict` now passes clean on all three
packages, and the hook enforces it going forward.

Updated: `runtime/manager/AGENTS.md`, `AGENTS.md`, `README.md` (repo layout diagrams +
test commands), `memory/wiki/deployment.md` and `memory/wiki/model-registry.md`
(`pmgr serve` → `pmgr-api`), `podman-compose.yml` / `podman-compose-ubuntu-dgx.yml`
(Dockerfile path), `scripts/install-rhel.sh` / `scripts/install-ubuntu-dgx.sh` /
`scripts/validate-ubuntu-dgx.sh` (`pmgr serve` → `pmgr-api`), and the
`scripts/tests/test_scripts_024.sh` assertions that checked the old command/path.

## RM-06 — Research the best inference-serving stack (item 7) — `done`

**Why**: `llama-server` is the only backend today. It may not be the best fit for every
hardware target (Apple Silicon vs NVIDIA DGX) or every future modality (RM-09).

**Done**: [memory/wiki/inference-engines.md](wiki/inference-engines.md) — full comparison
of llama.cpp, vLLM, MLX, and SGLang across Mac (M4 Max) / DGX Spark / generic Linux-NVIDIA,
covering throughput, quantization format support, modality coverage, and operational
complexity for a process-spawning manager. Bottom line: **mixed strategy, not a single
engine** — MLX on Mac, vLLM (or SGLang) on DGX Spark and generic Linux servers, llama.cpp
kept everywhere as the simple/single-user fallback. No engine covers every target modality
on every piece of hardware; the real design axis for RM-08/RM-09 is per-hardware backend
selection, not per-modality. The page also spells out concretely what this adds to the
manager's job — a second "heavy Python server" launch shape alongside the current
"spawn a binary" one, and new `registry.yaml` fields (`backend`, `quant_format`) — which
RM-08 and RM-09 should treat as their starting brief rather than re-deriving.

## RM-07 — Fine-grained per-model authorization scopes (item 2)

**Why**: today auth-service scopes are coarse (e.g. `inference:read`) — a client can call
any model the gateway exposes. Need per-model (and eventually per-modality: LLM/VLM/
multimodal) access control.

**Scope**: extend the client/scope model in `auth-service` (schema + JWT claims) to carry
model-level (or model-group-level) grants, and enforce them in the gateway's routing layer
alongside the existing scope check.

## RM-08 — Distributed inference across multiple hosts (item 5)

**Why**: today the manager only starts/monitors `llama-server` processes on the local
host. You want to pool capacity across multiple machines (MacBook Pro M4 Max, NVIDIA DGX
Spark, etc.).

**Scope**: extend the manager (post RM-05 split) with a notion of remote backends/nodes —
registration, health checks, and routing across hosts — informed by whatever RM-06
recommends per hardware type. Likely the largest item in this backlog; may be worth
breaking into its own sub-backlog once RM-05/RM-06 land.

## RM-09 — Multi-modal model support (item 6)

**Why**: today the platform only serves text LLMs. You want VLM, multimodal, audio,
image-generation, video-generation, and embedding models.

**Scope**: extend the manager's registry/lifecycle and the gateway's routing/API surface
to model different modalities (not all of them expose an OpenAI-chat-shaped API) — informed
by RM-06's engine research, since different modalities likely need different serving
backends (e.g. diffusers/ComfyUI for image/video, whisper.cpp for audio).

## RM-10 — Gateway admin dashboard (item 3)

**Why**: no visual way today to see running instances, downloaded models, or manage
inference lifecycle — only `pmgr` TUI (bare-metal) and raw API calls.

**Scope**: a web dashboard in the gateway (or a new UI module) showing live instances,
downloaded models, and start/stop/restart controls — consuming the manager REST API
cleaned up in RM-05.

## RM-11 — Auth module dashboard redesign (item 1)

**Why**: current auth UI needs an enterprise-grade redesign.

**Scope**: redesign the existing auth-service web UI. Do this together with or right after
RM-07 so the new UI is built once against the final (fine-grained) permission model instead
of being redone.

## RM-12 — E2E LLM tracing with Langfuse (item 8)

**Why**: current observability (Loki/Tempo/Grafana + OTel, specs 018/020/021/022) is
generic request tracing, not LLM-specific (prompts, completions, token usage, evals).

**Scope**: integrate Langfuse (self-hosted, matches the "open" requirement) alongside the
existing telemetry package — fine-grained end-to-end trace of prompt → model → completion,
without duplicating what Tempo already captures at the HTTP layer.

---

## Adding new items

Append a new row to the table with the next `RM-NN` id and a new `## RM-NN — ...` section
below, following the same shape (Why / Scope). Re-sort the table if the new item's
priority isn't "last."
