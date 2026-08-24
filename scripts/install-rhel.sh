#!/usr/bin/env bash
# scripts/install-rhel.sh
# Implements: memory/specs/023-redhat-compatibility.md — AC-3, AC-4, AC-5, AC-6, AC-7, AC-8, AC-9, AC-10, AC-11
# Implements: memory/specs/024-idempotent-deploy.md — AC-1 through AC-14
#
# Two modes of operation:
#
#   INSTALL mode (default): idempotent end-to-end installer. Runs all 10 steps.
#     Use on first setup or when reinstalling base software.
#
#   DEPLOY mode (--deploy): fast-path for post-release code updates. Runs only:
#     git pull, uv sync (if uv.lock changed), podman rebuild, service restarts.
#     Safe to run after every feature release. Skips packages, llama-server build,
#     keypair/TLS generation, and .env copying.
#
# Usage:
#   bash scripts/install-rhel.sh [options]
#
# Options:
#   --deploy                   Fast post-release deploy: git pull, uv sync, container rebuild, service restarts
#   --proxy=http://host:port   Export proxy to shell session immediately AND write to /etc/environment and .env files
#   --git-credentials=PATH     Path to netrc-format credentials file (machine github.com login USER password PAT)
#   --project-dir=PATH         Repository root (default: /opt/prometheus-ai-inference)
#   --user=NAME                llmops user to create/use (default: llmops)
#   --force                    Re-run all steps from scratch, even if already completed
#   --help                     Show this help message
#
# Default behaviour (INSTALL): every step is idempotent — it is skipped if the result is
# already present (binary installed, keys exist, .env files exist, etc.).
# Pass --force to redo everything regardless.
#
# DEPLOY mode state file: ${PROJECT_DIR}/.deploy-state
#   Tracks last successful deploy for idempotency (uv.lock hash, commit, timestamp).

set -euo pipefail

# ── Constants ──────────────────────────────────────────────────────────────────
TOTAL_STEPS=10
NO_PROXY_LIST="localhost,127.0.0.1,.internal,gateway,manager,auth-service,redis,loki,promtail,tempo,grafana,10.89.0.1"

# ── Defaults ───────────────────────────────────────────────────────────────────
DEPLOY_MODE=false
PROXY_URL=""
GIT_CREDENTIALS_FILE=""
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="/opt/prometheus-ai-inference"
LLMOPS_USER="llmops"
FORCE=false

# ── Argument parsing ───────────────────────────────────────────────────────────
for arg in "$@"; do
    case "${arg}" in
        --deploy)             DEPLOY_MODE=true ;;
        --proxy=*)            PROXY_URL="${arg#*=}" ;;
        --git-credentials=*)  GIT_CREDENTIALS_FILE="${arg#*=}" ;;
        --project-dir=*)      PROJECT_DIR="${arg#*=}" ;;
        --user=*)             LLMOPS_USER="${arg#*=}" ;;
        --force)              FORCE=true ;;
        --help)
            grep '^#' "$0" | grep -v '#!/' | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *)
            echo "ERROR: Unknown option: ${arg}" >&2
            echo "       Run with --help for usage." >&2
            exit 1
            ;;
    esac
done

# ── Early proxy export ─────────────────────────────────────────────────────────
# Implements: AC-10
# Export proxy to the current shell session immediately so that STEP 1 (git clone)
# and STEP 2 (dnf install) can reach the network through the proxy.
if [[ -n "${PROXY_URL}" ]]; then
    export http_proxy="${PROXY_URL}"
    export https_proxy="${PROXY_URL}"
    export HTTP_PROXY="${PROXY_URL}"
    export HTTPS_PROXY="${PROXY_URL}"
    export NO_PROXY="${NO_PROXY_LIST}"
    export no_proxy="${NO_PROXY_LIST}"
fi

# ── Log setup ──────────────────────────────────────────────────────────────────
LOG_DIR="${PROJECT_DIR}/logs"
LOG_FILE="${LOG_DIR}/install-rhel.log"
# Create the log directory without sudo if possible (e.g. writable temp dir in tests);
# fall back to sudo only when the path requires elevated permissions (e.g. /opt/...).
if [[ ! -d "${LOG_DIR}" ]]; then
    mkdir -p "${LOG_DIR}" 2>/dev/null || {
        sudo mkdir -p "${LOG_DIR}"
        sudo chown "${LLMOPS_USER}:${LLMOPS_USER}" "${LOG_DIR}"
    }
fi

# tee to both stdout and log file; log file captures all subsequent output
exec > >(tee -a "${LOG_FILE}") 2>&1

# ── Helpers ────────────────────────────────────────────────────────────────────
_now() { date '+%Y-%m-%d %H:%M:%S'; }

CURRENT_STEP=0
_step() {
    # Usage: _step "Description"
    CURRENT_STEP=$(( CURRENT_STEP + 1 ))
    echo ""
    echo "╔══════════════════════════════════════════════════════════════════╗"
    printf  "║  [STEP %d/%d] %s — %s\n" "${CURRENT_STEP}" "${TOTAL_STEPS}" "$(_now)" "$1"
    echo "╚══════════════════════════════════════════════════════════════════╝"
}

_info()    { echo "  ▸ $*"; }
_ok()      { echo "  ✔ $*"; }
_warn()    { echo "  ⚠ WARNING: $*"; }

# Trap: on any error, print context and tail of log
_on_error() {
    local exit_code=$?
    echo "" >&2
    echo "ERROR: step ${CURRENT_STEP} failed (exit ${exit_code}) — see ${LOG_FILE}" >&2
    echo "Last 20 lines of log:" >&2
    tail -n 20 "${LOG_FILE}" >&2
    exit "${exit_code}"
}
trap '_on_error' ERR

# Inject or update a key=value line in a file (idempotent)
# Value is passed via environment variable to avoid exposure in ps aux / process list.
# Usage: _set_env_var FILE KEY VALUE
# Sets KEY=VALUE unconditionally in FILE.
# Handles three cases: active line (KEY=...), commented line (#KEY=... or # KEY=...),
# and absent line. All three result in an active, correct KEY=VALUE entry.
_set_env_var() {
    local file="$1" key="$2" value="$3"
    _SENV_KEY="${key}" _SENV_VAL="${value}" _SENV_FILE="${file}" \
    python3 -c "
import os, re
k = os.environ['_SENV_KEY']
v = os.environ['_SENV_VAL']
f = os.environ['_SENV_FILE']
txt = open(f).read()
active_pat    = re.compile(r'^' + re.escape(k) + r'=.*', re.M)
commented_pat = re.compile(r'^#\s*' + re.escape(k) + r'=.*', re.M)
replacement   = k + '=' + v
if active_pat.search(txt):
    txt = active_pat.sub(replacement, txt)
elif commented_pat.search(txt):
    txt = commented_pat.sub(replacement, txt)
else:
    txt = txt + ('' if txt.endswith('\n') else '\n') + replacement + '\n'
open(f, 'w').write(txt)
"
}

# Inject or update a key=value line in /etc/environment (requires sudo)
_set_etc_env() {
    local key="$1" value="$2"
    if sudo grep -q "^${key}=" /etc/environment 2>/dev/null; then
        sudo sed -i "s|^${key}=.*|${key}=${value}|" /etc/environment
    else
        echo "${key}=${value}" | sudo tee -a /etc/environment >/dev/null
    fi
}

# ── Pre-flight ─────────────────────────────────────────────────────────────────
# Implements: memory/specs/024-idempotent-deploy.md — AC-10 (resolve project dir early)
echo ""
if [[ "${DEPLOY_MODE}" == "true" ]]; then
    # Implements: memory/specs/024-idempotent-deploy.md — AC-13
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
    echo "║  Prometheus — RHEL 9.7 installer                                    ║"
    echo "║  memory/specs/023-redhat-compatibility.md                           ║"
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
    echo "ERROR: This script is designed for RHEL 9.7 (Linux). Detected: $(uname -s)" >&2
    exit 1
fi

# ═════════════════════════════════════════════════════════════════════════════
# DEPLOY MODE — fast path (--deploy flag)
# Implements: memory/specs/024-idempotent-deploy.md — AC-1 through AC-14
# ═════════════════════════════════════════════════════════════════════════════
if [[ "${DEPLOY_MODE}" == "true" ]]; then

    DEPLOY_TOTAL_STEPS=6
    DEPLOY_STEP=0

    _dstep() {
        DEPLOY_STEP=$(( DEPLOY_STEP + 1 ))
        echo ""
        echo "╔══════════════════════════════════════════════════════════════════╗"
        printf  "║  [DEPLOY %d/%d] %s — %s\n" "${DEPLOY_STEP}" "${DEPLOY_TOTAL_STEPS}" "$(_now)" "$1"
        echo "╚══════════════════════════════════════════════════════════════════╝"
    }

    DEPLOY_STATE_FILE="${PROJECT_DIR}/.deploy-state"

    # ── Deploy Step 1: dirty-tree check + git pull ──────────────────────────
    # Implements: memory/specs/024-idempotent-deploy.md — AC-1, AC-15
    _dstep "Update repository (git pull)"
    git config --global --add safe.directory "${PROJECT_DIR}" 2>/dev/null || true

    # AC-15: detect dirty working tree (modified tracked files OR untracked files)
    DIRTY_STATUS="$(git -C "${PROJECT_DIR}" status --porcelain 2>/dev/null || true)"
    if [[ -n "${DIRTY_STATUS}" ]]; then
        BACKUP_TIMESTAMP="$(date -u '+%Y%m%d-%H%M%S')"
        BACKUP_ARCHIVE="${PROJECT_DIR}/../prometheus-backup-${BACKUP_TIMESTAMP}.tar.gz"
        _warn "Working tree is dirty — creating backup archive before cleaning:"
        _warn "  ${BACKUP_ARCHIVE}"
        # Exclude gitignored files that survive git clean -fd — they do not need recovery
        # and MUST NOT be bundled with the backup because they may carry live secrets.
        # .env files contain API keys / encryption material; .deploy-state is ephemeral.
        # Restrict the archive to mode 600 so other local users cannot read it.
        tar -czf "${BACKUP_ARCHIVE}" \
            --exclude='*.env' \
            --exclude='.deploy-state' \
            -C "$(dirname "${PROJECT_DIR}")" "$(basename "${PROJECT_DIR}")"
        chmod 600 "${BACKUP_ARCHIVE}"
        _ok  "Backup created (mode 600, .env files excluded): ${BACKUP_ARCHIVE}"
        _warn "Running 'git clean -fd' — untracked files in ${PROJECT_DIR} will be removed"
        git -C "${PROJECT_DIR}" clean -fd
        git -C "${PROJECT_DIR}" checkout -- .
        _ok  "Working tree cleaned"
    fi

    git -C "${PROJECT_DIR}" pull --ff-only

    # Normalize service script permissions after pull (mirrors install-mode STEP 6).
    # Prevents "Permission denied" on .sh files when git preserves non-executable bits.
    # See memory/wiki/deployment.md — service scripts must be executable.
    find "${PROJECT_DIR}/gateway" "${PROJECT_DIR}/auth-service" "${PROJECT_DIR}/runtime" \
        -type f -name '*.sh' -exec chmod 0755 {} + 2>/dev/null || true

    # Capture version after pull (tag may have changed)
    # Implements: memory/specs/024-idempotent-deploy.md — AC-13
    GIT_VERSION="$(git -C "${PROJECT_DIR}" describe --tags --abbrev=0 2>/dev/null || echo "untagged")"
    GIT_SHORT="$(git -C "${PROJECT_DIR}" rev-parse --short HEAD 2>/dev/null || echo "unknown")"
    _ok "Repository updated → ${GIT_VERSION} (${GIT_SHORT})"

    # ── Deploy Step 2: uv sync (gated on uv.lock hash) ──────────────────────
    # Implements: memory/specs/024-idempotent-deploy.md — AC-2, AC-3, AC-5
    _dstep "Sync Python dependencies (uv sync — skipped if uv.lock unchanged)"

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
        _ok "uv.lock unchanged — skipping uv sync"
        UV_SYNC_RAN=false
    fi

    # ── Deploy Step 3: podman compose down + up --build ─────────────────────
    # Implements: memory/specs/024-idempotent-deploy.md — AC-6, AC-7
    _dstep "Rebuild and restart containers (podman compose down + up --build)"
    _info "Stopping containers (graceful — no error if none running)..."
    (cd "${PROJECT_DIR}" && podman compose -f podman-compose.yml down 2>/dev/null || true)
    _info "Rebuilding and starting containers..."
    (cd "${PROJECT_DIR}" && podman compose -f podman-compose.yml up --build -d)
    _ok "Containers rebuilt and started"

    # ── Deploy Step 4: Restart Manager API ──────────────────────────────────
    # Implements: memory/specs/024-idempotent-deploy.md — AC-11
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
        _ok "pmgr serve not running — skipping restart"
    fi

    # ── Deploy Step 5: Notify about llama-server ────────────────────────────
    # Implements: memory/specs/024-idempotent-deploy.md — AC-12
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

    # ── Deploy Step 6: Write deploy state file ───────────────────────────────
    # Implements: memory/specs/024-idempotent-deploy.md — AC-4, AC-5
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

    cat > "${DEPLOY_STATE_FILE}" <<EOF
# .deploy-state — written by scripts/install-rhel.sh --deploy
# Implements: memory/specs/024-idempotent-deploy.md
LAST_DEPLOY_COMMIT=${DEPLOY_COMMIT}
LAST_DEPLOY_TIMESTAMP=${DEPLOY_TIMESTAMP}
LAST_UVSYNC_LOCK_HASH=${WRITTEN_LOCK_HASH}
EOF
    _ok "Deploy state saved"

    # ── Final summary ────────────────────────────────────────────────────────
    # Implements: memory/specs/024-idempotent-deploy.md — AC-14
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

# ─────────────────────────────────────────────────────────────────────────────
# INSTALL MODE — full 10-step installer (--deploy NOT set)
# Implements: memory/specs/024-idempotent-deploy.md — AC-8
# ─────────────────────────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────────────────────────
# STEP 1 — Clone or update repository
# Implements: AC-3
# ─────────────────────────────────────────────────────────────────────────
_step "Clone or update repository"

REPO_URL="https://github.com/<your-username>/prometheus-ai-inference.git"

# Configure git credentials from netrc file if provided (AC-3)
# The file must be in standard netrc format:
#   machine github.com login USER password PAT
# Credentials are written to ~/.netrc with chmod 600 — never echoed or logged.
if [[ -n "${GIT_CREDENTIALS_FILE}" ]]; then
    if [[ ! -f "${GIT_CREDENTIALS_FILE}" ]]; then
        echo "ERROR: --git-credentials file not found: ${GIT_CREDENTIALS_FILE}" >&2
        exit 1
    fi
    # Verify source file permissions are 600 (readable only by owner)
    SRC_PERMS="$(stat -c '%a' "${GIT_CREDENTIALS_FILE}" 2>/dev/null || echo '000')"
    if [[ "${SRC_PERMS}" != "600" ]]; then
        _warn "Credentials file permissions are ${SRC_PERMS} — expected 600. Aborting for safety."
        echo "ERROR: ${GIT_CREDENTIALS_FILE} must have mode 600. Run: chmod 600 ${GIT_CREDENTIALS_FILE}" >&2
        exit 1
    fi
    NETRC_FILE="${HOME}/.netrc"
    NETRC_HOST="github.com"

    # Guard: if source IS ~/.netrc, credentials are already in place — nothing to copy.
    # Without this check, `cat file >> file` corrupts the file (POSIX "input file is output file").
    if [[ "$(realpath "${GIT_CREDENTIALS_FILE}" 2>/dev/null)" == "$(realpath "${NETRC_FILE}" 2>/dev/null)" ]]; then
        _ok "~/.netrc is already the credentials file — skipping copy"
    else
        # Remove any existing entry for github.com to avoid duplicates.
        # chmod 600 the tmp BEFORE mv so ~/.netrc is never world-readable.
        if [[ -f "${NETRC_FILE}" ]]; then
            grep -v "machine ${NETRC_HOST}" "${NETRC_FILE}" > "${NETRC_FILE}.tmp" || true
            chmod 600 "${NETRC_FILE}.tmp"
            mv "${NETRC_FILE}.tmp" "${NETRC_FILE}"
        else
            touch "${NETRC_FILE}"
            chmod 600 "${NETRC_FILE}"
        fi
        # Append the new credentials (never echoed to stdout or log)
        cat "${GIT_CREDENTIALS_FILE}" >> "${NETRC_FILE}"
        _ok "Git credentials configured in ~/.netrc"
    fi
fi

# Git >= 2.35.2 rejects repos owned by a different UID (CVE-2022-24765).
# The installer may run as root while PROJECT_DIR is owned by llmops (set in STEP 3).
# Mark it safe unconditionally — harmless when owner matches.
git config --global --add safe.directory "${PROJECT_DIR}" 2>/dev/null || true

if [[ -d "${PROJECT_DIR}/.git" ]]; then
    if [[ "${FORCE}" == "true" ]]; then
        _info "--force: resetting repository to remote HEAD..."
        git -C "${PROJECT_DIR}" fetch origin
        git -C "${PROJECT_DIR}" reset --hard origin/HEAD
        _ok "Repository reset to remote HEAD"
    else
        _info "Repository already present at ${PROJECT_DIR} — pulling latest..."
        # Stash any local modifications (e.g. uv.lock from a previous partial run)
        # so that --ff-only can proceed cleanly. Stash is popped after the pull.
        STASH_OUT="$(git -C "${PROJECT_DIR}" stash 2>&1 || true)"
        if echo "${STASH_OUT}" | grep -q "No local changes"; then
            STASH_PUSHED=false
        else
            STASH_PUSHED=true
            _info "Stashed local modifications: ${STASH_OUT}"
        fi
        git -C "${PROJECT_DIR}" pull --ff-only
        if [[ "${STASH_PUSHED}" == "true" ]]; then
            git -C "${PROJECT_DIR}" stash pop || _warn "git stash pop failed — review ${PROJECT_DIR} manually"
        fi
        _ok "Repository updated"
    fi
else
    _info "Cloning ${REPO_URL} → ${PROJECT_DIR}..."
    PARENT_DIR="$(dirname "${PROJECT_DIR}")"
    sudo mkdir -p "${PARENT_DIR}"
    # chown only if the user already exists — STEP 3 will fix ownership after creating the user
    id "${LLMOPS_USER}" &>/dev/null && sudo chown "${LLMOPS_USER}:${LLMOPS_USER}" "${PARENT_DIR}" || true
    # The log setup (above) may have created PROJECT_DIR/logs/ before this step.
    # Save logs to a temp location, remove the non-git dir, clone, then restore.
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

# ─────────────────────────────────────────────────────────────────────────
# STEP 2 — Install system packages
# Implements: AC-3
# ─────────────────────────────────────────────────────────────────────────────
_step "Install system packages"

# python3-venv is a Debian/Ubuntu package name — on RHEL 9 venv is included in base python3
PKGS=(cmake gcc gcc-c++ make openblas-devel python3 python3-pip git podman)

# podman-compose may be in a separate repo on some RHEL versions; install best-effort
_info "Installing: ${PKGS[*]}"
sudo dnf install -y "${PKGS[@]}"

# podman-compose: try dnf first, fall back to pip
if ! command -v podman-compose &>/dev/null; then
    _info "podman-compose not in dnf — installing via pip..."
    python3 -m pip install --user podman-compose
fi

_ok "System packages installed"

# ─────────────────────────────────────────────────────────────────────────────
# STEP 3 — Create llmops user and project directory
# Implements: AC-3
# ─────────────────────────────────────────────────────────────────────────────
_step "Create llmops user and project directory"

if id "${LLMOPS_USER}" &>/dev/null; then
    _ok "User '${LLMOPS_USER}' already exists — skipping creation"
else
    _info "Creating user '${LLMOPS_USER}'..."
    sudo useradd --create-home --shell /bin/bash "${LLMOPS_USER}"
    _ok "User '${LLMOPS_USER}' created"
fi

PROJECT_PARENT="$(dirname "${PROJECT_DIR}")"
if [[ "${PROJECT_DIR}" == "/opt/prometheus-ai-inference" ]]; then
    _info "Ensuring /opt/prometheus-ai-inference ownership..."
    sudo mkdir -p /opt/prometheus-ai-inference
    sudo chown -R "${LLMOPS_USER}:${LLMOPS_USER}" /opt/prometheus-ai-inference
fi

_ok "User and directory ready"

# ─────────────────────────────────────────────────────────────────────────────
# STEP 4 — Install uv and sync Python workspace
# Implements: AC-5
# ─────────────────────────────────────────────────────────────────────────────
_step "Install uv and sync Python workspace"

if ! command -v uv &>/dev/null; then
    _info "Installing uv via pip..."
    python3 -m pip install --user uv
    # ensure ~/.local/bin is on PATH for this session
    export PATH="${HOME}/.local/bin:${PATH}"
fi

_info "uv version: $(uv --version)"

_info "Running uv sync from ${PROJECT_DIR}..."
(cd "${PROJECT_DIR}" && uv sync)

_ok "Python workspace synced — .venv populated"

# ─────────────────────────────────────────────────────────────────────────────
# STEP 5 — Build and install llama-server
# Implements: AC-4
# ─────────────────────────────────────────────────────────────────────────────
_step "Build and install llama-server (OpenBLAS / CPU-only)"

if command -v llama-server &>/dev/null && [[ "${FORCE}" != "true" ]]; then
    _ok "llama-server already installed at $(command -v llama-server) — skipping build (use --force to rebuild)"
else
    INSTALL_SCRIPT="${PROJECT_DIR}/runtime/scripts/install-server.sh"
    if [[ ! -f "${INSTALL_SCRIPT}" ]]; then
        echo "ERROR: ${INSTALL_SCRIPT} not found — is PROJECT_DIR correct?" >&2
        exit 1
    fi

    _info "Delegating to ${INSTALL_SCRIPT}..."
    # Pass OpenBLAS flags via env vars picked up by install-server.sh
    export INSTALL_PREFIX="${HOME}/.local"
    bash "${INSTALL_SCRIPT}"

    # Ensure ~/.local/bin is on PATH
    export PATH="${HOME}/.local/bin:${PATH}"
    _ok "llama-server installed at $(command -v llama-server)"
fi

# ─────────────────────────────────────────────────────────────────────────────
# STEP 6 — Create host directories with correct ownership and SELinux labels
# Implements: AC-6
# ─────────────────────────────────────────────────────────────────────────────
_step "Create host directories (ownership + SELinux)"

# UID/GID constants (must match container image definitions)
UID_GATEWAY=1000   # prometheus       (gateway/Dockerfile --uid 1000)
UID_AUTH=1001      # prometheus-auth  (auth-service/Dockerfile --uid 1001)
UID_MANAGER=1002   # pmgr             (runtime/manager/Dockerfile --uid 1002)
sudo mkdir -p /etc/prometheus/keys /etc/prometheus/certs
sudo chown "${LLMOPS_USER}:${LLMOPS_USER}" /etc/prometheus/keys /etc/prometheus/certs
sudo chmod 750 /etc/prometheus/keys /etc/prometheus/certs
sudo chcon -t container_file_t /etc/prometheus/keys /etc/prometheus/certs 2>/dev/null || true

_info "Creating /var/lib/prometheus/auth-service..."
sudo mkdir -p /var/lib/prometheus/auth-service
sudo chown -R "${UID_AUTH}:${UID_AUTH}" /var/lib/prometheus/auth-service
sudo chmod -R u+rwX,g+rwX /var/lib/prometheus/auth-service
sudo chcon -Rt container_file_t /var/lib/prometheus/auth-service 2>/dev/null || true

_info "Creating /srv/prometheus/models..."
sudo mkdir -p /srv/prometheus/models
sudo chown -R "${LLMOPS_USER}:${LLMOPS_USER}" /srv/prometheus/models
sudo chmod -R 750 /srv/prometheus/models
sudo chcon -Rt container_file_t /srv/prometheus/models 2>/dev/null || true

_info "Creating /var/log/prometheus/{gateway,auth-service,manager,runtime,observability}..."
sudo mkdir -p \
    /var/log/prometheus/gateway \
    /var/log/prometheus/auth-service \
    /var/log/prometheus/manager \
    /var/log/prometheus/runtime/logs \
    /var/log/prometheus/observability
sudo chown "${UID_GATEWAY}:${UID_GATEWAY}"  /var/log/prometheus/gateway
sudo chown "${UID_AUTH}:${UID_AUTH}"        /var/log/prometheus/auth-service
sudo chown "${UID_MANAGER}:${UID_MANAGER}"  /var/log/prometheus/manager
sudo chown "${LLMOPS_USER}:${LLMOPS_USER}"  /var/log/prometheus/runtime/logs
sudo chown "${UID_GATEWAY}:${UID_GATEWAY}"  /var/log/prometheus/observability
sudo chmod 750 /var/log/prometheus /var/log/prometheus/*

_info "Creating /var/run/prometheus/runtime/run..."
sudo mkdir -p /var/run/prometheus/runtime/run
sudo chown -R "${LLMOPS_USER}:${LLMOPS_USER}" /var/run/prometheus/runtime
sudo chmod -R u+rwx,g+rx,o-rwx /var/run/prometheus/runtime
sudo chcon -Rt container_file_t /var/log/prometheus /var/run/prometheus 2>/dev/null || true

_info "Making runtime shell scripts executable..."
sudo find "${PROJECT_DIR}/gateway" "${PROJECT_DIR}/auth-service" "${PROJECT_DIR}/runtime" \
    -type f -name '*.sh' -exec chmod 0755 {} + 2>/dev/null || true

_ok "Host directories created"

# ─────────────────────────────────────────────────────────────────────────────
# STEP 7 — Generate RSA keypair and self-signed TLS certificate
# Implements: AC-7
# ─────────────────────────────────────────────────────────────────────────────
_step "Generate RSA keypair and self-signed TLS certificate"

PRIV_KEY="/etc/prometheus/keys/private_2026-q1.pem"
PUB_KEY="/etc/prometheus/keys/public_2026-q1.pem"
GW_CERT="/etc/prometheus/certs/gateway.crt"
GW_KEY="/etc/prometheus/certs/gateway.key"
AUTH_CERT="/etc/prometheus/certs/auth.crt"
AUTH_KEY="/etc/prometheus/certs/auth.key"

# Keypair — skip if already present unless --force
if [[ -f "${PRIV_KEY}" && -f "${PUB_KEY}" && "${FORCE}" != "true" ]]; then
    _ok "RSA keypair already exists — skipping generation (use --force to regenerate)"
else
    _info "Generating RSA-2048 keypair..."
    sudo openssl genpkey -algorithm RSA \
        -out "${PRIV_KEY}" \
        -pkeyopt rsa_keygen_bits:2048
    sudo openssl rsa -in "${PRIV_KEY}" -pubout -out "${PUB_KEY}"
    sudo chmod 600 "${PRIV_KEY}"
    sudo chmod 644 "${PUB_KEY}"
    sudo chown "${LLMOPS_USER}:${LLMOPS_USER}" "${PRIV_KEY}" "${PUB_KEY}"
    sudo chcon -t container_file_t "${PRIV_KEY}" "${PUB_KEY}" 2>/dev/null || true
    _ok "RSA keypair generated"
fi

# Symlinks for podman-compose paths (private.pem / public.pem)
sudo ln -sf "${PRIV_KEY}" /etc/prometheus/keys/private.pem 2>/dev/null || true
sudo ln -sf "${PUB_KEY}"  /etc/prometheus/keys/public.pem  2>/dev/null || true

# --- Fix: Always set correct ownership/permissions for private.pem symlink and its target ---
AUTH_SYMLINK="/etc/prometheus/keys/private.pem"
if [[ -L "${AUTH_SYMLINK}" ]]; then
    AUTH_TARGET="$(readlink -f "${AUTH_SYMLINK}")"
    # Set permissions and ownership on the symlink target (the real key file)
    sudo chown "${UID_AUTH}:${UID_AUTH}" "${AUTH_TARGET}"
    sudo chmod 600 "${AUTH_TARGET}"
    sudo restorecon -v "${AUTH_TARGET}" 2>/dev/null || true
    # Set ownership on the symlink itself (has no effect on file perms, but for completeness)
    sudo chown -h "${UID_AUTH}:${UID_AUTH}" "${AUTH_SYMLINK}"
    sudo restorecon -v "${AUTH_SYMLINK}" 2>/dev/null || true
else
    # If not a symlink, just set on the file
    sudo chown "${UID_AUTH}:${UID_AUTH}" "${AUTH_SYMLINK}"
    sudo chmod 600 "${AUTH_SYMLINK}"
    sudo restorecon -v "${AUTH_SYMLINK}" 2>/dev/null || true
fi

# Warn if hostname looks like a real public hostname
HOSTNAME_FQDN="$(hostname -f 2>/dev/null || hostname)"
if [[ "${HOSTNAME_FQDN}" != *".internal" && "${HOSTNAME_FQDN}" != "localhost" ]]; then
    _warn "Self-signed certificates are for development/test only."
    _warn "Hostname '${HOSTNAME_FQDN}' does not end in '.internal'."
    _warn "Use a CA-issued certificate in production."
fi

# TLS certs — skip if already present (idempotent)
for pair in "${GW_CERT}:${GW_KEY}" "${AUTH_CERT}:${AUTH_KEY}"; do
    cert="${pair%%:*}"
    key="${pair##*:}"
    cn_label="$(basename "${cert}" .crt)"
    if [[ -f "${cert}" && -f "${key}" && "${FORCE}" != "true" ]]; then
        _ok "TLS cert ${cert} already exists — skipping (use --force to regenerate)"
    else
        _info "Generating self-signed TLS cert for ${cn_label}..."
        sudo openssl req -x509 -newkey rsa:2048 -nodes \
            -keyout "${key}" -out "${cert}" \
            -days 365 \
            -subj "/CN=${HOSTNAME_FQDN}" \
            -addext "subjectAltName=DNS:${HOSTNAME_FQDN},DNS:localhost,IP:127.0.0.1"
        _ok "TLS cert generated: ${cert}"
    fi
    # Always enforce correct ownership and permissions — idempotent, safe to re-run.
    # Applies even when the cert already existed (fixes root:root ownership from prior installs).
    # Implements: memory/specs/025-tls-cert-ownership-hotfix.md — AC-1, AC-2
    sudo chmod 644 "${cert}"
    sudo chmod 600 "${key}"
    if [[ "${cert}" == "${GW_CERT}" ]]; then
        _tls_uid="${UID_GATEWAY}"
    else
        _tls_uid="${UID_AUTH}"
    fi
    # Assign ownership to the service UID that reads the key at runtime (never root).
    # gateway cert/key → UID_GATEWAY (1000 / prometheus)
    # auth cert/key   → UID_AUTH    (1001 / prometheus-auth)
    # (UID_MANAGER=1002 / pmgr — no TLS cert needed for the manager container)
    sudo chown "${_tls_uid}:${_tls_uid}" "${cert}" "${key}"
    sudo restorecon -v "${cert}" "${key}" 2>/dev/null || true
done

# ─────────────────────────────────────────────────────────────────────────────
# STEP 8 — Copy .env templates (idempotent — never overwrites existing files)
# Implements: AC-8
# ─────────────────────────────────────────────────────────────────────────────
_step "Copy .env templates"

_copy_env() {
    local src="$1" dst="$2"
    if [[ -f "${dst}" && "${FORCE}" != "true" ]]; then
        _ok "${dst} already exists — skipping (use --force to overwrite)"
    else
        cp "${src}" "${dst}"
        _ok "Copied ${src} → ${dst}"
    fi
    # .env files contain secrets — always enforce restrictive permissions regardless of umask
    chmod 600 "${dst}"
}

_copy_env "${PROJECT_DIR}/.env.redhat.example"              "${PROJECT_DIR}/.env"
_copy_env "${PROJECT_DIR}/gateway/.env.podman.example"      "${PROJECT_DIR}/gateway/.env"
_copy_env "${PROJECT_DIR}/auth-service/.env.example"        "${PROJECT_DIR}/auth-service/.env"

# Fixed (non-secret) auth-service variables — always set unconditionally so that a
# stale or manually-edited auth-service/.env cannot leave them commented out or missing.
# Implements: memory/specs/025-tls-cert-ownership-hotfix.md — AC-5
AUTH_ENV="${PROJECT_DIR}/auth-service/.env"
_set_env_var "${AUTH_ENV}" "AUTH_DB_URL"                "sqlite+aiosqlite:////data/auth.db"
_set_env_var "${AUTH_ENV}" "AUTH_PRIVATE_KEY_FILE"      "/run/secrets/jwt_private_key.pem"
_set_env_var "${AUTH_ENV}" "AUTH_PUBLIC_KEY_FILE"       "/run/secrets/jwt_public_key.pem"
_set_env_var "${AUTH_ENV}" "AUTH_REVOCATION_REDIS_URL"  "redis://redis:6379/0"
_set_env_var "${AUTH_ENV}" "AUTH_RATE_LIMIT_RPM"        "10"

# Update absolute paths in root .env to match what we created
_set_env_var "${PROJECT_DIR}/.env" "JWT_PUBLIC_KEY_HOST_PATH"  "/etc/prometheus/keys/public.pem"
_set_env_var "${PROJECT_DIR}/.env" "JWT_PRIVATE_KEY_HOST_PATH" "/etc/prometheus/keys/private.pem"
_set_env_var "${PROJECT_DIR}/.env" "TLS_CERT_HOST_PATH"        "${GW_CERT}"
_set_env_var "${PROJECT_DIR}/.env" "TLS_KEY_HOST_PATH"         "${GW_KEY}"
_set_env_var "${PROJECT_DIR}/.env" "AUTH_TLS_CERT_HOST_PATH"   "${AUTH_CERT}"
_set_env_var "${PROJECT_DIR}/.env" "AUTH_TLS_KEY_HOST_PATH"    "${AUTH_KEY}"

# Bind-mount host paths for runtime data and logs — always set unconditionally so that
# podman-compose never falls back to relative paths (which lack correct permissions).
# Implements: memory/specs/025-tls-cert-ownership-hotfix.md — AC-4
_set_env_var "${PROJECT_DIR}/.env" "AUTH_DB_HOST_PATH"        "/var/lib/prometheus/auth-service"
_set_env_var "${PROJECT_DIR}/.env" "CONTAINER_LOG_HOST_PATH"  "/var/log/prometheus"
_set_env_var "${PROJECT_DIR}/.env" "MANAGER_LOG_HOST_PATH"    "/var/log/prometheus/manager"
_set_env_var "${PROJECT_DIR}/.env" "MANAGER_PID_ROOT"         "/var/run/prometheus/runtime/run"
_set_env_var "${PROJECT_DIR}/.env" "MANAGER_LOG_ROOT"         "/var/log/prometheus/runtime/logs"

_ok ".env files ready"

# ─────────────────────────────────────────────────────────────────────────────
# STEP 9 — Generate secrets and inject into .env files
# Implements: AC-9
# Secrets are NEVER printed to stdout; written directly to files only.
# ─────────────────────────────────────────────────────────────────────────────
_step "Generate and inject secrets"

_inject_secret() {
    local file="$1" key="$2" length="${3:-32}" mode="${4:-hex}"

    # Only inject if the current value looks like a placeholder
    current_val="$(grep "^${key}=" "${file}" 2>/dev/null | cut -d= -f2- || true)"
    if [[ "${current_val}" == *"replace-"* || "${current_val}" == *"<replace"* || -z "${current_val}" || "${FORCE}" == "true" ]]; then
        _info "Generating ${key} in ${file}..."
        if [[ "${mode}" == "base64" ]]; then
            secret="$(openssl rand -base64 "${length}")"
        else
            secret="$(openssl rand -hex "${length}")"
        fi
        # Write directly to file — never expose in stdout, log, or process list
        _set_env_var "${file}" "${key}" "${secret}"
        unset secret
        _ok "${key} injected"
    else
        _ok "${key} already set — skipping"
    fi
}

AUTH_ENV="${PROJECT_DIR}/auth-service/.env"
ROOT_ENV="${PROJECT_DIR}/.env"

_inject_secret "${AUTH_ENV}" "AUTH_ADMIN_API_KEY"          32 hex
_inject_secret "${AUTH_ENV}" "SHARE_TOKEN_ENCRYPTION_KEY"  32 hex
_inject_secret "${ROOT_ENV}" "GRAFANA_SECRET_KEY"          32 hex
_inject_secret "${ROOT_ENV}" "GRAFANA_ADMIN_PASSWORD"      16 base64

_ok "Secrets injected"

# ─────────────────────────────────────────────────────────────────────────────
# STEP 10 — Configure proxy (if --proxy was provided) and print summary
# Implements: AC-10
# ─────────────────────────────────────────────────────────────────────────────
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
    _info "No proxy specified — skipping proxy configuration"
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
echo "  7. Register the gateway client (once, after auth-service is healthy):"
echo "       AUTH_ADMIN_API_KEY=\$(grep ^AUTH_ADMIN_API_KEY auth-service/.env | cut -d= -f2)"
echo "       curl -s -X POST https://localhost:9000/admin/clients \\"
echo "         -H 'Content-Type: application/json' \\"
echo "         -H \"X-Admin-Key: \${AUTH_ADMIN_API_KEY}\" \\"
echo "         --cacert ${AUTH_CERT} \\"
echo "         -d '{\"client_name\":\"gateway-manager-sync\",\"role\":\"app\",\"allowed_scopes\":[\"backend-registry:read\"]}'"
echo "       # Copy client_id and client_secret into gateway/.env"
echo ""
echo "  8. Validate the installation:"
echo "       bash scripts/validate.sh"
echo ""
