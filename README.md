# Prometheus

> Local-infrastructure SLM inference platform with secured API gateway.

Prometheus runs quantized open-source language models on bare-metal hardware using **llama.cpp**, and exposes inference capabilities to containerized client applications through a secured gateway that enforces authentication, authorization, rate limiting, and consumption metering.

---

## Architecture

```
  ┌───────────────────────┐            ┌──────────────────────────────────┐
  │  Developer /          │            │                                  │
  │  Data Scientist       │──requests──▶   Prometheus Gateway             │
  └───────────────────────┘  AI result ◀──                                │
                                        │  The single secured entry point │
  ┌───────────────────────┐             │  for every AI inference request.│
  │  Internal Application │──requests──▶  Enforces who can call, how     │
  │  (automated service)  │  AI result ◀──  often, and what they can ask. │
  └──────────┬────────────┘            └────────────────┬─────────────────┘
             │ ①                                        │ ②
             │  obtains timed                           │  forwards
             │  access credential                       │  approved request
             ▼                                          ▼
  ┌──────────────────────────────┐    ┌──────────────────────────────────┐
  │  Prometheus Auth Service     │    │  AI Inference Engine             │
  │                              │    │                                  │
  │  Registers applications and  │    │  Runs open-source language       │
  │  issues short-lived access   │    │  models on local hardware.       │
  │  credentials. No credential, │    │  Never reachable directly —      │
  │  no inference.               │    │  only the Gateway may call it.   │
  └──────────────────────────────┘    └──────────────────────────────────┘
```

**How it works**:
1. An application registers with the Auth Service and obtains a short-lived
   access credential ①.
2. The application presents that credential to the Gateway, which validates it
   before forwarding the request to the AI engine ②.
3. The AI engine runs entirely on local hardware — no data leaves the network.

---

## Quick Start

### Prerequisites

- Python ≥ 3.11 + [uv](https://docs.astral.sh/uv/) (gateway development)
- **Podman** + Podman Desktop (running the containerised gateway) — Docker is not used
- Xcode Command Line Tools on macOS (`xcode-select --install`)
- A GGUF model file (see models pre-installed by GPT4All below)

### 1. Clone and configure

```bash
git clone https://github.com/<your-username>/prometheus-ai-inference.git
cd prometheus-ai-inference

# Install Python dependencies
uv sync

# Configure the gateway
cp gateway/.env.podman.example gateway/.env
# Edit gateway/.env — set JWT_ISSUER, JWT_PUBLIC_KEY_FILE, JWT_PUBLIC_KEY_HOST_PATH

# Create root .env for Podman Compose variable interpolation
cat > .env << 'EOF'
JWT_PUBLIC_KEY_HOST_PATH=/absolute/path/to/your/public.pem
EOF
```

### RHEL automated install (operators)

Operators deploying to RHEL 9.7 can use `scripts/install-rhel.sh` to provision a host, `scripts/install-rhel.sh --deploy` for fast post-release updates, and `scripts/validate.sh` to run smoke checks.

### 2. Install and start llama.cpp on the host (macOS, no Homebrew needed)

```bash
# Build from source — uses uv tool run cmake, no Homebrew or sudo required
bash runtime/scripts/install-server.sh
# Binary installed to ~/.local/bin/llama-server
```

**Option A — Manager TUI** (recommended, spec 008):

```bash
# Launch the interactive TUI to start/stop/monitor models
uv run pmgr
# Or use CLI commands:
uv run pmgr start llama3-8b-q4-local
uv run pmgr status
```

**Option B — Shell scripts** (manual, no TUI):

```bash
# Start both models (1B fast path on :8080 + 8B quality path on :8086)
bash runtime/scripts/start-all-servers.sh \
    runtime/mac-llama3-1b.env \
    runtime/mac-llama3-8b.env
# Binds each server to 127.0.0.1 only; auto-updates registry.yaml with backend_url
```

To start a single model only:

```bash
source runtime/mac-llama3-1b.env   # or your local .env copy
bash runtime/scripts/start-server.sh
```

> **GPT4All models**: If GPT4All is installed, GGUF files are already at
> `~/Library/Application Support/nomic.ai/GPT4All/`. The registry and env files
> reference these paths directly — no separate download needed.
>
> **Port note**: macOS reserves port 8081 for AirPlay. Use 8086 (or any other free port)
> for the second model instance.

### 3. Start the full stack

**Option A — Podman Compose (production-like):**
```bash
# Ensure Podman VM is running
podman machine start

podman compose -f podman-compose.yml up --build -d
# Gateway: http://localhost:8000
# Auth Service: http://localhost:9000
```

**Option B — Local development (no container):**
```bash
source .venv/bin/activate
uvicorn prometheus_gateway.asgi:app --host 0.0.0.0 --port 8000 --reload
```

### 4. Verify the stack is running

```bash
curl http://localhost:9000/health
# {"status":"ok"}

curl http://localhost:8000/health
# {"status":"ok"}

curl http://localhost:8000/v1/models
# {"object":"list","data":[{"id":"llama3-8b-q4-local",...}]}
```

### 5. Get a token and call the API

```bash
# Register a client with the auth service
CLIENT=$(curl -s -X POST http://localhost:9000/admin/clients \
  -H "X-Admin-Key: $AUTH_ADMIN_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"client_name":"my-app","role":"app","allowed_scopes":["inference:read"]}')
CLIENT_ID=$(echo $CLIENT | python3 -c "import sys,json; print(json.load(sys.stdin)['client_id'])")
CLIENT_SECRET=$(echo $CLIENT | python3 -c "import sys,json; print(json.load(sys.stdin)['client_secret'])")

# Obtain a JWT via client credentials
TOKEN=$(curl -s -X POST http://localhost:9000/oauth2/token \
  -d "grant_type=client_credentials&client_id=$CLIENT_ID&client_secret=$CLIENT_SECRET" \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")

# Chat completion
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"model":"llama3-8b-q4-local","messages":[{"role":"user","content":"What is AGI?"}],"max_tokens":120}'
```

> **Human operators** (RM-11): create a `password`-auth principal instead —
> `-d '{"client_name":"jane","role":"app","allowed_scopes":["inference:read"],"auth_method":"password","email":"jane@example.com","password":"..."}'`
> — then obtain a token with `grant_type=password&username=<email>&password=<password>`
> instead of `client_credentials`. The gateway's admin dashboard (`/admin`) uses this to
> log human operators in with email + password by default, alongside the existing
> client_id/client_secret mode for machine clients.

### Integrating your own client

Common questions from a developer wiring up a real client against this platform:

**Authentication (OAuth2)**

- **One token endpoint, no separate refresh endpoint.** `POST {auth-service}/oauth2/token`
  issues every token. There's no `grant_type=refresh_token` support today — tokens are
  short-lived and stateless (role-based TTL: `app` 5 min, `agent` 10 min, `cognitive`
  1 h, `admin` 3 h; see `ROLE_DEFAULT_TTL` in `auth-service/src/prometheus_auth/config.py`),
  and there's no server-side session to refresh. To get a new one, just call
  `/oauth2/token` again with the same `client_id`/`client_secret` — that credential pair
  is the durable thing, not the token. Plan your client to re-request a token whenever a
  call gets a `401`, or proactively a bit before `expires_in` runs out.
- **Grant type for a service-to-service client: `client_credentials`.** This is the one
  a real API client should use (see the example in step 5 above). `grant_type=password`
  also exists, but only for human operators signing into the admin dashboard with
  email + password (RM-11) — not the flow for a machine client.
- **Credentials are `client_id` + `client_secret`**, not an API key or a certificate.
  They're issued by an operator via `POST /admin/clients` (needs `X-Admin-Key`, or the
  dashboard's Create User screen) — the response's `client_secret` is shown once and
  can't be retrieved again afterwards.

**`POST /oauth2/token` example** (`client_credentials` grant — real response shape,
captured against a local dev instance and redacted for this doc):

```bash
curl -X POST http://localhost:9000/oauth2/token \
  -d "grant_type=client_credentials" \
  -d "client_id=<client_id from POST /admin/clients>" \
  -d "client_secret=<client_secret from POST /admin/clients>" \
  -d "scope=inference:read inference:stream model:llama3-8b-q4-local"
```

```json
{
  "access_token": "<header>.<payload>.<signature>",
  "token_type": "bearer",
  "expires_in": 300,
  "scope": "inference:read inference:stream model:llama3-8b-q4-local"
}
```

`access_token` is a signed JWT — decoding its payload (the middle, base64-encoded
segment) shows exactly what the gateway checks on every request:

```json
{
  "iss": "http://auth-service:9000",
  "sub": "<client_id, the token's subject>",
  "azp": "<client_id, same value, OAuth2 authorized-party>",
  "aud": "prometheus-gateway",
  "iat": 1788035748,
  "exp": 1788036048,
  "jti": "<unique token id, used for revocation lookups>",
  "scope": "inference:read inference:stream model:llama3-8b-q4-local",
  "role": "app",
  "client_name": "docs-example-client"
}
```

`scope` is what `Authorization: Bearer <access_token>` gets checked against on every
gateway call — `inference:read`/`inference:stream` gate the endpoint itself, each
`model:<id>` entry gates one specific model (RM-07, deny-by-default: no grant, no
access), and `exp` is when you'll need to call `/oauth2/token` again.

**Gateway API**

- **`POST /v1/chat/completions` follows the OpenAI Chat Completions format** — same
  request shape (`model`, `messages[]`, `stream`, `max_tokens`, `temperature`, and since
  RM-35, `tools`/`tool_choice` for native function-calling) and the same response shape
  (`choices[].message`, `usage`, etc.), so existing OpenAI-compatible SDKs/clients work
  by pointing their `base_url` at this gateway instead.
- **Base URL**: this is self-hosted infrastructure, not a hosted service with fixed
  dev/staging domains — the base URL is whatever host/port your operator deployed the
  gateway on (`http://localhost:8000` in this README's own local quickstart above; a
  real deployment's URL comes from whoever runs it).
- **`GET /v1/models`** — public, no token required, lists every active model:
  `{"object": "list", "data": [{"id", "object", "owned_by", "context_length", "family",
  "quantization", "modality"}, ...]}`. There's no per-model "supports tool-calling" flag —
  every model accepts the `tools`/`tool_choice` request fields, but whether the underlying
  model actually honors them depends on that model, not the gateway.
- **`GET /v1/models/mine`** (RM-45) — same response shape, but requires a Bearer token
  and returns only the models *your* client's `model:<id>` scopes actually grant, since
  access can be assigned or changed after your client was created. Useful for checking
  what you currently have before making an inference request, without guessing or
  hitting a `403`.

### 6. End-to-end integration test

```bash
# Runs all 12 checks: health, JWKS, client registration, token issuance,
# JWT claims, real inference against both models (1B + 8B), tampered token,
# no token, admin controls
uv run validations/e2e_test.py
```

---

## Development

### Setup

```bash
# Install uv (dependency manager) — only once
curl -LsSf https://astral.sh/uv/install.sh | sh

# Install all dependencies (creates .venv at repo root)
uv sync

# Activate the virtual environment
source .venv/bin/activate
```

### Gateway (Python / FastAPI)

```bash
# Tests
uv run pytest gateway/tests/ -v

# Lint + format check
uv run ruff check gateway/
uv run ruff format --check gateway/

# Type check
uv run mypy gateway/src/

# Add a runtime dependency
cd gateway && uv add <package>

# Add a dev dependency
cd gateway && uv add --dev <package>
```

### Auth Service (Python / FastAPI)

```bash
# Tests
(cd auth-service && uv run pytest tests/ -v)

# Lint + format + typecheck
(cd auth-service && uv run ruff check src/ && uv run ruff format --check src/ && uv run mypy src/)
```

### Manager CLI & TUI (spec 008)

```bash
# Launch the interactive TUI
uv run pmgr

# Non-interactive commands
uv run pmgr status                          # table of all detected processes
uv run pmgr start llama3-8b-q4-local       # start a model
uv run pmgr stop  llama3-8b-q4-local       # stop a model
uv run pmgr restart llama3-8b-q4-local     # stop + start
uv run pmgr list                            # all registry entries with running status

# Backends beyond llama.cpp — mlx (Apple Silicon), vllm, sglang (both need CUDA)
# See RM-06 in docs/roadmap.md for the comparison behind this list.
uv run pmgr register --backend mlx --id my-mlx-model --path mlx-community/<repo>

# Manager tests (split into core / api / tui — see runtime/manager/AGENTS.md)
uv run pytest runtime/manager/core/tests/ -v
uv run pytest runtime/manager/api/tests/ -v
uv run pytest runtime/manager/tui/tests/ -v
```

### Runtime tests

```bash
bash runtime/tests/test_runtime_scripts.sh
```

### Environment variables

See `gateway/.env.podman.example` and `auth-service/.env.example` for full configuration.

**Gateway** (`gateway/.env`):

| Variable | Required | Description |
|----------|----------|-------------|
| `JWT_ISSUER` | Yes | Expected `iss` claim in all JWTs |
| `JWT_AUDIENCE` | Yes | Expected `aud` claim (default: `prometheus-gateway`) |
| `JWT_PUBLIC_KEY_FILE` | One of | Path to RS256 public key PEM (inside container: `/run/secrets/jwt_public_key.pem`) |
| `JWT_PUBLIC_KEY_HOST_PATH` | Compose | **Host** path to the PEM file — used by Podman Compose bind-mount interpolation (set in root `.env`) |
| `JWT_JWKS_URL` | One of | Auth Service JWKS endpoint URL (e.g. `http://auth-service:9000/.well-known/jwks.json`) |
| `JWT_REVOCATION_REDIS_URL` | No | Redis URL for token revocation (omit to disable) |
| `MODEL_REGISTRY_PATH` | No | Path to `registry.yaml` (default: `runtime/models/registry.yaml`) |
| `ADMIN_DASHBOARD_ENABLED` | No | Serve the admin dashboard SPA at `/admin` (default: `false`) — instance lifecycle (RM-10) and the Users section (RM-11). |
| `AUTH_SERVICE_ADMIN_URL` | When admin dashboard enabled | auth-service admin base URL (e.g. `http://auth-service:9000/admin`) — backs the Users section. |
| `AUTH_SERVICE_ADMIN_API_KEY` | When admin dashboard enabled | Must match auth-service's own `AUTH_ADMIN_API_KEY`. |

**Auth Service** (`auth-service/.env`):

| Variable | Required | Description |
|----------|----------|-------------|
| `AUTH_ADMIN_API_KEY` | Yes | Secret key for `/admin/clients` endpoints |
| `AUTH_PRIVATE_KEY_FILE` | Yes | RS256 private key PEM for signing JWTs |
| `AUTH_PUBLIC_KEY_FILE` | Yes | RS256 public key PEM for JWKS endpoint |
| `AUTH_DATABASE_URL` | No | SQLite path (default: `/data/auth.db`) |
| `AUTH_JWT_ISSUER` | No | `iss` claim in issued JWTs (default: `https://auth.example.com`) |
| `AUTH_TOKEN_TTL_SECONDS` | No | JWT lifetime (default: `300`) |

> **Root `.env` is required for Podman Compose**: Compose reads the root `.env` to interpolate
> `${JWT_PUBLIC_KEY_HOST_PATH}` in `podman-compose.yml`. Without it, Compose creates a directory
> instead of a bind-mount and the gateway fails to start.

---

## Roadmap

See [roadmap.md](roadmap.md) for the index of shipped and planned work, and
[docs/roadmap.md](docs/roadmap.md) for the detail behind each item (why, scope,
tradeoffs). Items are implemented directly, one branch per item — no separate
spec-review pipeline.

---

## Git Workflow

```
main        ← production (protected, tagged releases)
  ↑ PR
develop     ← integration (protected, always green CI)
  ↑ PR
feat/NNN-*  ← one branch per spec
```

```bash
# Start a new feature
git checkout develop && git pull
git checkout -b feat/003-rate-limiting

# Open PR to develop when ready
# After develop is stable → PR to main
```

CI runs on every PR and merge. See [`.github/workflows/`](.github/workflows/).

---

## Project Structure

```
edge-ai-inference/
├── AGENTS.md                    # Copilot agent + project guidelines
├── README.md
├── validations/
│   └── e2e_test.py              # End-to-end integration test
├── gateway/                     # Prometheus API Gateway (Podman :8000)
│   ├── src/prometheus_gateway/
│   │   ├── auth/                # JWT middleware, JWKS, claims
│   │   └── models/              # Registry, request/response schemas
│   ├── tests/
│   ├── pyproject.toml
│   └── Dockerfile
├── auth-service/                # OAuth2 Auth Service (Podman :9000)
│   ├── src/auth_service/
│   │   ├── routes/              # /token, /admin/clients, /.well-known/jwks.json
│   │   ├── models/              # SQLAlchemy models, schemas
│   │   └── crypto.py            # RS256 key loading, JWT signing
│   ├── tests/
│   ├── pyproject.toml
│   └── Dockerfile
├── runtime/                     # llama.cpp bare-metal setup
│   ├── manager/                 # 3 packages — see runtime/manager/AGENTS.md (spec 008, RM-05)
│   │   ├── core/src/prometheus_manager_core/   # shared domain: scanner, lifecycle, registry, config
│   │   ├── api/src/prometheus_manager_api/     # FastAPI — containerized, pmgr-api
│   │   ├── tui/src/prometheus_manager_tui/     # Textual TUI + pmgr CLI — bare-metal only
│   │   ├── registry.yaml        # Model registry — source of truth (spec 008)
│   │   └── manager.toml         # Manager configuration
│   ├── models/
│   │   └── registry.yaml        # Legacy model registry (gateway fallback)
│   ├── scripts/
│   │   ├── install-server.sh    # Build llama-server from source
│   │   ├── start-server.sh      # Start inference server (env-parametrized)
│   │   └── download-model.sh    # HTTPS-only GGUF downloader
│   ├── logs/                    # Runtime: llama-server stdout/stderr (gitignored)
│   ├── run/                     # Runtime: PID files per model (gitignored)
│   └── tests/
│       └── test_runtime_scripts.sh
├── .github/
│   ├── agents/                  # Copilot custom agents
│   ├── instructions/            # File-specific coding instructions
│   └── prompts/                 # Reusable prompt commands
└── podman-compose.yml           # Gateway + Auth Service + Redis
```

---

## Security

- JWT RS256 — algorithm pinning, JWKS rotation, token revocation via Redis
- Zero unauthenticated endpoints (except `/health`)
- Per-model authorization — `model:<id>` scopes, deny-by-default — see [RM-07 in docs/roadmap.md](docs/roadmap.md#rm-07-fine-grained-per-model-authorization-scopes-item-2) for the migration impact on existing clients.
- Rate limiting per `user_id` + per `client_id`
- Prompt injection defence — `system`-role messages stripped before forwarding to llama.cpp
- Vision content parts (`image_url`) must be inline `data:` URIs — remote http(s) image URLs are rejected so the backend can't be used as an SSRF proxy (RM-09)
- llama.cpp bound to `127.0.0.1` — never reachable from Podman network directly
- RFC 9457 Problem Details on all errors — no stack traces exposed
- Client secrets stored as bcrypt hashes — never logged or returned after registration
- Admin endpoints protected by `X-Admin-Key` — never exposed outside internal Podman network

---

## Release History

| Version | Date | Highlights |
|---------|------|------------|
| v0.1.0 | 2026-03-28 | Gateway core, JWT auth, llama.cpp runtime scripts |
| v0.2.0 | 2026-03-28 | Auth Service (OAuth2 client credentials + JWKS), full E2E stack via Podman Compose |
| v1.3.0 | 2026-08-24 | Multi-backend model manager (llama.cpp/MLX/vLLM/SGLang), distributed inference across hosts, fine-grained per-model auth scopes, VLM + embeddings support, and a new React admin dashboard for lifecycle management |

---

## License

[Apache License 2.0](LICENSE).
