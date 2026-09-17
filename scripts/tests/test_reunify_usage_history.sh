#!/usr/bin/env bash
# PRM-113: the reunify tool's merge path, which is the only part with real
# logic — a day that already has rows under the new id must be summed, not
# renamed into a unique-key violation.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "${TMP}"' EXIT
PASS=0; FAIL=0
_pass() { echo "  PASS: $1"; PASS=$((PASS+1)); }
_fail() { echo "  FAIL: $1"; FAIL=$((FAIL+1)); }

# Invoked through `uv run --script`, not a bare `python3`, because that is what
# the shebang declares and what an operator actually runs. Calling `python3
# script.py` bypasses the shebang and uses whatever interpreter happens to be
# on PATH — here a system 3.9, which cannot run `zip(strict=True)`. This test
# passed for weeks only because PATH happened to resolve to a newer one; a
# check that is *almost* what really runs is worse than none, because it goes
# green while hiding the difference.

python3 - "${TMP}" <<'PY'
import sqlite3, sys, uuid
tmp = sys.argv[1]
reg = sqlite3.connect(f"{tmp}/registry.db")
reg.execute("CREATE TABLE models (id TEXT PRIMARY KEY, slug TEXT)")
reg.execute("INSERT INTO models VALUES ('cat-id','pretty-name')")
reg.commit(); reg.close()

g = sqlite3.connect(f"{tmp}/gateway.db")
g.execute("""CREATE TABLE usage_events (id TEXT PRIMARY KEY, model_id TEXT,
             model_slug TEXT, prompt_tokens INT)""")
g.execute("""CREATE TABLE usage_daily (id TEXT PRIMARY KEY, day TEXT, client_id TEXT,
             model_id TEXT, model_slug TEXT, prompt_tokens INT, completion_tokens INT,
             request_count INT, cost_usd REAL)""")
g.execute("INSERT INTO usage_events VALUES (?,'pretty-name','pretty-name',10)", (str(uuid.uuid4()),))
# same day+client under both names -> must merge
g.execute("INSERT INTO usage_daily VALUES (?,'2026-01-01','c','pretty-name','pretty-name',10,5,2,1.0)", (str(uuid.uuid4()),))
g.execute("INSERT INTO usage_daily VALUES (?,'2026-01-01','c','cat-id','cat-id',100,50,7,2.0)", (str(uuid.uuid4()),))
g.commit(); g.close()
PY

uv run --script "${ROOT}/scripts/reunify_usage_history.py" \
  --gateway-db "${TMP}/gateway.db" --registry-db "${TMP}/registry.db" --apply >/dev/null 2>&1

RESULT="$(python3 - "${TMP}" <<'PY'
import sqlite3, sys
g = sqlite3.connect(f"{sys.argv[1]}/gateway.db")
row = g.execute("SELECT prompt_tokens, completion_tokens, request_count, cost_usd FROM usage_daily WHERE model_id='cat-id'").fetchone()
left = g.execute("SELECT COUNT(*) FROM usage_daily WHERE model_id='pretty-name'").fetchone()[0]
ev = g.execute("SELECT model_id, model_slug FROM usage_events").fetchone()
rows = g.execute("SELECT COUNT(*) FROM usage_daily").fetchone()[0]
print(f"{row[0]}|{row[1]}|{row[2]}|{row[3]}|{left}|{ev[0]}|{ev[1]}|{rows}")
PY
)"
IFS='|' read -r PT CT RC COST LEFT EV_ID EV_SLUG NROWS <<< "${RESULT}"

[[ "${PT}" == "110" ]] && _pass "prompt_tokens summed (10+100)" || _fail "prompt_tokens=${PT}, expected 110"
[[ "${CT}" == "55" ]] && _pass "completion_tokens summed (5+50)" || _fail "completion_tokens=${CT}, expected 55"
[[ "${RC}" == "9" ]] && _pass "request_count summed (2+7)" || _fail "request_count=${RC}, expected 9"
[[ "${COST}" == "3.0" ]] && _pass "cost summed (1.0+2.0)" || _fail "cost=${COST}, expected 3.0"
[[ "${LEFT}" == "0" ]] && _pass "nothing left under the old name" || _fail "${LEFT} rows still under old name"
[[ "${NROWS}" == "1" ]] && _pass "the two daily rows became one" || _fail "${NROWS} daily rows, expected 1"
[[ "${EV_ID}" == "cat-id" ]] && _pass "event re-keyed to the catalog id" || _fail "event model_id=${EV_ID}"
[[ "${EV_SLUG}" == "pretty-name" ]] && _pass "the name it was billed under is preserved" || _fail "event model_slug=${EV_SLUG}"

echo ""
echo "  Results: ${PASS} passed, ${FAIL} failed"
[[ "${FAIL}" -eq 0 ]] || exit 1
