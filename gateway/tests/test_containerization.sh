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

# ── AC-8: Redis has no host port binding ─────────────────────────────────────
echo ""
echo "--- AC-8: Redis internal-only (no 6379:6379 binding) ---"

if grep -v '^\s*#' "${COMPOSE_FILE}" | grep -q '6379:6379'; then
    fail "AC-8: podman-compose.yml exposes Redis port 6379 to host — must be internal only"
else
    pass "AC-8: Redis port 6379 not exposed to host"
fi

# Double-check: redis service has no ports section with host binding
REDIS_SECTION=$(awk '/^  redis:/,/^  [a-z]/' "${COMPOSE_FILE}")
if echo "${REDIS_SECTION}" | grep -qE '^\s+- "?[0-9]+:[0-9]+"?'; then
    fail "AC-8: Redis service has a host:container port mapping"
else
    pass "AC-8: Redis service has no host port mapping"
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
