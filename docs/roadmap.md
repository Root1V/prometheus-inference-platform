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

For current status and the full item list, see the index in [`roadmap.md`](../roadmap.md).
Sequencing rationale that isn't captured there:
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
- **RM-14 to RM-18** came out of a 2026-08-25 discussion about whether the admin dashboard
  should stay split by backend service (gateway vs auth-service) or become one consolidated
  platform dashboard — researching comparable products (LiteLLM Proxy, Portkey, Helicone,
  OpenRouter) surfaced the sections they all converge on. RM-14 to RM-16 are real gaps for
  this project; PRM-17 was speculative and still is. **RM-18 is no longer speculative** — see
  below, it was merged into RM-11 once a concrete requirement showed up.
- **RM-19 to RM-23** came out of a 2026-08-26 requirements pass and are grouped by concern:
  - **Auth & Users** (RM-11, expanded): dashboard user/role management with two login modes.
  - **Nodes & instance provisioning** (RM-20, RM-21): a real node registry, and instance
    creation that reads from it instead of manual field entry. RM-21 depends on RM-20.
  - **Dashboard identity & orientation** (RM-19, RM-22): branding (logo/favicon) and a home
    page — pure UX, no new backend data model.
  - **Live platform visibility** (RM-23): who/what is connected right now, distinct from
    RM-15's historical usage aggregates.
- **"Dashboard / UX" block** (2026-08-27, after the RM-15/32/33/34 usage series shipped):
  the remaining `todo` items that are pure admin-ui work — RM-13, RM-14, RM-16, RM-23,
  RM-26, RM-27, RM-31 — grouped together and worked one branch at a time, same rhythm as
  the usage series. Excludes RM-12 (Langfuse — backend tracing integration), PRM-17
  (guardrails — speculative policy feature, not UX), and PRM-25 (node SSH credentials —
  infra/security, not UX). Started with **RM-26** (simplest, no backend dependency).

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

## RM-07 — Fine-grained per-model authorization scopes (item 2) — `done`

**Why**: today auth-service scopes are coarse (e.g. `inference:read`) — a client can call
any model the gateway exposes. Need per-model (and eventually per-modality: LLM/VLM/
multimodal) access control.

**Also found while scoping this**: `inference:read`/`inference:stream` were documented
scopes but were **never actually enforced** on `POST /v1/chat/completions` — any valid
JWT could already call any model. User chose (via AskUserQuestion) to fix this gap in the
same change, and to make the new per-model check **strict/deny-by-default** rather than
backward-compatible.

**Done**:
- `auth-service`: `model:<id>` scopes, additive to the fixed `VALID_SCOPES` enum —
  validated by pattern (`schemas.is_valid_scope`/`invalid_scopes`), not membership, since
  model ids are open-ended and live in the manager's registry, not auth-service. Wired
  into client registration/update (`admin.py`, `admin_ui.py` — a plain space-separated
  "Model access" text field, not a full redesign; RM-11 owns the real dashboard UI) and
  token issuance (`oauth2.py`). No DB migration needed — `allowed_scopes` was already a
  free-text space-separated column.
- `gateway`: `Claims.has_model_scope(model_id)`, enforced in `router.py`'s
  `chat_completions` handler — checked *after* the existing model-existence lookup (not
  before: `GET /v1/models` is already public/unauthenticated, so there's no secret to
  protect by hiding existence behind authorization, and doing it this way keeps
  unknown-model tests simple). Requires `inference:read`/`inference:stream` (now actually
  enforced) **and** `model:<id>` for the specific model requested.
- Confirmed the Web Chat UI (`ui/router.py`) is a separate proxy path that never calls
  `chat_completions` — unaffected by this change.

**⚠ Deployment/migration impact**: deny-by-default means **every client registered before
this shipped has zero model access** until an admin adds `model:<id>` scopes to it — see
[memory/wiki/auth-model.md](wiki/auth-model.md#per-model-scopes-rm-07) for the grant
command. Roll this out with that in mind; it will look like a total inference outage for
existing clients if deployed without a follow-up grant pass.

19 new tests (auth-service: scope validation, registration, token issuance; gateway:
`has_model_scope`, all-4 enforcement-order cases). All 84 auth-service + 119 gateway tests
pass (75/110 pre-existing — every existing gateway test token needed a `model:<id>` scope
added since the endpoint is now enforced). `mypy --strict` clean on both packages' `src/`.

## RM-08 — Distributed inference across multiple hosts (item 5) — `done`

**Why**: today the manager only starts/monitors `llama-server` processes on the local
host. You want to pool capacity across multiple machines (MacBook Pro M4 Max, NVIDIA DGX
Spark, etc.).

**Phase 1 done — multi-backend lifecycle (single host)**: before hosts can be distributed,
the manager needed to know how to launch more than one *kind* of server — this is the
"per-hardware backend selection" axis RM-06 called out as the harder, more foundational
question, and it turned out to be the more urgent gap. Implemented in `core`:
- `RegistryEntry.backend` (`llama_cpp`/`mlx`/`vllm`/`sglang`), with `path` validation
  relaxed for the three new backends (they commonly load a HF repo id directly, not a
  local `.gguf` file).
- `lifecycle.py`: one command-builder function per backend, dispatched on `entry.backend`.
  `llama_cpp` and `mlx` are verified against real binaries (`mlx_lm.server --help`, and a
  full live `register` → `start` → `status` → `stop` run against
  `mlx-community/SmolLM2-135M-Instruct` on this Mac). `vllm`/`sglang` command construction
  follows their documented CLIs but is **not verified against real installs** — both need
  CUDA, unavailable in this dev environment. Validate on the DGX Spark/Linux target before
  relying on them.
- `scanner.py`: process recognition generalized to all four backends. Alias resolution now
  comes primarily from the PID file the manager already writes (`{pid_dir}/{model_id}.pid`)
  rather than backend-specific cmdline flags — necessary because `mlx_lm.server` has no
  `--alias`/`--served-model-name` equivalent at all.
- `config.py`: new `[backends.mlx/vllm/sglang]` sections for per-backend binary path and
  start timeout (vLLM/SGLang default to 300s vs. llama.cpp's 60s — heavier startup per
  RM-06's findings).
- `pmgr register --backend`, and a Backend column in the CLI tables, the Textual Registry
  view (table + detail panel), and the Instances view table.

**Phase 2 done — multi-host distribution via the gateway**: chosen architecture is "remote
manager + shared registry" — each host runs its own bare-metal `pmgr-api` (RM-05) with its
own `registry.yaml`; there is no new central orchestrator process. The gateway is the only
component aware of the whole fleet, and only as a *reader*:
- `gateway/config.py`: new `MANAGER_NODES` setting (`"name1=url1,name2=url2,..."`),
  resolved via `Settings.resolved_manager_nodes`. Takes priority over the existing
  single-node `MANAGER_URL`, which keeps working unchanged for existing deployments.
- `gateway/models/manager_sync.py`: `ManagerRegistrySync` now polls every configured
  node's `/v1/backends` concurrently (`asyncio.gather`) instead of a single manager. The
  SSRF-prevention host allowlist — previously a fixed loopback/container-alias list — is
  now dynamic: base hosts ∪ the hostname of every explicitly configured `MANAGER_NODES`
  entry, so remote routing is possible without opening the gateway up to arbitrary hosts.
  One node being unreachable only drops *that node's* models from the registry on the next
  poll (partial availability); a `model_id` collision across two nodes keeps the
  first-seen entry and logs a warning rather than silently overwriting.
- `gateway/models/registry.py`: `ModelEntry.node` field records which host serves a model
  (observability only, not used for routing).
- Each node's own `pmgr-api` must set `PMGR_PROXY_HOST` to its real reachable
  hostname/IP (not loopback) so its `/v1/backends` response reports a `backend_url` the
  gateway can actually route to. Full details and the operational setup: see
  `memory/wiki/model-registry.md` → "Distributed nodes (RM-08 phase 2)".

10 new tests (`gateway/tests/test_manager_sync.py`): node-config parsing (empty, single,
multi, priority-over-`MANAGER_URL`, malformed), dynamic allowlist computation, multi-node
merge, partial-availability on node failure, `model_id` collision handling, untrusted-host
rejection. 129/129 gateway tests pass; `ruff`/`mypy --strict` clean.

**What's not verified**: same caveat as phase 1's vLLM/SGLang — the config parsing and
sync logic are unit-tested against mocked HTTP responses, but the actual cross-machine
`PMGR_PROXY_HOST` rewrite was not exercised against two real separate hosts (no second
machine available in this dev environment). Validate on the real fleet (Mac + DGX Spark,
etc.) before relying on it in production.

## RM-09 — Multi-modal model support (item 6) — `done` (VLM + embeddings)

**Why**: today the platform only serves text LLMs. You want VLM, multimodal, audio,
image-generation, video-generation, and embedding models.

**Scope decision**: the original ask covers four distinct API surfaces (VLM, audio,
image/video-gen, embeddings) — too much for one lightweight branch. Scoped down to VLM +
embeddings (user-selected): both reuse the existing chat/proxy infrastructure instead of
needing a new pipeline shape, and cover the most immediately useful cases (RAG via
embeddings, image understanding via VLM). Audio and image/video generation are separate,
larger follow-up items — see "What's not covered" below.

**Manager (`core`)**:
- `RegistryEntry.modality` (`text`/`vision`/`embedding`, default `text`) and
  `RegistryEntry.mmproj_path` (vision projector file). `pmgr register --modality
  --mmproj-path`.
- `lifecycle.py`: `_build_llama_cpp_cmd` adds `--embedding` for `modality: embedding` and
  `--mmproj <mmproj_path>` for `modality: vision` — both real llama-server flags. Only
  `llama_cpp` dispatches on modality today; `mlx`/`vllm`/`sglang` accept the field but
  don't act on it yet (documented gap, same shape as phase 1's unverified vLLM/SGLang).
- TUI: Modality column in `pmgr list`/registry view + detail panel.

**Gateway**:
- `ChatMessage.content` now accepts either a plain string or an OpenAI-shaped content-part
  array (`text` / `image_url`). `image_url.url` must be a `data:` URI — remote http(s)
  URLs are rejected to prevent the backend from being used as an SSRF proxy.
- `/v1/chat/completions` returns `400 modality-mismatch` if a request has an `image_url`
  part but the target model's `modality != "vision"`.
- New `POST /v1/embeddings` (OpenAI-shaped `{model, input}`) — same auth chain as chat
  completions (`inference:read` + per-model `model:<id>` grant, RM-07), `400
  modality-mismatch` if the model isn't `modality: embedding`.
- `ModelEntry.modality` threaded through the static registry loader, `ManagerRegistrySync`,
  and exposed on `GET /v1/models` / `GET /v1/backends`.

**What's verified**: both llama_cpp flags were checked against a real llama-server build on
this Mac — `--embedding` launched `second-state/All-MiniLM-L6-v2-Embedding-GGUF` and served
a real `/v1/embeddings` response; `--mmproj` launched `ggml-org/SmolVLM-256M-Instruct-GGUF`
and correctly answered a real image content-part chat request (both via direct curl against
the manager-launched command, not through the full gateway auth stack — that stack is
already covered by existing JWT/scope tests). 22 new tests (10 manager-core, 13 gateway
minus 1 that's schema-only) — full details in `memory/wiki/model-registry.md` "Modalities
(RM-09)".

**What's not covered** (follow-up items, not RM-09): audio (whisper.cpp), image/video
generation (diffusers/ComfyUI), and modality-specific dispatch for `mlx`/`vllm`/`sglang`
(e.g. `mlx-vlm`/`mlx-whisper`).

## RM-10 — Gateway admin dashboard (item 3) — `done` (phase 1)

**Why**: no visual way today to see running instances, downloaded models, or manage
inference lifecycle — only `pmgr` TUI (bare-metal) and raw API calls.

**Scope, as expanded during implementation**: start/stop/restart controls plus full
model registration (not just viewing) — a bigger surface than the original "view +
lifecycle controls" wording, chosen deliberately over replicating the TUI's HF-search/
download flow in the same pass (that's phase 2 — see below). Frontend stack: React 19 +
Vite + TypeScript + Tailwind, the project's first Node/npm toolchain — chosen over a
server-rendered Jinja2 dashboard (the pattern `gateway/ui` and `auth-service/admin_ui`
already use) for a real SPA feel; explicitly *not* using Postgres/SQLAlchemy/Celery/Redis
— this dashboard has no relational state to migrate and no heavy background jobs, so
that stack would be pure overhead.

**Phase 1 (this pass) — lifecycle control + manual registration**:
- `runtime/manager/api`: new `backend-registry:write` scope (`auth.py`); new `control.py`
  router — `POST /v1/backends` (register), `PATCH /v1/backends/{id}` (update fields —
  added after the user tried the dashboard and noticed there was no way to fix a typo'd
  field after registering; re-validates the *resulting* merged entry, e.g. switching
  `backend` still re-checks `path` against it), `DELETE /v1/backends/{id}` (deregister,
  stops first if running), `POST /v1/backends/{id}/start|stop|restart` — all thin wrappers
  around the same `prometheus_manager_core.lifecycle`/`registry` functions `pmgr` already
  calls locally. `GET /v1/backends` gained `?include_hidden=true` (operator view — also
  see non-`discovery`-exposed entries; the default stays filtered since this endpoint also
  feeds the gateway's routing sync). 30 manager-api tests.
- `gateway`: new `admin_dashboard_enabled` flag (default off, same pattern as
  `ui_enabled`); new `gateway/src/prometheus_gateway/admin/` package — `client.py`
  (OAuth2 token mgmt + HTTP calls to a manager node, deliberately separate from
  `manager_sync.py`'s working token logic rather than refactoring it) and `router.py`
  (`POST /admin/api/auth/login`, `/admin/api/nodes`, `/admin/api/instances` aggregated
  across all `MANAGER_NODES`, `/admin/api/nodes/{node}/models` register/deregister/update,
  `/admin/api/nodes/{node}/instances/{id}/{start,stop,restart}`) — all except `auth/login`
  require `admin:read`/`admin:write`, proxying to the right node, flattening manager-api's
  nested error shape to match the gateway's own RFC 9457 format. `auth/middleware.py`'s
  exempt-path logic now distinguishes the public SPA shell (`/admin/*`) from the protected
  JSON API (`/admin/api/*`) instead of a flat prefix, plus a specific exemption for the
  login route itself (no token exists yet at login time by definition). 24 gateway tests.
  Two new fixed scopes added to auth-service's `VALID_SCOPES`: `admin:write`,
  `backend-registry:write` — existing service accounts need a scope grant to use the new
  write paths, see [auth-model.md](../wiki/auth-model.md#admin-dashboard-rm-10) migration
  note.
- `gateway/admin-ui/`: the SPA itself (React/Vite/TS/Tailwind, HashRouter, react-query,
  axios). **Login goes through the gateway, not directly to auth-service** — the SPA POSTs
  client_id/secret to `POST /admin/api/auth/login`, which the gateway proxies server-side
  to its configured `AUTH_SERVICE_TOKEN_URL`. This wasn't the original design (the SPA
  originally called auth-service directly) — real browser testing caught that auth-service
  sets no CORS headers, so a direct cross-origin call from the SPA's origin is blocked
  outright. Routing through the gateway fixed it and turned out simpler: the SPA no longer
  needs to know the auth-service's URL at all, so the originally-planned
  `/admin/config.json` runtime-config mechanism for prefilling that field was removed
  entirely rather than left unused.
- Dockerfile: new `admin-ui-builder` stage (Node 22) builds the SPA unconditionally so the
  container image works whether or not `ADMIN_DASHBOARD_ENABLED` is set; build output is
  gitignored (regenerated by `npm run build` or the Docker stage, never committed).
  `.githooks/pre-push`/CI gained an `npm ci && npm run lint && npm run build` stage.

**Verified for real, not just unit-tested**: stood up all three services locally (RSA
keypair + auth-service on SQLite + manager-api + gateway, no Podman) and drove the actual
built SPA in a real browser end-to-end — logged in, registered a model (confirmed written
to `registry.yaml` on disk), watched the stat cards and table update live, edited a field
and confirmed the change landed on disk, deleted it (confirmed removed from disk), logged
out. Caught and fixed three real bugs this way that no unit test would have: the CORS issue
above, a login-time 401 that turned out to be the *local test environment* picking up a
real `gateway/.env`'s Redis revocation settings (unrelated to RM-10 — a testing-setup
pitfall, not a product bug), and — after the user tried the dashboard themselves — a
missing edit action, plus (while adding it) a reminder that a multi-process local stack
needs *every* affected process restarted, not just the one you last edited (manager-api's
new route 405'd until its own process was restarted, not just the gateway's).

**Found in passing while verifying, since fixed by the user in a separate session**: the
auth-service admin API examples in the README's Quick Start were stale against the current
schema (`client_name`/`role`/`allowed_scopes`, not `name`/`scope`; `/oauth2/token`, not
`/token`).

**Found by the user trying the dashboard — a crashed instance showed as "Stopped", not
"Error"**: once a model's process dies (crash, or the manager kills it after a start
timeout), `scanner.py`'s `scan()` — which only reports processes it can currently see —
has nothing left to report, so it looked identical to a model that was simply never
started. Fixed by having `lifecycle.py` persist a `{model_id}.error` marker (the failure
message) on a failed start, cleared on the next successful start or explicit stop;
`routes.py`'s `_merge()` now reports `state: "error"` + `error_message` when a model has no
live process but does have a marker. Surfaced in the dashboard as a red "Error" badge with
the message as a tooltip. 7 new tests (5 manager-core, 2 manager-api).

**Phase 2 (not yet started)** — HuggingFace search/browse + trigger-download-from-web with
live progress, matching the TUI's Discovery/Downloads tabs. Deferred because today's
`download_model()` (manager-core) is a blocking, TUI-process-local call with no REST
exposure or persisted progress state — exposing it needs a small async job-tracking
addition to manager-api, not just new routes.

## RM-11 — Auth & Users dashboard (item 1)

**Why**: current auth UI needs an enterprise-grade redesign. Expanded on 2026-08-26 with a
concrete requirement: a "Users" section to manage users and their role, and — since not
every caller is a machine — a second login mode alongside the existing OAuth2
client_id/client_secret flow. This absorbs what RM-18 (multi-user RBAC) had speculated
about; RM-18 is now marked merged rather than staying a separate, speculative item.

**Scope (not yet designed in detail)**:
- New **Users** section in the dashboard menu — list/create/edit users and assign roles.
- Two configurable login modes:
  - **OAuth2 client_credentials** (`client_id`/`client_secret`) — the existing mechanism,
    for other systems integrating machine-to-machine.
  - **Email + password** — for human operators at other companies using the dashboard
    directly. This is the **default** login mode.
- Open questions to resolve before building: how password auth is stored (bcrypt, matching
  the existing `client_secret_hash` pattern, is the obvious default), whether email/password
  sessions still issue the same JWTs the client-credentials flow does or need a separate
  session mechanism, and how "role" here maps onto the existing scope model (RM-07's
  `model:<id>` scopes plus `admin:read`/`admin:write`) rather than inventing a second,
  parallel permission system.

**Not scoped yet**: password reset, MFA, email verification — revisit if/when real usage
demands them.

Do this together with or right after RM-07 so the new UI and permission model are built
once, not redone.

**Done (2026-08-26)**: `oauth_clients` unified into `principals` (`auth_method: oauth2 |
password`), migrated automatically on startup, old table dropped. New `password` grant on
`/oauth2/token`; same JWT/scope model for both grants — role still just picks a default
TTL, `allowed_scopes` still the only real gate. Retired the old Jinja2 `/admin/ui/*`
dashboard entirely (router, templates, its tests); credential share-links (spec-016)
ported into JSON endpoints (`/admin/clients/{id}/share` + `/revoke`) instead of dropped —
simpler than the original since the SPA already holds the plaintext secret from the
create/rotate/reset response, no flash-cookie hand-off needed. New Users section in
`gateway/admin-ui` (table, create/edit modal with an auth_method toggle, scope picker,
credential-reveal + share-link dialog); Login page defaults to email+password with a
toggle to the existing client_id/secret mode. Verified end-to-end in-browser: password
login, scope-denial error message, edit-to-grant-scope, re-login, share-link generate +
one-time redemption + second-visit 410.

## RM-12 — E2E LLM tracing with Langfuse (item 8)

**Why**: current observability (Loki/Tempo/Grafana + OTel, specs 018/020/021/022) is
generic request tracing, not LLM-specific (prompts, completions, token usage, evals).

**Scope**: integrate Langfuse (self-hosted, matches the "open" requirement) alongside the
existing telemetry package — fine-grained end-to-end trace of prompt → model → completion,
without duplicating what Tempo already captures at the HTTP layer.

---

## RM-13 — Admin dashboard: live log viewer (added) — `done`

**Why**: requested by the user after trying RM-10's dashboard — when an instance is in the
`error` state (or any state), there's currently no way to see *why* without SSH access to
the node and manually finding `{log_dir}/{model_id}.log`. The error-state work in RM-10
surfaces a one-line `error_message` in the instances table already, but that's only the
last-known-failure summary, not the actual server output (startup logs, request logs,
crash stack traces).

**Scope (not yet designed in detail)**: clicking an instance row in the dashboard table
expands it inline to show that instance's live log tail. Needs, roughly:
- manager-api: a new read endpoint to tail `{log_dir}/{model_id}.log` (e.g.
  `GET /v1/backends/{model_id}/logs?tail=N`), scope `backend-registry:read` (read-only,
  no new write surface). "Live" (auto-updating while the row is expanded) likely means
  either polling this endpoint on an interval or a streaming response (SSE/chunked) —
  worth comparing both against the existing `refetchInterval` polling pattern the
  dashboard already uses elsewhere before picking one.
- gateway: a proxying `/admin/api/nodes/{node}/instances/{id}/logs` route, same
  `admin:read` scope as the rest of the read side.
- frontend: expandable table row (or a side panel) rendering the tail, ideally
  auto-scrolling and only fetching while expanded (not for every row on every poll cycle
  — that would multiply request volume by the number of registered models for no reason).

**Not scoped yet**: log retention/rotation policy, whether historical (not just live-tail)
logs are needed, and whether this should also cover manager-api's/gateway's own logs (this
item is specifically about *inference backend instance* logs, matching what `{log_dir}/
{model_id}.log` already captures via `lifecycle.py`'s `subprocess.Popen(..., stdout=log_fh)`).

**Scope (built)**: manager-api gained `GET /v1/backends/{model_id}/logs?tail=N` (default
200, capped at 2000; same `backend-registry:read` scope as the existing read endpoints) —
a plain whole-file read + `lines[-n:]`, not a seek-based tail; fine for a single model's
log, revisit only if that ever proves too slow. Gateway proxies it at `GET
/admin/api/nodes/{node}/instances/{model_id}/logs` (`admin:read`). Frontend: a "Logs"
toggle button per instance row expands a `<pre>`-style tail panel (auto-scrolling to the
newest line), polling every 3s **only while expanded** — not for every row on every cycle,
per the original scope note. Non-streaming polling, matching the dashboard's existing
`refetchInterval` pattern everywhere else, rather than introducing SSE/chunked responses
for a first version.

**Verified**: 5 new manager-api tests (`test_logs.py`, 38/38 manager-api tests green) + 3
new gateway proxy tests in `test_admin.py` (47/47 admin tests green), full
`.githooks/pre-push` green. Live-verified against the real local manager-api and its actual
running `gpt-oss-20b-mxfp4` instance (up 37h): expanded its row and confirmed the panel
rendered the real 64-line log file exactly (matching `wc -l`/`tail` on disk), with the
correct last line and auto-scroll landing at the bottom.

## RM-14 — Model playground (added) — `done`

**Why**: every comparable platform researched (LiteLLM Proxy, Portkey, Helicone) ships an
in-dashboard playground — a way to send a test prompt to a running model and see the
response without curl/Postman. Prometheus has none today; you have to hit the gateway's
inference API directly to sanity-check a model you just started.

**Auth finding that shaped the scope**: the admin dashboard's own login only ever requests
`admin:read admin:write` from auth-service's OAuth2 token endpoint (`admin/router.py`'s
`login`), and that endpoint *intersects* the requested scope against the principal's
`allowed_scopes` rather than just granting whatever's asked (`oauth2.py`) — meaning an
admin session token can never carry `inference:read`/`model:<id>` today. Naively adding
`inference:read` to the login's requested scope would have been actively dangerous: if an
admin principal doesn't already have that scope granted, the *entire login* fails with
`400 invalid_scope` (the endpoint rejects the whole request, not just that one scope) —
breaking dashboard login for any admin without that grant.

**Scope (built)**: no new endpoint, no login changes — the playground calls the real `POST
/v1/chat/completions` directly (matching the "explicitly the real API, register real
cost, use everything we're already offering" requirement) with the admin's existing
session token. `chat_completions`'s two RM-07 authorization checks (`inference:read`/
`inference:stream`, and per-model `model:<id>`) each gained an `admin:write`-holder bypass
— admin:write already implies full model management control (`admin:models`), so letting
it also invoke any model for testing isn't a new privilege, just an explicit, narrow
carve-out scoped to that one existing scope. Everything downstream of the auth check
(usage/cost recording via RM-32/33, TPM rate limiting, `GET /metrics` counters, tracing)
runs completely unchanged, since this *is* the real endpoint. Non-streaming only for v1,
text models only (embeddings playground UI not built). Frontend: a model picker (running
text instances only), a prompt textarea, and a response panel showing the raw completion
plus its real token counts.

**Verified**: 3 new tests in `test_model_scopes.py` (admin:write bypasses both checks,
admin:read alone does not, bypass covers streaming too) — 209/209 gateway tests green,
full `.githooks/pre-push` green. Live-verified against the real local demo stack: sent
"Say the word banana and nothing else." to the actually-running `gpt-oss-20b-mxfp4`,
got back a real "banana" completion (75+29=104 tokens), and confirmed that exact request
appeared as a new row on the Usage page under "Demo Admin" — proving cost/usage recording
fires for real through this path, not just the UI response.

**Redesigned (2026-08-28)**: first-use feedback on the single prompt/response layout was
that it wasted most of the page's width. Researched what current playgrounds converge on
(OpenAI, Anthropic's Playground, LiteLLM) — multi-turn conversation area plus a parameters
sidebar is the common shape, so that's what this became: a real conversation (each send
carries the full prior history, not just one message) on the left, a **Parameters** panel
on the right (Temperature, Top P, Max tokens, Stop sequences — the exact fields
`ChatCompletionRequest` already forwards, so zero backend changes), and a separate System
prompt field above the conversation. Added Copy/Regenerate per response and a Clear-
conversation action. Still no streaming (see RM-36) and still text models only (see
RM-37). Verified live: multi-turn context actually holds (asked the model "what word did
you just say" in a follow-up turn and it answered correctly), Regenerate produces a fresh
real call with different token/latency numbers, Clear empties the conversation.

**Also fixed alongside this**: every dashboard page shared a layout bug — `main` lacked
`min-w-0`, so a wide table (e.g. Instances' 900px-wide table) grew the whole page past the
viewport instead of scrolling inside its own box, and the Sidebar was a plain static flex
item, so scrolling down a tall page scrolled the nav out of view with it. Fixed both
(`min-w-0` on every route's `main`, `sticky top-0` + `overflow-y-auto` on `Sidebar`) —
unrelated to the Playground specifically, just discovered while trying it on the Instances
page with 28 rows.

## RM-15 — Usage: wire up today's per-client totals (added)

**Why**: LiteLLM's Usage page, Portkey, and Helicone all treat per-model/per-client token
and request usage as a first-class dashboard page. Today the only way to see usage in
Prometheus is going directly to Grafana/Tempo — there's no aggregated view in the admin
dashboard itself.

**Split (2026-08-27)**: scoping this while building RM-30's placeholder surfaced four
concrete, separable gaps rather than one monolithic "usage & spend" feature — recorded
below, then split into their own items so the part that needs zero new backend work isn't
stuck waiting on the parts that need real design work (persistence, pricing):

1. **Wire up what already exists** (this item, RM-15): `GET /v1/usage` (gateway,
   pre-existing, unrelated to this backlog series) already returns real per-client
   prompt/completion/request token counts — for the current UTC day only — but nothing in
   the admin-ui ever calls it.
2. **Real history** (RM-32): `/v1/usage`'s counters live in Redis with a daily TTL — fine
   for "today," useless for a trend chart. Needs an actual persisted, queryable store (a
   database table, or aggregating from OTel/Tempo traces, or leaning on RM-12's Langfuse
   integration if that lands first — Langfuse already tracks prompt/completion/token data,
   which may make a separate aggregation redundant. Decide the data source before
   designing RM-32).
3. **Per-model breakdown** (RM-32): `/v1/usage` only splits by client, not by model —
   "which model is costing the most" isn't answerable from it today.
4. **Pricing** (RM-33): there is no price-per-token/per-model concept anywhere in this
   codebase. Turning a token count into a dollar figure needs a new pricing table (keyed by
   model id and/or quantization) and a decision on where it's edited (a config file? a
   dashboard settings page? RM-14/RM-24's model-picker plumbing could inform where this
   lives).

**Scope (this item)**: a new "Usage" nav page rendering `GET /v1/usage`'s data — one row
per client (cross-referenced against the Users list for a readable name instead of a raw
`client_id`), showing prompt/completion/total tokens and request count for the current UTC
day. Must handle and clearly explain the two degraded states the endpoint itself returns:
an empty list when no Redis is configured, and a `503 usage-store-unavailable` if Redis is
configured but unreachable. No new backend work — this is a pure frontend read of an
endpoint that already exists.

**Not this item**: any historical view, per-model split, or dollar figure — seeing those
here would require RM-32/33 landing first; this item is explicitly "today's numbers only,"
labeled as such.

**Done (2026-08-27)**: new `/usage` route + sidebar entry, between Nodes and Users. New
`rootClient` in `api/client.ts` (same token-attach/401-redirect interceptors as the
existing `apiClient`, but for gateway endpoints outside `/admin/api` — `/metrics` didn't
need this since it's unauthenticated, but `/v1/usage` requires `admin:read`). Table shows
one row per client (name resolved via the existing Users list, falling back to the raw
`client_id` for any principal not found), plus loading/error/empty states. The two
degraded-state responses collapse to the same "No usage recorded for today yet." message
client-side, since the endpoint itself returns an identical empty array for "no Redis
configured" and "Redis configured, zero usage today" — there's no way to tell them apart
from the response alone, so the message doesn't claim a cause it can't verify. Verified
both states for real: installed Redis locally (this dev machine didn't have one), pointed
the gateway at it, made a real `/v1/chat/completions` call, and confirmed the resulting
row (name, exact token/request counts) rendered correctly — then reverted the gateway to
its prior no-Redis config and stopped Redis, confirming the page falls back to the empty
state cleanly.

**Carried over from RM-28's scoping**: once this lands with real persistence, revisit
whether the Overview page's golden-signals row (RM-28) should grow a small client-side
trend/sparkline, or just link into whatever historical view RM-15 builds — a client-side
rolling buffer sampled from `/metrics` was considered and deliberately deferred rather than
built twice.

## RM-16 — Routing & rate-limit visibility (added) — `done`

**Why**: the gateway already enforces rate limiting and circuit breakers (spec 007), but
that state is invisible today outside reading `.env` files or logs. Comparable platforms
expose current rate-limit/circuit-breaker state and routing rules directly in their
dashboards.

**Scope**: read-only, as scoped — live-editing config from the dashboard stays explicitly
out, `.env` remains the single source of truth. Extended the existing `GET
/admin/api/config` (RM-31) with the 6 rate-limit/circuit-breaker config fields already in
`Settings` (global RPM/TPM, the optional per-endpoint chat-completions override,
fail-open/strict mode, circuit-breaker failure/recovery/success thresholds) — nothing new
on the backend beyond exposing values that already existed. New `/limits` page: a config
card group plus a table of every backend's live `circuit_state`, reusing `GET /metrics`'s
existing `backends` map (already built for RM-28/29) via a newly-extracted shared
`CircuitBadge` component (previously private to `AttentionTable.tsx`, now in its own file
since it has a second real caller).

**Verified**: 3 new/updated tests in `test_admin.py` (44/44 admin tests green), full
`.githooks/pre-push` green. Live-verified against the local demo gateway: the Limits page
rendered the exact configured defaults (60 RPM, 40,000 TPM, no per-endpoint override,
"Allow (fail-open)" matching `RATE_LIMIT_STRICT=false`, 5/30s/2 circuit-breaker thresholds)
and the correct empty state for backend circuit traffic on a freshly started process.

## PRM-17 — Guardrails / content filtering (added, speculative)

**Why**: PII redaction and content filtering are common in comparable platforms (Portkey),
but nothing about Prometheus's actual use case has asked for this yet. Recorded because it
came up in the dashboard-feature research, not because there's a known need.

**Scope**: undefined. Revisit only if a real need shows up.

## RM-18 — Teams / multi-user RBAC (added, speculative) — **merged into RM-11**

**Why**: comparable platforms (LiteLLM Teams, Portkey RBAC) assume multiple humans
administer the platform. Prometheus today is single-operator. Flagged here as speculative
since nothing had asked for it yet.

**Update (2026-08-26)**: no longer speculative — a concrete requirement showed up (a Users
section, roles, email+password login for other companies). Rather than building this
separately from RM-11's auth UI, it's folded directly into RM-11's scope. See RM-11 for
the actual Why/Scope going forward; this entry stays only as a record of where the idea
originated.

## RM-19 — Dashboard branding: logo + favicon (added)

**Why**: the dashboard currently has no visual identity — just the text "Prometheus" in the
sidebar and the default Vite favicon in the browser tab.

**Scope**: add an icon/logo next to the "Prometheus" wordmark in the sidebar header, and
reuse that same icon as the page favicon. Needs an actual icon/logo asset chosen first —
not yet designed.

## RM-20 — Node registry (added)

**Why**: manager nodes are currently only known via the gateway's static `MANAGER_NODES`
config (RM-08) — there's no way to see, add, or edit them from the dashboard, and no
metadata beyond a URL (nothing recording whether a node is a Mac or an Nvidia box).

**Update (2026-08-26)**: split from the original scope. This item is now just the node
**inventory** — name, manager-api URL, hardware type (Mac / Nvidia), free-form tag/label.
This is what RM-21's node picker actually depends on. The SSH/remote-maintenance
credential piece (originally bundled here) is split out to PRM-25 — it's a materially
different, higher-risk concern (storing login credentials to a machine, not talking to its
manager-api) with no concrete consuming feature yet.

**Scope (not yet designed in detail)**: a **Nodes** admin section (CRUD) whose entries
**replace** the static `MANAGER_NODES` env var as the gateway's live routing source — not
just a display-only metadata table. This is the bigger, riskier part of this item: gateway
resolves which node to hit on every inference/instance-management request today via a
one-time `Settings.resolved_manager_nodes` read from env, so replacing that with a
mutable, admin-editable registry needs a caching/refresh strategy (adding a node in the UI
shouldn't require a gateway restart, and the hot request path shouldn't take on a live DB
read per request). `gateway/src/prometheus_gateway/models/manager_sync.py` already runs a
periodic background sync for something related (registry contents, not node topology, but
same pattern) — check it first for a mechanism to extend rather than building a second one.

**Not scoped yet**: exact storage location (auth-service's existing DB is the closest fit
given RM-11's admin-proxy pattern, but a live-routing dependency on auth-service being
reachable is a new failure mode worth weighing against caching); migration path for
existing `MANAGER_NODES` deployments (seed the registry from it once, then env var becomes
inert / removed, or keep both and merge).

**Done (2026-08-26)**: new `Node` table in auth-service (mirrors `Principal`'s
conventions), `/admin/nodes` CRUD. `MANAGER_NODES`/`MANAGER_URL`/`resolved_manager_nodes`
removed entirely from the gateway — `ADMIN_DASHBOARD_ENABLED` is now the single gate for
manager-node integration (already required to pair with
`AUTH_SERVICE_ADMIN_URL`/`AUTH_SERVICE_ADMIN_API_KEY` per RM-11). Resolved the
caching/freshness question simply: node resolution was never on the hot inference request
path to begin with (confirmed by exploration — `/v1/chat/completions` reads a pre-resolved
`ModelEntry.backend_url`, baked in by `ManagerRegistrySync`'s existing 30s poll), so
`ManagerRegistrySync._sync()` just re-fetches the node list from auth-service at the start
of every poll cycle instead of using a frozen constructor list — a newly-added node goes
live within one interval, no restart, no wakeup/interrupt mechanism needed.
`admin/router.py`'s `_resolve_node`/`list_instances` do the same live fetch (admin-only,
low-QPS, so a per-call HTTP hop to auth-service is a non-issue there). Breaking change,
no migration bridge (matches this project's established clean-cutover pattern) — existing
deployments must create their node(s) via the dashboard's Nodes section (or `POST
/admin/nodes`) after upgrading; `gateway/.env.podman.example` and `podman-compose.yml`
updated accordingly.

**Follow-up (2026-08-26)**: after using it, found two rough edges — no validation that a
newly-registered node is actually reachable (a typo'd URL just silently breaks routing),
and no way to see a node's health status. Added `Node.is_active`, set by an actual
connectivity check (`GET {manager_url}/health` — manager-api's unauthenticated liveness
probe) at creation and whenever `manager_url` changes, plus a manual `POST
/admin/nodes/{id}/check` to re-run it (e.g. after fixing a down node). An unreachable node
is still created — never rejected outright — just marked inactive, since it's a valid
node the operator will likely bring up shortly. `fetch_nodes()` (used by
`ManagerRegistrySync` and by admin's routing/instance-control endpoints) filters to
active-only, so an inactive node is silently excluded from both the poll-driven model
registry and node-scoped admin actions; the Nodes page itself still lists every node
(active or not) via the unfiltered auth-service proxy, with a status badge and a recheck
button per row.

Also added a manual `POST /admin/nodes/{id}/activate` and `/deactivate` per-row toggle for
on-demand overrides — e.g. taking a reachable node out of rotation for maintenance.
`/deactivate` is a pure override (no probe). `/activate` is deliberately **not**: it
re-probes and only actually activates if the node is reachable, otherwise it stays
inactive — an admin-settable "active" flag that ignores real reachability would show a
green badge for a node that still can't serve traffic, which is worse than not having the
button at all. `/activate` and `/check` end up running the identical probe-then-set logic;
kept as separate routes because "bring this node back into service" and "just tell me its
current status" are different operator intents worth distinct frontend messaging.

## PRM-25 — Node SSH/remote-maintenance credentials (added, speculative)

**Why**: came up while scoping RM-20 — being able to record how to reach a node's
underlying machine (not just its manager-api) for maintenance. Split out because there's no
concrete feature yet that would actually *use* stored SSH credentials (no "restart this
node", no remote log viewer at the host level) — recorded here rather than built.

**Scope**: undefined. If a real need shows up, this needs real security design (encrypted
at rest at minimum — the existing `share_crypto.py` / `SHARE_TOKEN_ENCRYPTION_KEY` pattern
from RM-11's credential-share-links is a reasonable starting point to reuse rather than
inventing a second encryption scheme) before any implementation, not as an afterthought.

## RM-21 — Simplified instance creation (added)

**Why**: registering an instance today means typing every field by hand — backend,
modality, family, quantization, path, port — even though almost all of it is already known:
the manager already scans for locally-downloaded models (registry entries with
`discovery: true`), and the port is just "the next free one." Manual entry is slow and
error-prone (typoed paths, port collisions).

**Scope (not yet designed in detail)**:
- Port becomes optional/hidden — the system auto-assigns the next available port starting
  from a configurable base value.
- manager-api needs to expose (or the dashboard needs to consume an existing) list of
  discovered/downloaded models (`discovery: true`) per node.
- Instance creation becomes: pick a node (RM-20's registry) → pick a model from that node's
  discovered list → backend/modality/family/quantization/path auto-fill from the discovered
  entry → only a few real parameters stay user-editable (e.g. context window).

## RM-22 — Platform overview: page shell + at-a-glance strip (added)

**Why**: the dashboard currently opens straight to the instances table — there's no single
page summarizing overall platform state at a glance.

**Research (2026-08-27)**: see the scoping memo published while designing this — comparable
products (LiteLLM, Portkey/Helicone, vLLM+Grafana, Open WebUI) all converge on the same
frame the SRE "four golden signals" (latency, traffic, errors, saturation) describe. More
usefully: auditing this repo found the gateway already computes most of what's needed and
never shows it anywhere — `GET /metrics` (requests/tokens/errors/latency
p50-p95-p99/per-model circuit state, in-memory, unauthenticated) and `GET /v1/usage`
(per-client daily token counts, Redis-backed) are both fully built and fully unused by the
React admin-ui. Given that, this item is split into four so most of it ships with **zero
new backend work**, rather than as one large "wait for RM-15/RM-23" page:

- **RM-22** (this item): the page shell itself — new route, nav entry, and the
  "at-a-glance" stat strip (node/instance/user counts from data the dashboard already
  polls), plus a links-out row to the existing Grafana ops dashboard and Tempo trace
  search rather than re-implementing log/trace search inside the React app.
- **RM-28**: the golden-signals row, sourced from `GET /metrics`.
- **RM-29**: the "what needs attention" row — instances joined with `/metrics`'s
  per-backend circuit state, sorted so anything not `ready` floats to the top.
- **RM-30**: a usage & cost row, but only as an honest "coming soon" placeholder — real
  numbers need RM-15 (persisted usage store + a pricing table; `GET /v1/usage` alone gives
  today-only totals with no per-model split and no dollar figure).

**Scope (RM-22 itself)**: new `/` route (Instances moves to its own nav item, matching
every comparable product's convention of a distinct overview vs. instance-list page);
stat strip: nodes (active/total), instances (running/stopped/error breakdown), users
(active/total), gateway uptime; a small links row to Grafana/Tempo. No new backend
endpoints — `useNodeRegistry()`, `useInstances()`, `useUsers()` already exist.

**Not scoped yet**: whether an unhealthy-model banner (reusing the Nodes page's
"unreachable nodes" banner pattern) belongs on this page or on RM-29's row instead —
revisit once RM-29 lands and it's clear which page an operator actually looks at first
when something's wrong.

**Resolved once RM-29 landed**: no separate banner. RM-29's "Needs attention" table *is*
that callout — always visible (not dismissible-and-forgotten like a banner), and it shows
structured detail (model/node/state/circuit) instead of just a name list. A banner
restating the same thing above it would be pure duplication.

**Done (2026-08-27)**: `/` now renders a new `Overview` page — stat strip (nodes
active/total, instances total + running/stopped subtext, users active/total, gateway
uptime from a new minimal `useMetrics()` hook against `GET /metrics`) plus a links row to
Instances/Nodes/Users. `Instances` moved to `/instances`; `Sidebar` gained an "Overview"
entry above it. `StatCard` gained an optional `sub` line to carry the breakdown text.
**Scope trim**: the external Grafana/Tempo links from the memo were dropped for this
pass — there's no `GRAFANA_URL`-shaped setting anywhere in the gateway's config to build a
reliable link from, and guessing one client-side (assuming Grafana sits on the same host
at :3000, per `podman-compose.yml`) would be fragile across deployments. Worth a small
follow-up once there's an actual config surface for it; not blocking for RM-28/29/30.

## RM-28 — Overview: golden signals row (added)

**Why**: split out of RM-22 — see above. `GET /metrics` already computes requests
(total/active), token counts, error count, and p50/p95/p99 latency over a rolling
1,000-request window; none of it is rendered anywhere today.

**Scope (not yet designed in detail)**: a row of stat cards fed by a new `useMetrics()`
hook against the gateway's existing `GET /metrics` (unauthenticated, so no scope-gating
needed on the frontend side). Must visibly caveat that the counters are process-memory
only — they reset on a gateway restart, and there's no historical trend in this phase.
Open question carried over from the scoping memo: is a client-side rolling buffer (sample
`/metrics` each poll, keep enough points in the browser for a small sparkline) worth doing
now, or better deferred to whenever RM-15 lands real persistence anyway?

**Done (2026-08-27)**: "Request health" row on Overview — requests (active now + total),
error rate, latency p50 (with p95/p99 as a sub-line), and circuits open (with a half-open
count folded into the sub-line when nonzero). `useMetrics()` (added for RM-22) widened
with the full `inference`/`backends` shape. The process-memory caveat renders as a plain
text line under the row rather than a dismissible banner — it's a standing fact about this
data, not a one-time alert. Verified end-to-end: issued a real `/v1/chat/completions`
request through the gateway and confirmed the row picked up the resulting
requests_total/latency/backend entry on the next poll. Client-side rolling-buffer sparkline
question: deferred, per the "ship the simple version now" default — revisit alongside
RM-15.

## RM-29 — Overview: models needing attention (added)

**Why**: split out of RM-22 — see above. The actual job of a home page is answering "what
do I need to fix right now," not just restating counts already visible on the Instances
page.

**Scope (not yet designed in detail)**: a compact table merging the existing Instances
list with `/metrics`'s per-backend circuit-breaker state (keyed by model id), sorted so
anything not in a healthy/`ready` state sorts first. Likely reuses the sort/status-pill
conventions already established by `NodeRow.tsx`/`UserStatusBadge`.

**Done (2026-08-27)**: refined "not in a healthy state" during implementation —
`stopped`/`paused` are normal resting states (27 of 28 demo instances are `stopped`, which
would swamp a "compact" table if included), so the actual filter is `state === "error"`
OR `circuit_state` is `"open"`/`"half-open"`: the two conditions that are genuinely
alarming rather than just idle. New `AttentionTable.tsx` (reuses the existing
`StatusBadge` component for state, a small local `CircuitBadge` for circuit state) sorted
by severity (crashed + open circuit ranks above either alone). Empty state reads "All
models healthy — nothing needs attention right now." rather than an empty table. Verified
with a real crash: registered a throwaway backend pointing at a nonexistent `.gguf` path,
started it, confirmed manager-api marked it `error` with a message and the row rendered
correctly (red `Error` pill, `Unknown` circuit since it never got an inference call), then
deleted it and confirmed the healthy empty-state returned.

## RM-30 — Overview: usage & cost placeholder (added)

**Why**: split out of RM-22 — see above. Showing partial/misleading numbers here (e.g.
today-only totals with no cost) would look broken rather than "coming soon"; better to
ship an honest placeholder now and the real row once RM-15 lands.

**Scope**: a single disabled-looking card on the Overview page stating usage & cost
tracking is coming, linking to this roadmap item / RM-15's status. No backend work.

**Done (2026-08-27)**: a single dashed-border, lowered-opacity card in a "Usage & cost"
section — coin icon, "Coming soon", and a one-line reason (needs a persisted usage store
and per-model pricing, not just today's per-client totals from `GET /v1/usage`). No literal
link to this roadmap item — nothing in the running app is wired to expose the repo's
roadmap docs, so a "link" would just be dead; the explanatory text carries the same
information instead. No backend work, as scoped.

## RM-23 — Active sessions / connected users (added) — `done`

**Why**: no visibility today into who or what is actively using the platform right now —
operators logged into the dashboard web UI, end users chatting via a model's own UI, API
callers, and (future) SDK users. RM-15 covers historical/aggregate usage; this is about
*live* connections instead.

**Scope trim (2026-08-28)**: JWTs are stateless — there's no server-side session object
anywhere to track, so a *real* connection registry isn't buildable without adding one.
Landed instead as a last-seen-based approximation: a new `ActivityTracker` (in-memory,
same single-process `asyncio.Lock` pattern as `MetricsStore`) records `(client_id, user_id,
connection_type, last_seen)` on every authenticated request, hooked directly into
`JWTAuthMiddleware` right after claims validation. `connection_type` is inferred purely
from the URL prefix (`/admin/api` → "dashboard", `/v1` → "api", the only signal available
without reading further into who's calling) — **not** tracked: the web chat UI's `/ui/*`
routes (spec 013), which are Bearer-exempt and authenticate via their own session cookie
(`ui/router.py`'s `_validate_session`), so those sessions are invisible to this mechanism.
Per-model attribution ("which model is being used") was also cut from v1 — the model id
only lives in the request body, not the URL, and reading it in middleware would mean
buffering every request body for a nice-to-have; a natural fast-follow once there's a
concrete need. New `GET /admin/api/sessions` (`admin:read`) returns entries seen in the
last 15 minutes, most-recent-first, pruning older ones on read. New `/sessions` page,
cross-referencing the Users list for a readable name.

**Verified**: 4 new `ActivityTracker` unit tests (`test_activity_tracker.py`) + 2 new
`test_admin.py` tests (53/53 admin-area tests green) — full gateway suite 206/206, full
`.githooks/pre-push` green. Live-verified against the local demo gateway: the current
admin dashboard session showed up on `/sessions` as "Dashboard · just now", name resolved
correctly against the real Users list.

## RM-24 — Model picker in Create User (done)

**Why**: today, granting a user access to a model means typing a raw `model:<id>` scope
string by hand in the Create User modal's free-text scope field (RM-11) — the operator has
to already know the exact model id and get the `model:` prefix right.

**Correction during scoping**: the original note above assumed `discovery: true` meant
"downloaded but not yet an instance," and that RM-21 needed to land first to supply a
model list. Neither held up — `discovery` is actually a runtime health flag (true only
while a model is running and passing health checks; RM-21's original premise needs its own
re-scoping, unrelated to this item). More directly: every registry entry (running or not)
already shows up as a row in the existing Instances table via `GET /admin/api/instances`
— there's no separate "known but not yet instantiated" model concept to build a new
endpoint for. So RM-24 needed nothing from RM-21 after all; it just reuses the
already-existing aggregated instances list.

**Done**: `ScopePicker.tsx` now renders a "Models" checkbox list sourced from
`useInstances()`, deduplicated by model id across nodes (access isn't node-specific).
Toggling a checkbox adds/removes the corresponding `model:<id>` scope. A model id already
granted to the user being edited, but not currently present in the discovered list (its
node is down, or it was deregistered), still renders — as a disabled/checked "not
currently found" row — so editing an existing user never silently drops access to a model
just because it's temporarily unreachable. The old free-text input is gone entirely: per
auth-service's `is_valid_scope`, `model:<id>` and the fixed scope enum are the *only* two
valid scope shapes, so there was no remaining case the picker didn't cover.

**Known gap, not fixed here**: auth-service's `_MODEL_SCOPE_RE` (`^model:[a-z0-9][a-z0-9_-]*$`)
rejects model ids containing a dot or uppercase letters — the picker will show a clear
`Unknown scope(s)` error from the backend if such an id is selected. This is a pre-existing
mismatch with manager-core's own `_ID_RE` (`^[a-z0-9][a-z0-9_-]{1,62}[a-z0-9]$`, which
likewise forbids dots/uppercase) — any registry entry with such an id was added by
hand-editing `registry.yaml` directly, bypassing `_validate_id`. Out of scope here since
it's a data-hygiene issue in the registry, not something this feature introduced.

## RM-26 — Instances list: numbered, paginated, active-first (added) — `done`

**Why**: the Instances table on the dashboard just lists rows in whatever order the
gateway returns them, with no row numbering and no cap — as the number of registered
instances grows (across more nodes, more models) the table gets long and running
instances get lost among stopped ones.

**Scope**: all client-side, in `InstanceTable.tsx` — no backend change needed, the
aggregated list already comes from one `GET /admin/api/instances` call. Added a leading
`#` column (continuous across pages, not reset per page). Sort by state rank (`ready` >
`loading` > `error` > `paused` > `stopped`) so anything non-idle surfaces above the many
normally-stopped rows. Paginated at 20 rows/page with Prev/Next controls and a "Showing
X–Y of Z" label, shown only once the list exceeds one page.

**Verified**: `npm run build` type-checks cleanly; full `.githooks/pre-push` green.
Live-verified against the local demo gateway's real 28-instance list: the one `ready`
instance sorted to row 1 ahead of 27 `stopped` ones, page 1 showed "Showing 1–20 of 28",
and Next correctly advanced to page 2 ("Showing 21–28 of 28", rows numbered 21–28).

## RM-27 — Delete user (added) — `done`

**Why**: the Users table only offers deactivate/reactivate (`UserRow.tsx`) — there's no way
to permanently remove a principal from the dashboard. auth-service's `DELETE
/admin/clients/{id}` already supports this (`?permanent=true` hard-deletes the row and
writes a Redis revocation key so any outstanding token is rejected immediately — see
`deactivate_client` in `auth-service/src/prometheus_auth/routers/admin.py`), so most of the
work is frontend, but not all: the gateway's own proxy (`DELETE /admin/api/users/{id}` in
`gateway/src/prometheus_gateway/admin/router.py`) currently calls `_auth_admin_request`
with no query params, silently dropping `permanent` even if the frontend sent it — that
proxy needs to forward the param through.

**Scope**: `_auth_admin_request` gained a `params` passthrough; `deactivate_user`'s route
now accepts `?permanent=true` and forwards it. Frontend: `useDeleteUser()` in `api/users.ts`
(always sends `permanent: true`, distinct from `useDeactivateUser`'s reversible call), a
Delete action in `UserRow.tsx`'s action column mirroring `NodeRow.tsx`'s
delete-with-`ConfirmDialog` pattern, with confirmation copy that explicitly contrasts it
with Deactivate ("Unlike Deactivate, this cannot be undone...") so an operator doesn't
reach for the wrong one.

**Verified**: `test_delete_user_permanent_forwards_query_param` (+ updated
`test_deactivate_user_proxies_delete` asserting the default is `permanent=false`) in
`test_admin.py` — 40/40 admin tests green, full `.githooks/pre-push` green. Live-verified
against the real local demo auth-service: deleted a genuine leftover test principal
("RM24 Test User") through the dashboard's new Delete button, confirmed the row disappeared
from both the UI and a direct `GET /admin/api/users` call.

## RM-31 — Overview: link out to Grafana/Tempo (added) — `done`

**Why**: dropped from RM-22's original scope — see that item's "Scope trim" note. The
Overview page memo called for a links row to the existing Grafana ops dashboard and Tempo
trace search, but there's no `GRAFANA_URL`-shaped setting anywhere in the gateway's config
to build a reliable link from, and guessing one client-side (assuming Grafana sits on the
same host at :3000, per `podman-compose.yml`) would be fragile across deployments —
different host, different port, TLS, or no Grafana deployed at all.

**Scope**: added a `grafana_url: str | None` setting (Tempo has no separately exposed UI in
`podman-compose.yml` — its trace search lives inside Grafana's Explore view against the
Tempo datasource, so one URL covers both). Exposed via a new `GET /admin/api/config`
(requires `admin:read`, like every other admin-ui endpoint — simpler than carving out an
unauthenticated exception for one non-secret URL). Frontend: `useDashboardConfig()` in
`api/config.ts` (`staleTime: Infinity` — it can't change without a gateway restart), and a
"Grafana / Tempo" link chip on Overview, rendered only when `grafana_url` is set, opening
in a new tab.

**Verified**: `test_admin.py` (3 new tests: configured/unset/scope-enforcement) — 43/43
admin tests green, full `.githooks/pre-push` green. Live-verified against the local demo
gateway both ways: with `GRAFANA_URL=http://localhost:3000` set, the Overview page showed
a working "Grafana / Tempo" link (`target="_blank"`, correct `href`); with it unset, the
link chip was absent entirely rather than a dead link.

## RM-32 — Usage: persisted history + per-model breakdown (added) — `done`

**Why**: split out of RM-15 — see that item's gap #2/#3. A trend chart and a "which model
costs the most" answer both need data `/v1/usage`'s Redis daily counters can't provide:
real persistence beyond a day, and a per-model dimension.

**Scope**: new `usage_daily` SQLite table (async SQLAlchemy, mirrors auth-service's
`db.py` conventions) — one row per (day, client_id, model_id), aggregate counters
incremented via an `asyncio.Lock`-guarded upsert (same single-process-safety assumption as
`MetricsStore`). Replaces the old Redis daily-TTL counters entirely — this is the gateway's
first persistent database. `GET /v1/usage` kept its original response shape (so the
already-shipped RM-15 page didn't break) and added a `by_model` array per client plus an
optional `?date=YYYY-MM-DD` query param for browsing past days. Frontend: `Usage.tsx` gained
an expandable per-client row (chevron) showing the model breakdown, and a native date picker
next to the heading.

**Verified**: `gateway/tests/test_usage_db.py` (6 unit tests on `db.py`) +
`test_rate_limiting.py`'s usage tests (incl. invalid-date 400, past-date-empty) — full
`.githooks/pre-push` green. Live-verified against the local demo gateway: seeded the
isolated demo SQLite file directly via `db.record_usage`, confirmed `/v1/usage` aggregates
correctly across two clients/two models, `?date=` on a past empty day returns `[]`, an
invalid date returns RFC9457 400, and the built admin-ui renders the expandable
per-model rows and reacts to the date picker.

## RM-33 — Usage: pricing table + real cost (added) — `done`

**Why**: split out of RM-15 — see that item's gap #4. No part of this codebase has ever
recorded what a token costs; without it, "usage" can show counts but never a dollar figure.

**Scope**: static config file, not a dashboard settings page — pricing changes rarely and
this avoids a new CRUD surface (auth + admin scopes + UI) for something that's really just
a handful of numbers per model. `gateway/pricing.yaml` (gitignored, real dollar figures are
deployment-specific — `gateway/pricing.yaml.example` is the committed template), keyed by
model id: `prompt_price_per_1m` / `completion_price_per_1m` USD. `pricing.py` loads it once
at startup (`PRICING_FILE` env var, defaults to `gateway/pricing.yaml`, missing file → empty
table, never an error). A model with no price entry gets `estimated_cost_usd: null`
everywhere — deliberately not `0`, so an unpriced model never looks free in the UI.
`GET /v1/usage` adds `estimated_cost_usd` per client (sum of its priced models only) and per
model in `by_model`. `Usage.tsx` adds an "Est. cost" column, rendered as `—` when null.

**Verified**: `gateway/tests/test_pricing.py` (4 unit tests) +
`test_usage_endpoint_includes_estimated_cost` in `test_rate_limiting.py` — full
`.githooks/pre-push` green (192 gateway tests). Live-verified against the local demo
gateway with a real `pricing.yaml` for `gpt-oss-20b-mxfp4`: `/v1/usage` and the built
admin-ui both show the computed cost for the priced model and `—`/`null` for `small-model`,
which has no price entry.

## RM-34 — Overview: wire the usage & cost card to real data (added) — `done`

**Why**: RM-30 shipped an honest "coming soon" placeholder specifically so the real numbers
wouldn't need to be faked. Once RM-32 (history/per-model) and RM-33 (pricing) exist, this
closes the loop.

**Scope**: replaced RM-30's placeholder with 3 real stat cards, fed by `useUsage()`
(RM-32/33's `GET /v1/usage`): **Tokens today** (sum of `total_tokens` across all clients,
sub-label counts clients), **Est. spend today** (sum of `estimated_cost_usd`, shown as "—
No pricing configured" when every client's cost is null rather than a misleading $0), and
**Top model** (highest-token model aggregated across all clients' `by_model` breakdowns).
Added a shared `formatUsdCost()` helper in `lib/format.ts` (used by both this card and
`Usage.tsx`, replacing that page's local copy) and a "→ Usage" link chip alongside the
existing Instances/Nodes/Users links.

**Verified**: `npm run build` type-checks cleanly; full `.githooks/pre-push` green. Live-
verified against the local demo gateway with a real `pricing.yaml`: Overview showed "Tokens
today: 515 (2 clients)", "Est. spend today: USD 0.0001", "Top model: gpt-oss-20b-mxfp4 (460
tokens today)" — matching `/v1/usage`'s actual aggregates.

## RM-35 — Native tool-calling (OpenAI-style function calling) (added) — `done`

**Why**: identified while extending the Playground — every comparable platform (and
OpenAI's own API) supports `tools`/`tool_calls` on chat completions; Prometheus's
`ChatCompletionRequest` allowlist has no `tools` field at all today, and `"tool"` is only
a recognized message *role*, not an actually-wired capability.

**Scope**: the gateway is a pure allowlist proxy — `GET /v1/chat/completions`'s response
handler already forwards the backend's full JSON body untouched
(`JSONResponse(content=resp_body, ...)`, no field-by-field reconstruction), so the
response side needed zero changes. The request side gained: `tools`/`tool_choice` on
`ChatCompletionRequest`; `tool_calls`/`tool_call_id` on `ChatMessage`, plus making
`content` optional (an assistant message that only calls a tool has `content: null`, per
OpenAI's shape); `to_llama_payload()` forwards both new fields and dumps messages with
`exclude_none=True` so the new optional fields don't show up as literal `null`s on every
message that doesn't use them. `_estimate_tokens()` treats `content: None` as `""`
instead of the string `"None"`. Playground gained a "Tools (function calling)" section: a
raw JSON textarea for the tools array (simplest honest option — a visual schema builder is
real UI work with no clear payoff yet) and a `tool_choice` select (`auto`/`required`/
`none` — dropped the OpenAI dict form that forces one specific function, since the local
gpt-oss backend used for verification didn't honor it), rendering any `tool_calls` in the
response as a distinct 🔧 block instead of empty text.

**Verified**: 3 new tests in `test_gateway_core.py` (tools/tool_choice forwarded intact,
response tool_calls pass through byte-for-byte, a full assistant-tool_calls +
tool-role-response round trip forwards correctly) — 212/212 gateway tests green, full
`.githooks/pre-push` green. Live end-to-end against the real locally-running
`gpt-oss-20b-mxfp4`: hit the backend directly first to confirm it genuinely supports
tool-calling (`tool_choice: "required"` — the OpenAI-style `{"type":"function",...}`
forcing dict was accepted but silently ignored by this backend build), then confirmed the
exact same real `tool_calls` response comes back through the gateway unmodified, and
through the redesigned Playground itself (a `get_weather` call rendered correctly with
real token counts and latency). One methodology pitfall worth recording: repeatedly
re-querying the *same* prompt against the *same* llama-server process produced
inconsistent tool-call-vs-plain-text answers even at `temperature: 0`, which looked like a
gateway bug at first — it was llama-server's own prompt/KV-cache reuse across near-
identical requests; a fresh, never-asked prompt reproduced the tool call every time.

**Follow-up (same day)**: live validation surfaced a real gap — after the model called a
tool, the conversation just stopped there. The Playground has no real tool executor (an
arbitrary `get_weather` the operator just typed has no oracle to actually call), so there
was no way to see the model's *final* answer using a tool's result. Added a small "answer
the tool call" form: one text input per pending `tool_call` for a typed mock result, which
gets sent back as `{role: "tool", tool_call_id, content}` message(s) to continue the
conversation. Also surfaced (and documented inline in the UI): `tool_choice: "required"`
forces a tool call on *every* turn, including the follow-up — an operator who leaves it on
"required" after submitting a result gets another tool call, not a final text answer, and
needs to switch to "auto" to see one. Verified live: submitted a mock weather result with
`tool_choice: required` (correctly forced another tool call, confirming the behavior),
then switched to `auto` and got a real final answer incorporating the mock data verbatim.

## RM-36 — Playground: streaming responses (added) — `done`

**Why**: the gateway's `/v1/chat/completions` already has real SSE streaming
(`_stream_response` in `router.py`) — RM-14 deliberately shipped the Playground
non-streaming first to keep the initial redesign scoped.

**Scope**: no backend changes — confirmed via direct curl that the gateway's existing
streaming path already forwards `tools`/`tool_choice` and produces real incremental
`delta.tool_calls` (OpenAI's standard shape: `{index, id?, function: {name?, arguments}}`,
first chunk carries `id`/`name`, later chunks carry only `arguments` fragments to
concatenate by `index`). The backends verified so far never include a `usage` field in
the stream, so token counts are shown as "not reported (streamed)" rather than a fabricated
estimate. Added `streamPlaygroundChat()` in `api/playground.ts` — a plain `fetch()` +
`ReadableStream` reader parsing SSE lines (not a react-query mutation, since progressive
UI updates don't fit that model), reusing `getStoredToken()`/`AUTH_EXPIRED_EVENT` for the
same auth/401 behavior as the axios-based clients. Playground gained a "Stream response"
checkbox; when on, `content` and `tool_calls` accumulate live into an in-progress bubble
(with a blinking cursor) that's replaced by a finalized turn once the stream ends. The
existing Regenerate/tool-result-submission flows both reuse the same send path, so they
stream too when the toggle is on.

**Bug caught during live verification**: the first implementation nested the params object
as a literal `{"params": {...}}` key in the request body instead of spreading it —
`ChatCompletionRequest`'s allowlist silently dropped the whole unrecognized key, so
`tools`/`tool_choice`/`temperature` etc. never reached the backend in streaming mode at
all. Caught by comparing a raw-curl streaming request (which correctly returned
`tool_calls`) against the same prompt through the Playground (which never did) — a
`window.fetch` monkey-patch surfaced the actual request body and the bug. Fixed by
spreading `params` at the top level, matching the non-streaming path's pattern.

**Verified**: `npm run build` type-checks cleanly; full `.githooks/pre-push` green.
Live end-to-end after the fix: plain streaming (visible live text accumulation, "tokens
not reported (streamed)", real latency), and streaming + tool-calling together (a
`get_weather` call correctly accumulated from incremental deltas, answered with a mock
result, and a real final streamed answer using that data) — both via the actual
Playground UI against the real running `gpt-oss-20b-mxfp4`.

**Follow-up bug report (same day)**: user reported some prompts (e.g. "dame un poema de
500 palabras") streamed nothing at all — just an empty bubble under "tokens not reported
(streamed)". Reproduced with the exact prompt via curl in both streaming and
non-streaming mode: `gpt-oss-20b-mxfp4` spent its *entire* `max_tokens` budget on hidden
`reasoning_content` (visibly reasoning about how to count to exactly 500 words) and never
emitted any `content` — `finish_reason: "length"` with `content: ""` in both modes. Not a
gateway or streaming bug — a genuine model/token-budget behavior that was simply invisible
before, in either response mode. Fixed by tracking `finish_reason` per turn and, when a
turn ends with empty content, no tool_calls, and `finish_reason === "length"`, showing an
explicit explanation ("Ran out of max tokens before producing a visible answer... Try
raising Max tokens") instead of a silent blank bubble. Verified live with the exact
reported prompt — the explanation now renders correctly in place of the blank response.

**Second follow-up (same day) — live reasoning display + a real conversation-breaking bug**:
user asked for the model's reasoning to visibly "paint in" during streaming rather than a
static "Streaming…" label sitting frozen for 4-8+ seconds while `reasoning_content` (not
shown anywhere) consumed the whole request. Added a "🧠 Thinking…" box that live-updates
from `reasoning_content` deltas (own auto-scrolling `ReasoningBox` component, same
accumulation pattern as `content`/`tool_calls`), shown only while no real `content`/
`tool_calls` have arrived yet — once the real answer starts, it takes over.

While verifying this, found a second, more serious bug triggered by the *previous*
follow-up's empty-response case: `sendStreaming`'s assistant-message construction used
`content: content || null` — since `content` is `""` (falsy) whenever nothing was
generated, this silently became `null` with no `tool_calls` either, an assistant message
shape the OpenAI API convention forbids (`null` content is only valid *alongside*
`tool_calls`). Once that malformed message sat in conversation history, llama.cpp
rejected *every subsequent request* in that conversation with a 400
(`"Assistant message must contain either 'content' or 'tool_calls'!"`) — sent as a bare
`{"error": {...}}` line without the `data:` SSE prefix, which the parser's
`line.startsWith("data:")` check silently skipped, so the failure surfaced as a blank
response completing in ~10ms with no explanation, indistinguishable at a glance from
"model produced literally nothing." Fixed both ends: `content` is now only ever `null`
when `tool_calls` exist (same fix applied defensively to the non-streaming path, which
happened to already be safe via `?? null` not converting `""`), and the SSE parser now
detects a non-`data:`-prefixed `{"error": {...}}` line and throws it as a real error
instead of dropping it. Verified live: reproduced two consecutive empty-response turns
(different poem prompts) followed by two normal exchanges in between, none of which broke
— confirming the conversation survives repeated empty turns instead of permanently
locking up after the first one.

**Third follow-up (same day) — the "ran out of tokens" case explained, and reasoning kept**:
user asked whether the model's reasoning was genuinely looping, and to stop discarding it
when the "ran out of tokens" message shows — keep it collapsed with a copy button for
external analysis. Investigated with a much higher `max_tokens` (1500) budget on the exact
"300 palabras exactas" prompt: confirmed it's real model behavior, not a bug — the model
composes each line, counts words one by one, finds it's off by N, patches the line, and
recounts, for every single line, and with only ~5 of the 12 lines needed done at 1500
tokens, this scales badly with the requested length rather than ever reliably converging.
Raising `max_tokens` further helps only so much for an "exact word count" ask specifically.

Added: `Turn` now keeps `reasoning` (from `reasoning_content`, both response modes) instead
of discarding it once the stream/response completes. When a turn ends with the "ran out of
max tokens" explanation, a collapsed `<details>` ("Show the model's reasoning (N chars)")
reveals the full text with its own Copy button, so an operator can inspect what the model
was actually doing outside the Playground. Verified live in non-streaming mode: the
disclosure rendered "1,649 chars", expanded to show the real word-counting loop, and the
copy button's `navigator.clipboard.writeText` call was confirmed to receive the exact
1,649-character string.

**Fourth follow-up (same day) — real token counts for streamed responses**: user asked to
show input/output token counts next to "tokens not reported (streamed)". The response never
carries a standard OpenAI `usage` field for `stream: true`, but llama.cpp's final chunk
includes its own `timings` object — confirmed by comparing a non-streaming call's `usage`
against the *same* request's `timings`: `cache_n + prompt_n` matched `usage.prompt_tokens`
exactly, and `predicted_n` matched `usage.completion_tokens` exactly. Real counts, just
under a llama.cpp-specific field rather than the OpenAI one — not a character-based
estimate. `streamPlaygroundChat()` now parses `timings` from the final chunk and computes
real `prompt_tokens`/`completion_tokens`/`total_tokens` from it; the existing "tokens not
reported (streamed)" fallback stays in place for any backend that doesn't emit `timings`
either. Verified live: a streamed response showed "76 + 95 = 171 tokens" instead of the
placeholder text.

**Fifth follow-up (same day) — reasoning viewer for every turn, and a real layout bug**:
two more asks: (1) show the collapsed reasoning disclosure for every turn, not just the
"ran out of tokens" failure case; (2) the chat area grew unbounded as the conversation got
longer, pushing the Parameters/Tools sidebar out of view — needed its own scroll.

(1) was a small render change — the `<details>` block moved out of the failure-only
branch and now renders whenever `turn.reasoning` is non-empty, success or failure alike.

(2) was a real flexbox layout bug: the page used `min-h-screen` (grows with content) with
`flex-1 overflow-y-auto` on the message list — but without every ancestor in that flex
chain capped to the viewport (`h-screen` + `min-h-0` at each level), a flex child's
`overflow-y-auto` never actually engages; it just grows to fit its content like anything
else, taking the whole page and the sidebar along with it. Fixed by making the outer
container `h-screen overflow-hidden` and adding `min-h-0` down the flex chain (main → chat
column → message list), so the message list is the only thing that scrolls internally now.
Also gave the right `<aside>` (Model/Parameters/Tools) its own `overflow-y-auto`, so it
stays fully visible and independently scrollable regardless of how long the conversation
gets. Verified live at a constrained 1400×700 viewport: sent several messages, confirmed
the message list scrolled on its own while the Parameters sidebar stayed fully visible and
static the entire time, and the reasoning disclosure appeared on ordinary successful
responses too.

## RM-37 — Playground: embedding model testing (done)

**Why**: `POST /v1/embeddings` already exists (RM-09) — the Playground's model picker just
filters to `modality === "text"`, so embedding models never show up as testable.

**Scope**: a second "Embeddings" tab next to "Chat" in the Playground — single text input,
no conversation history (each request is independent, nothing accumulates like chat
turns), result shows the vector's dimensionality, a truncated preview (first 8 values),
real token usage, latency, and a copy button for the full vector as JSON. The right
sidebar swaps to just a model picker filtered to `modality === "embedding"` — none of
Chat's params/tools apply to embeddings. Reused `usePlaygroundChat`'s pattern for a new
`useEmbeddings()` hook (`gateway/admin-ui/src/api/playground.ts`) — same real
`POST /v1/embeddings` call, same real usage/cost recording.

**Backend fix required to make this work at all**: `/v1/embeddings`
(`gateway/src/prometheus_gateway/router.py`) had no `admin:write` bypass — unlike
`/v1/chat/completions`'s RM-14 carve-out, so the Playground's admin-dashboard session
(which only carries `admin:*` scopes, no `inference:read`/`model:<id>`) would have gotten
a 403 on every embeddings call. Added the identical bypass used by chat completions.

**Verified**: `gateway/tests/test_modality.py` — new
`test_embeddings_admin_write_bypasses_scope_checks` (14/14 passing). Frontend: `tsc
--noEmit`, `npm run build`, `npm run lint` all clean. Live in the browser: tab switching
works, both empty states render correctly (no running text/embedding models in this dev
environment — verified via Instances showing 0 registered models), no new console errors,
existing Chat mode unaffected. Could not verify a real embeddings round-trip end-to-end —
no embedding-modality backend instance is running locally to test against.

## RM-38 — Image generation model support (done)

**Why**: RM-38 started as a speculative backlog stub with no backend chosen. Backend
research settled on `stable-diffusion.cpp`'s `sd-server` — the ggml/GGUF sibling of
llama.cpp (single compiled C++ binary, CMake build, Metal/CUDA via build flags) that ships
a native OpenAI-images-compatible HTTP server (`POST /v1/images/generations`), unlike
ComfyUI/A1111's full Python+CUDA stacks with no maintained OpenAI-compatible surface.

**Scope**: full feature — new `sd_cpp` backend + `image` modality in manager-core, a
`POST /v1/images/generations` gateway route, and a Playground Images tab. Registering an
image model uses the existing manual per-node registration API (path-based, like any
locally-placed model file) — this does **not** extend the Models page's
Hugging-Face-discovery/download flow to understand diffusion model shapes (multi-file
weight sets, VAE/text-encoder companions); that flow is tuned for single/sharded-LLM-GGUF
search and would need its own design pass.

**Real deviations confirmed by building the actual binary** (not assumed from docs):
`sd-server` takes `-l/--listen-ip` + `--listen-port`, not `--host`/`--port` like every
other backend here — `scanner.py`'s `_BackendSignature` gained per-signature
`port_flag`/`host_flag` fields to accommodate it. It also has no `/health` endpoint at all
(confirmed against the source, not just "unreliable" as one third-party report suggested)
— `/sdcpp/v1/capabilities` is used instead as the readiness probe, since model loading
happens synchronously before the HTTP server starts listening, making any 200 response a
valid readiness signal (`scanner.py`'s new `_health_path()` helper, shared by
`lifecycle.py`'s start-up polling).

**Verified end-to-end through the real running stack**, not mocks: built
stable-diffusion.cpp from source (`-DSD_METAL=ON`), downloaded a real model
(`Green-Sky/SD-Turbo-GGUF`), registered it on the local node via the admin API with
backend `sd_cpp` / modality `image`, started the instance from the dashboard (exercising
`_build_sd_cpp_cmd`, the new `_BackendSignature`, and `_health_path` for real — reached
`ready`), then generated a real image from a prompt through the full stack (browser →
gateway's new route → manager-api → `sd-server`) and confirmed it rendered in the
Playground's Images tab (~13s at default settings, real Metal-accelerated inference).

**Bug found and fixed while verifying** (pre-existing, unrelated to RM-38's own code):
`runtime/manager/api/src/prometheus_manager_api/routes.py`'s `_merge()` read
`entry.__dict__` instead of `entry.to_dict()` — RM-49 turned `backend_url` into a derived
`@property` on `RegistryEntry`, but a bare dataclass `__dict__` omits properties entirely,
so `GET /v1/backends` had been silently dropping `backend_url` for every backend since
RM-49, breaking the gateway's `manager_sync` routing regardless of modality. Fixed at all
7 call sites; added a regression assertion to `test_AC13_correct_scope_returns_list`.

**Verified**: `runtime/manager/core` — 164 tests, mypy, ruff clean. `gateway` — 241 tests
(8 new for `/v1/images/generations`, mirroring the embeddings test set), mypy, ruff clean.
`runtime/manager/api` — 78 tests (extended with the `backend_url` regression check), mypy,
ruff clean. Frontend — `tsc --noEmit`, `npm run build`, `npm run lint` all clean. Full
`.githooks/pre-push` green before pushing.

**Follow-up (Playground Images UX)**: each generated image now has a download button
(client-side `data:` URI → `<a download>`, no new endpoint needed) and opens a full-size
lightbox on click (Escape or backdrop click to close), matching the `createPortal` pattern
already used by `RegisterModelModal.tsx` and friends. Verified live: generated a real
image, opened the lightbox, closed it with Escape, triggered the download with no console
errors.

## PRM-39 — Video generation model support (added, speculative)

**Why**: same origin as RM-38 — flagged, not scoped. Self-hosted video generation is far
less mature/proven than image generation; even lower priority.

**Scope**: undefined. Revisit only if a real need and a viable self-hostable model/backend
both show up.

## RM-40 — Playground: image upload for Vision/VLM models (done)

**Why**: RM-09 already added vision content-part support to `/v1/chat/completions`
(`has_image`/modality checks in `router.py`) and the model registry already tracks
`modality: "vision"` per instance — the Playground just had no way to attach an image, and
in fact **no way to even select a vision model in Chat at all**: the model picker filtered
to `modality === "text"` only, excluding vision entirely.

**Scope**: an image upload/attach control in the Playground's message composer, enabled
only when the selected model's `modality` is `"vision"` (hidden otherwise, so it's never
offered for a model that would just reject it). Sends the image as an `image_url` content
part alongside the text prompt, matching what `router.py` already expects.

**What shipped**:
- `api/playground.ts`: `ChatMessage.content` broadened from `string | null` to `string |
  ContentPart[] | null` (`TextContentPart` / `ImageContentPart`, mirroring the gateway's own
  `schemas.py` shapes exactly — inline `data:image/...;base64,...` only, no remote URLs).
- `Playground.tsx`: the Chat model picker (`runningTextModels` → renamed
  `runningChatModels`) now includes `"vision"` alongside `"text"` — vision models handle
  plain text-only chat fine too (confirmed by the gateway's own `test_modality.py` suite),
  so this was a real pre-existing gap, not something to guard against.
- A paperclip button next to the composer, rendered only when the selected model's
  modality is `"vision"`; picks a file via a hidden `<input type="file" accept="image/*">`,
  reads it with `FileReader.readAsDataURL`, rejects anything over 5MB (a generous cap for a
  single prompt image, given the whole thing gets inlined as base64 into the request body
  and the in-memory conversation history). A thumbnail + filename + remove (×) button shows
  above the composer while an image is staged; switching to a non-vision model clears it
  (an attachment can never be sent to a model that would just reject it).
- `handleSend` builds `content` as a content-parts array only when an image is attached
  (plain string otherwise, unchanged for every non-vision turn) — allows an image-only send
  with no caption text.
- New `MessageContent` component renders either shape (plain string or parts array) the
  same way, replacing every place a message bubble rendered `{m.content}` directly (both
  the completed-turn and in-progress leading-message bubbles, plus the assistant response,
  for type-safety even though a model reply is always a plain string).

**Verified end-to-end through the real running stack**, not mocks: registered a real 8B
vision model (`Qwen3VL-8B-Instruct-Q4_K_M.gguf` + its `mmproj` projector file, both already
on disk but unregistered) as `qwen3vl-8b-test`, started it, confirmed it appeared in Chat's
model picker (previously impossible), confirmed the paperclip button appeared only for it.
Injected a real PNG into the hidden file input via the DevTools `DataTransfer` technique
(a small local CORS-enabled HTTP server served the file since a data: URI can't be
constructed by the test harness directly), got a live thumbnail preview, sent it with the
prompt "What animal is in this image and what is it wearing?" — the model correctly
answered "red panda" wearing a "black pointed witch's hat", holding an open book, matching
the actual test image exactly. Confirmed the message bubble rendered both the prompt text
and the image. Confirmed switching to a non-vision model hid the paperclip button.
Deregistered the test instance afterward. `tsc --noEmit`, `npm run lint`, `npm run build`,
and the full `.githooks/pre-push` (121 tests) all clean.

**Follow-up (found live)**: the paperclip button sat at the bottom of the composer row
(`items-end`) instead of matching the textarea's full height — fixed with `self-stretch`.
Attached images in a message bubble now open a full-size lightbox on click (`cursor-zoom-in`,
Escape or backdrop to close) — a new `expandedChatImageUrl` state (just the data: URL,
unlike Images tab's `expandedImage` which also needs prompt/model for its download button),
wired through a new `onImageClick` prop on `MessageContent`. Verified live: button height
matches the textarea's `getBoundingClientRect()` exactly (58px, same top/bottom); clicking
an attached image opened the real uploaded photo full-size, Escape closed it.

## RM-41 — Playground: show which model answered (done)

**Why**: identified during the Playground redesign — once you can switch models mid-
conversation, a response with no visible model label makes it easy to lose track of which
model actually produced which answer.

**Scope**: small — render the model id next to the Copy button on each assistant response
(the model used for that specific call is already known client-side at response time, no
backend change needed).

**What shipped**: applied to all three Playground tabs, not just Chat — `Turn`,
`EmbeddingResult`, and `ImageResult` each gained a `model` field, captured from the tab's
own selected-model variable at send time (not read back from the response later — the
gateway's response shapes don't echo it reliably enough to rely on, and capturing at send
time is correct by construction even if a later call changes the selection). Rendered as a
small `font-mono` label next to each result's action button (Copy for chat/embeddings,
Download for images); truncates on the width-constrained image cards, with the full id in
a hover title.

**Verified**: live in the browser, all three tabs — Chat: sent one turn on
`gpt-oss-20b-mxfp4`, switched to `qwen3-0-6b-iq4-nl-local-2`, sent another (both
non-streaming and with Stream response enabled) — each turn kept showing the model that
actually produced it. Embeddings: `qwen3-embedding-0-6b-q8-0-local` shown correctly.
Images: `sd-turbo-test` shown correctly, no layout overflow on the narrow image card.

## RM-42 — Playground: animate the "waiting for a response" state (done)

**Why**: identified during the Playground redesign — the current "Waiting for a
response…" text is static and doesn't read as active/alive while a real (sometimes
multi-second) inference call is in flight.

**Scope**: small, purely cosmetic — replace the static string with something that visibly
animates (e.g. an ellipsis cycle, a subtle pulse) so a slow response doesn't look stalled.

**What shipped**: a small `WaitingIndicator` component — the label text followed by three
bouncing dots, applied to all three Playground tabs, not just Chat (Embeddings' "Get
embedding" and Images' "Generate" had the exact same silent-until-done problem — nothing
in the results list signaled work in progress, just a disabled button). First cut reused
Tailwind's built-in `animate-pulse` (2s cycle) with 200ms stagger and text "." characters —
shipped, then found live to be too subtle/spaced out to notice: a 2s opacity fade with only
200ms offset reads as near-synchronous, and text-glyph dots carry their own font spacing.
Replaced with a purpose-built `@keyframes bounce-dot` (`index.css`) — 1.2s cycle, translateY
+ opacity — applied to fixed 4px `rounded-full` circles (not glyphs) via a new
`.animate-bounce-dot` utility class, 150ms stagger, tight `gap-0.5`, same idea as
WhatsApp/iMessage's typing indicator. Used for: non-streaming Chat's "Waiting for a
response", streaming Chat's pre-first-token "Streaming" state, Embeddings' "Generating
embedding", and Images' "Generating image" — each gated on the tab's own pending/sending
flag, shown after any earlier results without replacing them.

**Verified**: `tsc --noEmit`, `npm run build`, `npm run lint` clean. Live in the browser,
all three tabs: Chat — triggered a real generation, read `getComputedStyle` on the three
dots mid-flight, confirmed three distinct `transform: translateY(...)` / `opacity` values
at the same instant, proving the wave is actually visible frame to frame. Images — same
indicator caught live mid-generation ("Generating image •••"), then replaced cleanly by the
real result. Embeddings — completes too fast (~100-150ms) to catch the indicator in an
automated screenshot, but it's the identical gated-render pattern already verified
elsewhere, and the result rendered correctly afterward.

**Follow-up (send-time feedback)**: found live — non-streaming Chat never set `inProgress`
at all, so the user's own message stayed invisible until the *entire* round-trip finished
(the streaming path already showed it immediately via `inProgress.leading`). Fixed by
having `sendNonStreaming` set `inProgress` up front too, same as streaming — the existing
render logic (leading-message bubble + `WaitingIndicator`) then works for both paths
uniformly, so the old separate `isSending && !inProgress` branch became dead and was
removed; the `WaitingIndicator`'s label now reads `streamingEnabled` to say "Streaming" vs
"Waiting for a response". Also: all three tabs cleared their input on *success* rather than
immediately on send, and none of the three disabled their textarea while a request was in
flight — both fixed (input cleared up front, textarea `disabled` while pending) so the
prompt reads as sent right away and can't be edited mid-flight.

**Verified**: live in the browser (non-streaming) — sent a long-response prompt, confirmed
the user's bubble plus the bouncing-dots indicator appeared immediately below it, and
`textarea.value === ""` / `disabled === true` while pending.

**Follow-up (visual consistency across tabs)**: Embeddings and Images rendered the sent
input as plain muted text *inside* the same card as the result — Chat was the only tab with
a distinct question/answer split (an orange `bg-primary` bubble, then a separate card).
Restructured both to match: each result is now a `space-y-3` pair — an `ml-auto w-fit
max-w-[80%] bg-primary` bubble for the input/prompt, then the existing result card
(vector/image + meta row) below it, unchanged otherwise.

**Verified**: live in the browser, both tabs — Embeddings and Images now show the same
bubble-then-card shape as Chat.

**Follow-up (prompt history)**: added shell-style ArrowUp/ArrowDown history recall to all
three tabs' input textareas — a new `usePromptHistory()` hook (history array + browse
index + a stash of whatever was being typed before browsing started), one instance per
tab. ArrowUp only recalls when the caret is on the first line, ArrowDown only when it's on
the last, so multi-line drafts (Shift+Enter) keep normal cursor navigation. Each tab's send
handler records the sent text into its own history right before clearing the input.

**Verified**: live in the browser — Chat: sent two messages, ArrowUp recalled the most
recent then the older one, a third ArrowUp stayed put (no more history), ArrowDown walked
back down to the most recent then to the empty original draft. Embeddings: same recall
confirmed for one sent input.

**Why**: found while scoping RM-35 (tool-calling) — `router.py`'s `_sanitise_messages()`
unconditionally deleted every `role: "system"` message from the client's request before
forwarding it, per an old AC-6 rule ("the gateway itself does not inject system messages
in this spec... any system message from the client payload is treated as an injection
attempt"). Nothing in the codebase ever injects a system message of its own to protect —
the rule just made a client-supplied system prompt silently vanish. That directly broke
the RM-14 Playground's own System prompt field the moment it shipped: typed, sent, and
discarded before reaching the model, with no error to explain why.

**Scope**: removed `_sanitise_messages()` and its call site entirely — a caller has
already passed `inference:read`/`inference:stream` + `model:<id>` authorization by the
time messages are forwarded, so their own system prompt is legitimate input, exactly like
calling OpenAI's API directly. `payload["messages"]` now comes straight from
`ChatCompletionRequest.to_llama_payload()` (which already builds it from the validated
`body.messages`), with no separate sanitisation step for either the streaming or
non-streaming path (both share the same payload construction).

**Verified**: rewrote `test_gateway_core_AC6_strip_injected_system_message` (now
`test_client_system_message_is_forwarded`) to assert the opposite of the old behavior —
a system message now reaches the mocked backend intact. Full gateway suite green
(209/209), full `.githooks/pre-push` green.

## RM-44 — Dashboard: light/dark mode, auto-detected + manual toggle (done)

**Why**: the admin-ui only had one (light) palette — no dark mode, and no way to tell what
the user's OS is set to.

**Scope**: confirmed easier than a typical retrofit, as anticipated when this item was
added — `src/index.css` already centralized every color as a semantic Tailwind v4 `@theme`
token (`--color-background`, `--color-surface`, `--color-text`, `--color-text-muted`,
`--color-primary`, `--color-primary-foreground`) and every component consumes those tokens
rather than literal color utilities, so dark mode needed **zero changes to any page or
table component** — only the token definitions and the toggle itself.
- `index.css`: a `.dark { --color-*: ... }` block redefines the same 7 tokens. Not a naive
  inversion — `surface` sits one step lighter than `background` so cards still read as
  raised panels, and `primary` is bumped from `#ea580c` to `#f97316` (one Tailwind orange
  step brighter) since saturated accent colors read muddier against dark backgrounds at
  the same lightness. A `dark:` Tailwind variant (`@custom-variant`) wasn't needed at all —
  since every component already resolves color through the CSS custom properties, toggling
  a `.dark` class on `<html>` cascades everywhere automatically.
  `Sidebar.tsx` was deliberately left on its existing hardcoded dark navy (`bg-gray-900`
  etc.) rather than converted to tokens — it already reads as fixed "chrome" today, a
  legitimate pattern (persistent dark sidebar regardless of content theme, same as several
  real dashboards), and touching it wasn't necessary for the ask.
- `context/ThemeContext.tsx` (new): three-way `mode` (`"light" | "dark" | "system"`,
  persisted to `localStorage`), matching the existing `AuthContext`/`ToastContext`
  provider-hook shape. `resolvedTheme` is a plain derived value computed during render
  (`mode === "system" ? (systemPrefersDark ? "dark" : "light") : mode`) rather than synced
  via an effect — the codebase's lint rules (`react-hooks/set-state-in-effect`) reject
  `setState` calls driven by a prop/state change inside an effect, a pattern hit twice
  earlier this session (RM-48's settings modal, badge component) and avoided here from the
  start. The one genuine effect subscribes to `matchMedia("(prefers-color-scheme: dark)")`
  and updates a `systemPrefersDark` boolean — a real external-system subscription, not a
  derived value — so the OS theme changing live while "system" is selected re-resolves
  immediately, no reload needed.
- `index.html`: a small inline script (before any React code loads) reads the stored mode
  and applies the `dark` class synchronously, to avoid a light-to-dark flash on first
  paint — the standard technique for this exact problem, since React's own effect only
  runs after first render/paint.
- `Sidebar.tsx`: a 3-icon segmented control (`Sun`/`Monitor`/`Moon` from the already-used
  `lucide-react`) in the existing bottom `border-t` section, above Logout — the active mode
  highlighted, `role="radiogroup"` for accessibility.
- `main.tsx`: `ThemeProvider` wraps the whole tree (outermost, above `QueryClientProvider`)
  so theming also applies to the unauthenticated `/login` screen.

**Verified**: `tsc`/`eslint`/`vite build` clean. Full `.githooks/pre-push` green. Live in
the browser: confirmed System is the default on first load; switched to Dark and checked
Overview, Instances, and Models/Library (including a table with badges/pills and an Edit
modal) — every surface repainted correctly with no light-mode remnants; switched back to
Light and to System; confirmed the choice persists in `localStorage` across a reload.

**Follow-up**: the user caught what the first verification pass missed — status/modality/
quantization badges (`StatusBadge`, `Badge`/`ModalityBadge`, `CircuitBadge`,
`UserStatusBadge`) plus a few banners/toasts (`WarningBanner`, `ToastContext`, two error
boxes in `Playground.tsx`) carry their own fixed semantic color (`bg-green-100
text-green-700` and similar) rather than resolving through the `--color-*` tokens — by
design, since a badge's meaning (success/warning/error) is independent of theme, so it was
never going to be a token. In dark mode these rendered as light pastel chips sitting on the
new dark surfaces — legible, but visually disconnected from everything else, exactly what
RM-44's original scope note called "meaningfully easier... without touching individual
components at all, unless some inline literal color turns up" — one did.
- Added `@custom-variant dark (&:where(.dark, .dark *));` to `index.css` (Tailwind v4's
  equivalent of `darkMode: 'class'`) so `dark:` utilities work, keyed off the same `.dark`
  class the token overrides already use.
- Every affected file got a `dark:bg-{color}-500/15 dark:text-{color}-400`-style pair
  alongside its existing light classes — a translucent tinted background at reduced
  opacity plus a brighter text shade, rather than solid dark-shade swatches guessed by eye;
  reads as an intentional, cohesive "dark badge" treatment rather than a token inversion.
  Banners/toasts (larger surfaces, not tiny pills) got the analogous
  `dark:border-{color}-900 dark:bg-{color}-500/10 dark:text-{color}-300` treatment.
- Deliberately left alone: plain colored text with no background (`text-red-600` etc. used
  for inline error messages across several routes) — already reads fine against a dark
  surface, and hover-only light-tint backgrounds on icon buttons (delete/edit affordances
  in `InstanceRow.tsx`/`UserRow.tsx`/`NodeRow.tsx`/`DownloadedModelsTable.tsx`) — a brief
  hover flash is far less visually disruptive than the permanently-visible badges that
  prompted this, so left as a lower-priority follow-up rather than expanding this change
  into every hover state across four more files.
- **Verified**: full `.githooks/pre-push` green again. Live in the browser: Library table
  badges (Text/Vision modality, quantization, Ready/Stopped status) now sit correctly on
  the dark surface; confirmed light mode is pixel-identical to before (the `dark:` classes
  are additive, inert unless `.dark` is present).

## RM-45 — Let a client list which models it's actually allowed to use (done)

**Why**: `GET /v1/models` (`gateway/src/prometheus_gateway/router.py`) is unauthenticated
and lists every active model in the registry — it never looks at the caller's own JWT at
all, so a real registered client (an "App"/"Agent" role principal with a handful of
`model:<id>` grants, not an admin) has no way to ask the gateway "of everything that
exists, which of these am I actually authorized to call?" They'd have to already know
their own granted scopes out-of-band, or discover access by trial-and-error 403s. Access
can also be granted or changed after the client was created, so this isn't a one-time
lookup — the client may want to re-check before every batch of requests.

**Scope**: added a new endpoint, `GET /v1/models/mine`, rather than extending the existing
public `GET /v1/models` — the two have genuinely different security postures (one is
deliberately open, the other must read the caller's own grants), and `/v1/models/mine`
is NOT in the JWT middleware's `EXEMPT_PATHS`, so it gets the same Bearer-token
enforcement as every other protected route for free, with zero middleware changes.
Handler filters `registry.list_active_models()` down to entries where
`claims.has_model_scope(model.id)` is true (reusing RM-07's existing per-model scope
check verbatim); `admin:write` bypasses the filter and sees every model, matching the
RM-14 Playground carve-out (admin:write already implies full model management, so this
isn't a new privilege). A client with zero `model:<id>` grants gets a 200 with an empty
`data: []`, not an error — it's a legitimate state (client onboarded, no models
assigned yet), same shape as `GET /v1/models`.

**Verified**: `gateway/tests/test_gateway_core.py` — 401 with no token, filters to only
the granted `model:<id>` scopes, empty list for a token with zero model grants,
`admin:write` sees every active model. `uv run --project gateway pytest` green.

## RM-46 — Per-model performance metrics: avg response time, TTFT, inter-token latency (done)

**Why**: `GET /metrics` only tracked overall request latency percentiles (p50/p95/p99)
across all backends combined — nothing per-model, and nothing like time-to-first-token
(TTFT) or inter-token latency existed anywhere in the codebase.

**Scope**: per-backend latency tracking in `MetricsStore` (mirrors the existing global
p50/p95/p99, scoped per `backend_id`), plus two new measurements:
- **TTFT**: captured in `_stream_response()`'s `event_generator()` — the wall-clock time
  from `backend_start` to the first chunk whose `choices[0].delta.content` is non-empty
  (not the empty role-only opening chunk some backends send first). Streaming-only by
  construction — a non-streaming response has no distinguishable "first token" moment.
- **Inter-token latency**: llama.cpp's own `timings.predicted_per_token_ms`, confirmed live
  against a real running instance (both streaming and non-streaming responses carry a
  top-level `timings` object) — read directly, no need to compute it from token deltas.
  mlx/vllm/sglang backends don't send `timings` at all, so this (and TTFT) are `None` for
  them rather than a fabricated 0.

**What shipped**: `MetricsStore.record_inference()` gained optional `ttft_ms`/
`inter_token_ms` params; new per-backend sliding-window deques (same `_MAX_LATENCY_SAMPLES`
approach as the existing global one) for latency, ttft, and inter-token; `snapshot()`
exposes `latency_p50_ms`/`latency_p95_ms`/`ttft_p50_ms`/`inter_token_ms_avg` per backend,
with `None` (not 0) until a backend actually reports a value. `router.py` populates these
in both the streaming and non-streaming paths. Frontend: a single new "Latency" column on
the Instances table (not 4 — the roadmap's own caution against over-columning an already-
wide table) showing `p50 ms`, with the full breakdown (p50/p95/TTFT/inter-token) in a hover
tooltip; "—" for a backend with no samples yet rather than a misleading "0ms".

**Verified**: `gateway` — 246 tests (5 new, `test_metrics_store.py`), mypy, ruff clean.
`tsc --noEmit`, `npm run lint`, `npm run build` clean. Live, end-to-end: restarted the
gateway, sent one non-streaming and one streaming real chat-completions request to
`gpt-oss-20b-mxfp4`, confirmed `GET /metrics` returned `ttft_p50_ms: 791` and
`inter_token_ms_avg: 8.55` for that backend, and the Instances page's new Latency column
showed "837ms" with the full breakdown in its tooltip — every other (untouched) instance
correctly showed "—".

**Follow-up (throughput + columns instead of tooltip)**: after seeing it live, the user
asked what p50/p95/TTFT/inter-token actually mean, then asked for two changes — the four
values as real table columns instead of hidden behind a hover, and a web search for what
other metrics matter for LLM inference. Research (vLLM/TGI/TensorRT-LLM sources) confirmed
TTFT, TPOT/inter-token, throughput (tokens/sec), and e2e latency as the field's own "core
four" — throughput was the one still missing here, and cheap to add (the gateway already
computed `tps` for its own log lines, just never stored it). Added `tokens_per_second`
to `record_inference()`/`MetricsStore` (same None-until-reported pattern as ttft/
inter_token) and split the single "Latency" column into five: P50, P95, TTFT, Tok/s,
ms/tok — the roadmap's own earlier caution against over-columning was a reasonable
default, but an explicit ask from the person using the page overrides it.

**Bug found and fixed while wiring throughput**: `_stream_response()`'s streaming path
only read token counts from a per-chunk `usage` field, but llama.cpp's own streaming
chunks never carry one (confirmed live) — only the final chunk's `timings` object does.
`prompt_tokens`/`completion_tokens` were silently staying 0 for every llama.cpp streaming
request, which meant `tokens_per_second` (and the *global* `tokens_prompt_total`/
`tokens_completion_total` counters, pre-existing and unrelated to RM-46's own new fields)
were wrong for streaming. Fixed by falling back to `timings.cache_n + timings.prompt_n` /
`timings.predicted_n` when present — the exact mapping already verified client-side for
RM-36's Playground token counts, just not applied server-side until now.

**Verified**: gateway — 248 tests (2 more added for throughput), mypy, ruff clean.
`tsc --noEmit`, `npm run lint`, `npm run build` clean. Live: restarted the gateway, sent a
real streaming request, confirmed `GET /metrics` now returns `tokens_per_second_avg: 105.68`
(previously `null` from the bug above) alongside correctly non-zero global
`tokens_prompt_total`/`tokens_completion_total`, and the Instances page shows all five
columns populated for the tested model, "—" for every other.

**Follow-up (header tooltips)**: with the five metrics now visible as bare column headers
(P50/P95/TTFT/Tok/s/ms/tok), the abbreviations weren't self-explanatory. `COLUMNS` in
`InstanceTable.tsx` changed from a flat string array to `{label, title?}` objects — a
`title` attribute on the `<th>` (plus `cursor-help`) explains each metric on hover, once,
at the column level, rather than repeating the same static text on every row's cell (the
per-cell `title`s from the earlier tooltip-based design were removed as the now-redundant
duplicate). Non-metric columns (ID, State, Uptime, ...) get no `title` — nothing there
needs explaining.

**Verified**: live in the browser — `document.querySelector('th')` for "P50" returns the
full explanatory `title` text and `cursor: help`; all five metric headers carry their own
description, the other 11 columns carry none.

**Follow-up (Spanish tooltips + embeddings/images had zero metrics at all)**: translated
the five header tooltips to Spanish per an explicit request (the rest of the admin UI stays
English — this was a targeted ask, not a full i18n pass). The user also asked why TTFT
never populates for non-streaming calls (answered: non-streaming has no distinguishable
"first token" moment — the whole response arrives in one block, so there's nothing to
measure separately from total latency; not a code limitation) and why embeddings/image
models showed no metrics at all. The second question uncovered a real bug: `/v1/embeddings`
and `/v1/images/generations` never called `record_inference()` at all — not just missing
ttft/inter_token (which don't conceptually apply to a single blocking call anyway), but
missing *even basic latency*. Fixed both routes to time the backend call and record
latency + prompt-token count (embeddings) or just latency (images — no token count for an
image response), including on the error paths.

**Verified**: gateway — 248 tests (unchanged; the fix is additive telemetry with no change
to either route's response contract, so the existing functional tests remain the
correctness check), mypy, ruff clean. Live: restarted the gateway, sent a real embeddings
request and a real image-generation request — `GET /metrics` now shows
`qwen3-embedding-0-6b-q8-0-local` at 73ms and `sd-turbo-test` at 12913ms, both previously
absent from the backends map entirely.

**Follow-up (embeddings/images-specific throughput)**: with basic latency now recorded for
both modalities, the user asked what other metrics matter specifically for embedding and
image-generation serving (their existing ttft/inter_token/tokens_per_second columns don't
apply to either — no streaming, no completion tokens). Research confirmed two workload-
specific throughput analogs: **input tokens/sec** for embeddings (there's no completion
side, but the existing `tokens_per_second` field is genuinely just as meaningful computed
from `prompt_tokens` instead — reused the same field/column rather than adding a new one)
and **images/sec** for image generation (a new `images_per_second` field/column, since
there's no token concept at all for that workload). "Steps per second" was also identified
as a standard diffusion-serving metric but explicitly not implemented — `sd-server` exposes
no `timings` object and per-request step count isn't currently a known/passed parameter, so
it would need its own separate design pass.

**What shipped**: `MetricsStore` gained an `images_per_second` param/deque (mirrors
`tokens_per_second`'s None-until-reported pattern exactly); `/v1/embeddings` now passes
`tokens_per_second=prompt_tokens/(latency_ms/1000)`; `/v1/images/generations` now passes
`images_per_second=num_images/(latency_ms/1000)` (`num_images` from the response's `data`
array length, so `n > 1` is accounted for). New "Img/s" column on the Instances table, and
the "Tok/s" header tooltip updated to cover both its chat-output and embeddings-input
meanings.

**Verified**: gateway — 250 tests (2 more added for images_per_second), mypy, ruff clean.
`tsc --noEmit`, `npm run lint`, `npm run build` clean. Live: restarted the gateway, sent a
real embeddings request and a real image-generation request — `GET /metrics` returned
`tokens_per_second_avg: 145.35` for `qwen3-embedding-0-6b-q8-0-local` and
`images_per_second_avg: 0.079` for `sd-turbo-test` (both previously `null`), and the
Instances page shows both new/updated columns correctly populated, "—" for the other
metric each doesn't apply to.

## PRM-47 — Evaluate whether `GET /v1/models` should stay unauthenticated (added)

**Why**: raised while building [[RM-45]] — `GET /v1/models` is intentionally public today
(`gateway/AGENTS.md`: "it only lists active model IDs, no user data or inference
capability"), and the README's own quickstart uses it as the first unauthenticated `curl`
to confirm the gateway is alive before a caller has credentials. With RM-45's
`/v1/models/mine` now covering "what am I authorized to use," the original motivation for
also exposing the full catalog publicly is worth re-checking rather than assumed — but
nothing has actually changed about the tradeoff yet, so this is a review item, not a
decision to remove it.

**Scope**: confirm what, if anything, actually depends on unauthenticated access (the
README quickstart curl, any SDK/client bootstrap flow, the "Unknown Model" error message
in `router.py` that points callers at `GET /v1/models`) before considering tightening it.
Model catalog contents (IDs, family, quantization, context length) aren't sensitive on
their own — this is about whether *discoverability without credentials* is still wanted,
not about hiding secrets. Leaning against changing it unless a concrete reason turns up.

## RM-48 — Dashboard: Models page — discover, download, and manage the model lifecycle (done)

**Why**: today "Register model" (`RegisterModelModal.tsx`) is pure free-text entry — a
local `path`, or an `hf_repo`/`hf_filename` pair typed by hand, no search, no model card,
no record of what's actually been vetted or downloaded before it's wired up for instance
creation. The user wants a real lifecycle: browse/search open-source models, read the
model card, download to a configurable folder with visible progress (cancel/resume/
delete), and — critically — restrict instance creation to only models that have actually
been downloaded through this flow, not any path/repo string someone happens to type.

**Source: Hugging Face, not Kaggle or another hub** — and this isn't a from-scratch
choice, it's already the established one in this codebase:
- `runtime/manager/core/src/prometheus_manager_core/downloader.py` already implements
  resumable HF downloads with real-time progress, SHA-256 verification,
  and a `queued/downloading/verifying/done/failed/cancelled` status machine — used today by
  the terminal TUI's own Downloads view (`runtime/manager/tui/.../views/downloads.py`).
- The TUI also already has a **Discovery view** (`views/discovery.py`) that searches
  Hugging Face and does "one-key download" — GGUF quantizations for llama.cpp are
  overwhelmingly published there (bartowski, unsloth, and similar community quantizers),
  so this is where the models this platform actually runs already live.
- `[downloads]` in `manager.toml` already has a configurable download directory
  (`resolved_downloads_dir`) — the "configurable download folder" part of the ask is
  already satisfied, not new work.
- Kaggle Models skews toward notebook-era TensorFlow/PyTorch model zoo entries, not
  llama.cpp-ready GGUF artifacts — there's no existing integration to build on and
  materially less relevant inventory for this platform's use case. Not worth building a
  second hub integration unless a real need for it shows up.

**Scope decisions made during implementation** (both confirmed with the user before
coding): "resume" means restart-from-scratch, not real HTTP byte-range resume — nothing
in `downloader.py` supports partial-file resume today, and adding it was out of scope for
this pass; and deleting a downloaded model deletes the on-disk `.gguf` file(s) too (not
just the registry entry), blocked with a 409 if an instance is currently running on it.

**What shipped**:
- `runtime/manager/core/src/prometheus_manager_core/hf_discovery.py` (new) — the TUI
  Discovery view's pure helpers (`infer_quant`/`auto_id`/`next_free_port`/
  `shard_filenames`/`ssl_env`) moved here as the canonical versions (TUI re-exports them
  under their old private names, zero behavior change, its own test suite still passes
  unmodified), plus new `search_models`/`list_model_files`/`fetch_model_card` functions
  (the model-card fetch has no TUI precedent — `huggingface_hub.ModelCard.load`).
- `runtime/manager/api/src/prometheus_manager_api/discovery.py` (new) — `GET
  /v1/models/search[/files|/card]`, `POST /v1/models/downloads` (registers a
  `downloaded=False` entry immediately, then downloads each shard in a background
  asyncio task — mirrors the TUI's own `App._do_download`/`action_discovery_download`
  sequence exactly, down to marking `downloaded=True` + `path` only once every shard
  reports `done`), `GET /v1/models/downloads` (progress polling), `POST
  .../downloads/{id}/cancel|retry`, `DELETE /v1/models/{id}/downloaded`.
- Gateway proxy: `/admin/api/nodes/{node}/models/search[/files|/card]` and
  `/models/downloads[/{id}/cancel|retry]` and `/models/{id}/downloaded`, same
  `_resolve_node`/`manager_client`/`_passthrough` pattern as every other admin proxy route.
- New **Models** page (`routes/Models.tsx`): search box + results, file list + model
  card (raw markdown, no renderer dependency added) for the selected repo, a downloads
  panel with live progress bars (polled every 2s) and cancel/retry, and a downloaded-models
  list with delete.
- `RegisterModelModal`'s `hf_repo`/`hf_filename`/`hf_sha256` inputs removed (they never
  actually downloaded anything from the web dashboard — lifecycle.py's `start_instance`
  has no download step at all, so those fields were silently non-functional dead ends
  before this item); a note in the modal now points to the Models page instead. The
  `path` field stays, for the legitimate separate case of a `.gguf` already on disk
  outside this flow.

**Real bug found and fixed along the way**: `Registry.reload()`
(`runtime/manager/core/.../registry.py`) crashed with `FileNotFoundError` when
`registry.yaml` didn't exist yet — harmless once at least one model has ever been added
(the file exists from then on), but the very first model ever downloaded through this new
flow calls `reload()` before any file exists. Fixed to no-op (matching `__init__`'s own
"only load if the path exists" behavior) instead of crashing.

**Verified**: full `.githooks/pre-push` green across all 6 Python packages + admin-ui
(gateway 228, auth-service 83, telemetry 30, manager-core 154, manager-api 60, manager-tui
38 tests, plus 121 bash tests) — new coverage in `test_hf_discovery.py`,
`test_discovery.py` (manager-api, including a direct async test of the background
download orchestration function, since FastAPI's `TestClient` tears down its event loop
right after each request and can't reliably progress a fire-and-forget `asyncio.create_task`),
and `test_admin.py`. Live end-to-end in the browser against real Hugging Face data: searched
"llama-3.2" (real repos/download counts back), listed a repo's GGUF files, rendered a real
model card, downloaded `unsloth/SmolLM2-135M-Instruct-GGUF`'s Q2_K file (84 MB) to
completion with live progress, confirmed it appeared in Downloaded models, then deleted
it — confirmed both the on-disk file and the registry entry were gone afterward.

**Follow-up (same day)**: user hit `Unknown Node — Node 'Remote GPU' is not configured`
searching from the Models page — the node `<select>` was populated from `useNodes()`
(`api/instances.ts`, names only, no `is_active`), which lists every node regardless of
reachability, while `fetch_nodes()` on the manager-api side silently filters to active
nodes only. Selecting an inactive node was always going to 400. Fixed by switching
`Models.tsx` to `useNodeRegistry()` (`api/nodes.ts`, full objects) and filtering to
`is_active` client-side; the empty-state message also now distinguishes "no nodes exist"
from "nodes exist but none are active."

**Follow-up (same day)**: three usability requests after trying the search — a sort
control, file sizes before downloading, and the file list stretching the whole page.
- **Sort**: `search_models()` now accepts `sort` (one of `downloads`/`likes`/
  `created_at`/`last_modified`/`trending_score` — huggingface_hub's own accepted
  values, re-declared as `SORT_OPTIONS` rather than importing its private
  `ModelSort_T` alias) and passes it straight to `list_models(sort=...)`, which already
  returns highest/most-recent first — no separate "direction" needed. Threaded through
  the gateway proxy and a "Sort by" `<select>` next to the search box.
- **File size**: `list_model_files()` switched from `list_repo_files()` (filenames only)
  to `HfApi().model_info(repo_id, files_metadata=True)`, whose `siblings` carry a real
  `.size` per file — shown next to the quantization tag.
- **Scroll**: the file-list `<div>` under a selected repo had no `max-height`, so a
  repo with many quantizations (common — bartowski-style quantizers often publish 7+)
  pushed the whole page taller. Capped at `max-h-64 overflow-y-auto`, matching the
  pattern already used for the search-results list and the model-card panel.

**Follow-up (same day)**: real pause/resume — a genuine HTTP byte-range resume, not the
retry-from-scratch semantics the user originally chose for RM-48's first pass. Also
capped the "Downloaded models" list at `max-h-72 overflow-y-auto` (same class of
unbounded-height issue as the file-list fix above, just on a different list — this
environment alone has 29 downloaded models).
- `downloader.py`: `Status` gained `"paused"`, `DownloadState` gained
  `pause_requested` (mirrors `cancel_requested`, but keeps the partial file instead of
  deleting it). `download_model()` gained `resume: bool` — when true and a partial file
  exists, sends `Range: bytes={existing}-` and appends; the *server's actual response
  code* is the source of truth (206 = honored, appends; anything else = Range was
  ignored, falls back to a full fresh download rather than corrupting the file by
  appending onto stale/mismatched data). `Content-Range`'s total supersedes
  `Content-Length` when resumed, since the latter is only the remaining bytes.
- `manager-api/discovery.py`: `_run_download` split into `_kick_download` (fresh —
  unchanged behavior) and `_download_shards` (works over existing `DownloadState`s —
  skips shards already `"done"`, resumes one that's `"paused"` via `resume=True`, stops
  without touching later `"queued"` shards on a fresh pause so a later `/resume`
  continues the sequence exactly where it left off). New `POST .../pause` (409 if
  nothing's active) and `POST .../resume` (404 if nothing's paused).
- Gateway: the existing generic `.../downloads/{id}/{action}` proxy just needed `pause`/
  `resume` added to its allowed-actions tuple — no new route.
- Frontend: `usePauseDownload`/`useResumeDownload` hooks; `DownloadRow` shows Pause+Cancel
  while active, Resume while paused (amber progress bar/status for the paused state).

**Follow-up**: UX/UI redesign — the page worked but felt cramped and under-featured next to
LM Studio/Ollama (researched via web search: LM Studio's right-side detail panel on model
selection and flat model directory; Ollama's env-var-driven storage path and third-party
download-manager GUIs). Four asks, all shipped:
- **Settings**: gear button opens `ModelSettingsModal.tsx`, backed by new `GET`/`PATCH
  /v1/models/config` on manager-api (downloads dir, HF token env var, CA bundle) proxied
  through the gateway at `/admin/api/nodes/{node}/models/config`. Deliberately in-memory
  only — `ManagerConfig` retains no reference to the TOML path it was loaded from, so
  persisting to disk was out of scope for this pass; the modal says so explicitly ("changes
  apply immediately... aren't written to manager.toml").
- **Downloaded models as a real table**: `DownloadedModelsTable.tsx` (Name, Modality,
  Family, Quantization, Context, Size, Status, Actions), replacing the old plain list.
  Size comes from a new `file_size_bytes` field on `InstanceEntry`, computed server-side by
  `_file_size_bytes()` in manager-api's `routes.py` (sums shard file sizes on disk, `None`
  if not downloaded or a file's missing — never raises).
- **Click-to-preview**: clicking a row opens `ModelPreviewPanel.tsx` on the right —
  metadata (family/backend/context/size/port/node) plus the model card, via a new shared
  `ModelCardView.tsx` (also reused by the Discover tab's existing card toggle, so both
  render identically off one `useModelCard` call).
- **Layout**: page restructured into "Discover"/"Library" tabs (`Models.tsx`) — Discover
  keeps search/files/downloads but now spans 3 columns on wide screens instead of 2;
  Library is the new table, full width, with the preview panel sliding in beside it. Gear
  button and node picker moved to a shared header above the tabs.
- New `Badge.tsx` (generic pill + `ModalityBadge` with per-modality tone) reused across the
  table and preview panel; `formatBytes` moved from `Models.tsx` into `lib/format.ts` so
  both the table and the existing download-progress rows share one implementation.
- **Real bug caught before ship**: the new `PATCH .../models/config` gateway route 502'd
  because Starlette matched the pre-existing wildcard `PATCH .../models/{model_id}` route
  first (registered earlier) — literal "config" was being treated as a `model_id` and
  proxied to a nonexistent manager-api endpoint. Fixed by registering the two new
  `/models/config` routes before the wildcard PATCH/DELETE routes, with a comment to
  prevent regression.
- **Verified**: full `.githooks/pre-push` green (all Python packages + admin-ui build/lint
  + 121 bash tests). Live in the browser against the real local demo stack (30 downloaded
  models from `registry.yaml`): tab switching, settings modal loading/saving real config,
  table rendering real modality/family/quant/size/status per model, row-click preview with
  a real Hugging Face model card, Discover search/file-list/download unaffected.

**Follow-up**: three small Library-tab requests — a row index, sortable columns, and an
edit action — plus resolving where a model's name can be set at all.
- **Row index**: `DownloadedModelsTable.tsx` gained a leading `#` column, numbered by
  current sort order (matches `InstanceTable.tsx`'s existing `rowNumber` pattern).
- **Sortable columns**: Modality/Family/Size/Status headers are now click-to-sort buttons
  (`ArrowUp`/`ArrowDown`/`ArrowUpDown` from lucide, toggling asc/desc on repeat clicks),
  sorted client-side with a `useMemo` — no new API surface needed since the full list is
  already in memory.
- **Rename question resolved with the user**: this repo already made a deliberate call
  that a model's `id` is not editable in place (`RegisterModelModal.tsx`'s comment: "moving
  a model to a different node or renaming it isn't a field edit, it's a re-registration" —
  enforced server-side too, `_UPDATABLE_FIELDS` in manager-api's `control.py` excludes
  `id`). Rather than break that, the resolution is mixed: a name can only be *chosen* at
  download time (Discover tab gained a "Model name (optional)" field, passed as the
  already-supported-but-unexposed `model_id` on `StartDownloadRequest` — the backend has
  taken this field since RM-48 shipped, the UI just never had an input for it); once
  downloaded, only other fields are editable.
- **Edit action**: reused `RegisterModelModal` in its existing edit mode (`editing` prop)
  rather than building a new modal — it already disables node/ID and edits everything else
  (family, quantization, backend, modality, context length, path, discovery). Wired a
  `Pencil` button next to Delete in each row. Hit the same lazy-`useState`-only-runs-once
  bug the Instances page had already solved: without a `key={editingModel?.id}` on the
  modal, editing model A then model B reused A's stale form state. Fixed by copying
  `Dashboard.tsx`'s existing `key`-per-instance pattern.
- **Verified**: full `.githooks/pre-push` green. Live in the browser: sorted by size and
  status and confirmed order/arrows update; edited a real downloaded model's family field
  end-to-end (confirmed the table cell changed, then reverted it); confirmed the edit modal
  correctly reset between two different models (the bug above); typed a custom name into
  the new Discover-tab field and confirmed it renders next to the file list.

**Follow-up**: a data-quality pass surfaced two more issues, closing the loop on this
item's original "Why" (the Instances page's Register-model flow was still pure free-text,
letting an operator type any `path`/`family`/`modality`/`quantization` regardless of what's
actually downloaded).
- **Modality corrections**: reviewed all 30 registry entries — two are real vision-language
  architectures (`llava-mistral-7b-q5` — LLaVA; `qwen3vl-32B-Q4` — Qwen's VL line) that were
  tagged `modality: text`, and one is an embedding model (`qwen3-embedding-0-6b-q8-0-local`,
  repo literally `Qwen3-Embedding-0.6B-GGUF`) also tagged `text`. This wasn't cosmetic:
  `lifecycle.py`'s `_build_llama_cpp_cmd` only appends `--embedding` (or `--mmproj`) based
  on this field, so the embedding model would have started as a plain completion server.
  Fixed all three via the Library tab's edit action.
- **Instances page create flow, model picker**: `RegisterModelModal`'s create mode replaced
  free-text Path/Family/Quantization/Modality/mmproj-path inputs with a "Model" `<select>`
  populated from already-downloaded models on the chosen node (`Dashboard.tsx` now derives
  `downloadedModels` from its existing `useInstances()` call and passes it down — no new
  fetch). Picking one derives those fields plus `backend`/`context_length`/`hf_repo`/
  `hf_filename`/`hf_sha256` into the form; they render as disabled inputs so the operator
  can see what was inherited but not drift it from what the file actually is. Edit mode is
  untouched — this only applies to registering a *new* entry, matching the existing
  registry-key-is-immutable stance. The picker is filtered to the selected node (a
  downloaded file only exists on its own node) and resets when the node changes; an empty
  list shows a hint pointing at the Models page instead of a dead-end dropdown.
- **Verified**: full `.githooks/pre-push` green. Live in the browser: switched node from an
  unreachable one (empty picker, correct hint) to `local` (all 30 models listed); selected
  `llava-mistral-7b-q5` and confirmed every derived field populated correctly (including
  `vision` modality and the empty mmproj hint, matching its real gap); registered a second
  instance of that same file end-to-end, confirmed it appeared in the instance count, then
  deleted it to keep the demo registry clean.

**Verified**: `downloader.py` — new tests for pause-keeps-partial-file, resume sends the
correct `Range` header and appends, resume with nothing on disk behaves like a fresh
download, and resume falls back to a full restart when the server ignores Range (200
instead of 206) rather than silently corrupting the file. `discovery.py` — pause/resume
HTTP tests plus a direct `_download_shards` test proving a `"done"` shard is skipped and
a `"paused"` one is resumed with `resume=True`. Live end-to-end against real Hugging
Face: started downloading a 270 MB file, paused at 65 MB (partial file confirmed on
disk), resumed — `downloaded_bytes` continued from 65 MB (not 0), completed at exactly
270,885,952 bytes matching the expected size, `downloaded=True` set correctly. Full
`.githooks/pre-push` green across all 6 Python packages + admin-ui.

## RM-49 — Model registry: migrate registry.yaml → SQLite (done)

**Why**: user asked why the model registry still used a YAML file when `auth-service` and
the gateway's usage tracking (RM-32) both already use SQLite. Investigating surfaced a real
durability bug: `Registry._save()` did a full-file `open(path, "w")` + `yaml.safe_dump()`
on every single mutation — no locking, no atomic temp-file+rename, so a crash mid-write
could truncate/corrupt the entire registry. User's call: migrate, and fix the durability
issue as part of it (the migration itself fixes it — SQLite commits are atomic).

While doing the migration, the user also asked to evaluate whether every field in
`RegistryEntry` still earned its place — not just carry the schema over 1:1.

**Scope — storage engine**: Python's stdlib `sqlite3`, synchronously — deliberately *not*
async SQLAlchemy (the gateway's RM-32 pattern). `Registry` is called synchronously
everywhere it's used today — manager-api's route handlers (unawaited, inside `async def`),
the `pmgr-api`/`pmgr` CLIs, and the Textual TUI (plain sync callbacks and
`@work(thread=True)` background threads) — and making it async would have forced changes
across every call site in 3 packages. Kept `Registry`'s public method signatures
byte-for-byte identical (`get`/`add`/`update`/`remove`/`reload`/`entries`), so only
`registry.py`'s internals changed. One `sqlite3.Connection` per `Registry` instance
(`check_same_thread=False` + a `threading.RLock` around every public method — the TUI
genuinely calls in from ~9 separate worker threads), WAL journal mode, and every mutation
is now a single targeted, committed transaction instead of a full-table rewrite.

**Scope — schema evaluation** (every field checked against real call sites, not assumed):
- Removed `log_level` — stored and displayed in the TUI, but grep confirmed
  `lifecycle.py`'s command builders never read it; dead configuration, not a working
  feature.
- Removed `backend_url` as a stored column — always exactly `f"http://127.0.0.1:{port}"`
  on every real write path (`bind_host` comes from a global env var, not anything
  per-entry); kept as a computed `@property` for source compatibility with every existing
  reader.
- Consolidated `hf_filename` + `hf_filenames` into a single always-populated
  `hf_filenames: list[str]` — the old pattern needed a
  `list(entry.hf_filenames) if entry.hf_filenames else [entry.hf_filename]` reconstruction
  at every consumption site, which was the tell it should just be one field. Touched
  `registry.py`, `discovery.py`, and the TUI's `app.py`/`cli.py`/`views/registry.py`, plus
  the admin-ui's `types/instance.ts` and `RegisterModelModal.tsx` (the wire format changed,
  so the frontend contract had to follow).
- Added `created_at` (`DEFAULT CURRENT_TIMESTAMP`) — essentially free while the table was
  being defined from scratch, standard elsewhere in this codebase (auth-service's
  `Principal`/`Node`), not yet wired into any read path or UI (left for whenever a
  "sort by downloaded date" is actually wanted, not bundled in here).
- Attempted `CHECK (backend IN (...))`/`CHECK (modality IN (...))` constraints, **reverted**
  — `test_lifecycle.py::test_start_instance_rejects_unknown_backend` deliberately calls
  `registry.update(..., backend="does-not-exist")` to prove `start_instance()` has its own
  downstream validation; `Registry.update()` being permissive (only `add()` validates) is
  existing, intentional behavior the CHECK broke. Kept the established contract instead.
- Deliberately left `file_size_bytes` uncached (computed live in `routes.py`'s `_merge()`,
  unchanged) — a persisted value could go stale if a file moves/is deleted outside the app;
  live computation is more correct, not just simpler.
- Found, did not silently fix: `gpt-oss-20b-mxfp4` and `gemma4-31b-q6` share `port: 8087`
  in the live demo data — a real pre-existing gap (`_validate_port` only range-checks,
  never checks uniqueness). A `UNIQUE` constraint would have broken the migration's import
  of this repo's own data, so it's flagged as a follow-up rather than auto-resolved.

**Migration mechanics**: `Registry.__init__` checks for a legacy `registry.yaml` next to a
not-yet-existing `.db` path; if found, imports every entry into a `.db.tmp` file, commits,
`os.replace()`s it into place (atomic — a crash mid-import leaves an untouched, still-
migratable `.yaml` and an orphaned `.tmp`, never a half-populated DB masquerading as
complete), then renames the YAML to `.yaml.bak` (never deleted). `RegistryConfig.path`'s
default changed from `runtime/manager/registry.yaml` to `runtime/manager/registry.db`.

**`.gitignore`**: the existing generic `*.db` rule would have silently gitignored the new
`registry.db` — but `runtime/manager/registry.yaml` was tracked in git as this repo's own
demo/seed data (32 entries), so a fresh clone would've ended up with neither file. Added
`!runtime/manager/registry.db` as an explicit exception, plus `*.db-wal`/`*.db-shm` (WAL's
companion files) and `registry.yaml.bak` to the ignore list.

**Verified**: full `.githooks/pre-push` green (277 Python tests across manager-core/api/tui
plus admin-ui build/lint). Ran the real migration against this repo's own live
`runtime/manager/registry.yaml` (32 entries, including this session's own earlier fixes —
two vision-model corrections and one embedding-model correction): field-by-field diff
against a pre-migration backup confirmed every field round-tripped exactly, including the
one sharded model's `hf_filenames` list; confirmed idempotent on a second startup (no
re-migration, no stray `.tmp`/duplicate `.bak`); confirmed live in the browser against the
real gateway+manager-api stack — Overview/Instances/Models pages rendered identically
(same 32-model count, same modality/family/quant badges), then exercised all three write
paths live end-to-end: edited a real entry's `family` field (`UPDATE`, verified via a raw
`sqlite3` query), registered and deleted a temporary instance via the "Model" picker
(`INSERT` then `DELETE`, both verified the same way). `git rm`'d the tracked
`registry.yaml`, `git add`'d the new `registry.db`.

## RM-50 — fix: Registry data-quality cleanup (done)

**Why**: with 32 registry entries and 30 downloaded, the Instances and Library lists were
long enough to hide real problems. Auditing both (cross-referencing `registry.db` against
the actual `LLM_models/` filesystem) surfaced concrete bugs, not just clutter.

**Scope — confirmed issues, proposed fixes**:
- **Port collision**: `gpt-oss-20b-mxfp4` and `gemma4-31b-q6` both claim port 8087 — always
  a live bug (`_validate_port` only range-checks, never checks uniqueness). Reassign one.
- **Ghost download**: `minimax-m27-iq2m` is `downloaded=True` but
  `LLM_models/MiniMax-M2.7/` doesn't exist on disk at all — starting it will always fail.
  Delete the entry.
- **Dead undownloaded stubs**: `qwen3-0-6b-iq4-nl-local` (port 8081, never downloaded) is a
  redundant leftover — the same `hf_repo`/file is already downloaded under
  `qwen3-0-6b-iq4-nl-local-2`. `bf16-qwen3-8-flash-next-bf16-00001-of-00008-local` (port
  8111) is another never-completed registration, its id an artifact of `auto_id()` picking
  the first shard's filename. Delete both.
- **Size undercount** (real display bug, not just a data issue): `_file_size_bytes()`
  (`routes.py:300-320`) sums `entry.get("hf_filenames") or [Path(path).name]` — for models
  registered manually (not through the HF-download flow), `hf_filenames` is always empty,
  so only the first shard is ever counted. Confirmed against the filesystem: `llama4-scout-17b-q4`
  shows ~49.8 GB but is really 65.3 GB; `minimax-m2-q2` shows ~50 GB vs. 83.3 GB real;
  `qwen25-32b-q4` shows ~4.0 GB vs. ~19.9 GB real (worst case, ~80% undercounted);
  `qwen2.5-7b-q4` and `deepseek-v25-1210-iq1m` are also affected. Fixed in
  `_file_size_bytes()`: when `hf_filenames` is empty, list the file's own directory and run
  it through `hf_discovery.shard_filenames()` — the same shard-detection helper the Hugging
  Face discovery flow already uses — instead of a new one-off regex, so both paths agree on
  what counts as a shard.

**Noted but not proposed as fixes** (curation calls, not bugs — left for the user to
decide): total footprint is 564 GB, heavily concentrated in redundant same-family variants
(7 different Gemma-4 quant/size combinations downloaded simultaneously); an unregistered
`qwen3vl-8B-Q4/` on disk already ships its own `mmproj` file — unlike the two *registered*
vision models (`llava-mistral-7b-q5`, `qwen3vl-32B-Q4`), which have no mmproj file anywhere
and can't actually do vision yet; one orphan file (`Laguna-S-2.1-DFlash-BF16.gguf`) sits
unregistered next to its registered sibling.

**What shipped**: deleted the 3 broken registry entries (`minimax-m27-iq2m`,
`qwen3-0-6b-iq4-nl-local`, `bf16-qwen3-8-flash-next-bf16-00001-of-00008-local`) live through
the running dashboard — 32 entries down to 29; deleted the corresponding 10 MB orphan
partial shard (`runtime/models/downloads/BF16/...-00001-of-00008.gguf`, the only one of the
three with any file to clean up) and its now-empty directory; reassigned `gemma4-31b-q6`
from the colliding port 8087 to the freed 8081; fixed `_file_size_bytes()` in
`runtime/manager/api/src/prometheus_manager_api/routes.py` to reuse
`hf_discovery.shard_filenames()` against the file's own directory when `hf_filenames` is
empty, with a new test (`test_sharded_model_without_hf_filenames_sums_sibling_shards`)
covering the previously-uncovered case.

**Verified**: full `.githooks/pre-push` green (manager-api 78 tests, +1 from this change;
all other packages unchanged). Live in the browser: confirmed all 3 deletions actually
removed the rows and `registry.db` now has 29 entries with no id collisions; confirmed
`gpt-oss-20b-mxfp4`/`gemma4-31b-q6` no longer share a port; confirmed all 5 previously-
undercounted models now show their real combined size in the Library table —
`llama4-scout-17b-q4` 49.8 GB → 60.9 GB, `minimax-m2-q2` 50 GB → 77.6 GB,
`qwen25-32b-q4` 4.0 GB → 18.5 GB (the worst case), `deepseek-v25-1210-iq1m` 39.9 GB →
49.1 GB, `qwen2.5-7b-q4` 4.0 GB → 4.4 GB.

## RM-51 — fix: Separate the model catalog from running instances (done)

**Why**: found live, mid-session, while verifying RM-38 — an operator cleanup pass deleted
what it believed were unused *instances* through the dashboard, and it wiped 27 *model*
registrations along with them (down from 30 to 5 live rows). Root cause: `registry.db`'s
`models` table conflated two concepts into one row — "a model that's been downloaded/known"
and "a specific running instance of it, on some node/port/backend" — so there was no way to
remove an instance without also removing the model it came from.

**What shipped**: `runtime/manager/core/registry.py` split into two tables — `models` (the
catalog: path, family, quantization, hf_repo/sha256/filenames, downloaded, plus the
mmproj/vae/clip_l/t5xxl companion-file paths) and `instances` (`model_id` FK, port, backend,
modality, context_length, discovery, rss_estimate_mb, cfg_scale) — a real 1-to-many
relationship. `RegistryEntry` stays a flat merged view (every pre-split field, plus a new
`model_id`) computed via a join, so `lifecycle.py`, `scanner.py`, `capacity.py`, and most of
`control.py`/`routes.py`/`tui/cli.py` needed no changes at all. `remove()` now only ever
deletes the instance row (**the literal bug fix**); a new `remove_catalog()`/
`deregister_model()` pair handles the cascade case, guarded by `RegistryIntegrityError` if
instances still reference a catalog row being removed directly.

Downloading a model now creates **only** a catalog entry (no port/instance allocated) — the
operator explicitly creates an instance afterward via `RegisterModelModal`'s existing "pick
a downloaded model" dropdown (now sourced from the catalog, submitting a `model_id` FK
instead of copying every field into the request body). `DELETE /v1/models/{id}/downloaded`
(Models/Library page) became the cascade path: stops+removes every instance across every
node, then deletes the catalog row and file — but only with `confirm=true` if any instance
is live, otherwise 400s listing which ones (id + node), so neither the dashboard's
confirmation dialog nor a bare curl/script can cascade-stop instances across nodes by
accident. New `GET /v1/models` (manager-api) and `GET /admin/api/models` (gateway
aggregation) list the catalog with each entry's `instance_ids` — needed since a
downloaded-but-not-yet-instantiated model has zero instances and no longer shows up in
`GET /v1/backends`.

**Explicit scope boundary** (user-confirmed): ships the schema split, the safe delete
semantics, and the minimal "create one instance from a catalog entry" flow — not a
dedicated multi-simultaneous-instance management UI (per-model instance picker, port-aware
multi-instance dashboard). The schema/API already support running several instances of one
catalog entry; the richer UI for that is [[PRM-55]].

**Two real bugs found while implementing, fixed in passing**: `control.py`'s
`_control_action` passed `entry.__dict__` (not `.to_dict()`) into `_merge()` — silently
dropped `backend_url` and would have dropped the new `model_id` too (pre-existing, unrelated
to this migration, but the exact function being rewritten anyway). And the new migration's
backup path used `Path.with_suffix(".pre-rm51.bak")` on a `.db` file — which *replaces* the
`.db` extension rather than appending, producing `registry.pre-rm51.bak` instead of
`registry.db.pre-rm51.bak`; caught during the real-file migration rollout below and fixed
with `with_name` instead.

**Migration**: same structural pattern as RM-49's YAML→SQLite move (temp-file build, atomic
`os.replace`), adapted for a same-file split — backs up via `shutil.copy2` (not rename,
which would briefly leave the live path missing) *before* any destructive step, so a crash
before the swap leaves the original untouched and retries cleanly, and a crash after leaves
`registry.db.pre-rm51.bak` as a permanent recovery snapshot.

**Verified**: `runtime/manager/core` — 188 tests (new migration-split tests mirroring
RM-49's `TestLegacyYamlMigration` shape, catalog/instance CRUD tests including the literal
regression test for the bug: `remove(instance)` leaves `get_catalog()` intact).
`runtime/manager/api` — 88 tests (register-with-`model_id`, cascade-delete confirm/no-confirm
paths, new `GET /v1/models`). `gateway` — 254 tests (`model_id` passthrough/fallback in
`manager_sync`, new `GET /admin/api/models`). Frontend: `tsc`/lint/build clean. Full
`.githooks/pre-push` green.

Live, end-to-end against the real (git-tracked, 33-row) `registry.db`: migrated a scratch
copy first and diffed every field across all 33 rows against the pre-migration original (0
mismatches, including the FLUX.1 split-file fields) before touching the real file; migrated
the real file (restarting manager-api), confirmed `GET /v1/backends` unchanged
field-for-field; through the actual dashboard, registered a manual instance, deleted it, and
confirmed the catalog entry survived; created a second instance of an existing catalog entry
via `RegisterModelModal`'s picker (Models page's "Instances" column went 1 → 2); opened the
cascade-delete confirmation on a real running instance and confirmed it named the running
instance before allowing the (cancelled) delete.

**Follow-up (admin dashboard rate-limited itself)**: found live right after shipping — the
user hit `429 Rate Limit Exceeded` deleting an instance. Root cause: the gateway's
`RateLimitMiddleware` bucketed every `/admin/api/*` route (instances, nodes, catalog,
delete, ...) under the same generic per-client "default" 60 RPM budget shared with real
inference traffic — no admin-specific carve-out existed (unlike `/v1/chat/completions`,
which already gets its own endpoint slug/override). This PR's own `useModelCatalog()` poll
(5s, +12 RPM on the Instances/Dashboard page) was a real, if secondary, contributor — it cut
the remaining headroom under 60 from 36 to 24 RPM, so an ordinary delete (plus its
cache-invalidation refetch) or a second open dashboard tab was now enough to tip over.

Fixed at the root rather than by trimming polling: `/admin/api/*` (matched by prefix, since
its paths carry dynamic segments like `{node}/{model_id}`) now resolves to its own `"admin"`
rate-limit slug, with a new `Settings.rate_limit_rpm_admin` override (default 600) — the
same per-endpoint-override mechanism `chat_completions` already used, extended to a second
case. A logged-in dashboard session is a trusted internal credential firing several
legitimate concurrent polling queries, not an external API consumer competing for the same
budget as inference clients.

**Verified**: gateway — 257 tests (3 new: `_endpoint_slug` prefix-matches every
`/admin/api/*` path including dynamic segments, `_resolve_limits` applies the admin
override, and an end-to-end test proving a global RPM low enough to trip immediately on a
plain endpoint does *not* trip on `/admin/api/*` once the override is set). Full
`.githooks/pre-push` green. Live: restarted the gateway, confirmed
`GET /admin/api/instances` now returns `x-ratelimit-limit-requests: 600` (was 60); bounced
through Instances → Models → Overview rapidly (mirroring the multi-page-polling scenario)
and ran a register+delete cycle immediately after — all `200`/`201`/`204`, no `429`.

## RM-52 — sd_cpp: support split-file diffusion models (done)

**Why**: RM-38's `sd_cpp` backend only supported a single merged `.gguf` file (via
`-m/--model`) — fine for SD-Turbo, but the user asked for FLUX.1 [dev]-quality output
after finding SD-Turbo's results too basic/inconsistent. FLUX.1 (and SD3.5) ship as
separate files instead — a standalone diffusion model, a VAE, and text encoder(s)
(clip_l + t5xxl for FLUX.1) — which sd-server loads via `--diffusion-model` + `--vae` +
`--clip_l` + `--t5xxl` instead of a single `--model`. Confirmed against the real
installed `sd-server --help`, not just docs.

**Scope**: four new optional `RegistryEntry` fields — `vae_path`, `clip_l_path`,
`t5xxl_path`, `cfg_scale` — all sd_cpp-only, empty/`None` by default (single-file models,
including the existing SD-Turbo registration, are unaffected). Setting any of the first
three switches `lifecycle.py`'s `_build_sd_cpp_cmd` from `-m/--model <path>` to
`--diffusion-model <path>` + whichever of `--vae`/`--clip_l`/`--t5xxl` are non-empty.
`path` keeps meaning "the diffusion model file" in both modes — no new field needed for
that. `cfg_scale`, when set, becomes `--cfg-scale` at process startup. Wired through
`pmgr register`'s CLI flags, the manager REST API's `POST /v1/backends` and
`PATCH /v1/backends/{id}` (found and fixed a real gap while wiring this in: `PATCH`
re-validates `path` against path-traversal on update but had never been extended to the
new path fields — fixed alongside them, not left for later). Did **not** touch the admin
dashboard's Register/Edit modal — it still has no manual-path flow for new instances (a
pre-existing gap noted in RM-38's writeup), so split-file registration goes through
`pmgr register` or the REST API directly, same as any other manually-placed model today.

**Two more real bugs found only by actually running FLUX.1-dev, not by code review**:
1. sd-server's `--cfg-scale` defaults to 7.0 (tuned for classic non-distilled SD).
   FLUX.1/SD3.5 are guidance-distilled and need ~1.0 — cfg=7.0 against FLUX.1-dev
   returned a uniform-color solid-brown PNG (not an error, not a crash — a "successful"
   response that was actually garbage). Confirmed cfg=1.0 fixes it with a real generated
   image. Also confirmed empirically that `/v1/images/generations`'s JSON body does
   **not** honor a per-request `cfg_scale`/`steps` override on this sd-server build —
   only the process's own startup flag takes effect, which is why `cfg_scale` had to be
   a registry field (baked into the launch command), not a gateway request parameter.
2. The gateway's backend-forwarding httpx client had a flat 120s timeout
   (`gateway/src/prometheus_gateway/models/backends.py`) — fine for chat completions, far
   too short for a real FLUX.1-dev generation (~120-500s at 20 steps on Metal, varying
   with system load). A slow-but-working request was timing out client-side, then being
   retried by `BackendPool.forward()`'s own retry loop into an even longer wait, since
   sd-server keeps computing a request server-side even after the client gives up —
   surfaced to the user as "Upstream Error" despite the backend being perfectly healthy.
   Fixed by raising the timeout to 600s; harmless for already-fast backends since it's a
   ceiling, not a delay.

**t5xxl format note**: the fp8 (`t5xxl_fp8_e4m3fn.safetensors`) text encoder crashes
sd-server on this Mac (Apple M4 Max) — `ggml_metal_library_compile_pipeline: Function
kernel_mul_mm_f8_e4m3_f32 was not found in the library`, an fp8 Metal kernel gap on this
GPU generation. Switched to the GGUF-quantized encoder (`city96/t5-v1_1-xxl-encoder-gguf`,
`Q8_0`) instead, which loads and runs fine — worth remembering for any future guidance
here or in RM-06's inference-engine notes.

**Verified**: `runtime/manager/core` — 173 tests (9 new: split-file command-shape tests,
`cfg_scale` command-flag tests, round-trip persistence for both, a path-traversal
rejection, a schema-migration test for an existing pre-RM-52 `registry.db`), mypy, ruff
clean. `runtime/manager/api` — 81 tests (3 new: register + PATCH round-trip for the
split-file fields and for `cfg_scale`), mypy, ruff clean. `runtime/manager/tui` — 38 tests
(unchanged), mypy, ruff clean. `gateway` — 241 tests (unchanged; the timeout fix needed no
new test, it's a constant), mypy, ruff clean. Real end-to-end, through the actual running
stack: downloaded FLUX.1-dev Q8_0 GGUF (`city96/FLUX.1-dev-gguf`, 12.7GB) + VAE
(`auroraintech/flux-vae`) + clip_l (`comfyanonymous/flux_text_encoders`) + the GGUF t5xxl
encoder above (~23GB total), registered via `pmgr register --backend sd_cpp
--vae-path ... --clip-l-path ... --t5xxl-path ... --cfg-scale 1.0`, started the instance,
generated a real image from a prompt through the full stack (browser → gateway → manager-
api → sd-server) in the Playground's Images tab, confirmed a genuinely high-quality,
coherent image rendered (118.9s at cfg=1.0/20 steps) — a real, visible quality jump over
SD-Turbo, not just "the process started."

## RM-53 — Playground: unify Chat/Embeddings/Images into one adaptive chat (done)

**Why**: the three tabs were the same "pick a model, send a request, see the result"
pattern duplicated three times — RM-41 (model label) and RM-42 (waiting indicator, prompt
history, question/answer bubble layout) each had to be applied to all three separately
because nothing was shared beyond copy-pasted JSX.

**What shipped**: `Playground.tsx`'s three mode tabs (and the `mode` state driving them)
are gone. One Model selector (`components/PlaygroundModelPicker.tsx`, new — grouped by
`<optgroup>` into Text & Vision / Embedding / Image) spans every ready instance regardless
of modality; the selected instance's own `modality` now drives which config is visible —
system prompt/temperature/top-p/max-tokens/tools/streaming for `text`/`vision`, the
single-shot input + vector preview for `embedding`, prompt-only + image results for
`image`. `api/playground.ts` needed zero changes (every hook already took a bare
`model: string`, modality-agnostic by construction) — this was a pure frontend
state-composition rewrite.

**Confirmed with the user before building**: switching between models of different
modalities does **not** clear the results — one persistent, chronological,
mixed-content timeline (`entries: LogEntry[]`, a `{kind: "chat"|"embedding"|"image"}`
discriminated union replacing the old three parallel `Turn`/`EmbeddingResult`/
`ImageResult` arrays) can interleave a chat turn, an embedding result, and a generated
image based on whichever model was used at each point — matching the roadmap's own
wording literally ("one composer, one message/result list"). `historyMessages()` (what
actually gets sent as conversation context) filters to chat-kind entries only, so an
embedding/image action in between two chat turns is correctly excluded from the model's
context. "Regenerate" targets the last **chat**-kind entry specifically
(`entries.filter(e => e.kind === "chat").at(-1)`), not the literal last array element, so
it keeps working even when a later embedding/image entry was appended after it.

**Verified**: no test suite exists for `gateway/admin-ui` (confirmed: no vitest/jest, no
`.test.`/`.spec.` files) — `tsc --noEmit`, `npm run lint`, `npm run build` clean (first
pass, no fixes needed), full `.githooks/pre-push` green (backend suites unaffected, as
expected for a frontend-only change). Live, end-to-end: sent a real chat prompt to a text
model; without clearing, switched to an embedding model and got a real embedding — its
result appended below the chat turn in the same timeline; switched to an image model and
generated a real image — appended below both; switched to a vision model and confirmed the
attach-image button appeared, then switched back to plain text and confirmed it
disappeared; Clear emptied the entire mixed timeline (chat + embedding + image) in one
action.

## PRM-54 — Audio/music generation support (todo)

**Why**: RM-38 (image) established the modality-per-backend pattern (new backend, gateway
endpoint, Playground UI); audio/music generation is a natural next modality, and covers two
real, requested use cases — not just prompt-to-music.

**Scope**:
- Text-to-audio/music: a prompt generates a music/audio clip.
- Audio-to-audio: upload a reference track and either generate new music matching its
  style, or restyle it directly (e.g. rock → acoustic).
- New `audio` modality, a new backend integration, a gateway endpoint, and Playground UI
  (prompt input, file upload for the audio-to-audio case, an audio player + download for
  results).
- Backend/model choice is **not yet decided** — needs its own research pass first, same as
  RM-38 did for image generation, before any implementation starts.

## PRM-55 — Richer multi-instance-per-model management UX (todo)

**Why**: [[RM-51]]'s schema/API split (a `models` catalog + an `instances` table, 1-to-many)
makes running several instances of one catalog model — on different nodes/ports/backends —
fully supportable, but that PR deliberately shipped only the minimal flow: pick a catalog
entry, specify node/port/id/discovery/backend/modality, create one instance. Explicitly out
of scope there, per the user's own call, to keep that PR's blast radius contained.

**Scope**:
- A per-model instance list/picker inside the create-instance flow, showing "here are the N
  existing instances of this model" (node, port, state) before adding another — today's
  `RegisterModelModal` shows none of that context.
- A port-conflict-aware view when creating a second instance on the same node.
- Any dedicated UI for comparing/managing multiple instances of the same model side-by-side
  (the Models/Library page's "Instances" column today is just a count).

## RM-56 — Admin dashboard: edit rate limits live, no restart (done)

**Why**: RPM/TPM limits (global plus the per-endpoint chat/admin overrides) only changed via
`.env` + a gateway restart — raised while fixing RM-51's admin rate-limit 429. Restarting to
retune a limit means dropping in-flight inference requests, which is a poor trade for a knob
an operator wants to adjust while watching traffic.

**Scope**: mirrors [[RM-60]]'s DB-backed pricing override. A single-row `rate_limit_config`
table holds the admin-set values; its presence means "an operator configured these", its
absence means the `.env` values are in effect. `GET`/`PUT`/`DELETE /admin/api/limits`
(admin:read / admin:write) read, save and reset them, and an editable form on the Limits page
replaces that page's "read-only" disclaimer.

The live-apply turned out to need no middleware change at all: `_resolve_limits` already read
`self.settings.<field>` fresh on every request, and that Settings object is the same instance
as `app.state.settings` — so writing the field *is* the live update. `create_app` snapshots
the `.env` values into `app.state.rate_limit_env_defaults` before anything can overwrite them,
which is what "Reset to .env" restores and what the form shows beside each input. The env
snapshot deliberately lives on `app.state` rather than a module singleton, so apps built side
by side in tests can't leak limits into each other.

One guard worth naming: the endpoint refuses any value that would drop the admin bucket below
60 RPM (whether set directly or inherited from a too-small global). The dashboard polls
continuously, so a lower value would rate-limit the very page needed to undo the mistake —
leaving `.env` + a restart as the only way back, which is precisely what this item removes.
Values below 1 are rejected for the same class of reason. `rate_limit_strict` stays
`.env`-only: fail-open vs fail-closed is a deployment decision, not a tuning knob.

**Verified**: live against the real gateway — set chat completions to 3 RPM via the API and
confirmed the very next requests 429'd with `"rate limit of 3 RPM for endpoint
'chat_completions'"`, no restart; confirmed the lockout guard and the zero-value rejection
both 400 without applying or persisting anything; confirmed `DELETE` restored the `.env`
values and chat went back to 200. In the browser: saved from the form (toast, badge flipped
`from .env` → `custom`, and the read-only StatCard above updated from 60 to 90 — proving the
`staleTime: Infinity` config cache is invalidated on save), then reset back. 10 new tests in
`test_admin.py`; full suite green.

## RM-57 — Multi-instance-per-model: a shared logical name for routing (done)

**Why**: [[RM-51]]'s schema lets several instances reference one catalog model, but each
instance's own id was also the name a client had to call it by — so replicas were invisible as
replicas, and a client had to pick one itself. That's the missing piece before [[RM-58]] can
balance across anything.

**What it turned out to be**: much smaller than the roadmap assumed. It speculated a new
`served_name` field; in fact manager-api already ships the catalog `model_id` on every instance
and `ManagerRegistrySync` already stores it — `ModelEntry.model_id` carried a comment saying
"never used for request routing". Two instances of one catalog model already coexisted in the
registry under distinct ids without colliding. So this item is about *using* the grouping key
that was already there, with no schema or manager change at all.

**Scope**: `ModelRegistry.resolve(name)` returns a `ModelResolution` — the active instances
serving that name, plus the modality and context length that hold for all of them. The three
inference handlers validate against the resolution and pick a member afterwards; today that
pick is `members[0]`, deterministic, because *choosing well* is [[RM-58]].

Decisions worth recording:
- **Group first, instance id second.** manager-api sets `model_id = id` on direct
  registration, so the first instance of a model is usually named after its catalog entry.
  Resolving the instance id first would match that one exactly and silently ignore every
  replica added later — the operator would believe they were balancing while everything went
  to one instance. Confirmed live: with `qwen3-0-6b-iq4-nl-local-2` as both the catalog id and
  an instance id, a request to that name resolves to the group of 2.
- **A group whose members disagree on modality is refused** (400 `inconsistent-model-group`,
  naming the disagreement) rather than served from an arbitrary subset — serving a chat request
  from an embedding backend produces confident nonsense instead of an error. Operator's call.
- **`context_length` is the minimum across members**, since validation runs before a replica is
  chosen; a request that fits the smallest replica fits all of them.
- **Usage and pricing are attributed to the requested name, metrics and the circuit breaker to
  the replica that served.** Operator's call. Billing then matches what the client asked for —
  one price and one usage line per logical model — while observability still points at the
  machine that did the work, so a slow replica stays visible.
- A stopped model still resolves (with no members) rather than vanishing, so `503
  model-not-loaded` doesn't get demoted to `400 unknown-model` — a regression this nearly
  introduced.

`GET /v1/models` and `/v1/models/mine` now list every routable name with a `served_by` count,
and the dashboard's ScopePicker offers the catalog name (badged with its replica count)
alongside instance ids — granting only an instance id would pin a client to one replica.

**Not changed**: two instances sharing the *same* id on different nodes are still dropped with a
warning by `manager_sync` (RM-08 phase 2). Replicas are expressed by sharing a catalog
`model_id` with distinct instance ids, which is what the manager's own `add_instance` produces.

**Verified**: 21 tests, plus a live run against the real stack — registered and started a second
real instance of a model, watched the gateway group them on its own (`served_by=2`), routed to
both the logical name and an individual replica, and confirmed per-instance metrics counted 1
request each. Torn down afterwards; the registry is back to its original state.

## RM-58 — Gateway: intelligent load balancing across instances of the same model (superseded by RM-72)

**Superseded**: absorbed by [[RM-72]]. The audit in `docs/model-identity-proposal.md`
found that balancing can't be designed independently of the identity model — and that
the circuit-breaker/failover half of this item is urgent enough to ship on its own as
[[RM-69]], ahead of the identity work. The original framing is kept below for context.

**Why**: once [[RM-57]] lets several instances share a routable name, naively picking "the
first one" (or requiring the client to pick a specific instance) wastes the whole point of
running replicas — spreading load, tolerating one instance being down/circuit-broken, or
preferring the fastest one.

**Scope** (not yet designed in detail):
- Depends on [[RM-57]] shipping first — no grouping concept to balance across otherwise.
- Selection strategy — candidates worth evaluating rather than assuming one: round-robin
  (simplest, no state needed beyond a counter), least-active-requests (needs the gateway to
  track in-flight count per instance, which RM-16's rate-limit/circuit-breaker visibility
  work may already touch), or latency-aware (reuse [[RM-46]]'s per-instance `latency_p50_ms`/
  `ttft_p50_ms` metrics already collected in `MetricsStore` — route to whichever replica is
  currently fastest, rather than round-robin blind to real performance).
- Must respect the existing per-instance circuit breaker (spec 007) — an instance that's
  open/tripped should be skipped by the balancer, not just eventually 503 the client.
- Out of scope: cross-node balancing beyond what [[RM-08]]'s existing multi-node aggregation
  already does — this is about picking among instances the gateway already aggregates,
  not a new distributed-systems layer.

## RM-59 — Dashboard: search/filter for Models and Instances (done)

**Why**: both tables already sort and paginate, but with ~29 catalog models and a growing
instance list, finding a specific one meant scanning or paging. Deliberately client-side:
both tables already hold their full dataset in memory (the catalog and instance list are
fetched whole and polled), so a text filter needs no endpoint, no query param, and no refetch.

**Scope**: a shared `TableSearchInput` component (search icon, clear button, match count)
rendered above `InstanceTable` and `DownloadedModelsTable`. Instances match on id, model_id,
node, backend, modality, state and port; models match on name, family and quantization —
case-insensitive substring. Filtering is applied *before* the existing sort and pagination so
both keep operating on what's actually shown, and typing resets `InstanceTable` to page 1 (a
filter that shrinks the result set below the current page would otherwise leave the operator
on a page that no longer exists). A no-matches state is distinct from the never-registered
empty state — the latter still says "click Register model", which would be wrong copy for a
search that simply found nothing.

**Verified**: live in the browser against the real dashboard — Instances filtered by modality
("vision" → 2 of 10), the no-match state, and Models filtered by family ("gemma" → 5 of 29)
with row numbers renumbering correctly. `tsc`/`eslint`/`build` clean.

## RM-60 — Billing: real per-client cost, multi-currency display, tax, dashboard, budget alerts (done)

**Why**: [[RM-32]]/[[RM-33]] metered and priced usage but computed cost at **read time**
(`GET /v1/usage` against whatever `pricing.yaml` currently says) with no stored `cost_usd` —
a price change + restart silently re-priced every past day (confirmed real anti-pattern via
research: Stripe/Lago/Kill Bill all rate at time-of-usage, never retroactively). The user
confirmed this is meant to become **real billing of Prometheus's own users**, potentially
including individual (non-business) clients in Peru, not just an internal chargeback report.

**What shipped**: write-time cost (`record_usage()` now prices and stores `cost_usd`
per-row) + a new append-only `usage_events` audit table as the source of truth
(`usage_daily` stays only as the live-poll rollup) — this also closed a real gap where
`/v1/embeddings` and `/v1/images/generations` never recorded usage at all. New
`GET /v1/usage/export` CSV endpoint (arbitrary date range, per-request rate, reconciliation
row) + a date-range/export control on `Usage.tsx`. Multi-currency (PEN/USD/EUR) is
display-only — cost stays stored in USD, converted via an admin-configurable static rate
table (`admin/api/billing/currency-rates`), never a live FX API. A per-client tax rate
(`ClientBillingSettings.tax_rate_percent`) renders as a separate line item — plain and
configurable, explicitly **not** a SUNAT-compliant tax receipt (see [[PRM-61]], still
unbuilt/blocked). A hard monthly spend cap + soft alert thresholds (50/80/100% default) are
enforced via a new Redis-backed `BudgetTracker` (`budget.py`, atomic pipelined `INCRBY`,
mirroring `rate_limiter.py`'s `check_and_increment_rpm` — not `check_tpm_budget`'s known
read-then-write race) using reserve-before-forward/settle-after on `/v1/chat/completions`
(streaming + non-streaming), `/v1/embeddings`, and `/v1/images/generations`; alert emails go
through a new stdlib-`smtplib` `notifications.py` (no new dependency), optional/unconfigured
by default. New `Billing.tsx` dashboard (Recharts) shows the current-period summary, cap
indicator, cost-by-model, daily trend, and period history with CSV links; a "Billing
settings" action on `Users.tsx`/`UserRow.tsx` opens `ClientBillingSettingsModal.tsx`.
Real payment collection (charging a card) stays explicitly out of scope, as does a full
tax-determination engine and live multi-currency settlement. Pricing granularity stays
per-exact-model (unchanged, confirmed no need for tier-based pricing).

**Verified**: 318 backend tests passing (up from 257; new coverage includes a concurrent-
write regression test for the atomic-upsert fix, `BudgetTracker` reserve/settle/rollback
under concurrency, CSV export shape, and an end-to-end 402 on cap-exceeded), `ruff`/`mypy`
clean, admin-ui `tsc`/`eslint`/`build` clean. Live, against a real running gateway process
and SQLite DB (no test mocks): recorded real usage, then changed `pricing.yaml` to a wildly
different rate and restarted the gateway — confirmed the already-recorded day's cost was
unchanged (the actual bug fix); verified CSV export, tax, and currency-conversion math by
hand against the API response; found and fixed a real bug live (the alerts-listing endpoint
500'd when Redis was unreachable instead of degrading to an empty list — now covered by a
regression test); confirmed in the browser that the new Billing nav item, page, Usage page's
CSV export (real 200 response), and Overview's budget-alert banner all render correctly
against live data.

## PRM-61 — Peru/SUNAT e-invoicing compliance for individual (B2C) clients (todo, blocked on a business decision)

**Why**: split out of [[RM-60]] — Peru's Legislative Decree 1623 (effective Dec 2024)
requires a foreign digital-service provider billing Peru-based **individual consumers** to
register with SUNAT as a withholding agent and issue electronic invoices in SUNAT's UBL 2.1
format via an authorized PSE/OSE provider, with monthly filing and 5-year electronic
retention. This is a real legal/compliance program — SUNAT registration, choosing and
integrating an authorized e-invoicing provider (e.g. a PSE/OSE service), ongoing monthly
remittance — not a software feature a coding session should silently implement without
actual legal/tax review, since getting it wrong creates real legal/tax exposure for the
platform operator.

**This item is intentionally left unscoped** until the user (with actual legal/tax counsel,
not this assistant) decides:
- Whether Prometheus will actually bill Peru-based individual consumers at all, or restrict
  Peru-based billing to registered business entities (which sidesteps this requirement
  entirely, per Decree 1623's explicit B2C-only scope).
- If billing Peru individuals is required: which authorized PSE/OSE e-invoicing provider to
  integrate with, and confirmation of the registration/filing process with SUNAT (a business
  process, not a code change).

**Do not implement e-invoicing integration under [[RM-60]]'s tax phase** — that phase's
plain tax-rate line item is explicitly NOT a substitute for this compliance requirement.

## RM-62 — Cost-based model price suggestion (done)

**Why**: [[RM-60]] added a DB-backed Model Pricing table, but prices were still hand-typed —
no link to what a model actually costs to run. The user's own hardware (a MacBook Pro M4
Max) has a real cost (purchase price + electricity), and the platform already measures real
per-model throughput ([[RM-46]]'s `tokens_per_second_avg`/`images_per_second_avg`); this item
connects the two into a "suggest a break-even-plus-margin price" button, instead of guessing.

**What shipped**: the Node registry (auth-service `nodes` table + `/admin/nodes` CRUD + the
Nodes page's edit modal/table columns) gained 3 operator-entered fields —
`hardware_amortization_usd_per_hour`, `electricity_usd_per_hour` (summed into a computed,
read-only `hourly_cost_usd` total), and `price_margin_multiplier` — each falling back to a
platform default (a MacBook Pro M4 Max's real numbers: ~$0.3082/hr amortization over a
3-year life, ~$0.0146/hr electricity at Lima's residential rate, 1.3× margin) when left blank
at creation, so a node is never left without a usable total. A new prefill (prompt-processing)
throughput metric, `prompt_tokens_per_second_avg`, added to `telemetry.py`'s `MetricsStore`
and `GET /metrics`: llama.cpp-family backends already send `timings.prompt_per_second` (or
`prompt_ms`/`prompt_n` to derive it) on every response — router.py was reading
`predicted_per_token_ms` from the same object but discarding this field entirely, so this
closes a real gap with zero new backend instrumentation (previously `tokens_per_second_avg`
was chat's *decode*-phase rate only, with no separate prefill number). A "Suggest price"
calculator button per row in `ModelPricingTable.tsx`: looks up the model's node (via the
existing catalog `node` name link) → that node's cost total and margin → divides by the
model's observed prompt/completion/image throughput → fills the price inputs for review,
never auto-saves. A new `CurrencyRatesForm` on `Billing.tsx` gives the PEN/EUR rates
(`currency_rates` table, RM-60's endpoints had no consuming UI until now) an actual settings
form instead of hand-editing the DB.

**Verified**: live against the real dev gateway/auth-service — the operator's own node now
carries the 3 real cost fields (confirmed the SQLite migration applied the platform defaults
to the pre-existing node exactly as computed), confirmed `GET /metrics` reports
`prompt_tokens_per_second_avg` for a real llama.cpp backend, confirmed the Suggest button's
output matches the formula by hand across two separate live requests (different throughput
samples each time), and confirmed the currency-rates form loads and round-trips real PEN/EUR
values through the existing PUT endpoint.

## PRM-63 — Prepaid credits/quotas (todo)

**Why**: [[RM-60]] built post-paid, informational billing only — a client accrues cost
through the month and sees what they owe, with a hard spend cap as the only real-time gate;
real payment collection was explicitly out of scope ("solo mostrar cuánto deben pagar"). The
user wants an actual prepaid commercial model on top of that: a client buys a credit balance
in advance and draws it down as they use models, instead of (or alongside) a monthly invoice.
(Tiered model access — originally requested alongside this — was split out into [[PRM-64]]
once it turned out to be a separable concern with its own design direction.)

**Decided so far**:
- **Coexistence, confirmed**: prepaid credits sit alongside [[RM-60]]'s post-paid model, not
  as a replacement — which billing model applies is a per-client choice.
- **Payment methods, confirmed**: card, e-wallets (Yape/Plin), and bank transfers. Some
  reconcile automatically (card, presumably wallet APIs); bank transfers are confirmed
  **manual** — an admin marks a transfer as received and credits the account, so the credit
  ledger needs an explicit "pending confirmation" state, not just "paid".
- **"Extend usage window", resolved — it's just a top-up**: the user clarified this isn't a
  time-boxed subscription window at all. If a client buys 100 credits and burns through them
  on day one, they simply buy more credits to keep going — no interaction with [[RM-60]]'s
  calendar-month billing period to design around. This removes what was the least-scoped part
  of the original ask.

**Deferred idea, not needed now**: a real time-boxed access window (credits that expire, or a
subscription-style period distinct from a simple top-up) was raised while scoping the "extend
usage" question above. The user confirmed the immediate need is fully satisfied by plain
top-ups, so this isn't part of PRM-63's build — but it's kept here as a distinct idea for
later, in case expiring credits or a subscription-style window becomes an actual requirement.
If it's ever picked up, its interaction with [[RM-60]]'s calendar-month billing period
(`month_bounds()`) is the open design question to start from.

**Still open**:
- Real payment collection (actually charging a card/wallet, and a manual-confirmation flow
  for transfers) is required to sell credits at all — this can't ship without picking payment
  processor(s) and building that integration, which [[RM-60]] deliberately deferred and this
  item inherits.
- **SUNAT reporting placement — recommendation**: put it in [[PRM-61]], not here. SUNAT cares
  about revenue from a Peru-based individual regardless of whether it came from a post-paid
  invoice or a prepaid credit purchase — a second, PRM-63-owned reporting path would duplicate
  the actual compliance logic and risk drifting out of sync with it. PRM-63's job is just to
  make sure every credit purchase/payment event is captured with the fields PRM-61's eventual
  reporting will need (amount, currency, client, date, payment method) — not to build its own
  summary. [[PRM-61]] itself is still blocked on the underlying legal/business decision, so
  this is a placement recommendation, not something ready to build either way.

## PRM-64 — Tiered model access by plan (todo)

**Why**: split out of [[PRM-63]] — originally requested together with prepaid credits, but
"which models a client can call" and "how they pay for usage" are separable concerns, and
this one already has enough industry research behind it to stand on its own.

**Confirmed direction**: a hybrid plan model, industry-researched and confirmed by the user.
A `plan` grants a default bundle of the existing `model:<id>` scopes ([[RM-07]]) automatically
when assigned to a client; an admin can still add/remove individual model grants per client on
top of whatever the plan gave them. This reuses RM-07's existing enforcement as-is — a plan is
just a named bundle applied at grant time, not a new gateway-side check.

Why this direction over the alternatives found in research:
- **Not OpenAI/Anthropic's automatic spend-tier model** (tier rises automatically with
  cumulative spend/account age) — that's really an anti-fraud/rate-limit mechanism, not a
  purchased product, and this platform already has its own equivalent in [[RM-60]]'s spend
  caps/alerts. Keeping "which plan you bought" separate from "how trusted your account is"
  was a deliberate call, not an oversight.
- **Not the ungated-marketplace pattern** (OpenRouter, Together.ai, Fireworks, Replicate —
  full catalog open to everyone, price is the only differentiator) — doesn't fit the user's
  explicit ask that purchasing a tier should unlock access to bigger/newer models.
- **Not pure manual-grants-only** — doesn't scale as the client base grows, admin becomes a
  bottleneck for routine plan assignments.

**Still open**:
- How a plan is assigned/changed in relation to [[PRM-63]]'s prepaid credits — does buying a
  particular credit package imply a plan, or are plan and credit balance orthogonal
  purchases? Depends on PRM-63's own design landing first.
- Data model for a "plan" (name, ordered tier rank if any, its default `model:<id>` bundle)
  and where plan assignment is surfaced in the admin dashboard (likely alongside the existing
  per-client scope management).

## RM-65 — fix: 422 validation errors break the RFC 9457 error contract (done)

**Why**: `docs/sdk-integration-guide.md` (written for the Axonium team building Python/Go/Rust
client SDKs) documented that every gateway error is RFC 9457 `application/problem+json` with
a `type`/`request_id`/`trace_id` an SDK can key off. The Axonium team found this untrue for
one case: a request body that fails Pydantic validation (missing/wrong-typed field) fell
through to FastAPI's own default `RequestValidationError` handler, never reaching the
gateway's `_problem()`/`_rl_problem()`/`auth_error_response()` builders — `Content-Type` was
plain `application/json`, body was `{"detail": [...]}`, no `type`, no `request_id`. An SDK
typing errors by `type` (as the guide itself instructs) got nothing to key off for this one
status code, and 422 wasn't even in the guide's own error catalog.

**Scope**: registered a `RequestValidationError` exception handler on the FastAPI app
(`gateway/src/prometheus_gateway/main.py`) that builds the same envelope shape as the
existing three independent copies (`router.py`'s `_problem()`, `auth/errors.py`,
`rate_limit_middleware.py`'s `_rl_problem()`) — `type` suffix `validation-error`, status 422,
`Content-Type: application/problem+json`. Pydantic's original per-field error list
(`loc`/`msg`/`type`/`input`) is preserved under an `errors` extension member rather than
discarded, so nothing is lost for programmatic or debugging use — `detail` is a
human-readable summary derived from the same list. Didn't unify the three pre-existing
duplicate envelope-builders into one shared function while touching this — that's a separate,
larger refactor than what was reported, flagged here rather than done silently.

**Verified**: reproduced live against the real gateway (missing `messages` field on
`/v1/chat/completions` → confirmed the old `application/json`/`{"detail": [...]}` shape
first, matching the report exactly), fixed, restarted, re-ran the identical request →
confirmed `application/problem+json` with `type`/`request_id`/`trace_id`/`errors` all
present. New regression test `test_rm65_body_validation_error_uses_problem_details_envelope`
in `gateway/tests/test_gateway_core.py`; full suite (336 tests) green. Updated
`docs/sdk-integration-guide.md` §5.1/§5.2 to document the fixed 422 shape.

## RM-66 — fix: chat completions accepted a wrong-modality model (done)

**Why**: [[RM-09]] added modality enforcement for vision content parts and `/v1/embeddings`/
`/v1/images/generations` already reject a model of the wrong modality unconditionally on
every call (`entry.modality != "embedding"` / `!= "image"`). `/v1/chat/completions` only
checked modality reactively — `has_image and entry.modality != "vision"` — which means a
plain-text chat request against an embedding or image-generation model had no modality gate
at all. Confirmed live: calling `/v1/chat/completions` with an embedding model returned `200`
with repeating-token garbage output (`"User-Agent-Agent.uaaaauseragentua"`) instead of an
error — the caller pays for a real (wasted) generation instead of getting a clear, free 400.
Found by the Axonium SDK integration team while testing against the real gateway, who
correctly noted the check was "one-directional" (embeddings rejects a text model; chat never
rejected an embedding model).

**Scope**: `router.py`'s chat completions handler gained an unconditional modality gate —
`entry.modality not in ("text", "vision")` → `400 modality-mismatch`, placed before the
existing image-content-part check (which still runs after it, now only meaningfully reachable
once the model is already known to be chat-capable). Runs before the streaming/non-streaming
branch, so both paths are covered by one check.

**Verified**: reproduced live first (the exact garbage-output response, confirmed the caller
would have been billed for a real generation), fixed, restarted, re-ran the identical request
→ confirmed `400 modality-mismatch` with no backend call made; also confirmed an
image-generation model is rejected the same way. Two new regression tests in
`gateway/tests/test_modality.py` (non-streaming and streaming); full suite (338 tests) green.
Updated `docs/sdk-integration-guide.md` §5.1/§5.2.

## RM-67 — Admin dashboard: edit circuit-breaker thresholds live (done)

**Why**: asked for straight after [[RM-56]] — the Limits page now edits rate limits without a
restart, but the three circuit-breaker thresholds (failure / recovery timeout / success) sitting
right beside them were still `.env`-only. Same argument: restarting to retune a knob means
dropping in-flight inference.

**Scope**: mirrors [[RM-56]] — a single-row `circuit_breaker_config` table,
`GET`/`PUT`/`DELETE /admin/api/circuit-breaker`, an env-defaults snapshot on `app.state`, and a
second form on the Limits page.

The interesting difference is how the change reaches the running system. RM-56 needed nothing
beyond writing to Settings, because the rate-limit middleware re-reads it per request. The
circuit breaker doesn't: `BackendPool` captures the three values at construction and uses them
as defaults for breakers it creates, and each `CircuitBreaker` captures its own copy again — so
writing Settings alone would silently miss every backend already in service. The endpoint
therefore pushes into both layers via a new `BackendPool.update_circuit_breaker_settings()`,
which delegates to a new `CircuitBreaker.update_settings()` rather than reaching into another
class's private attributes.

That also reaches currently-open circuits, which is the desirable behaviour and worth stating
explicitly: `recovery_at` is derived as `opened_at + recovery_timeout` every time state is read,
not frozen when the circuit tripped, so shortening the timeout brings an open backend back
sooner and lengthening it defers the probe. The breaker's state itself is left alone — an open
circuit stays open.

`circuit_breaker_recovery_timeout` is capped at 3600s: unlike a bad threshold, a typo'd timeout
(30000 instead of 300) has a lasting, silent effect — a recovered backend stays cut off with
nothing surfacing why. All three reject values below 1.

**Verified**: the case that matters is covered by a test asserting a breaker built *before* the
edit picks up the new numbers, alongside one built after — that's the failure mode the whole
item exists to avoid. Live against the real gateway: saved and read back through the API,
confirmed `GET /admin/api/config` reflects it, confirmed the 3600s cap and the below-1 rejection
400 without applying or persisting anything, and — the path unit tests can't reach, since
`ASGITransport` skips lifespan — restarted the gateway and confirmed the saved thresholds were
loaded from the DB and pushed into the pool while `env_defaults` still reported what `.env` says.
In the browser: the form showed the saved values beside the `.env` ones, and Reset restored them.
Not verified end-to-end: an actually-open circuit adopting a new timeout — inducing one would
have meant killing a running model server, so that rests on the read-time `recovery_at`
derivation above plus the live-object test.

## RM-68 — Alembic migrations for the gateway database (done)

**Why**: `create_tables()` is a bare `Base.metadata.create_all`, which only ever creates
*missing tables* and never alters existing ones. [[RM-60]] already needed a hand-rolled,
schema-inspecting `ALTER TABLE` to add one column, and [[RM-70]] changes several tables that
hold real usage and billing data — doing that by hand again is how data gets lost.

**Scope**:
- Adopt Alembic against the gateway's own DB (`gateway/src/prometheus_gateway/db.py`).
- Stamp the existing schema as the baseline revision so current deployments don't re-create
  anything, and fold RM-60's manual `ALTER TABLE` stopgap into a real revision.
- Decide and document how migrations run (explicit command vs. on startup) — they must be
  idempotent either way, since SQLite and Postgres are both supported.
- Out of scope: the manager's registry DB, which uses raw SQL and its own schema handling.

**Verified**: against a copy of the real `gateway.db` (57 usage events, 6 model prices, 2
billing settings) — adopted, stamped, every row intact — and then against the real one by
restarting the gateway. Three tests cover the fresh / pre-Alembic / re-run paths; a fourth
diffs the migrated schema against the models, so a model edit without its migration fails
the suite instead of a deployment. The RM-60 `ALTER TABLE` stopgap survives only as the
"lift a pre-Alembic database to the baseline" step and never grows again.

## RM-69 — Replica failover, active health checks, and group-based pricing (done)

**Why**: today a second replica adds neither throughput nor fault tolerance. The circuit
breaker is checked only against the deterministic pick, so one tripped instance 503s the
model while a healthy replica idles; retries re-hit the same dead instance; a dead replica
stays listed for up to 30s; and routing by instance id misses the price table entirely,
which also silently bypasses the monthly spend cap. Findings A-D of
`docs/model-identity-proposal.md` §7.

**Scope**:
- Pick among `resolution.members` skipping circuit-open instances; 503 only when *all* are
  open (§7 A).
- Retry/failover to a different replica instead of the same URL (§7 B).
- Active health checks from the gateway so a dead replica leaves the group in seconds rather
  than one 30s poll (§7 C, decision #9).
- Resolve pricing against the group's model rather than the raw client string, so no name
  routes to the same model untariffed and uncapped (§7 D).
- Out of scope: *choosing well* among healthy replicas — that's [[RM-72]]. This item only
  guarantees a healthy one is chosen.

**Verified live**, which is the only way this one is worth believing: with two real replicas
of the same model running, the replica the selector picks first was killed outright and the
very next request was served by the other one with a 200 — the logs show `backend.failing_over`
then `backend.failover_succeeded`. Before this, that request was a 503. A later request paid
no failover cost at all, because `health_monitor.backend_unreachable` had already taken the
dead replica out of the candidate list: the two mechanisms cover different windows rather than
duplicating each other.

Liveness deliberately means *the process answered*, not *answered 200*. sd.cpp serves image
generation and replies 404 on `/health` (verified against the running sd-server), so a
status-code check would have taken image generation down entirely.

An open circuit is only taken through `allow_request()` while nothing usable has been found
yet: that call acquires a distributed probe lock held for the whole recovery timeout, so
spending it on a replica that won't be used would delay that replica's recovery purely
because a sibling was healthy.

Streaming gets the healthy-replica pick but not mid-stream failover — once response headers
are sent there is nowhere to go, which is the existing AC-17c constraint, not a new one.

## RM-70 — Model/instance identity: immutable slug, opaque ids, per-model label (done)

**Why**: one string is simultaneously the catalog id, the serving process's id, the name
clients send, and the key for pricing, scopes and usage rows. [[RM-57]] separated routing
from instance identity but reused `models.id` as the group name, so the collision survived —
which is why instance suffixes leak into the scope picker. Full design and industry
research: `docs/model-identity-proposal.md`.

**Scope**:
- Models gain an opaque `id`, an immutable public `slug` (what clients send) and a mutable
  display `name`; instances gain an opaque `id` and a `label` auto-incremented per model.
- `modality` moves to the catalog (it's a property of the weights); `context_length` stays
  per instance, resolution keeps using the group `min()` (decision #5).
- Old model and instance ids kept as aliases so existing tokens and SDK calls keep working.
- Existing `model:<id>` grants are **reissued**, not silently remapped (decision #4).
- Direct instance addressing moves to an `X-Prometheus-Instance` header, out of the `model`
  field (decision #2).
- Depends on [[RM-68]]. Out of scope: OpenAI-style dated snapshots — the slug design leaves
  room, building it now would be speculative.

**Opaque primary keys: deliberately not done.** Three arguments motivated them; two were
answered another way. Instance ids are no longer routable — targeting one moved to the
`X-Prometheus-Instance` header and `/v1/models` lists only models — and there is nothing to
rename, because slugs are immutable by decision. The third, that deleting and recreating a
model would inherit its history, turned out not to be fixed by opaque ids at all: billing,
pricing and grants key off the *slug*, not the catalog id. [[RM-74]] closes that instead.

Against that, the swap costs a service window — lifecycle.py names every running process's
PID and log file after its instance id, so renaming strands the processes the manager is
supervising — and would make logs unreadable (`ins_01J8XR9K2M4P.log`), which is an operability
regression. A semi-opaque `qwen3-0.6b-01J8XQ` was considered and rejected too: it prevents
reuse without bookkeeping, but changes the client-facing name on every re-registration and
taxes every consumer's ergonomics forever to prevent a rare event.

## RM-74 — Retired model names are never reused (done)

**Why**: deleting a model used to free its slug immediately. Usage rows are keyed by slug and
`model:<slug>` grants are written against it, so a new model taking a retired name would
inherit the old one's invoices *and* silently grant every client who could reach the old model
access to the new one — the exact thing the "reissue grants explicitly" decision exists to
prevent. Same reason npm, PyPI, S3 and Docker Hub never recycle a public name.

**Scope**:
- `archive_catalog()` replaces `remove_catalog()`: the row stays with `archived_at` set. An
  archived model doesn't route, doesn't list, and can't take an instance.
- Both the slug guard and a new id guard consult archived rows. The id guard matters because
  the catalog upsert is `INSERT OR REPLACE` — without it a new model reusing an archived id
  would overwrite that row and inherit its slug and history.
- `restore_catalog()` is the escape hatch, and the reason archiving can be the default:
  archiving the wrong model is undoable, losing its name is not.
- Keeping the row rather than a tombstone table also keeps the metadata, so a billing question
  about usage recorded under that name stays answerable — which file, which quantization.
- Out of scope: reserving *instance* ids. They're forensic, not a billing or auth key.

## RM-71 — Replica UX: "Add instance" flow and a model-level scope picker (todo)

**Why**: creating a replica today means re-entering every field of a full registration form,
and the scope picker lists individual instances, so granting access exposes the `-1`/`-2`
suffixes instead of one model.

**Scope**:
- "Add instance" from a model row asking only what genuinely differs between replicas —
  node, engine, port — inheriting the rest from the catalog, with the label auto-incremented.
- The full registration form stays for registering a genuinely new model.
- Scope picker lists models with a replica-count pill; instances never appear.
- Show observed capacity (`/slots` across healthy instances) beside the configured rate
  limit, with a warning when they diverge (decision #8).
- Depends on [[RM-70]].

## RM-72 — Gateway: intelligent load balancing across replicas (done)

**Why**: absorbs [[RM-58]]. Once [[RM-69]] guarantees a *healthy* replica is picked, the
remaining question is picking the *best* one.

**Scope**:
- Least-in-flight first — counted by the gateway itself, so it works for sd.cpp too, which
  exposes no metrics endpoint at all (verified live).
- Then free-slot and queue-aware, from llama.cpp's `llamacpp:requests_deferred` and `/slots`
  (`is_processing`, `n_ctx` vs `n_prompt_tokens` — real context saturation, verified live).
- Optional session affinity for multi-turn prefix-cache reuse. It *conflicts* with
  least-loaded by design, so the strategy is configurable per model rather than global.
- Depends on [[RM-70]]. Out of scope: cross-node scheduling beyond picking among instances
  the gateway already aggregates.

**Shipped**: least-in-flight, counted by the gateway (sd.cpp exposes no metrics endpoint at
all, so anything derived from engine numbers would silently stop balancing image generation),
normalised by the concurrent slots each engine reports. Ordering happens inside
`forward_with_failover` with no await between the sort and the count — the first attempt put
it inside `forward()`, passed its unit tests, and did nothing live: six concurrent requests
all selected before any had incremented anything and landed on the same replica.

**Session affinity: deliberately not built.** Measured first, on this hardware, with a
~900-token prefix sent twice:

| | prefill cold | prefill cached |
|---|---|---|
| qwen3-0.6b | 133 ms | 11 ms |
| gpt-oss-20b | 796 ms | 35 ms |

So the prize is real — ~95% of prefill, 0.8s per turn on the 20B — and two replicas mean two
separate caches, so a multi-turn conversation alternating between them pays it every turn.

What tipped the decision is that affinity conflicts with least-loaded *by design*: a client
pinned to a busy replica queues while its sibling idles, which is why the industry makes the
strategy per-model configurable, and that is real configuration surface for a workload that
doesn't exist here yet. Current concurrency is far below the 8 slots already available.

**Do not expose llama.cpp's `--parallel`.** Verified against llama-server b10101: with no
`--parallel`, a server comes up with 4 slots, `kv_unified=true`, and the full `--ctx-size`
available to each. Passing `--parallel 8` gives 8 slots, `kv_unified=false`, and
`n_ctx_slot=512` — the context is partitioned, so raising the slot count silently cuts every
request's usable context to an eighth. Replicas, not `--parallel`, are how concurrency grows
past 4 on one machine; the two caches that come with them are the unavoidable cost.

**Revisit affinity when** either a second node exists (caches then sit on different machines
with no way to share them) or sustained concurrency exceeds one server's 4 slots often enough
that requests genuinely queue.

## RM-73 — Billing traceability per replica + per-model metrics rollup (todo)

**Why**: usage rows record the name the client sent and nothing about which replica served,
so a billing dispute can't be traced to a machine. And `MetricsStore` indexes only by
instance, so with replicas the dashboard shows N loose rows and no way to see a model's
throughput — the only unit a consumer cares about.

**Scope**:
- `usage_events` records the opaque model id (survives renames), the slug (what appears on
  the invoice) and the instance that served.
- Per-model aggregate in `MetricsStore` alongside the existing per-instance entries.
- Fix the Overview's "circuits open — of N models" label, which counts backends and so
  reports "2 models" for two replicas of one.
- Depends on [[RM-70]].

## RM-75 — fix: manager registry path resolved against the process's cwd (done)

**Why**: `RegistryConfig.path` defaults to the relative `runtime/manager/registry.db`, and
`resolved_registry_path` handed it to `Registry` as-is, so the registry a manager opened
depended on the directory it happened to be started from. `Registry.__init__` does
`self._path.parent.mkdir(parents=True, exist_ok=True)`, so instead of failing loudly it
silently created a fresh, empty database — running any `pmgr` command from
`runtime/manager/` produced a nested `runtime/manager/runtime/manager/registry.db`. The
live manager only ever used the right file because it was launched from the repo root.

**Scope**: `resolved_registry_path` now anchors a relative path to the repo root
(`Path(__file__).resolve().parents[5]`, the same trick the gateway's model registry
already uses) and leaves absolute paths alone, so the container's
`PMGR_REGISTRY_PATH=/data/...` is unaffected. Path resolution only — no config-loading or
`Registry` changes, and the relative default in `manager.toml` stays as-is since it now
means the same thing from anywhere. Deliberately out of scope: the sibling
cwd-relative paths (`[server].log_dir`/`pid_dir`, `[downloads].dir`, `[tui].log_file_path`)
have the same weakness but write logs and models rather than the source of truth.

**Verified**: reproduced first — `pmgr list` from `runtime/manager/` created the nested
copy, and re-running it after the fix did not. The package test suites were *not* the
cause, contrary to the initial guess: all three pass from inside `runtime/manager/` without
creating anything, because their fixtures pass an absolute `tmp_path`. New tests cover the
default being absolute, resolving identically from three different cwds (including
`runtime/manager/`), and absolute paths — config and `PMGR_REGISTRY_PATH` — surviving
untouched. The stray nested file was empty (a `models` table with 0 rows, no `instances`
table) and confirmed via `lsof` not to be open by the running manager, which holds the real
`runtime/manager/registry.db`; deleted, with the manager left healthy (29 models, 10
instances, `/health` 200).
## RM-76 — A manually registered model can't be archived (done)

**Why**: found while verifying [[RM-74]]. `DELETE /v1/models/{id}/downloaded` returns 400
`not-downloaded` for anything that didn't come through the download flow, and
`DELETE /v1/backends/{id}` only ever removed an instance — so a model registered by hand has
no path to `archive_catalog()` at all. Pre-existing, but it matters more now: archiving is
the mechanism that keeps a published slug from being handed to a different model.

**Scope**:
- A way to retire a catalog entry that doesn't depend on there being a file to delete.
- Decide whether that's a new endpoint or a flag on the existing one — the current endpoint
  conflates "reclaim the disk" with "retire the model", which is why the gap exists.
- Out of scope: changing what `DELETE /v1/backends/{id}` does. Removing an instance and
  retiring a model are different acts and should stay different calls.

**Resolved by separating the two acts.** `DELETE /v1/models/{id}` retires a model — stops and
removes its instances, archives the catalog row, touches no files, and works regardless of how
the model was registered. `DELETE /v1/models/{id}/downloaded` keeps its old meaning, reclaiming
the disk, and now says in its own docstring which of the two a caller wants.

It lives beside `restore` in control.py rather than with the download endpoints: archive and
restore are a pair, and control's router is registered first, so there is no chance of
discovery's `/v1/models/{model_id}/...` patterns shadowing it.

## RM-77 — fix: the response body named the replica; `context_length: 0` was ambiguous (done)

**Why**: found by the Axonium SDK team reading the contract back to us. `sdk-changes-2026-09.md`
§3 says `model` is for models — a grant covers a model, billing attributes to a model,
`/v1/models` lists models — and the response body was the one surface where that wasn't true:
llama.cpp echoes its own `--alias`, which is the instance id, and the gateway passed the body
through untouched. Anyone attributing cost by `response.model` was billing an identifier that
doesn't appear in the catalog, split across replica names nobody recognises.

**Scope**:
- The body names the model on all three endpoints and on every streamed chunk. The replica
  stays knowable through the `X-Prometheus-Instance` headers, where it belongs.
- An alias request is answered with the canonical slug — the same thing OpenAI does when a
  `gpt-4o` request reports the snapshot it resolved to.
- Image models advertise `context_length: null` rather than `0`. Zero reads as "a window of
  zero", so a client checking `prompt_tokens < context_length` would reject every image
  request; null says "no such concept". **Contract change** for typed SDKs, where the field
  has to become optional.
- Out of scope: `family: ""` on two catalog models. That's a missing value, not a type
  problem — the fix is filling it in.

## RM-78 — Idempotency keys for inference requests (done)

**Why**: a client retry is always a new, billable generation, so an SDK can only retry where
the platform proves nothing ran — which excludes the commonest case, retrying after its own
timeout. Raised by the Axonium SDK team, who halted their retry work rather than guess about
billing.

Note what this is *not* for. Gateway-internal failover never double-bills: usage is recorded
once per returned response, never per attempt, so an aborted attempt writes nothing. That
wastes our compute on the abandoned instance, not the client's money. The answers document
originally conflated the two; the correction matters because it unblocks work the SDK team
had stopped.

**Scope**:
- `Idempotency-Key` request header on the three non-streaming endpoints, following the shape
  OpenAI and Anthropic already use (24h window, replay the stored result).
- Stored in the gateway's own database, not Redis. Redis here keeps only periodic snapshots,
  so a restart would drop keys and the retry that follows would regenerate and bill twice —
  precisely the failure this exists to prevent. Billing correctness needs the durable store,
  and the volume is low because the key is opt-in.
- Two states. A duplicate arriving while the first is still running gets `409`, or two
  concurrent retries generate twice and defeat the point.
- A fingerprint of the request, so reusing a key with different parameters is refused rather
  than answered with someone else's result.
- Bodies are retained up to 1 MiB, which covers chat, embeddings and a 512×512 image with
  room. Above it the key is still recorded but the body isn't, and a replay gets `409` saying
  the original succeeded — losing the guarantee silently would be worse than either.
- Out of scope: streaming. Replaying one means storing every chunk, and neither OpenAI nor
  Anthropic documents that semantics clearly.

**Verified live**: the same key twice returned an identical response with `Idempotent-Replay:
true`, and `usage_events` went 65 → 66 → 66 — the replay neither generated nor billed. Reusing
the key for a different question returned 409; a request without a key still recorded usage
normally.

**Settled in middleware, not at each return.** The handlers have a dozen exit paths between
claiming a key and producing a result, and a new one would silently leave the key held for the
whole window — blocking exactly the retry it protects. The handler leaves the body on
`request.state` instead of the middleware reading it back, because `call_next` hands middleware
Starlette's streaming wrapper rather than the `JSONResponse` the handler built: the first
attempt checked `isinstance(response, JSONResponse)`, which is never true there, so every key
was released and nothing deduplicated. Unit tests passed throughout — only the end-to-end path
showed it.

## RM-79 — The bare-metal stack has no Redis of its own (done)

**Why**: `podman-compose.yml` deliberately doesn't publish 6379 — Redis is internal to the
container network, which is right when the gateway runs in a container beside it. Running the
stack on the host, nothing owns that dependency, so it had been silently using whichever
container happened to publish the port. When that unrelated container stopped, every
authenticated request began failing closed with `401 invalid-token`.

What made it expensive was the diagnosis, not the outage: `/health` kept returning 200,
because the process *was* alive, and the symptom — 401 on every request — reads like broken
credentials rather than a stopped container.

**Scope**:
- A dev Redis that belongs to this project, published on the host.
- `/metrics` reports whether each dependency is actually reachable, and says what an outage
  breaks in plain words. The dashboard already polls that endpoint, so the Overview shows a
  non-dismissible banner rather than leaving the operator to infer it from 401s.
- `/health` is unchanged on purpose. It is a liveness probe (spec 001 AC-4) and the process
  really is alive; conflating the two would make a dependency outage look like a reason to
  restart the gateway, which would not help.
- Out of scope: readiness gating. Nothing orchestrates this deployment yet, so a `/ready`
  endpoint would have no consumer.

## RM-80 — fix: idempotency refusals shared one error type (done)

**Why**: found by the Axonium SDK team probing RM-78 against the deployment. All four reasons
a key can be refused answered `409 idempotency-conflict`, separated only by `detail` — so a
client had to match on prose, which is exactly what we removed from the 422 envelope in
[[RM-65]]: a reworded message then breaks a client in silence. And the four need opposite
handling. Only "still running" resolves by retrying; the other three never do.

A malformed key was the worst of it. It never conflicted with anything, so a client branching
on "conflict" concluded it had repeated a request when its key simply didn't fit.

**Scope**:
- One `type` per reason: `invalid-idempotency-key` (400), `idempotency-key-reuse`,
  `idempotency-in-progress`, `idempotency-response-not-retained` (409 each).
- `Conflict` became `Refusal` carrying a `kind`, so the reason is decided where it's known
  rather than reconstructed from the message at the edge.
- Out of scope: a `Retry-After` on the in-progress case. The only bound available is the
  backend timeout, 600s, which is an upper bound rather than an estimate — a number that
  pessimistic is worse than none.

## RM-81 — Idempotency: estimate the wait on an in-flight refusal (done)

**Why**: RM-80 gave each refusal its own type; the SDK team had also offered a `Retry-After` on
the in-flight one as an alternative. That was declined on the grounds that the only bound
available was the 600s backend timeout — an upper bound, not an estimate. The grounds were
false: [[RM-73]] already pools observed latency per model, and the record knows when the first
request started, so `p95 − elapsed` is a measurement. The refusal reasoned from an assumption
about the codebase instead of checking it.

**Scope**: `MetricsStore.model_latency_p95_ms()` pools the same samples the /metrics rollup
uses; `idempotency-in-progress` carries the derived `Retry-After`, with a short default when a
model has no observations yet, since those counters reset with the process. The other three
refusals deliberately carry none — a hint on a refusal that never resolves would invite the
retry we are telling the client not to make.

## RM-82 — Idempotency for streaming responses (done)

**Why**: a streaming retry still regenerates and bills twice — the same exposure [[RM-78]]
closed for non-streaming, left open in what is probably the busier path for a chat SDK. The
original decision framed this as "not going to do it", justified by there being no precedent:
neither OpenAI nor Anthropic supports replaying or resuming an LLM stream. True, but it omitted
that the billing hole is identical, which makes it a backlog item rather than a refusal.

**Scope** (not designed):
- A stored key can cover a *client* connection that dropped after our stream from the model
  completed — the full response was received and can be replayed.
- It cannot cover a stream the model itself broke, which is the case a client most wants
  covered: there is nothing complete to replay.
- Covering that needs resumption rather than replay — SSE's `Last-Event-ID`, plus a cursor for
  non-EventSource clients. Different work, and nobody comparable has built it.
- The SDK's own behaviour — rejecting a key on `stream()` — should now be relaxed for the
  cases above, and kept for a retry after the model's own stream broke.

**How it works**: the generator buffers what it emits and stores the assembled SSE body on a
clean finish, subject to the same 1 MiB cap as any other response; a replay re-emits it in one
chunk, since the client parses frames rather than timing them.

The claim cannot be settled by the middleware that handles every other endpoint: `call_next`
returns for a `StreamingResponse` *before* the generator has produced anything, so the
middleware would hand the key back while the response was still being made. The handler takes
the claim off the request and gives it to the generator, which settles it in its `finally`.

A stream that ended in an in-band error frame is released rather than stored. Storing it would
replay the failure to every retry, and the caller could never get past it.

**Verified live**: the same key twice on a real streamed completion — 127ms then 4ms, the same
27 frames byte for byte, `Idempotent-Replay: true`, and `usage_events` unchanged on the
replay.

## RM-83 — Mark an interrupted stream on the usage row (done)

**Why**: a stream that breaks halfway still bills for the tokens it produced, which is what
Anthropic and OpenAI both do — the compute was spent whether or not the client read the
result. Researched before deciding, because the alternative (refunding a partial generation)
under-bills real work and invites a client to disconnect on purpose. The gap was never the
policy, it was the evidence: a client disputing the charge for a half-delivered answer had
nothing to point at, and neither did we.

**Scope**: one boolean on `usage_events`, set when the stream ended on an error rather than
cleanly, surfaced in the CSV export. Not a refund path, not a separate price — the row says
what happened, a human decides whether to credit it.

**What the flag exposed**: it was unreachable as first written. llama.cpp reports token counts
only on its final `timings` frame, and a stream that dies mid-answer never sends one — so
`completion_tokens` stayed 0, the usage guard dropped the row, and every interrupted stream was
billed as zero and left no trace. The generator now tallies content chunks as they go (one
chunk is one token for llama.cpp) and falls back to that tally, with the prompt estimated the
same way the context check already estimates it. A clean stream is untouched: the real
`timings` numbers still win.

A stream that broke *before* the model emitted anything still writes no row: nothing was
generated, so there is nothing to bill or to mark. Only a partially delivered answer reaches
this flag.

## RM-84 — fix: the manager's JWKS URL pointed at a path that does not exist (done)

**Why**: found by restarting the stack. `manager-api` answered `401 Invalid or expired token`
to freshly minted, perfectly valid tokens, which sends the investigation to the credentials —
the one place the problem was not. The real cause was its JWKS URL, in two independent ways:

- `ApiConfig.jwks_url` defaulted to `/v1/jwks`, which the auth service does not serve and never
  did; it returns 404. Any manager started without a `manager.toml` could therefore never
  authenticate anyone. The embedded TOML defaults had the correct path, so which one applied
  depended on whether a config file happened to be found.
- The configured host was `localhost:9000`. `localhost` also resolves to `::1`, and anything
  bound to `0.0.0.0:9000` on the machine answers before a service bound to `127.0.0.1`. On this
  machine another project's object store did exactly that, and the manager was parsing an XML
  error page as a key set.

**Scope**: correct both defaults, in `manager.toml`, its example, the embedded defaults, and the
image's own `PMGR_JWKS_URL` — which had the 404 path too and was only ever right because the
compose files override it. Three tests anchor it, since a wrong default that nothing asserts is
how this survived.

**Not fixed here**: the manager still reports a dependency it cannot reach as `401 Invalid or
expired token`, which is what made this expensive to find. Fixed separately in [[RM-85]].

## RM-85 — fix: the manager blamed the caller for its own outage (done)

**Why**: [[RM-84]] took hours to find because of this. When the manager could not fetch the key
set, it answered `401 Invalid or expired token` — which does not mean "something went wrong",
it means "your credential is bad". So the investigation went to the credentials, rotated a
secret, re-minted tokens, and checked scopes, none of which could ever have helped. The log
held the real cause the whole time; the HTTP response contradicted it.

Note this is *not* what [[RM-79]] did for the gateway. That one surfaced dependency health in
`/metrics` so an outage is visible on the dashboard, and left the per-request status alone.
This is the per-request half, and the two are complementary.

**Scope**: failing to obtain a usable key set now raises its own error and becomes `503` with
`Retry-After` and a distinct problem type, saying explicitly that the caller's credentials are
not implicated. A token that was actually checked and found bad is still `401`. "Usable" covers
a URL that answers `200` with something that is not a key set — the RM-84 case exactly, where
another service on the port replied and its error page was parsed as keys.

**What it exposed**: `test_AC12_invalid_token_returns_401` had never once tested token
validation. It pointed at a URL serving no key set, so every request died fetching JWKS and the
401 it asserted came from the outage path. Separating the two statuses is what made it fail. It
now stubs the key set so the malformed token is what gets rejected.

**Verified live**: the manager pointed back at the hijacked URL with a valid token returns 503
and `Retry-After: 5` where it used to return 401; healthy tokens still get 200, and bad or
missing ones still get 401.

## RM-86 — registry.db is runtime state, not seed data (done)

**Why**: it was the single exception in `.gitignore`, and the exception was reasoned:
[[RM-49]] replaced a small hand-edited `registry.yaml` with SQLite, and the new file inherited
the old one's "committed seed data" status. That justification quietly expired. The file became
the live operational catalog — mutated by every registration, retirement and instance change —
and accumulated 26 model paths under one developer's home directory, in a public repo. Four
commits across its whole history, so it was not being maintained as seed data either: it
drifted silently and got committed occasionally, which is the worst of both arrangements.

Industry practice is consistent here: track schema and migrations, not the binary database.
A `.db` cannot be diffed or merged, and two machines registering models produce a conflict
with no resolution. It is also what this repo already does everywhere else — `pricing.yaml` is
ignored and `pricing.yaml.example` committed, the same for `.env`. The fix is to apply the
project's own existing convention to the one file that was exempt from it.

**Scope**: untrack `registry.db` (the working file stays put), drop the negation rule, and
commit `registry.db.example` — generated through `Registry` itself rather than hand-written SQL,
so its schema cannot drift from the code. Two illustrative models, repo-relative paths, no
instances (those are port- and machine-specific). Three tests guard the arrangement: the seed
exists and loads, it carries no absolute paths, and a missing registry is created rather than
fatal — the seed is an offer, not a prerequisite.

**Not done**: the absolute paths remain in git history. Removing them means rewriting history,
which is not worth it for a developer username and would break every existing clone.

**Warning when you pull this**: untracking a file deletes it from the working tree of everyone
who pulls the change — git does not know the local copy is precious. Anyone with a running
manager loses their live catalog on `git pull`, silently, while the process keeps serving from
the unlinked inode so nothing looks wrong until the next restart. This was learned the direct
way, on the machine that made the change. **Copy `registry.db` somewhere outside the repo
before pulling**, then copy it back. A running manager can also be read through its API
(`/v1/models`, `/v1/models/archived`, `/v1/backends`) while the process is still alive, which is
what made recovery verifiable here.

## RM-87 — a streamed request was only accounted for if the client drained the body (done)

**Why**: Axonium reported that streaming replay ([[RM-82]]) was unreliable — 0 replays in 6
tries, against 6 of 6 non-streaming — and asked for nothing. Reproducing it found a much larger
defect underneath, and their report is the only reason it was found at all.

Three things compounded:

- We forwarded the backend's own `data: [DONE]` **and** appended our own, so every streamed
  response carried two terminal frames.
- A client that stops reading at the first terminal frame — which is what every OpenAI-shaped
  SDK does — leaves the generator suspended. Everything the gateway does after that point ran
  only when the generator was eventually torn down, inside a request task the server had already
  cancelled, so it did not run at all.
- Everything that records what happened lived after that point: usage, metering, the
  idempotency settle, the spend-cap settle.

**Measured before the fix**: three streamed generations from an SDK-shaped client produced
**zero** usage rows, and still zero fifteen seconds later, while three identical ones from curl
(which drains to EOF) produced three. A client abandoning a long generation mid-way was also
billed nothing — the cheapest way to use the platform was to disconnect.

**Fix**: emit the terminal frame once, ourselves, and only after the request is fully accounted
for. Accounting is dispatched as a detached task rather than awaited, because creating a task is
synchronous and survives the cancellation that a disconnect triggers — a client that walks away
mid-generation is the case [[RM-83]] says we must still charge, not one to let through. The
idempotency settle stays awaited on the normal path so a retry arriving immediately finds a
stored result rather than a claim still being written, with the detached path as its fallback.

**Also fixed**: [[RM-83]]'s token fallback counted only `delta.content`. A reasoning model
streams its thinking as `reasoning_content`, so a stream abandoned while the model was still
reasoning tallied zero tokens and was billed nothing. TTFT deliberately still keys off visible
content — it is a latency metric with history behind it.

**Verified live**, all against the running stack: SDK-style 3/3 billed (was 0/3), draining 3/3,
abandoned mid-generation 1/1 (was 0), Axonium's exact measurement 6/6 replay (was 0/6), and a
retry with no delay at all 3/3 replay. One terminal frame per response, down from two.

**Known**: a stream the *client* abandoned is billed but not flagged `interrupted`, since
nothing broke on our side. Whether a client walking away should be visible on the usage row the
same way a broken stream is, is a policy question rather than a defect.

## RM-88 — usage rows say why a request stopped, not just that it did (done)

**Why**: [[RM-87]] left a gap it created. Before it, a caller hanging up mid-answer wrote no
usage row at all, so `interrupted` only ever had one cause and a boolean was enough. Once those
requests started being billed, the same column had to carry two events that are nothing alike:
*we* cut the answer short, or *they* did. Those are opposite conversations to have when a charge
is questioned, and a chat UI's stop button produces the second one all day long.

Leaving it also broke a promise already in a client's hands: the answers document sent to
Axonium says "if you are charged for an answer you never fully received, that row says so." A
stream the caller abandoned is exactly that, and it was not being marked.

Industry practice points the same way twice — usage data is financial data and a dispute is
resolved by tracing a line back to the event that produced it, *with its reason*; and a state
with three values is an enum, not a boolean with a comment.

**Scope**: `termination_reason` on `usage_events` — `complete`, `upstream_error`,
`client_disconnected` — derived from what the code already knew and was discarding. `interrupted`
stays as a derived column rather than being dropped: SDK clients parse it and it now means
precisely what they were told. It is never set independently, so the two can never disagree on a
row. The CSV gains the reason as a trailing column, leaving every existing position untouched.

The migration reads existing rows honestly rather than guessing: `interrupted = 1` can only have
meant `upstream_error`, because the other cause wrote no row before RM-87.

**Also settled here**: we bill the tokens actually *sent* to the caller, not everything the GPU
produced. That is what the implementation already did and it is the narrower, more defensible of
the two measures — but RM-83's notes and the Axonium document both described it as billing what
was generated, which overstates it in our own disfavour. Worth correcting the next time we write
to them.

**Verified live**: three streamed requests against the running stack produced `complete` (an SDK
stopping at `[DONE]`), `complete` (a client draining to EOF) and `client_disconnected` (a client
abandoning a long generation, billed 11+5 tokens). The CSV export carries both columns and
leaves them blank on the TOTAL row. The migration backfilled the live database with no
`interrupted` rows to reinterpret.

## RM-89 — `family` is never stored empty (done)

**Why**: Axonium reported a blank `family` on `qwen3-0.6b` and `qwen3-embedding` in three
consecutive rounds, each time saying it blocked nothing. Both HTTP registration paths defaulted
the field to `""`, and an empty string says nothing about whether the value is unknown, not
applicable, or simply never filled in.

**What was actually wrong, and what was not**: every active model in the catalog already had a
sensible family — the blank Axonium saw came from the gateway serving a catalog it could not
resync while [[RM-84]] was breaking its calls to the manager, and it resolved when that did.
Nothing needed reassigning. What was missing was the guarantee that it cannot happen again.

**Scope**: the registry fills the field at `add_catalog`, the single chokepoint both HTTP paths
go through, so a third path cannot reintroduce the gap. A caller-supplied family always wins and
is never overwritten. With none supplied, the GGUF's `general.architecture` is read from the file
header; failing that, `unknown` — the convention `infer_quant` already uses with `"?"`.

**Measured rather than assumed**: guessing a family from the identifier was implemented,
measured against this deployment's 28 models, and *thrown away* — it got 14 of them wrong.
`llava-mistral-7b-q5` is architecture `llama`, which no amount of reading its name reveals.

**Known, and deliberately not resolved here**: `general.architecture` is a true fact about a file
but is not always the lineage a human would name — phi4-mini reports `phi3`, minicpm5 reports
`llama`, and several finetunes report the base they were built on. That is why a supplied family
wins, and why existing rows were left untouched. Whether the two should be separate fields —
architecture as read from the file, family as a human names it — is a modelling question worth
answering before the fallback value ever appears on a customer-facing surface.

## RM-90 — honour `OTEL_RESOURCE_ATTRIBUTES` (done)

**Why**: the Argus team — who are centralising monitoring across the owner's applications —
found that `configure_tracing()` built its `Resource` with the direct constructor, which
silently discards both the SDK's resource detectors and the standard
`OTEL_RESOURCE_ATTRIBUTES` variable. Nothing errors; the attributes simply never appear. That
variable is how a deployment injects `service.namespace`, `service.version` and
`deployment.environment.name` without touching code, so without it every service of ours showed
up unattached to its platform: you could ask how `auth-service` was doing, but not how
Prometheus was doing.

**Scope**: one line, `Resource.create(attrs)` instead of `Resource(attributes=attrs)`, plus the
two tests Argus supplied. Existing behaviour is preserved — attributes passed to
`configure_tracing(resource_attributes=...)` still take precedence over the environment.

**Provenance**: patch and tests came from Argus. Both were read before applying rather than
applied on trust, and the first test was confirmed to fail without the change. Verified live:
with the variable set, `service.namespace`, `argus.component.role` and
`deployment.environment.name` all reach the Resource.

This is the first step of [[RM-91]] — integrating with Argus rather than running our own
observability stack.

## RM-91 — retire the self-hosted observability stack (done)

**Why**: a separate team, Argus, is centralising monitoring and alerting across all of the
owner's applications. Running our own Loki, Promtail, Tempo and Grafana beside that duplicates
the work and splits the picture in two. The move is deliberately gradual, starting with what we
have already stopped using.

**The distinction that governs this work**: what goes is the *backends* — the things that store
and display telemetry. What stays is the *instrumentation*: the `telemetry` package, the spans,
the trace ids. Argus asked for it explicitly ("no hace falta adoptar el SDK de Argus"), and
removing it would leave nothing to send them. Read as "remove everything to do with
observability", this item would break the integration it exists to enable.

**Stage 1 (done)**:
- `observability/` deleted — Grafana provisioning and dashboards, Loki, Promtail and Tempo
  configs, and the stack's own test script.
- The four services removed from `podman-compose.yml` and `podman-compose-ubuntu-dgx.yml`,
  along with their named volumes and a `depends_on: tempo` left behind on the manager.
- `GRAFANA_SECRET_KEY` and `GRAFANA_ADMIN_PASSWORD` dropped from both installers, both
  validators, the env examples and the hook's own assertions — generating secrets for a service
  we no longer ship is worse than not having them.
- `OTEL_EXPORTER_OTLP_ENDPOINT` no longer hardcodes `http://tempo:4318` in compose; it comes
  from configuration, which is where Argus supplies it.

**Deliberately not done in stage 1**, each for its own reason:
- `_DEFAULT_ENDPOINT = "http://tempo:4318"` in `telemetry/tracing.py` still names the deleted
  container. Changing it to "unset means do not export" was written and reverted: it flips
  `_TRACING_ACTIVE`, which changes what `TraceIDMiddleware` puts in every log line. That is a
  behaviour decision, not a deletion, and it deserves its own item. It also costs ~45% of the
  pre-push hook's runtime today, measured.
- `grafana_url` stays. It is a link on the Overview page and nothing depends on what serves it,
  so it repoints at Argus rather than being removed.
- The `ops:dashboard` scope stays. Clients may hold it; withdrawing a granted scope is a
  breaking change and needs its own decision.

**Next**: agree the endpoint question above, then the `traceparent` conversation Argus flagged —
`TraceIDMiddleware` starts a fresh root span and ignores an incoming one by deliberate design,
which stops a trace crossing service boundaries. They said it does not block their pilot.

## RM-92 — remove what the retired stack left behind (done)

**Why**: [[RM-91]] deliberately stopped at the infrastructure and left four things standing,
each for a stated reason. With monitoring moving to Argus wholesale, the owner's call was to
leave no residue behind rather than carry it into the migration.

**Removed**:
- `_DEFAULT_ENDPOINT = "http://tempo:4318"`. There is no default collector now; unset means
  export nowhere. This is the change RM-91 wrote and reverted, because it flips `_TRACING_ACTIVE`
  and so changes what `TraceIDMiddleware` puts in a log line — deliberate here: that flag means
  "spans leave this process", and with no collector they do not, so advertising ids nobody can
  look up would be worse than not. It also ends the retry storm that cost ~45% of the pre-push
  hook's runtime, measured.
- `grafana_url` — the setting, the `/admin/api/config` field, its TypeScript type, and the
  Overview link. The endpoint and its hook stay; `Limits.tsx` still uses them.
- The `ops:dashboard` scope, from the auth service, the scope picker, and the SDK guide.
  Confirmed against the live auth database first: no principal held it.
- `podman-compose-ubuntu-dgx.yml`, orphaned — even the DGX installer and validator drive
  `podman-compose.yml`.

**A flaky test this surfaced, and fixed**: `test_reasoning_tokens_are_counted_when_a_stream_breaks`
failed roughly two runs in three. [[RM-87]] made a streamed request's accounting a detached task
precisely so a disconnect cannot cancel it, and that same independence let a task from one test
still be writing while the next counted rows. The fixture now drains them. The flakiness was
real and pre-existing since RM-87; it happened to show up here.

**Namespace, raised by the owner and settled**: Argus were labelling our services
`service.namespace=edge-ai-inference`, which is this repository's directory name rather than the
product's. Agreed value is `prometheus-inference-platform` — not bare `prometheus`, because
Prometheus is also the ubiquitous metrics system and that name inside an observability platform
would be read wrong in queries, in alert routing, and at three in the morning. The variable is
set by Argus's deployment configuration rather than our code, so the change is theirs to make;
the value is updated in the tests they gave us and the request is in
`docs/argus-answers-2026-09-13.md`.

## RM-93 — services identify themselves to Argus (done)

**Why**: Argus reported, factually and without knowing what it implied, that `gateway`,
`manager-api` and `manager-core` had never emitted anything. Checking why found two defects of
ours and one non-defect.

**The one that matters**: `manager-api` and the terminal UI both called
`configure_tracing(service="manager")`. Two processes, one identity. Argus had just built a
silence probe that pages when a service stops emitting for 15 minutes — and with both named
`manager`, **a developer leaving the TUI open keeps the API looking alive after it dies**. Their
probe was defeated by our naming before it ever ran. Now `manager-api` and `manager-tui`.

**The second**: nothing in our deployment set `OTEL_SERVICE_NAME` or `OTEL_RESOURCE_ATTRIBUTES` —
not compose, not the env examples, nowhere. So no service ever carried a namespace, and the
`edge-ai-inference` Argus saw came from their own manual test run rather than from us. Both
variables are now set per service in `podman-compose.yml` and in the env examples, with
`service.namespace=prometheus-inference-platform`.

**The non-defect**: `manager-core` has no entrypoint. It is a library imported by the API and the
TUI and can never emit as a service of its own, so Argus are holding a catalogue entry that will
never connect. Told them to remove it rather than wait for it.

**Why nothing had emitted**: not instrumentation — all four services call `configure_tracing`,
and all four route through the same `prometheus_telemetry` via thin re-export shims, so
[[RM-90]]'s fix reaches every one of them. They had simply never been pointed at a collector.

**Verified live against Argus's own collector**, which turned out to be already running on
`localhost:4318`: the three services restarted with their identity, and the collector's
`otelcol_receiver_accepted_spans_total` went 297 → 337 with zero export errors. That is the first
telemetry `gateway` and `manager-api` have ever produced.

## RM-94 — telemetry env vars go where they are actually read (done)

**Why**: [[RM-93]] added `OTEL_SERVICE_NAME` and `OTEL_RESOURCE_ATTRIBUTES` to each service's
`.env.example`. That does nothing. Those files are parsed by pydantic-settings into a Settings
object and never reach `os.environ`, which is where the OpenTelemetry SDK reads them — confirmed
by test, not reasoning: a `.env` containing `OTEL_SERVICE_NAME` leaves `os.environ.get(...)`
returning `None`. The instruction looked right and was inert, which is the worst kind of
documentation. It also explains why the stale `OTEL_EXPORTER_OTLP_ENDPOINT=http://tempo:4318`
sitting in the live `gateway/.env` had never actually done anything.

**Scope**: the `.env.example` files now say plainly that these are process environment variables
and do not belong there, with the export lines shown. `runtime/telemetry.env.example` is the
bare-metal mechanism — `source` it, or point systemd's `EnvironmentFile` at a copy. Compose was
already correct, since its `environment:` entries are real process environment.

**Also, from Argus's review of our first real telemetry**:
- `OTEL_SEMCONV_STABILITY_OPT_IN=http/dup`. Our spans carried only the pre-2023 HTTP attribute
  names, so our traffic was invisible to every aggregation built on the stable ones — their
  aggregation by `server.address` over 230 of our spans came back empty. `http/dup` emits both
  spellings during the transition.
- `service.version` and `service.instance.id`. Without the first, an incident cannot be
  correlated with the deploy that caused it. Without the second, replicas share one identity and
  one dead instance of three is invisible — which is [[RM-93]]'s defect at another scale, worth
  fixing before it happens rather than after.

**Verified live** against Argus's collector: the gateway restarted carrying all of it, and their
`otelcol_receiver_accepted_spans_total` kept climbing with zero export errors.

## RM-95 — health probes ask where the engine answers, and stop tracing themselves (done)

Three things, all prompted by Argus reporting our gateway hammering a backend with 404s.

**The 404 was a workaround for a problem that did not exist.** The monitor treated *any* answer
as proof of life, justified by sd.cpp having no `/health`. It does answer `GET /` with 200 and the
body "Stable Diffusion Server is running" — measured, once someone looked. The rule it replaced
was wrong in a way worth naming: a 404 proves only that something speaks HTTP on that port, and
cannot tell a healthy backend from a broken one or from **an unrelated process that took the
port** — which is exactly what [[RM-84]] was, a container from another project bound to a port we
expected and its XML error page parsed as a key set. Every orchestrator treats 200-399 as success
and everything else as failure; so do we now, probing the path each engine actually serves.

The engine was available all along: the manager has always sent `backend` per instance and the
gateway dropped it building its `ModelEntry`. Caught live — the first run marked sd-turbo
unhealthy because the field was wired into one of the two construction sites and the sync used
the other. It would have taken image generation offline.

**Probe spans were 57% of this gateway's telemetry**, measured by the team receiving them. Six
backends on a ten-second interval produce ~72 spans a minute answering no question anyone asks —
whether a backend is up is a metric, which is what `capacity()` and the unreachable set already
are. Suppressed at the source via `_SUPPRESS_INSTRUMENTATION_KEY` rather than filtered at the
collector, so nothing is built, serialised or shipped to be discarded at the far end.

**GenAI semantic conventions, emitted by hand.** Argus asked for them and offered a package that
produces them. They are a handful of constants on a span we already create, against a dependency
that currently ships as a loose pre-release wheel with no index — their own position was that the
attribute table is the contract and hand emission is equally fine. The streamed path gets its own
span, parented to the request's captured context, because the handler's span has ended long
before a stream's outcome is known; that is the path carrying `client_disconnected`, which Argus
called the most valuable attribute we have.

## PRM-96 — the SDK must reach only the gateway, never auth-service (done)

**Why**: `docs/sdk-integration-guide.md` tells clients to obtain a token with
`POST /oauth2/token` against **auth-service**, and then to call the **gateway** with it. So an
SDK needs network reach to both, and the service that issues credentials has to be exposed
wherever a client runs. That is a second public surface, with its own authentication, its own
CORS, and its own ways to be got wrong — for no benefit the gateway could not provide.

Confirmed: the gateway exposes no token endpoint of its own today.

The same reasoning already settled the admin panel the other way. The dashboard reaches
auth-service *through* the gateway, which is the API-gateway-as-single-entry-point pattern and
the reason auth-service's admin API is not public. Token issuance is the one path that escaped
it.

**Decided — the gateway proxies, it does not issue**: one issuer, one signing key, and no
second copy of the client/scope/TTL rules to drift out of step with the first. Issuing would
have put bcrypt and RS256 signing on the same process that serves inference. The precedent was
already in the repo: `/admin/api/auth/login` proxies to auth-service for the dashboard.

**Shape**: `POST /oauth2/token` on the gateway — the same path as upstream, so an SDK changes
only the host. The body is forwarded verbatim and the response returned verbatim, **error
bodies included**: an OAuth2 client parses `{"error": "invalid_client"}` (RFC 6749 §5.2), and
translating that into problem+json would make this a worse token endpoint than the one it
replaces. The dashboard's login normalizes instead, because its caller is our own SPA. The one
thing the gateway answers for itself is `503` when it cannot reach auth-service at all, or is
not configured with a token URL — those are the gateway's failures, not OAuth2 outcomes.

Exempt from both the Bearer check (demanding a token to obtain one is circular) and the rate
limiter (which keys on claims this request has not produced yet — auth-service applies its own
limits to issuance, which is where they belong).

**What this does not do**: closing the old door is deployment configuration, not code. PRM-96
gives clients a path that needs one host; making auth-service unreachable from outside is an
operational change, and it needs notice to Axonium first, since their SDKs point at two hosts
today and both must keep working through the transition.

## PRM-107 — the model file contradicts a wrong modality (done)

**Why**: PRM-106's root cause was a registration, not a bug. A reranker was registered as
`text` — the default — started without `--reranking`, and answered chat requests with plausible
nonsense until a client reported three problems that were one. Nothing failed, because `text` is
the only modality that never errors. That is precisely what makes it unsafe as a silent default.

**Measured first, because a heuristic that reads a field still has to be checked against the
corpus.** Across this deployment's 28 readable catalogue files: 24 agree with what was
registered, and all 4 that disagree are files that assert nothing — two vision, two image. Zero
cases where the file asserted something wrong.

**Shape — and the asymmetry is the design**:

| the file... | conclusion |
|---|---|
| carries `<arch>.classifier.output_labels` | it is a reranker — refuse anything else |
| carries `<arch>.pooling_type`, no classifier | it produces embeddings — refuse `text` |
| carries neither | unknown; accept what the caller declared |

Silence is never evidence. A vision model's projector is a separate file and image models are
served by another engine entirely, so a file that says nothing could legitimately be any of
three modalities — blocking on silence would have rejected 4 of our own 28. Applied at both
registration paths and at the update path, which is the one that exists to *correct* a modality.

**Verified live** with the exact original mistake: registering the reranker as `text` is now
refused with a message naming the flag to use; registering it as `rerank` succeeds.

## PRM-114 — the rename field asks the server instead of guessing (done)

**Why**: spotted from the screen — "gpt-oss-20b-mxfp4 already has billing and a grant, why does
it let me change the slug?". The server did not: it answered `409` naming three clients and 39
billed rows. The *form* offered the field, because PRM-113 changed the rule on the server and
left the browser using the old one:

```
const unnamed = (model.slug || model.id) === model.id;   // "was it ever set"
```

`gpt-oss-20b-mxfp4` has `slug == id`, so the browser called it unnamed and enabled the input —
promising something the server would refuse on save.

**Shape**: the real question is whether anything depends on the name, and the answer needs
grants (auth-service), usage rows and pricing — none of which a browser can see. So the catalog
listing computes it, once for the whole list, and returns `rename_blockers` per model. Empty
means editable; otherwise the field is disabled and says what is in the way. A lookup that fails
leaves the field off rather than assuming it is safe.

## PRM-113 — billing keys on an id that cannot change (done)

**Why**: asked directly — "renaming a model, doesn't that affect billing too?" — and it did, in
two ways, both visible in this deployment's own data.

`usage_events.model_id` held the model's **public name**, which RM-70 lets an operator set once.
So naming a model:

1. **split its billing history in two.** `qwen3-0.6b` (85 rows) sat beside
   `qwen3-0-6b-iq4-nl-local-2` (37) — one model, two piles, and any per-model report counted
   them separately.
2. **lost it its price.** The same string is the pricing.yaml lookup key. Verified directly
   rather than inferred: a table keyed on the old name returns `0.621` for it and `None` for the
   new one. From that point the model billed `cost_usd = NULL` with nothing erroring — the exact
   silent-zero RM-60 set out to prevent.

**Shape**:
- Rows key on the **catalog id**, which never changes, and carry `model_slug` — the name in
  force when the row was written, because an invoice should say what the thing was called then.
- Prices resolve under **either** name, so an operator's existing pricing.yaml keeps working
  whichever identifier it was written against.
- `model_slug` appended to the CSV export by the append-only rule. `model_id` **changed meaning**,
  which is a first — called out explicitly in the guide rather than left to a diff.
- A rename is now refused only when something depends on the old name: a `model:<slug>` grant, a
  usage row, or a configured price. That check lives in the gateway, the only process that can
  see all three; the manager keeps what it alone can enforce, that a slug is never handed to
  another model. An unreachable auth-service **blocks** rather than waving through.
- `scripts/reunify_usage_history.py` reunites histories a past rename already split. Deliberately
  a separate, dry-run-by-default step: the slug→id mapping lives in the manager's registry, not
  the gateway's database, and rewriting billing history should be something an operator chooses.

## PRM-112 — the public name is set on the model, and the table says which name is which (done)

**Two things, both noticed from the screen.**

**The slug was edited from the instance form** — the same wrong place PRM-109 found modality in,
for the same reason: it names the *model*, and every instance of it answers to that one string.
Moved to the model's pencil. RM-70's rule is untouched and still enforced by `set_slug()`:
nameable once while it is still the id the migration backfilled, frozen after, because clients
route on it and `model:<slug>` grants key off it. A frozen slug now returns `409 slug-frozen`
rather than a generic failure.

**The subtitle under each model name was unreadable** — it rendered the slug only when it
differed from the display name, and never said what it was. So it appeared on some rows and not
others, and the one question it exists to answer ("which of these strings do I put in `model`?")
had no reliable answer. Now always rendered, always labelled `model:`.

**Measured while verifying**, and worth knowing before renaming anything in production: naming a
slug does not break existing clients, but it does leave their grants behind. A client holding
`model:<old-name>` keeps working **with the old name** — RM-70 keeps it resolvable as an alias —
and gets `403` on the new one until its grant is reissued.

## PRM-111 — the model's display name is editable, and finally visible (done)

**Why**: PRM-110 accepted `name` in the catalog PATCH and then never surfaced it. Spotted by
asking the obvious question — "I don't see where to edit the model's name" — and the answer was
that there was nowhere, and nowhere to see it either.

**Three identifiers, and the table was showing the one that cannot change**:

| field | what it is | changeable |
|---|---|---|
| `id` | the registry key | no |
| `slug` | what clients put in `model` — tokens and grants key off it | once (RM-70) |
| `name` | display label, nothing keys off it | freely |

The Models table rendered `id`. Measured: all 31 catalog rows have `name` populated, and one of
them already read "Qwen3 0.6B Instruct" — a name someone had set that appeared nowhere in the
product.

**Shape**: the table shows `name` with the routing slug underneath when they differ, because the
slug is what a client actually sends and it was invisible too. The pencil edits the name; the id
and slug are shown read-only beside it so it is obvious which one renaming does *not* touch.
Blank is refused, same as family.

**Verified live**: renamed the reranker to "Qwen3 Reranker 0.6B", confirmed its slug did not
move, and confirmed a client calling by that slug still scores.

## PRM-110 — family and name editable, and the button says what it does (done)

Two observations from using the dashboard, and the second corrected a mistake in PRM-109.

**The button.** "Register model" on the Instances page sends `model_id` — RM-51's "create an
instance of this already-catalogued model". It never registered a model; downloading one is what
puts it in the catalog. Renamed to "Register instance".

That also invalidates the exception PRM-109 left: modality stayed editable "while registering,
because that call creates the model too". It does not. So there was always a catalog entry to
inherit from, and the dropdown could only ever be used to disagree with it. Modality is now
read-only in both modes of that form, `add_instance()` no longer takes it as a parameter, and
manager-api stops passing one — the catalog entry answers.

**Family.** Joins modality and name as editable on the model. The argument was already written
in RM-89: the fallback is the GGUF's `general.architecture`, "a true fact about the file but not
always the lineage a human would name — phi4-mini is architecture `phi3`, minicpm5 is `llama`".
Nothing keys off family, people read it, and until now correcting one meant the instance PATCH —
once per replica. Blank is refused, for RM-89's reason.

**Found live while testing**: the reranker's family was `unknown`, which is the same blank box an
SDK team asked about four rounds running. Fixed through the new path.

## PRM-109 — modality belongs to the model, and is edited there (done)

**Why**: noticed from the UI — the modality dropdown sat on the Instances page, when modality
describes the weights. Checking it found the UI was not the problem, it was the symptom.

The column exists on **both** tables. RM-70 put it on `models` with a comment saying that made
"replicas disagreeing about it impossible" — but the `RegistryEntry` handed to the gateway is
built with `modality=inst_raw["modality"]` while every other property of the weights around it
(`path`, `family`, `quantization`, `mmproj_path`, `slug`) comes from the catalog. Modality was
the only one on the wrong side, so the guarantee never held: two replicas of one model could
route differently, and correcting PRM-106's reranker meant updating two tables by hand.

**Shape**:
- The entry the gateway receives reads `catalog.modality`. One source of truth where it is
  actually read.
- `PATCH /v1/backends/{id}` **refuses** `modality` rather than ignoring it, and the message names
  the model to patch instead — a silently dropped field is how you think you changed something.
- New `PATCH /v1/models/{id}` for the catalog (name and modality only), proxied by the gateway at
  `/admin/api/nodes/{node}/catalog/{id}`. PRM-107's file veto applies here too: moving the control
  does not make a wrong answer correct.
- Dashboard: a Modality column and a pencil on the Models page; read-only on the instance form,
  with a hint pointing at Models. Still editable while *registering*, because that call creates
  the model too — there is nothing to inherit from yet.

**Not done**: `instances.modality` still exists and is now ignored on read. Dropping it means
recreating the table in SQLite, which is not worth it for a column nothing reads.

## PRM-108 — modality is chosen in the dashboard, not guessed (done)

**Why**: PRM-107 stops a registration the file can contradict, but two gaps stayed open. The
dashboard's modality list did not contain `rerank` at all — a reranker could not be registered
correctly from the UI, only from the CLI. And selecting a downloaded file deliberately did *not*
carry its modality across, on the reasoning (written in the code) that modality was "a
per-instance choice, not derived from the downloaded file". That reasoning is what this whole
sequence disproved.

**Shape**:
- `rerank` added to the `Modality` type and to the dashboard's list.
- `add_catalog()` derives modality from the file when the file declares one — the same place and
  the same argument RM-89 used for `family`: a default that cannot be told apart from a choice
  gets filled in once, centrally, so a third call site cannot reintroduce it. Unlike `family`
  the file wins over the caller here, because modality is a property of the weights.
- Selecting a downloaded model in the register modal now carries its modality into the form,
  still editable — for vision and image the file says nothing, so their `text` is a default
  rather than an answer.

**The two paths differ on purpose**: cataloguing a download *corrects* silently (nobody chose
anything), while registering an instance with an explicitly wrong modality is *refused*
(someone did choose, and correcting it quietly would hide the mistake instead of teaching it).

## PRM-106 — rerankers get the endpoint they need (done)

**Why**: a project asked for reranker models. The instance was registered and a client tried to
use it, then filed three bugs: (1) the gateway discards `logprobs`, so they could only get a
binary yes/no instead of `P(yes)/(P(yes)+P(no))`; (2) requests "hang" 30-60s sporadically under
concurrency; (3) the chat template needs manual prefill or the model returns junk.

**They were one problem.** A reranker is a cross-encoder, and it was being served as a text
generator. Measured before changing anything:

- `llama-server` **does** implement `/rerank` — it answers `501 "Start it with --reranking"`.
  The instance had been started without the flag, so the only way in was chat completions.
- The "hangs" are not hangs. A 429's `Retry-After` is seconds-until-window-reset, so it ranges
  0-60s; their SDK honours it, waits, retries, and the caller sees *one slow request* among fast
  ones. Their own ladder totals 57 requests against a 60 RPM limit, which is why it looked like
  no clean load threshold.
- The template and the score both come free from the native endpoint.

**Shape**: `modality: "rerank"` in the manager (adds `--reranking`), and `POST /v1/rerank` on the
gateway — the de-facto Cohere/Jina shape that llama.cpp already implements: one query, N
documents, `{index, relevance_score}` back, ordered. Scope, idempotency, circuit breaker,
spend cap, usage recording (`request_kind="rerank"`, prompt-only — a reranker generates nothing)
all follow the `/v1/embeddings` precedent. A rerank model is refused by chat completions (RM-66
already did this) and a chat model is refused here.

**The rate-limit half fixes itself**: scoring 50 candidates was 50 requests against a 60 RPM
budget and is now 1.

**Verified live** end to end, with the backend started by the manager rather than by hand:
a four-document query ranked the relevant document at 0.992 and "Lima is the capital of Peru"
at 0.00006.

## PRM-105 — the model pair can differ, and first-token stops being a reasoning artefact (done)

Both found while producing the traffic Argus asked for in A-21 — neither would have shown up in
the data itself, which is the point worth keeping.

**The model pair**: `gen_ai.request.model` and `gen_ai.response.model` both came from
`resolution.model_key`, so they could never differ. Argus uses that pair to spot "asked for one
model, served another", which they say explains half their incidents; they would have watched 30
minutes, seen no difference, and concluded it does not happen here. Measured by asking for an
RM-70 alias: the HTTP body reported the resolved name and the span reported it on both halves.
`request.model` is now what the caller sent. Span name follows the convention,
`{operation} {request.model}` — Argus explicitly asked us not to trade the standard for their
cardinality, which they solved by aggregating their RED metrics on `response.model` instead.

**First-token latency**: `ttft_ms` is set by the first *visible* token and deliberately stays
that way — it has history behind it. But a reasoning model streams `reasoning_content` first:
`qwen3-0.6b` sent 30 of those before one `content` chunk, so `ttft_ms` appeared on 13 of 247
spans, and the ones that had it were the long `gpt-oss` answers. The sample was selected by
model and by length at once, so a p99 over it would have been wrong in a specific direction, not
merely noisy. Added `argus.inference.first_token_ms` — first token of any kind — which Argus
named and now feeds their latency histogram, keeping `ttft_ms` for experience dashboards.

## PRM-104 — one CLIENT span per model call, streamed or not (done)

**Why**: Argus (A-21) reported that `inference.request` was `Internal` where the convention asks
for `Client`. Checking it found something they could not see from outside: the GenAI attributes
went onto that INTERNAL span for a non-streaming answer, but a streamed one outlives that span
and already carried its own `CLIENT` span named `chat <model>`. So **the same data arrived under
two span kinds and two names, chosen by whether the caller asked for a stream** — their
client-side RED metrics saw half the traffic, and which half was the client's decision.

**Shape**: both paths emit one `CLIENT` span named `chat <model>` carrying the request and
response halves. On the non-streaming path it is started and ended at the backend call's real
bounds rather than wrapping the block, because the tokens it reports are only known after the
response is parsed. `inference.request` stays `INTERNAL` and keeps only its own attributes —
it describes the gateway's work, which is what it is.

**Verified** live against a real OTLP sink: one request of each kind now produces the same
`CLIENT chat qwen3-0.6b`. A test asserts both paths and compares them; asserting either one
alone could never have caught this.

## PRM-103 — the server span carries HTTP attributes (done)

**Why**: Argus (A-14, then A-19) measured our server spans and found them named `http.get`
with **no attributes at all**. `TraceIDMiddleware` opened them by hand, and by hand meant
nothing but a name. For `auth-service` and `manager-api`, which have no other instrumentation,
that meant Argus knew how many requests arrived and nothing else — no `http.route`, no status
code, no latency per endpoint, so no RED metrics and no per-endpoint SLO. It also meant the
`OTEL_SEMCONV_STABILITY_OPT_IN=http/dup` we set for A-11 had nothing to act on in two of three
services.

**Shape**: `instrument_fastapi()` in the shared telemetry package hands the SERVER span to
`opentelemetry-instrumentation-fastapi`, called last in each app so it wraps every middleware
and the routes are registered for `http.route` to resolve. `TraceIDMiddleware` stops opening a
span when that instrumentation is active and keeps only its own job: read the id from the span
that exists, bind it to the log context, return `X-Trace-ID`. Health and metrics stay excluded
— probe traffic was 57% of everything we sent Argus (RM-95).

**The guarantee that had to survive**: the ASGI instrumentation adopts a caller's `traceparent`
through the global propagator, which is exactly what AC-11 forbids. `instrument_fastapi()`
installs a propagator that extracts nothing and injects nothing — the `never` policy Argus
asked us to leave untouched, now enforced by configuration rather than by not having the
feature. Measured live with a forged `traceparent`: not adopted.

**Measured**: 0 attributes before, 23 after, with `http.route` templated
(`/v1/usage/{request_id}`, not the concrete id) so cardinality stays bounded — captured off the
wire from all three services with a real OTLP sink, not from a unit test.

**Not done, deliberately** — the `traceparent` change of A-06: Argus measured it and
recommended against it themselves. Our inference never crosses service boundaries, and the one
serious cross-service failure they had (A-18) was a `ConnectError`, so there was no server span
on the far side to join.

## PRM-102 — auth-service stops being published (done)

**Why**: [[PRM-96]] moved token issuance to the gateway, which left exactly one auth-service
surface an outsider still had to reach: `GET /share/<token>`, the one-time credential page.
Creating and revoking a share link already went through the gateway; opening one did not, so
the service still needed a published port for a single page.

Measured before building anything, and it was worse than "an exposure": auth-service builds
the link from `request.base_url` of the request that asked for it, and that request arrives
**from the gateway**. So the URL the dashboard showed an operator was `http://auth-service:9000/share/...`
— an internal hostname the recipient could not resolve. The link was already broken for anyone
not on the host. Closing the port did not break it; it made it visible.

**Shape**:
- `GET /share/{token}` on the gateway, proxying the page with its `no-store` / `noindex` /
  `no-referrer` headers intact — those headers are the point of a response that renders a
  secret in a browser, so they pass through rather than being rebuilt.
- The gateway rewrites `share_url` in the create-link response to its own base URL. It is the
  only party that knows its public address; auth-service should not have to know who is in
  front of it.
- The real visitor's IP and User-Agent are forwarded, because auth-service stamps
  `used_by_ip`/`used_by_ua` on the row — the record of who read a secret. Proxying without
  this would have recorded the gateway on every read and quietly emptied the audit trail.
  `X-Forwarded-For` is **overwritten**, never appended to, so a visitor cannot choose what the
  log says; trusting it is only safe because the service is no longer published.
- `podman-compose.yml`: auth-service goes from `ports:` to `expose:`, the same shape redis
  already had. `AUTH_BIND_HOST` is gone — it was a documented escape hatch justified by
  "dashboard access from another host", which stopped being true some time ago.
- The operator paths that reached port 9000 move inside the container (`podman exec`), and
  `validate.sh`'s auth health check now goes through the gateway's token endpoint — a better
  probe, because it exercises the path clients actually use.

**Not included**: `validations/*.py` are one-shot development scripts whose admin steps now
need `podman exec`. They carry a header saying so rather than being rewritten — they also
reference models that no longer exist, which is a separate cleanup.

## RM-97 — blocking query instead of a 30s poll for the catalog (done)

**Why**: the gateway asked the manager for the whole catalog every 30 seconds. The cost was never
the bandwidth — one node, 7 KB a cycle, 2880 cycles a day for a catalog that changes a few times
a week. The cost was the **staleness window**: up to half a minute routing to a backend that had
gone, or not routing to one that had arrived. There is direct evidence it hurt — [[RM-69]] added
active health probing precisely because "the manager registry poll runs every 30s, so an instance
that dies between polls keeps being offered". We had built a second mechanism to cover for the
first one's latency.

**Shape**: Consul's blocking query. `GET /v1/backends?index=<held>&wait=<seconds>` is held until
the registry stops matching that index, or the wait expires; the current index comes back in
`X-Registry-Index`. The gateway hands back what it holds and goes straight round again when
something changed.

**Why anchored on an index rather than streaming events** (SSE or WebSocket), which is the
obvious alternative and is what Envoy's xDS does: a dropped connection cannot lose anything here.
The caller asks again with the index it still holds, so reconnection is self-healing instead of
needing `Last-Event-ID` and replay. Kubernetes' watch is streaming and still anchors on
`resourceVersion`, returning `410 Gone` and forcing a re-list when a client falls behind —
evidence that streaming needs a state fallback anyway. WebSockets were rejected outright:
bidirectional machinery, proxy configuration and heartbeats for a one-directional feed, plus a
connection that outlives the token that authorised it. With one gateway and one node, none of
that pays.

**What the index deliberately excludes**: live process state. Process metrics change on every
read, so a fingerprint including them would never hold still and the blocking query would become
a busy loop. Liveness is not the registry's to report either — a process that dies without
deregistering is the gateway's health probing to catch, because a dead process cannot announce
itself. Declared state is watched; liveness is probed.

**Degrades rather than breaks**: a manager predating this answers immediately and without the
header, and the gateway notices, stops asking for a block, and goes back to sleeping the poll
interval.

**Measured end to end**, against the running stack: a registry change reached the gateway in
**1.2 seconds**, against up to 30 before. The held request itself releases about a second after
the change, and returns immediately when the index already differs.

**A regression this introduced, found by asking what happens when the manager is down**: the
loop skipped its sleep unless the previous cycle had *returned early*, and a failed request never
updated that flag — so an unreachable manager became a busy loop, measured at **1455 sync cycles
in ten seconds and 85% of a core**. An outage is exactly when a gateway must not spin. The test
is now the other way round: sleep unless the manager actually held the request, which a failure
by definition did not. Verified with the manager stopped — 0 cycles in 15 seconds, 0.0% CPU —
and recovery within one poll interval once it came back.

**Separately, and not caused by this**: a gateway that cannot reach the manager replaces its
catalog with nothing and serves zero models, rather than continuing on what it last knew. See
[[RM-98]].

## RM-98 — a manager outage empties the gateway's routing table (done)

**Why**: found by asking a simple question — what happens if the manager is not there? Measured:
the gateway goes to **zero models** and serves nothing until the manager returns.

The cause is one line of intent that reads reasonably and is wrong at this scale.
`_fetch_node_backends` returns `[]` on failure, commented "one down node must not block the
others — partial availability, not all-or-nothing". With several nodes that is right. With one,
`[]` *is* all-or-nothing, and the whole catalog disappears.

This matters more than it looks. The gateway keeps a copy of the manager's registry precisely so
the control plane is not in the data plane's critical path: the manager is where humans register
models, and inference should not stop because it is being restarted. Today the copy is discarded
the moment it cannot be refreshed, so that benefit is not actually delivered.

**What the industry does**, converging from three independent directions: Envoy keeps its last
known good configuration when the control plane is unreachable and documents it as a feature;
AWS calls it static stability — the data plane keeps working through a control-plane impairment;
RFC 8767 has DNS serve stale answers rather than fail, with refresh attempts rate-limited. All
three say the same thing: the data plane does not fall when the control plane does.

**Chosen: fail static, indefinitely, and visibly.** The alternative worth taking seriously was a
bounded grace period, RFC 8767's shape. Rejected because a TTL puts back on a timer exactly the
coupling static stability exists to break: a manager down for twenty minutes with a fifteen
minute grace still produces an outage, just later and while the operator is already busy. DNS
bounds it because a stale record can point at an address that now belongs to someone else — our
catalog points at backends we operate and whose liveness we verify ourselves every ten seconds,
so the risk that justifies the bound is not present here.

Indefinite but **not silent**, which is the part that separates graceful degradation from being
quietly broken: `manager_sync.serving_stale` says which nodes and for how long, and
`stale_cleared` says when it ended. "It still works" is precisely what stops anyone looking.

**The bug underneath was a lost distinction**: `_fetch_node_backends` returned `[]` both when a
node failed and when it genuinely served nothing, so the caller could not tell them apart and
keeping the last known state was impossible. It returns `None` on failure now. A node that
answers with an empty list still empties — an empty answer is an answer, and retiring the last
model on a node has to work.

**Verified live**: with the manager stopped, the gateway kept serving its 7 models and completed
a real inference — HTTP 200, 10/12 tokens — where it previously returned 404 on everything. The
stale warning appeared while it lasted and cleared when the manager returned.

## RM-99 — the last catalog survives a gateway restart (done)

**Why**: [[RM-98]] made a running gateway survive a manager outage by keeping its copy in memory,
and that left the worst case untouched. Measured: with the manager down, restarting the gateway
put it back to **zero models**. A deploy is precisely when someone is already touching the
infrastructure, so "the manager is down *and* the gateway restarts" is not the unlikely
coincidence it sounds like. Envoy has the same property — last known good lives in memory and a
restart falls back to bootstrap — but our gateway restarts on every deploy, so it bites harder.

**Scope, and the constraint that shaped it**: the snapshot is read in exactly one situation, a
start whose first sync produced nothing, and never again. The manager is the source of truth;
this is only what to do when it cannot be asked. A successful sync rebuilds the catalog from
scratch rather than merging, so anything the manager no longer serves disappears — verified by
hiding a model while the manager was down and watching the gateway drop it on reconnect.

Stored as what the manager *said*, not as what the gateway made of it, so a restore runs through
the same parsing as a live sync instead of a second path that can drift. Written only when the
content changed: with a blocking query a sync also runs each time the wait expires, and rewriting
an identical snapshot every minute would be churn for nothing. An outage never overwrites a good
snapshot with the emptiness it caused, because only nodes that answered are written.

**No expiry, and the age is logged instead.** `restored_from_snapshot` carries how old the copy
is, loudly, because the gateway is announcing that it is routing on something nobody has
confirmed. A three-week-old snapshot is a different judgement from an hour-old one, and the
operator can only make it with the number in front of them — while what the snapshot claims is
alive is verified independently within seconds by health probing either way.

**Verified live**, end to end: with the manager stopped and the gateway restarted, it came up with
its 7 models and served a real inference (HTTP 200, 82 ms) where it previously answered 404 on
everything; then, with a model hidden in the registry during the outage, the manager returning
took the catalog from 7 to 6 and dropped it.

**Also fixed here**: the flaky streaming test from [[RM-87]] was still failing about one run in
four. Draining detached tasks after each test left a window open — the engine is a module global,
so a straggler still awaiting a session writes into whichever database is current when it wakes,
which is the *next* test's. It drains before as well now; six consecutive clean runs.

## PRM-100 — a client can read the usage row for its own request (in-progress)

**Why**: [[RM-88]] added `termination_reason` so that a charge for a half-delivered answer could
be explained, and the answers document sent to Axonium says in as many words that it gives a
client disputing a charge something to point at. Both usage endpoints require `admin:read`. The
only party who can look is the one who does not need to — the promise is half kept, and Axonium
put that in writing before we noticed it ourselves.

**What actually blocks it, which is not the endpoint**: `usage_events` has no `request_id`
column. The gateway returns `x-request-id` on every response and never writes it to the billing
record, so today not even an administrator can go from a request identifier to its row. The link
the request needs does not exist on our side.

**Scope**, taking Axonium's design, which is better than the obvious one:
- `GET /v1/usage/{request_id}` returning that row only — tokens, model, `interrupted`,
  `termination_reason`, cost, timestamp. Not the request, not its content or parameters.
- Filtered by the `client_id` in the token; **404 rather than 403** for a request belonging to
  someone else, so the response does not confirm that it exists.
- No admin scope, no aggregates. Granting `admin:read` to a client so it can see its own row
  would let it see everyone's.
- Needs the `request_id` stored first, with a migration, and threaded through `_record_usage`
  from the four call sites that record usage.

**Explicitly not closing this as "won't do"**: the alternative Axonium offered — documenting that
`termination_reason` is an operational field rather than something a caller can query — is worse.
It would put in writing that we bill with a reason the payer cannot see.

**Two corrections from Axonium's next round, both raising the cost and both right.**

*Tokens have to come back broken down, not aggregated.* An aggregate cannot be reconciled against
the response once caching is involved, and reconciling is the only thing the endpoint is for.
Which surfaced something they could not have known: the usage row **does not store cached prompt
tokens at all**. The gateway reports `prompt_tokens_details.cached_tokens` and writes none of it,
so reconciliation would not fail occasionally — it would be impossible whenever the cache was
used. Another column in the same migration. (`instance_id` they also asked for is already stored,
and free.)

*The `404` breaks the case the endpoint exists for*, which they caught in their own design before
we built it. A replay carries its **own** request id, and by our own rule a replay records no
usage — so `GET /v1/usage/{replay_id}` would return `404`, and that id is the only one its caller
holds. A bare `404` then means three different things, one of which is ordinary correct
behaviour. Verified against the deployment: two ids, one row.

Answer, going one step past their proposal:
- **`X-Idempotent-Replay-Of` on the replay response**, so the link exists at the moment it is
  known and needs no storage or lookup. Cheap and can ship ahead of the rest.
- **A replay writes its own usage row** — zero tokens, zero cost, `replay_of` pointing at the
  original. Preferred over a side table because it keeps one place to look and turns "no row" into
  an explicit statement rather than an absence to interpret, which is precisely their objection.
  Note `_record_usage` currently returns early on zero tokens, so this needs a deliberate
  exception, and zero-token rows will appear in the CSV export.
- **No fourth `termination_reason`.** The first draft used `"replay"`, and Axonium asked what
  `interrupted` would then be — `true`, under the existing derivation, which is false: nothing was
  interrupted because nothing was generated. Answering showed the value was in the wrong column
  entirely. `termination_reason` says *how a generation ended*; a replay is a different **billing
  relationship** to a generation that already ended. So the replay row carries
  `termination_reason: "complete"` — accurate, because only complete responses are ever stored for
  replay: an error hands its key back, and a stream that ended in an error frame is released rather
  than stored. Three values, and the `interrupted` derivation is untouched.
- **`replay_of` becomes the discriminator, and that has to reach the CSV export.** Axonium spotted
  the consequence: with no fourth value, replay rows land in the `complete` bucket, so counting
  generations with `WHERE termination_reason = 'complete'` over-counts — silently, because the
  number comes out plausible and nothing fails. `replay_of IS NULL` is the test instead.
  They asked for a line in the guide; the export needs more than that. Its column list is
  explicit, so adding replay rows without adding `replay_of` to it would produce a file containing
  rows the reader cannot filter out — worse than the ambiguity it replaced, because a doc can tell
  you what to filter by only if the field is there. Both ship together or neither does.

They asked whether this changes the cost enough to reconsider. It does raise it, and the answer
is still yes: an endpoint that returns `404` for the ordinary case is not cheaper, it is unusable.

### PRM-100 — what shipped, and what is still waiting

**Shipped**: `request_id` and `cached_prompt_tokens` on `usage_events`, threaded through all four
paths that record usage; `request_id` on the idempotency record;
`GET /v1/usage/{request_id}` reading exactly one row filtered by the token's client id, with
`404` for anything else; `X-Idempotent-Replay-Of` on replay responses; the endpoint documented in
the integration guide.

Two details worth keeping: the route had to be declared **after** `/v1/usage/export`, because
FastAPI matches in declaration order and a parameterised path registered first swallows `export`
as a request id — caught by that endpoint's own tests. And the cached figure comes from
`prompt_tokens_details.cached_tokens` on the non-streaming path and llama.cpp's `timings.cache_n`
on the streaming one, which are the same quantity, so a row means the same thing either way.

**Verified live**: an inference, then its own row read back with the cached tokens present; a
replay whose `X-Idempotent-Replay-Of` resolves to the billed row; and a second client getting
`404` for the first client's request, indistinguishable from `404` for one that never existed.

**Then A-13, from the same round**: the export's shape was a commitment made in correspondence —
new columns appended, existing ones never moved — and documented nowhere. Axonium noticed while
accepting P-09, and the timing mattered: PRM-100 was about to add columns to a file whose format
was unwritten. The guide now carries the column list and the rule, `request_id` and
`cached_prompt_tokens` are appended to the export where the row already stored them, and a test
compares the documented list against the code so the two cannot drift. That test earned itself
immediately — an assertion reading `header[-2]` broke when two columns were appended, which is
precisely the failure the rule exists to prevent, in our own suite.

**Still waiting on Aeon**: the replay *rows* — zero tokens, `replay_of` — and with them
`replay_of` in the CSV export and the counting caveat in the guide. Axonium confirmed the export
change does not affect them but explicitly declined to answer for Aeon, who do impute cost from
row counts. Not built until they answer; the header above already covers the case a caller hits
today.

Append a new row to the table with the next `RM-NN` id and a new `## RM-NN — ...` section
below, following the same shape (Why / Scope). Re-sort the table if the new item's
priority isn't "last."
