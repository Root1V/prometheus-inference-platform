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
  this project; RM-17 was speculative and still is. **RM-18 is no longer speculative** — see
  below, it was merged into RM-11 once a concrete requirement showed up.
- **RM-19 to RM-23** came out of a 2026-08-26 requirements pass and are grouped by concern:
  - **Auth & Users** (RM-11, expanded): dashboard user/role management with two login modes.
  - **Nodes & instance provisioning** (RM-20, RM-21): a real node registry, and instance
    creation that reads from it instead of manual field entry. RM-21 depends on RM-20.
  - **Dashboard identity & orientation** (RM-19, RM-22): branding (logo/favicon) and a home
    page — pure UX, no new backend data model.
  - **Live platform visibility** (RM-23): who/what is connected right now, distinct from
    RM-15's historical usage aggregates.

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

## RM-13 — Admin dashboard: live log viewer (added)

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

## RM-14 — Model playground (added)

**Why**: every comparable platform researched (LiteLLM Proxy, Portkey, Helicone) ships an
in-dashboard playground — a way to send a test prompt to a running model and see the
response without curl/Postman. Prometheus has none today; you have to hit the gateway's
inference API directly to sanity-check a model you just started.

**Scope (not yet designed)**: a dashboard page where you pick a running instance and send
a request through the gateway's existing `/v1/chat/completions` (or `/v1/embeddings` for
embedding models) using your own admin session — showing the raw request/response, ideally
with streaming. Should reuse the gateway's existing inference API as-is; no new backend
inference surface, just a UI in front of what already exists.

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

## RM-16 — Routing & rate-limit visibility (added)

**Why**: the gateway already enforces rate limiting and circuit breakers (spec 007), but
that state is invisible today outside reading `.env` files or logs. Comparable platforms
expose current rate-limit/circuit-breaker state and routing rules directly in their
dashboards.

**Scope (not yet designed)**: start read-only — show current limits and per-backend
circuit-breaker state in the dashboard. Whether the dashboard should also let you *change*
that config live (vs. `.env` staying the single source of truth) is a bigger, separate
question — don't scope that in without deciding it deliberately, to avoid ending up with
two conflicting config sources.

## RM-17 — Guardrails / content filtering (added, speculative)

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
credential piece (originally bundled here) is split out to RM-25 — it's a materially
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

## RM-25 — Node SSH/remote-maintenance credentials (added, speculative)

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

## RM-23 — Active sessions / connected users (added)

**Why**: no visibility today into who or what is actively using the platform right now —
operators logged into the dashboard web UI, end users chatting via a model's own UI, API
callers, and (future) SDK users. RM-15 covers historical/aggregate usage; this is about
*live* connections instead.

**Scope (not yet designed)**: a page listing active sessions/connections, showing per
entry: which model is being used, connection type (dashboard web / model UI chat / API /
SDK), and how long it's been connected. Needs a session-tracking mechanism spanning
gateway (API calls), auth-service (dashboard login sessions), and any model-facing chat UI
— this is the least-designed item in this batch; where "active" state actually gets
recorded needs its own scoping pass before implementation starts.

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

## RM-26 — Instances list: numbered, paginated, active-first (added)

**Why**: the Instances table on the dashboard just lists rows in whatever order the
gateway returns them, with no row numbering and no cap — as the number of registered
instances grows (across more nodes, more models) the table gets long and running
instances get lost among stopped ones.

**Scope (not yet designed in detail)**: add a leading row-number column; sort running
instances before stopped ones (state order, not a separate boolean toggle); paginate the
table once the instance count passes a threshold (client-side pagination is likely
sufficient — the aggregated list already comes from one `GET /admin/api/instances` call,
no new backend endpoint obviously needed unless the list turns out large enough to want
server-side paging).

## RM-27 — Delete user (added)

**Why**: the Users table only offers deactivate/reactivate (`UserRow.tsx`) — there's no way
to permanently remove a principal from the dashboard. auth-service's `DELETE
/admin/clients/{id}` already supports this (`?permanent=true` hard-deletes the row and
writes a Redis revocation key so any outstanding token is rejected immediately — see
`deactivate_client` in `auth-service/src/prometheus_auth/routers/admin.py`), so most of the
work is frontend, but not all: the gateway's own proxy (`DELETE /admin/api/users/{id}` in
`gateway/src/prometheus_gateway/admin/router.py`) currently calls `_auth_admin_request`
with no query params, silently dropping `permanent` even if the frontend sent it — that
proxy needs to forward the param through.

**Scope (not yet designed in detail)**: forward `permanent` through the gateway's proxy;
add a Delete action in `UserRow.tsx`'s action column (mirrors `NodeRow.tsx`'s
delete-with-`ConfirmDialog` pattern already used for nodes) that calls it with
`?permanent=true`. Needs clear confirmation copy distinguishing it from Deactivate
(irreversible vs. reversible) so an operator doesn't reach for the wrong one.

## RM-31 — Overview: link out to Grafana/Tempo (added)

**Why**: dropped from RM-22's original scope — see that item's "Scope trim" note. The
Overview page memo called for a links row to the existing Grafana ops dashboard and Tempo
trace search, but there's no `GRAFANA_URL`-shaped setting anywhere in the gateway's config
to build a reliable link from, and guessing one client-side (assuming Grafana sits on the
same host at :3000, per `podman-compose.yml`) would be fragile across deployments —
different host, different port, TLS, or no Grafana deployed at all.

**Scope (not yet designed in detail)**: add a `GRAFANA_URL` (or similar) setting to the
gateway's config, exposed to the admin-ui (e.g. via a small unauthenticated `/admin/api/config`
read, or baked into the served `index.html` at container-build/start time — needs picking
one). Render the links row on Overview only when the setting is present; omit it entirely
otherwise rather than showing a dead link.

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

## Adding new items

Append a new row to the table with the next `RM-NN` id and a new `## RM-NN — ...` section
below, following the same shape (Why / Scope). Re-sort the table if the new item's
priority isn't "last."
