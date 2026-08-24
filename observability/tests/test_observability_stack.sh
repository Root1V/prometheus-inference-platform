#!/usr/bin/env bash
# observability/tests/test_observability_stack.sh
# Smoke-tests for the Grafana + Loki + Tempo observability stack.
# Requires the full stack to be running (podman compose up -d).
# See: memory/specs/021-ops-observability-stack.md
#
# Usage:
#   bash observability/tests/test_observability_stack.sh
#
# All tests:
#   AC-1/AC-4  Loki /ready and /metrics
#   AC-9       Tempo /ready
#   AC-5       Grafana /api/health
#   AC-1       Loki push — structured JSON log line accepted
#   AC-2       Loki query — pushed log line is queryable
#   AC-7       Grafana rejects anonymous access (401)
#   AC-8       Grafana rejects a JWT without ops:dashboard scope (403/401)

set -euo pipefail

LOKI_URL="${LOKI_URL:-http://localhost:3100}"
TEMPO_URL="${TEMPO_URL:-http://localhost:3200}"
GRAFANA_URL="${GRAFANA_URL:-http://localhost:3000}"
PASS=0
FAIL=0

_ok()   { echo "  PASS  $*"; ((PASS++)); }
_fail() { echo "  FAIL  $*"; ((FAIL++)); }

_http_check() {
  # usage: _http_check "label" <expected_status> <curl_args...>
  local label="$1"; shift
  local expected="$1"; shift
  local actual
  actual=$(curl --silent --output /dev/null --write-out "%{http_code}" "$@")
  if [[ "$actual" == "$expected" ]]; then
    _ok "$label (HTTP $actual)"
  else
    _fail "$label (expected HTTP $expected, got $actual)"
  fi
}

echo ""
echo "=== Prometheus Observability Stack — Smoke Tests ==="
echo ""

# ── AC-4: Loki readiness ────────────────────────────────────────────────────
echo "Loki"
_http_check "Loki /ready"   "200" "${LOKI_URL}/ready"
_http_check "Loki /metrics" "200" "${LOKI_URL}/metrics"

# ── AC-9: Tempo readiness ───────────────────────────────────────────────────
echo ""
echo "Tempo"
_http_check "Tempo /ready" "200" "${TEMPO_URL}/ready"

# ── AC-5: Grafana health ────────────────────────────────────────────────────
echo ""
echo "Grafana"
_http_check "Grafana /api/health" "200" "${GRAFANA_URL}/api/health"

# ── AC-1: Loki push — structured JSON log line ──────────────────────────────
echo ""
echo "Loki push / query"
NOW_NS=$(date +%s%N 2>/dev/null || python3 -c "import time; print(int(time.time()*1e9))")
PUSH_PAYLOAD=$(cat <<JSON
{
  "streams": [{
    "stream": {"service": "test-smoke", "level": "info"},
    "values": [["${NOW_NS}", "{\"level\":\"info\",\"event\":\"smoke_test\",\"component\":\"observability_test\",\"trace_id\":\"test-trace-001\"}"]]
  }]
}
JSON
)

PUSH_STATUS=$(curl --silent --output /dev/null --write-out "%{http_code}" \
  -X POST "${LOKI_URL}/loki/api/v1/push" \
  -H "Content-Type: application/json" \
  -d "${PUSH_PAYLOAD}")

if [[ "$PUSH_STATUS" == "204" ]]; then
  _ok "Loki push (HTTP 204)"
else
  _fail "Loki push (expected HTTP 204, got ${PUSH_STATUS})"
fi

# ── AC-2: Loki query — pushed line is queryable ─────────────────────────────
# Wait briefly for ingestion then query
sleep 3
QUERY_RESULT=$(curl --silent \
  "${LOKI_URL}/loki/api/v1/query_range?query=%7Bservice%3D%22test-smoke%22%7D&limit=5&start=$(date -v -30S +%s%N 2>/dev/null || python3 -c 'import time; print(int((time.time()-30)*1e9))')&end=${NOW_NS}")

if echo "${QUERY_RESULT}" | python3 -c "import sys,json; d=json.load(sys.stdin); assert len(d['data']['result']) > 0" 2>/dev/null; then
  _ok "Loki query returned pushed log line"
else
  _fail "Loki query returned no results (ingestion may be delayed — retry manually)"
fi

# ── AC-7: Grafana rejects anonymous access ──────────────────────────────────
echo ""
echo "Grafana auth"
ANON_STATUS=$(curl --silent --output /dev/null --write-out "%{http_code}" \
  "${GRAFANA_URL}/api/dashboards/uid/prometheus-ops")
if [[ "$ANON_STATUS" == "401" || "$ANON_STATUS" == "403" ]]; then
  _ok "Grafana rejects anonymous dashboard access (HTTP ${ANON_STATUS})"
else
  _fail "Grafana did NOT reject anonymous access (HTTP ${ANON_STATUS})"
fi

# ── AC-8: Grafana rejects JWT without ops:dashboard scope ──────────────────
# Build a minimal unsigned/tampered JWT with inference:read scope only
NO_SCOPE_JWT="eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJ0ZXN0QGV4YW1wbGUuY29tIiwic2NvcGUiOiJpbmZlcmVuY2U6cmVhZCJ9.INVALIDSIGNATURE"
NO_SCOPE_STATUS=$(curl --silent --output /dev/null --write-out "%{http_code}" \
  -H "X-JWT-Assertion: ${NO_SCOPE_JWT}" \
  "${GRAFANA_URL}/api/dashboards/uid/prometheus-ops")
if [[ "$NO_SCOPE_STATUS" == "401" || "$NO_SCOPE_STATUS" == "403" ]]; then
  _ok "Grafana rejects JWT without ops:dashboard scope (HTTP ${NO_SCOPE_STATUS})"
else
  _fail "Grafana did NOT reject JWT without scope (HTTP ${NO_SCOPE_STATUS})"
fi

# ── Summary ─────────────────────────────────────────────────────────────────
echo ""
echo "=== Results: ${PASS} passed, ${FAIL} failed ==="
echo ""

if [[ "${FAIL}" -gt 0 ]]; then
  exit 1
fi
