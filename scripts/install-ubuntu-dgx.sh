#!/usr/bin/env bash
# scripts/install-ubuntu-dgx.sh
# Ubuntu 24.04 / NVIDIA DGX Spark equivalent of scripts/install-rhel.sh
# Base source: Prometheus RHEL installer provided by user.
# Main adaptations:
#   - dnf -> apt
#   - gcc-c++ -> g++
#   - openblas-devel -> libopenblas-dev
#   - useradd -> adduser
#   - podman-compose via apt/pipx, not system pip
#   - uv via pipx, not system pip
#   - remove SELinux chcon/restorecon actions

# -----------------------------------------------------------------------------
# Quick reference
#
# This script has two modes:
# 1) Install mode (default): bootstrap host + clone/update repo + configure env.
# 2) Deploy mode (--deploy): idempotent refresh for an already installed host.
#
# Typical usage:
#   bash scripts/install-ubuntu-dgx.sh
#   bash scripts/install-ubuntu-dgx.sh --proxy=http://proxy.internal:8080
#   bash scripts/install-ubuntu-dgx.sh --project-dir=/opt/prometheus-ai-inference --user=llmops
#   bash scripts/install-ubuntu-dgx.sh --deploy
#   bash scripts/install-ubuntu-dgx.sh --deploy --force
#
# Notes:
# - --force means different things by mode:
#   * install mode: re-run steps from scratch where applicable.
#   * deploy mode: bypass state checks (for example uv.lock hash guard).
# - Secrets are generated only if placeholders/missing values are detected,
#   unless --force is provided.
# -----------------------------------------------------------------------------

set -euo pipefail

TOTAL_STEPS=10
NO_PROXY_LIST="localhost,127.0.0.1,.internal,gateway,manager,auth-service,redis,loki,promtail,tempo,grafana,10.89.0.1"

DEPLOY_MODE=false
PROXY_URL=""
GIT_CREDENTIALS_FILE=""
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="/opt/prometheus-ai-inference"
LLMOPS_USER="llmops"
FORCE=false

_print_help() {
    cat <<'EOF'
Prometheus installer for Ubuntu 24.04 / NVIDIA DGX Spark

Usage:
  bash scripts/install-ubuntu-dgx.sh [options]

Modes:
  default (install mode)
      Full host bootstrap and project setup.

  --deploy
      Idempotent deploy for an existing installation:
      - git pull
      - uv sync (only if uv.lock changed unless --force)
      - podman compose down/up --build
      - optional pmgr restart
      - deploy state update

Options:
  --deploy
      Run in deploy mode.

  --proxy=URL
      Configure runtime proxy env vars for this execution.

  --git-credentials=FILE
      Source file to append credentials to ~/.netrc (must be mode 600).

  --project-dir=PATH
      Target project directory (default: /opt/prometheus-ai-inference).

  --user=NAME
      Linux owner user for installation actions (default: llmops).

  --force
      Re-run guarded steps.

  --help
      Show this help.
EOF
}

for arg in "$@"; do
    case "${arg}" in
        --deploy)             DEPLOY_MODE=true ;;
        --proxy=*)            PROXY_URL="${arg#*=}" ;;
        --git-credentials=*)  GIT_CREDENTIALS_FILE="${arg#*=}" ;;
        --project-dir=*)      PROJECT_DIR="${arg#*=}" ;;
        --user=*)             LLMOPS_USER="${arg#*=}" ;;
        --force)              FORCE=true ;;
        --help)
            _print_help
            exit 0
            ;;
        *)
            echo "ERROR: Unknown option: ${arg}" >&2
            echo "       Run with --help for usage." >&2
            exit 1
            ;;
    esac
done

if [[ -n "${PROXY_URL}" ]]; then
    # Export lowercase and uppercase variants because different tools/services
    # may read one form or the other.
    export http_proxy="${PROXY_URL}"
    export https_proxy="${PROXY_URL}"
    export HTTP_PROXY="${PROXY_URL}"
    export HTTPS_PROXY="${PROXY_URL}"
    export NO_PROXY="${NO_PROXY_LIST}"
    export no_proxy="${NO_PROXY_LIST}"
fi

LOG_DIR="${PROJECT_DIR}/logs"
LOG_FILE="${LOG_DIR}/install-ubuntu-dgx.log"
if [[ ! -d "${LOG_DIR}" ]]; then
    mkdir -p "${LOG_DIR}" 2>/dev/null || {
        sudo mkdir -p "${LOG_DIR}"
        id "${LLMOPS_USER}" &>/dev/null && sudo chown "${LLMOPS_USER}:${LLMOPS_USER}" "${LOG_DIR}" || true
    }
fi
exec > >(tee -a "${LOG_FILE}") 2>&1

_now() { date '+%Y-%m-%d %H:%M:%S'; }

CURRENT_STEP=0
_step() {
    CURRENT_STEP=$(( CURRENT_STEP + 1 ))
    echo ""
    echo "=================================================================="
    printf "[STEP %d/%d] %s - %s\n" "${CURRENT_STEP}" "${TOTAL_STEPS}" "$(_now)" "$1"
    echo "=================================================================="
}

_info() { echo "  > $*"; }
_ok()   { echo "  OK: $*"; }
_warn() { echo "  WARNING: $*"; }

_on_error() {
    local exit_code=$?
    echo "" >&2
    echo "ERROR: step ${CURRENT_STEP} failed (exit ${exit_code}) - see ${LOG_FILE}" >&2
    echo "Last 20 lines of log:" >&2
    tail -n 20 "${LOG_FILE}" >&2 || true
    exit "${exit_code}"
}
trap '_on_error' ERR

_set_env_var() {
    local file="$1" key="$2" value="$3"
    if grep -q "^${key}=" "${file}" 2>/dev/null; then
        _SENV_KEY="${key}" _SENV_VAL="${value}" _SENV_FILE="${file}" \
        python3 -c "
import os, re
k = os.environ['_SENV_KEY']
v = os.environ['_SENV_VAL']
f = os.environ['_SENV_FILE']
txt = open(f).read()
txt = re.sub(r'^' + re.escape(k) + r'=.*', k + '=' + v, txt, flags=re.M)
open(f, 'w').write(txt)
"
    else
        echo "${key}=${value}" >> "${file}"
    fi
}

_set_etc_env() {
    local key="$1" value="$2"
    if sudo grep -q "^${key}=" /etc/environment 2>/dev/null; then
        sudo sed -i "s|^${key}=.*|${key}=${value}|" /etc/environment
    else
        echo "${key}=${value}" | sudo tee -a /etc/environment >/dev/null
    fi
}

_ensure_local_bin_path() {
    export PATH="${HOME}/.local/bin:${PATH}"
}

echo ""
if [[ "${DEPLOY_MODE}" == "true" ]]; then
    DEPLOY_START_TIME="${SECONDS}"
    GIT_VERSION="$(git -C "${PROJECT_DIR}" describe --tags --abbrev=0 2>/dev/null || echo "untagged")"
    GIT_SHORT="$(git -C "${PROJECT_DIR}" rev-parse --short HEAD 2>/dev/null || echo "unknown")"
    echo "╔══════════════════════════════════════════════════════════════════════╗"
    echo "║  Prometheus — DEPLOY mode                                           ║"
    printf  "║  Version : %-20s  Commit : %-18s  ║\n" "${GIT_VERSION}" "${GIT_SHORT}"
    echo "║  memory/specs/024-idempotent-deploy.md                             ║"
    echo "╚══════════════════════════════════════════════════════════════════════╝"
    echo "  project-dir : ${PROJECT_DIR}"
    echo "  llmops-user : ${LLMOPS_USER}"
    echo "  proxy       : ${PROXY_URL:-<none>}"
    echo "  git-creds   : ${GIT_CREDENTIALS_FILE:-<none>}"
    echo "  force       : ${FORCE}  (--force to bypass state file)"
    echo "  log file    : ${LOG_FILE}"
    echo ""
else
    echo "╔══════════════════════════════════════════════════════════════════════╗"
    echo "║  Prometheus — NVidia DGX Spark installer                             ║"
    echo "║  memory/specs/023-redhat-compatibility.md                            ║"
    echo "╚══════════════════════════════════════════════════════════════════════╝"
    echo "  project-dir : ${PROJECT_DIR}"
    echo "  llmops-user : ${LLMOPS_USER}"
    echo "  proxy       : ${PROXY_URL:-<none>}"
    echo "  git-creds   : ${GIT_CREDENTIALS_FILE:-<none>}"
    echo "  force       : ${FORCE}  (--force to re-run all steps from scratch)"
    echo "  log file    : ${LOG_FILE}"
    echo ""
fi

if [[ "$(uname -s)" != "Linux" ]]; then
    echo "ERROR: This script is designed for Linux. Detected: $(uname -s)" >&2
    exit 1
fi

# DEPLOY MODE
if [[ "${DEPLOY_MODE}" == "true" ]]; then
    # Deploy mode is intentionally short and idempotent. It assumes the host
    # is already bootstrapped and focuses on refresh/restart operations.
    DEPLOY_TOTAL_STEPS=6
    DEPLOY_STEP=0
    _dstep() {
        DEPLOY_STEP=$(( DEPLOY_STEP + 1 ))
        echo ""
        echo "=================================================================="
        printf "[DEPLOY %d/%d] %s - %s\n" "${DEPLOY_STEP}" "${DEPLOY_TOTAL_STEPS}" "$(_now)" "$1"
        echo "=================================================================="
    }

    DEPLOY_STATE_FILE="${PROJECT_DIR}/.deploy-state"

    _dstep "Update repository (git pull)"
    git config --global --add safe.directory "${PROJECT_DIR}" 2>/dev/null || true
    DIRTY_STATUS="$(git -C "${PROJECT_DIR}" status --porcelain 2>/dev/null || true)"
    if [[ -n "${DIRTY_STATUS}" ]]; then
        # Preserve current working tree before cleaning to avoid accidental
        # loss when the host contains local edits.
        BACKUP_TIMESTAMP="$(date -u '+%Y%m%d-%H%M%S')"
        BACKUP_ARCHIVE="${PROJECT_DIR}/../prometheus-backup-${BACKUP_TIMESTAMP}.tar.gz"
        _warn "Working tree is dirty - creating backup: ${BACKUP_ARCHIVE}"
        tar -czf "${BACKUP_ARCHIVE}" \
            --exclude='*.env' \
            --exclude='.deploy-state' \
            -C "$(dirname "${PROJECT_DIR}")" "$(basename "${PROJECT_DIR}")"
        chmod 600 "${BACKUP_ARCHIVE}"
        git -C "${PROJECT_DIR}" clean -fd
        git -C "${PROJECT_DIR}" checkout -- .
    fi
    git -C "${PROJECT_DIR}" pull --ff-only
    find "${PROJECT_DIR}/gateway" "${PROJECT_DIR}/auth-service" "${PROJECT_DIR}/runtime" \
        -type f -name '*.sh' -exec chmod 0755 {} + 2>/dev/null || true
    
    GIT_VERSION="$(git -C "${PROJECT_DIR}" describe --tags --abbrev=0 2>/dev/null || echo "untagged")"
    GIT_SHORT="$(git -C "${PROJECT_DIR}" rev-parse --short HEAD 2>/dev/null || echo "unknown")"
    _ok "Repository updated -> ${GIT_VERSION} (${GIT_SHORT})"

    _dstep "Sync Python dependencies (uv sync — skipped if uv.lock unchanged)"
    
    _ensure_local_bin_path
    
    UVLOCK_FILE="${PROJECT_DIR}/uv.lock"
    CURRENT_LOCK_HASH="sha256:$(sha256sum "${UVLOCK_FILE}" 2>/dev/null | awk '{print $1}' || echo 'none')"
    STORED_LOCK_HASH=""
    if [[ -f "${DEPLOY_STATE_FILE}" ]]; then
        STORED_LOCK_HASH="$(grep '^LAST_UVSYNC_LOCK_HASH=' "${DEPLOY_STATE_FILE}" 2>/dev/null | cut -d= -f2- || true)"
    fi
    
    if [[ "${FORCE}" == "true" || "${CURRENT_LOCK_HASH}" != "${STORED_LOCK_HASH}" ]]; then
        _info "uv.lock changed (or --force) — running uv sync..."
        (cd "${PROJECT_DIR}" && uv sync)
        _ok "uv sync complete"
        UV_SYNC_RAN=true
    else
        _ok "uv.lock unchanged - skipping uv sync"
        UV_SYNC_RAN=false
    fi

    _dstep "Rebuild and restart containers (podman compose down + up --build)"
    _info "Stopping containers (graceful — no error if none running)..."
    (cd "${PROJECT_DIR}" && podman compose -f podman-compose.yml down 2>/dev/null || true)
    _info "Rebuilding and starting containers..."
    (cd "${PROJECT_DIR}" && podman compose -f podman-compose.yml up --build -d)
    _ok "Containers rebuilt and started"

    _dstep "Restart Manager API (pmgr serve)"
    PMGR_PID="$(pgrep -f 'pmgr serve' 2>/dev/null || true)"
    if [[ -n "${PMGR_PID}" ]]; then
        _info "Sending SIGTERM to pmgr serve (PID ${PMGR_PID})..."
        kill -TERM "${PMGR_PID}" 2>/dev/null || true
        sleep 2
        _info "Restarting pmgr serve in background..."
        (cd "${PROJECT_DIR}" && nohup pmgr serve >> /var/log/prometheus/manager/pmgr.log 2>&1 &)
        _ok "Manager API restarted"
    else
        _ok "pmgr serve not running - skipping restart"
    fi

    _dstep "Check llama-server (manual restart required if running)"
    LLAMA_PID="$(pgrep -f 'llama-server' 2>/dev/null || true)"
    if [[ -n "${LLAMA_PID}" ]]; then
        _warn "llama-server is running (PID ${LLAMA_PID})"
        _warn "It must be restarted manually to pick up code changes:"
        _warn "  kill ${LLAMA_PID}"
        _warn "  source runtime/envs/<model>.env && bash runtime/scripts/start-server.sh"
    else
        _ok "llama-server not running — no restart needed"
    fi

    _dstep "Record deploy state → ${DEPLOY_STATE_FILE}"
    DEPLOY_COMMIT="$(git -C "${PROJECT_DIR}" rev-parse --short HEAD 2>/dev/null || echo 'unknown')"
    DEPLOY_TIMESTAMP="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
    if [[ "${UV_SYNC_RAN}" == "true" ]]; then
        WRITTEN_LOCK_HASH="${CURRENT_LOCK_HASH}"
    elif [[ -n "${STORED_LOCK_HASH}" ]]; then
        WRITTEN_LOCK_HASH="${STORED_LOCK_HASH}"
    else
        WRITTEN_LOCK_HASH="${CURRENT_LOCK_HASH}"
    fi
    cat > "${DEPLOY_STATE_FILE}" <<STATEEOF
# .deploy-state - written by scripts/install-ubuntu-dgx.sh --deploy
LAST_DEPLOY_COMMIT=${DEPLOY_COMMIT}
LAST_DEPLOY_TIMESTAMP=${DEPLOY_TIMESTAMP}
LAST_UVSYNC_LOCK_HASH=${WRITTEN_LOCK_HASH}
STATEEOF
    _ok "Deploy state saved"

    DEPLOY_ELAPSED=$(( SECONDS - DEPLOY_START_TIME ))
    echo ""
    echo "╔══════════════════════════════════════════════════════════════════════╗"
    echo "║  Deploy complete                                                    ║"
    printf  "║  Version : %-20s  Commit : %-18s  ║\n" "${GIT_VERSION}" "${GIT_SHORT}"
    printf  "║  Elapsed : %-5ss                                                 ║\n" "${DEPLOY_ELAPSED}"
    echo "║  Next    : bash scripts/validate.sh                                ║"
    echo "╚══════════════════════════════════════════════════════════════════════╝"
    echo ""
    echo "  Log saved to : ${LOG_FILE}"
    echo ""
    exit 0
fi

# INSTALL MODE
_step "Clone or update repository"

REPO_URL="https://github.com/<your-username>/prometheus-ai-inference.git"

if [[ -n "${GIT_CREDENTIALS_FILE}" ]]; then
    # Security check: do not accept world/group-readable credential files.
    if [[ ! -f "${GIT_CREDENTIALS_FILE}" ]]; then
        echo "ERROR: --git-credentials file not found: ${GIT_CREDENTIALS_FILE}" >&2
        exit 1
    fi
    SRC_PERMS="$(stat -c '%a' "${GIT_CREDENTIALS_FILE}" 2>/dev/null || echo '000')"
    if [[ "${SRC_PERMS}" != "600" ]]; then
        _warn "Credentials file permissions are ${SRC_PERMS} — expected 600. Aborting for safety."
        echo "ERROR: ${GIT_CREDENTIALS_FILE} must have mode 600. Run: chmod 600 ${GIT_CREDENTIALS_FILE}" >&2
        exit 1
    fi
    NETRC_FILE="${HOME}/.netrc"
    NETRC_HOST="github.com"
    if [[ "$(realpath "${GIT_CREDENTIALS_FILE}" 2>/dev/null)" == "$(realpath "${NETRC_FILE}" 2>/dev/null)" ]]; then
        _ok "~/.netrc is already the credentials file - skipping copy"
    else
        if [[ -f "${NETRC_FILE}" ]]; then
            grep -v "machine ${NETRC_HOST}" "${NETRC_FILE}" > "${NETRC_FILE}.tmp" || true
            chmod 600 "${NETRC_FILE}.tmp"
            mv "${NETRC_FILE}.tmp" "${NETRC_FILE}"
        else
            touch "${NETRC_FILE}"
            chmod 600 "${NETRC_FILE}"
        fi
        cat "${GIT_CREDENTIALS_FILE}" >> "${NETRC_FILE}"
        _ok "Git credentials configured in ~/.netrc"
    fi
fi

git config --global --add safe.directory "${PROJECT_DIR}" 2>/dev/null || true

if [[ -d "${PROJECT_DIR}/.git" ]]; then
    if [[ "${FORCE}" == "true" ]]; then
        _info "--force: resetting repository to remote HEAD..."
        git -C "${PROJECT_DIR}" fetch origin
        git -C "${PROJECT_DIR}" reset --hard origin/HEAD
        _ok "Repository reset to remote HEAD"
    else
        _info "Repository already present at ${PROJECT_DIR} — pulling latest..."
        STASH_OUT="$(git -C "${PROJECT_DIR}" stash 2>&1 || true)"
        if echo "${STASH_OUT}" | grep -q "No local changes"; then
            STASH_PUSHED=false
        else
            STASH_PUSHED=true
            _info "Stashed local modifications: ${STASH_OUT}"
        fi
        git -C "${PROJECT_DIR}" pull --ff-only
        if [[ "${STASH_PUSHED}" == "true" ]]; then
            git -C "${PROJECT_DIR}" stash pop || _warn "git stash pop failed - review ${PROJECT_DIR} manually"
        fi
        _ok "Repository updated"
    fi
else
    _info "Cloning ${REPO_URL} → ${PROJECT_DIR}..."
    PARENT_DIR="$(dirname "${PROJECT_DIR}")"
    sudo mkdir -p "${PARENT_DIR}"
    id "${LLMOPS_USER}" &>/dev/null && sudo chown "${LLMOPS_USER}:${LLMOPS_USER}" "${PARENT_DIR}" || true
    SAVED_LOGS=""
    if [[ -d "${PROJECT_DIR}" && ! -d "${PROJECT_DIR}/.git" ]]; then
        _info "Directory exists without .git (created by log setup) — resetting for fresh clone..."
        SAVED_LOGS="$(mktemp -d)"
        [[ -d "${PROJECT_DIR}/logs" ]] && mv "${PROJECT_DIR}/logs" "${SAVED_LOGS}/"
        sudo rm -rf "${PROJECT_DIR}"
    fi
    git clone "${REPO_URL}" "${PROJECT_DIR}"
    if [[ -n "${SAVED_LOGS}" && -d "${SAVED_LOGS}/logs" ]]; then
        mv "${SAVED_LOGS}/logs" "${PROJECT_DIR}/logs"
        rm -rf "${SAVED_LOGS}"
    fi
    _ok "Repository cloned"
fi

_step "Install system packages"

sudo apt update
sudo apt install -y \
    cmake \
    gcc \
    g++ \
    make \
    libopenblas-dev \
    python3 \
    python3-pip \
    python3-venv \
    python3-full \
    git \
    podman \
    pipx \
    openssl

_ok "System packages installed"

if ! command -v podman-compose &>/dev/null && ! podman compose version &>/dev/null; then
    _info "Installing podman-compose..."
    if sudo apt install -y podman-compose; then
        _ok "podman-compose installed via apt"
    else
        pipx install podman-compose
        _ensure_local_bin_path
        _ok "podman-compose installed via pipx"
    fi
fi

_ok "System packages installed"

_step "Create llmops user and project directory"

if id "${LLMOPS_USER}" &>/dev/null; then
    _ok "User '${LLMOPS_USER}' already exists - skipping creation"
else
    _info "Creating user '${LLMOPS_USER}'..."
    sudo adduser --disabled-password --gecos "" "${LLMOPS_USER}"
    _ok "User '${LLMOPS_USER}' created"
fi

sudo usermod -aG sudo "${LLMOPS_USER}" || true
sudo loginctl enable-linger "${LLMOPS_USER}" || true

if [[ "${PROJECT_DIR}" == "/opt/prometheus-ai-inference" ]]; then
    sudo mkdir -p /opt/prometheus-ai-inference
    sudo chown -R "${LLMOPS_USER}:${LLMOPS_USER}" /opt/prometheus-ai-inference
fi

_ok "User and directory ready"

_step "Install uv and sync Python workspace"

_ensure_local_bin_path
if ! command -v uv &>/dev/null; then
    _info "Installing uv via pipx..."
    pipx install uv
    _ensure_local_bin_path
fi

_info "uv version: $(uv --version)"
(cd "${PROJECT_DIR}" && uv sync)
_ok "Python workspace synced - .venv populated"

_step "Build and install llama-server"

if command -v llama-server &>/dev/null && [[ "${FORCE}" != "true" ]]; then
    _ok "llama-server already installed at $(command -v llama-server) — skipping build (use --force to rebuild)"
else
    INSTALL_SCRIPT="${PROJECT_DIR}/runtime/scripts/install-server-ubuntu-dgx.sh"
    if [[ ! -f "${INSTALL_SCRIPT}" ]]; then
        echo "ERROR: ${INSTALL_SCRIPT} not found - is PROJECT_DIR correct?" >&2
        exit 1
    fi
    _info "Delegating to ${INSTALL_SCRIPT}..."
    export INSTALL_PREFIX="${HOME}/.local"
    bash "${INSTALL_SCRIPT}"
    _ensure_local_bin_path
    _ok "llama-server installed at $(command -v llama-server)"
fi

_step "Create host directories (ownership <UID/GID>)"

# UID/GID constants (must match container image definitions)
UID_GATEWAY=1000   # prometheus       (gateway/Dockerfile --uid 1000)
UID_AUTH=1001      # prometheus-auth  (auth-service/Dockerfile --uid 1001)
UID_MANAGER=1002   # pmgr             (runtime/manager/Dockerfile --uid 1002)

sudo mkdir -p /etc/prometheus/keys /etc/prometheus/certs
sudo chown "${LLMOPS_USER}:${LLMOPS_USER}" /etc/prometheus/keys /etc/prometheus/certs
sudo chmod 750 /etc/prometheus/keys /etc/prometheus/certs

_info "Creating /var/lib/prometheus/auth-service..."
sudo mkdir -p /var/lib/prometheus/auth-service
#sudo chown -R "${UID_AUTH}:${UID_AUTH}" /var/lib/prometheus/auth-service
#sudo chmod -R u+rwX,g+rwX /var/lib/prometheus/auth-service
sudo chgrp 1001 /var/lib/prometheus/auth-service
sudo chmod 777 /var/lib/prometheus/auth-service


_info "Creating /srv/prometheus/models..."
sudo mkdir -p /srv/prometheus/models
sudo chown -R "${LLMOPS_USER}:${LLMOPS_USER}" /srv/prometheus/models
sudo chmod -R 750 /srv/prometheus/models

_info "Creating /var/log/prometheus/{gateway,auth-service,manager,runtime,observability}..."
sudo mkdir -p \
    /var/log/prometheus/gateway \
    /var/log/prometheus/auth-service \
    /var/log/prometheus/manager \
    /var/log/prometheus/runtime/logs \
    /var/log/prometheus/observability
sudo chown "${UID_GATEWAY}:${UID_GATEWAY}" /var/log/prometheus/gateway
sudo chown "${UID_AUTH}:${UID_AUTH}" /var/log/prometheus/auth-service
sudo chown "${UID_MANAGER}:${UID_MANAGER}" /var/log/prometheus/manager
sudo chown "${LLMOPS_USER}:${LLMOPS_USER}" /var/log/prometheus/runtime/logs
sudo chown "${UID_GATEWAY}:${UID_GATEWAY}" /var/log/prometheus/observability
sudo chmod 750 /var/log/prometheus /var/log/prometheus/*

_info "Creating /var/run/prometheus/runtime/run..."
sudo mkdir -p /var/run/prometheus/runtime/run
sudo chown -R "${LLMOPS_USER}:${LLMOPS_USER}" /var/run/prometheus/runtime
sudo chmod -R u+rwx,g+rx,o-rwx /var/run/prometheus/runtime

_info "Making runtime shell scripts executable..."
sudo find "${PROJECT_DIR}/gateway" "${PROJECT_DIR}/auth-service" "${PROJECT_DIR}/runtime" \
    -type f -name '*.sh' -exec chmod 0755 {} + 2>/dev/null || true

_ok "Host directories created"

_step "Generate RSA keypair and self-signed TLS certificate"

PRIV_KEY="/etc/prometheus/keys/private_2026-q1.pem"
PUB_KEY="/etc/prometheus/keys/public_2026-q1.pem"
GW_CERT="/etc/prometheus/certs/gateway.crt"
GW_KEY="/etc/prometheus/certs/gateway.key"
AUTH_CERT="/etc/prometheus/certs/auth.crt"
AUTH_KEY="/etc/prometheus/certs/auth.key"

if [[ -f "${PRIV_KEY}" && -f "${PUB_KEY}" && "${FORCE}" != "true" ]]; then
    _ok "RSA keypair already exists - skipping generation"
else
    _info "Generating RSA-2048 keypair..."
    sudo openssl genpkey -algorithm RSA -out "${PRIV_KEY}" -pkeyopt rsa_keygen_bits:2048
    sudo openssl rsa -in "${PRIV_KEY}" -pubout -out "${PUB_KEY}"
    _ok "RSA keypair generated"
fi

sudo chown "${UID_AUTH}:${UID_AUTH}" "${PRIV_KEY}" "${PUB_KEY}"
sudo chmod 644 "${PRIV_KEY}"
sudo chmod 644 "${PUB_KEY}"

sudo ln -sf "${PRIV_KEY}" /etc/prometheus/keys/private.pem 2>/dev/null || true
sudo ln -sf "${PUB_KEY}" /etc/prometheus/keys/public.pem 2>/dev/null || true

HOSTNAME_FQDN="$(hostname -f 2>/dev/null || hostname)"
if [[ "${HOSTNAME_FQDN}" != *".internal" && "${HOSTNAME_FQDN}" != "localhost" ]]; then
    _warn "Self-signed certificates are for development/test only."
    _warn "Hostname '${HOSTNAME_FQDN}' does not end in '.internal'."
    _warn "Use a CA-issued certificate in production."
fi

for pair in "${GW_CERT}:${GW_KEY}" "${AUTH_CERT}:${AUTH_KEY}"; do
    cert="${pair%%:*}"
    key="${pair##*:}"
    cn_label="$(basename "${cert}" .crt)"

    if [[ "${cert}" == "${GW_CERT}" ]]; then
        _tls_uid="${UID_GATEWAY}"
    else
        _tls_uid="${UID_AUTH}"
    fi

    if [[ -f "${cert}" && -f "${key}" && "${FORCE}" != "true" ]]; then
        _ok "TLS cert ${cert} already exists — skipping generation"
    else
        _info "Generating self-signed TLS cert for ${cn_label}..."
        sudo openssl req -x509 -newkey rsa:2048 -nodes \
            -keyout "${key}" -out "${cert}" \
            -days 365 \
            -subj "/CN=${HOSTNAME_FQDN}" \
            -addext "subjectAltName=DNS:${HOSTNAME_FQDN},DNS:localhost,IP:127.0.0.1"
        _ok "TLS cert generated: ${cert} (${cn_label})"
    fi

    sudo chown "${_tls_uid}:${_tls_uid}" "${cert}" "${key}"
    sudo chmod 644 "${cert}"
    sudo chmod 644 "${key}"
    
    _ok "TLS ownership fixed: ${cert} -> ${_tls_uid}:${_tls_uid}"
done

_step "Copy .env templates"

_copy_env() {
    local src="$1" dst="$2"
    if [[ -f "${dst}" && "${FORCE}" != "true" ]]; then
        _ok "${dst} already exists - skipping"
    else
        cp "${src}" "${dst}"
        _ok "Copied ${src} -> ${dst}"
    fi
    chmod 600 "${dst}"
}

_copy_env "${PROJECT_DIR}/.env.redhat.example"         "${PROJECT_DIR}/.env"
_copy_env "${PROJECT_DIR}/gateway/.env.podman.example" "${PROJECT_DIR}/gateway/.env"
_copy_env "${PROJECT_DIR}/auth-service/.env.example"   "${PROJECT_DIR}/auth-service/.env"

AUTH_ENV="${PROJECT_DIR}/auth-service/.env"
_set_env_var "${AUTH_ENV}" "AUTH_DB_URL"                "sqlite+aiosqlite:////data/auth.db"
_set_env_var "${AUTH_ENV}" "AUTH_PRIVATE_KEY_FILE"      "/run/secrets/jwt_private_key.pem"
_set_env_var "${AUTH_ENV}" "AUTH_PUBLIC_KEY_FILE"       "/run/secrets/jwt_public_key.pem"
_set_env_var "${AUTH_ENV}" "AUTH_REVOCATION_REDIS_URL"  "redis://redis:6379/0"
_set_env_var "${AUTH_ENV}" "AUTH_RATE_LIMIT_RPM"        "10"


_set_env_var "${PROJECT_DIR}/.env" "JWT_PUBLIC_KEY_HOST_PATH"  "/etc/prometheus/keys/public.pem"
_set_env_var "${PROJECT_DIR}/.env" "JWT_PRIVATE_KEY_HOST_PATH" "/etc/prometheus/keys/private.pem"
_set_env_var "${PROJECT_DIR}/.env" "TLS_CERT_HOST_PATH"        "${GW_CERT}"
_set_env_var "${PROJECT_DIR}/.env" "TLS_KEY_HOST_PATH"         "${GW_KEY}"
_set_env_var "${PROJECT_DIR}/.env" "AUTH_TLS_CERT_HOST_PATH"   "${AUTH_CERT}"
_set_env_var "${PROJECT_DIR}/.env" "AUTH_TLS_KEY_HOST_PATH"    "${AUTH_KEY}"

_set_env_var "${PROJECT_DIR}/.env" "AUTH_DB_HOST_PATH"        "/var/lib/prometheus/auth-service"
_set_env_var "${PROJECT_DIR}/.env" "CONTAINER_LOG_HOST_PATH"  "/var/log/prometheus"
_set_env_var "${PROJECT_DIR}/.env" "MANAGER_LOG_HOST_PATH"    "/var/log/prometheus/manager"
_set_env_var "${PROJECT_DIR}/.env" "MANAGER_PID_ROOT"         "/var/run/prometheus/runtime/run"
_set_env_var "${PROJECT_DIR}/.env" "MANAGER_LOG_ROOT"         "/var/log/prometheus/runtime/logs"

_ok ".env files ready"

_step "Generate and inject secrets"

_inject_secret() {
    local file="$1" key="$2" length="${3:-32}" mode="${4:-hex}"
    current_val="$(grep "^${key}=" "${file}" 2>/dev/null | cut -d= -f2- || true)"
    # Replace placeholders/empty values with secure random strings.
    if [[ "${current_val}" == *"replace-"* || "${current_val}" == *"<replace"* || -z "${current_val}" || "${FORCE}" == "true" ]]; then
        if [[ "${mode}" == "base64" ]]; then
            secret="$(openssl rand -base64 "${length}")"
        else
            secret="$(openssl rand -hex "${length}")"
        fi
        _set_env_var "${file}" "${key}" "${secret}"
        unset secret
        _ok "${key} injected"
    else
        _ok "${key} already set - skipping"
    fi
}

AUTH_ENV="${PROJECT_DIR}/auth-service/.env"
ROOT_ENV="${PROJECT_DIR}/.env"

_inject_secret "${AUTH_ENV}" "AUTH_ADMIN_API_KEY"          32 hex
_inject_secret "${AUTH_ENV}" "SHARE_TOKEN_ENCRYPTION_KEY"  32 hex
_inject_secret "${ROOT_ENV}" "GRAFANA_SECRET_KEY"          32 hex
_inject_secret "${ROOT_ENV}" "GRAFANA_ADMIN_PASSWORD"      16 base64

_ok "Secrets injected"

_step "Proxy configuration and final summary"

if [[ -n "${PROXY_URL}" ]]; then
    _info "Writing proxy settings to /etc/environment..."
    _set_etc_env "http_proxy"  "${PROXY_URL}"
    _set_etc_env "https_proxy" "${PROXY_URL}"
    _set_etc_env "HTTP_PROXY"  "${PROXY_URL}"
    _set_etc_env "HTTPS_PROXY" "${PROXY_URL}"
    _set_etc_env "NO_PROXY"    "${NO_PROXY_LIST}"
    _set_etc_env "no_proxy"    "${NO_PROXY_LIST}"

    _info "Writing proxy settings to ${PROJECT_DIR}/.env..."
    _set_env_var "${ROOT_ENV}" "http_proxy"  "${PROXY_URL}"
    _set_env_var "${ROOT_ENV}" "https_proxy" "${PROXY_URL}"
    _set_env_var "${ROOT_ENV}" "HTTP_PROXY"  "${PROXY_URL}"
    _set_env_var "${ROOT_ENV}" "HTTPS_PROXY" "${PROXY_URL}"
    _set_env_var "${ROOT_ENV}" "NO_PROXY"    "${NO_PROXY_LIST}"
    _set_env_var "${ROOT_ENV}" "no_proxy"    "${NO_PROXY_LIST}"

    _ok "Proxy configured: ${PROXY_URL}"
else
    _info "No proxy specified - skipping proxy configuration"
fi

# ── Final summary ──────────────────────────────────────────────────────────────
echo ""
echo "╔══════════════════════════════════════════════════════════════════════╗"
echo "║  Installation complete                                              ║"
echo "╚══════════════════════════════════════════════════════════════════════╝"
echo ""
echo "  Log saved to : ${LOG_FILE}"
echo ""
echo "  Next steps (manual):"
echo ""
echo "  1. Review and adjust absolute paths in ${PROJECT_DIR}/.env"
echo "     (JWT_PUBLIC_KEY_HOST_PATH, TLS_CERT_HOST_PATH, etc.)"
echo ""
echo "  2. Review gateway/.env — set JWT_ISSUER, MANAGER_URL, OTEL endpoint."
echo ""
echo "  3. Review auth-service/.env — AUTH_JWT_ISSUER must match gateway/.env JWT_ISSUER."
echo ""
echo "  4. Start llama-server:"
echo "       source runtime/envs/<model>.env && bash runtime/scripts/start-server.sh"
echo ""
echo "  5. Start Manager API:"
echo "       pmgr serve"
echo ""
echo "  6. Start Podman containers:"
echo "       podman compose -f podman-compose.yml up --build -d"
echo ""
echo "  7. Validate: bash scripts/validate.sh"
echo ""

