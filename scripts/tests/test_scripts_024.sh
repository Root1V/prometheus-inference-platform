#!/usr/bin/env bash
# scripts/tests/test_scripts_024.sh
# Implements: memory/specs/024-idempotent-deploy.md — AC-1 through AC-15
#
# Static/unit tests for scripts/install-rhel.sh and .gitignore.
# The deploy fast-path is exercised with local command stubs so the tests stay hermetic.
#
# Usage:
#   bash scripts/tests/test_scripts_024.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
INSTALL_SCRIPT="${REPO_ROOT}/scripts/install-rhel.sh"
GITIGNORE_FILE="${REPO_ROOT}/.gitignore"

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

assert_before() {
    local file="$1" first="$2" second="$3" name="$4"
    local first_line second_line
    first_line="$(grep -nF -- "$first" "$file" | head -n1 | cut -d: -f1 || true)"
    second_line="$(grep -nF -- "$second" "$file" | head -n1 | cut -d: -f1 || true)"
    if [[ -n "$first_line" && -n "$second_line" && "$first_line" -lt "$second_line" ]]; then
        pass "$name"
    else
        fail "$name (order check failed: ${first} before ${second})"
    fi
}

make_mock_bin() {
    local mock_dir="$1"
    mkdir -p "$mock_dir"

    cat >"$mock_dir/uname" <<'EOF'
#!/usr/bin/env bash
echo "Linux"
EOF

    cat >"$mock_dir/git" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
printf 'git %s\n' "$*" >>"${MOCK_LOG}"
case "$*" in
    *"status --porcelain"*)
        printf '%s\n' "${MOCK_GIT_STATUS_PORCELAIN:-}" ;;
    *"clean -fd"*)
        printf 'cleaned\n' >>"${MOCK_GIT_CLEAN_MARKER:-/dev/null}" ;;
    *"checkout -- ."*)
        printf 'checkout\n' >>"${MOCK_GIT_CHECKOUT_MARKER:-/dev/null}" ;;
    *"pull --ff-only"*)
        printf 'pull\n' >>"${MOCK_GIT_PULL_MARKER:-/dev/null}" ;;
    *"describe --tags --abbrev=0"*)
        printf '%s\n' "${MOCK_GIT_DESCRIBE:-v0.0.0}" ;;
    *"rev-parse --short HEAD"*)
        printf '%s\n' "${MOCK_GIT_SHORT:-abc1234}" ;;
esac
exit 0
EOF

    cat >"$mock_dir/uv" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
printf 'uv %s\n' "$*" >>"${MOCK_LOG}"
if [[ "${1:-}" == "sync" ]]; then
    printf 'sync\n' >>"${MOCK_UV_SYNC_MARKER}"
fi
exit 0
EOF

    cat >"$mock_dir/podman" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
printf 'podman %s\n' "$*" >>"${MOCK_LOG}"
if [[ "${1:-}" == "compose" && "${2:-}" == "down" && "${MOCK_PODMAN_DOWN_EXIT:-0}" != "0" ]]; then
    exit "${MOCK_PODMAN_DOWN_EXIT}"
fi
exit 0
EOF

    cat >"$mock_dir/pgrep" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
printf 'pgrep %s\n' "$*" >>"${MOCK_LOG}"
case "$*" in
    *"pmgr-api"*)
        if [[ -n "${MOCK_PMGRT_PID:-}" ]]; then
            printf '%s\n' "${MOCK_PMGRT_PID}"
        fi ;;
    *"llama-server"*)
        if [[ -n "${MOCK_LLAMA_PID:-}" ]]; then
            printf '%s\n' "${MOCK_LLAMA_PID}"
        fi ;;
esac
exit 0
EOF

    cat >"$mock_dir/kill" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
printf 'kill %s\n' "$*" >>"${MOCK_LOG}"
exit 0
EOF

    cat >"$mock_dir/nohup" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
printf 'nohup %s\n' "$*" >>"${MOCK_LOG}"
exit 0
EOF

    cat >"$mock_dir/sleep" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
printf 'sleep %s\n' "$*" >>"${MOCK_LOG}"
exit 0
EOF

    cat >"$mock_dir/date" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
case "$*" in
    "-u +%Y-%m-%dT%H:%M:%SZ")
        printf '%s\n' "${MOCK_DEPLOY_TIMESTAMP:-2026-05-09T18:00:00Z}" ;;
    "+%Y-%m-%d %H:%M:%S")
        printf '%s\n' "${MOCK_NOW_TIMESTAMP:-2026-05-09 18:00:00}" ;;
    *)
        /bin/date "$@" ;;
esac
EOF

    for tool in uname git uv podman pgrep kill nohup sleep date; do
        chmod +x "$mock_dir/$tool"
    done
}

make_project_dir() {
    local project_dir="$1"
    mkdir -p "$project_dir" "$project_dir/logs"
    printf 'lock\n' >"$project_dir/uv.lock"
    git -C "$project_dir" init -q >/dev/null 2>&1 || true
}

run_deploy() {
    local project_dir="$1" mock_dir="$2" output_file="$3"
    shift 3
    env \
        PATH="${mock_dir}:${PATH}" \
        MOCK_LOG="${project_dir}/mock.log" \
        MOCK_UV_SYNC_MARKER="${project_dir}/uv-sync.log" \
        MOCK_GIT_DESCRIBE="${MOCK_GIT_DESCRIBE:-v1.2.3}" \
        MOCK_GIT_SHORT="${MOCK_GIT_SHORT:-abc1234}" \
        MOCK_GIT_STATUS_PORCELAIN="${MOCK_GIT_STATUS_PORCELAIN:-}" \
        MOCK_PODMAN_DOWN_EXIT="${MOCK_PODMAN_DOWN_EXIT:-0}" \
        MOCK_PMGRT_PID="${MOCK_PMGRT_PID:-}" \
        MOCK_LLAMA_PID="${MOCK_LLAMA_PID:-}" \
        MOCK_DEPLOY_TIMESTAMP="${MOCK_DEPLOY_TIMESTAMP:-2026-05-09T18:00:00Z}" \
        MOCK_NOW_TIMESTAMP="${MOCK_NOW_TIMESTAMP:-2026-05-09 18:00:00}" \
        bash "${INSTALL_SCRIPT}" --deploy --project-dir="${project_dir}" "$@" >"${output_file}" 2>&1
}

compute_lock_hash() {
    local file="$1"
    printf 'sha256:%s' "$(sha256sum "$file" | awk '{print $1}')"
}

test_help_mentions_deploy_AC9() {
    local output
    output="$(bash "${INSTALL_SCRIPT}" --help 2>&1)"
    if echo "$output" | grep -Fq -- "--deploy" && \
       echo "$output" | grep -Fq -- "Fast post-release deploy" && \
       echo "$output" | grep -Fq -- "Safe to run after every feature release"; then
        pass "AC-9: --help documents --deploy mode and when to use it"
    else
        fail "AC-9: --help does not document --deploy mode"
    fi
}

test_default_project_dir_is_logged_AC10() {
    if grep -Fq 'PROJECT_DIR="/opt/prometheus-ai-inference"' "${INSTALL_SCRIPT}" && \
       grep -Fq 'project-dir : ${PROJECT_DIR}' "${INSTALL_SCRIPT}"; then
        pass "AC-10: default project dir is /opt/prometheus-ai-inference and is logged"
    else
        fail "AC-10: default project dir or deploy logging is missing"
    fi
}

test_deploy_header_prints_version_before_steps_AC13() {
    local project_dir mock_dir output_file
    project_dir="$(mktemp -d)"
    mock_dir="$(mktemp -d)"
    output_file="$(mktemp)"
    make_project_dir "$project_dir"
    make_mock_bin "$mock_dir"

    if run_deploy "$project_dir" "$mock_dir" "$output_file"; then
        assert_contains "$output_file" "Prometheus — DEPLOY mode" "AC-13: deploy banner is printed"
        assert_contains "$output_file" "Version :" "AC-13: deploy banner includes version"
        assert_contains "$output_file" "Commit :" "AC-13: deploy banner includes commit"
        assert_before "$output_file" "Prometheus — DEPLOY mode" "[DEPLOY 1/6]" "AC-13: banner prints before any deploy step"
    else
        fail "AC-13: deploy run failed unexpectedly"
    fi

    rm -rf "$project_dir" "$mock_dir" "$output_file"
}

test_fast_path_skips_install_only_steps_AC1_AC2() {
    local project_dir mock_dir output_file lock_hash
    project_dir="$(mktemp -d)"
    mock_dir="$(mktemp -d)"
    output_file="$(mktemp)"
    make_project_dir "$project_dir"
    make_mock_bin "$mock_dir"
    lock_hash="$(compute_lock_hash "${project_dir}/uv.lock")"
    cat >"${project_dir}/.deploy-state" <<EOF
LAST_DEPLOY_COMMIT=old1234
LAST_DEPLOY_TIMESTAMP=2026-05-09T17:00:00Z
LAST_UVSYNC_LOCK_HASH=${lock_hash}
EOF

    if run_deploy "$project_dir" "$mock_dir" "$output_file"; then
        assert_not_contains "${project_dir}/mock.log" "dnf install" "AC-1: deploy mode skips system package installation"
        assert_not_contains "${project_dir}/mock.log" "install-server.sh" "AC-1: deploy mode skips llama-server build"
        assert_not_contains "${project_dir}/mock.log" "uv sync" "AC-2: uv sync is skipped when uv.lock hash matches"
        assert_contains "$output_file" "uv.lock unchanged — skipping uv sync" "AC-2: output explains uv sync was skipped"
    else
        fail "AC-1/AC-2: deploy run failed unexpectedly"
    fi

    rm -rf "$project_dir" "$mock_dir" "$output_file"
}

test_uv_sync_runs_and_force_overrides_AC3_AC5() {
    local project_dir mock_dir output_file initial_hash new_hash
    project_dir="$(mktemp -d)"
    mock_dir="$(mktemp -d)"
    output_file="$(mktemp)"
    make_project_dir "$project_dir"
    make_mock_bin "$mock_dir"

    initial_hash="sha256:deadbeef"
    new_hash="$(compute_lock_hash "${project_dir}/uv.lock")"
    cat >"${project_dir}/.deploy-state" <<EOF
LAST_DEPLOY_COMMIT=old1234
LAST_DEPLOY_TIMESTAMP=2026-05-09T17:00:00Z
LAST_UVSYNC_LOCK_HASH=${initial_hash}
EOF

    if run_deploy "$project_dir" "$mock_dir" "$output_file"; then
        assert_contains "${project_dir}/mock.log" "uv sync" "AC-3: uv sync runs when uv.lock hash changes"
        assert_contains "$output_file" "uv sync complete" "AC-3: deploy output reports uv sync completion"
        if grep -Fq "LAST_UVSYNC_LOCK_HASH=${new_hash}" "$project_dir/.deploy-state"; then
            pass "AC-3: deploy state hash updates to the new uv.lock hash"
        else
            fail "AC-3: deploy state hash was not updated to the new uv.lock hash"
        fi
    else
        fail "AC-3: deploy run with changed hash failed unexpectedly"
    fi

    cat >"${project_dir}/.deploy-state" <<EOF
LAST_DEPLOY_COMMIT=old1234
LAST_DEPLOY_TIMESTAMP=2026-05-09T17:00:00Z
LAST_UVSYNC_LOCK_HASH=${new_hash}
EOF
    : >"${project_dir}/uv-sync.log"
    if run_deploy "$project_dir" "$mock_dir" "$output_file" --force; then
        assert_contains "${project_dir}/mock.log" "uv sync" "AC-5: --force always runs uv sync"
        if grep -Fq "LAST_DEPLOY_COMMIT=abc1234" "$project_dir/.deploy-state" && \
           grep -Fq "LAST_DEPLOY_TIMESTAMP=2026-05-09T18:00:00Z" "$project_dir/.deploy-state"; then
            pass "AC-5: --force overwrites deploy state"
        else
            fail "AC-5: --force did not overwrite deploy state contents"
        fi
    else
        fail "AC-5: deploy run with --force failed unexpectedly"
    fi

    rm -rf "$project_dir" "$mock_dir" "$output_file"
}

test_state_file_and_gitignore_AC4() {
    local project_dir mock_dir output_file expected_hash
    project_dir="$(mktemp -d)"
    mock_dir="$(mktemp -d)"
    output_file="$(mktemp)"
    make_project_dir "$project_dir"
    make_mock_bin "$mock_dir"
    expected_hash="$(compute_lock_hash "${project_dir}/uv.lock")"

    if run_deploy "$project_dir" "$mock_dir" "$output_file"; then
        if grep -Fq "LAST_DEPLOY_COMMIT=abc1234" "$project_dir/.deploy-state" && \
           grep -Fq "LAST_DEPLOY_TIMESTAMP=2026-05-09T18:00:00Z" "$project_dir/.deploy-state" && \
           grep -Fq "LAST_UVSYNC_LOCK_HASH=${expected_hash}" "$project_dir/.deploy-state"; then
            pass "AC-4: .deploy-state records commit, timestamp, and lock hash"
        else
            fail "AC-4: .deploy-state contents are incorrect"
        fi

        if grep -Fq ".deploy-state" "${GITIGNORE_FILE}"; then
            pass "AC-4: .deploy-state is gitignored"
        else
            fail "AC-4: .deploy-state is missing from .gitignore"
        fi
    else
        fail "AC-4: deploy run failed unexpectedly"
    fi

    rm -rf "$project_dir" "$mock_dir" "$output_file"
}

test_podman_order_and_graceful_down_AC6_AC7() {
    local project_dir mock_dir output_file
    project_dir="$(mktemp -d)"
    mock_dir="$(mktemp -d)"
    output_file="$(mktemp)"
    make_project_dir "$project_dir"
    make_mock_bin "$mock_dir"

    export MOCK_PODMAN_DOWN_EXIT=1
    if run_deploy "$project_dir" "$mock_dir" "$output_file"; then
        assert_before "${project_dir}/mock.log" "podman compose -f podman-compose.yml down" "podman compose -f podman-compose.yml up --build -d" "AC-6: podman down runs before up"
        assert_contains "$output_file" "Containers rebuilt and started" "AC-7: deploy continues after a missing-container down"
        pass "AC-7: podman compose down exits gracefully when no containers are running"
    else
        fail "AC-6/AC-7: deploy run failed unexpectedly"
    fi
    unset MOCK_PODMAN_DOWN_EXIT

    rm -rf "$project_dir" "$mock_dir" "$output_file"
}

test_install_mode_stays_on_ten_steps_AC8() {
    local step_count
    step_count="$(grep -c '^_step ' "${INSTALL_SCRIPT}" || true)"
    if [[ "$step_count" -eq 10 ]] && grep -Fq 'DEPLOY_MODE=false' "${INSTALL_SCRIPT}" && \
       grep -Fq 'INSTALL mode (default)' "${INSTALL_SCRIPT}"; then
        pass "AC-8: install mode remains the 10-step default path"
    else
        fail "AC-8: install mode changed or no longer has 10 steps"
    fi
}

test_permission_regressions_AC1_AC6_AC7_AC8() {
    if grep -Fq 'find "${PROJECT_DIR}/gateway" "${PROJECT_DIR}/auth-service" "${PROJECT_DIR}/runtime" \' "${INSTALL_SCRIPT}" && \
       grep -Fq 'chmod 0755 {} + 2>/dev/null || true' "${INSTALL_SCRIPT}" && \
       grep -Fq 'UID_GATEWAY=1000' "${INSTALL_SCRIPT}" && \
       grep -Fq 'UID_AUTH=1001' "${INSTALL_SCRIPT}" && \
       grep -Fq 'UID_MANAGER=1002' "${INSTALL_SCRIPT}" && \
       grep -Fq 'useradd --uid 1002 --gid 1002' "${REPO_ROOT}/runtime/manager/api/Dockerfile" && \
       grep -Fq 'sudo chown "${_tls_uid}:${_tls_uid}" "${cert}" "${key}"' "${INSTALL_SCRIPT}" && \
       grep -Fq 'chmod 600 "${dst}"' "${INSTALL_SCRIPT}" && \
       grep -Fq -- "--exclude='*.env'" "${INSTALL_SCRIPT}" && \
       grep -Fq 'chmod 600 "${BACKUP_ARCHIVE}"' "${INSTALL_SCRIPT}"; then
        pass "AC-1/AC-6/AC-7/AC-8: deploy and install paths enforce executable, service-owned, secret-safe, and backup-secure permissions"
    else
        fail "AC-1/AC-6/AC-7/AC-8: permission hardening regressions are missing"
    fi
}

test_manager_restart_source_AC11() {
    if grep -Fq 'kill -TERM "${PMGR_PID}"' "${INSTALL_SCRIPT}" && \
       grep -Fq 'nohup pmgr-api' "${INSTALL_SCRIPT}" && \
       grep -Fq 'pmgr-api in background' "${INSTALL_SCRIPT}"; then
        pass "AC-11: source restarts pmgr-api with SIGTERM and nohup"
    else
        fail "AC-11: source is missing the pmgr restart sequence"
    fi
}

test_llama_server_warning_AC12() {
    local project_dir mock_dir output_file
    project_dir="$(mktemp -d)"
    mock_dir="$(mktemp -d)"
    output_file="$(mktemp)"
    make_project_dir "$project_dir"
    make_mock_bin "$mock_dir"
    export MOCK_LLAMA_PID=4321

    if run_deploy "$project_dir" "$mock_dir" "$output_file"; then
        assert_contains "$output_file" "llama-server is running (PID 4321)" "AC-12: deploy warns when llama-server is already running"
        assert_contains "$output_file" "kill 4321" "AC-12: deploy output includes manual restart command"
        assert_contains "$output_file" "runtime/scripts/start-server.sh" "AC-12: deploy output names the restart script"
    else
        fail "AC-12: deploy run failed unexpectedly"
    fi

    unset MOCK_LLAMA_PID
    rm -rf "$project_dir" "$mock_dir" "$output_file"
}

test_deploy_summary_AC14() {
    local project_dir mock_dir output_file
    project_dir="$(mktemp -d)"
    mock_dir="$(mktemp -d)"
    output_file="$(mktemp)"
    make_project_dir "$project_dir"
    make_mock_bin "$mock_dir"

    if run_deploy "$project_dir" "$mock_dir" "$output_file"; then
        assert_contains "$output_file" "Deploy complete" "AC-14: deploy prints a completion summary"
        assert_contains "$output_file" "Version :" "AC-14: summary includes version"
        assert_contains "$output_file" "Commit :" "AC-14: summary includes commit"
        assert_contains "$output_file" "Elapsed :" "AC-14: summary includes elapsed time"
        assert_contains "$output_file" "bash scripts/validate.sh" "AC-14: summary includes validation command"
    else
        fail "AC-14: deploy run failed unexpectedly"
    fi

    rm -rf "$project_dir" "$mock_dir" "$output_file"
}

test_dirty_tree_backup_and_clean_pull_AC15() {
    local sandbox_dir project_dir mock_dir output_file backup_archive
    sandbox_dir="$(mktemp -d)"
    project_dir="${sandbox_dir}/project"
    mock_dir="$(mktemp -d)"
    output_file="$(mktemp)"
    make_project_dir "$project_dir"
    make_mock_bin "$mock_dir"
    # Plant sentinel .env files to verify they are excluded from the archive (security fix)
    mkdir -p "${project_dir}/gateway" "${project_dir}/auth-service"
    printf 'AUTH_ADMIN_API_KEY=sentinel_secret\n' > "${project_dir}/auth-service/.env"
    printf 'GRAFANA_SECRET_KEY=sentinel_grafana\n' > "${project_dir}/.env"
    export MOCK_GIT_STATUS_PORCELAIN=$' M tracked.txt\n?? .env.redhat.example'

    if run_deploy "$project_dir" "$mock_dir" "$output_file"; then
        backup_archive="$(ls -1 "${sandbox_dir}"/prometheus-backup-*.tar.gz 2>/dev/null | head -n1 || true)"
        if [[ -n "$backup_archive" && -f "$backup_archive" ]]; then
            pass "AC-15: dirty tree creates a timestamped backup archive"
        else
            fail "AC-15: dirty tree did not create the backup archive"
        fi

        # Security: archive must be owner-readable only (mode 600)
        local archive_perms
        archive_perms="$(ls -la "${backup_archive}" 2>/dev/null | awk '{print $1}' || true)"
        if [[ "${archive_perms}" == "-rw-------"* ]]; then
            pass "AC-15: backup archive has restrictive permissions (600)"
        else
            fail "AC-15: backup archive permissions are not 600 (got: ${archive_perms})"
        fi

        # Security: .env files must be excluded — they carry live secrets
        if tar -tzf "${backup_archive}" 2>/dev/null | grep -qE '(^|/)\.env$'; then
            fail "AC-15: backup archive contains .env files (security violation — secrets must not be bundled)"
        else
            pass "AC-15: backup archive excludes .env files"
        fi

        assert_contains "$output_file" "Working tree is dirty — creating backup archive before cleaning:" "AC-15: deploy warns before cleaning the tree"
        assert_contains "$output_file" "git clean -fd" "AC-15: deploy warns that git clean is being run"
        assert_before "${project_dir}/mock.log" "git -C ${project_dir} clean -fd" "git -C ${project_dir} pull --ff-only" "AC-15: git clean runs before git pull"
        assert_before "${project_dir}/mock.log" "git -C ${project_dir} checkout -- ." "git -C ${project_dir} pull --ff-only" "AC-15: git checkout reset runs before git pull"
    else
        fail "AC-15: deploy run with dirty tree failed unexpectedly"
    fi

    unset MOCK_GIT_STATUS_PORCELAIN
    rm -rf "$sandbox_dir" "$mock_dir" "$output_file"
}

test_clean_tree_skips_backup_AC15() {
    local sandbox_dir project_dir mock_dir output_file
    sandbox_dir="$(mktemp -d)"
    project_dir="${sandbox_dir}/project"
    mock_dir="$(mktemp -d)"
    output_file="$(mktemp)"
    make_project_dir "$project_dir"
    make_mock_bin "$mock_dir"
    unset MOCK_GIT_STATUS_PORCELAIN

    if run_deploy "$project_dir" "$mock_dir" "$output_file"; then
        if compgen -G "${sandbox_dir}/prometheus-backup-*.tar.gz" >/dev/null; then
            fail "AC-15: clean tree unexpectedly created a backup archive"
        else
            pass "AC-15: clean tree does not create a backup archive"
        fi
    else
        fail "AC-15: deploy run with clean tree failed unexpectedly"
    fi

    rm -rf "$sandbox_dir" "$mock_dir" "$output_file"
}

test_help_mentions_deploy_AC9
test_default_project_dir_is_logged_AC10
test_deploy_header_prints_version_before_steps_AC13
test_fast_path_skips_install_only_steps_AC1_AC2
test_uv_sync_runs_and_force_overrides_AC3_AC5
test_state_file_and_gitignore_AC4
test_podman_order_and_graceful_down_AC6_AC7
test_install_mode_stays_on_ten_steps_AC8
test_permission_regressions_AC1_AC6_AC7_AC8
test_manager_restart_source_AC11
test_llama_server_warning_AC12
test_deploy_summary_AC14
test_dirty_tree_backup_and_clean_pull_AC15
test_clean_tree_skips_backup_AC15

echo ""
echo "Tests passed: ${PASS}"
echo "Tests failed: ${FAIL}"

if [[ "${FAIL}" -ne 0 ]]; then
    exit 1
fi