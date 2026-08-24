#!/usr/bin/env bash
# runtime/tests/test_runtime_scripts.sh
# Implements: memory/specs/003-llama-cpp-runtime.md — AC-6, AC-7, AC-9, AC-10, AC-11, AC-15, AC-17
#
# Unit tests for runtime/scripts/start-server.sh and download-model.sh.
# Does NOT require llama-server to be installed (tests validation logic only).
#
# Usage:
#   bash runtime/tests/test_runtime_scripts.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
START_SERVER="${REPO_ROOT}/runtime/scripts/start-server.sh"
DOWNLOAD_MODEL="${REPO_ROOT}/runtime/scripts/download-model.sh"
INSTALL_SERVER="${REPO_ROOT}/runtime/scripts/install-server.sh"
REGISTRY="${REPO_ROOT}/runtime/models/registry.yaml"

PASS=0
FAIL=0

# ── Helpers ───────────────────────────────────────────────────────────────────

pass() { echo "  PASS: $1"; PASS=$((PASS + 1)); }
fail() { echo "  FAIL: $1"; FAIL=$((FAIL + 1)); }

run_test() {
    local name="$1"
    local expected_exit="$2"
    shift 2
    local actual_exit=0
    (set +e; "$@" >/dev/null 2>&1; exit $?) || actual_exit=$?
    if [[ "${actual_exit}" -eq "${expected_exit}" ]]; then
        pass "${name}"
    else
        fail "${name} (expected exit ${expected_exit}, got ${actual_exit})"
    fi
}

# ── start-server.sh tests ─────────────────────────────────────────────────────

echo ""
echo "=== start-server.sh ==="

# AC-6: missing PROMETHEUS_MODEL_PATH → exit non-zero
run_test "AC-6: missing PROMETHEUS_MODEL_PATH exits non-zero" 1 \
    env -i HOME="${HOME}" bash "${START_SERVER}"

# AC-6: empty PROMETHEUS_MODEL_PATH → exit non-zero
run_test "AC-6: empty PROMETHEUS_MODEL_PATH exits non-zero" 1 \
    env PROMETHEUS_MODEL_PATH="" bash "${START_SERVER}"

# AC-7: PROMETHEUS_MODEL_PATH set to non-existent file → exit non-zero
run_test "AC-7: non-existent model file exits non-zero" 1 \
    env PROMETHEUS_MODEL_PATH="/tmp/this-file-does-not-exist-$(date +%s).gguf" bash "${START_SERVER}"

# AC-15: --host flag in script is always 127.0.0.1
echo ""
echo "=== AC-15: bind address hardcoded in start-server.sh ==="
HOST_FLAG=$(grep -- '--host' "${START_SERVER}" | grep -v '^#' || true)
if echo "${HOST_FLAG}" | grep -q '127.0.0.1' && ! echo "${HOST_FLAG}" | grep -q '0.0.0.0'; then
    pass "AC-15: --host is hardcoded to 127.0.0.1, no 0.0.0.0 present"
else
    fail "AC-15: --host flag does not exclusively use 127.0.0.1"
    echo "       Found: ${HOST_FLAG}"
fi

# ── download-model.sh tests ───────────────────────────────────────────────────

echo ""
echo "=== download-model.sh ==="

# AC-9: http:// URL (non-TLS) → exit non-zero before any network call
run_test "AC-9: http:// URL rejected with non-zero exit" 1 \
    env PROMETHEUS_MODEL_URL="http://example.com/model.gguf" \
        PROMETHEUS_MODEL_DEST="/tmp/test-model.gguf" \
    bash "${DOWNLOAD_MODEL}"

# AC-9: missing PROMETHEUS_MODEL_URL → exit non-zero
run_test "AC-9: missing PROMETHEUS_MODEL_URL exits non-zero" 1 \
    env -i HOME="${HOME}" PROMETHEUS_MODEL_DEST="/tmp/model.gguf" bash "${DOWNLOAD_MODEL}"

# AC-10: unreachable URL → exit non-zero and no partial file left
# AC-10: unreachable URL → exit non-zero and no partial file left
TMP_DEST="/tmp/prometheus-test-$(date +%s).gguf"
# curl --fail returns 22 on HTTP 4xx/5xx — any non-zero exit is correct
actual_exit=0
(set +e; env PROMETHEUS_MODEL_URL="https://huggingface.co/this/repo/does/not/exist-$(date +%s).gguf" \
    PROMETHEUS_MODEL_DEST="${TMP_DEST}" \
    bash "${DOWNLOAD_MODEL}" >/dev/null 2>&1; exit $?) || actual_exit=$?
if [[ "${actual_exit}" -ne 0 ]]; then
    pass "AC-10: 404 URL exits non-zero (exit ${actual_exit})"
else
    fail "AC-10: expected non-zero exit on 404 URL, got 0"
fi

if [[ ! -f "${TMP_DEST}" ]]; then
    pass "AC-10: no partial file left after failed download"
else
    fail "AC-10: partial file found at ${TMP_DEST}"
    rm -f "${TMP_DEST}"
fi

# ── registry.yaml tests ───────────────────────────────────────────────────────

echo ""
echo "=== registry.yaml (AC-11) ==="

REQUIRED_FIELDS=("id" "path" "context_length" "family" "quantization")

# AC-11: YAML parses without error
if python3 -c "
import yaml, sys
data = yaml.safe_load(open('${REGISTRY}'))
models = data.get('models', [])
assert len(models) > 0, 'No models found'
missing = []
for m in models:
    for field in ['id', 'path', 'context_length', 'family', 'quantization']:
        if field not in m:
            missing.append(f\"{m.get('id','?')} missing {field}\")
if missing:
    for e in missing:
        print('  MISSING:', e, file=sys.stderr)
    sys.exit(1)
" 2>/dev/null; then
    pass "AC-11: registry.yaml is valid YAML with all required fields"
else
    fail "AC-11: registry.yaml is invalid or missing required fields"
fi

# ── Summary ───────────────────────────────────────────────────────────────────

echo ""
echo "Results: ${PASS} passed, ${FAIL} failed"
if [[ "${FAIL}" -gt 0 ]]; then
    exit 1
fi

# ── install-server.sh tests ───────────────────────────────────────────────────

echo ""
echo "=== install-server.sh (AC-17: static checks only, no actual build) ==="

# AC-17: unsupported OS exits non-zero
# We override uname inside the script by passing a fake OS string via env override.
# We test source-level: script must have an else/exit for unknown OS.
if grep -q 'Unsupported OS' "${INSTALL_SERVER}"; then
    pass "AC-17: install-server.sh has unsupported-OS guard"
else
    fail "AC-17: install-server.sh missing unsupported-OS guard"
fi

# AC-17: only HTTPS in repo clone URL — no http:// present
if grep -E 'http://[^/]' "${INSTALL_SERVER}" | grep -v '^#' | grep -q .; then
    fail "AC-17: install-server.sh contains a plain http:// URL"
else
    pass "AC-17: install-server.sh uses only HTTPS URLs"
fi

# ── Summary ───────────────────────────────────────────────────────────────────

echo ""
echo "Results: ${PASS} passed, ${FAIL} failed"
if [[ "${FAIL}" -gt 0 ]]; then
    exit 1
fi