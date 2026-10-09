# Prometheus — Roadmap / Backlog

Living backlog of improvements and new features. Unlike `memory/specs/`, items here are
**not** run through the full SDD pipeline (spec-writer → developer → test →
security-reviewer → human-approved → docs → release) — that process is kept for
already-shipped, security-critical work. Backlog items below are implemented directly,
**one branch per item**, to move faster and spend fewer tokens per change.

**The only non-negotiable rule carried over from SDD**: every branch that closes an item
must update `README.md` and the relevant docs under `docs/` in the same PR/commit set —
this file is not a substitute for real docs, it's a queue. (It used to say `memory/wiki/`.
That directory was deleted as unneeded; the rule outlived it by pointing at nothing, which
is the one thing a non-negotiable rule must not do.)

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
test commands), the deployment and model-registry wiki pages (`pmgr serve` → `pmgr-api`;
those pages have since been deleted), `podman-compose.yml` / `podman-compose-ubuntu-dgx.yml`
(Dockerfile path), `scripts/install-rhel.sh` / `scripts/install-ubuntu-dgx.sh` /
`scripts/validate-ubuntu-dgx.sh` (`pmgr serve` → `pmgr-api`), and the
`scripts/tests/test_scripts_024.sh` assertions that checked the old command/path.

## RM-06 — Research the best inference-serving stack (item 7) — `done`

**Why**: `llama-server` is the only backend today. It may not be the best fit for every
hardware target (Apple Silicon vs NVIDIA DGX) or every future modality (RM-09).

**Done**: a full comparison of llama.cpp, vLLM, MLX and SGLang across Mac (M4 Max) /
DGX Spark / generic Linux-NVIDIA,
covering throughput, quantization format support, modality coverage, and operational
complexity for a process-spawning manager. Bottom line: **mixed strategy, not a single
engine** — MLX on Mac, vLLM (or SGLang) on DGX Spark and generic Linux servers, llama.cpp
kept everywhere as the simple/single-user fallback. No engine covers every target modality
on every piece of hardware; the real design axis for RM-08/RM-09 is per-hardware backend
selection, not per-modality. What it added to the manager's job: a second "heavy Python
server" launch shape alongside the current "spawn a binary" one, and new `registry.yaml`
fields (`backend`, `quant_format`).

The comparison itself lived in `memory/wiki/inference-engines.md` and was deleted with the
rest of that directory. **The conclusion above is now the whole of it** — the per-hardware
numbers behind it are gone, which is worth knowing before citing RM-06 as evidence. PRM-133
redoes the part that still matters: which engines are worth offering, and on which node.

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
this shipped has zero model access** until an admin adds `model:<id>` scopes to it — the
grant command lived in the auth-model wiki page, since deleted. Roll this out with that in mind; it will look like a total inference outage for
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
  gateway can actually route to. The operational setup was written up in the model-registry
  wiki page, since deleted; `manager_sync.py`'s own comments are what remains.

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
minus 1 that's schema-only).

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


**Completed 2026-09-27, because the file was never loaded.** The variables were in the right
place and no launch path put them in a process environment: `docs/local-stack.md` gave the two
uvicorn commands without it, under a heading saying the services need no `source`. True of their
own `.env` files, false of telemetry — so anybody following the runbook ran the stack **dark**,
and all four services were. `instrument_fastapi` is a documented no-op when tracing is inactive,
so there was no server span either: no `http.route`, no status per endpoint, and PRM-157's audit
copy reached Argus by no path at all. Only manager-api had ever been seen from their side, which
is why A-32's evidence came from there and nobody noticed the rest.

The runbook's commands now load `runtime/telemetry.env` (and the managers' own identity files
alongside it — `--env-file` repeats). Verified end to end: 32-hex `x-trace-id` on all four
services, and the Argus agent's own counters moved 248 → 276 accepted spans for three requests,
all forwarded to their store.

**And the obvious check is a false negative**, which cost a wrong conclusion here first:
`/health` returns a UUID whether tracing is on or off, because RM-95 suppresses probe spans at
source. The diagnostic has to use a real route.
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

## PRM-100 — a client can read the usage row for its own request

**Why**: [[RM-88]] added `termination_reason` so a charge for a half-delivered answer could be
explained, and the answers document sent to Axonium says in as many words that it gives a client
disputing a charge something to point at. Both usage endpoints required `admin:read`, so the only
party who could look was the one who did not need to — and granting that scope to a client would
let it see everyone's rows.

**What it took**, which was not the endpoint: `usage_events` had no `request_id`, so not even an
administrator could go from the identifier we return on every response to the billing record.
Cached prompt tokens were reported and never stored, which would have made reconciliation
impossible whenever the cache was used rather than merely inaccurate — Axonium found that by asking
for the tokens broken down instead of aggregated, since an aggregate cannot be reconciled against
a response.

**Shipped**: both columns, threaded through all four paths that record usage; `request_id` on the
idempotency record; `GET /v1/usage/{request_id}` returning one row filtered by the token's client
id, with **404 rather than 403** for someone else's request so the response does not confirm it
exists; `X-Idempotent-Replay-Of` on replay responses, so a replay reaches the row it replayed at
the moment the link is known and with no storage or lookup; the endpoint in the integration guide.
PRM-115 corrected which model name it returns.

Two details worth keeping. The route had to be declared **after** `/v1/usage/export`, because
FastAPI matches in declaration order and a parameterised path registered first swallows `export` as
a request id — caught by that endpoint's own tests. And the cached figure comes from
`prompt_tokens_details.cached_tokens` on the non-streaming path and llama.cpp's `timings.cache_n`
on the streaming one, which are the same quantity, so a row means the same thing either way.

**Then A-13, from the same round**: the export's shape was a commitment made in correspondence —
new columns appended, existing ones never moved — and documented nowhere. Axonium noticed while
accepting P-09, and the timing mattered: this item was about to add columns to a file whose format
was unwritten. The guide now carries the column list and the rule, and a test compares the
documented list against the code so the two cannot drift. It earned itself immediately — an
assertion reading `header[-2]` broke when two columns were appended, which is the exact failure the
rule exists to prevent, in our own suite.

**The remainder is PRM-161**: how a replay appears in the ledger rather than only in a header.
Split off because it is a separate question — how a replay is represented in billing — and this
item's own promise did not depend on it.


## PRM-115 — The usage row reports the name the caller used

**Why**: PRM-113 re-keyed usage on the immutable catalog id so a rename could not split a
model's billing history. That was right for the ledger and wrong at one exit: `GET
/v1/usage/{request_id}` returned the catalog id as `model`. Axonium measured it against the
deployment and found the row disagreeing with the response about the same request — and the
value is not resolvable, because `GET /v1/models` advertises the slug and the catalog id is
deliberately unlisted (RM-70: a model picker should not show replicas as models). They export
`RequestUsage.model` in all three SDKs, so the field reached every consumer returning something
with no use.

**Scope**: the endpoint returns `model_slug` — stored per row at write time, so a row keeps the
name in force when it was billed — falling back to the catalog id where the column is null. The
guide's §3.8 needed no change: the fix is what it already documented. The export keeps both
columns and is the surface where the stable id belongs; a caller joining the two now joins on
`model_slug`, which is why a second identifier here would have bought nothing.

**Not fixed, and the real cause**: our stable ids look like names — `qwen3-0-6b-iq4-nl-local-2`
is a model, `qwen3-0-6b-iq4-nl-local-1` an instance of it, `qwen3-0.6b` the public name. Stripe
(`price_1ABC` + nickname) and Docker (digest + tag) avoid this by making the id opaque; ours is
a fossilised old slug, and Axonium misread the `instance_id` in the same payload for exactly
that reason. Making catalog ids opaque is a migration nobody has asked for yet.


## PRM-116 — The guide stops denying the idempotency it documents

**Why**: §6.2 — the section an SDK author reads to decide whether a retry is safe — stated
*«There is no idempotency-key mechanism in this API»*, while §3.8 of the same document described
`X-Idempotent-Replay-Of`. Stale text that survived every revision since RM-78 built the thing it
denies. An SDK following it never sends a key and bills a second generation on every retry.
Axonium found it, and were unharmed only because they build from their own recorded catalog
rather than our guide — which is the part worth worrying about: the guide had been wrong for
weeks and the reason it surfaced is that a reader had stopped trusting it.

**Scope**: §6.2's bullet now says what is actually true. §3.7 documents the request headers,
which appeared nowhere: `Idempotency-Key` (shape, fingerprinting, streaming, the 24h window) and
`X-Prometheus-Instance` (pinning, and that it never falls back). The response-header list gains
the four it was missing. §3.6's example uses the reranker's public slug instead of its catalog
id.

**And the guard, which is the actual fix**: a test compares every `type` suffix the gateway
raises against the guide's §5.2 table, in the same shape as A-13's export-columns guard. It
found **ten** undocumented — the four idempotency refusals Axonium named, plus `unknown-instance`,
`inconsistent-model-group`, `unauthorized`, and the export's three range errors, which nobody had
noticed. The four idempotency ones reach `_problem()` as `outcome.kind` rather than a literal, so
the guard reads them from where they are declared.


## PRM-117 — Rows a bug billed as unpriced get the price that was already in force

**Why**: found by reading the dashboard, not the code — models with real traffic showing `-` in
the total. Two causes, and only one was a defect. Most were simply never priced, where `-` is
correct and deliberate ("no price configured" is not "free"). But 95 rows of
`qwen3-embedding-0-6b-q8-0-local` were billed null while a price for it existed the whole time:
the rows were keyed on the model's public name and `model_price_config` on the catalog id, so
the lookup missed. That is PRM-113's bug, and `reunify_usage_history.py` re-keyed those rows
without ever recomputing what they cost.

**Scope**: `scripts/reprice_unpriced_usage.py`, dry-run by default like the reunify tool. The
part worth having is what it refuses: RM-60 prices a row at the rate in force when it was used
and never re-rates it, so a row older than its model's price is reported and left alone. That
check earned itself on the first run — a `sd-turbo-test` image row predates its own price by
eight hours, so 95 of the 96 candidates were repairable and the 96th was not.

**Two things it deliberately does not do.** It adds to `usage_daily` rather than rebuilding it
from `usage_events`: 41 of this deployment's 72 daily rows predate the events table entirely, so
a rebuild would silently zero the history that only lives in the rollup. And it leaves alone the
5 daily rows whose sub-costs already disagree with their total — a separate pre-existing defect,
not this tool's business; the test asserts the count does not grow.


## PRM-118 — The spend cap finds the price of a model that was renamed

**Why**: found while answering a fair challenge — if billing keys on `model_id` because that id
never changes, why does the price lookup accept the slug at all? The answer is that the key and
the lookup are different things: the reserve runs before a replica is chosen, and a model answers
to two names. But measuring it turned up something worse than the inconsistency being discussed.
`model_price_config` is keyed on the catalog id, and the reserve passed only the public name, so
for every renamed model it resolved no price — `est_cost` was `None`, no reservation was made,
and the cap never engaged. Measured on this deployment: `qwen3-embedding` and `qwen3-vl-8b` were
both uncapped. Nothing failed and nothing was logged.

RM-69 created `ModelResolution.model_key` to be the catalog id for exactly this reason, and its
comment predicted the failure in those words — "free and uncapped under one of its names". RM-70
then put the slug first in that expression and the field stopped being what every comment about
it still said it was.

**Scope**: `model_key` keeps its current meaning (the public name — 40 call sites read it as such,
including scope checks and the `model` a response reports); the catalog id gets its own
`model_catalog_id` rather than a second reinterpretation of one field, which is the mistake
PRM-115 had just finished cleaning up elsewhere. Four reserves and the streaming settle now name
the model both ways, as the four settles already did. `record_usage` resolved `price` by both
names and `cost_usd` by one, two branches apart — fixed with them.

**The actual fix is the test.** The rule is one line long and was still wrong in four places,
because it lived at nine call sites and nowhere else. `test_every_price_lookup_names_the_model_both_ways`
reads the source and fails on a lookup that names the model once. Run against the pre-fix tree it
names all six.


## PRM-119 — The invoice says what it cannot price, instead of saying zero

**Why**: reported from the dashboard with a screenshot — six requests, every detail row showing
`—`, and the period total reading `USD 0.00`. The rows were right: those two models have no
configured price, and RM-60's rule is that "no price" is never "free". The total was wrong.
`sum(row["cost_usd"] or 0.0 for ...)` collapsed six unknowns into a hard zero, so the one figure
a client actually reads was the only one in the system telling them they owed nothing.

**Scope**: `subtotal_usd` is None when nothing in the period could be priced, and `apply_tax` and
`convert_currency` propagate that rather than multiplying an unknown into a fabricated figure in
the client's own currency. The partly-priced period is the more dangerous case — it looks
complete — so the summary, each day and each model carry `unpriced_requests`, and the dashboard
marks a subtotal that does not cover every request. `CapIndicator` stops drawing a cap bar it
cannot compute: an empty bar reads as "0% used", and an unpriced model is never budget-checked at
all.

**And the column a person actually reads**: the detail table showed `model_id`, the catalog id.
The export's own comment beside `model_slug` says it is "what an invoice should show"; the
invoice was not showing it, because the CSV parser read columns by position and stopped at index
13, so a column appended later was invisible. It reads by header name now — A-13's appended-column
contract keeps position-readers working, but it is exactly why position-reading misses anything
new.


## PRM-120 — Every catalogued model starts with a price

**Why**: PRM-119 made the invoice admit what it could not price, which immediately showed how
much that was: `USD 0.0051 + 79 unpriced`. An unpriced model is worse than a gap in a report — it
records `cost_usd = NULL` and RM-60 never budget-checks it, so it is unbilled *and* uncapped.

**Scope**: a flat base price per modality, applied to any catalogued model that has no price row,
on every catalog sync (models are created in the manager; prices live in the gateway's database,
and doing it on sync makes it self-healing for everything catalogued before this). Stored with
`is_default = true` and surfaced as `source: "default"`, because RM-89's rule applies hardest to
money: a default indistinguishable from a choice is a bug, and here the difference is the answer
to why a client was charged what they were. Saving any price through the admin PUT clears the
flag — pressing Save on the base figure unchanged still means someone looked at it.

**Why flat, and not derived**: the first design computed a price from the model's file size and
the node's hourly cost via RM-62's formula. It was measured against this fleet before being built.
`tokens/s x GB` — the quantity that would have to be roughly constant for size to predict
throughput — came out:

    qwen3-0.6b   (dense,  0.36 GB)   363 tok/s ->   131
    qwen3-8b-q6  (dense,  6.26 GB)    63 tok/s ->   396
    gpt-oss-20b  (MoE,   11.28 GB)   115 tok/s ->  1295

A 10x spread: a MoE model reads only its active experts, and a very small model is bound by
overhead rather than bandwidth. That price would have been wrong by an order of magnitude on a
model in this deployment *and* would have looked measured. A flat base price claims nothing it
cannot support, and the throughput calculator on the pricing page replaces it with the real
figure once the model has served traffic.

**The figures** are aligned with published per-1M-token rates for hosted small-to-mid open models
rather than with any node's own cost. They are a starting point to be checked against current
rates, not a market quote.


## PRM-121 — Rate the whole history, now that nobody has been invoiced

**Why**: PRM-120 gave every model a base price, but only forward. PRM-117's tool refuses to price
usage older than the price itself — RM-60's rule, and the right default, since changing what a
past period cost is how a client gets a bill they were never shown. That refusal left 403 rows
permanently unbillable. The operator's answer settled it: no client has been invoiced from this
system yet, so the rule was protecting nobody and costing correct data.

**Scope**: `--include-usage-older-than-its-price` on `reprice_unpriced_usage.py`. It switches off
the age check, falls back to PRM-120's per-modality base price for a model with no price row at
all (a retired instance still has a `request_kind`, and the kind maps to exactly one modality),
prints what mode it is in, and re-labels its own plan — the default header says "the price was
already in force", which under this flag would be a lie. It is a flag rather than a default so
that using it is recorded as a decision somebody made, and so the next operator, who may well
have invoiced someone, does not get it by accident.

**Result on this deployment**: 403 events and 15 daily rows, +0.04554608 USD, both tables moving
by exactly the planned amount with row and token counts unchanged. Every usage event now has a
cost.

**Left alone deliberately**: five `usage_daily` rows from 2026-09-07 whose per-component costs do
not add up to their total. That day predates the `usage_events` table, so there is nothing to
recompute the split from — the total is complete and the breakdown is partial, and inventing the
difference would be worse than reporting it.


## PRM-122 — A real charge never renders as zero

**Why**: reported from the billing detail. Once PRM-121 gave every row a cost, most of them
displayed as `USD 0.00` — `formatUsdCost` rounds to 4 decimals, and a single small request costs
far less than that. 12 prompt + 12 completion tokens at PRM-120's base rate is 0.0000096 USD.
This is the same defect PRM-119 fixed in the period total, one row down and in a different layer:
a real figure rendered as a confident zero.

**Scope**: `formatUsdCost` and `formatCurrency` widen to two significant digits for amounts below
their floor, and keep 4 decimals as that floor, so every figure that already displayed well is
unchanged (1.628 stays 1.628). A true zero still shows 0.00, because that one is zero, and null
still shows an em dash. `formatCurrency` needed it too: a small USD amount stays small in PEN, and
that is the figure a client reads as their bill.

**Verified by running the function over the real amounts**, not by a test: `admin-ui` has no JS
test runner. That is now the second UI defect in this session a unit test would have caught (the
other was the CSV parser's CRLF handling), which is worth weighing against the cost of adding one.


## PRM-123 — Price the whole catalog, not just what happens to be running

**Why**: reported from the Model pricing page — most rows were still empty. PRM-120 seeds prices
on catalog sync, but it read the gateway's own registry, and that registry is built from
`/v1/backends`: running instances. A model that has been downloaded and never started has no
instance, so the gateway had never heard of it. 21 of this deployment's 30 catalogued models were
in that state. `admin/router.py`'s own comment had said so all along — *"a catalog entry with zero
instances only ever shows up here, not in list_instances"* — and the seeding read the wrong one.

The timing is the point: a price is wanted *before* a model first runs. Seeding from the served
set meant a model got its price only after it had already recorded requests at NULL, which is the
window PRM-120 existed to close.

**Scope**: the sync fetches each node's `/v1/models` and seeds from that, unioned with the served
set so a node whose catalog cannot be read still prices what it is actually running. No long-poll
index on the catalog call — it changes when somebody downloads a model, which is rare, and the
loop is already awake. A node that fails to answer is logged and skipped, never treated as a node
with an empty catalog (RM-98's rule).


## PRM-124 — Reset restores the base price, not a dash

**Why**: reported from the pricing table — pressing the reset arrow put the row back to an em
dash. The endpoint was doing exactly what it was written to do: remove the DB override, fall back
to `pricing.yaml` or to nothing. That was correct while unpriced was a normal state. PRM-120 made
"every catalogued model has a price" an invariant, and after it, reset produced the one state the
table is no longer supposed to have — and an unpriced model is not a blank cell, it is a model
that bills nothing and is never budget-checked.

**Scope**: DELETE removes the override and then writes the modality's base price, flagged
`is_default` again, applying it to the live table so the next request is priced without a restart.
Only when the modality cannot be determined at all is the model left unpriced — guessing one would
price it wrong on purpose. The modality comes from the gateway's registry when the model is
running, and from the node catalog otherwise, since most of the pricing table is models that have
never been started (PRM-123). `app.state.registry` is exposed for the first half of that.

**And the half that is not the endpoint**: the row's inputs are `useState` seeded from the entry,
which only runs at mount, and the reset handler blanked them locally. Even with the server
restoring the price, the row would have kept showing empty fields. The handler no longer touches
them and the row is keyed on the stored price, so it remounts with whatever is actually there.


## PRM-125 — Both toolbar buttons propose a price; Save commits it

**Why**: the two buttons beside each price did different kinds of thing. The calculator filled the
inputs and left them for review; the reset arrow wrote to the server on the click. So Save was
meaningless on half the toolbar, and the asymmetry was not cosmetic — it cost a real figure. On
this deployment `qwen3-embedding` was set to 0.437/0.5275, someone pressed reset while
demonstrating a different bug, and the price became 0.02/0.0. Nothing asked, nothing warned, and
it would have billed embeddings twenty times low until somebody happened to look. Restored from a
backup; the point is that no click should have been able to do it.

**Scope**: reset fills the inputs from the modality's base price and writes nothing, exactly as
the calculator does. The base prices ship with `GET /admin/api/billing/pricing` as
`defaults_by_modality` rather than from a second endpoint, so the table cannot hold a listing and
a set of defaults that disagree. The button is shown wherever a base price exists, not only on
overridden rows, since proposing a figure is useful on any of them.

**The DELETE endpoint stays** and still restores the base price (PRM-124): an API caller asking
for it explicitly is a different thing from a toolbar button doing it on a click. The dashboard no
longer calls it, so `useDeleteModelPrice` — orphaned by this change — is removed.


## PRM-126 — Structured outputs, and an allowlist that says what it dropped

**Why**: a client reported that structured outputs were unsupported. Measured against the live
engine first: llama.cpp honours `response_format` with a JSON schema — a two-field schema came
back as `{"capital": "Lima"}`. The capability was there the whole time and only the gateway
withheld it, because `ChatCompletionRequest` is an allowlist and `response_format` was not on it.

The allowlist itself is right (AC-5/AC-6: client-controlled fields must not reach the engine
unexamined). What was wrong was the silence. Pydantic drops an unrecognised field by default and
llama.cpp accepts unknown fields without complaint, so nothing in the path could tell a caller
their parameter had been discarded — and this guide documented it as "silently dropped if sent",
which makes it a decision somebody made rather than an oversight.

**What the industry does** (researched before choosing): OpenAI refuses an unrecognised top-level
argument with a 400 naming it. OpenRouter — a gateway over heterogeneous providers, the closest
analogue — routes and lets providers ignore what they cannot honour, *except* for a short list it
treats as too consequential to drop quietly, and `response_format` is on that list. Both agree on
the part that matters here: silently discarding this particular parameter is not acceptable
behaviour for either shape of system.

**Scope**: `response_format` forwarded as-is (same rationale as `tools` — the engine does the
grammar work, and validating the schema here would be a second, drifting copy of its rules).
`extra="forbid"` on the request schema, with `extra_forbidden` mapped to a dedicated
`400 unknown-parameter` that lists every offending name in one response. Kept distinct from
`422 validation-error` because the two have different fixes: "that field does not exist" versus
"that value is wrong".

**Found while measuring, not fixed here**: llama.cpp also honours `n` (returned 3 choices),
`seed` (identical output across two runs — real reproducibility), `logit_bias`, and both
penalties. All are still outside the subset. They now fail loudly instead of quietly, which is
the improvement this item claims; supporting them is a separate decision.


## PRM-127 — Unsupported parameters are reported, not refused

**Why**: PRM-126 chose OpenAI's answer to an unrecognised field — a 400 naming it. OpenAI is a
first-party API with one implementation; a gateway is not, and refusing outright throws away a
request the caller usually still wants served, while breaking anything already sending a harmless
extra. OpenRouter is the closer analogue and does the opposite: route anyway, let what cannot be
honoured be ignored, and offer `require_parameters` to callers who would rather fail.

**What OpenRouter gets for free and we do not**: discoverability. Its clients can look up which
parameters each provider supports, so ignoring is quiet but not hidden. Ours cannot, which is
exactly how a documented "silently dropped" survived for months. So the port adds the missing
half: `X-Prometheus-Ignored-Parameters` on the response, absent when there is nothing to report
so its presence always means something.

**Scope**: request schemas move from `extra="forbid"` to `extra="allow"` — the allowlist's real
guarantee is `to_llama_payload`, which names every field it forwards, so an unrecognised one
still never reaches the engine (AC-5/AC-6); the change is that the gateway can now *see* what it
is setting aside. `require_parameters: bool = False` on all four inference schemas returns the
`400 unknown-parameter` PRM-126 introduced, now reached only on request.

**The guard matters more than the rule.** `_parameter_check` is one function called from four
handlers, which is the shape that cost PRM-118 — a one-line rule repeated at nine call sites and
wrong at four. A new endpoint that forgets it does not fail; it goes back to dropping parameters
in silence. So a test reads the router, finds every handler taking one of the request models, and
fails naming any that does not check. Run against a tree with one call removed, it names that
handler.


## PRM-128 — A machine credential stops paying for itself twice

**Why**: the Executive Assistant team reported two problems — a rerank request that took 42s
against a 345ms median, and a 60 RPM ceiling that forced them to plan for ~20 suggestions a
minute. They are one bug and its own error handling.

Rate limits are charged per `client_id` (AC-1) and per `user_id` (AC-9), which is right: one user
of a multi-user client must not be able to eat the whole budget. But a `client_credentials` token
has no human behind it, so the JWT's `sub` and `azp` are both the client, both charges hit the
same Redis key, and every request cost two. Measured against the live deployment before touching
anything: **30 requests accepted, 429 on the 31st, `X-RateLimit-Limit-Requests: 60` on every one
of them**. The "hang" was that 429 carrying `Retry-After: 58` and their SDK honouring it — the
same phenomenon P-16 already explained to Axonium, reaching a second team through a different
door.

**Scope**: skip the per-user charge when the identity is the same string, for RPM in the
middleware and for TPM in the router's post-response increment. TPM had it too, so a 40,000
token/minute budget was really 20,000. AC-9 is unchanged where a distinct `sub` exists, and a
test pins that half so the fix cannot quietly become "never charge the user".

**Also measured, and not a bug**: `/v1/chat/completions` has its own bucket while `/v1/embeddings`
and `/v1/rerank` share `default` (`_ENDPOINT_SLUG_MAP`). The team assumed 60 RPM per endpoint and
divided by three; the real shape is 60 for chat and 60 shared between the other two, so their
ceiling is 30 suggestions a minute, not 20 — and after this fix, that is 30 real ones rather than
15. Giving those two endpoints their own slugs is a one-line change each if they need more.


## PRM-129 — Three rate-limit budgets, and every response names its own

**Why**: two requests from the new tripartite channel, which had to ship together.

*E-05 (Executive Assistant)*: `/v1/embeddings` and `/v1/rerank` fell through to the `default`
budget while `/v1/chat/completions` had its own. Nobody chose that — it is what happens when one
route is on `_ENDPOINT_SLUG_MAP`. Their copilot spends 3 requests per suggestion, 2 on the shared
pair, so nine concurrent executives asked for 63/min against a 60 budget and missed by 5%. One
line each moves them to 32 of 60 in three separate budgets, at 52%.

*A-02 (Axonium)*: separating the budgets without naming them would have made things worse where
nobody looks. Their `RateLimitSnapshot` holds one slot with no field saying which budget the
numbers describe — correct while there was one budget, wrong the moment there are three and a
single suggestion touches all of them in sequence. A dashboard would keep drawing a plausible
number belonging to a different budget. So `X-RateLimit-Scope` on every response and `scope` in
the 429 body, in the same deploy as the split rather than after it.

**Found while running this**: `test_a_seeded_model_actually_bills`, written in PRM-120, compared
`date.today()` (local) against a row `record_usage` stamps in UTC. It fails for the five hours a
day the two disagree and had been green until a run crossed 19:00 local. Two other tests in the
suite already carry a comment warning about exactly this; PRM-120 wrote the trap anyway.


## PRM-130 — The rate-limit 429 carries the same envelope as every other error

**Why**: A-04. PRM-129 announced `X-RateLimit-Scope` "on every response" and it was not on the
429 — Axonium exhausted a budget and read the whole envelope rather than taking the claim. In a
200 the scope is a convenience; in a 429 it is what decides whether a client backs off one
endpoint or all three, so it was missing from the one response that needed it.

The cause is the shape, not the omission: there were **two copies** of the `X-RateLimit-*` header
list, one per response path, and the new header went into the first. Adding the line to the second
would have fixed this instance and left the next header to go the same way — the same duplication
that cost PRM-118 and PRM-127.

**Scope**: one `_rl_headers()` builder used by both paths, and a test that fails if the two ever
send different header names again. Plus `trace_id`, which this envelope had been documented as
omitting: Axonium cited that omission as evidence of the pattern, so closing one field and leaving
the other would have kept the pattern and missed the point. The envelope is now a strict superset
of the standard one instead of a variant of it.


## PRM-131 — We emit metrics, not only traces

**Why**: found by installing `argus-obs-semconv` to review it (P-29) and looking for where its
instruments would plug in: nowhere. `configure_tracing()` had no sibling. The platform installed a
`TracerProvider` and never a `MeterProvider`, so three services emitted **zero** OTLP metric
points — and two of the numbers that matter most here cannot be carried by a span at all.
Time-to-first-token and cost per request are per-request facts that only mean anything aggregated;
a trace holds one of them at a time, and sampling then decides which ones survive.

It looked worse than it was. Argus's silence probe queries metrics, and A-01 itself says it "no
cubre el caso de un servicio que nunca ha emitido" — so the probe appeared to have been armed on
nothing since 13/09. A-28 settled it with the numbers: their collector derives metrics from our
spans with `spanmetrics` before sampling, and the probe fired six real incidents during their own
two-day outage. The gap is real, the emergency was not.

**Scope**: `configure_metrics()` in the shared telemetry package — the mirror of
`configure_tracing()`, same idempotency guard, same `OTEL_SDK_DISABLED` handling, same RM-92
convention that an unset endpoint means export nowhere rather than disable the SDK. Called from
the gateway's `create_app()`.

The four GenAI instruments come from **Argus's package**, not a copy of it: names, units,
instrument kinds and the meter scope are theirs, so a dashboard built on their conventions finds
our series without a translation layer and a rename arrives as a dependency bump. Pinned exactly
at the prerelease `1.0.0a5`. The earlier reason to wait was that the module was private and three
attribute names disagreed with ours; A-29 closed both.

Emission lives in `_record_usage()`, the one funnel the five success paths already share, and the
cost comes back from `db.record_usage()` rather than being re-derived — a second answer to a
question already answered is how PRM-118's reserve and settle came apart. An unpriced model
records no cost point at all: a counter incremented by `0.0` makes "no price" and "free" the same
line on a chart, which is PRM-119's rule moved onto an instrument.

The import of `argus_semconv.metrics` is deliberately inside the function. OpenTelemetry's
`_ProxyMeterProvider.get_meter()` accepts `attributes` and discards them, so a library imported
before the provider exists loses its scope attributes for the life of the process — including the
`argus.semconv.version` A-29 had just added for exactly this purpose. Measured both ways; there is
a test on it.

Out: the other two services (nothing GenAI to emit), error-path duration with `error.type`
(the instrument takes it, our error paths do not reach this funnel), and adopting the package
anywhere beyond these four instruments.


## PRM-132 — The budget alert survives a mail server that is down

**Why**: read in Argus's own postmortem (A-28) and it applies to us unchanged. They found their
telemetry *ingest* had a write-ahead queue on disk good for a weekend without network, while the
*dispatcher* — the part the whole system exists for — made one attempt over the same network and
dropped the alert on a TLS error. Six incidents, none delivered.

`notifications.py` is that shape. `send_budget_alert_email()` calls `smtplib` once from
`asyncio.to_thread`, logs a failure and returns. A client crossing their spend cap while SMTP is
unreachable produces no email, and nothing anywhere records that one was owed — the in-app banner
is derived live from `BudgetTracker`, so it is not a fallback for a missed notification, only a
parallel one.

**Scope**: retry with backoff, and a record of what was attempted so an undelivered alert is
visible rather than absent. Out: a general outbound queue, and any change to the thresholds
themselves.


## PRM-133 — Each node declares which engines it has, and an instance can only pick one of those

**Why**: `AddInstanceModal` renders the whole of `BACKENDS` as an Engine dropdown on every node,
unconditionally. Three of those five cannot run on the only node this deployment has: the `vllm`
and `sglang` command builders both carry the comment *"NOT verified against a real install (needs
CUDA)"*, and the node is Apple Silicon. The fact exists in a Python docstring and nowhere the
operator can see it — RM-89's rule about defaults, applied to options: an option that cannot work
must not look like one that can. And nodes carry no engine inventory at all, so nothing *could*
filter the list today.

**Scope**: `nodes.engines`, a nullable JSON column on auth-service's node registry, declared
through a checkbox list at node registration and read back by both forms that create an instance
(`AddInstanceModal` and `RegisterModelModal` — both put a model on a node, so filtering one and
not the other would be the same defect at the other call site).

**Three states, not two** (RM-98), and the column is nullable for exactly that reason: `NULL` is
"never declared" and offers every engine, which is what the form did before this existed, so the
node that predates the column does not become unusable. `[]` is "declared none" and offers
nothing, said out loud rather than shown as an empty dropdown. The operator's checkbox list starts
in the undeclared state and enters the declared one the moment a box is touched.

auth-service does not validate engine names against a list, on purpose: it is the identity and
node registry, it has no business knowing what an inference engine is, and a second copy of
`BACKENDS` would drift from the manager's. It checks the *shape* of an id and nothing more. The UI
intersects what it reads with the engines this build can launch, so a name it has never heard of
can never become a selectable option. `gateway/tests/test_engine_list.py` fails if the UI's list
and manager-core's ever disagree.

**Not built**: probing. `scanner.py` already knows every engine's process signature and
`lifecycle.py` already uses `shutil.which`, so a node's own manager-api could report what it can
actually launch rather than trusting a checkbox — a declared list is a claim about software on
another machine, and those go stale silently. The checkboxes are the right override either way;
the probe should become the default. Also out: installing an engine from the UI, and per-engine
launch-flag editing.

**Which engines to offer** — researched 2026-09-20, against what this catalog actually serves
(text, embedding, rerank, vision, image):

| engine | verdict |
|---|---|
| `llama_cpp`, `mlx`, `sd_cpp` | keep — the three that run here today |
| `vllm`, `sglang` | keep in the list, CUDA-only; they are the two standard production answers |
| **TGI** | **do not add.** Archived 2026-03-21 and in maintenance mode; its own README now sends users to vLLM, SGLang, llama.cpp and MLX |
| `hf-serve` | candidate, experimental. The only one spanning Transformers + Diffusers + Sentence Transformers in a single server, which is our three modalities in one process |
| `tei` / `infinity` | strongest additions for what we actually run. We serve embeddings and rerank on llama.cpp; both of these are purpose-built for it. TEI is one model per process with a Metal build; Infinity serves many models per process and covers rerank and CLIP. **Both now have their own items with measured requirements — `PRM-179` (TEI) and `PRM-180` (Infinity) — after Centinela's `C-01` asked for exactly what they do. This row stays as the survey that picked them; it is not the detail** |
| `vllm-mlx` | worth watching: continuous batching on Apple Silicon, reported 3.4x throughput at 5 concurrent requests on an M4 Max. That is precisely the ceiling the copilot team hit in E-08 on this hardware |
| `tensorrt_llm` | only meaningful once there is an NVIDIA node |
| `ollama`, `lmdeploy`, `mlc-llm` | not now. Ollama wraps llama.cpp and would duplicate a backend we have; the other two earn a place only with hardware we do not have |

Adding an engine to `BACKENDS` is not free — each one needs a command builder in `lifecycle.py`
and a process signature in `scanner.py`, and `vllm`/`sglang` show what an unverified builder is
worth. Nothing should join the list without one node that can actually run it.


## PRM-134 — The node registry moves to a coordinator manager

**Why**: auth-service held three tables and one of them was not security. `principals` and
`credential_share_tokens` are authentication; `nodes` is fleet inventory — a name, a manager URL,
a hardware class, two cost components, a margin, and a list of installed engines. It touches no
principal, no token and no scope, and its only consumer is the gateway.

RM-20 put it there for a real reason: auth-service was the only central service, and
**`manager-api` runs per node** — each with its own `registry.db` — so no manager could hold the
list *of* nodes without one becoming special.

**The decision is to accept exactly that.** One `manager-api` is designated the coordinator in
its `manager.toml` (`[fleet] coordinator = true`); it owns the registry and serves
`/v1/fleet/nodes/*`, and every other node leaves the flag false and behaves as before. That is
Nomad's server/client split and Kubernetes' control-plane/node split: identical software, one
configured role. A flag rather than an election, because two nodes and a laptop do not need
consensus and a leader nobody chose is harder to reason about than one written in a file.

**Its own database, not `registry.db`.** `registry.db` is per-node — the models and instances on
*this* host. The node list is fleet-level and there is one of it. Every system with this shape
keeps the two apart — etcd versus the kubelet's own state, Nomad's server store versus
`client/state.db` — and the reason is that node-local state is **disposable by design**: wiping
it and letting the node re-sync is routine. Sharing one file would mean the coordinator, which is
also a node, could not have its local state wiped without destroying the fleet.

**An earlier draft put this in the gateway's database and that was wrong on the user's
challenge.** It also leaned on `manager-owns-registry`'s rejection of a shared SQLite registry as
a live constraint, which PRM-149 then found had been overtaken — the manager itself uses SQLite
now. The argument for the coordinator stands on the industry pattern and on the per-node
constraint, not on that document.

**What this buys, stated precisely**: the node list no longer needs auth-service's admin API or
the shared admin key. It is **not** full independence — the manager token is still issued by
auth-service — but that token is cached with a 300 s TTL and renewed early, where the old
admin-key call failed on every poll. Measured on 2026-09-26: an auth-service the gateway could
not authenticate to emptied the entire model catalog.

**Scope**: `prometheus_manager_core.fleet` (registry + `Node`), `[fleet]` config with
`PMGR_FLEET_*` env overrides, `fleet_routes.py` with the seven endpoints under the existing
`backend-registry:*` scopes, the gateway's seven dashboard proxies repointed, `fetch_nodes`
reading the coordinator, `MANAGER_FLEET_URL` required when the dashboard is on, and
`scripts/migrate_node_registry.py` — idempotent, verifying, and deleting nothing.

**auth-service keeps its table and rows.** That is the rollback path, and this codebase's
additive-only convention does not drop tables. Nothing writes to them.

**Verified**: 28 tests in `runtime/manager/api/tests/test_fleet.py` — the 25 ported from
auth-service verbatim, because what they pin is the contract the dashboard depends on and the
contract did not change, plus three for the 409. A guard replaced auth-service's file: the seven
routes must 404, no route there may mention nodes under any path, and the table must still exist.
Live: `local` restarted as coordinator, two nodes migrated with their engines intact, the
dashboard's Nodes, Instances and Users all 200, ten models in the catalog, inference working, and
`manager_sync` logging only `refreshed` and `token_renewed`.

**One self-inflicted repair worth recording**: the abandoned draft had applied an Alembic
migration to `gateway.db`, so the stamp pointed at a revision that no longer existed on this
branch and the gateway refused to start. Reset to the real head and the orphaned table dropped,
after confirming its two rows were already in `fleet.db`.

## PRM-135 — hf-serve, and a `lab` node to try engines on

**Why**: first of the three engines being added one at a time (hf-serve, TEI, vLLM-MLX). hf-serve
earns the first slot because it is the only entry in `BACKENDS` that covers all five modalities
in one server — its `--task` vocabulary has a name for every one of ours.

**Scope**: `hf_serve` in `BACKENDS`, a command builder, a scanner signature, config defaults, and
the engine in the admin UI's list. Plus `manager-lab.toml`: a second manager-api on this same
machine, port 8091, its own registry, its own PID and log directories — the same hardware, but a
node where an engine can be exercised before it is offered where it matters. Registered as `lab`
with its engines declared (PRM-133).

Two things make its builder unlike the other five, and both are in the code as comments:
`--model-id` takes the Hub repo, not `entry.path`, because this catalog's paths are `.gguf` and
Transformers cannot read them — a model with no `hf_repo` is refused at launch with the reason
rather than handed a path to fail on. And `--task` is `modality` translated; this is the first
backend that acts on modality beyond llama_cpp's `--embedding`/`--mmproj`, which RM-09 predicted.

**Two defects, both found by launching it rather than by testing it**:

- Adding a backend is five coordinated edits — the tuple, the default TOML, `BackendsConfig`,
  `load_config`, and `_backend_config`'s dict. Missing the last three imported cleanly, typechecked
  cleanly and passed the whole suite; it failed at `start_instance` on a real request with
  `No [backends.hf_serve] config found`. Three tests now assert that every entry in `BACKENDS`
  resolves a binary, has a command builder, and has a scanner signature.
- The gateway's manager client waited **10s** for a start the manager was willing to wait **300s**
  for. A backend that loads slowly came back as a 502 with an empty message while the process was
  loading correctly — and httpx closing the connection cancelled the manager's handler, so the
  start that "failed" was aborted by the thing reporting the failure. The three lifecycle actions
  now use a ceiling above every backend's own timeout; everything else stays at 10s.

**Verified live**: `hf-serve --model-id sentence-transformers/all-MiniLM-L6-v2 --task embeddings
--device mps` launched by the manager on `lab`, ~2 minutes to load, then 384-dimension embeddings
with usage accounting. The gateway discovered it through `manager_sync` and routes to it.

**Not exercised**: an inference call all the way through the gateway. It returns 403 — RM-07's
deny-by-default, working exactly as designed, because the test client holds no
`model:minilm-hfserve` scope. Granting one is an operator decision, not a verification step.

**Found by the end-to-end call, once the model scope was granted**: `gen_ai.provider.name` came
out as `hf_serve` — our backend id, not the product's name, while `llama_cpp` and `sd_cpp` both
map to theirs. It would have reached Argus as a provider nobody could find, and correcting it
later would have split the series it had been accumulating. Mapped, with a test asserting no
backend's provider name still contains an underscore.

**Also installed on this machine**: `libmagic` (Homebrew). `pip install hf-serve` produces a
binary that cannot start without it, and nothing in the Python metadata says so — worth knowing
before this engine is declared on a node that does not have it.


## PRM-136 — A pass-through for tasks OpenAI has no shape for

**Why**: found by trying to serve `convaiinnovations/laya` on hf-serve. Laya does not load — it has
no root `config.json` and its entry point is its own `laya.Router`, not a Transformers `Auto`
class, despite a model card that says `library_name: transformers` and tags it
`endpoints_compatible`. But the attempt exposed two real gaps, and both were ours.

`text-classification` was not one of our modalities, so even a well-formed classifier could not be
registered. And measured against a running hf-serve: for classification it exposes **no `/v1/*`
surface at all** — `/v1/models` 404s, the OpenAI-compatible routes only exist for the tasks OpenAI
has. Classification is served at `/predict`, in hf-serve's own shape.

That is not a quirk of one engine. Laya and TypeSafe's Jev are a model class — non-autoregressive
"System 1" decision models that take a state plus typed questions and return calibrated
probability distributions in one forward pass. There is no OpenAI request body for that, and
inventing one would be this platform deciding what an engine's API should look like on the
engine's behalf.

**Scope**: `POST /v1/models/{model}/predict`. The body is forwarded verbatim and the answer comes
back verbatim, including the backend's status code — a 422 from the engine is the engine's answer.
What does **not** pass through is everything that makes this a gateway: the model still resolves,
the caller still needs `inference:read` and a `model:<slug>` grant, a dead replica is still
skipped, the call is still metered. The shape is the backend's; the policy is ours.

The modality check is the inverse of every other handler's: a model that *has* an OpenAI endpoint
is refused here, or the same model becomes reachable two ways with two billing paths and two
rate-limit buckets — and the one that bills correctly is whichever the caller did not use.

Its own rate-limit bucket, via a prefix/suffix rule rather than the exact-match map, because the
path carries the model name. PRM-129 is the record of what an unmapped route costs: `/v1/embeddings`
and `/v1/rerank` shared `default` for months and a copilot missed its pilot capacity by 5%.

**`MODALITIES` grows one verified engine-task at a time.** The route is general; the modality list
is not a guess. Today it is `classification` → hf-serve's `text-classification`, and nothing else
until something is launched and measured.

**Verified live**: `distilbert-base-uncased-finetuned-sst-2-english` on hf-serve, registered on
the `lab` node, launched by the manager, discovered by `manager_sync`. Through the gateway:
`POSITIVE 0.991` in 54ms, `NEGATIVE 0.965` in 165ms, `x-ratelimit-scope: predict`, a text model
refused with `Modality Mismatch` in 4ms, and three `request_kind='predict'` rows in `usage_events`.

**Not built**: a backend for Laya. It would be a sixth engine wrapping `laya.Router`, and this
route is what would make that engine reachable without inventing an API for it.


## PRM-137 — Zero-shot decisions, and the Playground can drive them

**Why**: PRM-136 built the pass-through and left `MODALITIES` with one entry, on the rule that it
grows one verified engine-task at a time. This is that verification, plus the benchmark that
decided which model to bring in.

`zero_shot` differs from `classification` in where the labels come from: a classifier has them
baked into the checkpoint, a zero-shot model is given them in the request. That is the whole point
of this class — the options are the caller's, decided per call, which is what "typed decision"
means for Jev and its kin.

**The model, chosen by measurement.** Of the "System One" open reproductions, only `wfzyx/von-1.0`
loads — it declares `ModernBertForSequenceClassification`, a standard class. NanoJev has no
`model_type`, mini-Jev ships a custom `ODMMiniModel`, OpenDecision and Laya have no root
`config.json`. Measured here, zero-shot, N=100, 10-bin ECE:

| | SST-2 (2 labels) | emotion (6 labels) | ECE (emotion) | p50 |
|---|---|---|---|---|
| `wfzyx/von-1.0` | 0.96 | **0.80** | **0.058** | 26–72 ms |
| `mDeBERTa-v3-base-mnli-xnli` | 0.81 | 0.40 | 0.088 | 31–57 ms |
| Laya (its own published zero-shot) | — | 0.583 | 0.318 | 38 ms |

Von beats Laya's published number on its own task family, and is five times better calibrated —
which matters because calibration is what this class sells. The generic NLI family is *not* a
substitute: mDeBERTa is worse than Laya at 6 labels. Caveats, stated rather than buried: different
dataset instances, N=100, Laya's column is self-published because it cannot be run here at all,
and Jev is a paid API so it was never measured.

**Scope**: the modality, its hf-serve task (`zero-shot-classification`), the pass-through set, and
the Playground. `useZeroShot()` posts to `/v1/models/{model}/predict`; the composer grows an
options field, because the labels belong to the request and not to the model picker.

**The result renders as a distribution, not a winner.** A decision model's answer is the shape of
its uncertainty — a single label would hide a 0.51/0.49 and that is precisely the case an operator
needs to see before wiring it to an automated action.

**Found while wiring it**: `PlaygroundModelPicker` built three hard-coded groups — Text & Vision,
Embedding, Image — so every modality added since was silently dropped. `rerank` had been
unselectable in the Playground since PRM-106 and nobody noticed, because a model missing from a
dropdown reads as a model nobody started. The groups are derived now, with the raw modality as the
fallback label: the failure mode is an ugly name, never a hidden model.

**And the two it had been hiding now work, not just show.** Making the picker complete exposed
that `classification` and `rerank` fell through to the chat path: selecting `sst2-clf` sent a chat
completion and got `Modality Mismatch` back. A visible option that cannot be used is the same
defect as a hidden one, so both got composers — classification sends only the text (its labels are
in the checkpoint, and the UI says so), rerank sends the query plus documents one per line, sorted
by score because a ranking shown in input order is not a ranking. All three share one renderer:
what differs is only what the request carries.

**Can a zero-shot model replace the reranker?** Measured, because the shapes look identical —
both are cross-encoders scoring N candidates against one input. On accuracy they are
indistinguishable here: over three hard retrieval cases Von and `qwen3-reranker` picked the same
document every time, including picking the same *wrong* one. The answer is still no, and the
reason is arithmetic rather than quality. A zero-shot model returns a **softmax over the
candidates**, so the scores sum to 1: with 20 passages the correct one scored 0.2095 where the
reranker gave 0.9999, and on a query with no answer in the corpus Von still had to hand 0.4951 to
something while the reranker returned 0.0033 / 0.0002 / 0.0001. That last one is the whole
difference — "nothing here answers this" is the signal that stops a RAG pipeline inventing an
answer, and a distribution that must sum to 1 cannot express it. It also means no fixed threshold
is possible, because every score moves when the candidate count does.

**Verified live**: Von registered on `lab`, started by the manager in 7s, driven from the
Playground — `facturación 0.7433 · cancelación 0.2418 · ventas 0.0091 · soporte técnico 0.0058`,
166 ms, and a `request_kind='predict'` row in `usage_events`.


## PRM-138 — A failed request hands its key back

**Why**: A-23. Synaptum, which builds on Axonium's SDKs, derives its keys from
`(run_id, step_id, body-hash)` on purpose — that determinism is what makes resuming a flow replay
instead of paying for the inference twice. The consequence is that a step's key never changes, and
they observed a step that failed once returning that failure for the whole 24-hour window, in
milliseconds, without reaching the platform. Four runs, three attempts each, all instant. It looked
like an outage and it was a held key.

Axonium could not reproduce it with the only failure they can force from outside (a `400` before
dispatch, which does release the key) and said so rather than sending us after a finding that did
not exist. Reproduced here with two tests, and there are **two** bugs, not one:

- **A backend 5xx is stored and replayed.** The handlers set `request.state.idempotency_result`
  from whatever the backend returned, status included, so the middleware's `complete()` wrote
  `state='completed' status=500` with the engine's error body. Every retry for 24h replayed it
  instantly. The middleware's own docstring already said "a success is stored for replay; anything
  else hands the key straight back" — the rule was true of the middleware and false of the
  handlers.
- **An unhandled exception skips the settle entirely.** `await call_next(request)` *raises* when an
  exception escapes the route, because Starlette's `ServerErrorMiddleware` sits outside this one,
  so nothing below it ran and the record stayed `in_progress` for the window. Later calls got
  `idempotency-in-progress` in milliseconds.

**Scope**: the success check moves into the middleware, where the docstring already placed it and
for the same reason it gives — a handler is one of a dozen exit paths, the middleware is one place.
And `call_next` is wrapped in try/except so the settle survives an exception on its way out.

**Answering A-23 directly**: a key whose request failed is now released, for every kind of failure.
Question 2 — "if it is kept, can it carry the original error type?" — stops applying: nothing is
kept, so a retry reaches the platform and gets a fresh, correctly typed answer. Question 3, the
missing `Idempotent-Replay` on a stored error, goes the same way: there are no stored errors to
replay.


## PRM-139 — The two new modalities had no price, so they billed nothing

**Why**: found while verifying A-22 rather than answering it from memory. Axonium asked when
`cost_usd` is `null` and said they could not produce one in this deployment. The live database had
**11** of them, all `request_kind='predict'` — every classification and zero-shot call made since
PRM-136 shipped.

PRM-120 gives each newly catalogued model a base price by modality. PRM-136 and PRM-137 added
`classification` and `zero_shot` to `MODALITIES` and not to that table, so those models were
catalogued with no price at all, and an unpriced request records `cost_usd = NULL` — correct, and
invisible: it reads as a request that cost nothing.

**Scope**: both at the rerank rate (0.02 / 0.00 per 1M), for the reason rerank has it — a
classifier and a zero-shot decider are prompt-only encoder passes that generate nothing. A test now
fails when a modality the registry accepts has no base price: `default_price_for()` returning None
for a modality with no published reference is a legitimate decision, but it has to be one somebody
took rather than a line nobody wrote.

**The 11 rows stay NULL, deliberately.** RM-60 prices a row at the rate in force when it was used,
and there was none — so `NULL` is the accurate record of an unpriced period, not bad data. The
repricing script refuses them on exactly that rule. Rewriting them would be falsifying history to
hide the mistake, and they are also the live example A-22 asked for.

**One caveat the token count does not capture**, flagged here rather than left to be rediscovered:
a zero-shot call runs the text once per candidate label, so its real compute scales with the option
count while the billed input does not. Priced per input token like the rest until that is worth
solving.


## PRM-140 — Laya: a backend configured by environment, not by flags

**Why**: PRM-136 recorded that Laya ships no server and that integrating it would mean writing one.
That was true of `laya` 0.3.4. They publish almost daily — 0.3.2 to 0.3.7 in four days — and 0.3.7
ships `laya-serve`: FastAPI, `/health`, `POST /v1/systemone`. The premise the earlier analysis
rested on had expired.

Laya is a "System One" decision model: a *state* plus a dict of typed questions (`choice`, `score`,
`noul`), answered in one forward pass, each with a probability distribution **and** a separate
confidence. Measured here: three questions in 367 ms end to end, 24 ms warm against the engine.

**Scope**: `laya` in `BACKENDS`, the `typed_decision` modality, its base price, the pass-through
path, and the Playground composer that can drive it.

**Two assumptions every other engine shared, and this one does not**:

- **It takes no arguments at all.** Host, port, device and which checkpoints to preload are every
  one of them environment variables, so `start_instance` grew the ability to pass an environment —
  inherited and extended, never replaced, because the child still needs `PATH` and `HF_TOKEN`. A
  second map rather than making all six builders return a pair: five take flags and one does not,
  and an asymmetry in the data is honest where one hidden behind a uniform signature is not.
- **The scanner read the port off the command line.** With no `--port` to find it scanned as 0,
  `_probe_health` returned `unknown` without probing, and a server answering perfectly well never
  reached `ready`. The port now comes from the registry, which is where the manager assigned it;
  reading it back off the process was only ever a convenience.

And the per-engine pass-through path that PRM-136 predicted in a comment — *"when a second one
arrives with a different path, this becomes a per-engine lookup"* — arrived.

**Found while integrating**:

- `pip install laya` gives you the `laya-serve` entry point and neither fastapi nor uvicorn; it
  starts and dies on `ModuleNotFoundError`. The server dependencies are behind a `[serve]` extra
  that the console script's own metadata never mentions. The third package this week whose
  metadata does not sustain what it ships.
- The request schema is not the one the README shows: options go in `criteria`, not `options`, and
  `instructions` is required. The Playground ships the schema that works as a fillable example,
  because an operator should not have to iterate against 422s to find it.
- A guard test's own parser broke on a parenthesis inside a comment. Fixed, because a guard that
  fails for a reason unrelated to what it guards teaches people to edit the guard.

**Verified live**: registered on `lab`, launched by the manager (all five `LAYA_*` variables
confirmed on the child process), scanned to `ready`, discovered by `manager_sync`, driven from the
Playground — `department: billing` at 0.968 with confidence 0.875, `urgency: medium` with
confidence **0.286** rendered in amber, `churn_risk: 29.1% yes` — and a priced usage row, 117
tokens at 2.34e-06.

**The amber is the point.** Laya reports confidence separately from the winning probability, and a
0.97 choice at 0.29 confidence is exactly the verdict an operator must not wire to an automated
action. The renderer shows both rather than the label alone.


## PRM-141 — Laya reads the words, not the meaning

**Why**: reported from the Playground on the first real try — `category` correct, `urgency` and
`churn_risk` wrong on an email that demanded a refund today or the contract ends.

**The cause is one thing.** Holding everything else fixed and changing only the wording of the
question, on the same email (`"...o cancelamos el plan"`):

```
"¿El cliente amenaza con cancelar?"          -> sí   0.980
"¿El cliente dice que cancelará el plan?"    -> sí   0.960
"¿El cliente amenaza con cancelar el servicio?" -> sí 0.861
"¿El cliente amenaza con irse?"              -> no   0.166
```

The email contains the word *cancelar*. Ask with it and the answer is right and confident; ask
with a synonym and it is wrong. **This model matches vocabulary far more than meaning**, which is
the single most useful thing to know before writing questions for it — and it is not in their
documentation.

**Two earlier claims here were wrong, and both failed the same way: the comparison moved two
variables at once.**

- *"The question ids are a contract and change the answer."* They do select a named workflow
  (`customer_service` is an exact id-set match), but the answer is identical: three runs each of
  `departamento/urgencia/fuga` and of the five official ids returned `alta(0.06)` every time. The
  first comparison changed the ids *and* added `from`/`subject` to the state *and* reworded the
  instructions. The official ids are kept in the example because a matched workflow is the
  documented path, not because they were measured to help.
- *"`noul` is not usable."* `noul` with the right wording returns **94.5%**. The original
  comparison changed the question type *and* the wording; with the wording held fixed, `noul` and
  `choice` agree.

**What still holds**, because that test was controlled — the same request sent to two servers:
`LAYA_AUTO_TASK=1` routes to the `typed-decisions` checkpoint, and everything collapses toward the
middle: `category` confidence 0.998 → 0.162, `action` 0.777 → 0.037, `churn_risk` a 49.8% coin
flip. The library says why on load — *"this checkpoint ships invalid temperatures or values
outside [0.5, 5] ... Treat confidence from the affected entries as uncalibrated"*. Calibration is
this model class's entire pitch. We do not set that variable and should not.

**Scope**: the Playground's example and the hint under it now carry the wording finding with its
numbers, and the incorrect claims are gone from the code comment.

**Von does not have this problem, and that decides which model routes real tickets.** The same
question asked three ways, against the same text, on both engines:

```
text: "...me cobraron dos veces..."          LAYA     VON
  "¿le cobraron dos veces?"                  0.988    0.999
  "¿hubo un cargo duplicado?"                0.945    0.999
  "¿se produjo una facturación errónea?"     0.700    0.998

text: "...o cancelamos el plan"               LAYA     VON
  "¿amenaza con cancelar?"                   0.830    0.927
  "¿amenaza con irse?"                       0.024    0.613
```

Von is an NLI model: judging whether one sentence entails another *written differently* is the
task it was trained on, so a paraphrase costs it a thousandth. Laya degrades on every synonym and
on the harder pair inverts — 0.830 to 0.024 is not doubt, it is the opposite answer held
confidently. With Laya the quality of the output depends on guessing the customer's vocabulary,
and "dar de baja" / "cerrar la cuenta" / "me voy a otro banco" are one intention with four
spellings. **Von for routing real traffic; Laya where the several-decisions-in-one-pass shape and
the separate confidence are worth the wording discipline.**

**The lesson is mine, not Laya's.** Axonium wrote in A-23 that they nearly sent us chasing a
finding that did not exist, and caught it with a control. This investigation made that mistake
twice in an hour, in the same shape, after quoting them approvingly for avoiding it.



## PRM-142 — An error envelope is policy, not shape

**Why**: Axonium tested `/v1/models/{model}/predict` with real grants (A-28) and found its `422`
outside the problem+json envelope — `content-type: application/json`, no `type`, no `request_id`,
no `trace_id`. The other three error paths on that route were clean. They made the argument with
the sentence PRM-136 wrote into the handler: *"the shape is the backend's; the policy is ours."*
An envelope is what makes a failure correlatable, typable, and classifiable as retryable. It was
leaving with the body.

**And the half they did not find, which is worse.** Five handlers forwarded a request, recorded
usage against the result, and returned the backend's status code verbatim — without ever reading
that status. So a request the engine **refused** was metered and billed exactly like one it had
served. Measured live before the fix:

```
POST /v1/models/sst2-clf/predict   {"campo_inventado":"x"}   -> 422
GET  /v1/usage/504456b6-…          -> prompt_tokens 6, cost_usd 1.2e-07,
                                      termination_reason "complete"
```

`"complete"` on a request the model never ran said the answer was whole.

**How much each route overcharged depended only on where its token count came from**, which is
the failure mode the four teams keep meeting — a value populated from the wrong source stays
plausible:

| route | count from | a refused request billed |
|---|---|---|
| `predict` | estimated from the *request* body | a real, non-zero charge — **measured** |
| `images` | `len(data) or 1` | one image, always |
| `embeddings` / `rerank` / `chat` | the backend's own `usage` object | 0 tokens — a junk row |

**Scope**: `_backend_refused()` and a guard before the recording in all five buffered handlers.
Only the pass-through route gets our envelope; the other four keep returning the backend's
OpenAI-shaped error body, because that *is* a contract the SDKs already parse and re-wrapping it
would be a breaking change announced to nobody. Told to Axonium as an asymmetry to decide on, not
resolved unilaterally.

**Two deliberate deviations from what was asked**:

- **`predict-backend-rejected`, not `predict-payload-rejected`.** A 429 or 403 from the engine is
  also a 4xx and is not about the payload. The type names what the gateway can see; the status
  and `backend_error` carry the cause.
- **The engine's error goes in `backend_error`, not `detail`.** RFC 9457 defines `detail` as
  human-readable text, so `_problem()` grew RFC 9457 extension members instead. Same information,
  in the field that can hold a structure.

**Verified**: four respx tests (envelope on a 422, no usage row for a refused request, a 5xx
mapped to `upstream-error` with `backend_status`, and the 2xx path still billing) plus a
per-handler AST guard — asserted on handlers rather than on a count, because the thing that must
not happen is a *new* forwarding route that bills without asking. One pre-existing test asserted
the old behaviour and was rewritten rather than deleted: PRM-136 pinned "the body comes back
verbatim, including on error", and that is precisely the decision this reverses.


## PRM-143 — A streamed request whose backend refused it returns 200 and an empty stream

**Why**: the sixth instance of PRM-142 and the only one not fixed there. `_stream_events` never
reads `resp.status_code` either, but by the time it could, `StreamingResponse` has already fixed
a 200. A backend 4xx is not SSE, so no line starts with `data:`, nothing is emitted, token counts
stay 0 — and the caller gets a 200 whose body is just the terminal frame.

Not fixed with the others because it is a restructure, not a guard: the status has to be probed
before the response object exists. Half-fixing it would have billed correctly while still lying
about the outcome.

**Scope**: probe the upstream status before constructing the `StreamingResponse`, so a refused
stream is an error response.

**How.** The connection is opened by `_stream_response` itself — `client.send(request, stream=True)`
returns once the response headers have arrived, without reading the body — and only a status worth
streaming is handed to `StreamingResponse`. `_stream_events` consumes the response that is already
open instead of making its own, and closes it in a `finally`, which is what its `async with` was
doing. `pool.acquire` moved to *after* the check, so a stream that never starts never claims the
slot RM-72 holds for as long as one generates; an engine rejecting everything would otherwise have
read as the busiest replica in the fleet and been routed away from.

**Two failures, told apart.** A refusal returns the backend's own status and body, which is what
the non-streaming half of the same endpoint does — a caller toggling `stream` must not get a
different error contract. A connection that never opened returns 503 `backend-unavailable`; it
used to land in the generator's own `except` and be delivered as `{"error": "stream interrupted"}`
inside a 200, so an SDK reading the status saw success. Neither path bills, neither stores anything
for an idempotent replay, and both release the claim RM-82 handed to the generator — the generator
being the thing that no longer runs.

The circuit breaker records what `BackendPool.forward` would have recorded for that status, reading
the pool's own transient set rather than restating the rule. Same request either way: a breaker that
counted differently depending on `stream` would be a second answer to one question.

**Verified live**: a streamed request carrying an unparseable JSON schema came back `400` with
llama.cpp's own message and left no usage row; the next healthy stream on the same model streamed
normally and left exactly one. The PRM-142 guard test kept its exemption for `_account_for_it` —
the status is checked by the enclosing function, not the one that bills — but now asserts
`_stream_response` is doing the asking, so the exemption cannot outlive the fix.


## PRM-144 — `payload_schema`, because `engine` is a proxy for it

**Why**: P-23 named a gap of our own — the public catalog gives a consumer the modality but not
the body shape, and for a pass-through route the shape is the engine's. We offered to publish
`engine` and asked Axonium to decide before we built it. They said no (A-27), and the argument is
better than ours:

> Motor sustituido por otro de contrato idéntico → rompe a todos aunque el contrato no cambió.

With `engine` in the contract, a consumer's dispatch table is keyed on the name of our
implementation, so **an internal substitution that changes nothing observable becomes a breaking
change for every client**. It is the same defect the four teams keep finding — a datum populated
from the wrong source stays plausible — except written into the contract deliberately.

They also corrected a claim in P-23. We wrote that `engine` "reveals nothing the `id` does not":
true of `minilm-hfserve`, false of `von-decide` and `laya-decide`. That an id leaks the engine is
a reason to revise the id, not to add a field that leaks it for every model.

**What they asked for instead** is a versioned *contract* identifier — `typed-decision.v1`,
`hf-inference.text-classification.v1` — which identifies the shape exactly, survives an engine
swap that preserves the contract, forces a deliberate `.v2` when the shape changes, does not
publish the stack to an unauthenticated endpoint, and can be pinned as a fixture in their corpus.
Grounded in four precedents they cited: Hugging Face's `pipeline_tag`, OpenRouter's
`supported_parameters`, Kubernetes' `apiVersion`, and OpenAI/Anthropic publishing capabilities
but never the engine.

**Scope**: `payload_schema` on the catalog entry, derived from `(modality, engine)` in one place,
and a guard so a new engine or modality cannot reach the catalog without one. `engine` is not
added anywhere.


## PRM-145 — The rates that priced a row, on the row itself

**Why**: A-25. P-21 told Axonium the number is `tokens × rate` and that the export carries the
rate on every line, so a ledger can **check** our figure rather than copy it. Then they measured
the asymmetry:

```
GET /v1/usage/export   -> 403 (needs admin:read)
GET /v1/usage/{id}     -> 200, and carries cost_usd but no rate
```

**The only path a non-admin consumer can walk could only copy.** Aeon told them
`usage.retrieve` would likely be how they build `OBS-007`, precisely because it is per-request
and needs no `admin:read` — and going that way would have lost the property that made P-21 worth
answering.

The rates are already stored on the row; PRM-115 put them there for the export. Nothing had to
be computed, only returned.

**Scope**: `prompt_price_per_1m`, `completion_price_per_1m` and `image_price_each` on the
`GET /v1/usage/{request_id}` response, under a `rates` object so a `null` means "no price was
configured" rather than "the field is missing". They stay frozen with the request, so a price
change never re-rates the history on read (RM-60).



## PRM-146 — A node list never fetched is not an empty one

**Why**: found while restoring the bare-metal dev stack after a restart, and the shape of the
failure is the point. The gateway came up **healthy**: `/health` 200, the dashboard login
working, `/v1/models` serving all 10 models from RM-99's snapshot. One poll interval later it
served zero, and the only trace was `manager_sync.refreshed count=0`.

`_refresh_nodes` already protects the node list — *"keep the previous node list rather than
wiping it on a blip"*. But on the first cycle after a restart **there is no previous list**. A
failed fetch leaves `_nodes` empty, and every line downstream then behaves correctly over zero
nodes: zero models found, zero stale nodes, and `self._registry._models = new_models` replaces
the restored snapshot with `{}`. RM-99's own guard held — the snapshot on disk was not
overwritten, because `len(stale_nodes) < len(self._nodes)` is `0 < 0` — so the good data was
still there, and memory had already thrown it away.

**`_nodes` being empty answered two different questions**, and the legitimate answer was given
to both:

- *no nodes are registered* — true, and it means zero models;
- *the registry could not be asked* — and that means nothing at all.

It is RM-98 one level up. That entry established that a node which failed must never be
mistaken for a node with nothing; this is a *registry* that could not be reached being mistaken
for a registry with nothing in it.

**Scope**: a `_nodes_ever_fetched` flag, set only on a successful fetch, and a `_sync` that
returns early with a distinct log line naming the two settings to check. A flag and not a
length test, deliberately — zero registered nodes is a real answer that must still empty the
catalog, or removing the last node would serve its models forever. Both halves are tested.

**Verified live**: with a deliberately wrong `AUTH_SERVICE_ADMIN_API_KEY`, the catalog now holds
its 10 snapshot models across polls and inference keeps working, with
`manager_sync.node_registry_never_reached` in the log instead of silence.



## PRM-147 — The stack's configuration stops living in a shell

**Why**: restarting the gateway to load a code change took the entire stack down for two hours.
Not one line of application code was at fault. `gateway/.env` held the Podman values —
`https://auth-service:9000`, `http://manager:8090`, `redis://redis:6379` — none of which resolve
on this machine, and the operator's terminal had been overriding all of them for months.
`auth-service/.env` did not exist at all, so that service was running entirely on shell state
too.

**Five variables failed at once and each one masked the next**, which is why it read as "the
dashboard is broken" rather than as a config problem:

| variable | symptom | why it misled |
|---|---|---|
| `AUTH_SERVICE_TOKEN_URL` | the login could not proxy | — |
| `JWT_JWKS_URL` | *every* authenticated call 500s | login is exempt from JWT validation, so login kept working |
| `JWT_ISSUER` | 401 after the above was fixed | reported as `Token signature validation failed`, pointing at the key |
| `MANAGER_URL` | no manager | — |
| `AUTH_SERVICE_ADMIN_API_KEY` | node registry 403 | and PRM-146 turned that into an empty catalog |

**And the documentation was wrong in the two places someone would rebuild from.**
`auth-service/.env.example` said the issuer had to match a value in the repo-root `.env` — a file
this service never reads, so the instruction sends you to verify against something that can
differ from what is actually minted while looking authoritative. It cost hours here, because it
is what produced the wrong conclusion that the issuer was already correct. The README named
`AUTH_DATABASE_URL` (no such setting), listed `AUTH_JWT_ISSUER` as optional when the service will
not start without it, gave a single `AUTH_TOKEN_TTL_SECONDS` where there are four per-role ones,
and omitted `SHARE_TOKEN_ENCRYPTION_KEY`, which is required and validated for length. The file
could not have been reconstructed from either document.

**Scope**: `docs/local-stack.md` — bare-metal and Podman values side by side, what each generated
secret costs if lost, the two trust boundaries that explain why the gateway needs an admin key at
all, and a symptom-to-cause table. Both `.env.example` files corrected and told which reader they
belong to. The README's auth-service table rebuilt against the actual field list. Nothing about
the secrets' values is written down anywhere.

**And a guard, because a runbook rots**: `test_settings_are_documented.py` fails when a setting
the service refuses to start without is missing from the README or the example, when the README
documents a name the service does not read, and when the example again tells the reader to match
the repo-root `.env`. The first two had three offenders each when written.

**What made this expensive, and it is the session's own lesson**: the process that could not be
replaced was holding its RSA signing keys from a path that no longer exists, and nothing said so
until it was killed. Checking `lsof` for what a process actually opened — before killing it — is
now in the runbook, because "it restarted fine before" only meant it had always been restarted
from the one terminal that could.



## PRM-148 — The requirements corpus comes back, and stays

**Why**: found in the architecture review. The source carries 288 `Implements:` comments naming
the spec each piece satisfies and the acceptance criteria it meets by number — `AC-8`,
`AC-27`. **218 of them pointed into `memory/specs/`, which does not exist.** Commit `546a196`
removed the SDD pipeline and the comments were never updated, so for months every acceptance
criterion the codebase claimed to implement was unverifiable, and `CLAUDE.md` instructed the
reader to reference a `memory/decisions/` that had gone with it.

It went unnoticed because **a comment cannot fail.** Nothing in the build, the linter or the
test suite reads a path inside a comment, so 218 authoritative-looking citations decayed in
silence.

**Restored, not rewritten.** 24 specs and 7 architecture decision records came back from
`546a196^` at their original paths, which makes all 288 citations resolve without touching a
line of code. Rewriting 218 comments to a new location would have been churn with no gain.

Two things deliberately left out: `memory/wiki/` (12 files the user had already said were not
needed) and `memory/roadmap.md`, superseded by the two-file index this project uses now.

**And the restored corpus needed correcting before it could be trusted**, which is the same
class of problem it was restored to fix:

- Its README declared the SDD lifecycle as live process — *"no feature is implemented without a
  spec in approved status"* — which `546a196` retired. It now says what it is: a historical
  record, with the live workflow named.
- Its index linked `006-multi-model-routing.md`; the file is `006-multi-model-gateway.md`.
- There is no spec 019. The number was skipped, nothing is lost, and the README now says so
  rather than leaving a gap that reads as a deletion.

**Scope**: `scripts/check_spec_references.py` parses every `Implements:` in the source and exits
non-zero naming each path that does not resolve, wired as its own pre-push phase because the
check is repo-wide rather than any one service's. It also fails when it finds *no* references at
all, so a broken scan cannot pass as a clean result. The hook's phase counter said `/7` while it
ran ten phases; corrected while adding the eleventh.



## PRM-149 — The architecture decisions say when they were last true

**Why**: `PRM-148` restored the requirements corpus and marked the *specs* as a historical
record, but left the 7 architecture decisions reading as live authority. The user caught it:
those records come from an initial design and the platform has evolved, so they must be read
critically rather than followed.

They were right, and the cost had already been paid. The first draft of `PRM-134` justified
moving the node registry partly by citing `manager-owns-registry`'s rejected-alternatives
table — *"Shared database (SQLite/Postgres) as registry — operational overhead"* — as a live
constraint. **The manager itself now uses SQLite.** That rejection had been overtaken by the
platform's own evolution, and nothing in the document said so.

**Audited all seven against the running code.** Two were current; five were not:

| ADR | Verdict |
|---|---|
| `rs256-jwt` | Current — a signing-key rotation that day was picked up via JWKS with no config change, which is the property it was decided for |
| `canonical-project-dir` | Current — `install-rhel.sh` uses the path as written |
| `llama-cpp-bare-metal` | Premise superseded: seven engines now, not one. Mechanism holds — engines run as host processes, never containerised, and Metal and direct GPU access are still the reasons |
| `openai-api-compatibility` | Deliberately amended: it claims the exact OpenAI shape for *all* inference endpoints, and `PRM-136` added one that is not |
| `podman-over-docker` | Over-claimed: "all environments" is false — the dev stack is bare-metal and zero containers were running. `PRM-147` is what that gap cost |
| `redis-for-state` | Half current, and the other half is a defect: idempotency moved to the database on purpose, while `BackendPool._in_flight` is a dict in the process — a real violation, still open |
| `manager-owns-registry` | Principle holds, three specifics obsolete: the artefact is SQLite not YAML, the DB rejection is dead, and the diagram says `:8000` |

**Scope**: a dated review note at the top of each record, and `memory/decisions/README.md`
naming the vocabulary. **The decisions themselves are not edited.** An ADR that was made *was*
made; rewriting it to match the present destroys the only record of why the code looks the way
it does, and deleting it makes the code look arbitrary. That is the standard ADR lifecycle and
this repository now states it.

**And a guard, because the failure mode here is silence.** `scripts/check_spec_references.py`
grew a second check: an architecture decision with no `Reviewed <date>` line fails the push.
The reasoning is that an unreviewed ADR reads exactly like a true one — five of these were
wrong and not one of them said so — so the absence of a review date is the only available
signal that nobody has looked. Verified by adding an unreviewed record and confirming exit 1.



## PRM-150 — `ready` means a model answered, not that a port is open

**Why**: `scanner._probe_health` returns `ready` when a health endpoint answers 200. That is
**liveness**, and it had been reported as readiness. The industry separates the two and has for
years — Kubernetes has `livenessProbe`, `readinessProbe` and `startupProbe`; the Open Inference
Protocol (KServe v2, Triton) serves `/v2/health/live`, `/v2/health/ready` **and
`/v2/models/{name}/ready`**, per model, distinct from the server.

The gap is not theoretical: several engines answer `/health` before the weights are loaded,
because they load on the first request. The process is alive, the port is open, and the first
real caller is the one that finds out. `hf-serve` will start with a `--task` the checkpoint
cannot perform and fail only when asked to do it.

**And there was a second half, which is the one that made it a lie rather than a gap.** The
`{model_id}.error` marker already existed and already carried a reason — but `routes.py` only
consulted it **when there was no live process**. A running instance with a marker still reported
`ready`. So even a correctly diagnosed failure could not reach the dashboard.

**Scope**: `prometheus_manager_core.readiness` sends one real request of the model's own modality
after health goes green, retrying three times over ~6s because a lazy load can still be in
flight. On failure `start_instance` writes the marker with the engine's own words, leaves
`discovery` false so the gateway never routes there, and **leaves the process running** — a model
that took minutes to load should not be discarded over a probe, its log holds the real error, and
an operator can stop it deliberately. It returns the state either way, because the process did
start: whether it is *usable* is now a separate fact, which is the whole distinction. `routes.py`
reads the marker for live processes too.

**What it catches, and what it does not.** It catches a task the checkpoint cannot perform,
weights that fail a lazy load, and a request schema the engine does not accept — which this
project hit for real with `laya`, whose options go in `criteria` and whose `instructions` is
required. It does **not** catch a model that answers correctly-shaped nonsense: `laya`'s
miscalibrated checkpoint returned a structurally perfect response while its own library logged
that the confidences were unusable. The roadmap entry that filed this item claimed otherwise, and
that was wrong — no single request distinguishes miscalibration from health, and saying it does
would be the same defect this codebase keeps finding.

**Image generation is excluded deliberately.** Every other probe costs milliseconds; generating an
image costs seconds of GPU on every start, and the only failure it would catch is one `/health`
already catches. `image` returns `skipped`, and `skipped` is a third outcome rather than a pass —
`ready` and `failed` are both False — so no caller can read "no probe ran" as "the model works".

**Not billed.** The probe serves no caller and never touches the gateway. PRM-142 established the
rule in the other direction; this is the same one.

**Verified**: 19 tests for the probe itself, including that `skipped` is not a pass, that a
transport failure is a verdict rather than an exception, that it retries and stops on success, and
that the `typed_decision` body is the shape laya actually accepts rather than the one its README
shows. Two more in manager-api for the half that made it visible: a live process with a marker now
reports `error` and keeps its pid, and one without a marker is still `ready`. A guard asserts every
modality is either probed or explicitly skipped, so a new one cannot arrive mute — the same
pattern as the engine and price guards.

**What the two existing lifecycle tests had to learn**: a start that is merely healthy no longer
clears the error marker or sets `discovery`. Both were updated to answer the readiness probe too,
which is the contract changing rather than a test being appeased.

## PRM-151 — Liveness has an age, and the cordon is the operator's alone

**Why**: `is_active` was decided by the coordinator probing each node, so a probe that failed and
a node that was genuinely down produced the same answer.

**And looking for that turned up a real bug, which is now the larger half of this item.**
`/deactivate` and the probe wrote **the same column**. So:

```
operator deactivates lab for maintenance   -> is_active = False
somebody presses Check (lab is up)         -> is_active = True   ← the cordon is gone
```

Verified live against the running stack before the fix. Two different facts shared one field, and
the observed one silently overwrote the operator's intent. Kubernetes keeps exactly this pair
apart — `spec.unschedulable` is intent, `status.conditions[Ready]` is observation — and nothing
observed may write the operator's half.

**Scope**: `enabled` (the cordon, written only by `/activate` and `/deactivate`) and
`last_seen_at` (an ISO timestamp, written only by a sighting), with `is_active` **derived**:
enabled *and* seen within a 60s TTL. A timestamp rather than a boolean because a boolean cannot
say *when*, and "a probe failed once" and "nothing has answered for an hour" need different
actions. The wire shape reports all three, so a caller can tell "cordoned" from "not answering" —
which one boolean could not.

`FleetSweep` probes every node every 15s and stamps the ones that answer, writing nothing for the
ones that do not: the previous sighting stands and the TTL decides, so a single missed sweep is
not an outage. Started from the app lifespan and only where `app.state.fleet` exists, so a plain
node sweeps nothing. The TTL is four sweep intervals, the same small multiple Kubernetes uses for
its node lease (10s renewal, 40s duration).

`POST /v1/fleet/nodes/{id}/heartbeat` accepts the push direction now. It stamps `last_seen_at` and
cannot set `enabled` — a node reporting in does not get to overrule an operator, and a cordoned
node should keep reporting so the operator can see it is healthy before lifting the cordon. **A
heartbeat from an unknown node is a 404, not an auto-join**: a misconfigured node with the wrong
name or URL must not silently start receiving traffic, which is why Kubernetes gates registration
behind CSR approval. The attempt is logged so an operator can see a node trying to join.

**What this does not deliver, and the honest reason.** The item was filed as "liveness by
heartbeat, not by a probe from the centre", and liveness is still a sweep from the centre.
`manager-api` has JWKS configuration to *validate* tokens and no client credentials to mint one,
so a node cannot authenticate to the coordinator at all. That is an operational decision — one
OAuth2 client for the fleet or one per node — and inventing it here would have been a design
choice smuggled in as an implementation detail. Filed as PRM-152. Both paths already write through
the same `mark_seen`, so the day nodes report in, the sweep can be switched off and nothing else
changes.

**A contract change worth naming**: a ported test asserted that a failed Check marked a node
inactive immediately. It no longer does — a failed probe writes nothing and the TTL decides, which
is what makes one missed probe stop being an outage. The test now pins the new behaviour and says
why, rather than being adjusted to pass.

**Verified**: 12 tests for the two facts and the TTL, including that a sighting cannot lift a
cordon, that a cordoned node still records sightings, and that a `fleet.db` written by PRM-134
gains both columns without a wipe — `enabled` defaulting to true, because every existing row was
registered by an operator who wanted it. 9 more at the API level for the cordon sequence, the
heartbeat, the unknown-node refusal and the sweep. Live: the coordinator restarted, the migration
ran on the existing database, the sweep stamped both nodes, and the original bug is gone — Check
now advances `last_seen_at` and leaves `enabled` alone.


## PRM-152 — A node needs a credential to report in

**Why**: PRM-151 built `POST /v1/fleet/nodes/{id}/heartbeat` and no node can call it.
`manager-api` holds a JWKS URL for *validating* incoming tokens and has no client id or secret to
obtain one, so the fleet's liveness is still a sweep from the coordinator — which cannot
distinguish "the node is down" from "I could not reach it", the ambiguity PRM-151 set out to end.

**Decided: one client per node.** The alternative was one shared by the fleet — one secret to
rotate, no per-node bookkeeping, and any holder able to report for any node. What settled it was
not the blast radius, which is modest once the scope is narrow: it is that PRM-157 had just landed
and records `actor_client_id` on every action, so a shared credential makes everything any node
does indistinguishable in the audit trail from the first day. The heartbeat is also not the last
thing a node will authenticate for, and a shared credential is harder to walk back later than to
get right now with two nodes. Kubernetes' NodeRestriction and Consul's per-agent
`node "web-01" { policy = "write" }` are the same answer. Rejected: a bootstrap token exchanged
for a per-node identity, which needs the coordinator to create registrations in auth-service —
the cross-service admin coupling PRM-134 removed from the fleet path.

**The sharper problem, found while reading**: the heartbeat required `backend-registry:write`. That
scope registers, cordons and deletes any node and starts and stops instances on every manager, so
the credential a node needed to say "I am alive" also handed it the fleet. Two new scopes replace
it — `fleet:heartbeat` for what it may do, `node:<id>` for who it may do it for — and
`assert_may_heartbeat` refuses a report the token does not name. `node:<id>` is pattern-matched
rather than enumerated for RM-07's reason: node ids live in the coordinator's registry, and a copy
here would be a second answer to "which nodes exist".

**And a node cannot derive its own identity** — the coordinator assigns a UUID at registration. It
is configured with it (`PMGR_FLEET_NODE_ID`), alongside its client id and secret and never in
`manager.toml`, which this repository tracks: `FleetConfig` exposes the three as properties over
the environment, so a `client_secret` written into the TOML fails at startup instead of being
committed. Rejected: a second endpoint keyed on node *name* (two ways to name one node) and
discovery by matching its own `manager_url` against the registry (a second identity key that can
disagree).

**The coordinator does not call itself.** It is a node and needs `last_seen_at` like the rest, but
it owns `fleet.db` — so it stamps its own row in process. No HTTP to itself, no credential issued
for a service to authenticate to itself.

**The sweep is now behind `[fleet] sweep`, on by default.** Off is the end state rather than an
option: while a probe also stamps `last_seen_at`, "the node is down" and "I could not reach it"
stay indistinguishable, which is the ambiguity PRM-151 set out to end. On by default so a fleet
upgraded to this code does not lose its liveness before its nodes are registered.

**Verified live, end to end.** `local` logged `fleet.heartbeat_started local=true` and kept its own
row fresh in process; `lab` logged `local=false`, minted one token and reported over HTTP, which the
coordinator's access log shows as `POST /v1/fleet/nodes/8ed6…/heartbeat 200`. With
`[fleet] sweep = false` and no `sweep_started` line at all, both rows keep advancing — so the
liveness now on record is what each node said about itself.

**And the refusal, at both layers.** Asked for a token naming `local`, auth-service answered
`400 invalid_scope: Scope(s) not permitted for this client`; using the token it legitimately holds
to report for `local`, the coordinator answered 403 naming the grant it needs and the one it has.
Either layer alone would be enough; both is what makes a compromised node's reach its own row.

Setting it up is the operator's: registering a client writes a secret. `docs/local-stack.md`
carries the commands, where the credentials go, and the `--env-file` the launch now needs — a
manager started without it serves inference and reports nothing, which with the sweep off means
nothing reports it at all.


## PRM-153 — Alembic in auth-service

**Why**: the architecture review found three services using three different migration strategies —
the gateway on Alembic with ten revisions, auth-service on `create_all` plus a list of
`ALTER TABLE ... ADD COLUMN` statements run on every boot with the exception swallowed, and
manager-core on `CREATE TABLE IF NOT EXISTS` with PRAGMA-guarded ALTERs. The gateway does it
properly and the other two do not.

**And there is a concrete pending need rather than a principle.** That list is additive *by
construction*: it can add a column and cannot express a drop, a rename, a type change or a
constraint. PRM-134 moved the node registry out of auth-service and left the `nodes` table behind,
and this service had no mechanism capable of removing it. The review had ranked
`_in_flight` → Redis first; checking showed the gateway runs a single process, so that one is
future debt while this one is blocking a cleanup that already exists.

**Scope**: `alembic.ini`, `migrations/env.py` and an autogenerated baseline describing what every
existing database already contains, plus `_migrate_to_head` with the same three cases as the
gateway's RM-68 — new database runs every revision, pre-Alembic database is lifted to the baseline
shape and **stamped** rather than having the baseline run against tables that already exist, and an
adopted database is upgraded. Without the middle case the first real migration would refuse or
destroy a database holding live principals.

The frozen ALTER list stays, for exactly one job: lifting a pre-Alembic database to the baseline,
because `create_all` builds missing *tables* and never touches an existing one's columns. It takes
no new entries, and a test asserts every statement in it is an `ADD COLUMN`.

`env.py` is copied from the gateway rather than shared. Two ~60-line files differing in their
metadata and one environment variable do not justify a migration framework for two services; that
they behave alike is asserted by tests instead.

**Not in this change**: dropping the dead `nodes` table. It is PRM-134's rollback path and PRM-134
merged hours earlier — a rollback path deleted the same day it was created is not one. Filed as
PRM-154, where it also becomes the first revision that proves this mechanism does what the old one
could not.

**Verified**: 7 tests covering all three cases, including that a legacy database with a live
principal keeps its row and gains the columns the frozen list adds, that adoption stamps the
baseline rather than head, and that a second run changes nothing. One test compares the migrated
schema against `Base.metadata` table by table, which is what catches a model changed without a
revision — a change that works on a fresh database and breaks every existing one. Then on a copy of
the real database (14 principals, 2 share tokens, 2 nodes — all intact, stamped), and then on the
real one, after which login, Nodes, Instances, Users, the catalog and a live inference all still
answer.


## PRM-154 — Drop the two things their moves left behind

**Why**: PRM-134 moved the node registry to the fleet coordinator and deliberately left
auth-service's table and rows in place as the rollback path. PRM-153 then gave auth-service a
migration mechanism that can remove a table, which the previous one could not.

Doing both on the same day would have removed the rollback path while the change it protects was
still hours old.

**And PRM-155 added a second one**: `fleet.db`'s `is_active` column, superseded by `enabled` +
`last_seen_at` and kept so a rollback of PRM-151 reads something sane. Same reasoning, same day,
same decision to wait — and it is now expressible, which it was not before PRM-155.

**Scope**: one revision dropping `nodes`, the `Node` model and `NodeType` out of
`auth-service/db.py`, and the six `ALTER TABLE nodes` entries out of the frozen additive list —
that last one is safe only because the table will no longer exist for a pre-Alembic database to be
lifted into, so it has to happen in the same change as the drop, not before it.

**What went with the model**: the three node request/response schemas and the engine-id validator,
orphaned when PRM-134 removed the routes that used them, and the three cost defaults, which are a
node's properties and which the coordinator already carries its own copies of.
`scripts/migrate_node_registry.py` went too — it read the table it was copying out of, so the drop
makes it inert rather than merely unused. A node row touched no principal, no token and no scope;
this service now owns only security.

**The guards were rewritten, not deleted.** `test_nodes.py` asserted that `nodes` must *still be
here* and named the reasons: the rows were the way back, the copying script deleted nothing, and
the mechanism of the day could not drop a table anyway. All three are spent, so the same test now
holds the drop in place — a second writable node registry is worse than either service owning it —
and a companion asserts the model is gone too, because a table dropped while its model survives is
rebuilt by `create_all` on the next adoption.

## And a rule in PRM-155 that only a subtractive migration could expose

`schema.apply` stamped every unstamped database at the baseline, and its docstring said so
deliberately: the schema script runs unconditionally with `IF NOT EXISTS`, so "both paths arrive at
the baseline shape before this is called" and `had_existing_tables` was recorded for an operator's
benefit and decided nothing.

That is true exactly while every migration is additive. Head and the baseline then differ only by
columns the script creates anyway, so running the chain over a brand-new database changes nothing.
**A migration that removes something breaks it**: a new database stamped at the baseline is told to
drop a column it was never given, which fails and takes down a first install.

So `had_existing_tables` became load-bearing — tables present means a pre-mechanism database that
has seen nothing since the baseline, absent means one the schema script just created at head — and
the obligation that makes it correct is now written down: **a schema script must describe head**.
It is the same contract Alembic puts on `create_all` + `stamp head`. The test that asserted the old
rule passed vacuously on an empty chain, which is why the rule survived to be found here; it now
carries the case that breaks.

**Verified live.** auth-service restarted, logged
`Running upgrade f7060a106063 -> a1d4e77c0b52`, and its 19 principals and token issuance came
through untouched with `nodes` gone. The coordinator restarted, logged
`schema.migrated chain=fleet version=2`, and `fleet.db`'s columns no longer include `is_active`
while both rows kept their `enabled` and `last_seen_at` — and `to_dict()` still reports
`is_active=True` for both, derived, which is the whole reason the stored copy had to go. The
gateway's catalog stayed at 10 models across both drops. The two rows and both databases were
copied to gitignored files first.



## PRM-155 — manager-core's schema changes come under version control

**Why**: the third of the three migration strategies the architecture review found. `registry.db`
and `fleet.db` were kept in shape by `CREATE TABLE IF NOT EXISTS` plus `ALTER TABLE` guarded by a
`PRAGMA table_info` check.

That is schema **inference**, not migration, and it has three consequences. Nothing recorded what
had been applied — `registry.db` had no version table at all, so no file could say which changes
it had seen. The guards accumulate for ever, because each is the only evidence its change exists.
And there was no transaction around "make the change and record it", so **a change interrupted
half-way left no trace of how far it got** — which on a schema that re-infers on the next startup
means indistinguishable from never having run.

**Not Alembic, and the review was wrong to assume it.** It recommended "Alembic in auth-service and
manager-core", and checking showed manager-core talks to SQLite through the stdlib `sqlite3` module
with **no SQLAlchemy dependency at all** — deliberately, for a component that runs on every node.
Alembic would add SQLAlchemy and Alembic to it, and without declarative models there is no
`--autogenerate`, which is most of what Alembic buys. What would arrive is the machinery and not
the benefit. So the same property is built with the tools the module already has: ~150 lines, no
new dependency.

**Scope**: `schema.py` with numbered `Migration` records, a `schema_version` table keyed by chain,
and `apply()` running each pending migration inside a transaction **with its own version bump**, so
a database is always at a version it actually reached. Two chains — `registry` and `fleet` — each
in its own database. `validate()` refuses a chain that is not strictly ascending from the baseline,
because a duplicated or out-of-order version applies different statements to different databases
depending on how far each had got.

Both chains are **empty today**, which is the honest state: the PRAGMA-guarded backfills are the
baseline, and the one column that wants dropping — `fleet.db`'s `is_active`, superseded by
`enabled` + `last_seen_at` — is PRM-151's rollback aid from the same day. Filed with PRM-154.

**One difference from Alembic worth stating.** An unstamped database is stamped at the baseline
whether it was created a moment ago or has been in use for months. In Alembic the baseline
*revision holds the DDL*, so a new database runs it and an existing one is stamped to avoid running
it twice; here the schema script runs unconditionally with `IF NOT EXISTS`, so both paths arrive at
the baseline shape first and stamping is all that is left. `had_existing_tables` therefore controls
nothing — it is recorded so an operator sees, once, that a database already in use came under
version control.

**A real defect the tests caught.** The first version rolled the *version* back on a failed
migration and left the DDL applied. Python's `sqlite3` opens a transaction implicitly for
INSERT/UPDATE/DELETE and **not for DDL**, so `ALTER TABLE` ran in autocommit and could not be rolled
back — exactly the half-applied state this mechanism exists to prevent, reproduced inside the thing
meant to fix it. An explicit `BEGIN` fixes it and the test that found it stays.

**Verified**: 15 tests, including that a migration can `DROP COLUMN` and move data while changing
shape — neither expressible before — that an applied migration does not run twice, that a failed one
leaves the previous version with nothing applied, and that a later run retries it. Then on copies of
the real databases, and then on the real ones: `registry.db` 31 models and 10 instances,
`registry-lab.db` 4 and 4, `fleet.db` 2 nodes, all stamped, `adopted: true` logged for all three
chains, and the dashboard, catalog and a live inference still answering.



## PRM-156 — Least-loaded routing reads the backend's own load

**Why**: the architecture review's fifth finding. `BackendPool._in_flight` is a plain dict, so with
several gateway replicas each sees only the requests it sent, and least-loaded routing degrades to
per-replica balancing with nothing to indicate it.

**Not Redis, and the review proposed Redis.** That is the second time in this block its
recommendation was the wrong tool — the first was Alembic for manager-core — and the pattern is
worth naming: the review identified the defects accurately and reached for the mechanism already in
the codebase instead of asking what the problem wanted.

Envoy's `LEAST_REQUEST`, Linkerd and Finagle all keep least-request counts **proxy-local** and
mitigate the partial view with power-of-two-choices. None of them shares a counter, for two
reasons: it costs a round trip on the selection path, and it leaks for ever when a process dies
between acquire and release — an in-process dict at least dies with the process that owns it.

**And there is something better than either here.** `BackendHealthMonitor` already polls `/slots`
to learn a backend's capacity, and the same response reports, per slot, whether it
`is_processing`. The busy half was being discarded while the gateway counted its own requests
instead. The backend's own count is ground truth, shared by construction across any number of
replicas, and impossible to leak because it is not the gateway's to leak.

**The signal is the larger of the two, because they are stale in opposite directions**:

- the reported count is up to one health-poll interval old (10s by default), so it misses requests
  this process has just sent;
- the local count misses every request any *other* replica sent.

`max` never underestimates, and underestimating is what makes a backend look emptiest exactly when
it is not. It also damps the herding a shared signal would otherwise cause with a strict
`argmin` selection: a replica's own count rises the instant it dispatches, so it stops choosing
that backend without waiting for the next poll. Power-of-two-choices remains the textbook answer
if that turns out not to be enough, and is deliberately not added on speculation.

`None` and `0` stay different: a backend that does not report — sd.cpp has no `/slots` route — is
absent from the figure rather than recorded as idle, which would make a silent backend look like
the emptiest in the fleet and send every request to it.

**Scope**: the busy count read in `_read_slots` and pushed in beside capacity, `set_reported_busy`
and `reported_busy` on the pool, and `load_ratio`'s numerator. RM-72's normalisation by capacity is
unchanged, so least loaded still means least loaded relative to what a backend can take.

**Verified**: 10 tests, including two pools standing for two replicas agreeing on which backend is
busier — the ranking they disagreed about before — and that a `release` lowers only the local half,
which is the leak a Redis counter would have added. Live against llama.cpp on :8084, polled through
a 400-token completion: `0/4` busy, then `1/4` for the duration, then back to `0/4`.



## PRM-157 — An audit log with an actor, and Argus gets a copy

**Why**: one of the three gaps the architecture review said decide whether this platform can be
sold at all. There was no record of who changed a price, started an instance or revoked a client —
and until PRM-134 it was impossible even in principle, because the gateway spoke to auth-service as
a blanket admin and every action arrived as "the gateway".

**The row is the record. Argus gets a copy.** Not the other way round, and the reason is not a
preference: an observability pipeline is lossy **by design** — sampled at the collector, retained
for weeks, exported best-effort — so a trail that may drop an event is not an audit trail.
Kubernetes draws the same line, with the API server writing to its own backend and whatever scrapes
the cluster as a consumer. The order in the code is literal: the row is written, then the emission
is attempted. If the emission fails the record survives; if the row fails, that is logged as loudly
as this codebase knows how, because a gap in the trail is the one thing an operator must not learn
about later.

**One door, not twenty.** Recorded in the `request_id` middleware after `call_next`, where the route
template, the resolved claims and the real status are all known. There are more than twenty mutating
`/admin/api` handlers, and a rule that must be remembered at each of them is the defect this
codebase keeps meeting: PRM-142 was five handlers forgetting to read a status, PRM-131 five
forgetting a metric input.

**No request body is ever stored, with one narrow exception.** `/admin/api/auth/login` carries a
password and a secret rotation *returns* a secret, so a log that kept bodies would be the largest
credential store in the platform. The action and the path parameters say what was touched. The
exception is `actor_email` on the login route, which records itself from the handler that had
already parsed the body: "who tried to sign in" is the most audited fact in any system and an email
is an identifier, not a secret. Failed attempts are recorded as carefully as successful ones,
because a run of failures against one address is the pattern an auditor looks for and a log of
successes cannot show it.

**The action is a route template**, `POST /admin/api/nodes/{node_id}/deactivate`, never the resolved
path — which would make every id its own action so nothing could be counted or alerted on. The id
goes in `target`, separately.

**A read is not audited.** Recording every dashboard poll would bury the writes in noise, which is
how audit logs stop being read.

**The Argus integration, and the half their conventions do not cover.** `argus.event`,
`argus.outcome`, `argus.component.role`, `argus.feature` and `argus.tenant` are used as they stand,
and the outcome word is taken from their tuple rather than retyped so a change on their side is an
import error here instead of two systems disagreeing about what "ok" means. What `argus_semconv`
1.0.0 has no attribute for is **the actor**, which for an audit event is the entire point. Rather
than inventing names in their namespace, the actor and action go out under `prometheus.audit.*` and
four attributes are proposed to them in channel entry P-31 — the PRM-144 lesson applied to
ourselves, since a private vocabulary standing in for a missing contract is exactly what we asked
them not to make us do. It reaches them as a span **event** on the server span that already exists,
not a new span, which would double every admin request in their trace view for no information.

**Readable with `admin:read`**, not `admin:write`: requiring write to see who wrote would mean only
the people who can alter the system are able to check it.

**Two things the tests caught.** The migration was missing the guard every post-baseline revision
here needs — the pre-Alembic adoption path runs `create_all` from today's models, so the table
already exists — and `db.py`'s own comment had warned about exactly that. And the test for "a failed
row does not fail the action" originally patched out the function that *contains* the guard and then
asserted the guard worked; it now breaks the database instead.

**Verified**: 15 tests, including that a password never reaches the table and that a denied change
is recorded. Live: a successful login, a failed login and a real node change all landed, with the
trail readable through the API and the table confirmed to contain neither password.



## PRM-158 — Admission control: refuse the last arrivals so the rest stay answerable

**Why**: the architecture review called this the most serious of the three gaps that decide whether
the platform competes, because it is the one that appears under real load — which is exactly when a
product is evaluated. A burst arrived in full, the rate limiter counted requests without knowing how
full the engine was, and a saturated GPU turned latency into timeouts for **everybody** instead of a
clean refusal for the last arrivals.

**It bounds; it does not queue.** The engine already has a queue — llama.cpp has slots and a pending
list, vLLM has `max_num_seqs` — so a gateway queue would move the wait and give it a second place to
be accounted for. What was missing is a limit on how deep we let the engine's own queue grow, which
is what Envoy's `max_pending_requests` is.

**The signal already existed.** PRM-156 taught the gateway to read `is_processing` per slot, so it
knows both a backend's capacity and its real load. Admission control is one comparison on numbers
that were already there, and it deliberately uses the *same* view of load that selection uses —
a second view would mean the two disagreeing about which backend is full.

**Where it lives.** `_healthy_members` returns "members that can take a request right now", and a
saturated backend cannot, so saturation sits beside `unreachable` and `circuit open` rather than
becoming a sixth check on each of the five forwarding handlers. That is not a conflation: all three
answer the same question. It also means a saturated replica with an idle sibling is a **routing**
decision, not a refusal — the request goes to the sibling, which is the whole reason replicas exist.
The 503 happens only when every replica is full.

**Its own error type.** "Every replica is down" and "every replica is busy" need different actions —
page somebody, versus back off — so they cannot share a type. `backend-unavailable` already documents
two causes that only a `Retry-After` distinguishes; a third would leave a client unable to tell a
broken model from a busy one. `503 capacity-exhausted` carries `Retry-After: 1`, a hint rather than a
promise: a slot frees when a request finishes and how long that takes is the model's business.

**Off by default, and that is the decision.** `ADMISSION_HEADROOM=0` limits nothing. Turning a limit
on by default would start refusing traffic on an existing deployment the first time it restarted, on
a number nobody chose for it. 2.0 is the value to start from, and it is a deployment decision because
the right headroom depends on request duration — a 200 ms embedding tolerates a deep queue, a
40-second completion does not.

A backend that reports no capacity is never refused on this basis. sd.cpp reports none, so there is
nothing to bound against, and inventing a number would refuse real traffic on a guess.

**The default nearly shipped inverted.** `load_ratio >= 0.0` is true for every backend that reports
capacity, so the first version refused *every* request whenever admission control was off — which is
the default. The test written to assert "zero disables it" is the only reason that is not in
production.

**Verified**: 8 tests, including that an idle sibling wins over a saturated one, that a full group
returns `capacity-exhausted` naming both replicas, and that behaviour is unchanged while disabled.


## PRM-159 — The SDK guide stops existing twice

**Why**: found while documenting PRM-158's new error, by a guard that already existed. A test asserts
every error type the gateway raises appears in the guide's catalog; it failed on
`capacity-exhausted`, as designed. Fixing it surfaced something worse.

**The guide exists twice** — `docs/sdk-integration-guide.md` under version control, and a copy in the
shared channel directory the SDK team vendors — and they had drifted **170 lines**:

```
docs/ (in git)     revision 2026-09-19b   §3.10: no    payload_schema: no
channel directory  revision 2026-09-26    §3.10: yes   payload_schema: yes
```

Every guide edit from PRM-142, PRM-144 and PRM-145 had gone **only to the channel copy**. The one
under version control was a week stale.

**And the drift was invisible in the worst direction: the guard reads the repo's copy.** So the
guarantee "every error the gateway raises is documented" had been holding true of a file consumers do
not read. A guard checking the wrong artefact is worse than no guard, because it is reported as a
pass.

**Scope**: the repo's copy brought up to date, and a second check in
`scripts/check_spec_references.py` that fails the push when the two differ, printing the exact `cp`.
It says nothing when the channel directory is absent, since that is somebody else's machine rather
than a failure. Verified in both directions.

This is `A-17` again — *"the change has leaked into §3.8, where you did not announce it"* — and the
same root cause the four teams keep meeting: **a fact that exists twice starts disagreeing, and it
shows up first where nobody is looking.** Told to Axonium in P-27 rather than quietly fixed, with the
offer to serve the guide from one place instead of copying it by hand.



## PRM-160 — One name, several models, with weights

**Why**: the last of the three gaps the architecture review said decide whether this competes.
Replacing a checkpoint was a cut — one public name resolved to one model, so pointing it somewhere
new moved every request at once and a regression was discovered by all of the traffic rather than by
a tenth of it. With seven engines and models that move weekly (Laya published 0.3.2 through 0.3.7 in
four days) that is not hypothetical.

What the industry calls this: SageMaker's **production variants** with `InitialVariantWeight`,
KServe's `canaryTrafficPercent`, Istio's weighted clusters. All the same shape — a route fans out to
real, separately deployed things, with weights.

**A new checkpoint is a new model, not a version of one**, and that is RM-70's rule rather than a new
one. There, a rename is a new model because `model:<slug>` grants and usage rows have to keep meaning
something. A different checkpoint prices differently, performs differently and fails differently, so
usage blended under one name would be two things averaged into a figure that looks plausible and
describes neither.

**Which is why billing needed no changes at all.** PRM-113 separated *the name the caller sent*
(`model_slug`) from *the id that bills* (`model_id`), and until now those were always the same model.
A traffic split is precisely the case that separation was built for — verified live on a 50/50 split:
every row carries `model_slug=chat` and a `model_id` of whichever variant served, at that variant's
rate.

**What the caller sees.** The request is unchanged. `model` in the response is still the name they
sent, never the variant — RM-77 settled that the body names the model asked for and not the thing
that served it, and a canary must not change what a response says. `X-Prometheus-Variant` names the
variant, when a split applied and only then: the same idea as `X-Prometheus-Instance` one level up,
which says which replica where this says which model.

The scope check still runs against the requested name, so a `model:<public-name>` grant covers every
variant behind it. That is deliberate: the caller was authorised for the name, and which checkpoint
answers is an operator's decision rather than a change in who may call it.

**A broken canary looks broken.** When the chosen variant has no usable replica the request fails
naming that variant; it does not fall back to the stable one. Falling back would mean a canary can
never fail its rollout — it would take traffic, be unable to serve it, and report perfect health
while the other variant carried everything. A 10% variant that is down produces 10% failures, which
is the signal a rollout exists to read (RM-98 again).

**Refused where it is written, not where it is used.** A split with no variants, a weight of zero or
less, a duplicated model id, or a variant this gateway does not have is rejected by the admin
endpoint. Each of those would otherwise be discovered by whichever share of traffic happened to draw
it. Weights are integers so 1:2 is expressible without anyone making three numbers add to 100, and
the percentage is computed on read rather than stored — a stored share is a second answer that can
disagree with the weights.

**One resolver, and a guard.** All five forwarding handlers go through `_resolve_requested` instead of
`registry.resolve`, and an AST guard asserts none of them resolves a name behind the split's back. A
request that bypassed it would ignore any canary on that name — and five handlers with one rule and
no structure enforcing it is exactly what PRM-142 and PRM-131 each cost.

Held in memory and backed by a table, loaded at startup and updated on an admin write, because
`_resolve_requested` is synchronous and on the request path: a split lookup that awaited a query
would put the database in front of every inference. One unparseable row is dropped with a loud line
rather than taking the table down, since a gateway that refused to start over one bad split would
stop serving every model that has none.

**Verified**: 15 tests including the weight distribution over 20,000 draws and the call-site guard.
Live: a split rejected for naming an unregistered variant, then a 50/50 split that returned exactly
5 and 5 across ten requests with the variant header on each, usage rows billed per variant, the body
still saying `chat`, and the name ceasing to resolve when the split was removed. Documented in the
SDK guide §3.6b and told to Axonium in P-28, including the part that affects them most: the total
cost of a split name is no longer `requests × one rate`.


## PRM-165 — fix: the manager's remaining paths still resolved against the cwd (done)

**Why**: RM-75 anchored `registry.db` to the repo root but stopped there, leaving the same
defect in every sibling path. `resolved_pid_dir` is the one that bites: a manager that writes
a pidfile under one cwd and looks for it under another has lost the instance it started.
`resolved_downloads_dir` re-fetches weights already on disk into a second tree.

**Scope**: `resolved_log_dir`, `resolved_pid_dir`, `resolved_downloads_dir` and
`resolved_ca_bundle` now use RM-75's rule — relative anchors to `_REPO_ROOT`, absolute passes
through. Out of scope, and deliberately so: `resolved_backend_binary` returns bare command
names (`vllm`, `python3`, `mlx_lm.server`) that exec resolves against `PATH`, so anchoring
them would make every non-llama_cpp backend fail to start; a test now pins that. `[server]`'s
own `resolved_binary` was left as dead code here and dropped in PRM-166.

**Verified**: the container is unaffected. The image installs the package non-editable into
`/app/.venv`, so `_REPO_ROOT` is `/app` — exactly the `WORKDIR` these paths already resolved
against, making the change a no-op there; `PMGR_REGISTRY_PATH` was already absolute. 10 new
tests cover each path anchoring to the repo root, staying identical across three cwds
(including `runtime/manager/`, the one that caused RM-75), absolute values passing through,
and backend binaries staying bare. Manager suites green: core 324, api 159, tui 38.

## PRM-166 — drop the manager's dead `resolved_binary` (done)

**Why**: `ManagerConfig.resolved_binary` has had no caller since RM-08 phase 1 moved
lifecycle to per-backend binaries (`a4ed1ca`). PRM-165 left it alone because it was outside
that fix; this closes it.

**Scope**: the property only. `ServerConfig.binary` stays — `resolved_backend_binary` still
reads it for `llama_cpp`. Nothing else changes.

**Verified**: no caller anywhere — no literal reference outside its own definition, no
`getattr`/`hasattr` reaching it, and no route or `to_dict` serialising `ManagerConfig` (all
`to_dict` are on registry/fleet entries). mypy over all four packages and the full pre-push
green.

## PRM-168 — fix: CI was red for 40+ runs because tests read the developer's `.env` (done)

**Why**: `Settings.model_config` points `env_file` at the real `gateway/.env` so the app finds
it from any cwd. Under pytest that silently supplied whatever the developer had configured, so
six fixtures enabling `admin_dashboard_enabled=True` without every field PRM-102's and
PRM-134's validators demand passed on a developer machine and failed in CI, which has no
`.env` — it is gitignored. `.githooks/pre-push` reported green the whole time, which is why
nobody was told: the hook and CI were not running the same thing.

**Scope**: tests only — no production code. An autouse session fixture in
`gateway/tests/conftest.py` neutralises `env_file` for the run, which is the part that stops
this recurring; the next validator someone adds now fails locally too. The six fixtures got
the fields they were missing (`auth_service_share_url` in five, `manager_fleet_url` in three).
`test_every_mutating_admin_route_yields_a_declared_type` was a seventh case of the same cause
with a different shape: it took the bare `settings` fixture, where the dashboard is off and
`create_app` mounts no `/admin/api/` routes, so it found none and read as every declared type
being unproduced — it now takes `admin_settings`. Out of scope: the six fixtures still
duplicate a long kwargs list, which is what let their gaps drift apart; that is PRM-169.

**Verified**: the failure was reproduced first in a worktree with no `.env` — `5 failed, 558
passed, 123 errors`, matching CI's profile — and the same worktree now runs the full
`.githooks/pre-push` green with no `.env` present at all, which is CI's condition. Counted
against the run log, the 123 errors were 182 mentions of `AUTH_SERVICE_SHARE_URL` and 68 of
`MANAGER_FLEET_URL` and nothing else. One failure seen locally
(`ModuleNotFoundError: prometheus_manager_core` in `test_payload_schema`) was an artefact of
running the gateway suite alone rather than through the hook, and does not occur in CI —
checked against the run's own log before treating it as out of scope.

## PRM-169 — One builder for the admin-dashboard test settings (done)

**Why**: PRM-168 repaired six fixtures but left the shape that broke them. Each spelled the
same dozen kwargs out by hand, so a validator added later had to be remembered in six places
and was remembered in none — five had no `auth_service_share_url`, three no
`manager_fleet_url`.

**Scope**: `dashboard_settings(key_file, **overrides)` in `gateway/tests/conftest.py` supplies
every field the dashboard's validators require; each of the six call sites now passes only
what it genuinely needs — a URL its respx mocks are built from, the rate limits it exercises,
a pricing file it reads. The canonical URLs are module constants beside it, since
`test_rate_limiting.py`'s mocks match those literals. Tests only; no production code.

**Verified**: not by the suite alone, which would pass on settings that quietly changed.
Each site's `Settings` was rebuilt from the pre-refactor literals and compared field by field
against what the builder produces: `test_admin`, `test_share_proxy` and `test_rate_limiting`
are identical objects. `test_billing_router`'s two sites are deliberately not — they adopt the
canonical admin URL and key in place of their own spellings, and gain the manager client
fields they had left at `None` — which is inert because nothing in that file mocks
auth-service or the coordinator, and the suite passing confirms it. A grep then confirmed no
hand-rolled dashboard `Settings(...)` remains. Full `.githooks/pre-push` green with no `.env`
present, which is CI's condition.

Append a new row to the table with the next `RM-NN` id and a new `## RM-NN — ...` section
below, following the same shape (Why / Scope). Re-sort the table if the new item's
priority isn't "last."


## PRM-161 — A replay says so in the ledger, not only in a header

**Why**: split out of PRM-100, which delivered everything a caller needs today. A replay records no
usage by our own rule, so its row does not exist — and "no row" is an absence the reader has to
interpret, which was Axonium's objection to the obvious design. A zero-token row carrying
`replay_of` makes it an explicit statement, in the one place anyone already looks.

**Decided, and worth keeping because the reasoning cost two rounds:**

*No fourth `termination_reason`.* The first draft used `"replay"`, and Axonium asked what
`interrupted` would then be — `true`, under the existing derivation, which is false: nothing was
interrupted because nothing was generated. Answering showed the value was in the wrong column
entirely. `termination_reason` says *how a generation ended*; a replay is a different **billing
relationship** to a generation that already ended. So a replay row carries
`termination_reason: "complete"` — accurate, because only complete responses are ever stored for
replay: an error hands its key back, and a stream ending in an error frame is released rather than
stored. Three values, and the `interrupted` derivation is untouched.

*`replay_of` is therefore the discriminator, and it has to reach the CSV export.* With no fourth
value, replay rows land in the `complete` bucket, so counting generations with
`WHERE termination_reason = 'complete'` over-counts — silently, because the number comes out
plausible and nothing fails. `replay_of IS NULL` is the test instead. Axonium asked for a line in
the guide; the export needs more, because its column list is explicit and adding replay rows
without adding `replay_of` would produce a file whose rows the reader cannot filter out — worse than
the ambiguity it replaced, since a doc can tell you what to filter by only if the field is there.
Both ship together or neither does.

Also note `_record_usage` returns early on zero tokens, so this needs a deliberate exception.

**It was filed `blocked` and it was never blocked**, which is worth writing down because of how
that happened rather than for the day it cost.

The reasoning was: adding rows changes row counts, Aeon imputes cost from row counts, and Axonium
declined to answer for them. Aeon answered on **19/09**, in channel entry `A-21`, quoted there in
their own words:

> «No leemos el export. Ni por índice ni por nombre: cero referencias en el repo. Así que las filas
> de replay con cero tokens no nos rompen ningún recuento, porque no hay recuento nuestro que
> romper.»

There is no reader, so there is no format to respect and no count to break. `A-21` is marked
`respondida`, so that answer was read and replied to at the time — and the roadmap kept saying
"still waiting on Aeon" for eight days anyway. On 2026-09-27 that sentence was carried into this
item, unread, and stamped `blocked`.

**Which is this codebase's own recurring defect, committed against itself.** The fact lived in two
places — the channel and the roadmap — and the stale copy was the one acted on. Nothing checks that
the two agree, and nothing mechanically can: the lesson is that a roadmap claim about *another
team's* position is a quotation, and a quotation gets re-read at the source before it decides
anything.

**What Aeon asked for instead, for the day they do connect**: read by column name, aggregate by
`model_id`, display `model_slug`. Tracked on their side as `OBS-007`, still `TODO`. The export
already does exactly that since PRM-115, so there is no migration waiting either.


## PRM-162 — The audit vocabulary becomes theirs, and the event hangs where the facts are

**Why**: PRM-157 emitted the actor under `prometheus.audit.*` because `argus_semconv` had no name
for it, and raised the gap in channel entry P-31 rather than inventing `argus.*` names in someone
else's namespace. A-32 answered, and the answer was better than the request: **three of the four
already existed**, and one of them we were already emitting.

| we proposed | it is | why |
|---|---|---|
| `argus.actor.id` | `user.id` | OTel's registry |
| `argus.actor.email` | `user.email` | OTel's registry |
| `argus.action` | `http.request.method` + `http.route` | **stable**, and already on the span |
| `argus.actor.kind` | `argus.actor.kind` | nothing standard says it. This one is theirs |

Their reasoning is the one this codebase applies to itself: naming something that already has a
stable name is RM-07's mistake in reverse. `argus.actor.kind` takes `user | service | unknown` with
`unknown` **legitimate rather than filler** — in an audit record "not stated" is a fact and has to
be distinguishable from "nobody set this", which is the three-state problem we have brought them
twice (RM-98, PRM-133) and which they avoided at the start this time.

**One subject, whichever acted.** `user.id` carries the user when the dashboard authenticated one
and the machine credential otherwise, and `argus.actor.kind` is what makes that readable — `svc-7`
does not mean the same thing both ways, which was their own argument for the attribute. Which also
removed `prometheus.audit.actor.client_id`: `argus.tenant` already carries the client, and for a
service credential a third copy of the same string is what it would have been.

**And renaming exposed the defect the rename depended on.** The module said the event hangs off the
server span that already exists, *because a second span would double every admin request in their
trace view for no information* — and the code called `start_span`, making exactly that second span
and hanging the event off it. So the event sat on a span with no attributes at all, and A-32's "you
already emit the route template" was true of the request and false of the event. Deleting our
action attribute without fixing this would have lost the action entirely. `trace.get_current_span()`
is the whole fix.

**Two payloads, deliberately different.** The event leaves to its span everything that span
carries — route template, status code, client address, all measured present with the `http/dup`
opt-in. The log line states them itself, because nothing joins a log line to a span: an attribute
the event can inherit is one the log line has to carry.

**`prometheus.audit.target` is what is left.** Neither vocabulary names *what a change was made
to*. It stays under `prometheus.` and goes back to them in P-32, which is the shape A-32 asked for
— a second round rather than four invented names across two namespaces.

**Verified live**: a real failed login through the running gateway emitted
`argus_actor_kind=unknown` (correct — no claims, no client, the email read from the body), with the
action, status and source ip on the log line and a 32-hex trace id; 19 spans reached the Argus
agent for it. And in-process with the opt-in on, the `audit.admin_action` event is on the **SERVER**
span carrying `http.route=/admin/api/auth/login` and `http.request.method=POST`, with no second
span anywhere.

One incidental fix: `test_the_scope_is_argus_own_and_keeps_its_attributes` pinned the package
version as a literal and failed on the bump to `1.0.0a7`. It is about the *attribute* surviving, so
it now reads the installed version — the dependency is `==` pinned, so a change is always a
deliberate edit that a tripwire in that test does not catch.


## PRM-163 — `/metrics` stops being public

**Why**: Axonium found it while answering a question about `/health` and reported it as `A-31`,
measured against the same gateway in the same minute — `GET /v1/backends` without a token is a
`401`, with an ordinary token a `403` naming `admin:read`, and `GET /metrics` a `200`. Verified
here before deciding: per-instance `circuit_state`, `instance_ids`, replica counts,
`dependencies.redis.reachable`, throughput and `jwt_validations_failed`, all with no credential.

AC-21's "no per-user data" was true and beside the point. Aggregate counters are still an
operational map: the circuit state says which replica to aim at and whether it is already failing,
and `jwt_validations_failed` is a free oracle for anyone testing credentials — it says whether their
attempts arrive, without them having to authenticate.

**The shape, one turn on from the one we keep meeting.** Not a truth in two places with one stale:
**a truth in two places with only one protected**, and the protected one is the one the
documentation describes. Guide §6.4 said `/v1/backends` "requires `admin:read`… exposes live circuit
state", so a reader came away with the circuit state being private. It was not. Axonium named it as
`A-27` again — there we accepted that an unauthenticated catalog must not name the engine, and this
is the same argument with more in the payload.

**Scope**: `/metrics` out of `JWTAuthMiddleware.EXEMPT_PATHS` and an `admin:read` check in the
handler, stated where `/v1/backends` states its own so the two say the same thing in the same place.
The rate-limit exemption stays, also matching its twin. `_problem` is imported from `router.py`
rather than hand-rolling a fourth copy of the envelope, which the validation handler above it
already names as a problem.

**The dashboard was the only consumer** and it called `axios.get("/metrics")` plain, with a comment
saying "unauthenticated". It now goes through `rootClient`, whose interceptor attaches the session
token — verified live: a real dashboard login yields `admin:read admin:write` and reads `/metrics`
at `200`. Nothing in the three SDKs reads it; Axonium said so and does not intend to.

**Verified live**: no token `401 missing-credentials` with no `backends` key in the body, an ordinary
token `403` in `application/problem+json`, the dashboard's own token `200`, and `/health` still open
at `200` — that one says only that the process answers, which is why RM-95 suppresses its spans.

Guide §6.4 corrected and the revision stamped `2026-09-28 · PRM-162/163`, with the deprecation note
a consumer needs: until today `/metrics` required nothing, so anything built against that now needs
the scope.


## PRM-164 — The audit target becomes their pair

**Why**: PRM-162 left `prometheus.audit.target` as the one attribute neither vocabulary named, and
raised it in P-32 offering two shapes. A-34 chose the flat pair — `argus.target.type` +
`argus.target.id` — on an argument better than ours: inside a JSON, type and id become one value
again, which is the route-template problem moved one level down. A JSON also forces a reader to
know the shape per route (`node_id` here, `client_id` there), so querying "the object" would have
to enumerate routes. `type` is closed cardinality for grouping, `id` open and opaque for filtering.

**The type describes the id, not the object of the action.** That is what settles the cases where
the two differ: `PATCH /admin/api/nodes/{node}/models/config` changes a node's model configuration
and the id it carries is a node, so the type is `node` — what was done is already in `http.route`.
So the type comes from the path segment the parameter belongs to, singular and lowercase, which is
what makes the value read like the route's resource without being the route.

**Not from the parameter's name, and that is the load-bearing part.** Ours are not a guide:
`{node}` and `{node_id}` are both nodes, and `/admin/api/users/{client_id}` administers a principal
that is very often a person. Their pipeline hashes `argus.target.id` where the type says `user`
(A-34 §2), so the type is what stands between a person's identifier and their store — taking `users`
from the route rather than `client` from the parameter is what makes the rule fire.

**One identifier, one treatment.** `client_id` appears under two resources — `/users/{client_id}`
and `/billing/clients/{client_id}/settings` — so the segment rule would call it `user` in one place
and `client` in the other, and only `user` is hashed. A pseudonym is worth what the least protected
place that subject's identifier appears is worth, so both emit `user`: erring toward hashing a
machine client's id costs a little query convenience, the other direction publishes a person's.
Raised in P-33, because the blind spot is in their rule and `client` is in their own example set.

**A closed set that nothing enforces is not closed**, so a guard walks every mutating admin route
and asserts the produced types are exactly `TARGET_TYPES` — a new route producing a new type fails
there rather than putting an undeclared value in their store, and a declared type no route produces
fails too. It earned itself immediately: it found `client` undeclared (the billing route above) and
`limit` declared for no route.

**And `{action}` is never a target.** `/instances/{model_id}/{action}` is start, stop or restart, so
the last parameter is not an object. It is the only such parameter across the admin routes, declared
rather than guessed — and it is also a finding for them, since `http.route` cannot tell those three
apart. In P-33.

### Two things found on the way, both real

**The log line's `trace_id` was `none` for every admin action except the login.** It was there by
accident: bound as a structlog contextvar by `TraceIDMiddleware`, which clears it in a `finally`
when the response is done — and `record_request` runs after `call_next`. The login survived because
it audits itself from inside its handler. Measured: the row carried `744c92ad…` and the response
header carried the same id while the line said `none`. The row was never wrong; what was lost is
exactly what Argus said the copy is for — search, the `trace_id` join, and alerts on patterns. The
ids are now stated on the line rather than inherited, which is the same two-payload rule PRM-162
wrote down: the event hangs off its span, the log line reaches no span.

**`greenlet` was an undeclared runtime dependency of the gateway.** SQLAlchemy's async engine
requires it and does not declare it as a hard dependency; auth-service has always declared it, the
two share a virtualenv, and so the gateway worked on a package it never asked for. A `uv sync` that
resolved only the gateway pruned it and the first database call failed with *"the greenlet library
is required to use this function"* — which is what a fresh install of the gateway alone would have
done all along. Now declared, same line as auth-service's.

**Verified live**: two real admin actions through the running gateway — a node check emitting
`argus.target.type=node` with the id in the clear, and a principal update emitting
`argus.target.type=user` with the id their pipeline hashes — both with `argus.actor.kind=user`, the
operator's `user.id`, and a `trace_id` matching the response header. The `user`-typed event is the
real traffic A-34 §3 asked for to confirm the pseudonym against their store.


## PRM-167 — One catalog, and it is the caller's

> Renumbered from PRM-166, which two branches claimed at once; the other landed on
> `main` first. This item's own commit and branch still say PRM-166 — same reasoning
> as the RM-NN items in CLAUDE.md, where the number is the identity and history is
> left alone.

**Why**: `GET /v1/models` needed no credential and returned every deployed model — ids, family,
quantization, context length, replica count, payload schema — to anyone who could reach the port.
Measured: a token holding `model:` grants for four models got all ten, because the handler took no
`request` at all and so could not filter. Its entire stated rationale was one line in
`memory/specs/001-gateway-core.md`: *"unauthenticated for discovery"*.

**The inconsistency it sat on.** RM-07 made model access deny-by-default — a client with no
`model:*` scope cannot call anything — while discovery was allow-all. That is PRM-163's shape
again: one truth in two places with only one protected. And the principle was already accepted with
Axonium in `A-27`, where we agreed an unauthenticated catalog must not name the engine; `family` +
`quantization` and ids like `laya-decide` narrow it anyway, which they pointed out at the time.

**What the industry does**, because this was a contract change and needed more than an internal
argument. Authenticated and scoped to the caller: OpenAI, Anthropic, Azure OpenAI, Bedrock, Vertex,
Groq, Together, Fireworks, Mistral. The closest analogue — a multi-tenant gateway in front of
several engines — is LiteLLM's proxy, which requires its key and filters the list to what that key
may call. The open catalogs belong to single-tenant engine processes meant to sit behind something
(vLLM, llama.cpp, Ollama) or to a marketplace whose product *is* the catalog (OpenRouter). This
platform is neither.

**`/v1/models/mine` already did it right** (RM-45) — 401 without a token, exactly the granted models
with one. So this converges rather than invents: one implementation, `/v1/models` answers it, and
`mine` is an alias kept because it is documented and SDKs call it. The item shape was duplicated
between the two handlers and now exists once.

**The risk is the quiet half, and it was taken deliberately.** A caller that renders a picker from
this endpoint now sees a shorter list with no error — a number that changes while nothing fails,
which is the defect this codebase keeps cataloguing. It cannot be made loud from here, so it is
announced instead: the guide's §3.1 states it as a breaking change with the date, and it goes to the
SDK team in the channel. An empty `data` is documented as "this token has no grants", which is a
different fact from "the platform has no models" and only an operator can tell them apart.

**Verified live**: no token `401 missing-credentials`; the four-grant token gets exactly its four
where it used to get ten; `/v1/models/mine` returns byte-identical JSON; the dashboard's session
(`admin:write`, RM-14's carve-out) still sees all ten. The only internal consumer was the
Playground, already on the authenticated client. Guards hold it: the catalog 401s without a token,
answers the grants with one, `admin:write` still bypasses, the two paths agree, and `/v1/models` is
asserted absent from `EXEMPT_PATHS` — which is where it lived and where it would come back.

**A-29's `/health`, answered in the same change.** It stays liveness-only, unauthenticated,
`{"status":"ok"}`, and must not grow to reflect the registry or redis: that would make it a small
`/metrics` with no credential, which PRM-163 had just closed. The question behind their question —
what an SDK should put behind a "Test connection" button — is `GET /v1/models`, which now proves
reachability, credentials and entitlement in one request. A green light from `/health` means only
that a process answered. Their point (a) was also right and is fixed: §3.7 listed `/health`,
`/metrics` and `/v1/models` as carrying no useful response headers, and all three carry
`X-Request-ID` and `X-Trace-ID` — measured on `/health`, which returns both.


## PRM-170 — Confidential clients only, in writing

**Why**: Axonium asked (`A-30`) whether §2.6's *"no mTLS unless a specific deployment asks for
it"* was about to fire. A fourth SDK is being written in Swift for Mundus, a commercially
distributed macOS/iOS app, and they needed the answer **before** writing it: a Swift package that
gains a `SecIdentity` in v2 is a breaking change in a binary that goes through App Store review.

**It is not an mTLS question, and that is the finding.** A `client_id` is the principal this
platform is built around: `model:<id>` grants attach to it, and `usage_daily`, `usage_events` and
`client_billing_settings` are all keyed by it. So the `client_secret` on an end user's device is
not "a secret in an awkward place" — it is **the integrator's identity, copied onto every user's
machine**: the identity that is granted models and that bills. Not a shape to harden; a shape not
to have. RFC 8252 says the same from the other side — a distributed app is a public client and
cannot keep a secret, whatever the keystore — and it is what OpenAI and Anthropic tell their own
customers about API keys in client applications.

**And PKCE is not the alternative**, which is the answer they were probably expecting. PKCE is the
correct flow for a public client, and what it produces is per-end-user identity in the token —
which this platform's authorization and billing model has nowhere to put. Supporting it means every
end user becoming a principal here, with their own grants and billing rows. That is a different
product, not a grant flag.

**mTLS would not have helped either**: a client certificate inside a downloaded app is a secret
inside a downloaded app. §2.6 stands as written, and now says why rather than only what.

**What unblocks them today**, which is the point of answering at all: the seam Mundus has to decide
now is not the grant and not mTLS — it is whether the Swift package talks to this platform
directly. Through a backend they run, the package never needs a credential store, a keychain
integration or a certificate identity: it takes a token, or a callback returning one, and that is
the whole surface. None of it can become a breaking change in its public API later, which is the
risk they raised.

**Scope**: §2.7 in the SDK guide — who may hold a credential, what to build instead, why not PKCE,
and what it means for an SDK's surface. §2.6's mTLS bullet gains the internet-facing case and a
pointer, because that is the sentence they quoted and where the next reader will look. Sender-
constrained tokens (DPoP, RFC 9449) plus platform attestation are named as the answer *if* direct
device access is ever wanted, so the next person does not arrive at mTLS again.

Nothing was built: the correct answer costs no platform work, which is worth recording because the
question read like a feature request.

**Superseded in substance by PRM-173.** Axonium took §2.7 to Mundus, who came back with the one
architecture neither side had considered — a credential per *end client* rather than one for the
integrator — which rebuts the reason this item gave rather than its letter. The rule that replaced
it is narrower and truer: the question is not whether an app is distributed, it is whose credential
it holds.

**Numbered 170, and it was written as 167.** Two other branches and a renumbering landed on `main`
while this was in progress: `PRM-166` went to a dead-code removal in the manager, the catalog change
that had been `PRM-166` became `PRM-167`, and 168 and 169 were taken. The branch name and the first
commit here still say `PRM-167` — published or not, renaming them would leave the log unqualified,
which is the same convention the catalog item's own renumbering used and the reason the `RM-NN`
items kept their numbers. The roadmap is the record.


## PRM-171 — The audit line leaves the process

**Why**: Argus measured it (`A-35`) — **zero log records in seven days**, against 45,066 spans in
the same window. The audit *event* reached them, because PRM-162 hung it off the server span. The
audit *line* did not.

Their hypothesis was right and the cause is one line of ours: `configure_logging` uses structlog's
`PrintLoggerFactory`, so every `logger.info()` in this platform is written straight to stdout and
never passes through stdlib `logging`. There was nothing for a handler to attach to, and their
agent has no file receiver — so the line existed, was correct, and lived in the terminal of
whoever started the process.

**It emptied something built two days earlier.** PRM-164 put the correlation ids on that line
*specifically* so it could be joined to a trace, and PRM-162 wrote down why the two payloads differ
— the event hangs off its span, the log line reaches no span. A line that reaches no span and no
collector reaches nobody. The row in the gateway's table is still the record of truth and that has
not changed; what was missing is the half Argus is *for*: search, correlation, and alerting on
patterns like a run of failures against one address.

**A structlog processor, not their SDK's `init()`.** They offered `argus.init()`, which installs a
stdlib logging handler. It would have seen nothing — `PrintLoggerFactory` again — and it configures
providers this platform already configures by hand (RM-95). So the export is a processor in the
chain that already exists: it sees the finished event, emits a copy, and returns the dict untouched.
stdout keeps its exact format and the rotating file its exact content. It sits **last**, so what
leaves is the event with its trace id and field order, the same one every other reader sees.

**And the first implementation exported nothing while reporting success**, which is worth recording
because of how it was found. It built `sdk._logs.LogRecord` directly; in `opentelemetry-sdk` 1.44
that class is not public — it lives under `_internal` — so the import raised, the per-event
`try/except` swallowed it, and `configure_logs` returned True over a bridge that carried nothing.
Swallowing is right for a log line and wrong for a setup failure. Measuring the collector's own
counter is what found it: a delta of zero. The export now goes through `LoggingHandler`, which is
public, builds the record, maps the severity and attaches the current span's ids; setup failures
raise.

**Verified live, end to end**: the Argus agent's `otelcol_exporter_sent_log_records_total` moved
974 → 979 for a dashboard login plus the two admin actions, and the node check carries trace
`2e324e0852b1823ac2a3ac729ad0735b` — which is also the real traffic `A-34 §3` and `A-36` asked for,
so they can confirm the `user`-typed target pseudonym against their store on our own events.

Off unless a collector is configured, same gate as tracing. Wired into the gateway, auth-service
and manager-api; the TUI is left out deliberately — it logs for the operator in front of it, not to
an audit trail.


## PRM-172 — `client` says `client`, and each verb is its own action

**Why**: the two things `A-36` asked for, both consequences of findings we had sent them in `P-33`.

**`client` again.** PRM-164 mapped the `client` segment to `user` because `client_id` is the same
value under two resources — `/users/{client_id}` administers the principal,
`/billing/clients/{client_id}/settings` configures its billing — and Argus's pipeline hashed only
`user`. Emitting what each segment said would have left the same subject hashed in one row and in
the clear in another, and a pseudonym is worth what the least protected place that subject's
identifier appears is worth. `A-36` answered that this was a hole on their side and `client` is now
protected exactly like `user`, so the override bought nothing and cost the distinction. Gone, and
the type describes the id again.

**Each verb is its own action.** `{action}` was a path parameter on two routes, so `http.route` —
which has been the audit action since PRM-162 — put start, stop and restart in one bucket, and
cancel, pause, resume and retry in another. Argus's own example of the use case was *"how many
deactivations this week"*, and that could not be counted. Seven explicit routes now, sharing one
implementation. Splitting is ours and breaks nobody: measured before proposing it, only the
dashboard SPA calls them and the SDK guide does not mention `/admin/api` once.

Two things fell out of it:

* The hand-rolled `action not in (...)` checks are gone — a verb that is not a route is a 404 from
  the router, which is what those checks were reproducing.
* `_NON_OBJECT_PARAMS` had exactly one member, `{action}`, and is gone with it. An empty set plus
  the filter that read it is code with nothing left to do; the guard over `TARGET_TYPES` is what
  will ask the question if a route puts a verb in a parameter again.

### And a hole underneath, which the split walked into

The dashboard SPA is mounted at `/admin`, and a Mount matches **every** sub-path. So an
`/admin/api/...` path that no route claimed fell through to the static-file handler and came back a
`405` carrying Starlette's own body — not the RFC 9457 envelope PRM-130 established for every error
this gateway returns. Nothing had noticed, because the only two routes that could reach it checked
their own verb by hand and returned the envelope themselves. Removing those checks removed the
cover.

So `/admin/api/{rest:path}` now answers 404 in the usual envelope, naming the method and path. It
covers every unknown admin path rather than the two verbs that exposed it.

**And it was registered in the wrong place first**, which is worth recording because it is the
second time: declared last inside `create_admin_router`, where it still precedes every billing
route, because FastAPI matches in declaration order and "last in its own router" is not last in the
app. Twenty-five billing tests went red at once. It lives in `main.py` after both admin routers now
— the same lesson PRM-100 learned on `/v1/usage/export`, learned again one router up.

**Verified live**: `POST /admin/api/nodes/local/instances/qwen3-0.6b/stop` proxies and its audit
line carries `action = POST /admin/api/nodes/{node}/instances/{model_id}/stop` with
`argus.target.type = instance` — the verb is in the template, so it can be counted. An unknown verb
returns `404 application/problem+json` with `type: not-found`, and the two 404s are distinguishable
by body: the real one comes from the manager (`prometheus.local`), the unknown one from the gateway
(`prometheus.internal`).


## PRM-173 — An end client's credential is their own, and a human issues it

**Why**: PRM-170 answered `A-30` with *confidential clients only* — a distributed app must not hold
a credential. Axonium took that to Mundus, and `A-34` came back with an architecture neither side
had considered: **a `client_id` per end client**, issued by Mundus as operator, pasted into that
client's own copy of the app.

**It rebuts the reason rather than the letter, and the reason was the load-bearing part.** PRM-170
argued the credential must not ship in a distributed app *because it is the integrator's* — one
leak exposing the identity that is granted models and billed, for every user at once. If the
credential belongs to the client, that argument stops applying: their grants, their bill, their
blast radius. It is the "bring your own key" shape OpenAI's and Anthropic's own desktop apps use.

Axonium were scrupulous about not deciding it for us: an app with a pasted secret is still a public
client in RFC 8252's terms, and whether that matters is the rule-writer's call.

**Accepted, and the business shape is why.** The integrator's product is their application; this
platform's product is consumption of the models. A person can use Mundus's app without ever holding
a credential here. The moment they want the model-backed features they are **our** customer, paying
us per consumption, with their own credential, grants and bill. So the credential being on their
laptop is the credential being where its owner is.

**And the answer to their blocking question: no, there is no issuance API, and there will not be
one.** Credential creation is a human administrator's act, always. Measured, because they had probed
and misread it:

```
POST :8020/admin/clients   405      GET :8020/admin/clients   404 {"detail":"Not Found"}
POST :9000/admin/clients   403      ← the real surface, platform admin key
```

Their reading was *"a handler exists and does not recognise that resource"*. It is the dashboard's
static mount at `/admin` answering — a Mount matches every sub-path — which is the same hole PRM-172
closed for `/admin/api/*` and left open one level up. There is nothing behind it.

The two surfaces that create a principal are the dashboard (`admin:write`, behind a human login,
guarded by `test_create_user_requires_admin_write`) and auth-service's admin API with the platform
key. Neither is reachable by an integrator, and neither is proxied for one. Issuing a credential
opens a billing account, which is a commercial act with a person on this side of it.

**What that costs Mundus, said plainly rather than discovered**: a human step between *a person wants
the AI features* and *their credential exists*. So the app has to treat "no credential yet" as a
first-class state, and the request comes to us rather than to them. And one credential per client,
on as many of that client's own devices as they like — which answers `A-34 §4`: the same secret on a
Mac and an iPhone is that client's secret on that client's devices, and revocation is per client.
Per-device issuance is not offered.

**Scope**: §2.7 of the SDK guide, rewritten from *who may hold a credential* to *whose it is and who
issues it*, with the onboarding consequence and the business shape that explains both. No code: the
mechanical half of the rule was already guarded.

## PRM-174 — An unmapped route answers in the gateway's own envelope

**Why**: Axonium probed `/admin/clients` while working out how credentials are issued
(`A-34`), got a `405` and a bare `{"detail": "Not Found"}`, and asked what was behind it —
reasonably, because that body looks like a handler that exists and does not recognise the
resource. Nothing was behind it. But the envelope was genuinely wrong: RFC 9457 everywhere
else, Starlette's default there, so an SDK typing errors by `type` and correlating by
`request_id` got neither.

**The measurement moved the boundary, and that is the finding.** The report said `/admin`.
`JWTAuthMiddleware` runs **before** routing, so on any protected path an unmapped URL is a
`401` in our own envelope and the router's `404` never happens. What leaked was the
middleware's exact complement — `/admin/<not-api>`, `/ui/*`, `/share/*`, and a wrong verb on
`/health` or `/oauth2/token`. **The auth middleware had been acting as the envelope's floor by
accident**, which is why nothing ever noticed: the only paths that could show the defect are
the ones nobody authenticates to. Scoping the fix to `/admin` would have fixed the instance,
left the class, and picked a boundary the measurement says is the wrong one.

**Scope**
- One app-wide `StarletteHTTPException` handler in `main.py` building the same `_problem`
  envelope — the move RM-65 made for the `422`, for the same reason. Nothing in `gateway/src`
  raises `HTTPException` (zero occurrences), so it can only fire for one Starlette itself
  raises: no route, wrong verb, or `StaticFiles` missing a file. `/share/{token}`'s upstream
  HTML page is a *returned* response, so it is untouched.
- `unknown-route` rather than reusing `not-found`, which the SDK guide defines as one data
  condition — "no usage row with that id belonging to this client". A bad URL is a mistake in
  the caller's code, not a fact about their data, and an SDK that cannot tell them apart
  retries the wrong one. PRM-172's `/admin/api` catch-all was moved onto the same slug; it
  survives only for its more specific `detail`.
- Starlette's `Allow` header is passed through, so a `405` still answers "then what is?".
- Tests: the class first — every exempt path, refused by routing, must be in the envelope, so
  the test fails if someone adds an exempt path and forgets. Plus the `401`-before-routing
  behaviour, pinned as the reason the leak was invisible. PRM-172's catch-all shipped with no
  test for its envelope at all; it has one now.
- Out: the `401`-on-an-unmapped-protected-path behaviour. It is odd (a path that does not
  exist demands credentials) but it hides nothing and changing it would weaken nothing.

**A method note worth keeping.** The first in-process probe used conftest's `test_app`, which
is a hand-built minimal FastAPI app with neither the handler nor the admin mount — it reported
every surface as broken and would have reported the fix as ineffective. It also made RM-65's
`422` handler look unregistered, and a stale dev process (started 95 seconds before `main.py`
last changed) appeared to confirm it. Both were instrument errors, measured away against
`create_app`. Same shape as A-37/A-39: **the instrument chosen decided the conclusion before
the data did.**

## PRM-175 — The audit target says where it lives

**Why**: `argus.target.type`/`.id` name the object an admin action acted on, but twelve
routes carry **two** objects — all of shape `/admin/api/nodes/{node}/<resource>/{model_id}`
— and PRM-164 kept only the inner one, because the last parameter is the most specific.
The outer id still reached Argus, but only inside the row's JSON parameter set, which is the
route-template problem one level down: a reader had to know each route's shape to find it, so
"every action on node X" was not a question the store could answer. Proposed in P-35 and
accepted in A-41 §3 on the argument that settled the shape — *"they are not two peer objects;
it is an object and the place it lives"*. A `target2` would have claimed two things of equal
rank, and then grouping by "the object" is ambiguous.

**Scope**
- `parent_for(request)` — the next-outer object parameter, derived by the **same** helper as
  the target. The derivation was duplicated in the first draft, which is this codebase's own
  recurring defect wearing its own name, so `_pair_at(request, index)` serves both.
- A-41 §3's two conditions, both tested. **The parent is classified from the first event**:
  the type comes from the route segment, never the parameter name, so a parent that is a
  principal says so and Argus's pipeline protects the id — their reason being that debuting
  the attribute without it would repeat, on a brand-new field, the leak the two teams had
  just fixed three times. **The pair goes together or it does not go**: half a pair is an
  identifier nobody knows whether to protect, so it is better absent. Applied to the target
  too — its two bare-id branches were unreachable, and the rule is the same rule.
- `argus-obs-semconv` `1.0.0a8` → `1.0.0a17`, which condition 1 needs:
  `ARGUS_TARGET_TYPE_PRINCIPALS` ships the principal set as data since `a14`. Every constant
  this repo uses was checked to still exist and still hold the same value; the one change is
  `ARGUS_OUTCOME_VALUES` gaining `denied` and `suspended`, additive, and read by index here.
- **The comment that restated which types are principals is gone.** It said "`user`", then
  PRM-172 came back and added "and now `client`" — a fact in prose beside the same fact in
  code, which is the shape both teams have now been bitten by four times. The set is read
  from the package and the tests compare against it.
- The two parent attribute names are **literals**: the package ships no constant for them at
  any published version. A test fails the moment it does, so the switch is forced rather than
  remembered.
- Out: mapping a 403 to `a17`'s new `denied` outcome. It is the right word and it changes how
  every refused admin action groups in their store, which is a decision to take with them.

**Verified live, which is what A-41 asked for**: a real `POST
/admin/api/nodes/local/instances/qwen3-0.6b/stop` emits `argus.target.type=instance`,
`.id=qwen3-0.6b`, `.parent.type=node`, `.parent.id=local`.

**It also found a green test that tested nothing.** PRM-174's catch-all assertion ran against
`multi_model_app`, which has the dashboard **off** — so there is no catch-all and no mount
there, and the assertion passed against the app-wide handler instead. Now on the
dashboard-enabled app and pinned to the catch-all's own `detail` string. Third time in two
days that the instrument, not the code, was the thing that was wrong.

## PRM-176 — The environment says `development`, not `bare-metal`

**Why**: `OTEL_RESOURCE_ATTRIBUTES` carried `deployment.environment.name=bare-metal`, which
names the **hardware**. This stack runs bare-metal in development and would in production
too, so as an *environment* the value distinguished nothing — and it sits outside the
attribute's conventional vocabulary, which is why it was the single true warning Argus
measured this platform getting when they checked `1.0.0a17` against all three teams' real
configurations (A-42). Promised to them in P-35 and paid here.

**Scope**
- `deployment.environment.name=development`, in **both** copies: `runtime/telemetry.env`,
  which the running stack reads, and `runtime/telemetry.env.example`, which the repo ships.
  The live one is gitignored, so only the example is reviewed — changing one and not the
  other leaves the stack on the old value with the repo claiming otherwise. No hook check for
  the pair, because the live file does not exist in CI; the risk is named here instead.
- The heading comment still says "bare-metal launch", and correctly: it is the launch that is
  bare-metal, not the environment.
- Out: `service.version=2.0.0`, which is also hand-maintained here and will drift from the
  package versions. Worth its own look, not this one's.

## PRM-177 — Platform-wide usage analytics on the dashboard

**Why**: the dashboard can answer "what did *this client* cost" and cannot answer "who uses
this platform, and for what". `Billing.tsx` has two charts — cost by model, and a daily cost
trend — and both are scoped to one `client_id`, because RM-60 built them for an invoice. The
questions an operator actually opens a dashboard with are comparative: which clients interact
most, how many requests arrived over a window, which models are actually used, what each
model and each client costs.

**Scope**
- Five views: most-active clients, request volume over a window, most-used models, cost per
  model, cost per client. Platform-wide, with a date range.
- **Four of the five need no new backend.** Measured: `query_daily_cost_range` and
  `query_model_cost_range` both take `client_id: str | None = None`, both already return
  `request_count`, and `client_id=None` already aggregates across the platform. "Most-used
  models" is a sort by `request_count` on data the billing widget already fetches and charts
  by cost.
- **One new query**: there is no `group_by(UsageEvent.client_id)`, so per-client totals and
  the activity ranking need `query_client_cost_range(start, end)` plus an endpoint.
- **The real design question is naming.** `usage_events` holds `client_id` and nothing else;
  a chart of UUIDs is not a chart anyone reads, and the name lives in auth-service
  (`client_name`/`label`). The admin router already proxies the principal list, so the join
  is available — but whether the dashboard joins per render, or the aggregate endpoint
  resolves names server-side, decides how it behaves when auth-service is down. Decide that
  before building, not during.
- Recharts is already a dependency and already used by the two existing charts, so no new
  library. Consult the `dataviz` skill before writing chart code.
- Watch the unpriced case: both queries return `unpriced_requests` precisely because PRM-119
  established that a period of entirely unpriced usage must not render as a confident zero. A
  cost chart that drops it repeats that defect in a new place.
- Out: anything per-end-user rather than per-client. A `client_id` is the only subject the
  usage store has, and since PRM-173 a client may be one person or an integrator — so "users"
  here means principals, and a real per-person view is a different item.

**Data to build against, measured 2026-10-03**: 4,035 events, 11 clients, 12 models,
2026-09-07 to 2026-10-03.

## PRM-178 — The audit outcome cannot leave the vocabulary unnoticed

**Why**: `argus.outcome` is a closed vocabulary, and nothing checked ours. A-43 is the
entry that found it, and it found it by correcting its own measurement: A-35 had told us we
emit no `argus.outcome` at all, because the query read `SpanAttributes` and this platform's
audit record lives in the **spanevent** scope. The real figure was 24 events in fourteen
days, `ok` and `error`, both in vocabulary. Then came the part that is ours: their
`Step.outcome()` validates the word but writes *span* attributes, and `record()` builds this
event by hand with `add_event`, so that validation never runs on it. Every caller goes
through `outcome_for` and is correct today; what was missing was anything that would say so
on the day a third value is added by hand.

**Scope**
- A warning when the outcome is outside `ARGUS_OUTCOME_VALUES`, and the value **sent
  anyway**. That is the deliberate part: an audit record edited to fit a vocabulary is a
  record of something that did not happen, and a dropped one makes a failure look like an
  absence (RM-98). It is also the shape Aeon asked Argus for at ingest — rule, counter, value
  kept rather than discarded.
- Exhaustive test over every status from 100 to 599, replacing two spot checks.
- Out: adopting `a17`'s `denied` for a 403, with `a18`'s `argus.denied_by` to attribute it.
  `error` is no longer the most precise word available, but changing it regroups every
  refused admin action in Argus's store — their dashboards, not ours. Proposed in P-36.

**A-44 needed nothing**: `argus.sampling.baseline_pct` is retired for
`retained_pct`/`policy`, and measured here, nothing in this repo reads either — the attribute
is written by their gateway, not by these services.

## PRM-179 — TEI as the second new engine: batched pairs and raw scores

**Why**: Centinela asked for batched `predict` on `von-decide` and batched reranking, both "on
the GPU", and measuring the chain showed our API already sends what they ask — `/v1/rerank`
makes **one** upstream call with every document, and `/v1/models/{m}/predict` forwards the body
verbatim. The limit is inside the engines. `hf-serve` 0.1.6 types `inputs` as `str`, measured
directly against the live instance: a list comes back `422` from its own pydantic
(`loc: ["body","inputs"]`). So the batch is not a flag we failed to set.

**TEI is the engine that already does both**, and PRM-133's own engine survey had it as one of
the two strongest additions before any of this came up. From its OpenAPI, not inferred:

| endpoint | request | what it gives Centinela |
|---|---|---|
| `/predict` | `inputs`: "a single string, a pair of strings or **a batch of mixed single and pairs**", plus `raw_scores` | C-01 §3's formats A **and** B in one call — zero-shot NLI *is* a batch of (premise, hypothesis) pairs |
| `/rerank` | `{query, texts[], raw_scores, return_text}` | C-01 §2, with token-based dynamic batching (`--max-batch-tokens`, default 16384) |

It serves **ModernBERT**, which is what `von-decide` is, and it has a native **Metal** build on
Apple Silicon (`brew install text-embeddings-inference`, launched as `text-embeddings-router`),
so it fits the way this platform already launches binaries rather than containers. `raw_scores`
also answers their §2 observation about scores saturating near 1.0 — they were calibrating on
top of a sigmoid that had already been applied.

## Verified against a running TEI, 2026-10-03

PRM-133's rule is that nothing joins `BACKENDS` without a node that can actually run it, and
it cites `vllm`/`sglang` as what an unverified builder is worth. So the premise was tested
before any wiring — `ghcr.io/huggingface/text-embeddings-inference:cpu-latest` under amd64
emulation, serving `cross-encoder/nli-distilroberta-base`, which is the same shape of model as
`von-decide`:

```
inputs: [["Un perro duerme en el sofá.", "Hay un perro en la imagen."]]
  → [[{"score":0.88670063,"label":"entailment"},
      {"score":0.083747655,"label":"neutral"},
      {"score":0.02955178,"label":"contradiction"}]]

inputs: [[p1,h1],[p2,h2]]        → one result per pair, in input order
raw_scores: true                 → [2.0113802, -0.34831908, -1.3899833]   logits, not softmax
64 pairs                         → 200, 64 results
```

Four things that settles:

1. **A batch of pairs works, and it is both of Centinela's formats.** Format B is a list of
   `[text, label]` pairs; format A is the same with the hypothesis repeated per text.
2. **Batch results are identical to single calls** — `0.88670063` to the last digit, which is
   their ±0.001 acceptance criterion met rather than argued.
3. **`raw_scores` returns logits**, which is their §2 calibration request, and it is a boolean.
4. **Labels come back as class names**, not indices.

And four launch gotchas that only reading the flags and the logs produced:

| gotcha | why it matters |
|---|---|
| `--max-client-batch-size` defaults to **32** | Centinela asks for 64. The default *refuses* their batch |
| `--prometheus-port` defaults to **9000** | which is auth-service on this host. Every instance needs its own, explicitly |
| `--hostname`, not `--host`; and it reads `HOSTNAME` from the environment | the container run logged `Invalid hostname, defaulting to 0.0.0.0` from Docker's own `HOSTNAME`. A shell that exports it would do the same, so it must be passed explicitly |
| the backend logged `does not support a batch size > 8, forcing max_batch_requests=8` | the client batch and the *backend* batch are different numbers. Whether that cap is CPU-backend-only or applies to Metal is unmeasured, and it is what decides their latency targets |

**Latency was not measured, deliberately.** 15 pairs took 443 ms and 64 took 12.4 s, but that
is amd64 emulation on Apple Silicon with the CPU backend — a floor of the worst case, not a
verdict on their ≤150 ms. Shape and correctness are verified; speed needs the Metal build.

**And the open question got sharper.** TEI's softmax is across the **model's own three NLI
classes** (0.887 + 0.084 + 0.030 = 1.0), not across the caller's candidate labels. Centinela's
`{sequence, labels, scores}` needs entailment compared *between candidates*, which is a
different normalisation that nothing upstream does. So the question in P-01 is not "who
reshapes the response" but "who performs a normalisation that no engine performs" — and TEI
handing back raw logits per pair is the cleanest input for whoever does.

**Blocked on a host decision, and not worked around.** `brew install
text-embeddings-inference` wants to upgrade `openssl@3` to `openssl@4` as a dependency, which
is a shared library many formulae on this machine link against. The install was attempted
twice and failed on a lock around that upgrade; forcing it, unlinking, or deleting lock files
would be changing a system library as a side effect of adding an engine. That is the
operator's call, so `tei` is **not** in `BACKENDS` yet — adding it unverified on this node is
exactly what PRM-133 warns against.

## Done, 2026-10-04 — and the install took a different road

`brew install text-embeddings-inference` never completed. It is reproducible from a clean
Cellar: brew pours `openssl@4`, then **deadlocks against its own lock** on
`/opt/homebrew/Cellar/openssl@3`, because `openssl@4` is not keg-only and declares
`link_overwrite` on `lib/libssl*`, `lib/libcrypto*`, `include/openssl/*`, `openssl.pc` and
`bin/openssl` — the unversioned paths that today point at `openssl@3`. Two wrong diagnoses were
written down before that one: "a system library upgrade" (it is not, the 16 dependents link
against the versioned keg) and "my own interrupted pour left debris" (it did, and removing it
changed nothing).

So TEI was **built from source** — `cargo install --path router -F metal`, 4m42s, and the build
uses `rustls`, so it links no openssl at all. The binary lives in `~/.cargo/bin`, which is the
same shape as `llama-server` in `~/.local/bin`: a binary this platform launches, not a package
it depends on.

### Measured on Metal, which is the number that was missing

`Starting Bert model on Metal(MetalDevice(DeviceId(1)))`, and **no** `max_batch_requests=8`
warning — that cap was the CPU backend's alone, which answers the question P-02 had to leave
open. Rerank, best of three:

| documents | `ms-marco-MiniLM-L-6-v2` (22M) | `bge-reranker-base` (278M) | Centinela on llama.cpp |
|---|---|---|---|
| 15 | 88 ms | 162 ms | 450-660 ms |
| 30 | **137 ms** | 328 ms | 1450-1540 ms |
| 64 | 132 ms | — | — |

Their C-01 §2 target is ≤200 ms for 30 documents. **The engine is no longer the limit; the
model size is** — met on a 22M model, missed on a 278M one. That reframes their "optional:
offer qwen3-reranker-0.6B" from a nice-to-have into the actual lever, pointing the other way:
a *smaller* reranker is what gets them under the target.

One caveat to carry forward: the engine warns that a `hidden_act=gelu` model is served with the
**GeLU+tanh approximation** rather than exact GeLU, and says so may "lead to subtle differences
with Transformers or Sentence Transformers outputs". That bears directly on PRM-180's ±0.01
cosine criterion, and on any comparison between TEI's numbers and `hf_serve`'s.

### Four things the real binary corrected

- `--host` is **rejected** (`unexpected argument '--host' found`); the flag is `--hostname`, and
  it is read from `HOSTNAME` in the environment when absent.
- `--revision` is **not passed, and that is a finding**. Pinning is right and Centinela asked
  for it, but this registry has no git revision: `hf_sha256` is a file content hash, not a
  commit id. Passing it would be a plausible value from the wrong source. A revision column is
  a registry change.
- `--prometheus-port` defaults to 9000 — auth-service here — so it is allocated per instance.
- `--max-client-batch-size` defaults to 32 and caps one request's inputs, so it is set to 64:
  the default would have refused the batch the engine was added for.

### The asymmetry this change created, and fixed

The first instance came up **healthy and was reported `not_ready`**: the manager's readiness
probe sends a real rerank, and it sent it to `/v1/rerank`, which TEI answers 404. PRM-183 had
made the *gateway* engine-aware and left the *manager* llama.cpp-shaped — the same fact in two
places, one updated. `readiness._probe_body` now keys `rerank` on the backend, which is what
its own docstring already claimed about pass-through modalities and had never needed to be true
of rerank. The path is stated twice, in two packages, and
`test_the_manager_probes_the_same_rerank_path_the_gateway_forwards_to` imports both and fails
if they diverge — a test instead of a dependency for two strings.

Measured after: `lifecycle.ready — answered /rerank with 200 on attempt 1`.

### Verified end to end, and the one seam that is not

The manager registered and launched `bge-rr-tei` on the `lab` node with exactly the builder's
command, the scanner sees the process, readiness passes, and the engine answers the shape
PRM-183's dialect converts. Probing the running instance also settled which routes TEI serves:
`/rerank`, `/predict`, `/embed`, `/v1/embeddings` and `/embeddings` all exist, and only
`/v1/rerank` 404s — so **`embedding` needs no gateway change**, since the OpenAI-compatible
path this platform already calls is there.

**Not verified: a live rerank through the gateway**, because it needs a `model:bge-rr-tei`
grant and only a human administrator issues those (PRM-173). The dialect is covered against
the captured real response instead.

**Scope**
- A command builder in `lifecycle.py` and a process signature in `scanner.py`, which is
  PRM-133's standing rule for adding an engine, on the `lab` node that exists for exactly this.
  The verified flag set is `--model-id` (Hub id, so `hf_repo` as with `hf_serve`), `--revision`
  (which the registry already has as `hf_sha`, and which is how a model stays pinned),
  `--hostname`, `-p/--port`, `--max-client-batch-size`, `--max-batch-tokens`, `--dtype`, and
  `--prometheus-port` from a second allocation.
- **`rerank` on TEI needs a gateway change, and this is the part C-01 §2 actually waits on.**
  Measured: `RerankRequest.to_llama_payload` emits `{model, query, documents}` to upstream
  `/v1/rerank`, which is llama.cpp's shape. TEI's is `POST /rerank` with `{query, texts}`. So
  the gateway needs the upstream rerank shape to be per-engine, the way `payload_schema`
  already is per-engine on the way out. Until that exists, a TEI reranker cannot be reached
  through `/v1/rerank` at all.
- `payload_schema` for `("classification", "tei")` and, when the normalisation question is
  answered, for `zero_shot` — `payload_schema_for` already keys on engine precisely so a
  `zero_shot` model on a new engine is not assumed to be an `hf-inference` body.
- `raw_scores` surfaced through the gateway for rerank, and documented in the catalogue along
  with the max documents and max tokens per document that C-01 §2 correctly points out are
  missing today. `--max-client-batch-size` is where that number comes from.

**The open question, and it is the real one**: TEI returns per-pair class scores. The
`{sequence, labels, scores}` shape Centinela gets today, with scores normalised across the
candidate labels, is the *transformers* zero-shot pipeline doing two extra things — building
hypotheses from a template and normalising across labels. TEI does neither. So either the
caller does that last step (and they are already asking for raw logits in §2, so this may be
what they want), or this platform owns a transform on a route whose entire design is that the
body and the answer are the engine's. **Switching `von-decide` from `hf-serve` to TEI would
also change its response shape**, which breaks Centinela and the axonium SDK that already
ships `predict`. Decide that before building: most likely answer is both engines coexisting,
with the batch path as its own catalogue entry rather than a silent swap underneath the
existing one.

## PRM-180 — SigLIP 2 served as a multimodal embedding

**Why**: Centinela runs `google/siglip2-base-patch16-256` locally on ONNX Runtime and wants it
on the platform — not for latency (they measure 8-18 ms for text, better than the ≤30 ms they
ask of us) but to get ~3 GB of text-encoder weights out of their API process. There is no
modality here that takes an image as embedding input: `embedding` exists (`qwen3-embedding`,
`minilm-hfserve`) and is text-only.

**Researched 2026-10-03, and the distinction that decides it is SigLIP 1 versus SigLIP 2:**

| engine | verdict |
|---|---|
| **Infinity** | **the candidate.** Serves `SiglipModel` with `/embed` and `/image_embed` plus an OpenAI-compatible `/embeddings`, runs on Apple **MPS**, and preprocesses with each model's own registered `AutoProcessor` |
| TEI | text tower only — the SigLIP PR is titled "text embeddings only". No image path |
| vLLM | its multimodal embedding support is SigLIP **1** (`google/siglip-base-patch16-224`); the SigLIP 2 image-embedding issue was **closed as not planned** |
| ONNX Runtime directly | what Centinela already uses, and the one path guaranteed to reproduce their vectors, at the cost of a fifth engine that serves one model |

**The AutoProcessor detail is the whole item.** Centinela's acceptance criterion is a cosine
within 0.01 of their local ONNX vectors over 20 images and 20 texts, and their requirements —
lowercase, Gemma tokenizer, fixed padding to 64 tokens, 256×256 bilinear, mean/std 0.5,
L2-normalised 768 — are a *preprocessing contract*, not model config. An engine that runs the
model's own AutoProcessor is reproducing the same preprocessing their ONNX export came from,
which is why Infinity is plausible where a hand-rolled server would not be. It still has to be
verified against their 20+20 set before anything is promised; their criterion is the test.

**Scope**
- Infinity on the `lab` node, with the command builder and scanner signature PRM-133 requires.
- A modality that accepts image input. Whether that is a new `multimodal_embedding` or a
  widening of `embedding` is not cosmetic: it decides the endpoint, the rate-limit bucket, the
  `payload_schema`, and whether an existing `model:<id>` grant reaches it. Asked in P-01.
- `logit_scale` (112.90) and `logit_bias` (-16.77) exposed in the catalogue. Small, ours,
  independent of the engine work, and they need it to turn a cosine into a probability.
- Out for now: `siglip2-so400m-patch16-384`, which would force them to reindex and changes
  hardware planning. Asked whether it is a real need.

## PRM-181 — Rate limits per client, not only per endpoint

**Why**: Centinela asked for `predict` to go from 60 to ≥600 per window for *their* client.
Measured: the counter key is `prometheus:rl:rpm:{identity}:{endpoint}:{bucket}`, so the budget
is already **counted** per client, and the window is a fixed 60-second bucket
(`int(time.time() // 60)`) — which answers the window question they asked. But the **value** is
global configuration (`rate_limit_rpm = 60`) with per-endpoint overrides that exist only for
`chat_completions` and `admin`. `predict` has none. So the limit cannot be raised for one
client: raising it raises it for everyone.

**Scope**
- A per-client limit that overrides the endpoint default, which is what every comparable
  gateway has: LiteLLM has per-key/per-team/per-user/per-customer RPM and TPM, and Kong's AI
  rate-limiting plugin evaluates an ordered policy list over consumer, consumer group, model
  and route. Per-customer is the industry default, not an exotic request.
- A `predict` endpoint override, which is missing regardless of the per-client work and is the
  cheap half.
- The window length documented in the SDK guide — asked for, and currently nowhere.
- Note for whoever builds it: the batch work in PRM-179 reduces the need (a refined search
  goes from 15-30 requests to one) but does not remove the gap, and TPM still scales with the
  batch even when RPM does not. Said in P-01 so a `429` on tokens is not a surprise.

## PRM-182 — The `predict` bucket becomes configurable, and the window gets written down

**Why**: the two halves of Centinela's C-01 §4 that needed no engine. `predict` has had its own
rate-limit bucket since PRM-136 — `X-RateLimit-Scope` answers `predict` — and no setting, so it
resolved to the generic 60 and four of their searches exhausted it. And the window they asked us
to document was in no document: the guide said "unix timestamp of the next window" without ever
saying what the window is.

**Scope**
- `rate_limit_rpm_predict` / `rate_limit_tpm_predict`, defaulting to `None`, which keeps today's
  behaviour exactly and lets an operator raise `predict` without raising chat and embeddings
  with it.
- `ENDPOINT_LIMIT_FIELDS`, one map from endpoint slug to its two Settings fields, replacing an
  `if/elif` chain. A new endpoint is one entry.
- The window in the SDK guide: **fixed 60-second buckets aligned to the wall clock**, not
  sliding per request, with the two consequences that follow — a burst spanning a boundary
  passes where the same burst seconds earlier would not, and each `X-RateLimit-Scope` is its own
  bucket so a 429 on one says nothing about the others. Plus the distinction C-01 §4 surfaced:
  the budget is **counted** per credential while the limit **value** is platform configuration.
- Out, deliberately: making `predict` editable from the dashboard. That needs two columns on
  `rate_limit_config`, a migration, the upsert signature and the Limits UI — the same work
  PRM-181 carries for per-client limits, so it belongs in one change. `.env` plus a restart
  raises it today, which is what was asked for.

**The bug this nearly shipped, and it is the reason for one of the tests.** The slug-to-field
relationship lived in two places — the middleware's `if/elif` and `RATE_LIMIT_FIELDS` — so
collapsing them looked like removing a duplication. It is not: `RATE_LIMIT_FIELDS` is the list
of fields the **dashboard persists**, every one of which is a column on `db.RateLimitConfig`,
and `main.py` reads them off a row with `getattr`. Deriving it from the resolution map added
`rate_limit_rpm_predict` to a list whose other consumer is a database that has no such column —
an `AttributeError` at startup, and only on a deployment that had ever saved limits from the
dashboard, which is the worst possible place for it to appear. Caught by reading the other
consumer before shipping, not by a test. `test_every_dashboard_field_is_a_real_column` is that
test now.

**Two earlier answers corrected while here** — both the same shape, a fact written down once and
then moved on without:
- `lifecycle.py` said hf-serve was verified against `0.1.4`. Re-checked against `0.1.6`, and the
  measured limit that matters now sits next to the launch command: `inputs` is typed `str`, so a
  batch is refused by the server and not by anything this builder does.
- PRM-133's engine survey picked `tei` and `infinity` in September on a one-line verdict each.
  That row now points at PRM-179 and PRM-180 rather than being read as the detail.

## PRM-183 — The rerank upstream shape is the engine's

**Why**: `/v1/rerank` was built against llama.cpp and forwarded its body verbatim, so the
*upstream* shape was llama.cpp's by accident rather than by choice. That is what blocks
Centinela's `C-01 §2`: TEI is the engine that batches rerank pairs on the GPU, and it cannot
be reached at all through this route. Both shapes measured from running servers on 2026-10-03,
llama.cpp through this gateway and TEI direct:

```
llama.cpp  POST /v1/rerank  {"model","query","documents","top_n"?}
  → {"model":…,"object":"list","usage":{"prompt_tokens":176,…},
     "results":[{"index":0,"relevance_score":0.9997715},…]}

TEI        POST /rerank     {"query","texts","raw_scores"?,"return_text"?}
  → [{"index":0,"score":0.9993574},{"index":1,"score":4.0859417e-05}]
  raw_scores: [{"index":0,"score":7.349291},{"index":1,"score":-10.105332}]
```

**Four differences, and only the first is cosmetic.** `score` against `relevance_score`; TEI
answers a **bare array** with no envelope; TEI has **no `top_n`**, so trimming is ours; and
**TEI reports no `usage` at all**. That last one is the reason this is a module and not an
`if engine == "tei"` at the call site: the handler meters and settles a budget reservation from
`resp_body["usage"]["prompt_tokens"]`, so a real TEI reranking would have been billed as **zero
tokens** and written a confident junk row — PRM-142's own finding ("the backend's own usage
object → 0 tokens, a junk row") arriving again one engine later. The normaliser substitutes the
estimate the route already computes for the spend cap, and marks it `prometheus_estimated` so
it never passes as counted.

**Scope**
- `models/rerank_dialects.py`: per-engine path, request builder and response normaliser.
  `llama_cpp` is `native`, meaning its body is still forwarded and returned untouched, so the
  existing contract is byte-identical — its 11 tests pass unchanged.
- The dialect is resolved over **every usable replica**, not the chosen one, because
  `forward_with_failover` may answer from another; replicas disagreeing on their engine's shape
  is refused as `inconsistent-model-group`, the same class `payload_schema_of` refuses to
  average over.
- An engine with no recorded dialect is a new `503 rerank-dialect-unknown` rather than a guess,
  and an upstream body the dialect cannot read is a `502` carrying the body rather than an
  empty ranking.
- `raw_scores` on `RerankRequest`, which is C-01 §2's ask: a reranker's probabilities saturate
  near 1.0 — they measured 0.99 for a loosely related document — and a saturated probability
  cannot be calibrated while the logit behind it can. **Declared rather than left an extra**,
  because an extra is reported by `ignored_parameters` on every engine including the one that
  honours it. Where the engine lacks it, it is named in `X-Prometheus-Ignored-Parameters` and
  refused under `require_parameters` — PRM-127's header for a second reason: not "this gateway
  does not act on it" but "the engine behind it does not have it".
- Out: registering a TEI reranker, which needs the engine installed (PRM-179) — so this is the
  half of C-01 §2 that could be built today, and it is the half that was blocking.

**A guard test caught the one thing I would have shipped wrong**:
`test_every_error_the_gateway_raises_is_in_the_guide` failed on the new `rerank-dialect-unknown`
until the SDK guide documented it. The contract is checked, not remembered.

## PRM-184 — TEI serves classification and zero_shot, with its own contract

**Why**: PRM-179 allowed TEI only `embedding` and `rerank`, and left `zero_shot` out because
who normalises across candidate labels was an open question with Centinela. Asked to implement
both, the question turned out to have an answer that does not require deciding for them:
**publish a different contract and let the caller choose by choosing an engine.** That is what
`payload_schema` has existed for since PRM-144, and this is the first time two engines serving
one modality genuinely disagree.

**They are not converted, and that is the decision.** hf-serve returns `{sequence, labels,
scores}` normalised across the candidate labels the caller supplied; TEI returns scores across
the **model's own** classes and has no notion of candidate labels at all — measured, it answers
hf-serve's body with `200` and discards `parameters` rather than refusing them. Converting one
into the other would mean this platform inventing a normalisation on a route whose whole design
is that the shape belongs to the engine.

**The body, measured against a running server, because one case is a trap:**

```
inputs: "a text"                     → one flat list of {label, score}
inputs: ["premise", "hypothesis"]    → ONE PAIR, not a batch of two texts
inputs: ["a", "b", "c"]              → 422
inputs: [["a"], ["b"]]               → a batch of two single texts → two lists
inputs: [["p1","h1"], ["p2","h2"]]   → a batch of two pairs → two lists
```

A batch is always a list of lists. The natural-looking "send my N texts as an array" is the one
form that silently returns a single wrong answer at N=2 and a `422` at N≥3. It matters directly
to Centinela: their `C-01 §3` format A is a flat array. Written down in the SDK guide under
`tei.predict.v1`.

**A probe that proved nothing, found by measuring rather than by it failing.** The readiness
probe for `zero_shot` sent hf-serve's body to TEI and got `200`, so an instance was reported
ready on evidence of nothing — the `candidate_labels` were discarded. It now sends a
`(premise, hypothesis)` pair for that engine, which is the only body whose answer depends on the
hypothesis: `entailment 0.982` against "a dog is sleeping", `contradiction 0.994` against "a car
is parked outside". Third instance of the same shape in two days, after the gateway's rerank
path and the manager's rerank probe.

**Three different per-model outcomes, which is why modality and model support stay apart.** The
allow-set is about modalities; whether a given model loads is the engine's to report:

| model | outcome |
|---|---|
| `cross-engine/nli-distilroberta-base` | serves |
| `wfzyx/von-1.0` (`von-decide`) | `Model is not supported` — architecture |
| `distilbert-...-sst-2-english` (`sst2-clf`) | `Could not download model artifacts` — the repo ships no `tokenizer.json`, which TEI's Rust tokenizer requires |

That last one will bite again: plenty of older Hub repos ship `vocab.txt` and no `tokenizer.json`.
Both reasons reached `runtime/logs/lab/<id>.log`, so the failure says why rather than only that
the process exited.

**Also settled while here**: `embedding` on TEI is now verified end to end, which PRM-179 had
allowed on a route-existence check — a weaker thing than it looked. Pinned to each replica of
one model, TEI and hf-serve agree to a cosine of **0.99999787** (`1 - cos = 2.1e-06`), which is
**4692x inside** the ±0.01 criterion Centinela set for SigLIP in `C-01 §1`. So the engine's own
`hidden_act=gelu` approximation warning is real as a warning and numerically irrelevant at that
tolerance.

**Scope**
- `_TEI_MODALITIES` gains `classification` and `zero_shot`; `text`, `vision`, `image` and
  `typed_decision` stay refused, the first three because TEI has no text generation at all.
- `("classification", "tei")` and `("zero_shot", "tei")` both publish `tei.predict.v1` — one id,
  because one endpoint and one body genuinely serve both.
- A guard test walks `_TEI_MODALITIES` and fails if a launchable pass-through modality publishes
  no schema, which would start fine and tell every caller "do not guess".
- Out: a live request through the gateway for the two new models, which needs `model:nli-tei`
  and `model:emotions-tei` grants that only a human administrator issues.

## PRM-185 — The blocking query promises wall seconds and counts awake ones

**Why**: Argus's `A-45` found that **89 % of the error spans across their whole platform in
seven days** — 663 of 747, over 48 services — were one pattern: `GET
/v1/backends?index=…&wait=60` ending in a client `ReadTimeout`. They then cross-referenced
`pmset -g log` and attributed 78 % of them to this Mac sleeping, concluding it was their
environment and not a defect of ours.

**Half of that conclusion is wrong, and the half that is wrong is ours.** Measured on this
machine:

```
time.monotonic()      205.50 h   <- what the blocking query's deadline uses
CLOCK_UPTIME_RAW      205.50 h   (excludes sleep)
CLOCK_MONOTONIC       678.97 h   (counts sleep)
wall since boot       678.97 h
```

`time.monotonic()` is `mach_absolute_time()` here, which tracks `CLOCK_UPTIME_RAW`: **474 hours
of sleep are invisible to it.** So `routes.list_backends`'s

```python
deadline = time.monotonic() + wait
while registry.index() == index and time.monotonic() < deadline:
```

measures a 60-second promise with a clock that stops. The machine sleeps mid-hold, wakes, and
the loop still believes it has nearly all its 60 seconds left — so it keeps holding. Their two
exact coincidences say the same thing from the other side: a span lasting 325.7 minutes against
a sleep episode of 325.7 minutes, matching to the decimal.

The sleep is their environment. **Failing to return within 60 wall-clock seconds is our bug**,
because `wait` is stated in seconds to a caller whose clock does not pause, and we implemented
it on one that does.

**Scope**
- Bound the hold by wall time as well as monotonic time, breaking on whichever expires first:
  monotonic still protects against an NTP step, and wall time stops a sleep from extending the
  hold. A hold that returns early is harmless — the contract is already "returns when the index
  changes **or** `wait` passes", and the caller re-asks with the index it still holds.
- It is the manager-api's route, not the gateway's. Worth saying because `A-45` attributes the
  span to `gateway`, and the gateway's own `/v1/backends` has no `index`/`wait` at all.
- Out: their remaining 143 failures with no sleep overlap. The same mechanism explains them if
  anything paused the tick counter that `pmset` does not log as a sleep episode, and
  `CLOCK_MONOTONIC - CLOCK_UPTIME_RAW` is the number that would settle it — but that is a
  measurement on their side, and this fix removes our contribution either way.

**And it is a third kind of instrument error**, after a test that ran against the wrong app and
a probe whose parameters the engine discarded: here the instrument is a **clock**, and it was
wrong about time rather than about data.

**Done.** Both deadlines, and the span now records `blocking_query.expired_by` as `wall` or
`monotonic` so a long hold in a trace does not have to be guessed at — and so Argus can tell a
sleep from an ordinary expiry in their own store.

The tests fake each clock in turn, since a sleep cannot be forced, and each one is scoped to
`routes.time` rather than to the real `time` module. **The first version patched the real
`time.monotonic` and deadlocked**: asyncio's event loop keeps its own clock there, so freezing it
means `asyncio.sleep` never returns. The clock under test belongs to one module, so that is where
it is replaced.

Two more things about those tests are worth keeping, because both were defects in the test rather
than the code:

- With the fix reverted, the frozen-monotonic case does not fail — it **hangs for ever**, which
  is exactly the symptom Argus reported. Verified by reverting. So each held request is bounded
  by a client timeout: a regression returns a readable assertion instead of a stuck suite.
- The override teardown lives in the fixture, not at the end of each test body. It was at the end
  of each body first, `app` is a module-level singleton shared by every test in the package, and
  a failing test leaked its auth override into `test_discovery.py::test_get_requires_auth` — a
  test that touches none of this and went green-to-`200`-instead-of-`401`. A fixture's teardown
  runs on failure; a line at the end of a function does not.

## PRM-186 — `raw_scores` reaches the contract, and the scope list comes out of it

**Why**: both are Axonium's `A-38`, and both are the same defect in opposite directions.

**`raw_scores` was described in a channel entry with measurements and never written into the
guide.** Their reason for refusing to implement it from a message is better than the field: *"that
is how a field ends up in five SDKs and in no allowlist check, and how a later re-vendoring
deletes it without anyone noticing."* It is the fourteen-times-named "one truth in two places"
defect, committed by us in the direction that costs most — the place we did not update is the only
one they read. Now in §3.6 with all three states: honoured where the engine has it, named in
`X-Prometheus-Ignored-Parameters` where it does not, and a `400` naming the engines that do.

**And the scope list in §6.3 was wrong, mine, two days old.** It said `default`,
`chat_completions`, `admin` and `predict`, omitting `embeddings` and `rerank` — which have had
their own buckets since PRM-129, as the paragraph immediately below it still said. Measured
against `_ENDPOINT_SLUG_MAP`: six scopes, and PRM-129 is not reverted. A caller indexing their
quota accounting against the short list would have been missing two buckets, which is precisely
what they said that list decides.

**Fixed by removing the list, not correcting it.** The guide now says the set is read from
`X-RateLimit-Scope`. A prose enumeration beside a map in code has exactly one future, and that is
Axonium's own answer from the same entry — they had five hand-written copies of this list across
five SDKs, already diverged, with TypeScript saying `chat` where the header says
`chat_completions`, published to npm the same day.

**Worth keeping**: two wrong copies of one truth, one per team, caught each other. They found
theirs by reconciling against ours; ours was wrong. No test on either side could have.

**Revision `2026-10-04b`.**

## PRM-188 — the envelope survives a validator's `ValueError`

**Why**: RM-65's handler promised that every body-validation failure leaves in the RFC 9457
envelope, so an SDK can type an error by `type` and correlate it by `request_id`. It kept that
promise for the errors it was tested with and broke it for a class it was not.

Pydantic attaches the original exception to every error raised by a *validator*:
`{'type': 'value_error', 'ctx': {'error': ValueError('...')}}`. A `ValueError` is not
JSON-serialisable, so `JSONResponse(content={... "errors": exc.errors()})` threw **inside the
handler**, and the request left as an unhandled exception — a 500, in no envelope, from the code
written to guarantee the envelope. A `ge=`/`le=` constraint carries `{'ge': 0}` instead, which
serialises, and that is the whole reason the gap stayed invisible.

**It was reachable before the change that found it**, by two validators `ChatCompletionRequest`
has carried for a long time: a `role` outside the four allowed, and RM-09's rule that an image
must be a `data:` URI. The second is the SSRF guard — so the gateway answered its own security
refusal with a 500, which reads to a caller as "the gateway broke" rather than "the gateway
protected itself."

**What let it survive is the shape of the tests, not their absence.** Both validators were
covered, directly, under `pytest.raises(ValidationError)`. That proves the *model* refuses; it
cannot say what the *route* answers, and the route was the broken half. Same pattern as the
`test_app` probe and the `candidate_labels` readiness check: the instrument asserted something it
did not measure. Every test added here goes over HTTP for that reason.

**Scope**: `jsonable_encoder(exc.errors())` in the handler — FastAPI's own default behaviour, one
import. In: the two reachable paths pinned over HTTP, the field-constraint class pinned as
unchanged so the fix is not paid for by the errors that already worked. Out: changing what `ctx`
holds — the encoder renders the exception as `{}` and Pydantic has already copied the validator's
text into `msg`, which is the field callers read.

## PRM-187 — `logprobs` reaches the engine

**Why**: Apeiron's `P2` #12 asks how confident the grounder was, so their agent can stop and ask
a human instead of clicking on a guess. llama.cpp has answered that question all along — the
gateway was the only thing in the way.

Leaving it to `extra` was not an option, for the reason `raw_scores` was declared in PRM-183: an
undeclared field is dropped and then named in `X-Prometheus-Ignored-Parameters` on *every*
engine, including the one that honours it. The caller is told the opposite of what is true.

**Both facts were measured against a running llama-server before anything was written.**
`logprobs: true` with `top_logprobs: 3` returns an OpenAI-shaped `logprobs.content[]`, one entry
per token, each with its `logprob` and its alternatives. And `top_logprobs` without
`logprobs: true` is refused — `400 top_logprobs requires logprobs to be set to true` — with
`logprobs: false` refused identically, so the flag must be present *and* true.

**That second one is why this gateway checks a pairing instead of forwarding it**, which it
almost never does. The 400 is llama.cpp's, in llama.cpp's error shape, and PRM-174 exists to stop
errors leaving by a door other than the problem+json envelope. One comparison keeps every refusal
in one shape.

**It also found PRM-188.** The validator added here was the first `model_validator` on a request
schema, and its refusal returned a 500 instead of a 422 — a handler defect reachable since long
before, which had to be fixed first and landed on its own. See [[PRM-188]].

**Scope**: in — both fields declared, forwarded only when set, the pairing rule, the 0–20 bound,
and §3.3 of the SDK guide with the natural-log note (a caller reading `-7.6` as a probability
gets a wrong answer silently). Out — emulating logprobs on engines that lack them, and the
`x-prometheus-revision` header this branch was originally named for: it needs a real revision
source, and there is not one yet (`pyproject.toml` says `0.1.0`, `telemetry.env` says `2.0.0`),
so inventing one would be a plausible-looking value from the wrong source. Still open.

## PRM-189 — TEI serves a local directory, not only a Hub id

**Why**: PRM-179 recorded that TEI's `--model-id` is a Hub id, "so a model with no `hf_repo`
cannot be served". That is half of what the flag accepts — it takes a local directory of
HF-format weights just as readily, verified by serving one.

The missing half was not a convenience. **Llama Prompt Guard 2 ships a `config.json` with no
`id2label`**, and TEI exits rather than start without it. The label map can only be added in a
local copy, so a model that could only be named by Hub id was unservable for a reason that had
nothing to do with the engine, the registry, or the model's weights.

The labels themselves were measured, not taken from convention: index 0 is `benign`, index 1 is
`malicious`, p=0.0004 on a benign prompt against p=0.9994 on an injection — and it catches the
Spanish injection too, which matters here.

**The local path wins when it is a directory that exists.** A local copy is something an operator
made on purpose, and silently preferring the Hub would serve weights that differ from the ones on
disk — the same class of failure as the one being fixed, pointed the other way. A `path` that is
absent, or that names a `.gguf`, falls through to `hf_repo`; every TEI row registered before this
carries an empty `path`, so all of them launch byte-identically, and that is pinned as a test
rather than claimed.

**The alternative was worse.** Putting the directory in the `hf_repo` field would have worked
today and is exactly the "plausible-looking value from the wrong source" this module already
refuses to do with `--revision`.

**Scope**: in — `_tei_model_id`, the five cases above as tests, and the PRM-179 docstring
corrected where it states the old rule. Out — a `local_path` column; `path` already means this
for every other backend.

## PRM-190 — a vision model cannot start without its projector

**Why**: `_build_llama_cpp_cmd` read `modality == "vision" and entry.mmproj_path`, and when the
path was empty it quietly dropped the flag. llama-server then started as a **text** model under
a `vision` label, and nothing anywhere said so:

* the launch succeeded;
* the readiness probe passed, because a readiness probe only sends text;
* the registry went on listing the model as `vision`;
* the first image request got `500 image input is not supported - hint: ... you may need to
  provide the mmproj`, from a model listed healthy for months.

**This is not hypothetical.** `qwen3vl-32B-Q4` has been in this fleet's registry as `vision`
with no projector since July. It was found while triaging Apeiron's model request, by running
it — not by reading the row — and the projector turned out to be sitting in the same official
Hub repo the weights came from, never downloaded. `Fara-7B` arrived with the same gap on the
same day, which is what made it a pattern rather than an accident.

**The claim already existed; only the enforcement was missing.** The CLI's help for
`--mmproj-path` has said "required for --modality vision on llama_cpp" the entire time, and no
code path checked it. That is the recurring defect in this codebase stated in one line: the
instrument asserted something it did not measure. The test suite agreed — a passing test named
`..._without_mmproj_path_omits_flag` documented the silent drop as correct behaviour, which is
how it survived review.

**Checked at launch, not in the registry.** `Registry.update` is deliberately permissive and
says why; `start_instance` does its own downstream checks, and this follows that.

**Scope**: in — a missing field and a missing *file* both refused, with a message naming the
symptom and the fix; the test that encoded the old behaviour replaced; other modalities pinned
as unaffected. Out — validating at register time as well. One enforcement point covers the CLI,
the API and the TUI alike, and a model can be registered before its weights finish downloading.

## PRM-191 — the hybrid deployment gets a file

**Why**: `podman-compose.yml` describes an all-container deployment and `docs/local-stack.md`
describes an all-bare-metal one, framed there as a choice — *"Bare metal or containers — one
choice, seven lines"*. **The mode this machine actually runs every day is neither**: gateway,
auth-service and both managers run directly on macOS while Redis runs in a container.

That hybrid had no file. So its one infrastructure dependency lived in whatever `docker run`
somebody last typed — a container belonging to **no compose project**, recreated by nothing, and
invisible to anyone reading the repository. `docs/local-stack.md` opens with the rule that exists
to prevent this, after a two-hour outage: *"nothing the stack needs to start may live only in a
shell."* A hand-typed container is worse than a shell variable, because it is not written down at
all.

**Redis is load-bearing, measured rather than assumed.** On the running stack: 12 connected
clients, 478 `GET`, and the two that matter — `INCRBY` (the rate limiter) and `SMEMBERS` (the
token-revocation list). Without it the gateway still starts and serves, logging
`rate_limit.redis_not_configured_fail_open`, and **silently loses both controls**.

**Why the base file could not simply gain the port.** Two reasons, one good and one not:

* The good one is `AC-8` of `memory/specs/004-podman-containerization.md`: in the all-container
  deployment Redis must not be reachable off-box, and that is correct.
* The other is that `AC-8` is enforced as a **string match** —
  `grep -v '^\s*#' podman-compose.yml | grep -q '6379:6379'`. A loopback binding is written
  `127.0.0.1:6379:6379`, which *contains* that substring, so the check refuses the safe binding
  for the same reason it refuses an unsafe one. It tests the spelling, not the property. Worth
  naming: the same compose publishes `8000:8000` and `8090:8090`, which bind `0.0.0.0` — so two
  services are on the LAN today while Redis is refused even on loopback.

**Solved as an overlay rather than by touching either of those.** `compose.baremetal.yml` merges
onto the base `redis` service and adds exactly one key, a `127.0.0.1:6379:6379` binding. The
image, command and healthcheck keep one definition. The all-container path never passes
`-f compose.baremetal.yml`, so Redis has no host binding there, the containerised gateway reaches
it by service name as before, and `AC-8` keeps describing `podman-compose.yml` truthfully —
verified by running `test_containerization.sh`, which still passes.

Both neighbouring teams on this machine bind the same way: Centinela's valkey at
`127.0.0.1:6395` and Argus's collector at `127.0.0.1:4317-4318`.

**Scope**: in — the overlay, the runbook's launch sequence and its bare-metal/Podman table, and
replacing the orphan container with the compose-managed one. Out, deliberately and recorded as a
separate item — **nothing checks the overlay**. `test_containerization.sh` reads only
`podman-compose.yml`, and it is not run by `.githooks/pre-push` at all, which is why `AC-8` has
gone unexercised. Rewriting that check to assert the property (*no service publishes 6379 on
anything but 127.0.0.1*, across both files) and wiring the suite into the hook is likely to wake
other long-dormant criteria in the same file, so it is its own change.

## PRM-192 — the port check asks the real question, and the hook asks it

**Why**: two defects, and the second is why the first survived.

**The check tested the spelling.** `AC-8` of `memory/specs/004-podman-containerization.md` says
Redis is internal-only, and `test_containerization.sh` enforced it with
`grep -q '6379:6379'` against `podman-compose.yml`. A loopback-only binding is written
`127.0.0.1:6379:6379` — which *contains* that substring. So the check refused the safest binding
available for precisely the same reason it refused the most dangerous one, and PRM-191 had to
route around it with an overlay file that nothing then checked.

**And nothing ran the suite.** It was in neither `.githooks/pre-push` nor any CI phase, which is
how a broken criterion stayed broken without anyone noticing. A check nobody runs is a claim.

**What replaces it**: `gateway/tests/check_compose_ports.py` parses **every** compose file in the
repo root with PyYAML — already present in each project venv, and needing no container daemon,
which keeps the suite static as its own header promises. It asserts three things:

* Redis published anywhere but loopback is a failure, in any file;
* `podman-compose.yml` publishing Redis *at all* is a failure — the all-container deployment runs
  every service as a container on one internal network, where a host binding buys nothing and
  widens the surface. That is AC-8's original and correct intent, kept;
* every other binding reachable from the local network is **named and not failed**.

That last one is deliberate. `podman-compose.yml` publishes `8000:8000` and `8090:8090`, which
bind `0.0.0.0` — so the gateway and the manager API are on the LAN today while Redis was refused
even on loopback. Whether that should change is a security decision for a person, not something
to alter inside a test-fixing change, so it is printed on every push until someone decides.

**Globbed, not named.** The old check named one file, which is exactly how PRM-191's overlay
escaped it. A compose file added tomorrow is covered the day it lands.

**Scope**: in — the checker, 18 tests pinning it (including the four spellings of `ports:`, where
an omitted host address means *every* interface and reading it as restrictive would pass the very
bindings this catches), the bash suite rewired, and the suite added to `pre-push` as phase 11 of
12. Out — changing the two `0.0.0.0` bindings, which is the open decision above. The spec is
unedited: `memory/specs/` is a historical record, so this entry supersedes AC-8's *verification*
while AC-8's *intent* is kept intact.

## PRM-193 — the control plane is loopback by default

**Why**: PRM-192 made the port checker print, on every push, the two bindings in
`podman-compose.yml` that reach the local network. Reviewing them gave two different answers, and
the difference is the point.

**`8000` (gateway) is not a defect.** `AC-10` of `memory/specs/004-podman-containerization.md`
requires `curl http://<server-ip>:8000/health` **from another machine**. It is the product's
entry point and the exposure is specified. Unchanged.

**`8090` (manager) was wider than its own justification.** The comment above it named two
consumers: the gateway "on the internal network" — which reaches it by service name,
`MANAGER_URL=http://manager:8090`, never through a host port — and "from the host for debugging",
which `127.0.0.1` serves. Neither needs `0.0.0.0`. What sat there instead is the **control
plane**: start and stop models, register and delete fleet nodes, launch downloads.

Two facts decided it. No installer in this repository configures a firewall — `install-rhel.sh`
and `install-ubuntu-dgx.sh` touch neither `firewalld` nor `iptables` — so the compose binding *is*
the control. And the repository already holds this exact instinct for a sibling service, in
`docs/local-stack.md`: *"`auth-service/start.sh` also works but binds `0.0.0.0`, which publishes
the identity service to the local network."* The manager never got the same treatment.

**Severity is moderate and was measured, not assumed.** Every route answers `401` without a
token; only `/health` does not. This is defence in depth, not an open door — which is why it is
hygiene rather than an incident.

**Why a variable and not a constant.** One deployment genuinely needs it wider: PRM-152 has every
node report liveness to the coordinator every 10 s, so on a multi-host fleet the coordinator's
8090 must be reachable from other machines. `${MANAGER_BIND_IP:-127.0.0.1}` ships closed and is
widened by declaring it in that deployment's own environment — the standard shape, and the only
option that leaves neither deployment insecure by omission.

**It also required fixing PRM-192's own checker**, found by running it rather than reading it: it
split `${MANAGER_BIND_IP:-127.0.0.1}` on the colons *inside* the braces and reported nonsense. It
now resolves `${VAR:-default}` and `${VAR-default}` before parsing, and leaves a form with no
default unresolved — so the unknown case reads as permissive, the same cautious direction as an
omitted host address.

**The honest limit, pinned as a test**: this reports the posture the repository *ships*. An
operator exporting `MANAGER_BIND_IP=0.0.0.0` has widened it deliberately in their own
environment, and the checker says nothing about that — which is the entire point of making it a
variable.

**Scope**: in — the binding, the runbook's fleet section with the multi-host instruction, the
interpolation fix, and the checker's note rewritten to say the remaining `8000` was reviewed and
is intended, so a future reader does not re-litigate it and anything *new* appearing there stands
out. Out — `8000`, decided above; and TLS or a reverse proxy in front of the gateway, which is a
larger question than a port binding.

## PRM-194 — the context length is nobody's decision

**Why**: `pmgr register` prompts `Context length [4096]`, and **nothing anywhere compares that
number to the model**. Nine of the ten `llama_cpp` rows in this registry are therefore a default
somebody pressed enter on, not a decision — audited by reading `*.context_length` out of each
GGUF rather than trusting the registry:

| model | registered | native | serving |
|---|---|---|---|
| `qwen36-35b-a3b-q4` | 4,096 | 262,144 | **1.6%** |
| `qwen3vl-8b-q4` | 8,192 | 262,144 | 3.1% |
| `qwen3-0-6b` (×2) | 4,096 | 40,960 | 10% |
| `qwen3-reranker` | 4,096 | 40,960 | 10% |
| `qwen3-embedding` | 4,096 | 32,768 | 12.5% |
| `qwen3vl-30b-a3b` | 32,768 | 262,144 | 12.5% |
| `fara-7b` | 32,768 | 128,000 | 25.6% |
| `gpt-oss-20b-mxfp4` | 131,072 | 131,072 | 100% |
| **`qwen3-8b-q6`** | **131,072** | **40,960** | **320%** |

**The last row is the one that is actually wrong**, and it fails in the direction nobody notices.
llama.cpp does not refuse a context beyond the trained length — it RoPE-scales and keeps serving,
so the model answers *worse* with no error, no warning in the response, and nothing in the
registry to say so. It also holds **18.0 GiB of KV cache** to do it, against 5.6 GiB at its real
40,960. The same request pattern costs 12.4 GiB more and returns lower quality.

**The ones serving a fraction are not wrong, they are unchosen.** Context costs KV cache and
small is a legitimate answer; what is missing is that anyone chose. The cost is per model and far
from uniform — computed from each GGUF's own attention geometry, validated against measured RSS
(35B: 20.9 GiB predicted vs 21.5 measured; fara-7b 7.4 vs 7.9; 30B-A3B 21.3 vs 20.1):

The 35B is the clearest case. It has **2 KV heads**, so its cache is the cheapest per token of
every large model here — 0.08 GiB per 1K against 0.14 for an 8B. It can hold 131,072 tokens for
10 GiB, a 32× increase for less RAM than the 8B currently wastes.

**Scope**: in — `read_gguf_context_length` reusing the GGUF reader `hf_discovery` already had
rather than adding a second one; the `register` prompt defaulting to the model's own figure;
`pmgr list` showing `registered/native` so an existing row can be judged at a glance. Out —
picking each model's number, which is a RAM budget decision per deployment; and KV-cache
quantization (`--cache-type-k/v q8_0`), which halves the cost at a quality trade and deserves
measuring on its own.

**Delivered, and one decision inside it worth recording: it warns, it does not refuse.** PRM-190
refused a vision model with no projector, and that was right because such a model *cannot* serve
an image. This is different in kind — a context beyond the trained length **works**, just worse,
and RoPE scaling is a capability llama.cpp offers deliberately. Refusing would make this platform
unable to do something the engine can. So an explicit `--context-length` is obeyed and the
over-commitment is said out loud, with both numbers in the message.

The reader was widened rather than duplicated: `_gguf_read_value` decoded strings and stepped over
everything else, which was all `read_gguf_architecture` ever needed. It now returns scalars too.
Its three existing callers are unaffected — two discard the result and one checks
`isinstance(value, str)` — and that is pinned by a test, because "one truth in two places" is the
defect this repository meets most.

`pmgr list` gained the column because fixing registration fixes nothing already registered. The
35B sat at 4,096 of 262,144 for months and the row looked entirely ordinary; what makes it legible
is the model's own figure printed beside it, with the over-committed case the only one coloured —
it is the only one that is wrong.

## PRM-195 — the template switch reaches the engine

**Why**: repo2deck, building a repo-to-slides tool on this platform, measured `qwen36-35b-a3b-q4`
spending **1,500 to 6,000 tokens thinking before every slide**, 80 to 140 seconds a call. They
found the switch that turns it off, confirmed it worked against `:8201` directly, and found it had
no effect through the gateway.

It was `PRM-127` working exactly as designed: the request body is an allowlist, and
`chat_template_kwargs` was not in it, so it was dropped and named in
`X-Prometheus-Ignored-Parameters`. The mechanism told them; nobody was reading the header, which
is the guide's failure before it is theirs.

Reproduced independently before writing anything:

```
without                        215 tokens   6.91 s
chat_template_kwargs            16 tokens   0.71 s     (enable_thinking: false)
```

Same useful answer. Tenfold, which is the difference between usable and not.

**They asked for three fields and this forwards one, and that is the part worth keeping.**
Against a running server, top-level `reasoning_effort` and `reasoning_budget` changed *nothing* —
identical output and identical reasoning length with and without. Forwarding them would have moved
them out of the ignored-parameters header and reported as honoured what the engine silently
discards: the inverse of the bug being fixed, and harder to notice than the original. They stay
dropped, and a test pins that.

`reasoning_effort` does work — **inside** the mapping, where the template reads it. Measured on
gpt-oss: 414 characters of reasoning becomes 23 on `low` and 583 on `high`, monotonic. So one
field carries every case, and the answer to their request is smaller and more correct than the
request.

**Forwarded as an opaque mapping**, like `response_format` and `tools`: the keys belong to each
model's own chat template, and a whitelist of them here would be a second copy of someone else's
Jinja, going stale with no test able to notice.

**What this does not fix**, said out loud in the guide: a key the template does not read is
ignored *by the template*, silently, and the gateway cannot detect that. The honest boundary is
that we guarantee delivery, not effect.

**Scope**: in — the field, forwarding, ten tests, §3.3 of the SDK guide with the measurements and
the explicit warning against the two top-level fields, and the note that reasoning is billed in
`completion_tokens` with no separate count because the engine reports none. Out — `P2`
(normalising `items: false`) and `P3` (cancellation on the non-streaming path), which are their
own changes.

## PRM-196 — an abandoned request lets go of the engine

**Why**: repo2deck stopped an exploration and found slots busy for minutes, generating answers for
clients that no longer existed. Reproduced here: a caller cut at 4 s left the slot busy a further
**~45 s** and the engine produced the full **3,000 tokens** for nobody.

**The streamed path never had this**, and that contrast is what located the bug. A streamed
response is a generator, so a hung-up caller raises `GeneratorExit` at a `yield`, the `finally`
closes the upstream connection, and llama.cpp sees the socket go — measured at ~0 s. The
non-streamed path simply awaits a complete response, and nothing in that await observes the
caller at all.

**Two wrong attempts preceded the right one, and both failed for reasons worth recording.**

*Polling `request.is_disconnected()`* — the obvious approach, and it works in a bare uvicorn app
(3.03 s after a cut at 3 s). Inside this gateway it polled **101 times returning False** while the
engine generated 3,000 tokens. The handler's `_receive` turned out to be
`BaseHTTPMiddleware...receive_or_disconnect`: Starlette's decorator-style middleware consumes
`http.disconnect` for its own bookkeeping and never passes it inward. This app has two of them —
`idempotency_middleware` and `request_id_middleware` — both written that way because they need to
see the *response*, which is what that base class is for.

*A passive tap in an outermost middleware* — never saw the message either, because ASGI is
pull-based: `http.disconnect` is only delivered when something calls `receive()`, and during a
long handler await nothing does. Making the tap poll actively did surface it, and broke the
request: it consumed the body message, so nothing reached the backend at all.

**The answer is vLLM's, and they met this exactly** — `vllm-project/vllm#10087`, fixed in `#11190`.
Their `with_cancellation` carries the note *"This does not use request.is_disconnected, which does
not work with middleware."* A dedicated task **awaits `request.receive()`** and is raced against
the handler with `asyncio.wait(FIRST_COMPLETED)`. Awaiting works where polling does not, because
`is_disconnected()` reads under an already-cancelled anyio scope and so only sees a message that
is already sitting there.

**Its precondition is the piece that explains the second failed attempt**: the body must already
have been read, because the listener consumes and discards everything while it waits. FastAPI has
parsed the body into a pydantic model before the handler runs, so at that point the only message
left is the disconnect. In an outermost middleware that precondition does not hold.

**Cancelling is not the same as not billing**, and RM-87 already settled which side we are on: a
caller who walks away mid-generation is the case most worth charging for. `ClientGone` is raised
rather than swallowed, and the call site accounts for the work through `_detach`, which survives
the teardown. This matches where the industry has landed — a gateway that lets the upstream run to
completion bills for tokens nobody received.

**Verified live**: 0 tokens generated after the cut at +8 s, +16 s and +24 s, against 3,000 before
the change, with `inference.client_disconnected` recorded once. Non-streamed, streamed and
embeddings all still answer normally.

**Scope**: in — the listener, the race, `499` at the chat route (nginx's convention; returning
`None` is vLLM's `#42794`, where FastAPI serialises a 200 with a `null` body), 13 tests including
guards against both wrong attempts. Out — embeddings, rerank and predict, which complete in
milliseconds and would pay a listener task for no benefit; and `/v1/images/generations`, which is
long enough to deserve the same treatment and is its own change.

## PRM-197 — a tuple schema survives the grammar converter

**Why**: repo2deck sent valid JSON Schema and got
`400 JSON schema conversion failed: Unrecognized schema: false`. llama.cpp's schema-to-grammar
converter does not implement **boolean** subschemas, and a boolean `items` is exactly how zod 4
closes a tuple.

Their workaround was to widen the tuple into `items: {"type": "string"}`, which costs the thing a
tuple is for: the second element stops being an integer. A client rewriting their data model to
get past our converter is the signal that this belonged here.

**The scope is one keyword, and that is a measurement.** Sixteen features were probed against a
running server. Only `items` fails when given a boolean — for `true` as well as `false`.
`additionalProperties`, `propertyNames`, `contains` and `not` all take one happily, as do `enum`,
`oneOf`/`anyOf`/`allOf`, `$ref`/`$defs`, `pattern`, `format`, `const`, `minimum`/`maximum`,
`uniqueItems` and `minItems`/`maxItems`. Normalising more would be a second, drifting copy of
someone else's validator.

**Nesting was measured too**: the same tuple fails inside `properties` and inside `$defs`, so a
top-level fix would have repaired the example and left the real schemas broken.

**It is a translation, not a relaxation.** In draft 2020-12 `items` applies to elements after
`prefixItems`, so `items: false` forbids anything past the prefix — which is a maximum length —
and `items: true` permits anything, which is what an absent `items` already means.

**`minItems` is deliberately not added**, and that is the easy mistake. A tuple schema does not
require its elements to be present: `["Ana"]` satisfies `prefixItems: [string, integer]`. Adding
`minItems` would hand the engine a stricter schema than the caller wrote, and a gateway that
quietly tightens a contract is worse than one that rejects it.

Verified live through the gateway with repo2deck's exact schema: `["Ana", 30]`, `finish_reason:
stop` — string and integer kept by position.

**Scope**: in — the normaliser as a pure function with 16 tests, wired in `to_llama_payload`,
returning new containers so the caller's own `response_format` is never mutated; §3.3 of the SDK
guide with the measured feature list and the warning against widening tuples. Out — the other
boolean positions, which the engine accepts; and any broader schema rewriting.

## PRM-198 — an abandoned image lets go of what it can

**Start with what this does not do.** `/v1/images/generations` is the longest blocking call this
gateway makes, so it looked like the place where PRM-196's race would pay most. It does not,
because **sd-server does not abort on connection close**. Measured, with a warm baseline:

```
calentamiento              12.7 s
referencia (caliente)      12.9 s
>>> cancelled at 1.0 s
next request               24.6 s     <- queued behind the cancelled job
```

The cancelled generation ran to completion and the next request waited for it. llama.cpp watches
the socket and stops; sd-server does not, and it offers nothing to ask with — `/cancel`, `/abort`,
`/slots` and `/metrics` are all 404 on it. **The GPU is not freed, and PRM-196's headline does not
transfer to this route.**

**What it does do is still worth having**, and all three are real:

* **The budget reservation is released.** RM-60 debits the caller's monthly cap *before*
  forwarding. Without this the cap stayed debited for an image they never received until the
  period rolled over — a caller silently losing spend for nothing.
* **A hang-up stops being reported as a backend failure.** The route's `except Exception`
  catch-all sits below, and it would have logged `images_generations.upstream_error` and fed the
  circuit breaker. A caller closing a tab is not a sick replica, and opening a breaker over it
  would take a healthy backend out of rotation.
* **The gateway stops waiting**, so its own worker and connection are returned.

Settled at **zero** cost, which is a decision rather than an omission: an image is all-or-nothing.
Chat bills what was streamed because those tokens reached the caller; here nothing was delivered
and there is no partial image to price.

**A correction to PRM-196's own record.** Its commit message says the abandoned work "is billed and
metered". Only the second half is true: that path calls `metrics_store.record_inference`, not
`_record_usage`, so there is no billing row. Which is right, and consistent with RM-83 — the
streamed path bills what was *sent*, and on an abandoned non-streamed request nothing was sent.
The claim was wrong, not the code.

**Scope**: in — the race on the image route, placed above the catch-all, with the reservation
settled and the disconnect recorded; three tests pinning the wiring, the ordering against the
catch-all, and the settle. Out — making sd-server abort, which it has no surface for; if it ever
gains one, this side is already in place.

## PRM-199 — a deleted client leaves its data behind

**Why**: found while auditing the 23 credentials on this deployment for which could be removed.
`DELETE /admin/clients/{id}?permanent=true` removes the principal and cascades
`credential_share_tokens`, which share its database. **The gateway's tables are a different
database with no foreign key to it**, so everything keyed by that `client_id` simply stays.

Not a prediction. Counted in `gateway.db`:

```
usage_daily                19 client_ids with no credential
usage_events                2
client_billing_settings     1   — still carrying a $10 monthly cap and an 18% tax rate
```

That last one is the uncomfortable member. A billing *policy* survives the principal it governed,
so a `client_id` reissued to someone else would inherit a cap and a tax rate nobody set for them.

**Usage rows are a different question from settings.** Deleting them destroys billing history, and
history is exactly what a usage ledger is for — the right answer there is probably to keep them
and mark the owner gone, not to cascade. Settings and idempotency records have no such claim.

**Scope**: in — decide per table whether a deletion should cascade, orphan-tolerate, or refuse;
a purge path for what already exists; and a check that names orphans rather than leaving them to
be discovered by an audit. Out — doing it as part of the credential cleanup that found it, which
is an operational task rather than a change.

## PRM-200 — three empty database files that are not the database

**Why**: `auth.db`, `auth-service/auth.db` and `gateway/gateway.db` all exist and are all **0
bytes**. The live databases are `data/auth-service/auth.db` and `gateway.db` at the repo root,
established the way `docs/local-stack.md` insists — by asking the running processes which files
they hold open, not by reading a config.

A file named `auth-service/auth.db`, sitting beside the service, looks like the answer to "where
is the auth database". It is empty, and anyone querying it finds a working system with no users.

This is the exact shape of the failure `docs/local-stack.md` was written after: *"A value in the
repo-root `.env` looks authoritative and is invisible to a bare-metal process. To find out what a
service is actually using, ask the service, not a file."* That outage took two hours. These three
files are the same trap in a different place, and they cost ten minutes during this audit.

**Scope**: in — remove them, after establishing for each that nothing creates it on startup (an
empty file that a service recreates is a symptom, not litter); and a line in the runbook's
"Diagnosing" section naming the two real paths. Out — changing where either database lives.

## PRM-201 — the sidebar folds and Users becomes scannable

**Why**: the nav was a fixed 256px and the Users table stacked one chip per granted model inside a
`max-w-xs` cell. The widest grant here is eleven models, which made that row roughly 400px tall —
**two users on a 1080p screen**, in a table whose job is comparing users.

**The sidebar** collapses to a 64px rail, persisted so it is a preference rather than a gesture.
Its tooltips render through a portal, and that is not over-engineering: positioned beside the icon
they never appeared, because the nav scrolls and `overflow-y: auto` computes `overflow-x: auto`
too. Measured in the running page — the tooltip's box ran to 134px against a clip at 64. Any
ancestor that scrolls does this, so moving the scroll elsewhere only moves the bug.

**Models fold to three plus a count.** Three is what fits on one line at the narrowest column
width, so rows are uniform and the table can be scanned down a column instead of read.

**Search covers the model scopes**, which is the point rather than a bonus. The question this page
could not answer was "who can reach `fara-7b`" — the grants existed only as chips inside rows. A
matching chip is pulled to the front and highlighted, because a filter that hides the reason a row
survived reads as broken.

**Scopes are summarised by family, with the privileged ones accented.** The first pass collapsed
them to "2 scopes" and hid the only interesting thing; printing them all hides it too, and for the
same reason. Measured: **eleven of fourteen credentials carry exactly `inference:read` +
`inference:stream`**. All the information is in the three that differ — the registry writer, the
two fleet nodes, and the one credential with `admin:write`. So each family is one chip carrying its
verbs, `admin` and `backend-registry:write` are accented, and a scan shows a wall of muted
`inference r·s` with the exceptions standing out.

**Three fields were already in the API and discarded by the UI**: `label` (set on 11 of 14 and
carrying what the name does not — "Video Vigilancia", "Device Control"), `token_ttl_seconds` (four
distinct values in use, and the longest sits on the credential holding `admin:write`), and
`created_at`, rendered as an age because "30d" answers "is this leftover?" while a timestamp makes
you do the subtraction.

**Destructive actions moved behind a menu.** Deactivate and Delete were bare adjacent icons
distinguishable only by glyph — the two that cannot be undone, one pixel-perfect click apart. They
are now labelled, separated by a rule, and behind a deliberate second step.

**Scope**: in — the collapsible rail, the Users table, `ScopeSummary`. Out — the other nine routes,
which share the sidebar and are unaffected; and `last_used`, which is not in the API at all. See
[[PRM-202]].

## PRM-202 — nobody records when a credential was last used

**Why**: found doing the thing the data should have supported. Asked which of this deployment's 23
credentials could be deleted, the honest answer required joining `usage_events` in the **gateway's**
database against the principal list from **auth-service** — two separate stores with no key between
them, reconciled by hand.

auth-service stores `created_at`, `updated_at`, `is_active` and `revoked_at`. **Nothing about use.**
It is the service that issues every token and it does not record that it did.

**The gateway's usage table is not a substitute**, and the audit proved it rather than assuming it.
Three credentials showed zero rows there and were live and load-bearing: the two fleet nodes, which
only ever call `/v1/fleet/nodes/{id}/heartbeat`, and `gateway-manager-sync`, which only syncs the
catalogue. Deleting on that evidence would have taken the fleet down. A credential can also mint
tokens without ever reaching an inference route, and nothing anywhere would show it.

**Scope**: in — a `last_used_at` on the principal, stamped at token issuance, which is the one
place that sees every use regardless of what the token is then spent on; surfaced in the Users
table beside the age that PRM-201 added. Out — per-endpoint usage, which the gateway already
records and bills from; this is about liveness, not accounting.

## PRM-203 — the admin shell is cached without saying so

**Why**: hit first-hand while verifying PRM-201. The UI was rebuilt, the new bundle was on disk and
named by the new `index.html`, and the browser went on loading the previous one:

```
on disk          assets/index-CdkjFYZV.js   (the rebuild)
browser loaded   assets/index-DbpWk7U2.js   (the previous build)
```

`curl -I` on the shell explains it — `etag` and `last-modified`, and **no `cache-control` at all**:

```
HTTP/1.1 200 OK
last-modified: Wed, 07 Oct 2026 00:19:58 GMT
etag: "eca0725e7d4224feb454fdf6243a412a"
```

With no explicit directive a browser is free to apply heuristic freshness — commonly a fraction of
the time since `last-modified` — and serve the shell from cache without revalidating. The shell
names the hashed bundle, so a stale shell pins a stale application. Every asset under `assets/` is
content-hashed and therefore safe to cache forever; `index.html` is the one file that must not be.

**What makes this worth an item rather than a shrug**: the failure is silent and asymmetric. The
operator who deploys has just hard-reloaded and sees the new UI; the people who did not are on the
old one with no indication, and the bug reports that follow describe behaviour that was fixed.

**Scope**: in — `Cache-Control: no-cache` on `index.html` (revalidate, not "don't store": the etag
still saves the transfer), and a long immutable max-age on the hashed assets, which currently carry
no directive either and are re-fetched more than they need to be. Out — a service worker or any
versioning scheme; the content hashes already do that job.

## PRM-204 — Instances says what a client would send

**Why**: eighteen columns. Six of them are performance metrics, and a metric only exists once a
request of the right shape has happened — so on this deployment they are `—` for nine of twelve
rows. Eighteen columns carrying four populated ones made every row scroll sideways and nothing
comparable.

**The field that mattered was missing entirely: `model_slug`.** It is in the API response and was
rendered nowhere, and it is *what a client puts in its `model` field*. The instance id is not:
granting `model:qwen3-embedding-0-6b-q8-0-local` does nothing, because the slug is
`qwen3-embedding`. That distinction has already cost time in this project — it is the page someone
opens to look the name up, and it was the one thing the page did not show. Now under every id.

**Three more were in the response and discarded.** `error_message`, so a failed start says why
rather than showing a red badge and sending you to the logs. `context_length`, which PRM-194 just
turned from a default somebody accepted into a decision. `file_size_bytes`, because "how much disk
is this costing" is asked on exactly this page.

**Two column sets behind a toggle** rather than one row of eighteen. Health is the default — what
is running, how much is it holding, for how long — and Performance carries the metrics for whoever
came looking for them.

**Bulk start and stop**, sequential and not parallel: each start loads weights, the largest here is
20 GB, and firing twelve at once would have the machine swapping rather than serving. A failure
stops the rest, which is what you want when the reason the second failed is that the first took the
memory. The toast reports `4 of 6` rather than claiming success.

**Seven bare icons became one contextual button and a menu.** Start and Stop were separate buttons
of which exactly one was ever enabled, so every row carried a permanently dead control, and Delete
sat two glyphs from Restart.

**A correction to this session's own record**: `npx tsc --noEmit` was reported as passing several
times while checking nothing. This project uses TypeScript project references with `files: []` at
the root, so that command is a no-op; the real check is `tsc -b`, which `npm run build` runs. The
builds were genuine, the standalone typecheck claims were not.

**Scope**: in — the two views, the slug, the discarded fields, bulk actions, the action menu. Out —
grouping by node, which the filter already serves.

## PRM-205 — the landing page says when something is wrong

**Why**: every number on Overview rendered identically. `Error rate 33.3%`, `Latency p50 12,004 ms`
and `Nodes 2 / 2 active` were the same grey, in the same card, with the same orange icon tile. The
page a person lands on could not tell them anything was wrong.

**`tone` is set only where the judgement is unambiguous**, and the restraint is the design rather
than a gap:

* **Circuits open** — a tripped breaker means the gateway has stopped sending traffic to a backend.
  There is no reading of that which is fine.
* **Nodes active vs configured** — a configured node that is not active is capacity the platform
  believes it has and does not.
* **Latency — deliberately uncoloured.** A p50 of twelve seconds is alarming for an embedding and
  entirely normal for a 20B model writing three thousand tokens. A threshold invented here would
  manufacture alarms, and a colour that cries wolf stops being read.

**The error rate needed its denominator before it needed a colour.** The dashboard was reading
`33.3%` while the gateway had served **three requests** — one failure out of three, after a
restart. Colouring that red would have been a false alarm about a platform that was fine. So the
card now carries `of 3 requests`, and below a usable sample it stays neutral and explains on hover
that the rate is not worth reading. Above it, 1% warns and 10% is bad.

**Every tone carries a reason.** A colour that cannot explain itself is a question rather than an
answer, so each coloured card says on hover what made it that colour — including the uncoloured
latency, which explains why it is staying out of it.

**Scope**: in — `StatTone` on `StatCard`, and the three Overview judgements. Out — thresholds for
latency or tokens, which are deployment- and model-specific and belong in `Limits` with real
configuration behind them rather than hardcoded in a card.

## PRM-206 — Nodes tells cordoned from not answering

**Why**: the page rendered one badge driven by `is_active`. That field is **derived** by the
coordinator from two facts it deliberately reports beside it, and `fleet.py` states the reason in
the serialiser itself: *"'cordoned' and 'not answering' need different actions from an operator and
a single boolean cannot tell them apart."* Before PRM-151 they shared one column, and a maintenance
cordon was erased by whoever pressed Check next.

The backend did that work. **`enabled` and `last_seen_at` were in the response and declared nowhere
in the UI's `Node` type**, so the frontend threw the distinction away and showed the collapsed
boolean — the same shape of loss as `label` in PRM-201.

**The heartbeat is the point of this page and was the thing it did not show.** Nodes report every
10 s against a 60 s liveness TTL, and the only way to see when one last reported was to open
`fleet.db` — which is exactly what the credential audit had to do to establish that two
zero-inference credentials were live and load-bearing.

Three states now, because they want three different responses:

* **Active** — allowed and reporting inside the window. Leave it alone.
* **Cordoned** — `enabled: false`. Deactivated on purpose, possibly perfectly healthy. Press
  Activate when maintenance is done.
* **Not answering / Never seen** — allowed but silent. Go and look at the box. "Never seen" is
  called out separately because it is almost always the three fleet identity variables missing from
  that node's environment, which `docs/local-stack.md` covers.

The 60 s threshold is `DEFAULT_LIVENESS_TTL_S` read from `fleet.py` rather than a number picked
here — a second copy of that rule is how two answers to one question start disagreeing, which is
what `nodes_client.py` already refuses to do for `is_active`.

**`$/hour` and `margin` were two bare numbers.** `$0.3228` and `1.3×` with no unit and no
explanation, and nothing in this codebase can derive either — they are an operator's input that
only this page shows back. Now labelled, with what they feed on hover.

**Verified live**: both nodes read Active with their real heartbeat age (5 s and 10 s against a 10 s
reporting interval). The cordoned and not-answering branches are **not** exercised live — doing so
means cordoning a node other teams are currently calling — so they are reasoned code paths, and
this says so rather than implying otherwise.

**Scope**: in — the two discarded fields, the three-state badge with the heartbeat age, the cost
basis labelled, nine columns to six. Out — a UI test for the unexercised branches; this project has
no frontend test runner, and adding one is its own change.

## PRM-207 — Usage answers who and how much

**Why**: four things, and the first was a trap.

**Two date controls that looked like one mechanism.** `Day`, top right, drove the table. `From` and
`To`, in a card below it, drove only the CSV export. That split is deliberate — RM-60 kept
`GET /v1/usage` single-day on purpose — but nothing on the page said so, and a pair of date inputs
sitting above a table reads as a filter on it. Now the control that drives the table says
*Showing*, and the export card is titled *Export a date range — does not change the table below*.

**The day's totals had to be added up by hand.** Three cards now carry requests, tokens and cost.
The cost card states its own caveat rather than printing a bare number: RM-33 never bills an
unpriced model as `$0`, so a total that silently omitted one would be that same lie in aggregate.

**Rows came in whatever order the group-by produced.** The question asked of this page is "who is
using the platform", which is answered by reading down a sorted column. Sorted by cost, falling back
to tokens so an unpriced client still ranks rather than sinking as a null.

**A share column**, because proportion is read off a shape far faster than off a column of numbers.
Measured against tokens rather than cost, so the one model with no configured price does not appear
as no usage at all. A client under half a percent reads `<1%` rather than `0%` — 272 tokens is use,
and a bar drawn beside a zero contradicts itself.

**Four cost columns became one.** Prompt, completion and image are still there, on the total's
tooltip and in the per-model rows. Detail does not need a permanent column each.

**And the expansion refused to answer its own question.** It opened only when a client had used more
than one model, so a client that called exactly one could not see *which*. It is the same question
either way.

**Unlike PRM-201, PRM-204 and PRM-206, there was nothing discarded here.** Those three each found
fields the API returned and the UI dropped; this page already uses everything `GET /v1/usage` gives
it. Worth recording, because "look for the thrown-away field" is a good first move and not a
universal one.

**Scope**: in — the date-scope labelling, the totals, the ranking, the share column, the cost
collapse, and always-expandable rows. Out — a usage-over-time chart: Billing already carries one
per client, and this page's job is the per-day, per-model breakdown rather than a second trend.


## PRM-208 — Usage over a range, with the data to act on it

**Why**: PRM-207 closed by ruling a trend chart out of scope, on the reasoning that Billing already
carries one. That was wrong in a specific way: Billing's chart is *per client*, so **all clients over
time** was answerable nowhere, and a page whose job is "who is using the platform" was answering it
one UTC day at a time — two rows and half a screen of empty.

Dropping the day also dissolved the ambiguity PRM-207 had settled for *labelling*: a `Day` picker
driving the table and a `From`/`To` pair silently driving only the CSV. One range now drives the
chart, the ranking and the export.

**Then the ranking had to be worth ranking.** Sorted-by-cost tells you who to look at and nothing
about what to do, so five things were added, each measured against live data first:

- **Request kind.** The platform serves five — chat, embedding, rerank, predict, image — and the page
  showed none. An `image` request carries **zero tokens** and still costs money: six of them are 9%
  of the entire bill. The share bar was measured in tokens, so the fourth most expensive client on
  the platform rendered as a bar at zero beside its own cost column. Share is now measured in cost,
  which is what the table already sorts by, and an image row says `1 image` rather than a bare `0`.
- **Effective rate (USD/Mtok).** Spans **800×** across live clients, $0.10 to $80.38. It is the
  number that distinguishes an expensive model from heavy use, and it had to be computed by dividing
  two columns by hand. Null, not `Infinity`, when there are no tokens to divide by.
- **Average tokens per request.** 7,121/req on one client, 37/req on another. Same bill, opposite
  remedy — one needs its context trimmed, the other a rate limit.
- **Prompt/completion split.** Completion prices at roughly 4–5× prompt, so the split decides between
  caching the prompt and capping `max_tokens`. It existed, on a tooltip.
- **Month-to-date against the cap.** Deliberately *not* derived from the selected range: a cap is a
  calendar-month limit, and measuring an arbitrary seven-day window against it would be this repo's
  recurring defect — the instrument asserting something it did not measure. The fields carry their
  period in their names and the column header states it.

**The kind mix and the busiest model** close the last gap: the five kinds were readable only by
expanding a client row, and four cards describing the range in the abstract — volume, tokens, money,
headcount — named nothing you could act on. Both are folded out of `by_model`, which the range
response already carries, so neither costs a request. The mix is counted in **requests**: tokens
would erase images, which have none, and cost would make the chart a second copy of the share
column. `minPointSize` keeps a kind with one request visible beside one with eight thousand —
a bar at zero pixels reads as "not used" rather than "barely used", and those are different answers.

**Scope**: in — `GET /v1/usage/range` (per-day, per-client, per-(client, model, kind), plus
month-to-date and caps), `query_client_cost_range` and `query_client_model_cost_range`, the presets,
the trend chart, the kind-mix chart, the busiest-model card, the one expandable table, and the five
columns above. Out — error rate and latency per client: `usage_events` records neither, so the page
cannot answer "who is failing" or "who is slow" and does not pretend to.


## PRM-209 — Models says what is on disk

**Why**: the page describes itself as the place to "manage what's on disk" and never stated the
figure. Measured: **522.9 GB** across 32 models on the local node, of which **443.4 GB — 85% —** is
held by **21 models with no instance**. The five largest idle: `minimax-m2-q2` 77.6 GB,
`laguna-s-2.1-q4` 70.0, `llama4-scout-17b-q4` 60.9, `deepseek-v25-1210-iq1m` 49.1,
`qwen3-coder-next-30b-q4` 45.2. Every one of those numbers required reading 32 rows and adding by
hand, on the page whose job is to answer exactly that.

**One em dash meant two different things.** A model with `downloaded: false` is served from another
host: no file exists here, so a size or quantization *cannot*. A model with `downloaded: true` and a
null size is on disk and the size was not reported — on this platform that is `promptguard2-tei` and
`minimax-m27-iq2m`, the latter a GGUF split across three shards. Both drew the same `—`, so "not
applicable" and "we failed to find out" were indistinguishable; only the second is worth chasing.
`downloaded`, not the node, is the discriminator — the totals use it too, so weights hosted
elsewhere are never counted as occupying space here.

**The description was contradicted by its own table**, ending "only downloaded models can be
selected when creating an instance" while `minilm-hfserve` sits two rows below it, not downloaded,
with two instances.

**Two things deliberately left alone.** The `model: <slug>` subtitle duplicates the name on 24 of 32
rows, and removing it where it duplicates was the obvious fix — but PRM-112 made it unconditional on
purpose, because a subtitle appearing on some rows and not others gave "which of these do I put in
`model`?" no reliable answer. That reasoning still holds and this item does not undo it. The node
selector stayed in the header rather than moving next to the table: it governs the Discover tab too,
so the header is where it belongs. It was simply never labelled.

**Scope**: in — the three figures, the `No instance` filter (which sorts by size, because an idle
363 MB model and an idle 77.6 GB one are not the same finding), the `n/a`/`?` split, the corrected
description, a labelled node selector, and dropping the row-number column. Out — bulk delete from
the filtered view, and any change to what the catalog reports.


## PRM-210 — Discover says what a download costs

**Why**: the tab understated its own primary action by two and a half times. `shard_filenames()`
in `hf_discovery.py` takes the file you picked and collects every sibling shard, so choosing one
part downloads the whole set — while the UI listed each part separately with its individual size.
Measured on a live repo: four quantizations, each two shards, rendered as eight rows reading 36.5,
26.8, 51.1, 26.8, 43.8, 26.8, 35.0 and 26.8 GB. Clicking the 26.8 GB row fetched **68.0 GB**. Shards
now collapse into one row per set, named with a `-*` wildcard and labelled with the part count, and
the size shown is the sum. A set with any unsized member reports no size at all rather than a
partial sum presented as a whole.

**And nothing said whether it would fit.** The catalog reports what weights occupy, never what is
left on the volume — a question only the node can answer, so `/v1/models/config` now answers it.
`_disk_usage` walks up to the nearest existing ancestor (a downloads directory not yet created still
sits on a real volume) and returns `(None, None)` rather than raising, so a node that cannot stat
its own volume still serves its config and the UI says "unknown" instead of guessing.

**The rest was space spent backwards.** Three equal columns gave a third of the width, permanently,
to a panel reading "No downloads yet", while repo ids wrapped to five lines in the column beside it
and every filename was truncated — hiding the one segment that distinguishes two quantizations of
the same repo. Two columns now, 2:3, `Downloads` appears only when there is something in it, and
nothing that identifies a thing is cut off. A result already downloaded on the node says so.

**Scope**: in — shard grouping mirroring `_SHARD_RE`, the summed size, `disk_free_bytes` /
`disk_total_bytes` on the node's config, the free-space line, the fit warning, the `in library`
mark, full names, and the layout. Out — blocking a download that does not fit: the figures are a
snapshot and the node is the authority, so the UI warns and lets the node refuse.

*Note: the "larger than free space" warning is untested against real data — the volume has 2.4 TB
free and no file in the catalog approaches it.*


## PRM-211 — Discover looks like it works

**Why**: PRM-210 corrected what the tab *said* — the shard arithmetic, the truncated names, the
missing free-space figure — and changed almost nothing about how it *looked*. Reported back as
"ugly and not intuitive", which was fair: that item did information design and called it done.

Five concrete faults, each visible in a screenshot:

- **The grid stretched both columns to the taller one.** Three file rows sat at the top of a panel
  seven hundred pixels deep. `items-start` is the whole fix.
- **The empty state spent the screen on nothing.** Before a search there is exactly one thing to do,
  and it was a small box in a corner with a blank panel beside it. It is now a centred affordance
  sized like the only action it is, with the example repo id spelled out.
- **The download control was a bare icon at the far edge of a very wide panel** — roughly a thousand
  pixels from the filename it acted on. It is now a labelled, bordered button inside the row.
- **Result rows were borderless text in a column.** Nothing said a row was a thing you could pick.
  They are bordered cards now, with the counts as pills rather than grey runs of digits.
- **The disabled search button was a washed-out primary**, which reads as a rendering fault rather
  than "not yet". Disabled is now neutral and inert.

`Downloads` also moved from the foot of the page to directly under the disk bar: it is progress on
the thing you just clicked, and it used to render below everything else, off-screen at the moment it
mattered. That gives finished entries a good seat, so in-flight ones sort first and the block is
capped at `max-h-60` — the manager keeps completed entries for the session, and a morning of
downloading would otherwise push the search off the page.

**Scope**: in — the two states of the tab, the row and card treatments, the button, and the
Downloads placement. Out — clearing completed downloads, which belongs to the manager rather than
the page.


## PRM-212 — A download row that shows the download

**Why**: the bar was conditional on `isActive || isPaused`, so the two states anyone actually asks
about — *did it finish?* and *where did it stop?* — were the two with no bar at all. A cancelled
download at 43% and a completed one rendered the same shape, and telling them apart meant reading
two byte counts and dividing. The bar is now always drawn, and its colour carries the outcome:
green full, amber paused, red failed, grey cancelled, primary in flight. A full bar means all of it
is here; a half bar means it is not.

**Four fields the response carried and the row threw away** — the same pattern as PRM-201, PRM-204
and PRM-206. `hf_filename`, so two quantizations of one repo downloading at once are not two
identical rows. `speed_bps` and `eta_seconds`, shown **only while `downloading`**, because both are
stale the instant a transfer pauses and a stale rate presented as current is this repo's recurring
defect. And the `[n/total]` shard suffix the manager appends to `model_id`, which was rendered
inside the name where it reads as part of it rather than as "file 2 of 3".

**Scope**: in — the bar, its colours, the recovered fields, and `… remaining` while incomplete.
Out — clearing finished entries, which belongs to the manager.


## PRM-213 — Downloads shows the one that matters

**Why**: PRM-211 moved the panel to the top of the tab, where progress belongs, and PRM-212 made
each row carry real detail. Together those turned a session's accumulated entries into a wall: the
manager keeps finished downloads for the life of the process, so the first thing on the page became
a history of the morning rather than a status.

**One row — but "the latest" is the wrong rule on its own.** With something in flight, the row you
want is the one moving, not whichever finished most recently. `downloadRank` orders in-flight above
paused above failed above finished, and ties fall back to the order the node returned, which is the
order they were started.

**The rest are one click away rather than gone.** Collapsing three concurrent downloads to a single
visible row would be the page quietly dropping work it is actually doing — the same class of defect
this repo keeps finding, so `Show all N` expands them.

**Scope**: in — the ranking, the single row, the toggle. Out — clearing finished entries, which is
the manager's to do and would remove the need for most of this.


## PRM-214 — Playground opens ready to use

**Why**: the model was `useState("")` falling through to `readyInstances[0]` — whichever instance the
API happened to list first. On this platform that is `von-decide`, so the Playground opened as a
zero-shot decision form, with its labels field and its Decide button, while the first text model sat
fifth in the list. Nothing about that was a choice. It now prefers text, then vision, then anything
at all, and remembers what you picked.

**The rail was mostly empty, and the page said nothing about why.** Picking a model does not just
change where the request goes: it rebuilds the composer. Six of the seven modalities put nothing in
the right rail but the model select — measured at 590px of empty column under it, 77% of its height —
while text and vision filled it with system prompt, streaming, sampling, stop sequences and tools.
The picker moved to the header, beside `Clear`, with a line naming the modality, the node and what
the modality actually does; the rail renders only when it has parameters to hold.

**And the empty state is where the mode gets explained.** 538px of conversation panel carried one
grey line reading "No messages yet". It is empty exactly when someone does not yet know what this
model does, so that is what it says now, with two runnable examples — the fastest way to learn what
a mode is, is to run it.

**Two smaller things**: the temperature and top-p sliders were unstyled natives (`accent-color: auto`,
class `w-full`), the only blue elements in an orange app; and `Clear` used `disabled:opacity-40` over
a bordered button, the washed-out treatment PRM-211 already replaced on Discover.

**Scope**: in — the default and its persistence, the picker's move, the conditional rail, the
per-modality empty state, the sliders and the button. Out — the composer layouts themselves, and
clearing the draft when the modality changes (the same text is often worth sending to both).


## PRM-215 — Playground bubbles fit their text

**Why**: reported as the page looking deformed, and it was. Chat bubbles carried
`ml-auto max-w-[80%]` on a block `div`. A max-width is a ceiling, not a width, and a block element
fills what it is given — so every message rendered as a slab at 80% of the panel regardless of
content. Measured: **459px wide for 19 characters.** Four bubbles in the non-chat modalities already
had `w-fit` and looked right; the other eight did not, which is why the same page looked correct in
some modes and broken in others.

**Configuration appeared to live in two arbitrary places**, and the split is actually principled —
it just said so nowhere. A zero-shot model's labels are chosen *per call*; that is the whole point
of zero-shot, and they belong with the text you are sending, not in a rail beside `Temperature`.
Settings that persist across requests stay in the rail, now headed `Model settings`; the per-request
inputs are wrapped with the composer in one bordered block that reads as "the request you are about
to send".

**Scope**: in — `w-fit` on every bubble, the composer block, the rail heading. Out — translating the
interface, which is a separate decision about the whole admin UI rather than this page.


## PRM-216 — Admin in Spanish and English, the reader's choice

**Why**: the admin UI is written in English end to end while the platform is operated in Spanish, so
every explanatory string — the ones that exist to be *read* rather than scanned — lands in the wrong
language for the person reading it. The two have already begun mixing by accident: `Playground.tsx`
carries Spanish placeholders (`facturación, soporte técnico, ventas, cancelación`) among English
labels. Translating individual strings where they irritate makes that mixing worse rather than
better, which is why this is an infrastructure item and not a patch.

**Shape**: a string catalogue with `es` and `en`, and a reader-set switch that copies the pattern
`ThemeContext` already establishes — `light`/`dark`/`system` becomes `es`/`en`/`system`, defaulting
to `system` and reading `navigator.language`, persisted under a `prometheus-*` key and surfaced in
the sidebar beside the theme control.

**Size, measured**: 11 routes, 37 components, roughly 79 visible text literals plus about 90
`placeholder`/`title`/`aria-label` strings — call it 170 as a floor, since that count misses JSX text
spanning several lines and strings built by template literal.

**One thing stays English, deliberately**: `problem+json` bodies from the gateway and the
manager-api. Those are an API contract, not interface copy — the SDK guide documents them and
neighbouring teams parse `title` and `type` — so translating them would be changing a wire format to
improve a dashboard.

**Scope**: in — the catalogue, the two locales, the switch and its persistence, and every string the
admin renders. Out — the backend's error bodies, model output, the roadmap and docs, and machine
translation of either locale (both are written, not generated).


## PRM-217 — Every model has its settings in one place

**Why**: two things, and the second decided the first. The split between a right-hand rail and
controls above the composer was reported as uncomfortable twice; I argued it was principled
(per-request inputs versus persistent settings) and was asked again, so settings now live in one
place whatever the model.

**Then the research question: do only text and vision models have parameters?** No, and the answer
is why PRM-214 was wrong to hide the rail — it read the UI's omissions as the API's limits.

| Modality | Parameters the gateway accepts | Reachable before |
|---|---|---|
| text / vision | temperature, top_p, max_tokens, stop, tools, tool_choice, stream, system | all |
| rerank | `top_n`, `raw_scores` (PRM-183) | none |
| image | `n`, `size` | none |
| zero-shot | `candidate_labels`, `multi_label` | labels only |
| classification | none — labels are baked into the checkpoint | n/a |
| embedding | **none** — the schema is model+input, and `dimensions`/`encoding_format` are documented as unsupported | n/a |

`multi_label` was measured rather than assumed: on `von-decide` with four labels, off gives a
softmax summing to 1.0 (`ventas` leads at 0.319); on gives independent sigmoids (0.499/0.450/0.423/
0.421) and `facturación` leads. Different scores, different winner.

**And one of the four is ignored by the engine it is offered on.** `qwen3-reranker-0-6-q4` returns
byte-identical scores with and without `raw_scores`, and the gateway says so in
`X-Prometheus-Ignored-Parameters` — a header PRM-183 added for exactly this and that no client had
ever read. Shipping the control without reading it would have been a switch that does nothing,
indistinguishable from one that works. The rerank result now names what the engine threw away.

**Scope**: in — the rail made unconditional, the per-modality settings moved into it, the four
parameters wired through, embedding's "no parameters" note, and reading the ignored-parameters
header for rerank. Out — reading that header on the other endpoints, which deserves to be done
uniformly rather than one modality at a time.


## PRM-218 — Examples that actually run

**Why**: PRM-214 added a runnable example to each modality's empty state, on the reasoning that the
fastest way to learn what a mode does is to run it. It filled the text box and nothing else, so for
every modality that needs a companion input — zero-shot's labels, rerank's documents,
typed-decision's questions — pressing Send answered *"Give at least two options, comma-separated"*.
An example that cannot be run teaches only that the page is broken. Each example now carries that
companion, and clicking one sends it: a demonstration you have to finish yourself is not one.

**And running them found a crash that was always there.** `nli-tei` is registered with modality
`zero_shot`, but its endpoint is a raw NLI classifier: it answers
`[{label: "entailment", score}, …]`, not `von-decide`'s `{sequence, labels, scores}`. `useZeroShot`
took `raw[0]`, `labels` came back undefined, the renderer mapped over it and **the entire page went
white**. Reachable before this item by typing labels by hand; nobody had. The client now checks the
shape and says what is wrong, because a blank screen is the worst possible way to report a
mis-registered model.

*Worth deciding separately*: whether `nli-tei`'s registry entry is wrong, or whether a bare NLI
checkpoint deserves its own modality. The UI stops crashing either way; the catalogue is still
claiming something the endpoint does not do.

**Scope**: in — companion inputs on examples, auto-send, the shape guard, and moving the model
caption beside `Clear` (it was a full-width row costing the conversation a line of height). Out —
changing `nli-tei`'s registration, which is a catalogue decision rather than a UI one.


## PRM-219 — An `nli` modality with the pair it needs

**Why**: `nli-tei` is `cross-encoder/nli-distilroberta-base` registered as `zero_shot`, and it is not
one. Zero-shot classification is the *pipeline* built on a model like this: write one hypothesis per
candidate label, run the cross-encoder on each, normalise the entailment scores across the labels.
The bare model takes a **(premise, hypothesis) pair** and answers
`entailment / neutral / contradiction`.

**The project had already found this twice and never named it.** PRM-184 fixed the readiness probe,
writing that TEI "ignores `parameters` entirely rather than refusing it — so this probe passed
against TEI while the `candidate_labels` were thrown away". PRM-144's payload contract spelled out
the consequence: *"a caller that dispatched on `modality` alone would read one as the other."* Both
told the two apart by sniffing `backend == "tei"`. Then the admin UI dispatched on modality alone,
read one as the other, and blanked the page (PRM-218). A distinction the code keeps rediscovering
deserves a name.

**Measured, as a two-field composer**: `"cancelled the subscription yesterday"` →
`"no longer a subscriber"` gives entailment **0.979**; `"renewed for two more years"` →
`"cancelled the contract"` gives contradiction **0.993**. Both in 87ms.

**And the panel says what the checkpoint cannot do.** It is English-trained: on
`"canceló ayer"` → `"ya no es suscriptor"` it answers *contradiction at 0.94* — not hedging, the
exact inverse. A model that is confidently wrong in the language the platform is operated in has to
say so where it is used, not in a commit message.

**Scope**: in — the modality in the manager registry, a pair-shaped readiness probe, the gateway's
pass-through set and payload contract, pricing, the picker group, the two-field composer and its
examples, and re-registering `nli-tei`. Out — a zero-shot *pipeline* over NLI models, which would
let a checkpoint like this back `von-decide`'s modality properly; that is its own item.


## PRM-220 — Billing charts say what happened

**Why**: three things, and the first two are the same defect this repo keeps meeting — a picture
asserting more than was measured.

**Daily spend was a `monotone` spline.** A day's spend is one number; the curve interpolated every
instant between days and drew a smooth rise and fall. On this client's three days — 0.0861, 3.3055,
0.1743 — what happened was a spike on the 6th, and a monotone curve through so few points can
overshoot past the data's own range. One bar per day says that and nothing more.

**`Cost by model` rendered three bars and two labels.** Recharts drops a category tick when labels
collide, so `qwen36-35b-a3b-q4` at USD 0.5043 appeared as an unnamed quantity on a page about money.
Horizontal bars give each slug a row of its own, sorted dearest first — which a category axis cannot
do and which is the order the question is actually asked in.

**The subtotal never said what it bought.** `total_tokens` and `request_count` are both in the
summary response; only the second was rendered, and only in the history table. The card now reads
`8.1M tokens · 1,183 requests`, with the exact figures on its tooltip.

*Worth recording*: two findings from the review turned out to be wrong, and the source said so.
`unpriced_requests` **is** rendered, in the history row, with a good explanation of what it means;
`request_count` is there too. Reading the page is not reading the code.

**Revised after use.** Bars made three days legible and a thirty-day month unreadable, and the
question a period chart is read for is the trend. The area is back — but `linear`, with a dot per
day. The original objection was narrower than "no curve": `monotone` interpolates *curvature*,
inventing acceleration nobody measured, and through few points it can draw a peak higher than the
dearest day. Straight segments between marked observations keep the trend and never leave the range
of the data.

`Cost by model` also stopped being a chart. Three 18px bars adrift in a 256px box read as
unfinished, and widening them would have treated the symptom: that card asks the question the
overview's rankings answer better, so it reuses `RankTable` — one visual grammar for "ranked by
cost", denser, and carrying the tokens and requests behind each figure, which the chart had nowhere
to put. Its value column shrink-wraps rather than taking a fixed width, because `significantDecimals`
goes to twenty places: `USD 0.00000064` is fourteen characters, `w-20` clipped it and `w-28` still
missed by three pixels. Rounding would be the silent zero PRM-119 prevents, so the column gives way.

**Scope**: in — the two charts, the subtotal's sub-line, the repeated period in a card label, and
the empty spend-cap card. Out — the page's structure, which is PRM-221.


## PRM-221 — Billing opens on the platform

**Why**: all nine billing endpoints were keyed by a client or by global config, so the page built on
them opened with a client selector — and a selector first means the reader has to already know which
client they care about before the page tells them anything. The first question a billing page is
asked is *what did the platform bill this month*, and that was unanswerable: during this review I
answered it by looping seven requests by hand, which is exactly the work the page was imposing.

**What the aggregate makes visible**, and no per-client view ever could: `qwen3vl-8b-q4` is **61%**
of October's bill at USD 10.31, and Sentinel is **53%** of it. Concentration like that is the whole
reason to look at billing, and it was spread across seven screens.

**Subtotal leads, tax is its own line, and that is deliberate.** `tax_rate_percent` is configured
per client, so a single blended "total billed" would add figures computed at different rates and
present the sum as one number. The total is still returned — it is what is owed — but the two are
never collapsed into one headline.

**The rule about zero holds here too.** A month in which nothing could be priced returns
`subtotal_usd: null`, not `0.0`, and the unpriced request count is a card of its own. A confident
0.00 over usage that happened is the silent $0 every other layer of this system refuses to produce.

**Scope**: in — `build_platform_overview`, the endpoint, `list_client_billing_settings` (all rows,
since tax is per client), the overview cards, two ranking tables, and moving the client selector
down beside the detail section it governs. Out — ranking by anything other than cost; tokens and
requests ride along on each row instead.


## PRM-222 — The trend curve is a choice

**Why**: PRM-220 replaced a `monotone` spline with bars, then with a `linear` area, on the argument
that curvature between daily points is interpolation nobody measured — and through few points it can
draw a peak above the dearest day in the data. That argument is correct and it is not the whole
question. Over a thirty-day period the smooth curve reads the trend better, and the trend is what a
period chart is opened for.

So neither answer is right for every reading, which makes it a control rather than a decision I keep
making on someone else's behalf. `Smooth` / `Exact`, defaulting to smooth, remembered per reader
under a `prometheus-*` key like the sidebar's width and the Playground's model.

**The labels do the arguing.** Smooth says it is "curved between days, easier to read a trend, and
not proportional — the curve passes through values that were never measured". Exact says it "never
leaves the range of the data". Putting the distinction where it is used beats putting it in a commit
message twice.

**Scope**: in — the toggle, its persistence, and the two tooltips. Out — applying the same choice to
the Usage page's trend chart, which has the same property and should get the same control when
someone asks for it rather than on speculation.


## PRM-223 — Limits says which backends it has not heard from

**Why**: the circuit table rendered `metrics.backends`, which has a row per backend that has served
a request since the gateway process started. Measured on the running platform: **12 instances ready,
3 rows**. The other nine were healthy and had simply seen no traffic in the hour since the last
restart. A model missing from a list reads as a model nobody started — the mistake PRM-137 named
when `rerank` had been invisible in the Playground since PRM-106.

**It matters more here than on a dropdown.** An absent row is not a closed circuit. "Closed" means
the backend is taking traffic and the breaker is satisfied; absent means the breaker has never been
exercised there at all. Conflating them is the difference between a backend known to work and one
nobody has tested. Every running model is listed now, with `no traffic yet` in place of a badge, and
the process-memory caveat that Overview already carries is here too — it is the reason the nine are
blank.

**`On store unavailable` was the only card without a line of its own**, and the one that most needed
one: it decides what happens to every request when Redis cannot be reached. "Deny (strict)" is a
word, not an answer. It now says which, and warns when the setting is fail-open, because with the
store unreachable the limiter cannot count and traffic passes unmetered.

**Scope**: in — the table's row source, the `no traffic yet` state, the counter caveat, and the
fourth card. Out — the limit dimensions themselves (RPD, TPD, IPM, split input/output TPM, tiers),
which the review alongside this one covers and which are their own items.


## PRM-224 — TPM counts every token, and tells input from output

**Why**: the token limit measured 93% of the traffic. `increment_tpm` was called from two of the
seven `_record_usage` call sites, both in the chat path. Measured on live data: **5,305 requests and
1,462,186 tokens** from embeddings, rerank, predict and images spent no token budget at all. A
client could exhaust embeddings without ever approaching a limit named after tokens.

**The fix is structural, not six more copies.** The increment moved inside `_record_usage`, which
every path already calls and which already has the counts, and `rl_redis` is a **required**
parameter — mypy named all six missing call sites the moment it became one, and a path added later
cannot quietly skip it.

**Input and output are their own counters now.** Both vendors this platform is shaped after count
them apart, and the reason is physical: output tokens are generated one at a time and dominate
latency, while input is processed in parallel at prefill. Measured here, Sentinel's traffic is 88%
prompt and Code2Presentation's is 75/25 — identical TPM, very different load. The combined key is
untouched so existing limits keep their meaning; the two ceilings are optional and default to
unenforced, because silently splitting a number people have tuned against would change what they
bought.

**They are a meter, not a gate**, and the code now says so. How many tokens a request will produce
is not knowable before it runs, so the budget is spent and the *next* request is refused. "Token
limit" reads as a gate; this has always been a meter, and nothing said it.

**And `/v1/images/generations` was not on the endpoint map**, so it fell to `default` and shared a
budget with every unmapped route — the exact failure PRM-129 documents eleven lines below the map it
is missing from. A test pinned that behaviour, which makes it recorded rather than unnoticed. It is
also the one endpoint priced per image rather than per token, so a token-shaped shared bucket hid it
twice over.

**Scope**: in — coverage, the split, the optional ceilings, the images slug, and three tests. Out —
RPD/TPD buckets, IPM, and per-client tiers, which are the next three items.


## PRM-225 — A day is a limit too

**Why**: a per-minute limit answers "how hard can you push right now". It does not answer "how much
of this is yours", and the two are different questions — a client can sit comfortably under every
per-minute ceiling for twenty-four hours and still consume a month of capacity. Both vendors publish
daily dimensions alongside the per-minute ones for exactly that reason.

**Off by default, and unset means unmeasured.** The combined and per-direction minute limits are
what deployments are tuned against; a daily ceiling is a different policy decision, so it is opt-in
and costs no Redis traffic until someone opts in. The price of that choice is documented on the
method: switching a daily limit on mid-day starts counting from that moment rather than from
midnight.

**Requests are check-and-increment, tokens are read.** The same split PRM-224 made explicit: a
request's existence is known before it runs, so RPD can refuse atomically and a refused request has
still spent its slot; the size of a response is not knowable before it exists, so TPD is a meter
that refuses the *next* caller. `Retry-After` points at the next UTC midnight, which is a long wait
and exactly the point — a daily budget that reset sooner would not be one.

**The module docstring said `Sliding-window` over a `time // 60` fixed window.** Corrected, because
it is a correctness claim rather than a description: a fixed window lets a caller spend a full quota
at 11:59:59 and a second one at 12:00:00. Changing the window is its own item; describing it
honestly is not.

**Scope**: in — the two dimensions, their keys and TTLs, the shared 429, counting TPD from the same
place PRM-224 moved TPM to, three tests, and the docstring. Out — IPM and per-client tiers, the two
items left from the limits review, and the fixed-to-sliding window change.


## PRM-226 — Images per minute

**Why**: the last of the five dimensions the industry publishes, and the only one that can be a gate
rather than a meter. How many tokens a completion will produce is unknowable until it exists, which
is why TPM/TPD spend the budget and refuse the *next* caller. How many images a request will produce
is `n`, in the body, before anything is generated — so a request that would not fit is refused
before the backend is asked to do the work, and a single `n: 10` cannot overshoot a ceiling of 5.

**Checked in the handler, counted in `_record_usage`.** The handler is where `n` is knowable at all,
which is the same reason RM-60 put the spend reserve there rather than in the middleware. The
counter is then incremented from `image_count` — what the backend actually returned — because a
generation that produced fewer should not spend a minute's budget for images nobody received.

**The headroom check is a read, and the race it allows is deliberate.** Two requests arriving
together can both pass and overshoot by one batch. RM-60 chose atomic reserve-and-roll-back for
*money*, where an overshoot is a real charge; here the counter follows actual output, so the error
is bounded by concurrency and clears within the minute. Reserving `n` and settling the difference on
every request would buy back a bounded, self-healing error at the cost of a second write path.

**Worth recording: the first attempt was wrong in a way only a test could show.** The gate landed
inside `if cap_usd is not None` — the branch that runs when a client has a monthly spend cap — so
the image limit silently applied to some clients and not others. The test that caught it asserts the
backend was never called, which is the actual property a gate has and a meter does not.

**Scope**: in — the counter, the headroom check, the handler gate, `rate_limit_ipm`, and three
tests. Out — per-client tiers, the last item from the limits review, and the fixed-to-sliding window
change.


## PRM-227 — Three layers, and a request passes all of them

**Why**: the established practice layers limits by purpose — a platform ceiling that protects the
hardware, a consumer entitlement that expresses what a client bought, and per-route micro-limits on
expensive operations — and evaluates them as a **conjunction**: a request passes every applicable
layer, not whichever is most specific. Only the third existed here. Measured on live data, one
client reached **112 requests in a minute** — 56 embeddings, 52 rerank, 4 chat — without a single
refusal, because six counters of 60 never see each other.

**Expressed in the key scheme that already existed.** A client-wide counter is the same key with `*`
where the endpoint goes; the platform's is `*` in both positions under a reserved identity. No
schema, no migration, one INCR per layer.

**Layer 2 is always enforced and layer 1 is opt-in**, for different reasons. A deployment whose
consumer layer is absent is the configuration the practice calls wrong, so shipping it behind a
switch would be shipping the bug with the switch beside it. Layer 1 is sized against the hardware,
and a number chosen in a config default rather than by the operator would be invented.

**Two tests forced two corrections, and both were design errors rather than test errors.**

The first ordering was platform → consumer → endpoint, on the reasoning that no client should spend
its budget on a request the platform would not serve. But while the two carry the same value, the
consumer layer always fires first and the endpoint layer becomes unreachable — a client hammering
one route was told `scope: client` when `scope: embeddings` was the useful answer. Most specific
reason wins where both apply.

The second was giving the consumer layer the per-endpoint value as its default. That re-merges every
route into one budget, which is precisely the bug PRM-129 fixed when a copilot using embeddings and
rerank hit its ceiling 5% short of nine users. The default is the **sum** of the per-endpoint
allowances instead: it bounds a client at full tilt on every route at once without undoing a
separation that was deliberate. A real, tighter number per client is PRM-228's job, chosen rather
than invented.

**Scope**: in — the two layers, the scope constants, the client-wide and platform token counters,
the shared refusal with its `scope` field, and three tests. Out — the tiers that give layer 2 a
per-client value (PRM-228), a pre-auth IP throttle, and per-model limits, which is the axis both
vendors actually use and a better one than per-endpoint.


## PRM-228 — Rate-limit tiers

**Why**: PRM-227 made the consumer layer exist and deliberately left its value as a derived default —
the sum of the per-endpoint allowances, which bounds the pathological case and little else. This is
where a real number is chosen. Tiers rather than per-client values because that is what both vendors
do and the reason scales: fourteen clients could be tuned individually, but a hundred are fourteen
forgotten decisions and eighty-six defaults nobody chose.

**Where it lives.** A `rate_limit_tiers` catalogue, and a `tier` column on
`client_billing_settings` — the row already fetched and cached on the hot inference path for RM-60's
spend cap, so the tier rides along instead of adding a second lookup per request. The table's name
is then slightly short of what it holds; that is a worse name traded for a cheaper request, and the
trade is recorded on the column.

**Null is a default, not a licence.** A tier that omits `ipm` is saying nothing about images, not
granting them without limit. Zero is refused outright, because a ceiling nobody can pass is never
what someone meant to type, and "leave this alone" (absent) stays distinct from "clear it" (null).

**Two ways to lose a limit without noticing, both closed.** A tier with clients on it cannot be
deleted — doing so would move them to the platform defaults silently. And a client cannot be parked
on a tier that does not exist, because an unknown tier resolves to the platform defaults and looks
exactly like a tier that does.

**A bug worth recording**: the first `delete_rate_limit_tier` counted the clients *and* deleted the
row in one call, so the router's "refuse while in use" check ran against a row that was already
gone. Counting and deleting are separate now, and a test asserts the tier survives the refusal.

**Scope**: in — the table and migration, the cached resolution, layer 2 reading the tier, the tier
CRUD endpoints, assignment validation, cache invalidation on every write, the Users modal field, and
three tests. Out — a tier editor in the UI (the endpoints are there; the catalogue is small and
changes rarely), automatic promotion by spend, and per-model limits.


## PRM-229 — Limits shows the three layers

**Why**: PRM-224 through PRM-228 added eleven dimensions and two layers, and the page kept showing
two dimensions of one layer. Worse than incomplete, it was wrong: the card reading
`Global RPM 60 — requests/min per client` is the per-**endpoint** value, so a reader learned a
number that is not what a client may consume. That is the defect this whole sweep has been chasing,
introduced by me, by changing the system and not the page describing it.

**Three cards, one per layer**, each saying what it bounds in a line — *everyone at once*, *one
consumer across every endpoint*, *one consumer on one route* — with every dimension listed under the
scope that actually applies to it.

**The effective value, not the configured one.** PRM-227 left the consumer layer always enforced
with a ceiling derived from the per-endpoint allowances, so `rate_limit_rpm_client` being null does
not mean "no ceiling": it means 360, computed and enforced. Reporting the raw setting would have had
this page say a layer refuses nothing while it refuses at 360 — the same class of lie, one layer
down. Each field now carries `set` / `derived` / `unset`, and the middle one is why the field exists.

**Eleven are `.env`-only and the page says so** rather than letting a reader discover it by not
finding them. Making them editable needs a column each on `RateLimitConfig`; PRM-182 recorded that
this is one change rather than two, and it is the next item.

**Scope**: in — `limits_by_layer` with effective values, the three cards, the corrected form hints
and page intro. Out — editing the eleven (needs the migration), and a live view of which layer is
currently refusing, which is the diagnostic item after it.


## PRM-230 — Which ceiling is refusing right now

**Why**: PRM-229 made the configuration honest and left the question the page is actually opened
with unanswered. A request passes three conjoined layers across seven dimensions; a caller sees one
429 and the reason is whichever of about twenty counters crossed first. No arrangement of settings
cards answers that — it is a measurement, and the page had none.

**Read the counters, do not re-derive them.** `live_counters()` scans the current minute and day
buckets; the layer comes from the key shape (`*platform*`, the all-endpoints `*`, or a real consumer
on a real route) rather than from where a setting is *grouped*, because those disagree —
`rate_limit_tpm_input` sits in the client group and is checked per endpoint. Each row is paired with
the ceiling the middleware would use, tier included, through the same resolver the middleware now
calls.

**What it found on the first real read.** Twenty-three counters standing, and **fifteen of them with
no ceiling at all**: PRM-224..226 shipped input/output, daily and image meters, and nobody set the
numbers, so they count faithfully and refuse nothing. The platform layer is in the same state — its
TPM counter is incremented on every request and `RATE_LIMIT_TPM_PLATFORM` is unset. Those rows say
`counted, not checked` instead of showing a plausible number, because a measured, unenforced
dimension is a finding and inventing a ceiling for it would bury the finding.

**Scope**: in — `live_counters`, `counter_layer` / `live_limit_for`, `GET /admin/api/limits/live`,
the "Right now" section, and moving `_resolve_limits` into `rate_limits.endpoint_limits` so the view
and the middleware read one fact. Out — editing the eleven (still the migration, still next), and
enforcing a tier's other five dimensions: `rpd`, `tpd`, `ipm`, `tpm_input` and `tpm_output` are
columns on `rate_limit_tiers` that nothing reads, which this view is what made visible.


## PRM-231 — The tier dimensions nothing read

**Why**: PRM-228 built the tier catalogue with seven dimensions and the middleware read two. An
operator could set `rpd`, `tpd`, `tpm_input`, `tpm_output` or `ipm` on a tier, see it validated,
saved and returned by the admin API, and the platform would ignore it in silence. A control that
does nothing is worse than one that is missing: the missing one is visible. PRM-230's live view is
what surfaced it — two of the counters those ceilings would bound were already being incremented at
the all-endpoints key with nothing reading them, and the page printed `counted, not checked`.

**Client-wide, not per endpoint.** A tier is what a client may consume, which is the reading
`tier.rpm` and `tier.tpm` already had, so all seven now live at the same key. The alternative —
overriding the platform's per-endpoint default — would make `rpd: 10,000` grant sixty thousand
across six routes: the number saying one thing and the system doing another, which is PRM-129's
shape. The platform's own `.env` values stay per endpoint and are unchanged; a tier silent on a
dimension changes nothing about it.

**Two counters had to start being written.** `tpd` and `ipm` were only ever incremented per
endpoint, so a tier reading them across all endpoints would have read zero forever. PRM-227 is the
precedent: a ceiling whose counter is never written is not a ceiling, and nothing says so.

**The guard is the point.** `test_every_tier_dimension_refuses_something` sets one dimension at a
time to 1 and asserts a request is refused, without looking at how any of them is enforced. The bug
it replaces was five columns whose every part had a passing test.

**Scope**: in — the five dimensions at the consumer layer, `tier.ipm` in the images gate, the `tpd`
and `ipm` increments at the all-endpoints key, the live view reporting a `tier` source on those
counters, the tier dropdown listing every dimension a tier sets, and the SDK guide's three `scope`
values (it still said the limit value is "per endpoint — not per client", which stopped being true
at PRM-227). Also `_reset_cache_for_testing` clearing the tier cache PRM-228 added and left out.
Out — a tier editor UI; tiers are still created through the API.


## PRM-232 — The eleven become editable

**Why**: PRM-224 through PRM-228 added eleven dimensions that were live, enforced and `.env`-only,
and PRM-229 had the page admit it in a chip. The blocker was never the UI: `main.py` applies a
saved override by reading `rate_limits.RATE_LIMIT_FIELDS` off a `RateLimitConfig` row with
`getattr`, so a name in that list without a column is an `AttributeError` at startup — and only on
deployments that have ever saved limits from the dashboard, which is the worst place for it to
appear. PRM-182 nearly shipped exactly that and wrote the constraint down instead.

**One migration, seventeen fields.** Eleven nullable columns, guarded for the pre-Alembic adoption
path. `predict`'s pair comes with them: PRM-182 left it `.env`-only for want of this migration and
said so in a test, which is now the test that it is editable.

**The cards became the editor.** PRM-229 drew three read-only layer cards beside a form that edited
six of the numbers on them. Growing the form to seventeen would have put every value on the page
twice, and the second copy is the one that goes stale. Each dimension is now an input inside the
card that explains what its layer bounds.

**A blank box means three different things, so it says which.** No ceiling at all (the platform
pair), PRM-227's computed sum (the client pair), or the global value (a per-endpoint override) —
each one is the field's placeholder. And a `derived` value is deliberately *not* prefilled: a
prefilled 360 would be saved as a fixed 360 by the next edit to any other field, and the consumer
ceiling would quietly stop following the global it is computed from. Verified live: raising the
global RPM to 100 moved the derived ceiling to 600 after a save that left it blank.

**Saving makes this page the source for all seventeen** — the rule the six already followed, now
stated on the page, with Reset restoring every `.env` value from the snapshot `create_app` takes
before any row is applied.

**Scope**: in — the migration, the eleven columns, `RATE_LIMIT_FIELDS` grown to seventeen, a PUT
driven off that list with partial edits (absent = leave alone, null = clear), the editable cards.
Out — per-model limits and sliding windows, still the two real gaps against the industry.


## PRM-233 — How close is each ceiling

**Why**: PRM-230 answered "which ceiling refused me" with a list of every counter standing. That is
the right answer once a 429 has happened and the wrong shape for the question before it — *who is
about to be refused*. With fourteen clients across six endpoints and seven dimensions the list runs
to a hundred rows, and the one at 98% looks like the ninety-nine at 2%.

**Two roll-ups.** The whole platform this minute, and one row per consumer carrying both its own
client-wide ceilings and its worst counter — different facts, and the second is the one that
produces the 429: a client at 3% of its client-wide RPM can have a single endpoint at 98%. The
per-counter list is still a click away.

**The platform's RPM counter is now unconditional.** Its TPM counter has always been written on
every request; RPM existed only once `RATE_LIMIT_RPM_PLATFORM` was set, so the dashboard could show
what the platform spent in tokens and not in requests — and the operator deciding what that ceiling
should be was the one person who could not see the number. Counting is not enforcing, and an
unset platform ceiling still refuses nothing; that now needs its own test rather than resting on
the absence of a Redis key.

**The rate limit this page never showed.** Every ceiling on it is keyed on a credential, so every
one applies *after* authentication. `/ui/login` has had a per-IP throttle since spec 017 AC-11 —
enforced, `UI_ENABLED=true` on this deployment, and absent from the one page called Limits. Shown
read-only: a login throttle an attacker could widen through the admin API is not obviously one
worth making editable through the admin API.

**And the panel now fails visibly.** Three retries with backoff left it reading "Reading the
counters…" for seven seconds against a gateway without this endpoint — which is what an operator
running an older build sees. A diagnostic that looks like it is still thinking is worse than one
that says it failed.

**Scope**: in — the two roll-ups, the unconditional platform RPM counter, `login_throttle` in the
limits payload and its panel, `retry: false` on the live query. Out — the auth-service's own
per-IP limit on token issuance (another service's configuration, and the gateway cannot read it),
and that the admin login reaches it through the gateway's address rather than the operator's, so
all operators share one bucket.


## PRM-234 — Name the layers by number

**Why**: the page opens by saying a request passes three layers, and then labels the cards
`PLATFORM`, `CLIENT`, `ENDPOINT`. Which of the three is in front of you — the broadest or the
narrowest — was the one thing those names did not carry, and it is the content of the distinction:
PRM-227's whole argument is about which layer catches what the others cannot see.

**Numbered by the server.** `LAYER_NUMBERS` is derived from `LIMIT_LAYERS`' order, which is the
order a request meets them and the order the middleware's comments have used since PRM-227 ("Layer
1: the platform", "Layer 3 alone bounds a route"). The dashboard renders the number rather than
working it out: the configuration cards and the live counters are two views of the same three
layers, and two places counting independently is how one ends up calling a ceiling Layer 2 while
the other calls it Layer 3.

**Scope**: in — `LAYER_NUMBERS`, `n` on each layer and `layer_n` on each live counter, the chips on
both views, and a test that the order is platform/client/endpoint so reordering the tuple cannot
silently renumber the page.


## PRM-235 — Activity, and the end user the gateway threw away

**Why**: Sessions was three columns — credential, what kind of path it last called, how long ago —
sourced from an in-process tracker that a restart empties. Measured on the live deployment right
after a deploy: two consumers with 63 and 10 requests *that day* did not appear at all, because
the only source that knew about them had been cleared. A page that goes blank after a restart says
"nobody is using this platform", which is the mistake PRM-223 fixed on the circuit table.

**It was also the wrong word.** In LLM tooling a *session* is the grouping of one conversation's or
one agent run's calls — Langfuse groups traces by a session id, Helicone groups requests into a
tree by id and path. What API gateways actually publish is traffic per consumer: Kong labels its
request counters by Consumer and meters each call against one, LiteLLM breaks spend and requests
down per key and per customer. Nobody ships a "who is connected" page, because tokens are
stateless — the caveat already written in `ActivityTracker`'s own docstring.

**Activity** is the second thing under the second name: per consumer, what it is spending this
minute and against which ceiling (Redis, survives a restart), what it spent today (the usage rows),
and last-seen where the tracker still has it. Top 50, because per-consumer series are
high-cardinality — LiteLLM keeps end users out of its Prometheus export by default for the same
reason.

**And the end user, which was arriving and being dropped.** Every ceiling and every invoice here
stops at the credential: one client is one row however many people are behind it. The industry's
answer is a field on the request — OpenAI's `user`, now superseded by `safety_identifier` — and
this gateway accepted both, named them in `x-prometheus-ignored-parameters`, and threw them away.
Both are read now, with LiteLLM's precedence (header, then `user`, then `safety_identifier`), kept
out of what is forwarded to any engine, and written to `usage_events`. The streaming path nearly
missed it — `_stream_response` has its own local `body` — and the suite said so, which is the
difference between this and `user` working for buffered requests and silently not for streamed ones.

**Scope**: in — the identifier and its column, `/admin/api/activity`, the new page and the sidebar
entry, `/#/sessions` redirecting rather than 404-ing, and the SDK guide. `GET /admin/api/sessions`
stays: it is published, the dashboard no longer calls it, and removing it is a separate decision.
Out — grouping calls into conversations the way Langfuse and Helicone do, which needs a session id
per call and is a different feature from identifying a person; and per-end-user budgets, which the
column now makes possible.


## PRM-236 — What did they do, not just how much

**Why**: PRM-235 built a page about consumption and left the question underneath it unanswered. A
row saying 63 requests says a credential was busy; it does not say whether that was inference,
listing its models, or an operator poking at the Playground. `connection_type` looked like the
answer and was not — it is the URL prefix of whichever request happened to be last, so a client
that listed its models once after a thousand completions reads the same as one that only listed.

The page also said `not in the window`, which named the mechanism instead of the fact. An operator
does not know that there is a window, or that it is the gateway's own memory, or that a restart
empties it.

**Three sections, one per question.** *Here now* — who called in the last 15 minutes, what kind of
call it was, and what they are spending this minute. *Consumers today* — who ran something and on
which models, from the usage rows, which outlive both the window and the process. *End users
today* — the same day from the other side, because one person can be served by several consumers
and a list nested inside each one cannot be read across them. Expanding a consumer gives the row
the question asks for: this user, this many requests, these models.

**The tracker counts actions now.** One bounded label per kind of call — chat, embeddings, rerank,
predict, images, listed its models, read backend status, read its own usage, dashboard, Playground
— and a count and last-seen for each. Closed set by construction: these are per-identity counters
in memory, and a label taken from an unbounded URL is an unbounded dictionary.

**The Playground identifies itself**, with a header, because it and an SDK are the same credential
on the same route and nothing in the request can separate them. Self-reported, and only ever used
as a label.

**And a test-isolation gap the change exposed**: the tracker is a module-level singleton that
nothing reset between tests. Harmless while it held one connection type per client; counting
actions made another test's 403 check appear as this credential's own history. Cleared by an
autouse fixture, like the caches beside it.

**Scope**: in — `classify_action` and the per-action counters, model and kind in the usage
grouping, `end_users_today`, the three-section page, the Playground header. Out — a per-request
event log (this counts kinds, it does not list calls), and anything about the `/ui/*` chat, which
authenticates by cookie and never reaches the tracker.


## PRM-237 — The number under the label

**Why**: two questions from one screenshot, and both were the page's fault.

**The pill was a running total.** `Admin dashboard ×284` sat under a heading reading *credentials
that have made a request in the last 15 minutes*, and the number was every request of that kind
since the tracker first saw the credential — it only ever grew. Counted in one-minute buckets now,
with the snapshot summing the buckets inside the window; an action whose buckets have all aged out
is dropped rather than reported at zero, which would read as "did this, nil times". Fifteen ints
per action per identity is the price of the number and its heading agreeing.

**And the operator was the busiest consumer on their own platform.** Measured from the browser:
248 requests in 15.4 minutes with nothing open but this page — `/admin/api/users` every 5 seconds
and `/admin/api/activity` every 10. That is real traffic, it spends the admin bucket, and
`MIN_ADMIN_RPM` exists because of it, so hiding it would be the page lying to protect itself.
What was missing was the page saying whose it is: the row carries a `you` badge, from the server's
own view of who is asking, and the section says the dashboard polls and roughly how much.

**Scope**: in — minute-bucketed action counts, dropping aged-out actions, `is_you`, and the
section copy. Out — reducing the polling itself, which is a different change with its own
tradeoff, and one worth measuring across every page rather than from this one.


## PRM-238 — One window per table

**Why**: the same consumer showed `Chat completions ×23` and, one line below in its own expanded
detail, `89 requests`. Both numbers were right — the first is the last fifteen minutes from the
tracker, the second is today from the usage rows — and nothing on the page said they were measuring
different spans. Two true numbers that read as a contradiction are worse than one number.

**Each table answers for its own window.** The first attempt removed the expansion from the live
rows, which fixed the contradiction by deleting half of it; the live section is exactly where an
operator asks *who is this consumer serving right now*. `query_activity_since` gives that section
its own breakdown, and both halves of the window start at the same instant —
`ActivityTracker.window_started_at`, the opening edge of the oldest minute bucket the snapshot
sums. A rolling 900 seconds would have included requests the pills exclude, and the detail would
not have added up to the summary above it. Verified live: a pill reading `Chat completions ×3` over
a detail reading 2 + 1.

They can still differ honestly — the pills count every authenticated request and only billable
inference writes a usage row — so the live detail says so rather than leaving the reader to find
out by subtracting.

**And every column names its own span**: `Doing (last 15 min)`, `Requests in this window`,
`Requests today`, with each section stating which window it covers.

**Here now stopped trusting one source.** It listed whoever the in-process tracker had seen, so a
consumer with 27 requests inside the window was missing from it after a restart emptied that
tracker — the mistake PRM-235 fixed one section down, still live one section up. Either source
counts now, and a row with usage but no tracker entry says `since a restart` instead of claiming it
has not called.

**And a row that reported the tracker's silence as the consumer's.** With no tracker entry, every
visible column read `—` or "not tracked since the gateway started" while the drawer under it showed
seven requests: an empty row with a full drawer. What the usage rows know is filled in now — the
kinds of call, counted from `request_kind`, and last-seen from the newest row — marked `*` because
that source only knows what bills and cannot see a model listing. Reconstructing last-seen from
*any* row today put a consumer last seen seventeen minutes ago into a table promising fifteen, so
it is filled only from inside the window; a test holds that line.

**And the two numbers that were left to be subtracted.** A pill reading `Chat completions ×7` sat
above a detail reading `6`: requests that *arrived* against requests that *produced a usage row*.
Measured on the live deployment the gap was four and stable, and the gateway log agreed — ten
`auth.ok`, five `inference.complete`. That gap is a finding, not noise: a stream still open, a
caller that disconnected, a request refused before it reached a backend. The drawer states it —
"7 arrived, 6 produced a usage row, 1 did not" — because a reader who has to do the subtraction
concludes the page is broken.

It is withheld where it cannot be trusted. The first attempt printed `2 arrived, 4 billed` right
after a restart, the page reporting fewer requests than results: the tracker had been up for
seconds while the usage rows covered the full fifteen minutes. Clamping the query to the uptime
would have made them agree by discarding the detail that survives a restart, which is what PRM-235
built — so the detail keeps the whole window and the arithmetic waits until the process has been up
for all of it, with the section saying why.

**Scope**: in — `query_activity_since`, `window_started_at` as the one boundary, per-window
breakdowns on each consumer, window-named column headers, the Here-now membership rule, the
usage-derived fallback for an untracked row, and `uptime_s` so a short-lived process can say why
its own half of the window is thin.


## PRM-239 — A refresh button, and the width

**Why**: the page polls every ten seconds and had no way to ask it for an answer now. That gap
matters right after you change something — issue a key, run a call, move a client to a tier — when
the wait *is* the question "did that land". Ten seconds of it is long enough that the reflex is to
reload the whole page, which costs far more than a button.

**A timestamp, not "8s ago".** A relative label has to tick to stay true, which means a timer and
a re-render of the table every few seconds to keep one word honest — and `Date.now()` during
render is impure besides, which the lint says out loud. The clock time of the last answer is right
the moment it is painted and stays right.

**And the copy runs the full width.** Every explanatory paragraph was capped at `max-w-4xl`, which
on a wide window wrapped three-line sentences beside half a screen of nothing.

**Scope**: in — the refresh control, the last-updated timestamp, removing the width cap on this
page's copy.


## PRM-240 — Stop polling for things that do not change

**Why**: `useUsers` refetched every five seconds because it was written for the Users page, where
that is right — you create a client there and want to see it. Six other pages then imported the
hook to turn a `client_id` into a name and inherited the timer. Measured from the browser on the
Activity page: 165 calls to `/admin/api/users` in 15.4 minutes, painting three names that had not
changed. That is roughly twelve requests a minute per open tab, spent against the admin bucket
that `MIN_ADMIN_RPM` exists to protect, and it is why PRM-237 found the operator at the top of
their own Activity page.

**Opt-in, not off.** `useUsers({ live: true })` on the Users page, plain everywhere else, with a
five-minute `staleTime` for the name readers — and one query key either way, so the list is still
fetched once and shared rather than copied per caller. Mutations invalidate that key, so a name
changed on the Users page appears on the others immediately; the poll was never what kept them
correct.

`useModelCatalog` had the same shape for the same reason and got the same treatment.

**Guarded from Python**, as `test_engine_list.py` is: the invariant spans two languages and only
one of them has a test runner here. Three checks — the interval is behind the flag, the set of
pages asking for it is the list in the test, and the flag never reaches the cache key, which is how
this would silently become two copies of the same list instead of one.

**Scope**: in — the two hooks, their live callers, the guard. Out — the other nine polls, which
are on the pages that own the data they poll and watch things that change on their own.


## PRM-241 — A day is a UTC day

**Why**: `db.record_usage` stamps `usage_events.day` from UTC, because that is what billing is
reconciled against. PRM-235's Activity endpoint asked for "today" with `date.today()` — the
machine's local date. The two agree for nineteen hours a day and disagree for five, so the page
emptied its two "today" sections every evening at seven, and the twenty tests that covered them
went red twenty minutes after going green with no code change between.

Caught by `.githooks/pre-push` on the way to the first push of this work, which is the one job that
hook has and the reason it runs on everything rather than on what seems risky.

**The trap was already known.** `test_default_prices.py` and `test_model_groups.py` each carry a
hand-written comment saying `record_usage` stamps the day in UTC and `date.today()` is wrong. A
comment in two tests did not stop a third caller walking into it, so the rule is a test now: no
source file asks the local clock what day it is, and the fact it rests on — that the usage row
stamps UTC — is asserted beside it, because if that ever changes the rule inverts.

**And the page says whose midnight it is.** "Since midnight" reads as the reader's; it is UTC, five
hours from theirs here.

**Scope**: in — the UTC date at the call site, the guard, the page copy, the two tests that asked
the same question the same wrong way.


## PRM-242 — The idempotency fingerprint covers what the client sent

**Why**: reported as `VRT-PRM-004` by Veritium, CC Axonium and synaptum, the morning PRM-235 was
deployed — and caused by it. `_begin_idempotent` was handed `body.model_dump()`, the gateway's
model *with its defaults*, so the fingerprint covered fields the client had never sent. PRM-235
added `user` and `safety_identifier` to `ChatCompletionRequest`; every chat request's fingerprint
moved, and every key stored before 07:18 answered `409 idempotency-key-reuse` for the rest of its
24-hour window. The gateway telling a caller it had misused its key, on the retry-after-a-crash
path that is the only reason the key exists.

Their framing is the one that matters and is wider than the incident: with the fingerprint taken
over the gateway's model, **any additive change to any request schema invalidates every in-flight
key on that route**, and neither side can see it coming.

**The fix is theirs**: `model_dump(exclude_unset=True)`. The dump moved *into* `_begin_idempotent`,
which now takes the model rather than a volume of it — four call sites each passed their own
`model_dump()`, and fixing four lines leaves the fifth route waiting for someone to copy the wrong
one.

**And a transitional acceptance, because the fix moves the fingerprint again.** Keys stored between
the PRM-235 deploy and this one carry the full-dump fingerprint, so the fix on its own would deal
the reported error one more time, to the team that reported it. `begin()` also accepts the legacy
fingerprint; the old payload is the new one plus defaults, so a match means the same request and
the acceptance never widens what counts as equal — a test holds that half. Removable after
2026-10-10, one window past the deploy, and the comment says so.

**Axonium asked whether the 409 could distinguish the two causes.** It cannot: the record stores a
hash, not the body, so on a mismatch the gateway cannot tell a changed body from a moved
fingerprint. Distinguishing would need a fingerprint-schema version stored alongside, and even then
the only honest message is "this was computed under different rules" — it still could not replay,
having no way to verify the body matches. Removing the cause is the better trade, which is their
own conclusion.

**Scope**: in — the fingerprint, the helper signature, the transitional acceptance, the four call
sites, and the test whose comment had documented the defect as the intent. Out — a fingerprint
schema version, which the above argues against.


## PRM-243 — The guide says what the platform does

**Why**: three gaps in `docs/sdk-integration-guide.md`, each raised by a consumer who had already
paid for it.

**The `409` remedy was the expensive one.** §5.2 told callers to *«generate a new key, or resend
the original request unchanged»*. After PRM-235 the second half produced the error, and Axonium
had copied the first half into five SDKs, five error messages and a public site — where it buys a
second billable generation for work the first request may have finished. PRM-242 makes "resend
unchanged" true again; the entry now says so and names the cost of the other branch. §6 stops
saying "path and payload" and says "the path and the payload you sent", with what changed and why.

**§4 claimed the backend keeps generating when you walk away.** True before PRM-196, false for
chat completions since, and still exactly true for `/v1/images/generations` — measured: sd-server
does not abort on connection close. Five SDKs carry a rule resting on that premise, so it is now a
table per route, with what counts as abandoned (the connection closing, which is what aborting a
`fetch` does) and the part nobody had written down: **what was generated before the cut is
billed**, by RM-87's deliberate choice.

**And how to enumerate your own grant.** `GET /v1/models` is scope-filtered since PRM-167, so it
cannot reveal a `model:<id>` grant whose id you have not guessed. A token requested with no
`scope` comes back with the full grant in its own `scope` field. aeon found that by trying it; it
should not have needed finding.

**Scope**: in — the three sections, the revision header, and the mirrored copy the push hook
checks.

## PRM-244 — Admission headroom per model

**Why**: `admission_headroom` is one float for the whole deployment, and the comment above it
already says why that cannot be right — *"the right headroom depends on how long the model's
requests take: a 200ms embedding tolerates a deep queue, a 40-second completion does not"*. With a
single number an operator chooses between protecting the completion and throttling the embedding.
Asked for by Veritium (`VRT-PRM-001` §4).

**Scope**: in — a per-model override resolved like the per-endpoint rate limits are, with the
global as the default. Out — turning it on by default, which PRM-157's note already argues
against: it would start refusing traffic on an existing deployment at the first restart, on a
number nobody chose.


## PRM-245 — How long it took, on the usage row

**Why**: the dashboard reported `p95 17,351 ms · p99 17,351 ms`. Two percentiles that are always
equal is a distribution with one point in it — they came from five requests since the last
restart, because latency lived only in `metrics_store`'s process memory. The numbers themselves
already existed: the router has computed `duration_s` and `ttft_s` since PRM-131 and hands them to
the GenAI metrics. The one place that persists anything dropped them.

**Two nullable columns**, and null means *not timed* rather than zero. A request nobody measured is
not a fast request, and counting it as zero is how a p95 improves the more instrumentation is
missing.

**Percentiles in Python, nearest-rank.** SQLite has no `percentile_cont`, and the window-function
alternative is accepted by only one of the two engines this runs on. Nearest-rank returns a
duration some request actually had; interpolation invents one that none did, which is a poor thing
to print beside a model's name.

`ttft_ms` is the *visible* first token, the same one PRM-131 chose and for its measured reason:
63.7% of spans carry a first token of any kind, 4.4% the first visible one.

**Scope**: in — the columns, the migration, the router passing what it already held,
`query_latency_by_model`. Out — backfilling, which has nothing to backfill from.


## PRM-246 — The dashboard answers what to do

**Why**: it read `metrics_store`, which is process memory. After a restart the platform's headline
was `0 active · 5 total` requests and a p95 equal to its p99 — on a deployment with fourteen days
of history and 4,535 requests to one model. Beside that sat counts of things that exist (nodes,
instances, users), which answer "is the fleet where I left it" and not one question anybody acts
on. A dashboard whose numbers reset when you deploy cannot be used to decide anything.

**Rebuilt in the order the practice recommends** — attention, then signals with something to
compare against, then the trend, then who and on what — and everything that can come from the
usage rows does, so a deploy no longer changes it. One endpoint rather than the six independent
polls the page used to make, because a single screen assembled from six clocks is a single screen
that is never consistent with itself.

**Attention is computed, not left to be spotted**: a consumer within 20% of a ceiling, a client
near its monthly cap, an open circuit, and requests that billed nothing — which is how an unpriced
model goes a month unnoticed. Empty says so in words, because an empty list and a healthy platform
look identical.

**Only failures are coloured.** The first version painted a 77.6% fall in cost red, alarming an
operator about a bill going down. Traffic, tokens and cost have no good direction; failures do.

**Two bugs the live data found, both of the same family — a claim the code did not check.** The
model table promised the ones that matter and sliced the first eight of whatever order the
group-by returned, which was alphabetical: the busiest model on the deployment, at 4,535 requests,
was not in it. And every p95 came out empty because the cost rows are keyed by `model_id` while
the latency query grouped by `model_slug` — the two PRM-113 separated on purpose. Both have tests
named after the measurement.

**Scope**: in — `/admin/api/overview`, the per-day error aggregates on `query_daily_cost_range`,
the page. Out — a per-model drill-down from here, which is what Usage and Billing already are.


## PRM-247 — The dashboard, more visual and with fewer lists

**Why**: PRM-246 made the figures true and left them in tables. A table of eight models with four
numeric columns is a thing you read; the question underneath it — *which model eats the traffic and
which eats the money* — needs the two numbers next to each other, and nobody divides them in their
head.

**Three series at once.** Requests, tokens and cost on one chart, each scaled to its own peak,
because 28 against 64,780 against 0.035 on a shared axis is two flat lines on the floor. The axis
says `%` and the tooltip carries the real values; clicking a metric isolates it and the axis
becomes that series' own units, since with one line there is nothing to normalise for. Windows of
7, 14 and 30 days.

**The tables became bars**, and the first thing that showed was the contrast they had been hiding:
`gpt-oss-20b-mxfp4` takes 4,544 requests for USD 5.99 while `qwen3vl-8b-q4` takes 3,385 for USD
10.31. Two bars per model, traffic above cost, and the gap between them is the finding.

**Three columns paired by height.** The first arrangement put an eight-row panel beside a two-row
one, twice, so half the lower page was blank. The two tall panels take the outer columns and the
two short ones stack between them.

**A header that says who is reading**, from the token rather than the browser, with the fleet
demoted to one line.

**A search that reaches what you cannot point at.** Clients and instances are names an operator
already knows; rate-limit tiers live inside Limits and currency rates inside Billing, and those are
what a search is for. The field widens leftwards on focus — it sits at the right edge — and a
result carries `?focus=<key>` so the destination scrolls to the thing and rings it for three
seconds, because landing on a page of seventeen inputs is not the same as finding one of them.

**Two bugs the live data found.** The audit feed coloured every row as a failure: it compared
`outcome` against `"success"`, a value the recorder never writes — it writes `"ok"`. And the feed
was 64 logins deep, burying the ten configuration changes it exists to show; signing in is not a
change to the platform. A Python guard now checks that every searchable feature points at a route
that exists and a `data-focus` some page actually marks — it caught one missing anchor on its first
run.

**Scope**: in — the chart, the windows, the bar panels, the three-column layout, the header,
search over features, the focus-flash mechanism and its anchors on Limits, Billing and Activity.
Out — quick-action tiles, which would need a decision about which four actions earn the space.

## PRM-248 — The search highlights the row, not just the page

**Why**: PRM-247's search reached the right page and stopped there. The only `data-focus` anchors
were section headings, so searching for a client, a model or an instance landed on a table of
twenty rows with nothing marked — the scan the search exists to replace. The reader said it
plainly: nothing showed what had been found.

Two failures only appeared with the page in front of me. The flash gave up after two fixed
retries (60ms, then 900ms), which covers a page that answers from cache and misses Models
entirely — it takes over three seconds to list a node's catalogue, by which time the window had
closed. And Models shows one node at a time, so a model on a node the page was not showing never
rendered at all; the link now carries `?node=`. The retry became a poll to an eight-second
deadline, stopping the moment the element appears.

The flash also needed a background tint, not only a ring: on a `<tr>` a box-shadow is clipped or
dropped depending on the border model, so the highlight was invisible on exactly the rows this
item is about.

**Scope**: in — row-level `data-focus` on users, instances and downloaded models; `?node=` on
model links; the deadline poll; the background tint; `useFocusFlash` on Users, Dashboard and
Models. Out — highlighting a row inside a collapsed drawer, which would have to expand it first.

## PRM-249 — The login page tells you why you are there

**Why**: the last page of the sweep, and the one with the least excuse for being vague — it is
where an operator arrives already having lost something.

The sign-in reply carries `expires_in`, which on this deployment is 10800: three hours. Nothing
read it. The token was stored and the clock ignored, so a session ended in silence — the next
call 401'd, the reader was thrown here, and the page said nothing about why. `navigate("/")`
then discarded the route they were interrupted on. The deadline is stored now, the session ends
on time rather than arriving as a wall of failed panels, and the page distinguishes an expiry
from a sign-out and names the route it will return them to. A banner in the last five minutes
says when the session ends — not a toast, which clears itself after six seconds, and not a
countdown, because the useful fact is a wall-clock time that does not change.

A refused sign-in read `Invalid Credentials — Invalid client credentials.` — doubled, because
the generic handler joins an RFC 9457 title to a detail repeating it, and written for the
machine grant. A person who mistyped a password was told about credentials they had never been
shown.

The client-id tab is gone. A client id belongs to a software integration, which takes its token
from the auth-service's token endpoint; this dashboard signs in people. The endpoint still
accepts the other grant — this is the dashboard declining to offer it. A line on the page says
so, so nobody hunts for a control that was removed on purpose.

The form also carried no `autocomplete` tokens at all, so password managers mostly declined to
fill the one screen where they matter most.

**Measured, and handed to PRM-250**: a wrong password against a real account answers in 179ms
and against an unknown one in 4.5ms, because bcrypt only runs when the account exists — and
eight failed attempts in a row are served at full speed. The message is identical either way,
but the clock is not. That is a backend fix and gets its own item.

**Scope**: in — the page, email-only sign-in, stored expiry, the expiry/sign-out notice and
return route, the five-minute banner, autocomplete, show/hide, the Caps Lock hint, the 401
wording. Out — the timing oracle and login throttling (PRM-250), and any password-reset flow,
which has no endpoint behind it.
