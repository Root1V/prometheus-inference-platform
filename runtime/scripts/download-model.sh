#!/usr/bin/env bash
# runtime/scripts/download-model.sh
# Implements: memory/specs/003-llama-cpp-runtime.md — AC-8, AC-9, AC-10
#
# Downloads a GGUF model file from HuggingFace (or any HTTPS source).
# Supports resume of partial downloads via curl --continue-at -.
#
# Usage:
#   export PROMETHEUS_MODEL_URL=https://huggingface.co/.../model.gguf
#   export PROMETHEUS_MODEL_DEST=/srv/models/model.gguf
#   ./runtime/scripts/download-model.sh

set -euo pipefail

# ── Validation ────────────────────────────────────────────────────────────────

# Require PROMETHEUS_MODEL_URL
if [[ -z "${PROMETHEUS_MODEL_URL:-}" ]]; then
    echo "ERROR: PROMETHEUS_MODEL_URL is required but not set." >&2
    echo "       Export the full HTTPS URL to the .gguf file:" >&2
    echo "       export PROMETHEUS_MODEL_URL=https://huggingface.co/.../model.gguf" >&2
    exit 1
fi

# Require PROMETHEUS_MODEL_DEST
if [[ -z "${PROMETHEUS_MODEL_DEST:-}" ]]; then
    echo "ERROR: PROMETHEUS_MODEL_DEST is required but not set." >&2
    echo "       Export the destination file path:" >&2
    echo "       export PROMETHEUS_MODEL_DEST=/srv/models/model.gguf" >&2
    exit 1
fi

# AC-9: reject non-HTTPS URLs before any network request
if [[ "${PROMETHEUS_MODEL_URL}" != https://* ]]; then
    echo "ERROR: PROMETHEUS_MODEL_URL must use HTTPS. Got: ${PROMETHEUS_MODEL_URL}" >&2
    echo "       Only HTTPS downloads are allowed for security reasons." >&2
    exit 1
fi

# Ensure destination directory exists
DEST_DIR="$(dirname "${PROMETHEUS_MODEL_DEST}")"
if [[ ! -d "${DEST_DIR}" ]]; then
    echo "ERROR: destination directory does not exist: ${DEST_DIR}" >&2
    echo "       Create it first: mkdir -p ${DEST_DIR}" >&2
    exit 1
fi

# ── Download ──────────────────────────────────────────────────────────────────

echo "Downloading model..."
echo "  URL  : ${PROMETHEUS_MODEL_URL}"
echo "  dest : ${PROMETHEUS_MODEL_DEST}"

# AC-8: download with resume support
# AC-10: --fail ensures non-zero exit on HTTP errors (404, 403, etc.)
#        A temp file is used so that a failed download leaves no partial file at DEST.
TMP_DEST="${PROMETHEUS_MODEL_DEST}.tmp"

cleanup() {
    # AC-10: remove partial temp file on failure
    if [[ -f "${TMP_DEST}" ]]; then
        rm -f "${TMP_DEST}"
        echo "Cleaned up incomplete download: ${TMP_DEST}" >&2
    fi
}
trap cleanup ERR INT TERM

curl \
    --fail \
    --location \
    --continue-at - \
    --output "${TMP_DEST}" \
    --progress-bar \
    "${PROMETHEUS_MODEL_URL}"

# Atomically move to final destination only on success
mv "${TMP_DEST}" "${PROMETHEUS_MODEL_DEST}"

echo "Download complete: ${PROMETHEUS_MODEL_DEST}"
ls -lh "${PROMETHEUS_MODEL_DEST}"
