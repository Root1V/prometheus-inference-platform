#!/usr/bin/env bash
# scripts/validate.sh
# Implements: memory/specs/023-redhat-compatibility.md — AC-12, AC-13, AC-14, AC-15
#
# Post-install smoke-test for the Prometheus RHEL stack.
# Mirrors the 10 installation steps from scripts/install-rhel.sh so you can
# see exactly which steps completed successfully and which did not.
# Prints a PASS/FAIL table for each check and exits with code 1 if any check fails.
# Safe to re-run at any time — performs no writes to the host.
#
# Usage:
#   bash scripts/validate.sh [options]
#
# Options:
#   --project-dir=PATH   Repository root (default: /opt/prometheus-ai-inference)
#   --user=NAME          llmops user to validate (default: llmops)
#   --gateway-cert=PATH  CA cert for gateway TLS (default: /etc/prometheus/certs/gateway.crt)
#   --auth-cert=PATH     CA cert for auth-service TLS (default: /etc/prometheus/certs/auth.crt)
#   --help               Show this help message

set -uo pipefail

# ── Defaults ───────────────────────────────────────────────────────────────────
PROJECT_DIR="/opt/prometheus-ai-inference"
LLMOPS_USER="llmops"
GW_CACERT="/etc/prometheus/certs/gateway.crt"
AUTH_CACERT="/etc/prometheus/certs/auth.crt"

# ── Argument parsing ───────────────────────────────────────────────────────────
for arg in "$@"; do
    case "${arg}" in
        --project-dir=*)  PROJECT_DIR="${arg#*=}" ;;
        --user=*)         LLMOPS_USER="${arg#*=}" ;;
        --gateway-cert=*) GW_CACERT="${arg#*=}" ;;
        --auth-cert=*)    AUTH_CACERT="${arg#*=}" ;;
        --help)
            grep '^#' "$0" | grep -v '#!/' | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *)
            echo "ERROR: Unknown option: ${arg}" >&2
            exit 1
            ;;
    esac
done

# ── Result tracking ────────────────────────────────────────────────────────────
PASS_COUNT=0
FAIL_COUNT=0
declare -a RESULTS=()

_pass() {
    local name="$1" detail="${2:-}"
    PASS_COUNT=$(( PASS_COUNT + 1 ))
    RESULTS+=("  PASS  ${name}${detail:+  (${detail})}")
}

_fail() {
    local name="$1" detail="${2:-}"
    FAIL_COUNT=$(( FAIL_COUNT + 1 ))
    RESULTS+=("  FAIL  ${name}${detail:+  → ${detail}}")
}

_info_result() {
    local name="$1" detail="${2:-}"
    RESULTS+=("  INFO  ${name}${detail:+  (${detail})}")  
}

_print_results() {
    echo ""
    echo "╔══════════════════════════════════════════════════════════════════════╗"
    echo "║  Prometheus — post-install validation                               ║"
    echo "╠══════════════════════════════════════════════════════════════════════╣"
    printf "║  %-6s  %-30s  %-30s ║\n" "Result" "Check" "Detail"
    echo "╠══════════════════════════════════════════════════════════════════════╣"
    for line in "${RESULTS[@]}"; do
        printf "║ %s\n" "${line}"
    done
    echo "╠══════════════════════════════════════════════════════════════════════╣"
    printf "║  Total: %d passed, %d failed%s║\n" \
        "${PASS_COUNT}" "${FAIL_COUNT}" \
        "$(printf '%*s' $(( 37 - ${#PASS_COUNT} - ${#FAIL_COUNT} )) '')"
    echo "╚══════════════════════════════════════════════════════════════════════╝"
    echo ""
}

# ── STEP 1: repo — repository is cloned at PROJECT_DIR
# Implements: AC-12
# ──────────────────────────────────────────────────────────────────────────────
echo "[ step-1/repo ] Checking repository clone at ${PROJECT_DIR}..."
if [[ -d "${PROJECT_DIR}/.git" ]]; then
    REPO_COMMIT="$(git -C "${PROJECT_DIR}" rev-parse --short HEAD 2>/dev/null || echo 'unknown')"
    REPO_BRANCH="$(git -C "${PROJECT_DIR}" rev-parse --abbrev-ref HEAD 2>/dev/null || echo 'unknown')"
    _pass "step-1/repo" "cloned — ${REPO_BRANCH}@${REPO_COMMIT}"
else
    _fail "step-1/repo" "PROJECT_DIR '${PROJECT_DIR}' is not a git repository — run install-rhel.sh first"
fi

# ── STEP 2: packages — required system packages installed
# Implements: AC-12
# ──────────────────────────────────────────────────────────────────────────────
echo "[ step-2/packages ] Checking required system packages..."
MISSING_PKGS=()
for cmd in cmake gcc podman git python3; do
    command -v "${cmd}" &>/dev/null || MISSING_PKGS+=("${cmd}")
done
if [[ "${#MISSING_PKGS[@]}" -eq 0 ]]; then
    _pass "step-2/packages" "cmake gcc podman git python3 all found on PATH"
else
    _fail "step-2/packages" "missing: ${MISSING_PKGS[*]} — run install-rhel.sh STEP 2"
fi

# ── STEP 3: llmops-user — user and project directory ownership
# Implements: AC-12
# ──────────────────────────────────────────────────────────────────────────────
echo "[ step-3/llmops-user ] Checking '${LLMOPS_USER}' user and project directory..."
if ! id "${LLMOPS_USER}" &>/dev/null 2>&1; then
    _fail "step-3/llmops-user" "user '${LLMOPS_USER}' does not exist — run install-rhel.sh STEP 3"
elif [[ ! -d "${PROJECT_DIR}" ]]; then
    _fail "step-3/project-dir" "'${PROJECT_DIR}' not found — run install-rhel.sh STEP 3"
else
    _pass "step-3/llmops-user" "user '${LLMOPS_USER}' exists, project dir present"
fi

# ── STEP 4: uv + venv — Python toolchain
# Implements: AC-12
# ──────────────────────────────────────────────────────────────────────────────
echo "[ step-4/uv-venv ] Checking uv and Python venv..."
UV_CMD=""
if command -v uv &>/dev/null; then
    UV_CMD="$(command -v uv)"
elif [[ -x "${HOME}/.local/bin/uv" ]]; then
    UV_CMD="${HOME}/.local/bin/uv"
fi
if [[ -n "${UV_CMD}" ]]; then
    UV_VER="$("${UV_CMD}" --version 2>/dev/null | head -1 || echo 'unknown')"
    if [[ -d "${PROJECT_DIR}/.venv" ]]; then
        _pass "step-4/uv-venv" "${UV_VER} · .venv present"
    else
        _fail "step-4/uv-venv" "${UV_VER} found but ${PROJECT_DIR}/.venv missing — run install-rhel.sh STEP 4"
    fi
else
    _fail "step-4/uv-venv" "uv not found on PATH or ~/.local/bin — run install-rhel.sh STEP 4"
fi

# ── STEP 5: llama-server — binary present and functional
# Implements: AC-12
# ──────────────────────────────────────────────────────────────────────────────
echo "[ step-5/llama-server ] Checking llama-server binary..."
if command -v llama-server &>/dev/null && llama-server --version &>/dev/null 2>&1; then
    VER="$(llama-server --version 2>&1 | head -1)"
    _pass "step-5/llama-server" "${VER}"
else
    _fail "step-5/llama-server" "llama-server not found on PATH or --version failed — run install-rhel.sh STEP 5"
fi

# ── STEP 6: host-dirs — required host directories exist
# Implements: AC-12
# ──────────────────────────────────────────────────────────────────────────────
echo "[ step-6/host-dirs ] Checking required host directories..."
HOST_DIRS=(
    "/etc/prometheus/keys"
    "/etc/prometheus/certs"
    "/var/lib/prometheus/auth-service"
    "/var/log/prometheus"
    "/var/run/prometheus/runtime/run"
)
MISSING_DIRS=()
for dir in "${HOST_DIRS[@]}"; do
    [[ -d "${dir}" ]] || MISSING_DIRS+=("${dir}")
done
if [[ "${#MISSING_DIRS[@]}" -eq 0 ]]; then
    _pass "step-6/host-dirs" "all ${#HOST_DIRS[@]} required directories present"
else
    _fail "step-6/host-dirs" "missing: ${MISSING_DIRS[*]} — run install-rhel.sh STEP 6"
fi

# ── STEP 7: keys + certs — RSA keypair and TLS certificates
# Implements: AC-12
# Extended: memory/specs/025-tls-cert-ownership-hotfix.md — AC-1, AC-2, AC-3
# ──────────────────────────────────────────────────────────────────────────────
echo "[ step-7/keys-certs ] Checking RSA keypair and TLS certificates..."
MISSING_KEYS=()
for f in "/etc/prometheus/keys/private.pem" "/etc/prometheus/keys/public.pem" \
         "/etc/prometheus/certs/gateway.crt" "/etc/prometheus/certs/auth.crt"; do
    [[ -f "${f}" ]] || MISSING_KEYS+=("$(basename "${f}")") 
done
if [[ "${#MISSING_KEYS[@]}" -eq 0 ]]; then
    _pass "step-7/keys-certs" "private.pem public.pem gateway.crt auth.crt all present"
else
    _fail "step-7/keys-certs" "missing: ${MISSING_KEYS[*]} — run install-rhel.sh STEP 7"
fi

# Ownership + mode checks — cert/key files must be owned by the service UID,
# not root, so containers can read them via bind mount.
# gateway → UID 1000 (prometheus), auth → UID 1001 (prometheus-auth)
_check_cert_ownership() {
    local file="$1" expected_uid="$2" expected_mode="$3"
    local base
    base="$(basename "${file}")"
    if [[ ! -f "${file}" ]]; then
        _fail "step-7/cert-ownership" "${base}: file not found"
        return
    fi
    local actual_uid actual_mode
    actual_uid="$(stat --format='%u' "${file}" 2>/dev/null || echo 'unknown')"
    actual_mode="$(stat --format='%a' "${file}" 2>/dev/null || echo 'unknown')"
    if [[ "${actual_uid}" != "${expected_uid}" ]]; then
        _fail "step-7/cert-ownership" "${base}: owner UID is ${actual_uid}, expected ${expected_uid} — run install-rhel.sh STEP 7"
    else
        _pass "step-7/cert-ownership" "${base}: owner UID ${actual_uid} ✓"
    fi
    if [[ "${actual_mode}" != "${expected_mode}" ]]; then
        _fail "step-7/cert-ownership" "${base}: mode is ${actual_mode}, expected ${expected_mode}"
    else
        _pass "step-7/cert-ownership" "${base}: mode ${actual_mode} ✓"
    fi
}
_check_cert_ownership "/etc/prometheus/certs/gateway.crt" "1000" "644"
_check_cert_ownership "/etc/prometheus/certs/gateway.key" "1000" "600"
_check_cert_ownership "/etc/prometheus/certs/auth.crt"    "1001" "644"
_check_cert_ownership "/etc/prometheus/certs/auth.key"    "1001" "600"

# --- Validate private.pem symlink and target ownership/permissions ---
AUTH_SYMLINK="/etc/prometheus/keys/private.pem"
if [[ -L "${AUTH_SYMLINK}" ]]; then
    AUTH_TARGET="$(readlink -f "${AUTH_SYMLINK}")"
    if [[ -f "${AUTH_TARGET}" ]]; then
        SYMLINK_UID="$(stat --format='%u' "${AUTH_SYMLINK}" 2>/dev/null || echo 'unknown')"
        TARGET_UID="$(stat --format='%u' "${AUTH_TARGET}" 2>/dev/null || echo 'unknown')"
        TARGET_MODE="$(stat --format='%a' "${AUTH_TARGET}" 2>/dev/null || echo 'unknown')"
        if [[ "${SYMLINK_UID}" != "1001" ]]; then
            _fail "step-7/private.pem-symlink" "symlink owner UID is ${SYMLINK_UID}, expected 1001 (prometheus-auth)"
        else
            _pass "step-7/private.pem-symlink" "symlink owner UID ${SYMLINK_UID} ✓"
        fi
        if [[ "${TARGET_UID}" != "1001" ]]; then
            _fail "step-7/private.pem-target" "target owner UID is ${TARGET_UID}, expected 1001 (prometheus-auth)"
        else
            _pass "step-7/private.pem-target" "target owner UID ${TARGET_UID} ✓"
        fi
        if [[ "${TARGET_MODE}" != "600" ]]; then
            _fail "step-7/private.pem-target" "target mode is ${TARGET_MODE}, expected 600"
        else
            _pass "step-7/private.pem-target" "target mode ${TARGET_MODE} ✓"
        fi
    else
        _fail "step-7/private.pem-target" "symlink target does not exist: ${AUTH_TARGET}"
    fi
else
    _fail "step-7/private.pem-symlink" "/etc/prometheus/keys/private.pem is not a symlink"
fi

# ── STEP 8: env-files — .env files copied from templates, free of placeholders
# Implements: AC-13
# ──────────────────────────────────────────────────────────────────────────────
echo "[ step-8/env-files ] Checking .env files for presence and placeholder values..."

ENV_FILES=(
    "${PROJECT_DIR}/.env"
    "${PROJECT_DIR}/gateway/.env"
    "${PROJECT_DIR}/auth-service/.env"
)

env_ok=true
for env_file in "${ENV_FILES[@]}"; do
    rel="${env_file#${PROJECT_DIR}/}"
    if [[ ! -f "${env_file}" ]]; then
        _fail "env-files:${rel}" "file not found — run install-rhel.sh STEP 8"
        env_ok=false
        continue
    fi
    # Search for lines that still contain placeholder patterns
    bad_lines="$(grep -n '<replace-\|<placeholder\|replace-with-' "${env_file}" 2>/dev/null || true)"
    if [[ -n "${bad_lines}" ]]; then
        first="$(echo "${bad_lines}" | head -1)"
        _fail "env-files:${rel}" "placeholder on line ${first%%:*}"
        env_ok=false
    else
        _pass "env-files:${rel}"
    fi
done

# Verify bind-mount host paths are set to absolute RHEL values in root .env.
# Implements: memory/specs/025-tls-cert-ownership-hotfix.md — AC-4
_check_bind_path() {
    local key="$1" expected="$2"
    local actual
    actual="$(grep "^${key}=" "${PROJECT_DIR}/.env" 2>/dev/null | cut -d= -f2- || true)"
    if [[ "${actual}" == "${expected}" ]]; then
        _pass "env-files:bind-mount:${key}"
    else
        _fail "env-files:bind-mount:${key}" "expected '${expected}', got '${actual:-<unset>}'"
        env_ok=false
    fi
}
_check_bind_path "AUTH_DB_HOST_PATH"        "/var/lib/prometheus/auth-service"
_check_bind_path "CONTAINER_LOG_HOST_PATH"  "/var/log/prometheus"
_check_bind_path "MANAGER_LOG_HOST_PATH"    "/var/log/prometheus/manager"
_check_bind_path "MANAGER_PID_ROOT"         "/var/run/prometheus/runtime/run"
_check_bind_path "MANAGER_LOG_ROOT"         "/var/log/prometheus/runtime/logs"
unset -f _check_bind_path

# Verify fixed (non-secret) auth-service variables are active in auth-service/.env.
# Implements: memory/specs/025-tls-cert-ownership-hotfix.md — AC-5
echo "[ step-8/auth-env ] Checking fixed auth-service variables are active..."
_check_auth_var() {
    local key="$1" expected="$2"
    local actual
    actual="$(grep "^${key}=" "${PROJECT_DIR}/auth-service/.env" 2>/dev/null | cut -d= -f2- || true)"
    if [[ "${actual}" == "${expected}" ]]; then
        _pass "step-8/auth-env:${key}"
    else
        _fail "step-8/auth-env:${key}" "expected '${expected}', got '${actual:-<unset or commented>}'"
    fi
}
if [[ -f "${PROJECT_DIR}/auth-service/.env" ]]; then
    _check_auth_var "AUTH_DB_URL"               "sqlite+aiosqlite:////data/auth.db"
    _check_auth_var "AUTH_PRIVATE_KEY_FILE"     "/run/secrets/jwt_private_key.pem"
    _check_auth_var "AUTH_PUBLIC_KEY_FILE"      "/run/secrets/jwt_public_key.pem"
    _check_auth_var "AUTH_REVOCATION_REDIS_URL" "redis://redis:6379/0"
    _check_auth_var "AUTH_RATE_LIMIT_RPM"       "10"
else
    _fail "step-8/auth-env" "auth-service/.env missing — run install-rhel.sh STEP 8 first"
fi
unset -f _check_auth_var

# ── STEP 9: secrets — required secrets injected (non-placeholder values)
# Implements: AC-13
# ──────────────────────────────────────────────────────────────────────────────
echo "[ step-9/secrets ] Checking required secrets are injected..."
AUTH_ENV_FILE="${PROJECT_DIR}/auth-service/.env"
ROOT_ENV_FILE="${PROJECT_DIR}/.env"

_check_secret() {
    local file="$1" key="$2"
    local val
    val="$(grep "^${key}=" "${file}" 2>/dev/null | cut -d= -f2- || true)"
    if [[ -z "${val}" || "${val}" == *"replace-"* || "${val}" == *"<replace"* ]]; then
        _fail "step-9/secrets:${key}" "not set or still a placeholder — run install-rhel.sh STEP 9"
    else
        _pass "step-9/secrets:${key}"
    fi
}

if [[ -f "${AUTH_ENV_FILE}" ]]; then
    _check_secret "${AUTH_ENV_FILE}" "AUTH_ADMIN_API_KEY"
    _check_secret "${AUTH_ENV_FILE}" "SHARE_TOKEN_ENCRYPTION_KEY"
else
    _fail "step-9/secrets" "auth-service/.env missing — run install-rhel.sh STEP 8 first"
fi
if [[ -f "${ROOT_ENV_FILE}" ]]; then
    _check_secret "${ROOT_ENV_FILE}" "GRAFANA_SECRET_KEY"
    _check_secret "${ROOT_ENV_FILE}" "GRAFANA_ADMIN_PASSWORD"
else
    _fail "step-9/secrets" ".env missing — run install-rhel.sh STEP 8 first"
fi

# ── STEP 10: proxy — proxy configuration (optional)
# Implements: AC-12
# ──────────────────────────────────────────────────────────────────────────────
echo "[ step-10/proxy ] Checking proxy configuration..."
if [[ -f /etc/environment ]] && grep -q '^http_proxy=\|^HTTP_PROXY=' /etc/environment 2>/dev/null; then
    PROXY_VAL="$(grep '^http_proxy=\|^HTTP_PROXY=' /etc/environment | head -1 | cut -d= -f2-)"
    _pass "step-10/proxy" "configured: ${PROXY_VAL}"
else
    _info_result "step-10/proxy" "not configured (optional — only needed behind corporate proxy)"
fi

# ── Runtime: llama-health — bare-metal inference server
# ──────────────────────────────────────────────────────────────────────────────
echo "[ llama-health ] Checking llama-server health endpoint..."
LLAMA_RESP="$(curl -sf --max-time 5 http://127.0.0.1:8080/health 2>/dev/null || echo "")"
if echo "${LLAMA_RESP}" | grep -q '"status"'; then
    _pass "llama-health" "${LLAMA_RESP}"
else
    _fail "llama-health" "no response from http://127.0.0.1:8080/health (server may not be running)"
fi

# ── Runtime: gateway-health — Podman container
# Implements: AC-14
# ──────────────────────────────────────────────────────────────────────────────
echo "[ gateway-health ] Checking gateway health endpoint..."
if [[ -f "${GW_CACERT}" ]]; then
    GW_RESP="$(curl -sf --max-time 5 --cacert "${GW_CACERT}" https://localhost:8000/health 2>/dev/null || echo "")"
else
    GW_RESP="$(curl -sf --max-time 5 https://localhost:8000/health 2>/dev/null || echo "")"
fi

if echo "${GW_RESP}" | grep -q '"status"'; then
    _pass "gateway-health" "${GW_RESP}"
else
    HTTP_CODE="$(curl -so /dev/null -w '%{http_code}' --max-time 5 \
        ${GW_CACERT:+--cacert "${GW_CACERT}"} https://localhost:8000/health 2>/dev/null || echo "000")"
    _fail "gateway-health" "HTTP ${HTTP_CODE} from https://localhost:8000/health"
fi

# ── Runtime: auth-health — Podman container
# Implements: AC-14
# ──────────────────────────────────────────────────────────────────────────────
echo "[ auth-health ] Checking auth-service health endpoint..."
if [[ -f "${AUTH_CACERT}" ]]; then
    AUTH_RESP="$(curl -sf --max-time 5 --cacert "${AUTH_CACERT}" https://localhost:9000/health 2>/dev/null || echo "")"
else
    AUTH_RESP="$(curl -sf --max-time 5 https://localhost:9000/health 2>/dev/null || echo "")"
fi

if echo "${AUTH_RESP}" | grep -q '"status"'; then
    _pass "auth-health" "${AUTH_RESP}"
else
    HTTP_CODE="$(curl -so /dev/null -w '%{http_code}' --max-time 5 \
        ${AUTH_CACERT:+--cacert "${AUTH_CACERT}"} https://localhost:9000/health 2>/dev/null || echo "000")"
    _fail "auth-health" "HTTP ${HTTP_CODE} from https://localhost:9000/health"
fi

# ── Runtime: oauth2 — full round-trip (register → token → /v1/models → delete)
# Implements: AC-15
# ──────────────────────────────────────────────────────────────────────────────
echo "[ oauth2 ] Running OAuth2 smoke-test..."

ADMIN_KEY="$(grep '^AUTH_ADMIN_API_KEY=' "${AUTH_ENV_FILE}" 2>/dev/null | cut -d= -f2- || true)"

if [[ -z "${ADMIN_KEY}" || "${ADMIN_KEY}" == *"replace-"* || "${ADMIN_KEY}" == *"<replace"* ]]; then
    _fail "oauth2" "AUTH_ADMIN_API_KEY not set or still a placeholder in auth-service/.env — skipping"
else
    AUTH_BASE="https://localhost:9000"
    GW_BASE="https://localhost:8000"
    CACERT_AUTH="${AUTH_CACERT}"
    CACERT_GW="${GW_CACERT}"
    TEST_CLIENT_NAME="validate-smoke-test-$(date +%s)"

    # Register a temporary test client
    REG_RESP="$(curl -sf --max-time 10 \
        ${CACERT_AUTH:+--cacert "${CACERT_AUTH}"} \
        -X POST "${AUTH_BASE}/admin/clients" \
        -H "Content-Type: application/json" \
        -H "X-Admin-Key: ${ADMIN_KEY}" \
        -d "{\"client_name\":\"${TEST_CLIENT_NAME}\",\"role\":\"app\",\"allowed_scopes\":[\"inference:read\"]}" \
        2>/dev/null || echo "")"

    CLIENT_ID="$(echo "${REG_RESP}" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('client_id',''))" 2>/dev/null || echo "")"
    CLIENT_SECRET="$(echo "${REG_RESP}" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('client_secret',''))" 2>/dev/null || echo "")"

    if [[ -z "${CLIENT_ID}" || -z "${CLIENT_SECRET}" ]]; then
        _fail "oauth2" "Failed to register test client — auth-service may be unreachable or unhealthy"
    else
        # Obtain token
        TOKEN_RESP="$(curl -sf --max-time 10 \
            ${CACERT_AUTH:+--cacert "${CACERT_AUTH}"} \
            -X POST "${AUTH_BASE}/oauth2/token" \
            -H "Content-Type: application/x-www-form-urlencoded" \
            -d "grant_type=client_credentials&client_id=${CLIENT_ID}&client_secret=${CLIENT_SECRET}&scope=inference:read" \
            2>/dev/null || echo "")"

        ACCESS_TOKEN="$(echo "${TOKEN_RESP}" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('access_token',''))" 2>/dev/null || echo "")"

        if [[ -z "${ACCESS_TOKEN}" ]]; then
            _fail "oauth2" "Token request failed — check auth-service logs"
        else
            # Call GET /v1/models
            MODELS_CODE="$(curl -so /dev/null -w '%{http_code}' --max-time 10 \
                ${CACERT_GW:+--cacert "${CACERT_GW}"} \
                -H "Authorization: Bearer ${ACCESS_TOKEN}" \
                "${GW_BASE}/v1/models" 2>/dev/null || echo "000")"

            if [[ "${MODELS_CODE}" == "200" ]]; then
                _pass "oauth2" "register → token → GET /v1/models HTTP 200"
            else
                _fail "oauth2" "GET /v1/models returned HTTP ${MODELS_CODE}"
            fi
        fi

        # Clean up: delete test client (best-effort — failure does not affect exit code)
        curl -sf --max-time 5 \
            ${CACERT_AUTH:+--cacert "${CACERT_AUTH}"} \
            -X DELETE "${AUTH_BASE}/admin/clients/${CLIENT_ID}" \
            -H "X-Admin-Key: ${ADMIN_KEY}" \
            &>/dev/null || true

        # Clear sensitive vars from memory
        unset ACCESS_TOKEN CLIENT_SECRET ADMIN_KEY
    fi
fi

# ── Print results and exit ─────────────────────────────────────────────────────
_print_results

if [[ "${FAIL_COUNT}" -gt 0 ]]; then
    exit 1
fi
exit 0
