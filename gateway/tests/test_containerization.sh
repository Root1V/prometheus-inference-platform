#!/usr/bin/env bash
# gateway/tests/test_containerization.sh
# Implements: memory/specs/004-podman-containerization.md — AC-2, AC-3, AC-8, AC-9
#
# Static/structural tests that run WITHOUT requiring a live Podman daemon.
# They validate the Dockerfile and podman-compose.yml contents directly.
#
# AC-1, AC-4, AC-5, AC-6, AC-7, AC-10 require a running Podman environment
# and are verified manually on the target hosts per the spec.
#
# Usage:
#   bash gateway/tests/test_containerization.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
DOCKERFILE="${REPO_ROOT}/gateway/Dockerfile"
COMPOSE_FILE="${REPO_ROOT}/podman-compose.yml"

PASS=0
FAIL=0

pass() { echo "  PASS: $1"; PASS=$((PASS + 1)); }
fail() { echo "  FAIL: $1"; FAIL=$((FAIL + 1)); }

echo ""
echo "=== Containerization static checks ==="

# ── AC-2: non-root user declared in Dockerfile ────────────────────────────────
echo ""
echo "--- AC-2: Non-root user (prometheus uid 1000) ---"

if grep -q 'USER prometheus' "${DOCKERFILE}"; then
    pass "AC-2: Dockerfile switches to USER prometheus before CMD"
else
    fail "AC-2: Dockerfile missing 'USER prometheus'"
fi

if grep -qE 'useradd.*--uid 1000.*prometheus|adduser.*1000.*prometheus' "${DOCKERFILE}"; then
    pass "AC-2: prometheus user created with uid 1000"
else
    fail "AC-2: prometheus user not created with uid 1000"
fi

# ── AC-3: build tools absent from runtime stage ───────────────────────────────
echo ""
echo "--- AC-3: No build tools in runtime stage ---"

# Extract only the runtime (final) stage of the Dockerfile
RUNTIME_STAGE=$(awk '/^FROM.*AS runtime/,0' "${DOCKERFILE}")

for tool in "COPY --from=ghcr.io/astral-sh/uv" "pip install" "apt-get install" "dnf install"; do
    if echo "${RUNTIME_STAGE}" | grep -qF "${tool}"; then
        fail "AC-3: build tool found in runtime stage: '${tool}'"
    else
        pass "AC-3: runtime stage does not contain '${tool}'"
    fi
done

if echo "${RUNTIME_STAGE}" | grep -q 'uv sync'; then
    fail "AC-3: uv sync found in runtime stage (should only be in builder stage)"
else
    pass "AC-3: no 'uv sync' in runtime stage"
fi

# ── AC-8: Redis is not reachable off this host ───────────────────────────────
#
# PRM-192. This used to grep the compose file for the substring `6379:6379`. A
# loopback-only binding is spelled `127.0.0.1:6379:6379` and *contains* that
# substring, so the check refused the safest binding available for the same
# reason it refused the most dangerous one — it tested the spelling, not the
# property. PRM-191 had to route around it with an overlay file, which nothing
# then checked.
#
# `check_compose_ports.py` parses every compose file in the repo with PyYAML —
# already in each project venv, and no container daemon, which keeps these
# checks static as this file promises at the top. It fails when Redis is
# published anywhere but loopback, fails when the all-container deployment
# publishes it at all, and *names without failing* every other binding that is
# reachable from the local network.
echo ""
echo "--- AC-8: Redis internal-only (property, not spelling) ---"

if (cd "${REPO_ROOT}/gateway" && uv run python tests/check_compose_ports.py "${REPO_ROOT}"); then
    PASS=$((PASS + 2))
else
    fail "AC-8: a compose file publishes Redis beyond this host — see above"
fi

# ── AC-9: No secrets baked into Dockerfile layers ────────────────────────────
echo ""
echo "--- AC-9: No credentials in Dockerfile ---"

# These patterns should never appear in the Dockerfile
SECRET_PATTERNS=(
    'JWT_PUBLIC_KEY='
    'JWT_PRIVATE_KEY='
    'SECRET='
    'PASSWORD='
    'COPY.*\.pem'
    'COPY.*\.key'
    'COPY.*\.env'
    'COPY.*gateway/\.env'
)

for pattern in "${SECRET_PATTERNS[@]}"; do
    if grep -qiE "${pattern}" "${DOCKERFILE}"; then
        fail "AC-9: potentially sensitive pattern found in Dockerfile: '${pattern}'"
    else
        pass "AC-9: no '${pattern}' in Dockerfile"
    fi
done

# AC-9: .env files must NOT be copied into the image
if grep -E 'COPY.*\.env' "${DOCKERFILE}" | grep -v '^#' | grep -q .; then
    fail "AC-9: .env file is being COPY'd into the image"
else
    pass "AC-9: no .env file COPY in Dockerfile"
fi

# ── Compose structural checks ─────────────────────────────────────────────────
echo ""
echo "--- Compose file structure ---"

# Both services declared
for svc in gateway redis; do
    if grep -q "^  ${svc}:" "${COMPOSE_FILE}"; then
        pass "Compose: '${svc}' service declared"
    else
        fail "Compose: '${svc}' service missing"
    fi
done

# Gateway exposes port 8000
if grep -q '"8000:8000"' "${COMPOSE_FILE}" || grep -q "'8000:8000'" "${COMPOSE_FILE}" || grep -q '- "8000:8000"' "${COMPOSE_FILE}"; then
    pass "Compose: gateway port 8000 exposed to host"
else
    fail "Compose: gateway port 8000 not mapped"
fi

# env_file present for gateway (secrets injected at runtime, not baked)
if grep -q 'env_file' "${COMPOSE_FILE}"; then
    pass "Compose: env_file directive present (runtime injection)"
else
    fail "Compose: env_file directive missing"
fi

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo "Results: ${PASS} passed, ${FAIL} failed"
if [[ "${FAIL}" -gt 0 ]]; then
    exit 1
fi
