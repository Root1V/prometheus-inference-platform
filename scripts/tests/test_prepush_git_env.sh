#!/usr/bin/env bash
# PRM-278: a push from a linked worktree must not let the hook's tests write
# into the real repository. Git hands a hook GIT_DIR in that case, and a test's
# `git -C "$dir" init` then re-initialises the repository the push came from,
# as bare. The hook unsets it; this checks that it does, by running the hook's
# own prologue -- the real lines, not a copy -- followed by that init, and
# reading core.bare afterwards. A control without the unset must flip it, or
# this test would pass against a hook that never had the bug.
set -uo pipefail
unset GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
HOOK="${ROOT}/.githooks/pre-push"
TMP="$(mktemp -d)"
trap 'rm -rf "${TMP}"' EXIT
PASS=0; FAIL=0
_pass() { echo "  PASS: $1"; PASS=$((PASS+1)); }
_fail() { echo "  FAIL: $1"; FAIL=$((FAIL+1)); }

PROLOGUE="$(sed -n '1,/^unset GIT_DIR/p' "${HOOK}")"
if ! grep -q '^unset GIT_DIR' <<<"${PROLOGUE}"; then
    _fail "the hook has no 'unset GIT_DIR' line"
    echo "Results: ${PASS} passed, ${FAIL} failed"; exit 1
fi

# Push from a linked worktree through a hook made of $1, then report the
# shared config's core.bare.
bare_after_push() {
    local case="${TMP}/$2"
    mkdir -p "${case}/hooks"
    git init -q "${case}/repo"
    git -C "${case}/repo" -c user.name=t -c user.email=t@t commit -q --allow-empty -m init
    git -C "${case}/repo" worktree add -q "${case}/wt" -b wt
    git init -q --bare "${case}/remote.git"
    { printf '%s\n' "$1"; printf 'git -C "$(mktemp -d)" init -q\n'; } > "${case}/hooks/pre-push"
    chmod +x "${case}/hooks/pre-push"
    git -C "${case}/wt" -c core.hooksPath="${case}/hooks" push -q "${case}/remote.git" wt:wt 2>/dev/null
    git config --file "${case}/repo/.git/config" core.bare
}

echo "=== PRM-278: a push from a worktree leaves core.bare alone ==="
got="$(bare_after_push "${PROLOGUE}" fixed)"
[[ "${got}" == "false" ]] && _pass "with the hook's prologue, core.bare stays false" \
    || _fail "with the hook's prologue, core.bare is '${got}'"

got="$(bare_after_push "$(grep -v '^unset GIT_DIR' <<<"${PROLOGUE}")" control)"
[[ "${got}" == "true" ]] && _pass "control: without the unset, the same push flips core.bare" \
    || _fail "control: without the unset core.bare is '${got}' -- the test cannot see the bug"

echo ""
echo "Results: ${PASS} passed, ${FAIL} failed"
[[ ${FAIL} -eq 0 ]]
