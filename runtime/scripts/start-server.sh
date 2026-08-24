#!/usr/bin/env bash
# runtime/scripts/start-server.sh
# Implements: memory/specs/003-llama-cpp-runtime.md — AC-5, AC-6, AC-7, AC-14, AC-15
#
# Starts llama-server with the correct flags for the current host platform.
# Controlled entirely by environment variables — identical across macOS (Metal)
# and RHEL 9.7 (OpenBLAS / CPU-only).
#
# Quick start (macOS, Llama 3.2 1B):
#   export PROMETHEUS_MODEL_PATH="/Users/$USER/Library/Application Support/nomic.ai/GPT4All/Llama-3.2-1B-Instruct-Q4_0.gguf"
#   export PROMETHEUS_MODEL_ALIAS=llama3-1b-local
#   export PROMETHEUS_GPU_LAYERS=-1
#   bash runtime/scripts/start-server.sh
#
# Or source the env file first:
#   source runtime/mac-llama3-1b.env.example   # then edit the copy
#   bash runtime/scripts/start-server.sh
#
# Env vars:
#   PROMETHEUS_MODEL_PATH    (required) Absolute path to .gguf weights file
#   PROMETHEUS_MODEL_ALIAS   Model ID exposed via /v1/models (default: inferred from filename)
#   PROMETHEUS_CTX_SIZE      Context window in tokens (default: 4096)
#   PROMETHEUS_GPU_LAYERS    -1 = all layers on GPU, 0 = CPU-only (default: -1 on macOS, 0 on Linux)
#   PROMETHEUS_THREADS       CPU threads (default: all logical cores)
#   PROMETHEUS_LLAMA_PORT    HTTP port (default: 8080)

set -euo pipefail

# ── llama-server binary resolver ──────────────────────────────────────────────
# Checks in priority order:
#   1. System PATH (e.g. /usr/local/bin after a system install)
#   2. ~/.local/bin (default install prefix for install-server.sh)
_llama_server() {
    if command -v llama-server &>/dev/null; then
        exec llama-server "$@"
    elif [[ -x "${HOME}/.local/bin/llama-server" ]]; then
        exec "${HOME}/.local/bin/llama-server" "$@"
    else
        echo "ERROR: llama-server not found." >&2
        echo "       Run: bash runtime/scripts/install-server.sh" >&2
        exit 1
    fi
}

# ── Validation ────────────────────────────────────────────────────────────────

# AC-6: fail fast if PROMETHEUS_MODEL_PATH is unset or empty
if [[ -z "${PROMETHEUS_MODEL_PATH:-}" ]]; then
    echo "ERROR: PROMETHEUS_MODEL_PATH is required but not set." >&2
    echo "       Export it before running this script, or source an env file:" >&2
    echo "         source runtime/env/mac-llama3-1b.env" >&2
    echo "         bash runtime/scripts/start-server.sh" >&2
    exit 1
fi

# AC-7: fail if the model file does not exist on disk
if [[ ! -f "${PROMETHEUS_MODEL_PATH}" ]]; then
    echo "ERROR: model file not found: ${PROMETHEUS_MODEL_PATH}" >&2
    exit 1
fi

# ── Configuration ─────────────────────────────────────────────────────────────

MODEL_PATH="${PROMETHEUS_MODEL_PATH}"
CTX_SIZE="${PROMETHEUS_CTX_SIZE:-4096}"
THREADS="${PROMETHEUS_THREADS:-$(nproc 2>/dev/null || sysctl -n hw.logicalcpu)}"
PORT="${PROMETHEUS_LLAMA_PORT:-8080}"

# Default GPU layers: all on GPU for macOS Metal, CPU-only for Linux
if [[ -z "${PROMETHEUS_GPU_LAYERS:-}" ]]; then
    if [[ "$(uname -s)" == "Darwin" ]]; then
        GPU_LAYERS=-1   # Metal: all layers on GPU
    else
        GPU_LAYERS=0    # RHEL / OpenBLAS: CPU-only
    fi
else
    GPU_LAYERS="${PROMETHEUS_GPU_LAYERS}"
fi

# ── Model alias (exposed as model ID in /v1/models and /v1/chat/completions) ──
# If not set, infer a clean alias from the filename.
# This alias must match the "id" field in runtime/models/registry.yaml.
MODEL_BASENAME="$(basename "${MODEL_PATH}" .gguf | tr '[:upper:]' '[:lower:]')"
if [[ -n "${PROMETHEUS_MODEL_ALIAS:-}" ]]; then
    MODEL_ALIAS="${PROMETHEUS_MODEL_ALIAS}"
else
    # Strip quantization suffixes for a cleaner alias
    MODEL_ALIAS="$(echo "${MODEL_BASENAME}" | sed 's/[._-][qQ][0-9].*//')"
fi

# ── Chat template ─────────────────────────────────────────────────────────────
# Implements: memory/specs/003-llama-cpp-runtime.md — Q3 answer
#
# llama.cpp's peg-native parser reads the chat_template key embedded in the
# GGUF file and applies it automatically.  Forcing --chat-template breaks this:
# the parser evaluates only the 4-token header prefix instead of the full user
# message, producing hallucinated output regardless of model size.
#
# Do NOT add --chat-template here.  If a model's metadata is genuinely missing,
# re-pack the GGUF or open a runtime spec to add explicit support.

# ── Startup ───────────────────────────────────────────────────────────────────

echo "Starting llama-server..."
echo "  model      : ${MODEL_PATH}"
echo "  alias      : ${MODEL_ALIAS}"
echo "  ctx-size   : ${CTX_SIZE}"
echo "  gpu-layers : ${GPU_LAYERS}"
echo "  threads    : ${THREADS}"
echo "  port       : ${PORT}"
echo "  template   : auto (from GGUF metadata)"
echo ""

# AC-14, AC-15: --host is ALWAYS hardcoded to 127.0.0.1 — never 0.0.0.0
_llama_server \
    --model         "${MODEL_PATH}" \
    --alias         "${MODEL_ALIAS}" \
    --ctx-size      "${CTX_SIZE}" \
    --n-gpu-layers  "${GPU_LAYERS}" \
    --threads       "${THREADS}" \
    --host          127.0.0.1 \
    --port          "${PORT}" \
    --metrics
