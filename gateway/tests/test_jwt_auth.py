"""Tests for JWT Authentication Middleware.

Each test maps 1-to-1 with an Acceptance Criterion in:
memory/specs/002-jwt-authentication-middleware.md
"""

import logging
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import httpx
import respx
from httpx import ASGITransport, AsyncClient
from jose import jwt

from tests.conftest import build_test_app, make_token, public_pem_to_jwk
from prometheus_gateway.auth import jwks as jwks_module


# ── AC-1 ───────────────────────────────────────────────────────────────────


async def test_jwt_auth_AC1(client, rsa_keys):  # memory/specs/002-jwt-authentication-middleware.md
    """Given a valid RS256 JWT, when processed, then claims are on request.state."""
    token = make_token(rsa_keys["private"])

    resp = await client.get("/protected", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["user_id"] == "user-123"
    assert body["client_id"] == "client-abc"
    assert "inference:read" in body["scope"]


# ── AC-2 ───────────────────────────────────────────────────────────────────


async def test_jwt_auth_AC2(client):  # memory/specs/002-jwt-authentication-middleware.md
    """Given no Authorization header, when processed, then 401 missing-credentials."""
    resp = await client.get("/protected")

    assert resp.status_code == 401
    body = resp.json()
    assert body["type"].endswith("missing-credentials")
    assert body["status"] == 401


# ── AC-3 ───────────────────────────────────────────────────────────────────


async def test_jwt_auth_AC3(
    client, alt_rsa_keys
):  # memory/specs/002-jwt-authentication-middleware.md
    """Given a JWT signed with the wrong private key, when validated, then 401 invalid-token."""
    # Token signed by alt_rsa_keys — the app trusts rsa_keys only
    token = make_token(alt_rsa_keys["private"])

    resp = await client.get("/protected", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 401
    assert resp.json()["type"].endswith("invalid-token")


# ── AC-4 ───────────────────────────────────────────────────────────────────


async def test_jwt_auth_AC4(client, rsa_keys):  # memory/specs/002-jwt-authentication-middleware.md
    """Given an expired JWT (beyond clock skew), when validated, then 401 token-expired."""
    # exp is 120 s in the past — well beyond the 30 s clock-skew allowance
    token = make_token(rsa_keys["private"], exp_delta=-120)

    resp = await client.get("/protected", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 401
    assert resp.json()["type"].endswith("token-expired")


# ── AC-5 ───────────────────────────────────────────────────────────────────


async def test_jwt_auth_AC5(client, rsa_keys):  # memory/specs/002-jwt-authentication-middleware.md
    """Given a JWT with wrong iss, when validated, then 401 invalid-token."""
    token = make_token(rsa_keys["private"], iss="https://evil.example.com")

    resp = await client.get("/protected", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 401
    assert resp.json()["type"].endswith("invalid-token")


# ── AC-6 ───────────────────────────────────────────────────────────────────


async def test_jwt_auth_AC6(client, rsa_keys):  # memory/specs/002-jwt-authentication-middleware.md
    """Given a JWT with wrong aud, when validated, then 401 invalid-token."""
    token = make_token(rsa_keys["private"], aud="some-other-service")

    resp = await client.get("/protected", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 401
    assert resp.json()["type"].endswith("invalid-token")


# ── AC-7 ───────────────────────────────────────────────────────────────────


async def test_jwt_auth_AC7(client):  # memory/specs/002-jwt-authentication-middleware.md
    """Given a GET /health request, when processed, then 200 with no auth required."""
    resp = await client.get("/health")

    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


# ── AC-8 ───────────────────────────────────────────────────────────────────


async def test_jwt_auth_AC8(
    settings, rsa_keys
):  # memory/specs/002-jwt-authentication-middleware.md
    """Given a JWT whose jti is in the Redis revocation list, then 401 token-revoked."""
    revoked_jti = "revoked-jti-abc123"
    token = make_token(rsa_keys["private"], jti=revoked_jti)

    # Inject a fake Redis client with the jti pre-marked as revoked
    class FakeRedis:
        async def get(self, key: str) -> bytes | None:
            if key == f"prometheus:revoked:{revoked_jti}":
                return b"1"
            return None

    app = build_test_app(settings, redis_client=FakeRedis())
    # Give settings a Redis URL so revocation logic is exercised
    settings.jwt_revocation_redis_url = "redis://fake:6379/0"

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.get("/protected", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 401
    assert resp.json()["type"].endswith("token-revoked")


# ── AC-9 ───────────────────────────────────────────────────────────────────


async def test_jwt_auth_AC9_alg_none(client):  # memory/specs/002-jwt-authentication-middleware.md
    """Given a JWT with alg:none, when processed, then 401 invalid-token (algorithm pinning)."""
    import base64
    import json

    header = (
        base64.urlsafe_b64encode(json.dumps({"alg": "none", "typ": "JWT"}).encode())
        .rstrip(b"=")
        .decode()
    )
    payload_data = {
        "sub": "user-x",
        "iss": "https://auth.test",
        "aud": "prometheus-gateway",
        "iat": int(datetime.now(tz=timezone.utc).timestamp()),
        "exp": int((datetime.now(tz=timezone.utc) + timedelta(hours=1)).timestamp()),
        "jti": "some-jti",
    }
    payload_b64 = base64.urlsafe_b64encode(json.dumps(payload_data).encode()).rstrip(b"=").decode()
    # alg:none token — no signature (empty third segment)
    token = f"{header}.{payload_b64}."

    resp = await client.get("/protected", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 401
    assert resp.json()["type"].endswith("invalid-token")


async def test_jwt_auth_AC9_hs256(client):  # memory/specs/002-jwt-authentication-middleware.md
    """Given a JWT signed with HS256, when processed, then 401 invalid-token (algorithm pinning)."""
    payload = {
        "sub": "user-x",
        "iss": "https://auth.test",
        "aud": "prometheus-gateway",
        "iat": datetime.now(tz=timezone.utc),
        "exp": datetime.now(tz=timezone.utc) + timedelta(hours=1),
        "jti": "some-jti",
    }
    token = jwt.encode(payload, "symmetric-secret", algorithm="HS256")

    resp = await client.get("/protected", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 401
    assert resp.json()["type"].endswith("invalid-token")


# ── AC-10 ──────────────────────────────────────────────────────────────────


async def test_jwt_auth_AC10(client, rsa_keys):  # memory/specs/002-jwt-authentication-middleware.md
    """Given token passed as ?token= query param, when processed, then 401 missing-credentials."""
    token = make_token(rsa_keys["private"])

    resp = await client.get("/protected", params={"token": token})

    assert resp.status_code == 401
    assert resp.json()["type"].endswith("missing-credentials")


# ── AC-11 ──────────────────────────────────────────────────────────────────


async def test_jwt_auth_AC11(
    client, rsa_keys, caplog
):  # memory/specs/002-jwt-authentication-middleware.md
    """Given any request, when processed, then the raw JWT never appears in log output."""
    valid_token = make_token(rsa_keys["private"])
    bad_token = "invalid.token.here"

    with caplog.at_level(logging.DEBUG):
        # Successful auth path
        await client.get("/protected", headers={"Authorization": f"Bearer {valid_token}"})
        # Failed auth path
        await client.get("/protected", headers={"Authorization": f"Bearer {bad_token}"})
        # Missing header path
        await client.get("/protected")

    all_log_text = "\n".join(
        record.getMessage() + str(record.__dict__) for record in caplog.records
    )
    assert valid_token not in all_log_text, "Valid JWT token must not appear in log output"
    assert bad_token not in all_log_text, "Invalid JWT token must not appear in log output"


# ── AC-12 ──────────────────────────────────────────────────────────────────


@respx.mock
async def test_jwt_auth_AC12(
    rsa_keys, tmp_path
):  # memory/specs/002-jwt-authentication-middleware.md
    """Given JWT_JWKS_URL configured and stale cache, when TTL elapsed, keys are re-fetched."""
    jwks_url = "https://auth.test/.well-known/jwks.json"
    jwks_response = {"keys": [public_pem_to_jwk(rsa_keys["public"])]}

    call_count = {"n": 0}

    def jwks_handler(request):
        call_count["n"] += 1
        return httpx.Response(200, json=jwks_response)

    respx.get(jwks_url).mock(side_effect=jwks_handler)

    # First fetch at t=0 — should hit the endpoint
    with patch("prometheus_gateway.auth.jwks.time") as mock_time:
        mock_time.monotonic.return_value = 0.0
        await jwks_module.fetch_jwks_keys(jwks_url)

    assert call_count["n"] == 1, "First call must fetch from endpoint"

    # Within L1 TTL (t=10s < 30s L1 TTL) — should use in-process cache
    with patch("prometheus_gateway.auth.jwks.time") as mock_time:
        mock_time.monotonic.return_value = 10.0
        await jwks_module.fetch_jwks_keys(jwks_url)

    assert call_count["n"] == 1, "Within TTL must use cached keys"

    # After L1 TTL (t > 30s) — must re-fetch (Redis is None so falls through to live fetch)
    with patch("prometheus_gateway.auth.jwks.time") as mock_time:
        mock_time.monotonic.return_value = jwks_module._L1_CACHE_TTL + 1.0
        await jwks_module.fetch_jwks_keys(jwks_url)

    assert call_count["n"] == 2, "After TTL the middleware must fetch updated key set"
