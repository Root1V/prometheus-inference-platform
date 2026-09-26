---
id: "003"
title: "llama.cpp Bare-Metal Runtime Setup"
status: closed
current-agent: ""
pipeline-log: []
created: 2026-03-28
updated: 2026-03-28
---

# 003 — llama.cpp Bare-Metal Runtime Setup

## Problem Statement

The Prometheus gateway proxies inference requests to a llama.cpp HTTP server, but there is no
documented, repeatable procedure for installing, compiling, and running that server on the two
target environments used by the project (Mac M2 development machine and HPE DL380 RHEL 9.7 test
servers). Without standardised build instructions and startup scripts, every engineer must
re-derive the setup steps, risking inconsistent configurations and security regressions (e.g.,
accidentally binding llama.cpp to all interfaces).

## Goals

- [ ] Document build prerequisites for macOS (Apple Silicon / Metal) and RHEL 9.7 (CPU / OpenBLAS)
- [ ] Provide `runtime/scripts/install-server.sh` — builds and installs `llama-server` from source (one-command setup)
- [ ] Provide `runtime/scripts/start-server.sh` — environment-parametrized, works on both platforms
- [ ] Provide `runtime/scripts/download-model.sh` — downloads a GGUF file from HuggingFace
- [ ] Document how to update `runtime/models/registry.yaml` with correct local model paths
- [ ] Document how to verify the server is running and reachable from the gateway
- [ ] Enforce that llama.cpp always binds to `127.0.0.1` only

## Non-Goals

- Docker / container image for llama.cpp — it must always run bare-metal on the host
- Model fine-tuning or training
- Multi-model serving (serving more than one model concurrently per server instance)
- GPU setup for RHEL servers (they have no GPU)
- Automated provisioning / Ansible / Terraform

## Proposed Solution

Two shell scripts in `runtime/scripts/` cover the full operational lifecycle:

1. `download-model.sh` — one-time download of a GGUF from HuggingFace to a local path.
2. `start-server.sh` — starts `llama-server` with the correct flags for the current host,
   controlled entirely by environment variables so it is identical across both platforms.
   The only platform difference is the compile-time choice of GPU backend.

### Target Environments

| Property | Mac M2 (dev) | HPE DL380 (test) |
|----------|-------------|-----------------|
| OS | macOS (Apple Silicon) | Red Hat Enterprise Linux 9.7 |
| CPU | Apple M2 (8-core) | 2 × 8-core Intel/AMD (16 cores/server) |
| RAM | 16 GB | 256 GB |
| GPU | Apple Metal (unified memory) | None |
| llama.cpp backend | Metal (`GGML_METAL=ON`) | OpenBLAS (`GGML_BLAS=ON`) |
| Typical workload | 7–8B models at Q4_K_M | 70B+ models at Q4_K_M |
| Purpose | Developer E2E testing | Integration / performance testing |

### Build Procedure — macOS (Metal)

Prerequisites (installed via Homebrew):
```bash
brew install cmake git llvm
```

Build:
```bash
git clone https://github.com/ggerganov/llama.cpp.git
cd llama.cpp
cmake -B build \
  -DGGML_METAL=ON \
  -DCMAKE_BUILD_TYPE=Release
cmake --build build --config Release -j$(sysctl -n hw.logicalcpu)
sudo cmake --install build --prefix /usr/local
```

### Build Procedure — RHEL 9.7 (OpenBLAS, CPU-only)

Prerequisites (DNF):
```bash
sudo dnf install -y git cmake gcc gcc-c++ make openblas-devel
```

Build:
```bash
git clone https://github.com/ggerganov/llama.cpp.git
cd llama.cpp
cmake -B build \
  -DGGML_BLAS=ON \
  -DGGML_BLAS_VENDOR=OpenBLAS \
  -DCMAKE_BUILD_TYPE=Release
cmake --build build --config Release -j$(nproc)
sudo cmake --install build --prefix /usr/local
```

### Script: `runtime/scripts/start-server.sh`

Parametrized via environment variables. Follows the existing project convention.

| Variable | Default | Description |
|----------|---------|-------------|
| `PROMETHEUS_MODEL_PATH` | *(required)* | Absolute path to the `.gguf` weights file |
| `PROMETHEUS_CTX_SIZE` | `4096` | Context window size in tokens |
| `PROMETHEUS_GPU_LAYERS` | `0` | Layers offloaded to GPU (`-1` = all; `0` = CPU-only) |
| `PROMETHEUS_THREADS` | `$(nproc)` | CPU threads for inference |
| `PROMETHEUS_LLAMA_PORT` | `8080` | Port the server listens on (localhost only) |

The script must use `set -euo pipefail` and fail fast if `PROMETHEUS_MODEL_PATH` is unset.
It must always pass `--host 127.0.0.1` and never `0.0.0.0`.

### Script: `runtime/scripts/download-model.sh`

Downloads a `.gguf` file from HuggingFace given:

| Variable | Description |
|----------|-------------|
| `PROMETHEUS_MODEL_URL` | Full HTTPS URL to the `.gguf` file on HuggingFace |
| `PROMETHEUS_MODEL_DEST` | Destination path on disk (directory must exist) |

Uses `curl` with `--fail --location --continue-at -` flags so partial downloads can be
resumed and the script fails loudly on HTTP errors.

### Registry Update

After downloading a model, the operator adds an entry to `runtime/models/registry.yaml`:

```yaml
models:
  - id: "llama3-70b-q4"
    path: "/srv/models/Meta-Llama-3-70B-Instruct.Q4_K_M.gguf"
    context_length: 8192
    family: llama3
    quantization: Q4_K_M
```

The `id` value must match what clients send in the `model` field of API requests.

### Verifying Server Connectivity from the Gateway

```bash
# From the bare-metal host
curl -s http://127.0.0.1:8080/health

# From inside the gateway Docker container
curl -s http://host.docker.internal:8080/health
```

Both must return HTTP 200 with `{"status":"ok"}`.

### Key Design Decisions

| Decision | Rationale |
|----------|-----------|
| Metal backend on Mac | Apple Silicon GPU shares unified memory; Metal gives full 16 GB |
| OpenBLAS on RHEL | CPU-only server; OpenBLAS multi-threading saturates 16 cores/server |
| Env-var parametrization | Same script works across both platforms without branching |
| `--log-format json` | Consistent with gateway's structured JSON logging policy |
| `set -euo pipefail` | Fail fast vs. silent misconfiguration |
| Bind to `127.0.0.1` | Only the Gateway container may call llama.cpp (via host networking) |

## API Contract

No new gateway API endpoints are introduced by this spec.
The llama.cpp server exposes `POST /v1/chat/completions`, `GET /health`, and `GET /v1/models`
natively — these are consumed by the gateway as specified in `memory/specs/001-gateway-core.md`.

## Data Model

`runtime/models/registry.yaml` schema (per entry):

```yaml
id:             string   # stable identifier, kebab-case
path:           string   # absolute path to .gguf on host filesystem
context_length: integer  # max tokens; must match the model's training context
family:         string   # llama3 | mistral | phi | qwen (selects prompt template)
quantization:   string   # Q4_K_M | Q5_K_M | Q8_0 | … (informational)
```

## Security Considerations

- **Network binding**: `llama-server` MUST be started with `--host 127.0.0.1`. Binding to
  `0.0.0.0` would expose the unauthenticated inference API to all network interfaces.
  The startup script enforces this; the AC verifies it at runtime.
- **No authentication on llama.cpp**: llama.cpp has no auth layer. The Gateway is the sole
  authorised caller. Firewall rules on RHEL servers must block external traffic to port 8080.
- **HuggingFace downloads over HTTPS only**: `download-model.sh` must use `https://` URLs.
  The script must reject `http://` URLs at the input validation step.
- **Model path validation**: `PROMETHEUS_MODEL_PATH` must point to a `.gguf` file.
  The startup script must verify the file exists before launching the server.
- **No secrets in scripts**: HuggingFace tokens (if required for gated models) must be passed
  via `HUGGINGFACE_TOKEN` environment variable, never hardcoded in the script.

## Acceptance Criteria

### Build — macOS

- [ ] **AC-1**: Given a Mac M2 with Homebrew installed, when the Metal build sequence is run
  (`cmake -DGGML_METAL=ON …` + `cmake --build`), then the build completes without errors and
  the `llama-server` binary is present at `/usr/local/bin/llama-server`.
  *Verify*: `llama-server --version` prints without error.

- [ ] **AC-2**: Given the Metal-compiled `llama-server`, when it is started with a Q4_K_M 8B
  model and `PROMETHEUS_GPU_LAYERS=-1`, then `GET /health` returns HTTP 200 and `nvidia-smi`
  (or `system_profiler SPDisplaysDataType`) shows GPU utilisation > 0 during inference.
  *Verify*: `curl -s http://127.0.0.1:8080/health` → `{"status":"ok"}`.

### Build — RHEL 9.7

- [ ] **AC-3**: Given a RHEL 9.7 host with `openblas-devel` installed, when the OpenBLAS build
  sequence is run (`cmake -DGGML_BLAS=ON -DGGML_BLAS_VENDOR=OpenBLAS …` + `cmake --build`),
  then the build completes without errors and `llama-server` is present at
  `/usr/local/bin/llama-server`.
  *Verify*: `llama-server --version` prints without error.

- [ ] **AC-4**: Given the OpenBLAS-compiled `llama-server` on RHEL, when it is started with a
  Q4_K_M 70B model and `PROMETHEUS_GPU_LAYERS=0`, then `GET /health` returns HTTP 200 and
  CPU utilisation across all 16 cores rises during inference.
  *Verify*: `curl -s http://127.0.0.1:8080/health` → `{"status":"ok"}`.

### `start-server.sh`

- [ ] **AC-5**: Given `PROMETHEUS_MODEL_PATH` is set to a valid `.gguf` file, when
  `start-server.sh` is executed, then `llama-server` starts, binds to `127.0.0.1:8080`,
  and `GET http://127.0.0.1:8080/health` returns HTTP 200 within 60 seconds.
  *Verify*: `curl --retry 6 --retry-delay 10 -s http://127.0.0.1:8080/health`.

- [ ] **AC-6**: Given `PROMETHEUS_MODEL_PATH` is NOT set in the environment, when
  `start-server.sh` is run, then the script exits with a non-zero status code and prints an
  error message referencing `PROMETHEUS_MODEL_PATH` before launching any process.
  *Verify*: `PROMETHEUS_MODEL_PATH= bash start-server.sh; echo "exit=$?"` → `exit=1` (or non-zero).

- [ ] **AC-7**: Given `PROMETHEUS_MODEL_PATH` points to a path that does not exist on disk,
  when `start-server.sh` is run, then the script exits with a non-zero status code and a
  `file not found` error before launching `llama-server`.
  *Verify*: `PROMETHEUS_MODEL_PATH=/nonexistent.gguf bash start-server.sh; echo "exit=$?"`.

### `download-model.sh`

- [ ] **AC-8**: Given `PROMETHEUS_MODEL_URL` is a valid HTTPS HuggingFace URL and
  `PROMETHEUS_MODEL_DEST` is a writable path, when `download-model.sh` is executed, then
  the `.gguf` file is present at `$PROMETHEUS_MODEL_DEST` after the script exits 0.
  *Verify*: `ls -lh "$PROMETHEUS_MODEL_DEST"` shows the file at expected size.

- [ ] **AC-9**: Given `PROMETHEUS_MODEL_URL` is an `http://` (non-TLS) URL, when
  `download-model.sh` is run, then the script exits non-zero with an error message requiring
  HTTPS before any network request is made.
  *Verify*: `PROMETHEUS_MODEL_URL=http://example.com/model.gguf bash download-model.sh; echo "exit=$?"`.

- [ ] **AC-10**: Given `PROMETHEUS_MODEL_URL` is unreachable (e.g., 404), when
  `download-model.sh` is run, then the script exits non-zero and no partial file is left at
  `$PROMETHEUS_MODEL_DEST`.
  *Verify*: run with an invalid URL; confirm `$PROMETHEUS_MODEL_DEST` does not exist afterwards.

### Registry Update

- [ ] **AC-11**: Given a new model entry is added to `runtime/models/registry.yaml` following
  the documented schema, when the YAML is validated (`python -c "import yaml; yaml.safe_load(open('runtime/models/registry.yaml'))"`),
  then it parses without error and all required fields (`id`, `path`, `context_length`,
  `family`, `quantization`) are present.
  *Verify*: `python -c "import yaml; ..."` exits 0.

### Gateway Connectivity

- [ ] **AC-12**: Given `llama-server` is running on the bare-metal host and the gateway
  container is up, when the gateway container executes `curl -s http://host.docker.internal:8080/health`,
  then it receives HTTP 200 with body `{"status":"ok"}`.
  *Verify*: `docker exec prometheus-gateway curl -s http://host.docker.internal:8080/health`.

- [ ] **AC-13**: Given `llama-server` is running with a model listed in `registry.yaml`, when
  `POST /v1/chat/completions` is sent through the gateway with `"model": "<registered-id>"`,
  then the gateway returns HTTP 200 and a valid OpenAI-compatible response body.
  *Verify*: `curl -s -X POST http://localhost:8000/v1/chat/completions -H "Authorization: Bearer <token>" -d '{"model":"llama3-8b-q4","messages":[{"role":"user","content":"ping"}]}'`.

### `install-server.sh`

- [ ] **AC-16**: Given a macOS (Apple Silicon) or RHEL 9.7 host where prerequisites are already
  installed (Homebrew+cmake on Mac; dnf+openblas-devel on RHEL), when `install-server.sh` is
  executed, then the build completes without errors and `llama-server --version` exits 0 from
  `${INSTALL_PREFIX}/bin/llama-server`.
  *Verify*: `bash runtime/scripts/install-server.sh && llama-server --version`.

- [ ] **AC-17**: Given `install-server.sh` source code, when it is inspected, then it rejects
  any OS other than Darwin and Linux with a clear error, and it never uses `http://` for the
  repository clone URL.
  *Verify*: `bash -c 'OS=Windows bash runtime/scripts/install-server.sh'` exits non-zero;
  `grep -v "https://" runtime/scripts/install-server.sh | grep 'http://'` returns nothing.

### Security

- [ ] **AC-14**: Given `llama-server` is started via `start-server.sh`, when the listening
  sockets are inspected, then llama-server is bound ONLY to `127.0.0.1` and NOT to `0.0.0.0`
  or any external interface.
  *Verify (macOS)*: `lsof -iTCP:8080 -sTCP:LISTEN` shows only `127.0.0.1:8080`.
  *Verify (RHEL)*: `ss -tlnp | grep 8080` shows only `127.0.0.1:8080`.

- [ ] **AC-15**: Given `start-server.sh` source code, when it is inspected, then the
  `--host` flag passed to `llama-server` is hardcoded to `127.0.0.1` and there is no code
  path that passes `0.0.0.0` or omits the flag entirely.
  *Verify*: `grep -E '\-\-host' runtime/scripts/start-server.sh` outputs only `127.0.0.1`.

## Open Questions

- [x] **Q1**: Should `download-model.sh` support HuggingFace gated models requiring an access
  token?
  **Answer**: No — all models in use are public and require no authentication token.
  The script will use plain `curl` or `wget` without `Authorization` headers.

- [x] **Q2**: For the RHEL servers, should the models be stored at `/srv/models/`?
  **Answer**: Yes — `/srv/models/` confirmed as the storage path on both HPE servers.

- [x] **Q3**: Should `start-server.sh` auto-select the chat template?
  **Answer**: Yes — the script must detect the template from the model filename/family
  and pass `--chat-template <name>` automatically. Fallback: let llama.cpp auto-detect.

## Available Models (Mac M2 — GPT4All path)

All stored under `~/Library/Application Support/nomic.ai/GPT4All/`:

| File | Size | Family | Notes |
|------|------|--------|-------|
| `Meta-Llama-3-8B-Instruct.Q4_0.gguf` | 4.3 GB | llama3 | Primary test model |
| `Llama-3.2-1B-Instruct-Q4_0.gguf` | 737 MB | llama3 | Fastest, good for CI |
| `DeepSeek-R1-Distill-Llama-8B-Q4_0.gguf` | 4.4 GB | llama3 | Reasoning model |
| `mistral-7b-instruct-v0.1.Q4_0.gguf` | 3.8 GB | mistral | — |
| `mistral-7b-instruct-v0.2.Q4_0.gguf` | 3.8 GB | mistral | — |
| `Phi-3-mini-4k-instruct.Q4_0.gguf` | 2.0 GB | phi3 | Lightest capable model |
| `qwen2.5-coder-7b-instruct-q4_0.gguf` | 4.1 GB | qwen2.5 | Code-focused |
| `functionary-small-v2.4.Q4_0.gguf` | 3.8 GB | mistral | Function calling |
| `zephyr-7b-beta-pl.Q4_0.gguf` | 3.8 GB | mistral | — |

**Recommended for initial E2E testing**: `Phi-3-mini-4k-instruct.Q4_0.gguf` (2 GB, fast, fits comfortably in 16 GB RAM).

## References

- Related specs: [memory/specs/001-gateway-core.md](001-gateway-core.md)
- llama.cpp instructions: `.github/instructions/llama-cpp.instructions.md`
- llama.cpp releases: https://github.com/ggerganov/llama.cpp
- OpenBLAS: https://github.com/OpenMathLib/OpenBLAS
- HuggingFace GGUF hub: https://huggingface.co/models?library=gguf
