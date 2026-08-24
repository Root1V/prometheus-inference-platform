#!/usr/bin/env bash
# scripts/tests/test_scripts_025.sh
# Implements: memory/specs/025-tls-cert-ownership-hotfix.md — AC-1, AC-2, AC-3
#
# Static / structural tests for the STEP 7 ownership fix in install-rhel.sh
# and the extended cert-ownership check in validate.sh.
# Does NOT require a running RHEL host, Podman, or actual certificate files.
#
# Usage:
#   bash scripts/tests/test_scripts_025.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
INSTALL_SCRIPT="${REPO_ROOT}/scripts/install-rhel.sh"
VALIDATE_SCRIPT="${REPO_ROOT}/scripts/validate.sh"

PASS=0
FAIL=0

pass() { echo "  PASS: $1"; PASS=$(( PASS + 1 )); }
fail() { echo "  FAIL: $1"; FAIL=$(( FAIL + 1 )); }

assert_contains() {
    local file="$1" needle="$2" name="$3"
    if grep -Fq -- "$needle" "$file"; then
        pass "$name"
    else
        fail "$name (missing: $needle)"
    fi
}

assert_not_contains() {
    local file="$1" needle="$2" name="$3"
    if grep -Fq -- "$needle" "$file"; then
        fail "$name (unexpected: $needle)"
    else
        pass "$name"
    fi
}

# Return the 1-based line number of the first occurrence of a fixed string.
# Outputs empty string if not found.
_line_of() {
    grep -nF -- "$1" "$2" | head -n1 | cut -d: -f1 || true
}

assert_after() {
    local file="$1" anchor="$2" target="$3" name="$4"
    local anchor_line target_line
    anchor_line="$(_line_of "${anchor}" "${file}")"
    target_line="$(_line_of "${target}" "${file}")"
    if [[ -n "${anchor_line}" && -n "${target_line}" && "${target_line}" -gt "${anchor_line}" ]]; then
        pass "${name}"
    else
        fail "${name} (expected '${target}' after '${anchor}': anchor=${anchor_line:-missing} target=${target_line:-missing})"
    fi
}

# ── Section header ─────────────────────────────────────────────────────────────
echo ""
echo "=== AC-1 & AC-2: chown + restorecon outside else block (install-rhel.sh) ==="

# The idempotency guard closes with a lone `fi` followed by the ownership block.
# We verify that chown and restorecon appear AFTER the fi that closes the cert loop's
# if/else, not inside the else branch that generates certs.
#
# Structural test: the comment marking the hotfix fix must appear after the
# "already exists — skipping" message (which is the if-branch body).

assert_after "${INSTALL_SCRIPT}" \
    "TLS cert \${cert} already exists — skipping" \
    "Always enforce correct ownership and permissions" \
    "AC-1/AC-2: ownership comment appears after idempotency skip message (outside else)"

assert_after "${INSTALL_SCRIPT}" \
    "TLS cert \${cert} already exists — skipping" \
    'sudo chown "${_tls_uid}:${_tls_uid}" "${cert}" "${key}"' \
    "AC-1: chown is executed after the idempotency guard (not only on generation)"

assert_after "${INSTALL_SCRIPT}" \
    "TLS cert \${cert} already exists — skipping" \
    'sudo restorecon -v "${cert}" "${key}" 2>/dev/null || true' \
    "AC-2: restorecon is executed after the idempotency guard (not only on generation)"

# Verify chown is NOT inside the else block by confirming no chown appears
# between "Generating self-signed TLS cert" and "_ok TLS cert generated".
# We do this by extracting the else block lines and checking for chown absence.
ELSE_BLOCK="$(awk '/Generating self-signed TLS cert/{found=1} found{print} /_ok "TLS cert generated/{found=0}' "${INSTALL_SCRIPT}")"
if echo "${ELSE_BLOCK}" | grep -Fq 'sudo chown'; then
    fail "AC-1: chown still appears inside the else/generation block — fix incomplete"
else
    pass "AC-1: chown does NOT appear inside the cert generation else block"
fi

if echo "${ELSE_BLOCK}" | grep -Fq 'sudo restorecon'; then
    fail "AC-2: restorecon still appears inside the else/generation block — fix incomplete"
else
    pass "AC-2: restorecon does NOT appear inside the cert generation else block"
fi

# ── Section header ─────────────────────────────────────────────────────────────
echo ""
echo "=== AC-1: UID constants are correct (1000 / 1001) ==="

assert_contains "${INSTALL_SCRIPT}" \
    'UID_GATEWAY=1000' \
    "AC-1: UID_GATEWAY is 1000 (prometheus)"

assert_contains "${INSTALL_SCRIPT}" \
    'UID_AUTH=1001' \
    "AC-1: UID_AUTH is 1001 (prometheus-auth)"

assert_contains "${INSTALL_SCRIPT}" \
    'sudo chmod 644 "${cert}"' \
    "AC-1: cert files get chmod 644 (always)"

assert_contains "${INSTALL_SCRIPT}" \
    'sudo chmod 600 "${key}"' \
    "AC-1: key files get chmod 600 (always)"

# ── Section header ─────────────────────────────────────────────────────────────
echo ""
echo "=== AC-3: validate.sh step-7/cert-ownership checks ==="

assert_contains "${VALIDATE_SCRIPT}" \
    '_check_cert_ownership' \
    "AC-3: validate.sh defines _check_cert_ownership helper"

assert_contains "${VALIDATE_SCRIPT}" \
    '"step-7/cert-ownership"' \
    "AC-3: validate.sh uses step-7/cert-ownership check key"

assert_contains "${VALIDATE_SCRIPT}" \
    '"/etc/prometheus/certs/auth.key"    "1001" "600"' \
    "AC-3: validate.sh checks auth.key ownership UID 1001 mode 600"

assert_contains "${VALIDATE_SCRIPT}" \
    '"/etc/prometheus/certs/auth.crt"    "1001" "644"' \
    "AC-3: validate.sh checks auth.crt ownership UID 1001 mode 644"

assert_contains "${VALIDATE_SCRIPT}" \
    '"/etc/prometheus/certs/gateway.key" "1000" "600"' \
    "AC-3: validate.sh checks gateway.key ownership UID 1000 mode 600"

assert_contains "${VALIDATE_SCRIPT}" \
    '"/etc/prometheus/certs/gateway.crt" "1000" "644"' \
    "AC-3: validate.sh checks gateway.crt ownership UID 1000 mode 644"

assert_contains "${VALIDATE_SCRIPT}" \
    "stat --format='%u'" \
    "AC-3: validate.sh uses stat --format=%u to read UID (Linux-compatible)"

assert_contains "${VALIDATE_SCRIPT}" \
    "stat --format='%a'" \
    "AC-3: validate.sh uses stat --format=%a to read mode (Linux-compatible)"

# ── AC-4: bind-mount env path injection ───────────────────────────────────────
# install-rhel.sh STEP 8 must unconditionally inject all 5 bind-mount paths.
# validate.sh must verify each path is set to the correct absolute value.

assert_contains "${INSTALL_SCRIPT}" \
    '"AUTH_DB_HOST_PATH"' \
    "AC-4: install-rhel.sh sets AUTH_DB_HOST_PATH unconditionally in STEP 8"

assert_contains "${INSTALL_SCRIPT}" \
    '"/var/lib/prometheus/auth-service"' \
    "AC-4: install-rhel.sh sets AUTH_DB_HOST_PATH to /var/lib/prometheus/auth-service"

assert_contains "${INSTALL_SCRIPT}" \
    '"CONTAINER_LOG_HOST_PATH"' \
    "AC-4: install-rhel.sh sets CONTAINER_LOG_HOST_PATH unconditionally in STEP 8"

assert_contains "${INSTALL_SCRIPT}" \
    '"/var/log/prometheus"' \
    "AC-4: install-rhel.sh sets CONTAINER_LOG_HOST_PATH to /var/log/prometheus"

assert_contains "${INSTALL_SCRIPT}" \
    '"MANAGER_LOG_HOST_PATH"' \
    "AC-4: install-rhel.sh sets MANAGER_LOG_HOST_PATH unconditionally in STEP 8"

assert_contains "${INSTALL_SCRIPT}" \
    '"MANAGER_PID_ROOT"' \
    "AC-4: install-rhel.sh sets MANAGER_PID_ROOT unconditionally in STEP 8"

assert_contains "${INSTALL_SCRIPT}" \
    '"MANAGER_LOG_ROOT"' \
    "AC-4: install-rhel.sh sets MANAGER_LOG_ROOT unconditionally in STEP 8"

assert_contains "${VALIDATE_SCRIPT}" \
    '"AUTH_DB_HOST_PATH"' \
    "AC-4: validate.sh checks AUTH_DB_HOST_PATH in bind-mount verification block"

assert_contains "${VALIDATE_SCRIPT}" \
    '"CONTAINER_LOG_HOST_PATH"' \
    "AC-4: validate.sh checks CONTAINER_LOG_HOST_PATH in bind-mount verification block"

assert_contains "${VALIDATE_SCRIPT}" \
    'env-files:bind-mount' \
    "AC-4: validate.sh uses env-files:bind-mount: check labels for bind-mount paths"

# ── AC-5: auth-service/.env fixed-variable injection ─────────────────────────
# install-rhel.sh STEP 8 must unconditionally inject 5 fixed (non-secret) vars
# into auth-service/.env. validate.sh must verify each is active.

assert_contains "${INSTALL_SCRIPT}" \
    '"AUTH_DB_URL"' \
    "AC-5: install-rhel.sh sets AUTH_DB_URL unconditionally in STEP 8"

assert_contains "${INSTALL_SCRIPT}" \
    '"sqlite+aiosqlite:////data/auth.db"' \
    "AC-5: install-rhel.sh sets AUTH_DB_URL to correct container-internal path"

assert_contains "${INSTALL_SCRIPT}" \
    '"AUTH_PRIVATE_KEY_FILE"' \
    "AC-5: install-rhel.sh sets AUTH_PRIVATE_KEY_FILE unconditionally in STEP 8"

assert_contains "${INSTALL_SCRIPT}" \
    '"/run/secrets/jwt_private_key.pem"' \
    "AC-5: install-rhel.sh sets AUTH_PRIVATE_KEY_FILE to /run/secrets/jwt_private_key.pem"

assert_contains "${INSTALL_SCRIPT}" \
    '"AUTH_PUBLIC_KEY_FILE"' \
    "AC-5: install-rhel.sh sets AUTH_PUBLIC_KEY_FILE unconditionally in STEP 8"

assert_contains "${INSTALL_SCRIPT}" \
    '"AUTH_REVOCATION_REDIS_URL"' \
    "AC-5: install-rhel.sh sets AUTH_REVOCATION_REDIS_URL unconditionally in STEP 8"

assert_contains "${INSTALL_SCRIPT}" \
    '"redis://redis:6379/0"' \
    "AC-5: install-rhel.sh sets AUTH_REVOCATION_REDIS_URL to redis://redis:6379/0"

assert_contains "${INSTALL_SCRIPT}" \
    '"AUTH_RATE_LIMIT_RPM"' \
    "AC-5: install-rhel.sh sets AUTH_RATE_LIMIT_RPM unconditionally in STEP 8"

assert_contains "${VALIDATE_SCRIPT}" \
    '"AUTH_DB_URL"' \
    "AC-5: validate.sh checks AUTH_DB_URL in step-8/auth-env block"

assert_contains "${VALIDATE_SCRIPT}" \
    '"AUTH_PRIVATE_KEY_FILE"' \
    "AC-5: validate.sh checks AUTH_PRIVATE_KEY_FILE in step-8/auth-env block"

assert_contains "${VALIDATE_SCRIPT}" \
    'step-8/auth-env' \
    "AC-5: validate.sh uses step-8/auth-env: check labels for auth-service fixed vars"

# _set_env_var fix: must handle commented-out lines (#KEY=...) as well as active/absent.
assert_contains "${INSTALL_SCRIPT}" \
    'commented_pat' \
    "AC-5: _set_env_var handles commented-out lines (commented_pat pattern present)"

assert_contains "${INSTALL_SCRIPT}" \
    "r'^#\\s*'" \
    "AC-5: _set_env_var matches # KEY= and #KEY= comment patterns"
echo ""
echo "══════════════════════════════════════════════════════════"
printf "  Test results: %d passed, %d failed\n" "${PASS}" "${FAIL}"
echo "══════════════════════════════════════════════════════════"

if [[ "${FAIL}" -gt 0 ]]; then
    exit 1
fi
exit 0
