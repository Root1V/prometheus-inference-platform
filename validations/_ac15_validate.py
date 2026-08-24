# /// script
# dependencies = ["python-dotenv"]
# ///
"""One-shot AC-15 circuit breaker validation script."""
import os
import sys
import time
import json
import urllib.request
import urllib.error
import urllib.parse

GW = "http://localhost:8000"
AUTH = "http://localhost:9000"

# ── helpers ──────────────────────────────────────────────────────────────────

def http_post(url: str, data: dict, headers: dict = {}) -> tuple[int, dict, dict]:
    body = json.dumps(data).encode()
    req = urllib.request.Request(
        url, data=body,
        headers={"Content-Type": "application/json", **headers},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status, json.loads(r.read()), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read()), dict(e.headers)


def http_get(url: str, headers: dict = {}) -> tuple[int, dict, dict]:
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read()), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read()), dict(e.headers)


def http_form(url: str, data: dict, headers: dict = {}) -> tuple[int, dict, dict]:
    body = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(
        url, data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded", **headers},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status, json.loads(r.read()), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read()), dict(e.headers)


def ok(msg: str) -> None:
    print(f"  ✅  {msg}")

def fail(msg: str) -> None:
    print(f"  ❌  {msg}")
    sys.exit(1)

# ── main ─────────────────────────────────────────────────────────────────────

import dotenv  # type: ignore
dotenv.load_dotenv("auth-service/.env")
admin_key = os.environ.get("AUTH_ADMIN_API_KEY", "")
if not admin_key:
    fail("AUTH_ADMIN_API_KEY not set — check auth-service/.env")
print(f"Admin key: {admin_key[:8]}…")

# 1. Register inference client
status, body, _ = http_post(
    f"{AUTH}/admin/clients",
    {"client_name": "ac15-cb-test", "role": "app", "allowed_scopes": ["inference:read"]},
    {"X-Admin-Key": admin_key},
)
if status != 200 or "client_id" not in body:
    fail(f"Client registration failed: {status} {body}")
client_id, client_secret = body["client_id"], body["client_secret"]
ok(f"Registered inference client → {client_id[:12]}…")

# 2. Get inference token
status, body, _ = http_form(f"{AUTH}/oauth2/token", {
    "grant_type": "client_credentials",
    "client_id": client_id,
    "client_secret": client_secret,
    "scope": "inference:read",
})
if status != 200:
    fail(f"Token issuance failed: {status} {body}")
token = body["access_token"]
auth_hdr = {"Authorization": f"Bearer {token}"}
ok("Token issued")

# 3. Register admin client for /v1/backends
status, body, _ = http_post(
    f"{AUTH}/admin/clients",
    {"client_name": "ac15-admin-test", "role": "admin", "allowed_scopes": ["admin:read", "inference:read"]},
    {"X-Admin-Key": admin_key},
)
if status != 200 or "client_id" not in body:
    fail(f"Admin client registration failed: {status} {body}")
adm_id, adm_secret = body["client_id"], body["client_secret"]
status, body, _ = http_form(f"{AUTH}/oauth2/token", {
    "grant_type": "client_credentials",
    "client_id": adm_id,
    "client_secret": adm_secret,
    "scope": "admin:read inference:read",
})
if status != 200:
    fail(f"Admin token issuance failed: {status} {body}")
admin_token = body["access_token"]
ok("Admin token issued")

# 4. Send requests to trigger circuit breaker (threshold = 5)
print("\nSending requests to llama3-1b-q4-local (server is DOWN)…")
payload = {
    "model": "llama3-1b-q4-local",
    "messages": [{"role": "user", "content": "ping"}],
    "max_tokens": 5,
}

got_503 = False
retry_after_val = None

for i in range(1, 12):
    status, body, hdrs = http_post(f"{GW}/v1/chat/completions", payload, auth_hdr)
    retry_after = hdrs.get("Retry-After") or hdrs.get("retry-after")
    title = body.get("title", body.get("detail", ""))
    print(f"  [{i}] HTTP {status}  title={title!r}  Retry-After={retry_after!r}")
    if status == 503 and retry_after:
        # Circuit fast-fail — this is the AC-15 path we want
        got_503 = True
        retry_after_val = retry_after
        break
    if status == 503 and not retry_after:
        # Backend unreachable (HALF-OPEN probe or first failures) — circuit opening
        print(f"       (backend-unreachable path — circuit not yet OPEN, retrying…)")
    time.sleep(0.5)

print()
if got_503:
    ok("AC-15: 503 Service Unavailable received when circuit is OPEN ✓")
    if retry_after_val and retry_after_val != "N/A":
        ok(f"AC-15: Retry-After header = {retry_after_val}s ✓")
    else:
        fail("AC-15: Retry-After header MISSING on 503 response")
else:
    fail("AC-15: never got 503 — circuit did not open after 8 requests")

# 5. Check /v1/backends for circuit state
status, backends_body, _ = http_get(f"{GW}/v1/backends",
                                    {"Authorization": f"Bearer {admin_token}"})
print(f"\nGET /v1/backends → {status}")
if status != 200:
    fail(f"/v1/backends failed: {status} {backends_body}")

target = next(
    (b for b in backends_body.get("data", []) if b.get("id") == "llama3-1b-q4-local"),
    None,
)
if not target:
    fail("llama3-1b-q4-local not found in /v1/backends")

print(f"\n  llama3-1b-q4-local:")
print(f"    circuit_state        = {target.get('circuit_state')}")
print(f"    consecutive_failures = {target.get('consecutive_failures')}")
print(f"    circuit_opened_at    = {target.get('circuit_opened_at')}")
print(f"    circuit_recovery_at  = {target.get('circuit_recovery_at')}")

if target.get("circuit_state") == "open":
    ok("AC-15: circuit_state=open confirmed in /v1/backends ✓")
else:
    fail(f"AC-15: circuit_state={target.get('circuit_state')} — expected 'open'")

if target.get("circuit_opened_at"):
    ok("AC-15: circuit_opened_at set ✓")
else:
    fail("AC-15: circuit_opened_at is null")

if target.get("circuit_recovery_at"):
    ok("AC-15: circuit_recovery_at set ✓")
else:
    fail("AC-15: circuit_recovery_at is null")

print("\n✅  AC-15 FULLY VALIDATED\n")
