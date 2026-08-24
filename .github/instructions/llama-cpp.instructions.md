---
description: "Use when working with llama.cpp: bare-metal setup, server API integration, model management, quantization, GPU/CPU configuration, prompt formatting, or runtime scripts."
applyTo: "runtime/**"
---

# llama.cpp — Integration Guidelines

## Architecture Constraint

llama.cpp runs as a **bare-metal HTTP server** on the host — never containerized.
The Gateway container reaches it via the host network (e.g., `http://host-gateway:8080`).

```
Podman network (bridge)
  └─ gateway container ──► host.containers.internal:8080 ──► llama.cpp (host process)
```

## llama.cpp Server API

Relevant endpoints (llama.cpp v0.2+):

| Endpoint | Purpose |
|----------|---------|
| `POST /v1/chat/completions` | OpenAI-compatible chat (preferred) |
| `POST /completion` | Raw completion |
| `GET /health` | Health check |
| `GET /v1/models` | List loaded models |
| `POST /tokenize` | Count tokens for metering |

Always prefer `/v1/chat/completions` — it's OpenAI-compatible and easier to test.

## Prompt Format

Different models use different prompt templates. The runtime scripts must apply the correct template:

| Model Family | Template |
|--------------|----------|
| Llama 3.x | `<|begin_of_text|><|start_header_id|>system<|end_header_id|>...` |
| Mistral / Mixtral | `[INST] ... [/INST]` |
| Phi-3 | `<|system|>...<|end|><|user|>...<|end|><|assistant|>` |
| Gemma 2 | `<start_of_turn>user\n...<end_of_turn>\n<start_of_turn>model` |

Do **not** use `--chat-template` — the peg-native parser reads the template from GGUF metadata automatically. Forcing any value causes only 4 header tokens to be evaluated, producing hallucinated output.

## Server Startup Script Pattern

```bash
#!/usr/bin/env bash
# runtime/scripts/start-server.sh
set -euo pipefail

MODEL_PATH="${PROMETHEUS_MODEL_PATH:?MODEL_PATH required}"
CTX_SIZE="${PROMETHEUS_CTX_SIZE:-4096}"
GPU_LAYERS="${PROMETHEUS_GPU_LAYERS:-0}"   # 0 = CPU only; -1 = all layers on GPU
THREADS="${PROMETHEUS_THREADS:-$(nproc)}"
PORT="${PROMETHEUS_LLAMA_PORT:-8080}"

exec llama-server \
  --model "$MODEL_PATH" \
  --alias "$MODEL_ALIAS" \
  --ctx-size "$CTX_SIZE" \
  --n-gpu-layers "$GPU_LAYERS" \
  --threads "$THREADS" \
  --host 127.0.0.1 \              # NEVER bind to 0.0.0.0
  --port "$PORT"
```

**Security**: Always bind to `127.0.0.1`, never `0.0.0.0`. The Gateway is the only authorised caller.

## Model Management

- Model weights live outside the repo (large files). Store paths in `runtime/models/registry.yaml`.
- `registry.yaml` format:
  ```yaml
  models:
    - id: "llama3-8b-q4"
      path: "/srv/models/Meta-Llama-3-8B-Instruct.Q4_K_M.gguf"
      context_length: 8192
      family: llama3
      quantization: Q4_K_M
  ```

## Token Counting

Always pre-count tokens for rate limiting BEFORE forwarding:
```python
async def count_tokens(text: str) -> int:
    resp = await llama_client.post("/tokenize", json={"content": text})
    return resp.json()["tokens"].__len__()
```

## Recommended Quantizations for SLM (Low-Resource)

| VRAM / RAM | Recommended | Notes |
|------------|-------------|-------|
| 4 GB | Q4_K_M (7B) | Best quality/size trade-off |
| 8 GB | Q5_K_M (7B) or Q4_K_M (13B) | |
| 16 GB | Q6_K (13B) or Q4_K_M (34B) | |
| CPU only | Q4_0 or IQ3_M | Fastest inference on CPU |
