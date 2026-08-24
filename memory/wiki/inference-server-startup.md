# Runbook — Inference Server Startup

Starting and managing the `llama-server` process on bare-metal hosts.

**Applies to**: Mac M2 (dev) · HPE DL380 × 2 / RHEL 9.7 (test)
**Related spec**: [memory/specs/003-llama-cpp-runtime.md]../specs/003-llama-cpp-runtime.md)

---

## Prerequisites

`llama-server` must be installed on the host. If not:

```bash
# Install from source (builds Metal on macOS, OpenBLAS on RHEL)
bash runtime/scripts/install-server.sh

# Verify
llama-server --version
```

---

## Starting the Server

### Mac M2 — Metal backend (recommended for dev E2E testing)

Phi-3-mini (2 GB, fastest):

```bash
export PROMETHEUS_MODEL_PATH="~/Library/Application Support/nomic.ai/GPT4All/Phi-3-mini-4k-instruct.Q4_0.gguf"
export PROMETHEUS_GPU_LAYERS=-1      # all layers on Metal GPU
export PROMETHEUS_CTX_SIZE=4096
bash runtime/scripts/start-server.sh
```

Llama 3.2 1B (737 MB, ideal for CI):

```bash
export PROMETHEUS_MODEL_PATH="~/Library/Application Support/nomic.ai/GPT4All/Llama-3.2-1B-Instruct-Q4_0.gguf"
export PROMETHEUS_GPU_LAYERS=-1
bash runtime/scripts/start-server.sh
```

Llama 3 8B (4.3 GB, primary test model):

```bash
export PROMETHEUS_MODEL_PATH="~/Library/Application Support/nomic.ai/GPT4All/Meta-Llama-3-8B-Instruct.Q4_0.gguf"
export PROMETHEUS_GPU_LAYERS=-1
export PROMETHEUS_CTX_SIZE=8192
bash runtime/scripts/start-server.sh
```

### HPE DL380 — OpenBLAS (CPU-only)

```bash
sudo mkdir -p /srv/models

# Download model first (if not already present)
export PROMETHEUS_MODEL_URL="https://huggingface.co/bartowski/Meta-Llama-3-8B-Instruct-GGUF/resolve/main/Meta-Llama-3-8B-Instruct-Q4_K_M.gguf"
export PROMETHEUS_MODEL_DEST="/srv/models/llama3-8b-q4km.gguf"
bash runtime/scripts/download-model.sh

# Start server (CPU-only, 16 threads)
export PROMETHEUS_MODEL_PATH="/srv/models/llama3-8b-q4km.gguf"
export PROMETHEUS_GPU_LAYERS=0
export PROMETHEUS_THREADS=16
export PROMETHEUS_CTX_SIZE=8192
bash runtime/scripts/start-server.sh
```

---

## Configuration Reference

| Variable | Default | Description |
|----------|---------|-------------|
| `PROMETHEUS_MODEL_PATH` | *(required)* | Absolute path to the `.gguf` weights file |
| `PROMETHEUS_CTX_SIZE` | `4096` | Context window size in tokens |
| `PROMETHEUS_GPU_LAYERS` | `0` | Layers on GPU (`-1` = all; `0` = CPU-only) |
| `PROMETHEUS_THREADS` | `nproc` | CPU thread count |
| `PROMETHEUS_LLAMA_PORT` | `8080` | Listening port (always bound to `127.0.0.1`) |

---

## Verifying the Server is Ready

```bash
# From the host
curl -s http://127.0.0.1:8080/health
# Expected: {"status":"ok"}

# List loaded model
curl -s http://127.0.0.1:8080/v1/models | python3 -m json.tool

# Quick smoke test (no gateway, direct)
curl -s -X POST http://127.0.0.1:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"any","messages":[{"role":"user","content":"Reply with just: ok"}],"max_tokens":5}'
```

From inside the gateway Podman container:

```bash
podman exec prometheus-gateway curl -s http://host.containers.internal:8080/health
```

---

## Running as a Background Process

A `systemd` unit file will be added in a future spec. For now, use `nohup`:

```bash
nohup bash runtime/scripts/start-server.sh > /var/log/llama-server.log 2>&1 &
echo $! > /var/run/llama-server.pid
```

To stop:

```bash
kill $(cat /var/run/llama-server.pid)
```

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|-------------|-----|
| `ERROR: PROMETHEUS_MODEL_PATH is required` | Variable not exported | `export PROMETHEUS_MODEL_PATH=...` |
| `ERROR: model file not found` | Wrong path | `ls "$PROMETHEUS_MODEL_PATH"` to confirm |
| `curl: (7) Failed to connect` | Server still starting | Retry after ~10 s; large models take 20-30 s to load |
| `{"error":"model not loaded"}` | Model is loading | Wait for `"status":"ok"` from `/health` |
| Gateway returns `503 Backend Unavailable` | `llama-server` not running | Start the server first |
| Out-of-memory (macOS) | Model too large for 16 GB | Use Phi-3-mini (2 GB) or Llama 3.2 1B (737 MB) |
| Low throughput on RHEL | Hyperthreading sharing cores | Set `PROMETHEUS_THREADS=$(nproc --ignore=2)` |
