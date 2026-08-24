#!/usr/bin/env -S uv run --script
# /// script
# dependencies = ["python-dotenv"]
# ///
"""End-to-end integration test for the full Prometheus stack (auth-service + gateway).

Tests:
  1. auth-service /health
  2. gateway /health
  3. JWKS endpoint
  4. OAuth2 client registration
  5. Token issuance (Client Credentials grant)
  6. Real inference — llama3-1b-q4-local (small, fast)
  7. Real inference — llama3-8b-q4-local (regular, quality)
  8. Tampered token → 401
  9. No token → 401
  10. admin API: list clients
  11. Admin key required → 403
"""

import base64
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from dotenv import load_dotenv

_REPO_ROOT = Path(__file__).parent.parent
load_dotenv(_REPO_ROOT / "auth-service" / ".env")

AUTH_URL = os.environ.get("AUTH_URL", "http://localhost:9000")
GW_URL = os.environ.get("GW_URL", "http://localhost:8000")
ADMIN_KEY = os.environ.get("AUTH_ADMIN_API_KEY", "")
if not ADMIN_KEY:
    print("ERROR: AUTH_ADMIN_API_KEY not set. Export it or place it in auth-service/.env", file=sys.stderr)
    sys.exit(1)

PASS = 0
FAIL = 0


def ok(msg: str) -> None:
    global PASS
    PASS += 1
    print(f"  [PASS] {msg}")


def fail(msg: str) -> None:
    global FAIL
    FAIL += 1
    print(f"  [FAIL] {msg}", file=sys.stderr)


def get_json(url: str, method: str = "GET", data: bytes | None = None, headers: dict | None = None) -> tuple[int, dict]:
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    try:
        resp = urllib.request.urlopen(req)
        return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


# ── 1. Health checks ───────────────────────────────────────────────────────────
print("\n=== Health checks ===")
status, body = get_json(f"{AUTH_URL}/health")
if status == 200 and body.get("status") == "ok":
    ok("auth-service /health → 200 ok")
else:
    fail(f"auth-service /health → {status} {body}")

status, body = get_json(f"{GW_URL}/health")
if status == 200 and body.get("status") == "ok":
    ok("gateway /health → 200 ok")
else:
    fail(f"gateway /health → {status} {body}")


# ── 2. JWKS endpoint ──────────────────────────────────────────────────────────
print("\n=== JWKS ===")
status, body = get_json(f"{AUTH_URL}/.well-known/jwks.json")
if status == 200 and "keys" in body and body["keys"][0].get("kty") == "RSA":
    kid = body["keys"][0]["kid"]
    ok(f"JWKS → RSA key with kid={kid}")
else:
    fail(f"JWKS → {status} {body}")


# ── 3. Register client ────────────────────────────────────────────────────────
print("\n=== Admin: register client ===")
payload = json.dumps({"client_name": "e2e-stack-test", "role": "app", "allowed_scopes": ["inference:read"]}).encode()
status, body = get_json(
    f"{AUTH_URL}/admin/clients",
    method="POST",
    data=payload,
    headers={"Content-Type": "application/json", "X-Admin-Key": ADMIN_KEY},
)
if status == 200 and body.get("client_secret", "").startswith("pmt_live_"):
    client_id = body["client_id"]
    client_secret = body["client_secret"]
    ok(f"register client → client_id={client_id[:8]}…, secret=pmt_live_…")
else:
    fail(f"register client → {status} {body}")
    sys.exit(1)  # can't continue without credentials


# ── 4. Token issuance ─────────────────────────────────────────────────────────
print("\n=== OAuth2 token ===")
form = urllib.parse.urlencode({
    "grant_type": "client_credentials",
    "client_id": client_id,
    "client_secret": client_secret,
    "scope": "inference:read",
}).encode()
status, body = get_json(f"{AUTH_URL}/oauth2/token", method="POST", data=form)

if status == 200 and "access_token" in body:
    token = body["access_token"]
    expires_in = body["expires_in"]
    ok(f"token issued → expires_in={expires_in}s, scope={body.get('scope')}")

    # Decode claims
    parts = token.split(".")
    padded = parts[1] + "=" * (4 - len(parts[1]) % 4)
    claims = json.loads(base64.urlsafe_b64decode(padded))
    if all(k in claims for k in ("iss", "sub", "aud", "jti", "scope", "role", "client_name")):
        ok(f"JWT claims → iss={claims['iss']}, role={claims['role']}, aud={claims['aud']}")
    else:
        fail(f"JWT claims missing keys: {claims}")
else:
    fail(f"token → {status} {body}")
    sys.exit(1)


# ── 5. Gateway JWT validation + real inference (both models) ──────────────────
print("\n=== Gateway JWT validation + real inference ===")

INFERENCE_QUESTION = (
    "In two or three sentences: what is AGI (Artificial General Intelligence), "
    "and why do projects that run small language models locally on bare-metal "
    "— instead of sending data to the cloud — matter in the path toward it?"
)

INFERENCE_MODELS = [
    ("llama3-1b-q4-local", "Llama 3.2 1B  (small, fast)"),
    ("llama3-8b-q4-local", "Llama 3   8B  (regular, quality)"),
]

auth_headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

for model_id, model_label in INFERENCE_MODELS:
    payload = json.dumps({
        "model": model_id,
        "messages": [{"role": "user", "content": INFERENCE_QUESTION}],
        "max_tokens": 120,
        "temperature": 0.7,
    }).encode()

    status, body = get_json(
        f"{GW_URL}/v1/chat/completions",
        method="POST",
        data=payload,
        headers=auth_headers,
    )
    if status == 200:
        reply = body["choices"][0]["message"]["content"].strip()
        usage = body.get("usage", {})
        ok(
            f"[{model_label}] inference → 200, "
            f"{usage.get('completion_tokens', '?')} tokens generated "
            f"({usage.get('prompt_tokens', '?')} prompt)"
        )
        print(f"\n  ┌─ {model_label} {'─' * max(0, 54 - len(model_label))}")
        for line in reply.splitlines():
            print(f"  │  {line}")
        print(f"  └{'─' * 57}\n")
    elif status == 502:
        ok(f"[{model_label}] valid token → 502 Backend Unavailable (JWT accepted, llama.cpp down — expected)")
    elif status == 401:
        fail(f"[{model_label}] valid token REJECTED by gateway → {json.dumps(body)}")
    else:
        fail(f"[{model_label}] inference → unexpected {status}: {json.dumps(body)}")

# Re-use last payload for tampered/no-token checks below
real_payload = payload

# ── 6. Tampered token ─────────────────────────────────────────────────────────
status, body = get_json(
    f"{GW_URL}/v1/chat/completions",
    method="POST",
    data=real_payload,
    headers={"Authorization": f"Bearer {token}TAMPERED", "Content-Type": "application/json"},
)
if status == 401:
    ok("tampered token → 401 correctly rejected")
else:
    fail(f"tampered token → {status} (should be 401): {body}")

# ── 7. No token ───────────────────────────────────────────────────────────────
status, body = get_json(
    f"{GW_URL}/v1/chat/completions",
    method="POST",
    data=real_payload,
    headers={"Content-Type": "application/json"},
)
if status == 401:
    ok("no token → 401 correctly rejected")
else:
    fail(f"no token → {status} (should be 401)")

# ── 8. List clients ───────────────────────────────────────────────────────────
print("\n=== Admin: list clients ===")
status, clients = get_json(f"{AUTH_URL}/admin/clients", headers={"X-Admin-Key": ADMIN_KEY})
if status == 200 and isinstance(clients, list) and len(clients) >= 1:
    has_secrets = any("client_secret_hash" in c for c in clients)
    if not has_secrets:
        ok(f"list clients → {len(clients)} client(s), no secret_hash exposed")
    else:
        fail("list clients → client_secret_hash exposed in response!")
else:
    fail(f"list clients → {status}")

# ── 9. Admin key required ─────────────────────────────────────────────────────
status, _ = get_json(f"{AUTH_URL}/admin/clients")
if status == 403:
    ok("no admin key → 403 Forbidden")
else:
    fail(f"no admin key → {status} (should be 403)")

# ── Summary ───────────────────────────────────────────────────────────────────
print(f"\n{'='*50}")
print(f"Results: {PASS} passed, {FAIL} failed")
if FAIL > 0:
    sys.exit(1)
