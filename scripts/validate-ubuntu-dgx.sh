#!/usr/bin/env bash
# scripts/validate-ubuntu-dgx.sh
#
# Post-install smoke-test for the Prometheus Ubuntu/DGX Spark stack.
# Adapted from scripts/validate.sh originally built for RHEL.
#
# Usage:
#   bash scripts/validate-ubuntu-dgx.sh [options]
#
# Options:
#   --project-dir=PATH   Repository root (default: /opt/prometheus-ai-inference)
#   --user=NAME          llmops user to validate (default: llmops)
#   --gateway-cert=PATH  CA cert for gateway TLS (default: /etc/prometheus/certs/gateway.crt)
#   --auth-cert=PATH     CA cert for auth-service TLS (default: /etc/prometheus/certs/auth.crt)
#   --help               Show this help message

set -uo pipefail

PROJECT_DIR="/opt/prometheus-ai-inference"
LLMOPS_USER="llmops"
GW_CACERT="/etc/prometheus/certs/gateway.crt"
AUTH_CACERT="/etc/prometheus/certs/auth.crt"

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
    RESULTS+=("  FAIL  ${name}${detail:+  -> ${detail}}")
}

_info_result() {
    local name="$1" detail="${2:-}"
    RESULTS+=("  INFO  ${name}${detail:+  (${detail})}")
}

_print_results() {
    echo ""
    echo "======================================================================"
    echo " Prometheus - Ubuntu/DGX Spark post-install validation"
    echo "======================================================================"
    for line in "${RESULTS[@]}"; do
        echo "${line}"
    done
    echo "----------------------------------------------------------------------"
    echo " Total: ${PASS_COUNT} passed, ${FAIL_COUNT} failed"
    echo "======================================================================"
    echo ""
}

echo "[ platform ] Checking Ubuntu/DGX platform..."
if [[ "$(uname -s)" == "Linux" ]]; then
    if [[ -f /etc/os-release ]]; then
        . /etc/os-release
        _info_result "platform/os" "${PRETTY_NAME:-Linux}"
        if [[ "${ID:-}" == "ubuntu" ]]; then
            _pass "platform/ubuntu" "${VERSION_ID:-unknown}"
        else
            _fail "platform/ubuntu" "expected Ubuntu, got ${ID:-unknown}"
        fi
    else
        _info_result "platform/os" "Linux"
    fi
else
    _fail "platform/os" "expected Linux, got $(uname -s)"
fi

ARCH="$(uname -m)"
if [[ "${ARCH}" == "aarch64" || "${ARCH}" == "arm64" ]]; then
    _pass "platform/arch" "${ARCH}"
else
    _info_result "platform/arch" "${ARCH}"
fi

if command -v nvidia-smi >/dev/null 2>&1; then
    GPU_LINE="$(nvidia-smi --query-gpu=name,driver_version --format=csv,noheader 2>/dev/null | head -1 || true)"
    if [[ -n "${GPU_LINE}" ]]; then
        _pass "platform/nvidia-smi" "${GPU_LINE}"
    else
        _fail "platform/nvidia-smi" "nvidia-smi present but GPU query failed"
    fi
else
    _fail "platform/nvidia-smi" "nvidia-smi not found"
fi

echo "[ step-1/repo ] Checking repository clone at ${PROJECT_DIR}..."
if [[ -d "${PROJECT_DIR}/.git" ]]; then
    REPO_COMMIT="$(git -C "${PROJECT_DIR}" rev-parse --short HEAD 2>/dev/null || echo 'unknown')"
    REPO_BRANCH="$(git -C "${PROJECT_DIR}" rev-parse --abbrev-ref HEAD 2>/dev/null || echo 'unknown')"
    _pass "step-1/repo" "cloned - ${REPO_BRANCH}@${REPO_COMMIT}"
else
    _fail "step-1/repo" "PROJECT_DIR '${PROJECT_DIR}' is not a git repository - run install-ubuntu-dgx.sh first"
fi

echo "[ step-2/packages ] Checking required system packages..."
MISSING_CMDS=()
for cmd in cmake gcc g++ make podman git python3 curl openssl; do
    command -v "${cmd}" >/dev/null 2>&1 || MISSING_CMDS+=("${cmd}")
done

if command -v podman-compose >/dev/null 2>&1 || podman compose version >/dev/null 2>&1; then
    :
else
    MISSING_CMDS+=("podman-compose or podman compose")
fi

if [[ "${#MISSING_CMDS[@]}" -eq 0 ]]; then
    _pass "step-2/packages" "cmake gcc g++ make podman git python3 curl openssl found"
else
    _fail "step-2/packages" "missing: ${MISSING_CMDS[*]} - run install-ubuntu-dgx.sh STEP 2"
fi

if dpkg -s libopenblas-dev >/dev/null 2>&1; then
    _pass "step-2/openblas" "libopenblas-dev installed"
else
    _fail "step-2/openblas" "libopenblas-dev missing"
fi

echo "[ step-3/llmops-user ] Checking '${LLMOPS_USER}' user and project directory..."
if ! id "${LLMOPS_USER}" >/dev/null 2>&1; then
    _fail "step-3/llmops-user" "user '${LLMOPS_USER}' does not exist - run install-ubuntu-dgx.sh STEP 3"
elif [[ ! -d "${PROJECT_DIR}" ]]; then
    _fail "step-3/project-dir" "'${PROJECT_DIR}' not found - run install-ubuntu-dgx.sh STEP 3"
else
    _pass "step-3/llmops-user" "user '${LLMOPS_USER}' exists, project dir present"
fi

if id -nG "${LLMOPS_USER}" 2>/dev/null | grep -qw sudo; then
    _pass "step-3/sudo-group" "${LLMOPS_USER} belongs to sudo"
else
    _info_result "step-3/sudo-group" "${LLMOPS_USER} not in sudo group"
fi

if loginctl show-user "${LLMOPS_USER}" -p Linger 2>/dev/null | grep -q 'Linger=yes'; then
    _pass "step-3/linger" "enabled"
else
    _info_result "step-3/linger" "not enabled or loginctl unavailable"
fi

echo "[ step-4/uv-venv ] Checking uv and Python venv..."
UV_CMD=""
if command -v uv >/dev/null 2>&1; then
    UV_CMD="$(command -v uv)"
elif [[ -x "${HOME}/.local/bin/uv" ]]; then
    UV_CMD="${HOME}/.local/bin/uv"
elif [[ -x "/home/${LLMOPS_USER}/.local/bin/uv" ]]; then
    UV_CMD="/home/${LLMOPS_USER}/.local/bin/uv"
fi

if [[ -n "${UV_CMD}" ]]; then
    UV_VER="$("${UV_CMD}" --version 2>/dev/null | head -1 || echo 'unknown')"
    if [[ -d "${PROJECT_DIR}/.venv" ]]; then
        _pass "step-4/uv-venv" "${UV_VER} - .venv present"
    else
        _fail "step-4/uv-venv" "${UV_VER} found but ${PROJECT_DIR}/.venv missing - run install-ubuntu-dgx.sh STEP 4"
    fi
else
    _fail "step-4/uv-venv" "uv not found on PATH or user .local/bin - run install-ubuntu-dgx.sh STEP 4"
fi

echo "[ step-5/llama-server ] Checking llama-server binary..."
LLAMA_BIN=""
if command -v llama-server >/dev/null 2>&1; then
    LLAMA_BIN="$(command -v llama-server)"
elif [[ -x "${HOME}/.local/bin/llama-server" ]]; then
    LLAMA_BIN="${HOME}/.local/bin/llama-server"
elif [[ -x "/home/${LLMOPS_USER}/.local/bin/llama-server" ]]; then
    LLAMA_BIN="/home/${LLMOPS_USER}/.local/bin/llama-server"
fi

if [[ -n "${LLAMA_BIN}" ]] && "${LLAMA_BIN}" --version >/dev/null 2>&1; then
    VER="$("${LLAMA_BIN}" --version 2>&1 | head -1)"
    _pass "step-5/llama-server" "${VER}"
else
    _fail "step-5/llama-server" "llama-server not found or --version failed - run install-ubuntu-dgx.sh STEP 5"
fi

echo "[ step-6/host-dirs ] Checking required host directories..."
HOST_DIRS=(
    "/etc/prometheus/keys"
    "/etc/prometheus/certs"
    "/var/lib/prometheus/auth-service"
    "/srv/prometheus/models"
    "/var/log/prometheus"
    "/var/log/prometheus/gateway"
    "/var/log/prometheus/auth-service"
    "/var/log/prometheus/manager"
    "/var/log/prometheus/runtime/logs"
    "/var/log/prometheus/observability"
    "/var/run/prometheus/runtime/run"
)
MISSING_DIRS=()
for dir in "${HOST_DIRS[@]}"; do
    [[ -d "${dir}" ]] || MISSING_DIRS+=("${dir}")
done
if [[ "${#MISSING_DIRS[@]}" -eq 0 ]]; then
    _pass "step-6/host-dirs" "all ${#HOST_DIRS[@]} required directories present"
else
    _fail "step-6/host-dirs" "missing: ${MISSING_DIRS[*]} - run install-ubuntu-dgx.sh STEP 6"
fi

echo "[ step-7/keys-certs ] Checking RSA keypair and TLS certificates..."
MISSING_KEYS=()
for f in "/etc/prometheus/keys/private.pem" "/etc/prometheus/keys/public.pem" \
         "/etc/prometheus/certs/gateway.crt" "/etc/prometheus/certs/gateway.key" \
         "/etc/prometheus/certs/auth.crt" "/etc/prometheus/certs/auth.key"; do
    [[ -f "${f}" || -L "${f}" ]] || MISSING_KEYS+=("$(basename "${f}")")
done
if [[ "${#MISSING_KEYS[@]}" -eq 0 ]]; then
    _pass "step-7/keys-certs" "private/public keys and gateway/auth TLS certs present"
else
    _fail "step-7/keys-certs" "missing: ${MISSING_KEYS[*]} - run install-ubuntu-dgx.sh STEP 7"
fi

_check_cert_ownership() {
    local file="$1" expected_uid="$2" expected_mode="$3"
    local base actual_uid actual_mode
    base="$(basename "${file}")"
    if [[ ! -f "${file}" ]]; then
        _fail "step-7/cert-ownership" "${base}: file not found"
        return
    fi
    actual_uid="$(stat --format='%u' "${file}" 2>/dev/null || echo 'unknown')"
    actual_mode="$(stat --format='%a' "${file}" 2>/dev/null || echo 'unknown')"
    if [[ "${actual_uid}" == "${expected_uid}" ]]; then
        _pass "step-7/cert-ownership" "${base}: owner UID ${actual_uid}"
    else
        _fail "step-7/cert-ownership" "${base}: owner UID is ${actual_uid}, expected ${expected_uid}"
    fi
    if [[ "${actual_mode}" == "${expected_mode}" ]]; then
        _pass "step-7/cert-mode" "${base}: mode ${actual_mode}"
    else
        _fail "step-7/cert-mode" "${base}: mode is ${actual_mode}, expected ${expected_mode}"
    fi
}

_check_cert_ownership "/etc/prometheus/certs/gateway.crt" "1000" "644"
_check_cert_ownership "/etc/prometheus/certs/gateway.key" "1000" "600"
_check_cert_ownership "/etc/prometheus/certs/auth.crt"    "1001" "644"
_check_cert_ownership "/etc/prometheus/certs/auth.key"    "1001" "600"

AUTH_SYMLINK="/etc/prometheus/keys/private.pem"
if [[ -L "${AUTH_SYMLINK}" ]]; then
    AUTH_TARGET="$(readlink -f "${AUTH_SYMLINK}")"
    if [[ -f "${AUTH_TARGET}" ]]; then
        TARGET_UID="$(stat --format='%u' "${AUTH_TARGET}" 2>/dev/null || echo 'unknown')"
        TARGET_MODE="$(stat --format='%a' "${AUTH_TARGET}" 2>/dev/null || echo 'unknown')"
        if [[ "${TARGET_UID}" == "1001" || "${TARGET_UID}" == "$(id -u "${LLMOPS_USER}" 2>/dev/null)" ]]; then
            _pass "step-7/private.pem-target" "target owner UID ${TARGET_UID}"
        else
            _fail "step-7/private.pem-target" "target owner UID is ${TARGET_UID}, expected 1001 or ${LLMOPS_USER}"
        fi
        if [[ "${TARGET_MODE}" == "600" ]]; then
            _pass "step-7/private.pem-target" "target mode ${TARGET_MODE}"
        else
            _fail "step-7/private.pem-target" "target mode is ${TARGET_MODE}, expected 600"
        fi
    else
        _fail "step-7/private.pem-target" "symlink target does not exist: ${AUTH_TARGET}"
    fi
else
    _fail "step-7/private.pem-symlink" "/etc/prometheus/keys/private.pem is not a symlink"
fi

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
        _fail "env-files:${rel}" "file not found - run install-ubuntu-dgx.sh STEP 8"
        env_ok=false
        continue
    fi
    bad_lines="$(grep -n '<replace-\|<placeholder\|replace-with-' "${env_file}" 2>/dev/null || true)"
    if [[ -n "${bad_lines}" ]]; then
        first="$(echo "${bad_lines}" | head -1)"
        _fail "env-files:${rel}" "placeholder on line ${first%%:*}"
        env_ok=false
    else
        _pass "env-files:${rel}"
    fi
done

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
    _fail "step-8/auth-env" "auth-service/.env missing - run install-ubuntu-dgx.sh STEP 8 first"
fi
unset -f _check_auth_var

echo "[ step-9/secrets ] Checking required secrets are injected..."
AUTH_ENV_FILE="${PROJECT_DIR}/auth-service/.env"
ROOT_ENV_FILE="${PROJECT_DIR}/.env"

_check_secret() {
    local file="$1" key="$2"
    local val
    val="$(grep "^${key}=" "${file}" 2>/dev/null | cut -d= -f2- || true)"
    if [[ -z "${val}" || "${val}" == *"replace-"* || "${val}" == *"<replace"* ]]; then
        _fail "step-9/secrets:${key}" "not set or still a placeholder - run install-ubuntu-dgx.sh STEP 9"
    else
        _pass "step-9/secrets:${key}"
    fi
}

if [[ -f "${AUTH_ENV_FILE}" ]]; then
    _check_secret "${AUTH_ENV_FILE}" "AUTH_ADMIN_API_KEY"
    _check_secret "${AUTH_ENV_FILE}" "SHARE_TOKEN_ENCRYPTION_KEY"
else
    _fail "step-9/secrets" "auth-service/.env missing - run install-ubuntu-dgx.sh STEP 8 first"
fi
if [[ -f "${ROOT_ENV_FILE}" ]]; then
    _check_secret "${ROOT_ENV_FILE}" "GRAFANA_SECRET_KEY"
    _check_secret "${ROOT_ENV_FILE}" "GRAFANA_ADMIN_PASSWORD"
else
    _fail "step-9/secrets" ".env missing - run install-ubuntu-dgx.sh STEP 8 first"
fi

echo "[ step-10/proxy ] Checking proxy configuration..."
if [[ -f /etc/environment ]] && grep -q '^http_proxy=\|^HTTP_PROXY=' /etc/environment 2>/dev/null; then
    PROXY_VAL="$(grep '^http_proxy=\|^HTTP_PROXY=' /etc/environment | head -1 | cut -d= -f2-)"
    _pass "step-10/proxy" "configured: ${PROXY_VAL}"
else
    _info_result "step-10/proxy" "not configured (optional)"
fi

echo "[ podman ] Checking Podman runtime..."
if podman info >/dev/null 2>&1; then
    PODMAN_VER="$(podman --version 2>/dev/null || echo podman)"
    _pass "podman/info" "${PODMAN_VER}"
else
    _fail "podman/info" "podman info failed"
fi

if [[ -f "${PROJECT_DIR}/podman-compose.yml" ]]; then
    if (cd "${PROJECT_DIR}" && podman compose -f podman-compose.yml config >/dev/null 2>&1); then
        _pass "podman/compose-config" "podman-compose.yml valid"
    elif command -v podman-compose >/dev/null 2>&1 && (cd "${PROJECT_DIR}" && podman-compose -f podman-compose.yml config >/dev/null 2>&1); then
        _pass "podman/compose-config" "podman-compose.yml valid"
    else
        _fail "podman/compose-config" "compose config failed"
    fi
else
    _fail "podman/compose-file" "${PROJECT_DIR}/podman-compose.yml not found"
fi

echo "[ runtime/processes ] Checking runtime processes..."
if pgrep -f 'llama-server' >/dev/null 2>&1; then
    _pass "runtime/llama-process" "running"
else
    _info_result "runtime/llama-process" "not running"
fi

if pgrep -f 'pmgr serve' >/dev/null 2>&1; then
    _pass "runtime/pmgr-process" "running"
else
    _info_result "runtime/pmgr-process" "not running"
fi

echo "[ llama-health ] Checking llama-server health endpoint..."
LLAMA_RESP="$(curl -sf --max-time 5 http://127.0.0.1:8080/health 2>/dev/null || echo "")"
if echo "${LLAMA_RESP}" | grep -q '"status"'; then
    _pass "llama-health" "${LLAMA_RESP}"
else
    _fail "llama-health" "no response from http://127.0.0.1:8080/health"
fi

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

echo "[ oauth2 ] Running OAuth2 smoke-test..."
ADMIN_KEY="$(grep '^AUTH_ADMIN_API_KEY=' "${AUTH_ENV_FILE}" 2>/dev/null | cut -d= -f2- || true)"

if [[ -z "${ADMIN_KEY}" || "${ADMIN_KEY}" == *"replace-"* || "${ADMIN_KEY}" == *"<replace"* ]]; then
    _fail "oauth2" "AUTH_ADMIN_API_KEY not set or placeholder in auth-service/.env"
else
    AUTH_BASE="https://localhost:9000"
    GW_BASE="https://localhost:8000"
    CACERT_AUTH="${AUTH_CACERT}"
    CACERT_GW="${GW_CACERT}"
    TEST_CLIENT_NAME="validate-smoke-test-$(date +%s)"

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
        _fail "oauth2" "failed to register test client"
    else
        TOKEN_RESP="$(curl -sf --max-time 10 \
            ${CACERT_AUTH:+--cacert "${CACERT_AUTH}"} \
            -X POST "${AUTH_BASE}/oauth2/token" \
            -H "Content-Type: application/x-www-form-urlencoded" \
            -d "grant_type=client_credentials&client_id=${CLIENT_ID}&client_secret=${CLIENT_SECRET}&scope=inference:read" \
            2>/dev/null || echo "")"

        ACCESS_TOKEN="$(echo "${TOKEN_RESP}" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('access_token',''))" 2>/dev/null || echo "")"

        if [[ -z "${ACCESS_TOKEN}" ]]; then
            _fail "oauth2" "token request failed"
        else
            MODELS_CODE="$(curl -so /dev/null -w '%{http_code}' --max-time 10 \
                ${CACERT_GW:+--cacert "${CACERT_GW}"} \
                -H "Authorization: Bearer ${ACCESS_TOKEN}" \
                "${GW_BASE}/v1/models" 2>/dev/null || echo "000")"

            if [[ "${MODELS_CODE}" == "200" ]]; then
                _pass "oauth2" "register -> token -> GET /v1/models HTTP 200"
            else
                _fail "oauth2" "GET /v1/models returned HTTP ${MODELS_CODE}"
            fi
        fi

        curl -sf --max-time 5 \
            ${CACERT_AUTH:+--cacert "${CACERT_AUTH}"} \
            -X DELETE "${AUTH_BASE}/admin/clients/${CLIENT_ID}" \
            -H "X-Admin-Key: ${ADMIN_KEY}" \
            >/dev/null 2>&1 || true

        unset ACCESS_TOKEN CLIENT_SECRET ADMIN_KEY
    fi
fi

_print_results

if [[ "${FAIL_COUNT}" -gt 0 ]]; then
    exit 1
fi
exit 0
