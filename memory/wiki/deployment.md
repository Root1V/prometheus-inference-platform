# Deployment

How to start and stop the full Prometheus stack. Covers both environments: macOS (Metal, development) and RHEL 9.7 (OpenBLAS, production/test).

> Sources: `memory/specs/003-llama-cpp-runtime.md`, `memory/specs/004-podman-containerization.md`, `memory/specs/008-llama-server-manager.md`

> **Python workspace**: all services (gateway, auth-service, manager, telemetry) share a single uv workspace rooted at `/`. Run `uv sync` once from the repo root to install all dependencies into a shared `.venv`. There is no separate `.venv` per service.

---

## Stack overview

```
bare-metal host
├── llama-server :808x   (one process per model, managed by pmgr)
└── pmgr serve   :8090   (Manager REST API)

Podman network
├── gateway       :8000   (JWT validation · rate limiting · proxy)
├── auth-service  :9000   (OAuth2 token issuance · JWKS)
├── redis         :6379   (rate-limit counters · revocation cache)
└── observability stack   (Loki · Tempo · Grafana · Promtail)
```

**Startup order**: llama-server(s) first → Manager API → containers.

---

## Prerequisites

> **RHEL 9.7**: use `.env.redhat` as your starting point — already has `host.containers.internal`, Linux paths, and RHEL CA bundle pre-filled.
> ```bash
> cp .env.redhat .env   # then edit absolute paths for your server
> ```

Operators deploying to RHEL 9.7 can automate these host-provisioning steps using `scripts/install-rhel.sh`; run `scripts/validate.sh` afterwards to verify the installation. See `memory/specs/023-redhat-compatibility.md` for details and operator guidance.

### 1. System user for operations

Create a dedicated `llmops` user that owns the project directory and runs management tasks.

```bash
sudo useradd --system --create-home --shell /bin/bash llmops || true
sudo mkdir -p /opt/prometheus-ai-inference
sudo chown -R llmops:llmops /opt/prometheus-ai-inference
ls -ld /opt/prometheus-ai-inference
```

Omit `--system` for a regular interactive account and set a password with `sudo passwd llmops`. Do not run containers as this user — containers use numeric UIDs inside images.

### 2. Install uv (tooling)

`uv` manages all Python dependencies. Install it into the ops user environment:

```bash
# RHEL 9.7
sudo dnf install -y python3 python3-pip  # python3-venv not needed on RHEL 9 — venv is included in python3

python3 -m pip install --user uv

# Ensure ~/.local/bin is on PATH
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc
source ~/.bashrc

uv --version

# Sync workspace deps from repo root
cd /opt/prometheus-ai-inference
uv sync
```

### 3. Proxy settings (if required)

If the host sits behind a corporate HTTP(S) proxy, export these variables **before any step that pulls from the internet** (package install, build, model download). Add them to `/etc/environment` or the shell profile of the `llmops` user so they persist across reboots and are inherited by systemd units.

```bash
# HTTP / HTTPS proxy (replace with your proxy host:port)
http_proxy=http://<proxy-host>:<port>
https_proxy=http://<proxy-host>:<port>

# Uppercase variants required by some tooling
HTTP_PROXY=$http_proxy
HTTPS_PROXY=$https_proxy

# Bypass list — internal Podman services must not go through the proxy
NO_PROXY="localhost,127.0.0.1,.internal,gateway,manager,auth-service,redis,loki,promtail,tempo,grafana"
no_proxy=$NO_PROXY
```

For containers, pass the same variables via `podman-compose.yml` `environment:` or inject them at image build time if packages are pulled during the build.

### 4. Build llama-server (once per machine)

**macOS (Metal / Apple Silicon)**
```bash
brew install cmake git llvm
git clone https://github.com/ggerganov/llama.cpp.git
cd llama.cpp
cmake -B build -DGGML_METAL=ON -DCMAKE_BUILD_TYPE=Release
cmake --build build --config Release -j$(sysctl -n hw.logicalcpu)
sudo cmake --install build --prefix ~/.local
```

**RHEL 9.7 (OpenBLAS, CPU-only)**
```bash
sudo dnf install cmake gcc gcc-c++ git openblas-devel
git clone https://github.com/ggerganov/llama.cpp.git
cd llama.cpp
cmake -B build -DGGML_BLAS=ON -DGGML_BLAS_VENDOR=OpenBLAS -DCMAKE_BUILD_TYPE=Release
cmake --build build --config Release -j$(nproc)
sudo cmake --install build --prefix ~/.local
```

Or use the install script:
```bash
bash runtime/scripts/install-server.sh
```

### 5. Download models

```bash
# Via CLI
pmgr download <huggingface-repo-id> <filename.gguf>

# Via TUI Discovery tab: search → [d]

# Via script (legacy)
bash runtime/scripts/download-model.sh <model-url> <local-path>
```

See [TLS for downloads (HuggingFace)](#tls-for-downloads-huggingface) if downloads fail with SSL certificate errors behind a corporate proxy.

### 6. Configure `.env` files

Three `.env` files must exist before running `podman compose up`:

| File | How Compose reads it | Purpose |
|------|----------------------|---------|
| `.env` (root) | Variable substitution (`${VAR}`) | Bind-mount host paths · bind hosts · secrets |
| `gateway/.env` | `env_file:` in `gateway` service | JWT config · Manager API credentials · OTEL |
| `auth-service/.env` | `env_file:` in `auth-service` service | RSA key paths · JWT issuer · admin key · DB |

Copy the templates:
```bash
cp .env.redhat.example .env                      # then set absolute paths for your host
cp gateway/.env.podman.example gateway/.env
cp auth-service/.env.example auth-service/.env
```

#### Generate secrets

Run these commands **once** to generate the required secrets, then paste the output into the corresponding `.env` file.

```bash
# auth-service/.env — AUTH_ADMIN_API_KEY
# Admin key used to call the auth-service admin API (register clients, create users)
openssl rand -hex 32

# auth-service/.env — SHARE_TOKEN_ENCRYPTION_KEY
# Encrypts credential share links (specs/016)
openssl rand -hex 32

# root .env — GRAFANA_SECRET_KEY
# Grafana internal session signing key
openssl rand -hex 32

# root .env — GRAFANA_ADMIN_PASSWORD
# Grafana web UI admin password
openssl rand -base64 16
```

> `MANAGER_CLIENT_ID` and `MANAGER_CLIENT_SECRET` in `gateway/.env` are **not pre-generated** — they come from registering a client via the auth-service admin API after the stack is running for the first time. See [Register the gateway client](#register-the-gateway-client) below.

#### Register the gateway client

Run once after the auth-service container is healthy (Step 3):

```bash
# Read the admin key from auth-service/.env
AUTH_ADMIN_API_KEY=$(grep ^AUTH_ADMIN_API_KEY auth-service/.env | cut -d= -f2)

curl -s -X POST https://localhost:9000/admin/clients \
  -H "Content-Type: application/json" \
  -H "X-Admin-Key: $AUTH_ADMIN_API_KEY" \
  --cacert auth-service/certs/dev.crt \
  -d '{"client_name":"gateway-manager-sync","role":"app","allowed_scopes":["backend-registry:read"]}' \
  | python3 -m json.tool
```

Copy `client_id` and `client_secret` from the response into `gateway/.env`:
```bash
MANAGER_CLIENT_ID=<client_id from response>
MANAGER_CLIENT_SECRET=<client_secret from response>
```

Then restart the gateway container so it picks up the new credentials:
```bash
podman compose -f podman-compose.yml restart gateway
```

#### Root `.env`

Every host-path bind-mount in `podman-compose.yml` must be declared here. Missing variables → Compose silently creates a directory at the mount target → `IsADirectoryError` at runtime.

```bash
# root .env (gitignored — copy from .env.redhat.example)

# JWT keys (host paths)
JWT_PUBLIC_KEY_HOST_PATH=/etc/prometheus/keys/public.pem
JWT_PRIVATE_KEY_HOST_PATH=/etc/prometheus/keys/private.pem

# TLS certificates (host paths)
TLS_CERT_HOST_PATH=/etc/prometheus/certs/gateway.crt
TLS_KEY_HOST_PATH=/etc/prometheus/certs/gateway.key
AUTH_TLS_CERT_HOST_PATH=/etc/prometheus/certs/auth.crt
AUTH_TLS_KEY_HOST_PATH=/etc/prometheus/certs/auth.key

# Auth DB (SQLite persisted across restarts)
AUTH_DB_HOST_PATH=/var/lib/prometheus/auth-service

# Logs (host directory mounted into containers)
CONTAINER_LOG_HOST_PATH=/var/log/prometheus
MANAGER_LOG_HOST_PATH=/var/log/prometheus/manager

# Manager runtime dirs (PID files + logs on bare-metal)
MANAGER_PID_ROOT=/var/run/prometheus/runtime/run
MANAGER_LOG_ROOT=/var/log/prometheus/runtime/logs

# Issuer — must match AUTH_JWT_ISSUER in auth-service/.env and JWT_ISSUER in gateway/.env
AUTH_JWT_ISSUER=https://prometheus-victor.internal

# CA bundle (RHEL default — required on hosts behind Zscaler/corporate proxy)
SSL_CERT_FILE=/etc/pki/tls/certs/ca-bundle.crt
REQUESTS_CA_BUNDLE=/etc/pki/tls/certs/ca-bundle.crt

# Bind hosts (defaults: loopback for admin interfaces)
AUTH_BIND_HOST=127.0.0.1
GRAFANA_BIND_HOST=127.0.0.1

# llama-server bind host for instances launched by the Manager.
# 127.0.0.1 (default): loopback only — use on bare-metal; most secure.
# 0.0.0.0: all interfaces — required when Podman containers need to reach
#   llama-server via host.containers.internal. Use only in trusted networks.
PROMETHEUS_LLAMA_BIND_HOST=127.0.0.1

# Manager container → host bridge.
# Must be consistent with PROMETHEUS_LLAMA_BIND_HOST:
#   PROMETHEUS_LLAMA_BIND_HOST=0.0.0.0  →  PMGR_PROXY_HOST=host.containers.internal
#   PROMETHEUS_LLAMA_BIND_HOST=127.0.0.1 →  PMGR_PROXY_HOST=  (empty — bare-metal mode)
# Source: manager.toml [api] proxy_host. Overridden by this env var at runtime.
PMGR_PROXY_HOST=host.containers.internal

# Performance tuning (RHEL/CPU-only)
PROMETHEUS_GPU_LAYERS=0
PROMETHEUS_THREADS=32

# Secrets (replace before production — generate with: openssl rand -hex 32)
GRAFANA_SECRET_KEY=replace-with-long-random-string
GRAFANA_ADMIN_PASSWORD=replace-with-strong-password
```

#### `gateway/.env`

Injected directly into the `gateway` container via `env_file:`.

```bash
# gateway/.env (gitignored — copy from gateway/.env.podman.example)

# JWT validation — must match AUTH_JWT_ISSUER in auth-service/.env and root .env
JWT_ISSUER=https://prometheus-victor.internal
JWT_AUDIENCE=prometheus-gateway
JWT_CLOCK_SKEW_SECONDS=30

# Key source: JWKS is preferred for Podman Compose (zero-downtime key rotation).
# The auth-service serves the JWKS endpoint automatically.
JWT_JWKS_URL=https://auth-service:9000/.well-known/jwks.json
# Alternative — static bind-mounted PEM (bare-metal / standalone dev):
# JWT_PUBLIC_KEY_FILE=/run/secrets/jwt_public_key.pem

# Token revocation (Redis is on the internal Podman network)
JWT_REVOCATION_REDIS_URL=redis://redis:6379/0

# LLAMA_CPP_URL is deprecated — backend URLs are now set per-model in registry.yaml
# (specs/006-multi-model-gateway.md — AC-10). Remove from any existing .env.

# Manager REST API — the gateway polls this for the live registry.
# Register the client once — see "Register the gateway client" in step 6.
MANAGER_URL=http://manager:8090
MANAGER_CLIENT_ID=<client_id from auth-service admin API>
MANAGER_CLIENT_SECRET=<client_secret from auth-service admin API>

# Web Chat UI (specs/013-web-chat-ui-proxy.md)
UI_ENABLED=true
AUTH_SERVICE_TOKEN_URL=https://auth-service:9000/oauth2/token
AUTH_SERVICE_TLS_VERIFY=false   # self-signed cert in the Podman internal network

LOG_LEVEL=INFO
OTEL_EXPORTER_OTLP_ENDPOINT=http://tempo:4318
```

> `GATEWAY_TLS_CERT_FILE` and `GATEWAY_TLS_KEY_FILE` are **not set here** — `podman-compose.yml` overrides them to the container-internal `/run/secrets/` paths via the `environment:` block. Host paths come from `TLS_CERT_HOST_PATH` / `TLS_KEY_HOST_PATH` in the **root** `.env`.

#### `auth-service/.env`

Injected directly into the `auth-service` container via `env_file:`.

```bash
# auth-service/.env (gitignored — copy from auth-service/.env.example)

# Container-internal paths — DO NOT CHANGE (match the volume targets in podman-compose.yml)
AUTH_PRIVATE_KEY_FILE=/run/secrets/jwt_private_key.pem
AUTH_PUBLIC_KEY_FILE=/run/secrets/jwt_public_key.pem

# kid tag — must match the filename stem of the active key pair (e.g. "2026-q1")
AUTH_ACTIVE_KID=2026-q1

# Issuer — must be identical to JWT_ISSUER in gateway/.env and AUTH_JWT_ISSUER in root .env
AUTH_JWT_ISSUER=https://prometheus-victor.internal

# Required — generate with: openssl rand -hex 32
AUTH_ADMIN_API_KEY=<replace-with-openssl-rand-hex-32>

# DB — container-internal path matching the /data bind-mount in podman-compose.yml
AUTH_DB_URL=sqlite+aiosqlite:////data/auth.db

# Redis (revocation cache — internal Podman network)
AUTH_REVOCATION_REDIS_URL=redis://redis:6379/0

# Rate limiting
AUTH_RATE_LIMIT_RPM=10

LOG_LEVEL=INFO
OTEL_EXPORTER_OTLP_ENDPOINT=http://tempo:4318

# AUTH_TLS_CERT_FILE / AUTH_TLS_KEY_FILE — do NOT set here for Podman deployments.
# podman-compose.yml overrides them to /run/secrets/ paths via environment:.

# Credential share links — generate with: openssl rand -hex 32
SHARE_TOKEN_ENCRYPTION_KEY=<replace-with-openssl-rand-hex-32>
```

> `AUTH_JWT_ISSUER` (auth-service) and `JWT_ISSUER` (gateway) **must be identical** — the auth-service embeds this string as the `iss` claim; the gateway rejects any token whose `iss` does not match.

### 7. Keys, certificates, and host directory setup

Run these commands once per host before starting the stack.

#### JWT keys and TLS certificates

```bash
sudo mkdir -p /etc/prometheus/keys /etc/prometheus/certs
sudo chown $(whoami) /etc/prometheus/keys /etc/prometheus/certs

# RSA keypair for JWT signing
openssl genpkey -algorithm RSA -out /etc/prometheus/keys/private_2026-q1.pem -pkeyopt rsa_keygen_bits:2048
openssl rsa -in /etc/prometheus/keys/private_2026-q1.pem -pubout -out /etc/prometheus/keys/public_2026-q1.pem
sudo chmod 644 /etc/prometheus/keys/public_2026-q1.pem
sudo chmod 600 /etc/prometheus/keys/private_2026-q1.pem
sudo chcon -t container_file_t /etc/prometheus/keys/*.pem

# Self-signed TLS certificate for development (use a real cert in production)
openssl req -x509 -newkey rsa:2048 -nodes \
  -keyout /etc/prometheus/certs/dev.key -out /etc/prometheus/certs/dev.crt \
  -days 365 -subj "/CN=prometheus.local"
sudo chmod 644 /etc/prometheus/certs/dev.crt
sudo chmod 600 /etc/prometheus/certs/dev.key
sudo chown root:root /etc/prometheus/certs/*
sudo restorecon -Rv /etc/prometheus/certs || true
```

> Private keys must never be committed to the repository. For production use a dedicated secret manager or HSM.

#### Host directories

UIDs used inside container images (from `AGENTS.md`): `prometheus`=1000 (gateway, observability), `pmgr`=1001 (manager), `auth`=1002 (auth-service).

```bash
# Auth DB parent dir — the container creates auth.db on first run; do NOT pre-create the file
sudo mkdir -p /var/lib/prometheus/auth-service
sudo chown -R 1002:1002 /var/lib/prometheus/auth-service
sudo chmod -R u+rwX,g+rwX /var/lib/prometheus/auth-service
sudo chcon -Rt container_file_t /var/lib/prometheus/auth-service

# Model storage
sudo mkdir -p /srv/prometheus/models
sudo chown -R llmops:llmops /srv/prometheus/models
sudo chmod -R 750 /srv/prometheus/models
sudo chcon -Rt container_file_t /srv/prometheus/models || true

# Log directories (one subfolder per service)
sudo mkdir -p /var/log/prometheus/{gateway,auth-service,manager,runtime,observability}
sudo chown 1000:1000 /var/log/prometheus/gateway
sudo chown 1002:1002 /var/log/prometheus/auth-service
sudo chown 1001:1001 /var/log/prometheus/manager
sudo chown llmops:llmops /var/log/prometheus/runtime
sudo chown 1000:1000 /var/log/prometheus/observability
sudo chmod 750 /var/log/prometheus /var/log/prometheus/*

# Manager runtime dirs (PID files + log output)
sudo mkdir -p /var/log/prometheus/runtime/logs /var/run/prometheus/runtime/run
sudo chown -R llmops:llmops /var/log/prometheus/runtime /var/run/prometheus/runtime
sudo chmod -R u+rwX,g+rwX,o-rwx /var/log/prometheus/runtime
sudo chmod -R u+rwx,g+rx,o-rwx /var/run/prometheus/runtime
sudo chcon -Rt container_file_t /var/log/prometheus /var/run/prometheus || true

# Observability config files and Grafana provisioning
sudo chmod 644 /opt/prometheus-ai-inference/observability/loki/loki-config.yaml
sudo chmod 644 /opt/prometheus-ai-inference/observability/tempo/tempo-config.yaml
sudo chcon -t container_file_t \
  /opt/prometheus-ai-inference/observability/loki/loki-config.yaml \
  /opt/prometheus-ai-inference/observability/tempo/tempo-config.yaml
sudo chmod -R a+rX /opt/prometheus-ai-inference/observability/grafana/provisioning
sudo chcon -Rt container_file_t /opt/prometheus-ai-inference/observability/grafana/provisioning

# Service scripts must be executable
sudo find gateway auth-service runtime -type f -name '*.sh' -exec chmod 0755 {} +
sudo chcon -t container_file_t gateway/*/*.sh auth-service/*/*.sh || true
```

Create `/etc/logrotate.d/prometheus` to prevent log files from filling the disk (logrotate runs daily via cron/systemd-timer on RHEL):

```text
/var/log/prometheus/*/*.log {
  daily
  rotate 14
  compress
  missingok
  copytruncate
}
```

---

## Starting the stack

### Step 1 — Start llama-server instances

**Single model (dev)**
```bash
source runtime/mac-llama3-1b.env
bash runtime/scripts/start-server.sh
```

**All models (production)**
```bash
bash runtime/scripts/start-all-servers.sh
# Reads all .env files in runtime/envs/, launches one llama-server per file,
# writes backend_url into registry.yaml for each instance.
```

Verify:
```bash
curl http://127.0.0.1:8080/health
# → {"status":"ok"}
```

### Step 2 — Start Manager

```bash
# Copy and edit config once (skip if manager.toml already exists)
cp runtime/manager/manager.toml.example runtime/manager/manager.toml

# Start API only (no TUI)
pmgr serve
# Binds to 0.0.0.0:8090 — reachable from Podman containers via host.containers.internal:8090

# Or start with TUI (includes the REST API)
pmgr tui
```

> **`PROMETHEUS_LLAMA_BIND_HOST` and `PMGR_PROXY_HOST` must be consistent.** See the root `.env` comments for details. Quick reference:
>
> | Scenario | `PROMETHEUS_LLAMA_BIND_HOST` | `PMGR_PROXY_HOST` |
> |----------|------------------------------|-------------------|
> | Bare-metal TUI (default, most secure) | `127.0.0.1` | `""` (empty) |
> | Manager running inside Podman | `0.0.0.0` | `host.containers.internal` |
>
> Quick check before starting:
> ```bash
> grep -E '^PROMETHEUS_LLAMA_BIND_HOST|^PMGR_PROXY_HOST' .env
> ```

### Step 3 — Start Podman containers

```bash
podman machine start   # macOS only
podman system connection default podman-machine-default-root

podman compose -f podman-compose.yml up --build -d
```

> **No private registry** — images are built directly on each host from source. There is no push/pull step.

> **Observability stack** (Grafana + Loki + Promtail + Tempo) is included in `podman-compose.yml` and starts with the same command. It is optional — the platform services work without it. Access Grafana at `http://localhost:3000`.

Verify:
```bash
curl http://localhost:8000/health
# → {"status":"ok"}
```

---

## Stopping the stack

```bash
# Stop containers
podman compose -f podman-compose.yml down

# Stop llama-server instances
pmgr stop --all
# or individually:
pmgr stop llama3-1b-q4-local
```

---

## Updating the stack — Idempotent deployment

After each feature release, operators can use the `--deploy` flag to pull code changes and restart services without re-running slow installation steps.

### Quick start

```bash
cd /opt/prometheus-ai-inference
bash scripts/install-rhel.sh --deploy
```

**What `--deploy` does:**
1. Detects and cleans any uncommitted changes in the project directory
2. Pulls the latest code (`git pull --ff-only`)
3. Syncs Python dependencies only if `uv.lock` has changed (cached otherwise)
4. Rebuilds and restarts Podman containers
5. Restarts the Manager API and llama-server processes
6. Records the deployment state for idempotency tracking

**What `--deploy` skips** (already installed):
- System packages (`dnf install`)
- llama.cpp build from source
- JWT keypair generation
- TLS certificate generation

### Idempotency

`--deploy` is safe to run repeatedly. The script uses a state file (`.deploy-state`) to track:
- Last successful deployment commit
- Last successful uv sync timestamp
- SHA256 hash of `uv.lock` to detect changes

If nothing has changed since the last deploy, re-running `--deploy` only pulls metadata and restarts services (fast path ~10 seconds).

### With uncommitted changes

If the server working tree contains uncommitted files (untracked or modified), `--deploy` will:
1. Create a timestamped backup archive: `prometheus-backup-YYYYMMDD-HHMMSS.tar.gz`
2. Clean the tree (`git clean -fd && git checkout -- .`)
3. Pull the latest code
4. Proceed with deployment

**Inspect the backup** before discarding it:
```bash
ls -lh ~/prometheus-backup-*.tar.gz
tar -tzf ~/prometheus-backup-YYYYMMDD-HHMMSS.tar.gz | head -20
```

### Forcing a full redeploy

```bash
bash scripts/install-rhel.sh --deploy --force
```

`--force` always runs `uv sync`, even if `uv.lock` hasn't changed. Use this to pick up build-environment changes or recover from corrupted state files.

### Validating the deployment

After `--deploy` completes, run:
```bash
bash scripts/validate.sh
```

This checks:
- Services are healthy (gateway, auth-service, Manager API)
- llama-server backends are available
- Authentication and basic inference work
- No critical configuration mismatches

---

## Reference

### Environment files

| File | Purpose |
|------|---------|
| `runtime/mac-llama3-1b.env` | macOS Metal — Llama 3.2 1B (dev default) |
| `runtime/mac-llama3-8b.env` | macOS Metal — Llama 3 8B |
| `runtime/qwen-coder-7b.env` | Qwen Coder 7B |
| `.env.redhat.example` | **RHEL 9.7** template — `host.containers.internal`, GPU layers=0, threads=32, RHEL CA bundle |
| `runtime/manager/manager.toml` | Manager config (binary, ports, `proxy_host`, registry, downloads CA bundle) |
| `.env` (root) | Compose bind-mount paths + secrets (see [step 6](#6-configure-env-files)) |
| `gateway/.env` | JWT issuer · llama.cpp URL · Redis URL (see [step 6](#6-configure-env-files)) |
| `auth-service/.env` | RSA key paths · JWT issuer · admin key · TLS (see [step 6](#6-configure-env-files)) |

### Container security defaults

| Constraint | Value |
|------------|-------|
| gateway user | `prometheus` (uid 1000) |
| manager user | `pmgr` (uid 1001) |
| auth-service user | `auth` (uid 1002) |
| Privileged mode | Never (`--privileged` forbidden) |
| Redis host port | Not exposed (internal Podman network only) |
| Secrets in image layers | Never — mounted at runtime via env file or bind-mount |

### Manager runtime defaults

Defaults declared in `manager.toml` and `config.py`:

- `log_dir`: `runtime/logs`
- `pid_dir`: `runtime/run`
- `log_file_path`: `runtime/logs/manager.log`

For production on RHEL, override via root `.env`:

```
MANAGER_LOG_HOST_PATH=/var/log/prometheus/manager
```

`podman-compose.yml` uses `${MANAGER_LOG_HOST_PATH:-/var/log/prometheus/runtime/logs}` so the variable takes effect without code changes.

### RHEL 9.7 — auto-start on boot

```bash
# Enable linger so the user service starts at boot without login
loginctl enable-linger <deploy-user>

# Generate and install systemd unit (one-time setup)
podman generate systemd --new --name prometheus-gateway-gateway-1 \
  > ~/.config/systemd/user/prometheus-gateway.service
systemctl --user daemon-reload
systemctl --user enable --now prometheus-gateway.service
```

A pre-generated unit file is in `runtime/systemd/` for reference.

---

## Podman VM (macOS only)

```bash
podman machine start
podman system connection default podman-machine-default-root
podman ps
```

### Corporate TLS CA injection (Zscaler or similar)

Required on every new Podman VM before `podman build` can pull packages:

```bash
ssh -i ~/.local/share/containers/podman/machine/machine \
    -p <VM_PORT> -o StrictHostKeyChecking=no root@127.0.0.1

# Inside VM:
cat > /etc/pki/ca-trust/extracted/pem/tls-ca-bundle.pem << 'CERT'
<paste PEM here>
CERT
update-ca-trust && exit
```

CA issuer: `CN=Global TLS Interception Issuing CA America`.

### Gateway TLS termination (Web Chat UI)

Required when `UI_ENABLED=true`. The gateway starts uvicorn with TLS when both variables are set:

| Variable | Description |
|----------|-------------|
| `GATEWAY_TLS_CERT_FILE` | Path to PEM certificate file |
| `GATEWAY_TLS_KEY_FILE` | Path to PEM private key file |

Setting only one of the two causes the gateway to refuse startup with a configuration error.

The `prometheus_session` cookie always has the `Secure` flag. Without HTTPS the browser silently drops it — making login impossible. This is an intentional fail-safe, not a bug.

**Dev self-signed cert** (one-time setup per machine):
```bash
bash gateway/certs/gen-dev-cert.sh       # → gateway/certs/dev.crt + dev.key
bash auth-service/certs/gen-dev-cert.sh  # → auth-service/certs/dev.crt + dev.key
# Add each dev.crt to the OS/browser trust store once
```

**Auth-service port binding**: defaults to `127.0.0.1:9000`. The gateway reaches it via the internal Podman network. To expose the admin dashboard from another machine during bootstrap, set `AUTH_BIND_HOST=0.0.0.0` in `auth-service/.env` — SSH port-forwarding is the recommended alternative.

---

## TLS for downloads (HuggingFace)

The Manager's streaming downloader uses `requests` to pull GGUF files. On hosts behind a corporate proxy (e.g. Zscaler), the TLS chain includes an interception certificate that must be trusted.

**Priority order (how the Manager resolves the CA bundle):**
1. `[downloads] ca_bundle = "/path/to/bundle.pem"` in `manager.toml` → passed as `requests.get(verify=<path>)`
2. `REQUESTS_CA_BUNDLE` environment variable → respected natively by `requests`
3. `truststore` package installed → injects OS-native keychain at startup
4. None of the above → `certifi` bundle (default `requests` behaviour — fails behind Zscaler)

**`verify=False` is forbidden** — configure the correct bundle instead.

```toml
# manager.toml — uncomment on RHEL/Zscaler hosts
[downloads]
dir = "runtime/models"
hf_token_env = "HF_TOKEN"
# ca_bundle = "/etc/pki/ca-trust/extracted/pem/tls-ca-bundle.pem"
```

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|-------------|-----|
| `IsADirectoryError` on container start | Bind-mount path missing from root `.env` | Add the missing `*_HOST_PATH` variable |
| Instance shows `error` in TUI Instances view | `PROMETHEUS_LLAMA_BIND_HOST=0.0.0.0` — scanner was probing an unroutable address | Fixed in scanner; verify `PMGR_PROXY_HOST` is consistent (see [Step 2](#step-2--start-manager)) |
| `503 model-not-loaded` from gateway | llama-server not running or `discovery:false` | `pmgr start <model-id>` or toggle discovery in TUI |
| `502 backend-unavailable` | llama-server crashed | `pmgr restart <model-id>`, check `/var/log/prometheus/runtime/logs/` |
| Instance visible in `pmgr list` but not started by manager | Orphan process (started manually) | Manager detects it via `psutil` — appears in Instances view as "orphan"; use `pmgr deregister` to clean up |
| Gateway `401 token-expired` immediately | System clock skew | Sync NTP; check `iat`/`exp` diff |
| Podman build fails with TLS error | Corporate CA not injected into Podman VM | See [Corporate TLS CA injection](#corporate-tls-ca-injection-zscaler-or-similar) |
| HuggingFace download fails with SSL error | Corporate CA not trusted by Python/requests | See [TLS for downloads](#tls-for-downloads-huggingface) |
| Auth-service admin dashboard not reachable from another host | Port bound to `127.0.0.1` by design | Set `AUTH_BIND_HOST=0.0.0.0` in `auth-service/.env` or use SSH port-forward: `ssh -L 9000:localhost:9000 <host>` |

---

## Related

- `memory/specs/003-llama-cpp-runtime.md` — build and runtime details
- `memory/specs/004-podman-containerization.md` — Dockerfile and compose design
- [inference-server-startup.md](inference-server-startup.md) — detailed llama-server runbook
- [model-registry.md](model-registry.md) — registry.yaml schema and model routing
- `memory/decisions/2026-03-28-llama-cpp-bare-metal.md` — why llama.cpp stays on bare-metal
- `memory/decisions/2026-03-28-podman-over-docker.md` — why Podman instead of Docker
