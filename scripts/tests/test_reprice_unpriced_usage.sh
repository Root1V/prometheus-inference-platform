#!/usr/bin/env bash
# PRM-117: the repricing tool. The logic worth pinning is what it REFUSES —
# RM-60 says a row is priced at the rate in force when it was used and never
# re-rated, so a row older than its model's price must survive untouched even
# though a price now exists for it. Everything else is arithmetic.
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
g = sqlite3.connect(f"{sys.argv[1]}/gateway.db")
g.execute("""CREATE TABLE model_price_config (model_id TEXT PRIMARY KEY,
             prompt_price_per_1m REAL, completion_price_per_1m REAL,
             image_price REAL, updated_at TEXT)""")
g.execute("""CREATE TABLE usage_events (id TEXT PRIMARY KEY, recorded_at TEXT, day TEXT,
             client_id TEXT, model_id TEXT, model_slug TEXT, request_kind TEXT,
             prompt_tokens INT, completion_tokens INT, image_count INT,
             prompt_price_per_1m REAL, completion_price_per_1m REAL,
             image_price_each REAL, cost_usd REAL)""")
g.execute("""CREATE TABLE usage_daily (id TEXT PRIMARY KEY, day TEXT, client_id TEXT,
             model_id TEXT, prompt_tokens INT, completion_tokens INT, request_count INT,
             cost_usd REAL, prompt_cost_usd REAL, completion_cost_usd REAL,
             image_cost_usd REAL)""")

g.execute("INSERT INTO model_price_config VALUES ('cat-id',1000.0,2000.0,NULL,'2026-01-10 00:00:00')")
g.execute("INSERT INTO model_price_config VALUES ('img-model',NULL,NULL,0.5,'2026-01-10 00:00:00')")
g.execute("INSERT INTO model_price_config VALUES ('half-priced',1000.0,NULL,NULL,'2026-01-10 00:00:00')")

def ev(eid, rec, model, slug, kind="chat", pt=0, ct=0, imgs=0, cost=None):
    g.execute("INSERT INTO usage_events VALUES (?,?,?,?,?,?,?,?,?,?,NULL,NULL,NULL,?)",
              (eid, rec, rec[:10], "c", model, slug, kind, pt, ct, imgs, cost))

# billed under the public name after the price existed -> repairable
ev("a", "2026-01-20 00:00:00", "cat-id", "pretty-name", pt=1000, ct=500)
# identical usage, but from BEFORE the price was set -> must be refused
ev("b", "2026-01-05 00:00:00", "cat-id", "pretty-name", pt=1000, ct=500)
# already priced -> not a candidate at all
ev("c", "2026-01-20 00:00:00", "cat-id", "pretty-name", pt=1000, ct=500, cost=9.99)
# no price under either name -> left alone
ev("d", "2026-01-20 00:00:00", "no-price", "no-price", pt=1000, ct=500)
# image priced per image, not per token
ev("e", "2026-01-20 00:00:00", "img-model", "img-model", kind="image", imgs=4)
# half a token price is not a discount -> left alone
ev("f", "2026-01-20 00:00:00", "half-priced", "half-priced", pt=1000, ct=500)
# resolvable by SLUG rather than id, which is how the bug presented
ev("g", "2026-01-20 00:00:00", "some-instance", "cat-id", pt=1000, ct=500)

g.execute("INSERT INTO usage_daily VALUES (?,'2026-01-20','c','cat-id',1000,500,1,NULL,NULL,NULL,NULL)", (str(uuid.uuid4()),))
g.execute("INSERT INTO usage_daily VALUES (?,'2026-01-05','c','cat-id',1000,500,1,NULL,NULL,NULL,NULL)", (str(uuid.uuid4()),))
g.commit(); g.close()
PY

OUT="$(uv run --script "${ROOT}/scripts/reprice_unpriced_usage.py" --gateway-db "${TMP}/gateway.db" --apply 2>&1)"
q() { sqlite3 "${TMP}/gateway.db" "$1"; }

# 1000 * 1000/1e6 + 500 * 2000/1e6 = 1.0 + 1.0 = 2.0
[[ "$(q "SELECT ROUND(cost_usd,6) FROM usage_events WHERE id='a'")" == "2.0" ]] \
  && _pass "repaired row priced with the gateway's own arithmetic" || _fail "id=a cost=$(q "SELECT cost_usd FROM usage_events WHERE id='a'")"
[[ "$(q "SELECT prompt_price_per_1m FROM usage_events WHERE id='a'")" == "1000.0" ]] \
  && _pass "the rate applied is recorded on the row" || _fail "id=a kept no rate"
[[ "$(q "SELECT cost_usd IS NULL FROM usage_events WHERE id='b'")" == "1" ]] \
  && _pass "a row older than its price is refused (RM-60)" || _fail "id=b was re-rated"
[[ "$(q "SELECT cost_usd FROM usage_events WHERE id='c'")" == "9.99" ]] \
  && _pass "an already-priced row is never touched" || _fail "id=c changed"
[[ "$(q "SELECT cost_usd IS NULL FROM usage_events WHERE id='d'")" == "1" ]] \
  && _pass "no price configured stays unpriced, not zero" || _fail "id=d was given a cost"
[[ "$(q "SELECT ROUND(cost_usd,6) FROM usage_events WHERE id='e'")" == "2.0" ]] \
  && _pass "images price per image (4 x 0.5)" || _fail "id=e cost=$(q "SELECT cost_usd FROM usage_events WHERE id='e'")"
[[ "$(q "SELECT cost_usd IS NULL FROM usage_events WHERE id='f'")" == "1" ]] \
  && _pass "half a token price is not a discount" || _fail "id=f was priced"
[[ "$(q "SELECT ROUND(cost_usd,6) FROM usage_events WHERE id='g'")" == "2.0" ]] \
  && _pass "a price found by slug is applied too" || _fail "id=g cost=$(q "SELECT cost_usd FROM usage_events WHERE id='g'")"

# the rollup gets the same money, added rather than recomputed
[[ "$(q "SELECT ROUND(cost_usd,6) FROM usage_daily WHERE day='2026-01-20'")" == "2.0" ]] \
  && _pass "the daily rollup moved by the same amount" || _fail "daily=$(q "SELECT cost_usd FROM usage_daily WHERE day='2026-01-20'")"
[[ "$(q "SELECT cost_usd IS NULL FROM usage_daily WHERE day='2026-01-05'")" == "1" ]] \
  && _pass "the refused row's day is left alone" || _fail "the 01-05 rollup was touched"

# and running it twice must not double the money
uv run --script "${ROOT}/scripts/reprice_unpriced_usage.py" --gateway-db "${TMP}/gateway.db" --apply >/dev/null 2>&1
[[ "$(q "SELECT ROUND(cost_usd,6) FROM usage_daily WHERE day='2026-01-20'")" == "2.0" ]] \
  && _pass "idempotent — a second run bills nothing again" || _fail "daily doubled to $(q "SELECT cost_usd FROM usage_daily WHERE day='2026-01-20'")"

echo "$OUT" | grep -q "2026-01-05" \
  && _pass "the refusal is reported, not silent" || _fail "the plan never mentioned the refused row"

echo
echo "  Results: ${PASS} passed, ${FAIL} failed"
[[ "${FAIL}" -eq 0 ]]
