#!/usr/bin/env bash
# scripts/tests/test_scripts_023.sh
# Implements: memory/specs/023-redhat-compatibility.md — AC-1, AC-2, AC-3, AC-4, AC-5, AC-6, AC-7, AC-8, AC-9, AC-10, AC-11, AC-12, AC-13, AC-14, AC-15
#
# Unit / static tests for scripts/install-rhel.sh and scripts/validate.sh.
# Does NOT require a running RHEL host, llama-server, or Podman containers.
#
# Usage:
#   bash scripts/tests/test_scripts_023.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
INSTALL_SCRIPT="${REPO_ROOT}/scripts/install-rhel.sh"
VALIDATE_SCRIPT="${REPO_ROOT}/scripts/validate.sh"

PASS=0
FAIL=0

# ── Helpers ───────────────────────────────────────────────────────────────────

pass() { echo "  PASS: $1"; PASS=$(( PASS + 1 )); }
fail() { echo "  FAIL: $1"; FAIL=$(( FAIL + 1 )); }

run_test() {
    local name="$1" expected_exit="$2"
    shift 2
    local actual_exit=0
    (set +e; "$@" >/dev/null 2>&1; exit "$?") || actual_exit=$?
    if [[ "${actual_exit}" -eq "${expected_exit}" ]]; then
        pass "${name}"
    else
        fail "${name} (expected exit ${expected_exit}, got ${actual_exit})"
    fi
}

# Create a minimal fake project dir with clean .env files
_make_project_dir() {
    local dir="$1"
    mkdir -p "${dir}/gateway" "${dir}/auth-service"
    # Root .env — include bind-mount host paths required by validate.sh AC-4
    {
        echo "JWT_ISSUER=https://prometheus-victor.internal"
        echo "AUTH_DB_HOST_PATH=/var/lib/prometheus/auth-service"
        echo "CONTAINER_LOG_HOST_PATH=/var/log/prometheus"
        echo "MANAGER_LOG_HOST_PATH=/var/log/prometheus/manager"
        echo "MANAGER_PID_ROOT=/var/run/prometheus/runtime/run"
        echo "MANAGER_LOG_ROOT=/var/log/prometheus/runtime/logs"
    } > "${dir}/.env"
    echo "JWT_ISSUER=https://prometheus-victor.internal"  > "${dir}/gateway/.env"
    # auth-service/.env — include fixed vars required by validate.sh AC-5
    {
        echo "AUTH_JWT_ISSUER=https://prometheus-victor.internal"
        echo "AUTH_DB_URL=sqlite+aiosqlite:////data/auth.db"
        echo "AUTH_PRIVATE_KEY_FILE=/run/secrets/jwt_private_key.pem"
        echo "AUTH_PUBLIC_KEY_FILE=/run/secrets/jwt_public_key.pem"
        echo "AUTH_REVOCATION_REDIS_URL=redis://redis:6379/0"
        echo "AUTH_RATE_LIMIT_RPM=10"
    } > "${dir}/auth-service/.env"
    # Initialise a minimal git repo so validate.sh CHECK 0 passes
    git -C "${dir}" init -q
    git -C "${dir}" commit --allow-empty -q -m "init" \
        --author="test <test@test>" 2>/dev/null || true
}

# ── AC-1: RHEL .env template contents ─────────────────────────────────────────
echo ""
echo "=== AC-1: RHEL .env templates — required keys and values ==="

# Every template must have active REQUESTS_CA_BUNDLE (not commented)
for template in ".env.redhat.example" "gateway/.env.podman.example" "auth-service/.env.example"; do
    file="${REPO_ROOT}/${template}"
    if grep -q "^REQUESTS_CA_BUNDLE=/etc/pki/tls/certs/ca-bundle.crt" "${file}"; then
        pass "AC-1: ${template} has active REQUESTS_CA_BUNDLE"
    else
        fail "AC-1: ${template} missing active REQUESTS_CA_BUNDLE (must not be commented out)"
    fi
done

# Root template: host.containers.internal present
if grep -q "host\.containers\.internal" "${REPO_ROOT}/.env.redhat.example"; then
    pass "AC-1: .env.redhat.example contains host.containers.internal"
else
    fail "AC-1: .env.redhat.example missing host.containers.internal"
fi

# Root template: host.docker.internal must NOT appear as an active value
if ! grep -Ev "^#" "${REPO_ROOT}/.env.redhat.example" | grep -q "host\.docker\.internal"; then
    pass "AC-1: .env.redhat.example has no active host.docker.internal"
else
    fail "AC-1: .env.redhat.example contains active host.docker.internal"
fi

# Root template: PROMETHEUS_GPU_LAYERS=0
if grep -q "^PROMETHEUS_GPU_LAYERS=0" "${REPO_ROOT}/.env.redhat.example"; then
    pass "AC-1: .env.redhat.example has PROMETHEUS_GPU_LAYERS=0"
else
    fail "AC-1: .env.redhat.example missing PROMETHEUS_GPU_LAYERS=0"
fi

# Root template: PROMETHEUS_THREADS=32
if grep -q "^PROMETHEUS_THREADS=32" "${REPO_ROOT}/.env.redhat.example"; then
    pass "AC-1: .env.redhat.example has PROMETHEUS_THREADS=32"
else
    fail "AC-1: .env.redhat.example missing PROMETHEUS_THREADS=32"
fi

# Root template: no macOS /Users/ paths as active values
if ! grep -Ev "^#" "${REPO_ROOT}/.env.redhat.example" | grep -Eq "^[A-Z_]+=/Users/"; then
    pass "AC-1: .env.redhat.example has no macOS /Users/ paths"
else
    fail "AC-1: .env.redhat.example contains macOS /Users/ paths"
fi

# Root template: Linux absolute paths for keys/certs
if grep -q "^JWT_PUBLIC_KEY_HOST_PATH=/etc/prometheus" "${REPO_ROOT}/.env.redhat.example"; then
    pass "AC-1: .env.redhat.example uses /etc/prometheus Linux paths for JWT keys"
else
    fail "AC-1: .env.redhat.example missing Linux absolute paths for JWT keys"
fi

# ── AC-2: JWT issuer and no hardcoded secret values ───────────────────────────
echo ""
echo "=== AC-2: JWT issuer consistency and no hardcoded secrets ==="

for template in ".env.redhat.example" "gateway/.env.podman.example" "auth-service/.env.example"; do
    file="${REPO_ROOT}/${template}"
    if grep -Eq "^(AUTH_JWT_ISSUER|JWT_ISSUER)=https://prometheus-victor\.internal" "${file}"; then
        pass "AC-2: ${template} has correct JWT issuer (https://prometheus-victor.internal)"
    else
        fail "AC-2: ${template} missing or incorrect JWT issuer"
    fi
done

# Secret fields must not carry a real hex/base64 value (only placeholders or blank)
for template in ".env.redhat.example" "auth-service/.env.example"; do
    file="${REPO_ROOT}/${template}"
    # Non-comment lines: reject any value of 40+ contiguous hex chars (real secret)
    if ! grep -Ev "^#|^$" "${file}" | grep -Eq "=[a-f0-9]{40,}$"; then
        pass "AC-2: ${template} has no hardcoded hex secret values"
    else
        fail "AC-2: ${template} contains what looks like a hardcoded hex secret"
    fi
done

# ── AC-3 / AC-11: install-rhel.sh — argument parsing ─────────────────────────
echo ""
echo "=== AC-3 / AC-11: install-rhel.sh argument parsing ==="

# --help exits 0
run_test "AC-3: install-rhel.sh --help exits 0" 0 \
    bash "${INSTALL_SCRIPT}" --help

# --force flag is recognised — no ERROR, --help still exits 0
run_test "AC-11: install-rhel.sh --force is a recognised flag (no ERROR)" 0 \
    bash "${INSTALL_SCRIPT}" --force --help

# --force appears in --help output
actual_help_force="$(bash "${INSTALL_SCRIPT}" --help 2>/dev/null || true)"
if echo "${actual_help_force}" | grep -q '\-\-force'; then
    pass "AC-11: --force appears in --help output"
else
    fail "AC-11: --force missing from --help output"
fi

# Unknown flag exits non-zero
run_test "AC-11: install-rhel.sh unknown flag exits non-zero" 1 \
    bash "${INSTALL_SCRIPT}" --unknown-flag-xyz

# Unknown flag prints ERROR:
actual_out="$(bash "${INSTALL_SCRIPT}" --unknown-flag-xyz 2>&1 || true)"
if echo "${actual_out}" | grep -q "^ERROR:"; then
    pass "AC-11: unknown flag output starts with ERROR:"
else
    fail "AC-11: unknown flag output missing ERROR: prefix (got: ${actual_out:0:80})"
fi

# ── AC-11: OS guard — exits non-zero on non-Linux ─────────────────────────────
echo ""
echo "=== AC-11: OS guard ==="

OS="$(uname -s)"
if [[ "${OS}" != "Linux" ]]; then
    TMP_PROJ="$(mktemp -d)"
    actual_exit=0
    # Note: the script exits at the OS check AFTER creating the log dir,
    # so we need a writable project dir
    (set +e; bash "${INSTALL_SCRIPT}" --project-dir="${TMP_PROJ}" >/dev/null 2>&1; exit "$?") \
        || actual_exit=$?
    rm -rf "${TMP_PROJ}"
    if [[ "${actual_exit}" -ne 0 ]]; then
        pass "AC-11: install-rhel.sh exits non-zero on non-Linux (${OS})"
    else
        fail "AC-11: install-rhel.sh should exit non-zero on non-Linux (${OS})"
    fi
else
    pass "AC-11: OS guard check skipped — running on Linux (guard not applicable)"
fi

# ── AC-3: Step header format in script source ─────────────────────────────────
echo ""
echo "=== AC-3: Step header format (source code) ==="

# Script must use the [STEP N/9] printf format
if grep -q '\[STEP %d/%d\]' "${INSTALL_SCRIPT}"; then
    pass "AC-3: install-rhel.sh contains [STEP N/9] format string"
else
    fail "AC-3: install-rhel.sh missing [STEP N/9] format string"
fi

# Script must reference install-rhel.log
if grep -q 'install-rhel\.log' "${INSTALL_SCRIPT}"; then
    pass "AC-3: install-rhel.sh references install-rhel.log"
else
    fail "AC-3: install-rhel.sh missing install-rhel.log reference"
fi

# Script must have exactly 10 _step() calls
STEP_COUNT="$(grep -c '^_step ' "${INSTALL_SCRIPT}" || true)"
if [[ "${STEP_COUNT}" -eq 10 ]]; then
    pass "AC-3: install-rhel.sh has exactly 10 _step() calls"
else
    fail "AC-3: install-rhel.sh has ${STEP_COUNT} _step() calls (expected 10)"
fi

# ERR trap must be present (AC-11 failure handler)
if grep -q "trap.*_on_error.*ERR" "${INSTALL_SCRIPT}"; then
    pass "AC-11: install-rhel.sh sets ERR trap for step failure handling"
else
    fail "AC-11: install-rhel.sh missing ERR trap"
fi

# ── AC-3: STEP 1 git clone (source code) ──────────────────────────────────────
echo ""
echo "=== AC-3: STEP 1 git clone/pull (source code) ==="

# REPO_URL must point to the configured Git host
if grep -q 'github\.com/<your-username>/prometheus-ai-inference\.git' "${INSTALL_SCRIPT}"; then
    pass "AC-3: install-rhel.sh contains canonical repo URL"
else
    fail "AC-3: install-rhel.sh missing canonical repo URL"
fi

# Idempotent: pull --ff-only if .git dir already present
if grep -q 'pull --ff-only' "${INSTALL_SCRIPT}"; then
    pass "AC-3: install-rhel.sh uses git pull --ff-only for idempotent update"
else
    fail "AC-3: install-rhel.sh missing git pull --ff-only"
fi

# Clone branch: uses git clone (fresh install path)
if grep -q 'git clone' "${INSTALL_SCRIPT}"; then
    pass "AC-3: install-rhel.sh uses git clone for fresh install"
else
    fail "AC-3: install-rhel.sh missing git clone"
fi

# Default PROJECT_DIR is /opt/prometheus-ai-inference
if grep -q 'PROJECT_DIR="/opt/prometheus-ai-inference"' "${INSTALL_SCRIPT}"; then
    pass "AC-3: install-rhel.sh default PROJECT_DIR is /opt/prometheus-ai-inference"
else
    fail "AC-3: install-rhel.sh PROJECT_DIR default is not /opt/prometheus-ai-inference"
fi
echo ""
echo "=== AC-4: llama-server build delegation ==="

INSTALL_SERVER_SCRIPT="${REPO_ROOT}/runtime/scripts/install-server.sh"

# OpenBLAS flags live in runtime/scripts/install-server.sh (auto-selected on Linux)
if grep -q "DGGML_BLAS=ON" "${INSTALL_SERVER_SCRIPT}" && grep -q "DGGML_BLAS_VENDOR=OpenBLAS" "${INSTALL_SERVER_SCRIPT}"; then
    pass "AC-4: install-server.sh contains OpenBLAS cmake flags (set on Linux path)"
else
    fail "AC-4: install-server.sh missing -DGGML_BLAS=ON or -DGGML_BLAS_VENDOR=OpenBLAS"
fi

if grep -q "runtime/scripts/install-server.sh" "${INSTALL_SCRIPT}"; then
    pass "AC-4: install-rhel.sh delegates to runtime/scripts/install-server.sh"
else
    fail "AC-4: install-rhel.sh does not delegate to runtime/scripts/install-server.sh"
fi

# --force flag must exist (replaces --skip-llama-build)
if grep -q '\-\-force' "${INSTALL_SCRIPT}"; then
    pass "AC-4: install-rhel.sh supports --force flag"
else
    fail "AC-4: install-rhel.sh missing --force flag"
fi

# STEP 5 default must skip build when llama-server is already on PATH (idempotent)
if grep -q 'command -v llama-server.*FORCE' "${INSTALL_SCRIPT}"; then
    pass "AC-4: install-rhel.sh skips llama-server build by default when binary already present"
else
    fail "AC-4: install-rhel.sh does not have idempotent guard for llama-server build"
fi

# ── AC-3: STEP 2 package list — RHEL-compatible names ────────────────────────
echo ""
echo "=== AC-3: STEP 2 package list — RHEL-compatible names ==="

# python3-venv must NOT appear in active code (Debian/Ubuntu name, not available on RHEL 9)
# Exclude comment lines (lines starting with optional whitespace then #)
if ! grep -v '^\s*#' "${INSTALL_SCRIPT}" | grep -q 'python3-venv'; then
    pass "AC-3: install-rhel.sh does not use python3-venv in active code (not a valid RHEL 9 package)"
else
    fail "AC-3: install-rhel.sh still references python3-venv in active code (invalid on RHEL 9)"
fi

# python3 must appear in PKGS (required for uv and podman-compose fallback)
if grep -q 'python3' "${INSTALL_SCRIPT}"; then
    pass "AC-3: install-rhel.sh includes python3 in package list"
else
    fail "AC-3: install-rhel.sh missing python3 in package list"
fi

# ── AC-5: uv install and uv sync in source ────────────────────────────────────
echo ""
echo "=== AC-5: uv install and Python workspace sync ==="

# Script must attempt to install uv if not present
if grep -q "pip install.*uv\|pip3 install.*uv" "${INSTALL_SCRIPT}"; then
    pass "AC-5: install-rhel.sh installs uv via pip if not present"
else
    fail "AC-5: install-rhel.sh missing uv installation step"
fi

# Script must run uv sync from the project directory
if grep -q "uv sync" "${INSTALL_SCRIPT}"; then
    pass "AC-5: install-rhel.sh runs uv sync"
else
    fail "AC-5: install-rhel.sh missing uv sync call"
fi

# uv sync must be run from PROJECT_DIR (not an arbitrary directory)
if grep -q "cd.*PROJECT_DIR.*&&.*uv sync\|cd.*\${PROJECT_DIR}.*uv sync" "${INSTALL_SCRIPT}"; then
    pass "AC-5: install-rhel.sh runs uv sync from \${PROJECT_DIR}"
else
    fail "AC-5: install-rhel.sh uv sync not run from \${PROJECT_DIR}"
fi

# ── AC-6: Host directory creation in source ───────────────────────────────────
echo ""
echo "=== AC-6: Host directory setup ==="

for dir in "/etc/prometheus/keys" "/etc/prometheus/certs" \
           "/var/lib/prometheus/auth-service" \
           "/var/log/prometheus" "/var/run/prometheus/runtime/run"; do
    if grep -q "${dir}" "${INSTALL_SCRIPT}"; then
        pass "AC-6: install-rhel.sh creates ${dir}"
    else
        fail "AC-6: install-rhel.sh missing mkdir for ${dir}"
    fi
done

# chcon container_file_t is applied
if grep -q "chcon.*container_file_t" "${INSTALL_SCRIPT}"; then
    pass "AC-6: install-rhel.sh applies chcon container_file_t labels"
else
    fail "AC-6: install-rhel.sh missing chcon container_file_t"
fi

# ── AC-7: RSA keypair and TLS cert names in source ────────────────────────────
echo ""
echo "=== AC-7: RSA keypair and TLS certificate generation ==="

if grep -q "private_2026-q1\.pem" "${INSTALL_SCRIPT}" && \
   grep -q "public_2026-q1\.pem" "${INSTALL_SCRIPT}"; then
    pass "AC-7: install-rhel.sh generates private_2026-q1.pem and public_2026-q1.pem"
else
    fail "AC-7: install-rhel.sh missing 2026-q1 keypair names"
fi

# Key permissions: private 600, public 644
if grep -q "chmod 600.*PRIV_KEY\|chmod 600.*private" "${INSTALL_SCRIPT}"; then
    pass "AC-7: install-rhel.sh sets chmod 600 on private key"
else
    fail "AC-7: install-rhel.sh missing chmod 600 for private key"
fi

# Self-signed cert uses -addext subjectAltName (not just -subj)
if grep -q "subjectAltName" "${INSTALL_SCRIPT}"; then
    pass "AC-7: install-rhel.sh uses subjectAltName in TLS cert"
else
    fail "AC-7: install-rhel.sh missing subjectAltName in TLS cert"
fi

# Idempotent: keypair generation is skipped when files already exist
if grep -q 'if \[\[ -f.*PRIV_KEY' "${INSTALL_SCRIPT}"; then
    pass "AC-7: install-rhel.sh skips keypair generation if already exists (idempotent)"
else
    fail "AC-7: install-rhel.sh missing idempotent check for existing keypair"
fi

# --force must override the keypair idempotency check
if grep -q 'PRIV_KEY.*FORCE\|PUB_KEY.*FORCE\|FORCE.*PRIV_KEY' "${INSTALL_SCRIPT}"; then
    pass "AC-7: keypair idempotency check is overridden by --force"
else
    fail "AC-7: keypair generation does not respect --force flag"
fi

# --force must override TLS cert idempotency check
if grep -q 'cert.*FORCE\|FORCE.*cert' "${INSTALL_SCRIPT}"; then
    pass "AC-7: TLS cert idempotency check is overridden by --force"
else
    fail "AC-7: TLS cert generation does not respect --force flag"
fi

# ── AC-8: Idempotent .env copy in source ──────────────────────────────────────
echo ""
echo "=== AC-8: Idempotent .env copy ==="

# Script must guard with [[ -f "${dst}" ]] before copying
if grep -q '\[\[ -f.*dst.*\]\]' "${INSTALL_SCRIPT}" || grep -q '\[\[ -f "\${dst}"' "${INSTALL_SCRIPT}"; then
    pass "AC-8: install-rhel.sh guards env copy with -f check (idempotent)"
else
    fail "AC-8: install-rhel.sh missing -f guard for idempotent env copy"
fi

# --force must override the .env copy idempotency check
if grep -q 'dst.*FORCE\|FORCE.*dst' "${INSTALL_SCRIPT}"; then
    pass "AC-8: .env copy idempotency check is overridden by --force"
else
    fail "AC-8: .env copy does not respect --force flag"
fi

# Three template→destination mappings are present
if grep -q '\.env\.redhat\.example.*\.env' "${INSTALL_SCRIPT}"; then
    pass "AC-8: install-rhel.sh copies .env.redhat.example → .env"
else
    fail "AC-8: install-rhel.sh missing .env.redhat.example → .env mapping"
fi

if grep -q '\.env\.podman\.example.*gateway/\.env' "${INSTALL_SCRIPT}"; then
    pass "AC-8: install-rhel.sh copies gateway/.env.podman.example → gateway/.env"
else
    fail "AC-8: install-rhel.sh missing gateway/.env.podman.example → gateway/.env"
fi

if grep -q 'auth-service/\.env\.example.*auth-service/\.env' "${INSTALL_SCRIPT}"; then
    pass "AC-8: install-rhel.sh copies auth-service/.env.example → auth-service/.env"
else
    fail "AC-8: install-rhel.sh missing auth-service/.env.example → auth-service/.env"
fi

# ── AC-9: Secret injection in source ──────────────────────────────────────────
echo ""
echo "=== AC-9: Secret injection ==="

# _inject_secret generates the four required secrets
for secret in "AUTH_ADMIN_API_KEY" "SHARE_TOKEN_ENCRYPTION_KEY" \
              "GRAFANA_SECRET_KEY" "GRAFANA_ADMIN_PASSWORD"; do
    if grep -q "_inject_secret.*\"${secret}\"" "${INSTALL_SCRIPT}"; then
        pass "AC-9: install-rhel.sh injects ${secret}"
    else
        fail "AC-9: install-rhel.sh missing _inject_secret call for ${secret}"
    fi
done

# Secrets must NOT be echoed to stdout
if ! grep -n 'echo.*\$secret\b' "${INSTALL_SCRIPT}" | grep -v '^#' | grep -q .; then
    pass "AC-9: install-rhel.sh never echoes \$secret to stdout"
else
    fail "AC-9: install-rhel.sh may echo secret value to stdout"
fi

# Placeholder detection regex covers the required patterns
if grep -q '"replace-"\|"<replace"' "${INSTALL_SCRIPT}"; then
    pass "AC-9: _inject_secret checks for replace- and <replace placeholder patterns"
else
    fail "AC-9: _inject_secret missing placeholder pattern checks"
fi

# --force must re-inject secrets even when placeholder pattern is absent
if grep -q 'FORCE.*inject\|FORCE.*secret\|\"\${FORCE}\".*==.*true' "${INSTALL_SCRIPT}"; then
    pass "AC-9: _inject_secret re-injects secrets when --force is set"
else
    fail "AC-9: _inject_secret does not respect --force flag"
fi

# ── AC-10: Proxy flag in source ───────────────────────────────────────────────
echo ""
echo "=== AC-10: Proxy configuration ==="

# Proxy written to .env
if grep -q "_set_env_var.*http_proxy\|_set_env_var.*HTTP_PROXY" "${INSTALL_SCRIPT}"; then
    pass "AC-10: install-rhel.sh writes http_proxy/HTTP_PROXY to .env"
else
    fail "AC-10: install-rhel.sh missing proxy vars in .env"
fi

# Proxy written to /etc/environment
if grep -q "_set_etc_env.*http_proxy\|_set_etc_env.*HTTP_PROXY" "${INSTALL_SCRIPT}"; then
    pass "AC-10: install-rhel.sh writes http_proxy/HTTP_PROXY to /etc/environment"
else
    fail "AC-10: install-rhel.sh missing proxy vars in /etc/environment"
fi

# NO_PROXY includes standard bypass entries
if grep -q 'NO_PROXY_LIST=.*localhost.*127\.0\.0\.1' "${INSTALL_SCRIPT}"; then
    pass "AC-10: NO_PROXY_LIST contains localhost and 127.0.0.1"
else
    fail "AC-10: NO_PROXY_LIST missing standard bypass entries"
fi

if grep -q 'NO_PROXY_LIST=.*10\.89\.0\.1' "${INSTALL_SCRIPT}"; then
    pass "AC-10: NO_PROXY_LIST contains 10.89.0.1 (Podman subnet)"
else
    fail "AC-10: NO_PROXY_LIST missing 10.89.0.1"
fi

# Proxy is only applied when --proxy= flag is provided (guarded by if block)
if grep -q 'if \[\[ -n.*PROXY_URL' "${INSTALL_SCRIPT}"; then
    pass "AC-10: proxy configuration is gated on --proxy= flag"
else
    fail "AC-10: proxy configuration not gated on --proxy= flag"
fi

# Early proxy export: must appear before STEP 1 (before _step calls)
PRE_STEP_LINES="$(awk '/^_step /{exit} {print}' "${INSTALL_SCRIPT}")"
if echo "${PRE_STEP_LINES}" | grep -q 'export http_proxy'; then
    pass "AC-10: proxy is exported to shell session before STEP 1 (early export)"
else
    fail "AC-10: proxy export to shell session missing or placed after STEP 1"
fi

# ── AC-3: git credentials flag (source code) ─────────────────────────────────
echo ""
echo "=== AC-3: --git-credentials flag and ~/.netrc setup ==="

# Flag must be parsed
if grep -q '\-\-git-credentials=\*)' "${INSTALL_SCRIPT}"; then
    pass "AC-3: install-rhel.sh parses --git-credentials= flag"
else
    fail "AC-3: install-rhel.sh missing --git-credentials= flag parsing"
fi

# Credentials written to ~/.netrc
if grep -q '\.netrc' "${INSTALL_SCRIPT}"; then
    pass "AC-3: install-rhel.sh uses ~/.netrc for git credentials"
else
    fail "AC-3: install-rhel.sh missing ~/.netrc configuration"
fi

# ~/.netrc must be chmod 600
if grep -q 'chmod 600.*netrc\|chmod 600.*NETRC' "${INSTALL_SCRIPT}"; then
    pass "AC-3: install-rhel.sh sets chmod 600 on ~/.netrc"
else
    fail "AC-3: install-rhel.sh missing chmod 600 for ~/.netrc"
fi

# Source file permissions must be validated (600 required)
if grep -q 'SRC_PERMS\|stat.*600' "${INSTALL_SCRIPT}"; then
    pass "AC-3: install-rhel.sh validates source credentials file permissions"
else
    fail "AC-3: install-rhel.sh missing permission validation for credentials file"
fi

# Guard: source == destination (realpath check) must be present to prevent
# `cat file >> file` corrupting ~/.netrc when --git-credentials=~/.netrc is passed
if grep -q 'realpath.*GIT_CREDENTIALS_FILE.*NETRC_FILE\|realpath.*NETRC_FILE.*GIT_CREDENTIALS_FILE' "${INSTALL_SCRIPT}"; then
    pass "AC-3: install-rhel.sh guards against --git-credentials pointing to ~/.netrc itself"
else
    fail "AC-3: install-rhel.sh missing realpath guard (src == dst corruption risk)"
fi

# git safe.directory must be configured before any git operation (CVE-2022-24765)
# Without this, git >= 2.35.2 refuses to operate when root runs on an llmops-owned repo
if grep -q 'safe\.directory.*PROJECT_DIR' "${INSTALL_SCRIPT}"; then
    pass "AC-3: install-rhel.sh configures git safe.directory before git operations (CVE-2022-24765)"
else
    fail "AC-3: install-rhel.sh missing git safe.directory config (fails when run as root on llmops-owned repo)"
fi

# Idempotent pull: stash local changes before pull, pop after
# (prevents failure when uv.lock or other files are modified by a previous partial run)
if grep -q 'git.*stash' "${INSTALL_SCRIPT}"; then
    pass "AC-3: install-rhel.sh stashes local changes before git pull --ff-only"
else
    fail "AC-3: install-rhel.sh missing git stash before pull (dirty working tree will fail)"
fi
if grep -q 'stash pop' "${INSTALL_SCRIPT}"; then
    pass "AC-3: install-rhel.sh restores stashed changes after git pull"
else
    fail "AC-3: install-rhel.sh missing git stash pop after pull"
fi

# Clone path: chown of parent dir must be conditional on llmops user existing
# (user is created in STEP 3 — chown cannot run unconditionally in STEP 1)
if grep -q "id.*LLMOPS_USER.*chown\|id.*llmops.*chown" "${INSTALL_SCRIPT}"; then
    pass "AC-3: install-rhel.sh guards parent dir chown on user existence (fresh-host safe)"
else
    fail "AC-3: install-rhel.sh chown of parent dir not guarded — fails when llmops user absent"
fi

# Clone path: PROJECT_DIR pre-created by log setup (no .git) must be handled gracefully
if grep -q '\-d.*PROJECT_DIR.*&&.*!.*\.git\|! -d.*\.git.*PROJECT_DIR' "${INSTALL_SCRIPT}"; then
    pass "AC-3: install-rhel.sh handles PROJECT_DIR pre-created by log setup before git clone"
else
    fail "AC-3: install-rhel.sh missing guard for non-git PROJECT_DIR (log setup creates it first)"
fi

# Credentials file content must never be echoed to stdout
# (printing the path is fine; printing the contents is forbidden)
if ! grep -n 'echo.*\$(' "${INSTALL_SCRIPT}" | grep -qE 'cat.*GIT_CRED|cat.*netrc'; then
    pass "AC-3: install-rhel.sh never echoes credentials file content to stdout"
else
    fail "AC-3: install-rhel.sh may echo credentials to stdout"
fi

# ── AC-12: validate.sh binary check (runtime) ─────────────────────────────────
echo ""
echo "=== AC-12: validate.sh binary check ==="

MOCK_DIR="$(mktemp -d)"
MOCK_BIN="${MOCK_DIR}/bin"
mkdir -p "${MOCK_BIN}"

# CHECK 0: validate.sh reports FAIL when PROJECT_DIR is not a git repo
TMP_PROJ_NOREPO="$(mktemp -d)"
# Do NOT call _make_project_dir here — we need a dir WITHOUT .git
mkdir -p "${TMP_PROJ_NOREPO}/gateway" "${TMP_PROJ_NOREPO}/auth-service"
echo "KEY=value" > "${TMP_PROJ_NOREPO}/.env"
echo "KEY=value" > "${TMP_PROJ_NOREPO}/gateway/.env"
echo "KEY=value" > "${TMP_PROJ_NOREPO}/auth-service/.env"
actual_out_norepo="$(bash "${VALIDATE_SCRIPT}" \
  --project-dir="${TMP_PROJ_NOREPO}" \
  --gateway-cert="" --auth-cert="" 2>/dev/null || true)"
if echo "${actual_out_norepo}" | grep -q "FAIL.*step-1"; then
    pass "AC-12: validate.sh prints FAIL for repo check when .git absent"
else
    fail "AC-12: validate.sh missing repo check (no FAIL when .git absent)"
fi
rm -rf "${TMP_PROJ_NOREPO}"

TMP_PROJ_12="$(mktemp -d)"
_make_project_dir "${TMP_PROJ_12}"

# Without llama-server in PATH → FAIL and exit 1
actual_exit_12=0
(set +e
 PATH="${MOCK_BIN}:${PATH}" bash "${VALIDATE_SCRIPT}" \
   --project-dir="${TMP_PROJ_12}" \
   --gateway-cert="" --auth-cert="" >/dev/null 2>&1
 exit "$?") || actual_exit_12=$?

if [[ "${actual_exit_12}" -ne 0 ]]; then
    pass "AC-12: validate.sh exits 1 when llama-server not in PATH"
else
    fail "AC-12: validate.sh should exit 1 when llama-server missing"
fi

# With fake llama-server → binary check PASS
cat > "${MOCK_BIN}/llama-server" << 'EOF'
#!/usr/bin/env bash
echo "llama-server version 999.0.0 (test stub)"
exit 0
EOF
chmod +x "${MOCK_BIN}/llama-server"

actual_output_12="$(PATH="${MOCK_BIN}:${PATH}" bash "${VALIDATE_SCRIPT}" \
  --project-dir="${TMP_PROJ_12}" \
  --gateway-cert="" --auth-cert="" 2>/dev/null || true)"

if echo "${actual_output_12}" | grep -q "PASS.*step-5"; then
    pass "AC-12: validate.sh prints PASS for binary when llama-server found"
else
    fail "AC-12: validate.sh did not print PASS for binary check"
fi

# ── AC-13: validate.sh env-file placeholder check (runtime) ───────────────────
echo ""
echo "=== AC-13: validate.sh env-file placeholder check ==="

TMP_PROJ_13="$(mktemp -d)"
mkdir -p "${TMP_PROJ_13}/gateway" "${TMP_PROJ_13}/auth-service"
echo "KEY=value" > "${TMP_PROJ_13}/.env"
echo "KEY=value" > "${TMP_PROJ_13}/gateway/.env"
# auth-service/.env has a placeholder
echo "AUTH_ADMIN_API_KEY=<replace-with-openssl-rand-hex-32>" > "${TMP_PROJ_13}/auth-service/.env"

actual_out_13="$(PATH="${MOCK_BIN}:${PATH}" bash "${VALIDATE_SCRIPT}" \
  --project-dir="${TMP_PROJ_13}" \
  --gateway-cert="" --auth-cert="" 2>/dev/null || true)"

if echo "${actual_out_13}" | grep -q "FAIL.*env-files"; then
    pass "AC-13: validate.sh prints FAIL when placeholder found in .env"
else
    fail "AC-13: validate.sh did not FAIL when placeholder present"
fi

# Missing .env file → FAIL
TMP_PROJ_13b="$(mktemp -d)"
mkdir -p "${TMP_PROJ_13b}/auth-service"
echo "KEY=value" > "${TMP_PROJ_13b}/.env"
# gateway/.env intentionally absent

actual_out_13b="$(PATH="${MOCK_BIN}:${PATH}" bash "${VALIDATE_SCRIPT}" \
  --project-dir="${TMP_PROJ_13b}" \
  --gateway-cert="" --auth-cert="" 2>/dev/null || true)"

if echo "${actual_out_13b}" | grep -q "FAIL.*env-files"; then
    pass "AC-13: validate.sh prints FAIL when .env file is missing"
else
    fail "AC-13: validate.sh did not FAIL for missing .env file"
fi

# Clean .env files → env-files check passes (script still exits 1 due to health checks)
TMP_PROJ_13c="$(mktemp -d)"
_make_project_dir "${TMP_PROJ_13c}"

actual_out_13c="$(PATH="${MOCK_BIN}:${PATH}" bash "${VALIDATE_SCRIPT}" \
  --project-dir="${TMP_PROJ_13c}" \
  --gateway-cert="" --auth-cert="" 2>/dev/null || true)"

# All three env-files checks should PASS (none FAIL)
if echo "${actual_out_13c}" | grep -q "FAIL.*env-files"; then
    fail "AC-13: validate.sh reported env-files FAIL for clean .env files"
else
    pass "AC-13: validate.sh does not FAIL env-files for clean .env files"
fi

# ── AC-14: validate.sh health checks (runtime) ────────────────────────────────
echo ""
echo "=== AC-14: validate.sh health checks ==="

TMP_PROJ_14="$(mktemp -d)"
_make_project_dir "${TMP_PROJ_14}"

# With no containers running, gateway-health and auth-health should FAIL
actual_out_14="$(PATH="${MOCK_BIN}:${PATH}" bash "${VALIDATE_SCRIPT}" \
  --project-dir="${TMP_PROJ_14}" \
  --gateway-cert="" --auth-cert="" 2>/dev/null || true)"

if echo "${actual_out_14}" | grep -q "FAIL.*gateway-health"; then
    pass "AC-14: validate.sh prints FAIL for unreachable gateway-health"
else
    fail "AC-14: validate.sh did not FAIL gateway-health when no container running"
fi

if echo "${actual_out_14}" | grep -q "FAIL.*auth-health"; then
    pass "AC-14: validate.sh prints FAIL for unreachable auth-health"
else
    fail "AC-14: validate.sh did not FAIL auth-health when no container running"
fi

# ── AC-15: validate.sh oauth2 check (runtime) ─────────────────────────────────
echo ""
echo "=== AC-15: validate.sh oauth2 check ==="

TMP_PROJ_15="$(mktemp -d)"
mkdir -p "${TMP_PROJ_15}/gateway" "${TMP_PROJ_15}/auth-service"
echo "KEY=value" > "${TMP_PROJ_15}/.env"
echo "KEY=value" > "${TMP_PROJ_15}/gateway/.env"
# Placeholder admin key → oauth2 check should FAIL without any network call
echo "AUTH_ADMIN_API_KEY=replace-with-openssl-rand-hex-32" > "${TMP_PROJ_15}/auth-service/.env"

actual_out_15="$(PATH="${MOCK_BIN}:${PATH}" bash "${VALIDATE_SCRIPT}" \
  --project-dir="${TMP_PROJ_15}" \
  --gateway-cert="" --auth-cert="" 2>/dev/null || true)"

if echo "${actual_out_15}" | grep -q "FAIL.*oauth2"; then
    pass "AC-15: validate.sh prints FAIL when AUTH_ADMIN_API_KEY is placeholder"
else
    fail "AC-15: validate.sh did not FAIL oauth2 with placeholder admin key"
fi

# Empty admin key → FAIL
echo "AUTH_ADMIN_API_KEY=" > "${TMP_PROJ_15}/auth-service/.env"
actual_out_15b="$(PATH="${MOCK_BIN}:${PATH}" bash "${VALIDATE_SCRIPT}" \
  --project-dir="${TMP_PROJ_15}" \
  --gateway-cert="" --auth-cert="" 2>/dev/null || true)"

if echo "${actual_out_15b}" | grep -q "FAIL.*oauth2"; then
    pass "AC-15: validate.sh prints FAIL when AUTH_ADMIN_API_KEY is empty"
else
    fail "AC-15: validate.sh did not FAIL oauth2 with empty admin key"
fi

# validate.sh must never use -k/--insecure (security constraint from spec)
if ! grep -q -- "-k\b\|--insecure" "${VALIDATE_SCRIPT}"; then
    pass "AC-15: validate.sh never uses -k/--insecure (no TLS bypass)"
else
    fail "AC-15: validate.sh contains -k/--insecure (forbidden)"
fi

# validate.sh exits 1 when any check fails (overall exit code)
actual_exit_15=0
(set +e
 PATH="${MOCK_BIN}:${PATH}" bash "${VALIDATE_SCRIPT}" \
   --project-dir="${TMP_PROJ_15}" \
   --gateway-cert="" --auth-cert="" >/dev/null 2>&1
 exit "$?") || actual_exit_15=$?
if [[ "${actual_exit_15}" -ne 0 ]]; then
    pass "AC-15: validate.sh exits 1 when any check fails"
else
    fail "AC-15: validate.sh should exit 1 when checks fail"
fi

# ── validate.sh argument parsing ─────────────────────────────────────────────
echo ""
echo "=== validate.sh argument parsing ==="

run_test "validate.sh: --help exits 0" 0 bash "${VALIDATE_SCRIPT}" --help
run_test "validate.sh: unknown flag exits non-zero" 1 bash "${VALIDATE_SCRIPT}" --bad-flag-xyz

# --user flag must be accepted (no ERROR)
actual_help_user="$(bash "${VALIDATE_SCRIPT}" --help 2>/dev/null || true)"
if echo "${actual_help_user}" | grep -q '\-\-user'; then
    pass "AC-12: validate.sh --user appears in --help output"
else
    fail "AC-12: validate.sh --user missing from --help output"
fi

# ── validate.sh 10-step structure (static) ────────────────────────────────────
echo ""
echo "=== AC-12: validate.sh 10-step structure (static) ==="

# Each step keyword must appear in validate.sh
for step in "step-1/repo" "step-2/packages" "step-3/llmops-user" "step-4/uv-venv" \
            "step-5/llama-server" "step-6/host-dirs" "step-7/keys-certs" \
            "step-8/env-files" "step-9/secrets" "step-10/proxy"; do
    if grep -q "${step}" "${VALIDATE_SCRIPT}"; then
        pass "AC-12: validate.sh contains check '${step}'"
    else
        fail "AC-12: validate.sh missing check '${step}'"
    fi
done

# step-2 checks for cmake, podman, git, python3
for pkg in cmake podman git python3; do
    if grep -q "\b${pkg}\b" "${VALIDATE_SCRIPT}"; then
        pass "AC-12: validate.sh checks for package '${pkg}' in step-2"
    else
        fail "AC-12: validate.sh missing package check for '${pkg}'"
    fi
done

# step-3 checks for user existence via id
if grep -q 'id.*LLMOPS_USER\|id.*llmops' "${VALIDATE_SCRIPT}"; then
    pass "AC-12: validate.sh checks llmops user existence (id) in step-3"
else
    fail "AC-12: validate.sh missing 'id' check for llmops user in step-3"
fi

# step-4 checks for .venv directory
if grep -q '\.venv' "${VALIDATE_SCRIPT}"; then
    pass "AC-12: validate.sh checks for .venv directory in step-4"
else
    fail "AC-12: validate.sh missing .venv check in step-4"
fi

# step-6 checks for /etc/prometheus and /var/lib/prometheus paths
for dir in "/etc/prometheus/keys" "/var/lib/prometheus" "/var/log/prometheus" "/var/run/prometheus"; do
    if grep -q "${dir}" "${VALIDATE_SCRIPT}"; then
        pass "AC-12: validate.sh checks for host dir '${dir}' in step-6"
    else
        fail "AC-12: validate.sh missing host dir check '${dir}'"
    fi
done

# step-7 checks for private.pem, public.pem, gateway.crt, auth.crt
for f in "private.pem" "public.pem" "gateway.crt" "auth.crt"; do
    if grep -q "${f}" "${VALIDATE_SCRIPT}"; then
        pass "AC-12: validate.sh checks for key/cert '${f}' in step-7"
    else
        fail "AC-12: validate.sh missing key/cert check '${f}'"
    fi
done

# step-9 checks for the four required secrets
for secret in "AUTH_ADMIN_API_KEY" "SHARE_TOKEN_ENCRYPTION_KEY" "GRAFANA_SECRET_KEY" "GRAFANA_ADMIN_PASSWORD"; do
    if grep -q "${secret}" "${VALIDATE_SCRIPT}"; then
        pass "AC-13: validate.sh checks secret '${secret}' in step-9"
    else
        fail "AC-13: validate.sh missing secret check '${secret}' in step-9"
    fi
done

# step-10 proxy check must be non-fatal (INFO result, not FAIL, when no proxy)
if grep -q '_info_result.*step-10/proxy\|step-10/proxy.*info' "${VALIDATE_SCRIPT}"; then
    pass "AC-12: validate.sh step-10/proxy uses _info_result (non-fatal when no proxy)"
else
    fail "AC-12: validate.sh step-10/proxy should be _info_result (not a hard FAIL)"
fi

# ── Cleanup ───────────────────────────────────────────────────────────────────
rm -rf "${MOCK_DIR}" "${TMP_PROJ_12}" \
       "${TMP_PROJ_13}" "${TMP_PROJ_13b}" "${TMP_PROJ_13c}" \
       "${TMP_PROJ_14}" "${TMP_PROJ_15}"

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo "══════════════════════════════════════════════════════════"
printf "  Test results: %d passed, %d failed\n" "${PASS}" "${FAIL}"
echo "══════════════════════════════════════════════════════════"

if [[ "${FAIL}" -gt 0 ]]; then
    exit 1
fi
exit 0
