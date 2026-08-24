#!/usr/bin/env bash
# runtime/scripts/start-all-servers.sh
# Implements: memory/specs/006-multi-model-gateway.md — AC-11, AC-12
#
# Launch multiple llama-server instances (one per model) and update
# registry.yaml with the backend_url for each successfully started model.
#
# Usage:
#   bash runtime/scripts/start-all-servers.sh <env-file-1> [env-file-2 ...]
#
# Each env file must export:
#   PROMETHEUS_MODEL_PATH    — absolute path to the .gguf file
#   PROMETHEUS_MODEL_ALIAS   — model ID (must match an id in registry.yaml)
#   PROMETHEUS_LLAMA_PORT    — unique TCP port (e.g. 8080, 8081)
#
# Optional per env-file:
#   PROMETHEUS_CTX_SIZE, PROMETHEUS_GPU_LAYERS, PROMETHEUS_THREADS
#
# Example:
#   bash runtime/scripts/start-all-servers.sh \
#       runtime/mac-llama3-1b.env \
#       runtime/mac-llama3-8b.env
#
# On SIGTERM / SIGINT all child processes are killed cleanly.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
REGISTRY_YAML="${REPO_ROOT}/runtime/models/registry.yaml"
START_SERVER="${SCRIPT_DIR}/start-server.sh"

# ── Arg validation ────────────────────────────────────────────────────────────

if [[ $# -lt 1 ]]; then
    echo "ERROR: at least one env file is required." >&2
    echo "Usage: bash $(basename "$0") <env-file-1> [env-file-2 ...]" >&2
    exit 1
fi

# ── Collect ports from all env files — validate uniqueness (AC-12) ────────────
# Note: uses bash 3.2-compatible parallel arrays (no associative arrays).

declare -a ENV_FILES=("$@")
declare -a SEEN_PORTS=()
declare -a SEEN_FILES=()

for env_file in "${ENV_FILES[@]}"; do
    if [[ ! -f "${env_file}" ]]; then
        echo "ERROR: env file not found: ${env_file}" >&2
        exit 1
    fi

    # Extract port without sourcing (avoids side effects)
    port="$(grep -E '(export )?PROMETHEUS_LLAMA_PORT' "${env_file}" | tail -1 | grep -oE '[0-9]+')"

    if [[ -z "${port}" ]]; then
        echo "ERROR: PROMETHEUS_LLAMA_PORT not set in ${env_file}" >&2
        exit 1
    fi

    # Check for conflicts using parallel arrays (bash 3.2 compatible)
    conflict_file=""
    for i in "${!SEEN_PORTS[@]}"; do
        if [[ "${SEEN_PORTS[$i]}" == "${port}" ]]; then
            conflict_file="${SEEN_FILES[$i]}"
            break
        fi
    done

    if [[ -n "${conflict_file}" ]]; then
        echo "ERROR: port conflict — port ${port} used in both '${conflict_file}' and '${env_file}'" >&2
        exit 1
    fi
    SEEN_PORTS+=("${port}")
    SEEN_FILES+=("${env_file}")
done

# ── Signal handler — kill all children cleanly ────────────────────────────────

declare -a CHILD_PIDS=()

_cleanup() {
    echo "Stopping all llama-server instances..."
    for pid in "${CHILD_PIDS[@]:-}"; do
        if kill -0 "${pid}" 2>/dev/null; then
            kill "${pid}" && echo "  killed PID ${pid}"
        fi
    done
    exit 0
}

trap _cleanup SIGTERM SIGINT

# ── Launch one llama-server per env file ──────────────────────────────────────

for env_file in "${ENV_FILES[@]}"; do
    # Source into a sub-shell to read variables cleanly
    alias_name="$(bash -c "set -a; source '${env_file}'; echo \"\${PROMETHEUS_MODEL_ALIAS:-}\"" 2>/dev/null)"
    port="$(bash -c "set -a; source '${env_file}'; echo \"\${PROMETHEUS_LLAMA_PORT:-8080}\"" 2>/dev/null)"

    if [[ -z "${alias_name}" ]]; then
        echo "ERROR: PROMETHEUS_MODEL_ALIAS not set in ${env_file}" >&2
        exit 1
    fi

    pid_file="/tmp/prometheus-${alias_name}.pid"

    echo "Starting llama-server for '${alias_name}' on port ${port} ..."

    # Launch in background — inherit env from env file
    (set -a; source "${env_file}"; bash "${START_SERVER}") &
    child_pid=$!
    CHILD_PIDS+=("${child_pid}")

    # Write PID file — AC-11
    echo "${child_pid}" > "${pid_file}"
    echo "  PID ${child_pid} → ${pid_file}"

    # Update registry.yaml with backend_url — Q3 (memory/specs/006)
    backend_url="http://127.0.0.1:${port}"
    echo "  Updating registry.yaml: ${alias_name} → ${backend_url}"

    # Use Python for portable in-place YAML update (avoids sed portability issues)
    "${REPO_ROOT}/.venv/bin/python" - <<PYEOF
import sys
import re

registry = "${REGISTRY_YAML}"
alias = "${alias_name}"
url = "${backend_url}"

with open(registry) as f:
    content = f.read()

# Pattern: find the model entry block and set/replace backend_url under it
# Strategy: find '  - id: "<alias>"' and update or insert backend_url in that block

lines = content.splitlines(keepends=True)
result = []
in_target = False
inserted = False
i = 0

while i < len(lines):
    line = lines[i]

    # Detect start of target model block
    if re.match(r'^\s+-\s+id:\s+["\']?' + re.escape(alias) + r'["\']?\s*$', line):
        in_target = True
        inserted = False
        result.append(line)
        i += 1
        continue

    if in_target:
        # Detect existing backend_url line — replace it
        if re.match(r'^\s+backend_url:\s+', line):
            result.append(f'    backend_url: "{url}"\n')
            inserted = True
            i += 1
            continue
        # Detect end of this model block (next model entry or EOF)
        if re.match(r'^\s+-\s+', line) or i == len(lines) - 1:
            if not inserted:
                result.append(f'    backend_url: "{url}"\n')
                inserted = True
            in_target = False

    result.append(line)
    i += 1

with open(registry, "w") as f:
    f.writelines(result)

print(f"  registry.yaml updated: {alias} backend_url = {url}")
PYEOF

done

echo ""
echo "All ${#ENV_FILES[@]} instance(s) started. Waiting (Ctrl-C to stop all)..."
wait
