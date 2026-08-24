# Model Registry

How Prometheus tracks, routes, and exposes inference models. The registry is the single source of truth for all model metadata.

> Sources: `memory/specs/006-multi-model-gateway.md`, `memory/specs/008-llama-server-manager.md`, `memory/specs/010-registry-view-redesign.md`

---

## Ownership

The registry (`runtime/manager/registry.yaml`) is owned by the **Manager**, not the Gateway.

```
[Gateway :8000]         ← public discovery endpoint `/v1/models`
    │  GET /v1/models      (unauthenticated, discovery)
    │
    │  (internally, gateway queries Manager for authoritative data)
    │  GET /v1/backends    (service account JWT with scope: backend-registry:read)
    ▼
[Manager API :8090]     ← reads registry.yaml, returns discovery:true entries (admin: `/v1/backends`)
    │
    ▼
[registry.yaml]         ← single source of truth, on bare-metal host
```

The gateway never reads `registry.yaml` directly — it queries the Manager API. This keeps infrastructure concerns out of the gateway.

---

## registry.yaml schema

Each model entry:

```yaml
models:
  - id: llama3-1b-q4-local          # unique identifier used in API requests
    backend: llama_cpp               # llama_cpp | mlx | vllm | sglang — see RM-06/RM-08
    modality: text                   # text | vision | embedding — see RM-09 below
    mmproj_path: ""                  # vision projector .gguf — only for modality: vision on llama_cpp
    family: llama3                   # model family — selects prompt template (llama3, mistral, phi, qwen)
    quantization: Q4_0               # GGUF quantization level
    context_length: 8192             # max tokens (prompt + completion)
    port: 8080                       # loopback port for this instance
    backend_url: http://127.0.0.1:8080  # set when instance is running
    path: /path/to/model.gguf        # local GGUF file path
    downloaded: true                 # false = not yet on disk
    discovery: false                 # true = advertised via Manager API / Gateway
    rss_estimate_mb: 1000            # estimated RAM usage (model size × 1.2 heuristic)
    log_level: info
    hf_repo: bartowski/Meta-Llama-3.1-8B-Instruct-GGUF  # HuggingFace repo (set at registration)
    hf_filename: Meta-Llama-3.1-8B-Instruct-Q4_0.gguf   # single-file models
    hf_filenames: []                 # non-empty for sharded models — ordered list of all shard filenames
```

**Sharded models**: when `hf_filenames` is non-empty, `hf_filename` holds the first shard and `hf_filenames` contains the complete ordered list. The Manager downloads all shards sequentially and stores them under the same `dest_dir`. On retry, the full sequence restarts from shard 1.

**`backend_url`** is set automatically by `start-all-servers.sh` when an instance starts. It is cleared when the instance stops.

**`discovery`** controls visibility:
- `true` → included in `GET /v1/models` and accessible via the gateway
- `false` → registered but not externally accessible (default for new entries)

**`backend`** selects the launch command and how the scanner recognizes the
process — see [inference-engines.md](inference-engines.md) for the comparison
behind this. `path` means different things per backend: a local `.gguf` file
for `llama_cpp`, or commonly a HuggingFace repo id (e.g.
`mlx-community/Llama-3.2-3B-Instruct-4bit`) for `mlx`/`vllm`/`sglang`, which
load directly from the Hub. `pmgr register --backend <name>` sets it;
`register` defaults to `llama_cpp` for backward compatibility with existing
entries that predate this field.

---

## Adding a model to the registry

There are two ways to create a registry entry:

### 1. Manual edit
Edit `runtime/manager/registry.yaml` directly and fill in all required fields. Useful for models already on disk.

### 2. Auto-registration from Discovery tab (`[d]`)
Select a `.gguf` file in the Discovery tab and press `[d]`. The Manager:

1. Derives an ID from the filename: lowercase slug + `-local` suffix, max 63 chars (e.g. `llama-3-2-3b-instruct-q4-k-m-local`). Appends `-2`, `-3`, … if the ID already exists.
2. Assigns the next free port: lowest integer ≥ 8081 not already used by any registry entry.
3. Writes the new entry to `registry.yaml` with `downloaded: false`, `discovery: false`.
4. Enqueues the download and switches to the Downloads tab.

After the download completes, start the instance and toggle `discovery: true` in the Registry view to make it available via the gateway.

---

## Request routing

The gateway uses `model` field from the request body to look up the backend URL:

```
POST /v1/chat/completions  {"model": "llama3-1b-q4-local", ...}
         │
         │  registry.lookup("llama3-1b-q4-local")
         ▼
         http://127.0.0.1:8080    →  llama-server instance
```

Error responses by routing state:

| State | Response |
|-------|----------|
| Model not in registry | `400 unknown-model` |
| Registered, no `backend_url` | `503 model-not-loaded` |
| `backend_url` present, connection fails | `503 backend-unavailable` |
| Vision content part on a non-vision model | `400 modality-mismatch` |
| `/v1/embeddings` against a non-embedding model | `400 modality-mismatch` |

## Modalities (RM-09)

`modality` (`text` / `vision` / `embedding`) tells the gateway which endpoint and request
shape a model accepts, and tells `lifecycle.py` which extra flags to launch it with:

| modality | Gateway endpoint | llama_cpp launch flag |
|----------|-------------------|------------------------|
| `text` (default) | `POST /v1/chat/completions`, plain string content | — |
| `vision` | `POST /v1/chat/completions`, `content` may be an array with `image_url` parts | `--mmproj <mmproj_path>` |
| `embedding` | `POST /v1/embeddings` | `--embedding` |

Register with `pmgr register --modality vision --mmproj-path /path/to/mmproj.gguf` or
`--modality embedding`. `--modality` defaults to `text`, matching pre-RM-09 entries.

**Vision requests**: `image_url.url` must be an inline `data:image/...;base64,...` URI —
the gateway rejects `http(s)://` image URLs with a validation error. Letting the backend
fetch an arbitrary client-supplied URL would turn `/v1/chat/completions` into an SSRF
proxy for whatever the backend process can reach; inlining the image closes that off. A
`vision`-modality model still accepts plain-text-only messages — only the `image_url`
content-part type requires a vision model, not the array-content shape itself.

**Embeddings requests**: `POST /v1/embeddings` mirrors OpenAI's shape —
`{"model": "...", "input": "text" | ["text", ...]}` — and is proxied to the backend's own
`/v1/embeddings` unchanged. It goes through the same auth checks as chat completions
(`inference:read` scope + per-model `model:<id>` grant, RM-07) but is otherwise separate
from `/v1/chat/completions` — no `max_tokens`/context-length accounting, since embedding
requests don't generate completions.

**What's verified**: both flags were checked against a real llama-server build on this Mac
— `--embedding` launched against `second-state/All-MiniLM-L6-v2-Embedding-GGUF` and served
a real `/v1/embeddings` response; `--mmproj` launched against
`ggml-org/SmolVLM-256M-Instruct-GGUF` and correctly answered a real image content-part
chat request. **What's not wired**: only `llama_cpp` dispatches on `modality` today —
`mlx`/`vllm`/`sglang` accept the field (so it round-trips through the registry and the
gateway) but `lifecycle.py`'s command builders for those three backends don't yet add
modality-specific flags (e.g. `mlx-vlm`/`mlx-whisper` for MLX vision/audio — see
[inference-engines.md](inference-engines.md)). Follow-up work, not a blocker for the
llama_cpp path documented above.

All backend URLs must resolve to loopback (`127.0.0.1`), `host.containers.internal`, or a
node hostname explicitly configured via `MANAGER_NODES` (RM-08 phase 2, below) — never an
arbitrary public address.

## Allowed backend hosts

The gateway enforces a whitelist of allowed backend hostnames to prevent routing to arbitrary external hosts. The canonical allowed values are:

- `127.0.0.1` (IPv4 loopback)
- `::1` (IPv6 loopback)
- `host.docker.internal` (Docker / Podman alias for host)
- `host.containers.internal` (Podman alias for host)
- the hostname of every node configured in `MANAGER_NODES` (RM-08 phase 2 — dynamic, not in the fixed list above)

Operational notes:
- When running the Manager inside a container, set `PMGR_PROXY_HOST=host.containers.internal` so the Manager rewrites `127.0.0.1` in `backend_url` to the container-visible host alias for health probing.
- Some runtimes resolve `host.containers.internal` to a numeric IP (e.g. `10.89.0.1`) before the gateway sees it. The gateway accepts such numeric IPs only when they correspond to one of the allowed host aliases (this prevents accidental exposure to arbitrary IPs).
- Do not set `backend_url` to a public IP or hostname manually. For remote model hosts, use the `MANAGER_NODES` mechanism below — it's the reviewed, purpose-built path; the allowlist only trusts hosts the operator explicitly configured, never arbitrary ones.

## Distributed nodes (RM-08 phase 2)

Each host in the fleet (a Mac, a DGX Spark, a Linux server, …) runs its own bare-metal
`pmgr-api` (`prometheus-manager-api`, from RM-05) with its own `registry.yaml`. There is
**no central orchestrator** — the gateway is the only thing that's aware of the whole
fleet, and it only *reads* from each node's API; it never tells a node what to run.

```
gateway ── polls GET /v1/backends every MANAGER_POLL_INTERVAL_S ──▶  pmgr-api  (Mac, :8090)
        ── polls GET /v1/backends every MANAGER_POLL_INTERVAL_S ──▶  pmgr-api  (DGX Spark, :8090)
        ── polls GET /v1/backends every MANAGER_POLL_INTERVAL_S ──▶  pmgr-api  (Linux server, :8090)
```

**Gateway config** — `MANAGER_NODES` replaces `MANAGER_URL` for more than one node:

```bash
# Single node (unchanged, still works):
MANAGER_URL=http://127.0.0.1:8090

# Multiple nodes — "name1=url1,name2=url2,...":
MANAGER_NODES=mac-m4-max=http://mac.local:8090,dgx-spark=http://dgx.local:8090
```

`MANAGER_NODES` takes priority over `MANAGER_URL` when both are set. The same
`MANAGER_CLIENT_ID`/`MANAGER_CLIENT_SECRET` service-account credentials are used for every
node — all nodes' `pmgr-api` instances validate JWTs against the same central
auth-service, so one token works fleet-wide.

**Per-node requirement**: each node's `pmgr-api` must set `PMGR_PROXY_HOST` to that node's
own network-reachable hostname or IP (e.g. `PMGR_PROXY_HOST=dgx.local` on the DGX Spark),
**not** left as loopback. Without this, the node's `/v1/backends` reports
`backend_url: http://127.0.0.1:<port>` — meaningful only on that node's own machine — and
the gateway, running elsewhere, would never be able to reach it. The gateway derives its
per-node allowlist entry from the *hostname in `MANAGER_NODES`*, and expects the node's
reported `backend_url` to use that same hostname.

**model_id collisions**: model ids must be unique across the whole fleet, the same way
they're already unique within one node's registry. If two nodes report the same
`model_id`, the gateway keeps whichever node it saw first (stable per process lifetime)
and logs `manager_sync.model_id_collision` — it does not silently overwrite. Avoid this by
namespacing ids per node if collisions are likely (e.g. `llama3-8b-mac` vs. `llama3-8b-dgx`).

**Partial availability**: if one node's `pmgr-api` is unreachable, only *that node's*
models disappear from the aggregated registry on the next poll — the rest of the fleet is
unaffected. This is a resilience property, not a routing decision: the gateway still fails
a request the normal way (`503 model-not-loaded`) if the specific model a client asked for
was on the unreachable node.

**What's verified vs. not**: the config parsing, multi-node polling, host-allowlisting, and
model_id-collision logic are unit-tested (`gateway/tests/test_manager_sync.py`) against
mocked HTTP responses simulating multiple nodes. The actual cross-machine
`PMGR_PROXY_HOST` rewrite was **not** live-verified against two real separate hosts in this
change — there was no second machine available to test against. Validate this on your
actual fleet (Mac + DGX Spark, etc.) before relying on it in production.

---

## Discovery flag lifecycle

| Event | `discovery` value |
|-------|------------------|
| New entry added (manual or via TUI) | `false` |
| Instance starts successfully | automatically set to `true` |
| Instance stops or is deregistered | automatically set to `false` |
| Operator toggles with `[v]` in TUI | toggled manually |

This means `GET /v1/models` (gateway) always reflects the live running state — no manual sync needed.

---

## Managing the registry

### Via TUI (`pmgr tui`)

- **Registry tab**: browse all entries, toggle discovery with `[v]`, see catalog metadata (family, quant, context length, estimated RAM, source, size)
- **Downloads tab**: queue HuggingFace downloads with real-time progress; auto-registers new entry
- **Discovery tab**: search HuggingFace, press `[d]` on any GGUF file → auto-registers + enqueues download

### Via CLI (`pmgr`)

```bash
pmgr list                             # show all registered models
pmgr start  <model-id>               # start instance (sets backend_url, discovery:true)
pmgr stop   <model-id>               # stop instance  (clears backend_url, discovery:false)
pmgr restart <model-id>              # stop + start
pmgr pause  <model-id>               # SIGSTOP — freeze process, keeps port reserved
pmgr resume <model-id>               # SIGCONT — unfreeze
pmgr deregister <model-id>           # stop + remove from registry.yaml entirely
pmgr-api                            # start Manager REST API only (no TUI)
```

### Via Manager REST API (`http://host:8090`)

Read (scope `backend-registry:read`):
```
GET    /v1/backends                    → list registered models with live state
                                          ?include_hidden=true also includes discovery:false
                                          entries (RM-10 — operator use; the default omits
                                          them since this endpoint also feeds the gateway's
                                          routing sync, which should only see exposed models)
GET    /v1/backends/{model_id}         → single model with live state
```

Write (scope `backend-registry:write`, RM-10 — same JWT/JWKS auth as read, separate scope):
```
POST   /v1/backends                    → register (body: id, port required; path/family/
                                          quantization/backend/modality/mmproj_path/
                                          discovery/hf_repo/hf_filename/hf_sha256 optional)
                                          → 201 with the created entry, 400 on validation error
PATCH  /v1/backends/{model_id}         → update fields (any subset of the optional register
                                          fields above; id is not patchable — that's a
                                          remove+re-add, not an in-place edit) → 200 with
                                          the updated entry, 400 on validation error. The
                                          *resulting* merged entry is re-validated, so e.g.
                                          changing only `backend` still re-checks the
                                          existing `path` against the new backend.
DELETE /v1/backends/{model_id}         → deregister (stops it first if running) → 204
POST   /v1/backends/{model_id}/start   → 200 with merged live state, 409 if already running
POST   /v1/backends/{model_id}/stop    → 200, 409 if not running
POST   /v1/backends/{model_id}/restart → 200
```

Authentication: RS256 JWT (service account) issued by `auth-service`. The Gateway's
`ManagerRegistrySync` uses a `backend-registry:read`-only token for its periodic polling;
the Gateway's admin dashboard (below) additionally needs `backend-registry:write`.

### Via Gateway API (`http://gateway:8000`)

```
GET  /v1/models          → public discovery: list active models (discovery:true + backend_url set)
GET  /v1/backends        → admin endpoint — requires admin:read scope
```

### Admin dashboard (RM-10)

`GET /admin` (when `ADMIN_DASHBOARD_ENABLED=true`) serves a static React SPA — built from
`gateway/admin-ui/`, output copied to `gateway/src/prometheus_gateway/admin/static/` — that
lets an operator see and control every node's instances from one page: start/stop/restart,
register, edit, and deregister, without SSH access to any individual host. It calls
`/admin/api/*` (JSON, `admin:read`/`admin:write` scopes), which the gateway proxies to the
right node's manager-api using the `MANAGER_NODES` config from RM-08 phase 2. Full auth
flow and scope-grant commands: [auth-model.md](auth-model.md#admin-dashboard-rm-10).

**Not in phase 1**: HuggingFace search/browse and triggering downloads from the dashboard
(the register form takes an already-known local path or `hf_repo`/`hf_filename`, mirroring
the TUI's "1. Manual edit" flow above — not its Discovery-tab HF search). That requires a
new async download-job subsystem in manager-api (today's `download_model()` is a blocking,
TUI-process-local call with no REST exposure) — tracked as a phase 2 follow-up.

---

## Capacity warnings

Before launching a new instance the manager checks estimated RAM:

| Threshold | Action |
|-----------|--------|
| > 85% host RAM | Yellow warning dialog in TUI |
| > 95% host RAM | Red blocking alert — launch aborted unless operator confirms override |

`rss_estimate_mb` is set in the registry. If missing, heuristic: GGUF file size × 1.2.

---

## Port convention

Each model instance gets a unique loopback port. Current assignments in the project:

| Port | Model ID |
|------|----------|
| 8080 | `llama3-1b-q4-local` |
| 8083 | `functionary-small-v24-q4-local` |
| 8084 | `mistral-7b-v01-q4-local` |
| 8085 | (next available) |
| 8086 | `llama3-8b-q4-local` |
| 8089 | `mistral-7b-v02-q4-local` |

New ports are auto-assigned by the manager when adding entries via TUI/Discovery.

---

## Related

- `memory/specs/006-multi-model-gateway.md` — per-model routing implementation
- `memory/specs/008-llama-server-manager.md` — Manager TUI, CLI, and REST API
- `memory/specs/010-registry-view-redesign.md` — discovery flag and Registry view columns
- `memory/decisions/2026-05-03-manager-owns-registry.md` — why registry moved from gateway to manager
- [deployment.md](deployment.md) — how to start all servers from env files
