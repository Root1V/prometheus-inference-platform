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

All backend URLs must resolve to loopback (`127.0.0.1`) or `host.containers.internal` — never a public address.

## Allowed backend hosts

The gateway enforces a whitelist of allowed backend hostnames to prevent routing to arbitrary external hosts. The canonical allowed values are:

- `127.0.0.1` (IPv4 loopback)
- `::1` (IPv6 loopback)
- `host.docker.internal` (Docker / Podman alias for host)
- `host.containers.internal` (Podman alias for host)

Operational notes:
- When running the Manager inside a container, set `PMGR_PROXY_HOST=host.containers.internal` so the Manager rewrites `127.0.0.1` in `backend_url` to the container-visible host alias for health probing.
- Some runtimes resolve `host.containers.internal` to a numeric IP (e.g. `10.89.0.1`) before the gateway sees it. The gateway accepts such numeric IPs only when they correspond to one of the allowed host aliases (this prevents accidental exposure to arbitrary IPs).
- Do not set `backend_url` to a public IP or hostname. If you need remote model hosts, use a secured tunnel or a registry entry specifically marked and reviewed for that purpose.


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
pmgr serve                            # start Manager REST API only (no TUI)
```

### Via Manager REST API (`http://host:8090`)

```
GET  /v1/backends        → list all registered models with live state (admin, requires JWT scope: backend-registry:read)
```

Authentication: RS256 JWT (service account) with scope `backend-registry:read`, issued by `auth-service`. The Gateway uses this internal client to fetch the authoritative registry and then exposes a filtered, public discovery view at its own `GET /v1/models` endpoint.

### Via Gateway API (`http://gateway:8000`)

```
GET  /v1/models          → public discovery: list active models (discovery:true + backend_url set)
GET  /v1/backends        → admin endpoint (if implemented on gateway) — requires admin scope; in normal operation the Gateway proxies discovery from Manager and exposes only `/v1/models` for clients
```

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
