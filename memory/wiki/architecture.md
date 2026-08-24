# Prometheus — Architecture

## Overview

**Prometheus** is a local-infrastructure SLM (Small Language Model) inference platform.
It runs quantized open-source models on bare-metal hardware using **llama.cpp** and exposes
inference capabilities to internal applications through a secured API gateway that enforces
authentication, rate limiting, and consumption metering.

**Design principle**: the inference engine (llama.cpp) is never containerised and never
reachable from outside the host. The Gateway is the single authorised entry point for all
client traffic.

---

## C4 Level 1 — System Context

> **Audience**: architects, technical leads.
> **Question**: Which systems exist and how do they interact at the boundary?
> **Rule**: one box per logical system; named protocol on each arrow; no internal components.

```
  ┌─────────────────────┐
  │  Developer /        │
  │  Data Scientist     │──── Bearer JWT ────────────────────────────────┐
  └─────────────────────┘                                                 │
                                                                          ▼
  ┌─────────────────────┐              ┌──────────────────────────────────────────┐
  │  Internal Service   │              │  Prometheus Gateway                      │
  │  (automated app)    │─Bearer JWT──▶│                                          │
  └──────────┬──────────┘              │  Validates access credentials.           │
             │                         │  Enforces usage policies.                │
             │ OAuth2                  │  Proxies approved requests to the        │
             │ client_credentials      │  AI inference engine.                    │
             ▼                         └────────────────────┬─────────────────────┘
  ┌──────────────────────────────────┐                      │
  │  Prometheus Auth Service         │◀── JWKS (RS256) ─────┘
  │                                  │    (public keys, on startup & rotation)
  │  Issues short-lived Bearer JWT   │
  │  tokens via OAuth2               │
  │  client_credentials grant.       │
  │  Publishes public key material   │
  │  for offline verification.       │
  └──────────────────────────────────┘

  ┌──────────────────────────────────┐
  │  AI Inference Engine             │
  │  (bare-metal host)               │◀── inference request ──── Prometheus Gateway
  │                                  │    (HTTP, per-model routing, internal only)
  │  Runs one process per model.     │
  │  Each instance bound to loopback │
  │  — never reachable from network. │
  └──────────────────────────────────┘

  ┌──────────────────────────────────┐
  │  HuggingFace Hub                 │
  │  [External]                      │◀── HTTPS (GGUF weights, setup only)
  └──────────────────────────────────┘    ── Prometheus Gateway / operator
```
---

## C4 Level 2 — Container Diagram

> **Audience**: senior developers, DevOps, security engineers.
> **Question**: What deployable units exist, what technology runs in each, and
>               how do they communicate (protocol + port)?
> **Rule**: tech stack per box; protocol + port on every arrow; no internal code details.

```
══════════════════════════════════════════════════════════════════════════════
  Podman Network: prometheus_net
══════════════════════════════════════════════════════════════════════════════

  ┌──────────────────────────────────────────────────────────────────┐
  │  Client Applications  [Podman containers]                        │
  │  ┌────────────┐   ┌────────────┐   ┌────────────┐               │
  │  │   App A    │   │   App B    │   │   App N    │               │
  │  └────────────┘   └────────────┘   └────────────┘               │
  └────────────────┬─────────────────────────┬────────────────────────┘
                   │ ① HTTP :8000            │ ② HTTP :9000
                   │   Bearer JWT            │   OAuth2 client_credentials
                   ▼                         ▼
  ┌────────────────────────────────┐  ┌────────────────────────────────┐
  │  Prometheus Gateway   :8000   │  │  Prometheus Auth Service :9000 │
  │  [Python 3.13 / FastAPI]      │  │  [Python 3.13 / FastAPI]       │
  │                               │  │                                │
  │  JWT auth · rate limiting     │③ │  OAuth2 token issuance         │
  │  prompt injection filter      │──▶  JWKS endpoint                 │
  │  llama.cpp proxy              │  │  Client registration           │
  │  SSE streaming · metering     │  │  HTTP :9000                    │
  │                               │  └───────────────┬────────────────┘
  └───────┬──────────────┬─────────┘                  │ ⑤ SQLite file I/O
          │ ④            │ ⑥                          ▼
          │ Redis        │ HTTP :8080        ┌──────────────────────────┐
          │ protocol     │ (loopback only)   │  SQLite  /data/auth.db   │
          │ :6379        │                   │  [SQLite 3]              │
          ▼              │                   │  client registry         │
  ┌──────────────────┐   │                   │  bcrypt hashed secrets   │
  │  Redis   :6379   │   │                   └──────────────────────────┘
  │  [Redis 7]       │   │
  │  rate-limit      │   │
  │  counters (TTL)  │   │
  │  JWT revocation  │   │
  └──────────────────┘   │

══════════════════════════════════════════════════════════════════════════════
  Bare-Metal Host  (outside Podman network — loopback only)
══════════════════════════════════════════════════════════════════════════════
                        │
          ┌─────────────┴────────────────────────────────────┐
          │ ⑥ BackendPool routes by model_id → backend_url    │
          │   (registry.yaml: host.containers.internal:port)  │
          │                                                    │
          ▼ HTTP :8080 · 127.0.0.1 only                       ▼ HTTP :8086 · 127.0.0.1 only
  ┌────────────────────────────────┐  ┌────────────────────────────────────┐
  │  llama-server   :8080          │  │  llama-server   :8086              │
  │  [llama.cpp · Metal / BLAS]    │  │  [llama.cpp · Metal / BLAS]        │
  │                                │  │                                    │
  │  id: llama3-1b-q4-local        │  │  id: llama3-8b-q4-local            │
  │  fast path — 1B params         │  │  quality path — 8B params          │
  └────────────────┬───────────────┘  └──────────────────┬─────────────────┘
                   │ mmap                                 │ mmap
                   └──────────────────┬───────────────────┘
                                      ▼
  ┌──────────────────────────────────────────────────────────────────────────┐
  │  GGUF Model Files  [host filesystem]                                     │
  │  macOS: ~/Library/Application Support/nomic.ai/GPT4All/                  │
  │  RHEL:  /srv/models/                                                     │
  └──────────────────────────────────────────────────────────────────────────┘
```
---

## Request Lifecycle

Every inference request flows through the gateway middleware stack in this order:

```
Client Request
    │
    ▼
[1] Request ID injection        X-Request-ID: <uuid4>
    │
    ▼
[2] JWT Authentication          RS256 · iss · aud · exp · sub validation
    │                           JWKS cached, rotated every 5 min
    ▼
[3] Scope enforcement           required: inference:write
    │
    ▼
[4] Model validation            model ID must exist in registry.yaml
    │                           → 400 unknown-model if absent
    ▼
[4b] Backend routing            registry.yaml: model_id → backend_url
    │                           → 503 model-not-loaded if no backend_url set
    │                           (BackendPool: shared httpx.AsyncClient per backend)
    ▼
[5] Prompt injection filter     Strip system-role override attempts
    │
    ▼
[6] Rate limiting               RPM + TPM per user_id + client_id (Redis)
    │
    ▼
[7] Proxy to llama.cpp          POST {backend_url}/v1/chat/completions
    │                           Streams SSE tokens back to client
    │                           → 503 backend-unavailable if connection fails
    ▼
[8] Metering                    Record model, backend_url, prompt_tokens,
                                completion_tokens, latency
```

---

## Component Responsibilities

### llama.cpp (Bare-Metal — one process per model)

- Each model runs as an independent host process; llama.cpp loads exactly one model per process.
- Managed via `runtime/scripts/start-server.sh` (single model) or `start-all-servers.sh` (N models).
- `start-all-servers.sh` launches each instance from a separate env file, writes a PID file to
  `/tmp/prometheus-<alias>.pid`, and auto-updates `backend_url` in `registry.yaml`.
- Each instance is bound to a unique loopback port (`PROMETHEUS_LLAMA_PORT`) — **never on a public interface**.
- Chat template is auto-detected by the `peg-native` parser from the GGUF metadata (`chat_template` key).
  Do **not** pass `--chat-template` — forcing any value causes the parser to evaluate only the
  4-token header prefix instead of the full user message, producing hallucinated responses.
- Platform adapters:
  - **macOS (Apple Silicon)**: Metal backend — all GPU layers offloaded to unified memory.
  - **RHEL 9.7 (Intel/AMD)**: OpenBLAS — saturates all CPU cores, no GPU.

**Dev model assignment** (macOS GPT4All path):

| Registry ID | File | Port | Role |
|-------------|------|------|------|
| `llama3-1b-q4-local` | `Llama-3.2-1B-Instruct-Q4_0.gguf` | 8080 | Fast path |
| `llama3-8b-q4-local` | `Meta-Llama-3-8B-Instruct.Q4_0.gguf` | 8086 | Quality path |

### Prometheus Auth Service (Podman :9000)

Self-contained OAuth2 authorization server for the Prometheus platform.

| Endpoint | Description |
|----------|-------------|
| `POST /token` | OAuth2 client_credentials grant — returns RS256 JWT |
| `GET /.well-known/jwks.json` | Public JWKS for offline JWT verification |
| `POST /admin/clients` | Register a new client (admin-only, `X-Admin-Key` required) |
| `GET /admin/clients` | List registered clients, sans secret hash |
| `DELETE /admin/clients/{id}` | Revoke a client registration |

Client secrets are stored as bcrypt hashes. The plain-text secret is only returned once at registration.

### Prometheus Gateway (Podman :8000)

The central control plane. Enforces all security and operational policies.

| Middleware layer | Responsibility |
|-----------------|----------------|
| Request ID | Inject `X-Request-ID` for distributed tracing |
| Auth | Validate RS256 JWT — `iss`, `aud`, `exp`, `sub`, `scope` |
| Authz | Enforce `inference:write` scope per endpoint |
| Model check | Reject unknown model IDs (400); reject models with no `backend_url` (503) |
| BackendPool | Shared `httpx.AsyncClient` per `backend_url`; clients created at startup, closed on shutdown |
| Injection filter | Strip system-role override attempts (`system` messages from users) |
| Rate limit | Per-`user_id` AND per-`client_id`: RPM + TPM caps via Redis |
| Proxy | Forward sanitised request to `{backend_url}/v1/chat/completions`; stream SSE back |
| Metering | Record `model`, `backend_url`, `prompt_tokens`, `completion_tokens`, latency per request |

**Admin endpoints**:

| Endpoint | Scope required | Description |
|----------|----------------|-------------|
| `GET /v1/models` | `inference:read` | Active models only (`backend_url` set) |
| `GET /v1/backends` | `admin:read` | All registered models with `backend_url` + `active`/`inactive`/`invalid` status |

### LlamaServerManager TUI (spec 008 — bare-metal host)

A Textual terminal UI and `pmgr` CLI for managing the llama-server lifecycle on the host.
It runs **on the bare-metal host** (not in a container) and communicates directly with each
llama-server process via its loopback port.

**CLI commands**: `pmgr start|stop|restart|pause|resume|status|download|deregister`

**TUI views**:

| View | Purpose |
|------|---------|
| Dashboard | Compact table — all models, status, CPU/RAM/uptime sparklines, registry summary |
| Instances | Full detail table + capacity bar + live per-model metrics panel |
| Registry | Edit `registry.yaml` entries (path, port, context_length, rss_estimate_mb) |
| Downloads | HuggingFace model download progress — real-time bar, speed, ETA, cancel/retry; supports multi-shard models (sequential) |
| Discovery | Search HuggingFace for GGUF models, browse per-repo file list, one-key `[d]` download |

**Data pipeline** (2-second poll cycle):

1. `scanner.scan()` calls `psutil.process_iter()` to find llama-server processes.
2. Each process is probed via `GET /health` to confirm it is alive.
3. `_proc_cache: dict[int, psutil.Process]` stores persistent `Process` objects across scans
   so that `cpu_percent(interval=None)` returns the real inter-scan delta (first call always
   returns 0.0 — that call is made at first encounter and the result discarded).
4. Live model metadata is fetched on-demand when the user selects a row:
   - `GET /props` → `default_generation_settings.n_ctx` (context length), `chat_template`
     (raw Jinja2 string; identified by `_detect_chat_template()`)
   - `GET /slots` → active slot count
   - `GET /metrics` (requires `--metrics` flag) → Prometheus text format: tokens served,
     throughput, prompt-eval latency, active requests
5. Fetched values are stored in `_live_cache` keyed by model ID so they survive the 2-second
   table rebuild cycle without flicker.

**macOS RAM note**: `psutil.virtual_memory().used` excludes compressed pages on macOS and
diverges from Activity Monitor. The correct formula is `vm.total - vm.available`.

### Redis

- Stores sliding-window rate limit counters with TTL.
- Stores JWT revocation list (jti → expiry).
- Ephemeral — no persistent storage required; counters reset on restart.

### Client Applications (Podman)

- Obtain a JWT from the Auth Service using the client credentials flow.
- Call `POST /v1/chat/completions` via the `openai` Python SDK or plain HTTP.
- Never contact llama.cpp directly — Podman network policy prevents this.

---

## Infrastructure

### Target Environments

| Property | Mac M2 (dev) | HPE DL380 × 2 (test) |
|----------|-------------|----------------------|
| OS | macOS (Apple Silicon) | Red Hat Enterprise Linux 9.7 |
| CPU | Apple M2 8-core | 2 × 8-core Intel/AMD (16 cores) |
| RAM | 16 GB unified | 256 GB |
| GPU | Apple Metal (unified memory) | None |
| llama.cpp backend | Metal (`GGML_METAL=ON`) | OpenBLAS (`GGML_BLAS=ON`) |
| Model storage | `~/Library/Application Support/nomic.ai/GPT4All/` | `/srv/models/` |
| Purpose | Developer E2E testing | Integration & performance testing |

### Technology Stack

| Technology Stack | |
|-------|----------|
| Gateway language | Python 3.13 |
| Gateway framework | FastAPI + uvicorn |
| Auth Service framework | FastAPI + uvicorn |
| Auth Service storage | SQLite (client registry) via SQLAlchemy |
| Async HTTP client | `httpx` |
| Auth library | `python-jose` (JWT RS256) + `bcrypt` (secret hashing) |
| Rate limiting store | Redis 7 |
| Dependency management | `uv` (workspace) |
| Container runtime | Podman + Podman Compose |
| SLM runtime | llama.cpp (`llama-server`) |
| Model format | GGUF (quantized) |

---

## Security Model

### Threat Model

| ID | Threat | Mitigation |
|----|--------|------------|
| T1 | Direct llama.cpp access bypassing auth | Network isolation — llama.cpp binds to `127.0.0.1` only; Podman network cannot reach it directly |
| T2 | Prompt injection (system-role override) | Gateway strips `system` messages injected by users; only gateway-controlled prompts forwarded |
| T3 | Token exhaustion / DoS | Rate limiting per `user_id` + `client_id` (RPM + TPM); hard monthly quotas |
| T4 | Auth bypass | Mandatory RS256 JWT validation on every request (except `/health`); no fallback auth mode |
| T5 | Credential / token leakage | JWTs not logged; client secrets stored as bcrypt hashes; never returned after registration |
| T6 | Metering gap | All code paths (including errors, timeouts) emit metering records before returning |
| T7 | Algorithm confusion attack | JWT parser pinned to RS256; `alg: none` and HS256 explicitly rejected |
| T8 | Forced `--chat-template` producing wrong output | `start-server.sh` never passes `--chat-template`; peg-native reads from GGUF metadata only |
| T9 | SSRF via malicious `backend_url` in registry.yaml | Gateway validates all `backend_url` values at startup against `_ALLOWED_BACKEND_HOSTS`; any non-loopback / non-docker-internal URL marks the model `invalid` and it is excluded from routing |

### Network Isolation

```
  Internet / internal network
          │
          ▼  :8000
  ┌──────────────────────────────────────┐
  │   Prometheus Gateway  (Podman)       │
  │   BackendPool validates all          │
  │   backend_url values against         │
  │   _ALLOWED_BACKEND_HOSTS at load     │
  │   (127.0.0.1, ::1,                   │
  │    host.docker.internal,             │
  │    host.containers.internal)         │
  └──────────┬───────────────────────────┘
             │  host.containers.internal:8080   host.containers.internal:8086
             ├──────────────────────────────────────────────┐
             ▼                                              ▼
  ┌─────────────────────────────┐    ┌──────────────────────────────────┐
  │  llama-server  127.0.0.1:8080│    │  llama-server  127.0.0.1:8086   │
  │  llama3-1b-q4-local          │    │  llama3-8b-q4-local             │
  │  bare-metal, NOT in Podman   │    │  bare-metal, NOT in Podman       │
  └─────────────────────────────┘    └──────────────────────────────────┘
```

Firewall rules on RHEL servers must block external access to inference ports (8080, 8086, and any additional model ports).

---

## Decisions Index

Project decisions live in `memory/decisions/`.

| Decision | Title | Date |
|----------|-------|------|
| [2026-03-28-llama-cpp-bare-metal]../decisions/2026-03-28-llama-cpp-bare-metal.md) | llama.cpp runs bare-metal, never containerised | 2026-03-28 |
| [2026-03-28-rs256-jwt]../decisions/2026-03-28-rs256-jwt.md) | RS256 JWT for gateway authentication | 2026-03-28 |
