#!/usr/bin/env bash
# scripts/join-fleet.sh — PRM-266: put this device in the fleet, or say why not.
#
# Adding a node was a page of instructions: write a TOML, set three environment
# variables, open a tunnel, hope. Every one of those can be wrong in a way that
# surfaces as the same symptom — a node that never appears — and the operator
# finds out by reading logs on two machines.
#
# So this verifies before it writes, and each check fails with the thing to do
# about it. The last check is a real heartbeat: if it returns 200 the node is in
# the fleet, which is a measurement rather than a promise.
#
# What it does NOT do, deliberately: create the credential. Only a human admin
# issues credentials on this platform, and a script that mints its own would be
# the hole that rule exists to close.
#
# Usage:
#   PMGR_FLEET_CLIENT_ID=... PMGR_FLEET_CLIENT_SECRET=... \
#   bash scripts/join-fleet.sh \
#     --name=imac --node-id=<uuid> \
#     --coordinator=http://192.168.68.101:8090 \
#     --auth=http://127.0.0.1:9000/oauth2/token \
#     --port=8090

set -euo pipefail

NAME="" NODE_ID="" COORDINATOR="" AUTH_URL="" PORT="8090" DRY_RUN=0
for arg in "$@"; do
  case "$arg" in
    --name=*)        NAME="${arg#*=}" ;;
    --node-id=*)     NODE_ID="${arg#*=}" ;;
    --coordinator=*) COORDINATOR="${arg#*=}" ;;
    --auth=*)        AUTH_URL="${arg#*=}" ;;
    --port=*)        PORT="${arg#*=}" ;;
    --dry-run)       DRY_RUN=1 ;;
    *) echo "unknown argument: $arg" >&2; exit 2 ;;
  esac
done

fail() { echo "  ✗ $1" >&2; [ -n "${2:-}" ] && echo "    → $2" >&2; exit 1; }
ok()   { echo "  ✓ $1"; }

for required in NAME NODE_ID COORDINATOR AUTH_URL; do
  [ -n "${!required}" ] || fail "missing --${required,,}" "see the usage block at the top of this script"
done
: "${PMGR_FLEET_CLIENT_ID:?set PMGR_FLEET_CLIENT_ID — a human admin issues it, with scopes fleet:heartbeat and node:$NODE_ID}"
: "${PMGR_FLEET_CLIENT_SECRET:?set PMGR_FLEET_CLIENT_SECRET}"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

echo "Joining '$NAME' to the fleet at $COORDINATOR"

# ── 1 · the toolchain ───────────────────────────────────────────────────────
command -v uv >/dev/null 2>&1 || fail "uv is not installed" \
  "curl -LsSf https://astral.sh/uv/install.sh | sh"
ok "uv present"

# ── 2 · this machine can describe itself ────────────────────────────────────
# Run before anything networked: a device that cannot read its own hardware is
# a device whose profile will be empty, and that is better known now.
PROFILE_SUMMARY="$(uv run --project runtime/manager/core python -c "
from prometheus_manager_core.profile import device_profile
p = device_profile()
if p.probe_errors:
    raise SystemExit('probes failed: ' + '; '.join(p.probe_errors))
a = p.accelerator
print(f\"{p.os}/{p.arch} · {p.cpu_cores_physical}c · {p.ram_total_mb}MB RAM · {a.kind if a else '?'}\")
" 2>&1)" || fail "this machine could not read its own hardware" "$PROFILE_SUMMARY"
ok "profile: $PROFILE_SUMMARY"

# ── 3 · the coordinator answers ─────────────────────────────────────────────
curl -fsS --max-time 5 "$COORDINATOR/health" >/dev/null 2>&1 \
  || fail "no answer from the coordinator at $COORDINATOR" \
          "check the address and that its manager binds beyond loopback ([api] host)"
ok "coordinator reachable"

# ── 4 · the credential mints a token ────────────────────────────────────────
# The identity service is loopback-only by design, so from another machine this
# is usually reached through an SSH tunnel. Saying which of the two failed —
# unreachable, or reachable and refusing — is the whole point of splitting it
# from the next check.
# Not reachable and reachable-but-refusing are different problems with
# different fixes, and collapsing them into one message is how an operator
# spends an afternoon on a tunnel that was never broken.
TOKEN_BODY="$(mktemp)"
TOKEN_STATUS="$(curl -s -o "$TOKEN_BODY" -w '%{http_code}' --max-time 10 -X POST "$AUTH_URL" \
  -d "grant_type=client_credentials" \
  -d "client_id=$PMGR_FLEET_CLIENT_ID" \
  -d "client_secret=$PMGR_FLEET_CLIENT_SECRET" \
  -d "scope=fleet:heartbeat node:$NODE_ID" || true)"
case "$TOKEN_STATUS" in
  000) rm -f "$TOKEN_BODY"; fail "no answer from the identity service at $AUTH_URL" \
            "it is loopback-only by design — from another machine: ssh -N -L 9000:127.0.0.1:9000 <coordinator-host>" ;;
  200) : ;;
  *)   DETAIL="$(cat "$TOKEN_BODY")"; rm -f "$TOKEN_BODY"
       fail "the identity service refused this credential ($TOKEN_STATUS)" \
            "it must exist AND carry both scopes — fleet:heartbeat and node:$NODE_ID. A credential for a different node is refused here, not later. Answer: $DETAIL" ;;
esac
TOKEN="$(sed -n 's/.*"access_token":"\([^"]*\)".*/\1/p' "$TOKEN_BODY")"
rm -f "$TOKEN_BODY"
[ -n "$TOKEN" ] || fail "the identity service answered 200 without a token" "check the auth-service logs"
ok "credential accepted, with the node scope"

# ── 5 · the node is registered, and the beat lands ──────────────────────────
# A heartbeat from an unknown id is a 404 on purpose: membership is an operator
# decision, not something a heartbeat grants. So this check distinguishes "your
# credential is wrong" from "nobody registered this node".
STATUS="$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 -X POST \
  "$COORDINATOR/v1/fleet/nodes/$NODE_ID/heartbeat" -H "Authorization: Bearer $TOKEN")"
case "$STATUS" in
  200) ok "heartbeat accepted — this node is in the fleet" ;;
  404) fail "the coordinator holds no node with id $NODE_ID" \
            "register it first from the dashboard (Nodes → register); membership is not granted by a heartbeat" ;;
  401|403) fail "the coordinator refused the credential ($STATUS)" \
            "the credential needs BOTH scopes: fleet:heartbeat and node:$NODE_ID" ;;
  *) fail "unexpected answer from the coordinator: $STATUS" ;;
esac

[ "$DRY_RUN" = "1" ] && { echo "dry run: everything checks out, nothing written."; exit 0; }

# ── 6 · write the two files ─────────────────────────────────────────────────
CONFIG="runtime/manager/manager-$NAME.toml"
ENV_FILE="runtime/manager/$NAME.env"
mkdir -p "runtime/manager" "runtime/logs/$NAME" "runtime/run/$NAME"

cat > "$CONFIG" <<TOML
# Written by scripts/join-fleet.sh for node '$NAME'.
[api]
# Beyond loopback: the gateway reads this node's backends over the network.
host = "0.0.0.0"
port = $PORT
jwks_url = "${AUTH_URL%/oauth2/token}/.well-known/jwks.json"
proxy_host = ""

[server]
# Loopback only, and the manager refuses anything else: engines are never
# exposed directly, only through the gateway.
host = "127.0.0.1"
binary = "~/.local/bin/llama-server"
start_timeout_s = 60
stop_timeout_s = 10
log_dir = "runtime/logs/$NAME"
pid_dir = "runtime/run/$NAME"

[fleet]
coordinator = false
coordinator_url = "$COORDINATOR"
auth_token_url = "$AUTH_URL"

[registry]
path = "runtime/manager/registry-$NAME.db"

[downloads]
dir = "runtime/models/downloads"
hf_token_env = "HF_TOKEN"
TOML
ok "wrote $CONFIG"

umask 077
cat > "$ENV_FILE" <<ENV
PMGR_FLEET_NODE_ID=$NODE_ID
PMGR_FLEET_CLIENT_ID=$PMGR_FLEET_CLIENT_ID
PMGR_FLEET_CLIENT_SECRET=$PMGR_FLEET_CLIENT_SECRET
ENV
ok "wrote $ENV_FILE (0600 — it holds a secret)"

cat <<NEXT

Start it with:

  uv run --env-file $ENV_FILE --project runtime/manager/api \\
    pmgr-api --config $CONFIG

It will report its profile on the first beat. Confirm from the coordinator side
with the Nodes page: the node shows what it is, not just that it is up.
NEXT
