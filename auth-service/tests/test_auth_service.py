"""Tests for Prometheus Auth Service.

Maps 1-to-1 with memory/specs/005-auth-service.md Acceptance Criteria.
"""

import pytest
from jose import jwt

from .conftest import ADMIN_HEADERS, register_client


# ── AC-21: health ─────────────────────────────────────────────────────────────


async def test_auth_AC21_health(client):
    """AC-21: GET /health returns 200 {"status": "ok"} without auth."""
    resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


# ── AC-17: JWKS endpoint ──────────────────────────────────────────────────────


async def test_auth_AC17_jwks(client):
    """AC-17: GET /.well-known/jwks.json returns RSA public key with required fields."""
    resp = await client.get("/.well-known/jwks.json")
    assert resp.status_code == 200
    body = resp.json()
    assert "keys" in body
    key = body["keys"][0]
    assert key["kty"] == "RSA"
    assert key["use"] == "sig"
    assert key["alg"] == "RS256"
    assert "kid" in key
    assert "n" in key
    assert "e" in key


# ── AC-11: register client ────────────────────────────────────────────────────


async def test_auth_AC11_create_client(client):
    """AC-11: POST /admin/clients creates client and returns client_secret once."""
    data = await register_client(client, role="app", scopes=["inference:read"])
    assert "client_id" in data
    assert data["client_secret"].startswith("pmt_live_")
    assert data["role"] == "app"
    assert data["token_ttl_seconds"] == 300
    assert "inference:read" in data["allowed_scopes"]


async def test_auth_AC11_invalid_scope(client):
    """AC-11: Creating client with unknown scope returns 422."""
    resp = await client.post(
        "/admin/clients",
        json={"client_name": "bad", "role": "app", "allowed_scopes": ["not:a:scope"]},
        headers=ADMIN_HEADERS,
    )
    assert resp.status_code == 422


# ── AC-14: admin key required ────────────────────────────────────────────────


async def test_auth_AC14_missing_admin_key(client):
    """AC-14: /admin/* without X-Admin-Key returns 403."""
    resp = await client.post(
        "/admin/clients",
        json={"client_name": "x", "role": "app", "allowed_scopes": ["inference:read"]},
    )
    assert resp.status_code == 403


async def test_auth_AC14_wrong_admin_key(client):
    """AC-14: /admin/* with wrong key returns 403."""
    resp = await client.get("/admin/clients", headers={"X-Admin-Key": "wrong"})
    assert resp.status_code == 403


# ── AC-12: list clients ───────────────────────────────────────────────────────


async def test_auth_AC12_list_clients(client):
    """AC-12: GET /admin/clients returns list without client_secret_hash."""
    await register_client(client, name="list-test")
    resp = await client.get("/admin/clients", headers=ADMIN_HEADERS)
    assert resp.status_code == 200
    items = resp.json()
    assert len(items) >= 1
    for item in items:
        assert "client_secret_hash" not in item
        assert "client_id" in item
        assert "role" in item


# ── AC-1: token issuance ──────────────────────────────────────────────────────


async def test_auth_AC1_token_issuance(client, settings):
    """AC-1: Valid credentials return a JWT with all required claims."""
    data = await register_client(client, role="app", scopes=["inference:read"])

    resp = await client.post(
        "/oauth2/token",
        data={
            "grant_type": "client_credentials",
            "client_id": data["client_id"],
            "client_secret": data["client_secret"],
            "scope": "inference:read",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["token_type"] == "bearer"
    assert body["expires_in"] == 300
    assert body["scope"] == "inference:read"

    # Decode and verify claims without signature check (crypto already tested separately)
    payload = jwt.get_unverified_claims(body["access_token"])
    assert payload["sub"] == data["client_id"]
    assert payload["aud"] == "prometheus-gateway"
    assert payload["role"] == "app"
    assert "jti" in payload
    assert "client_name" in payload


# ── AC-3: invalid secret ──────────────────────────────────────────────────────


async def test_auth_AC3_invalid_secret(client):
    """AC-3: Wrong client_secret returns 401 invalid_client."""
    data = await register_client(client)
    resp = await client.post(
        "/oauth2/token",
        data={
            "grant_type": "client_credentials",
            "client_id": data["client_id"],
            "client_secret": "pmt_live_wrong",
            "scope": "inference:read",
        },
    )
    assert resp.status_code == 401
    assert resp.json()["error"] == "invalid_client"


# ── AC-4: invalid scope ───────────────────────────────────────────────────────


async def test_auth_AC4_scope_not_in_allowed(client):
    """AC-4: Requesting a scope not in allowed_scopes returns 400 invalid_scope."""
    data = await register_client(client, scopes=["inference:read"])
    resp = await client.post(
        "/oauth2/token",
        data={
            "grant_type": "client_credentials",
            "client_id": data["client_id"],
            "client_secret": data["client_secret"],
            "scope": "admin:models",
        },
    )
    assert resp.status_code == 400
    assert resp.json()["error"] == "invalid_scope"


# ── AC-5: deactivated client ──────────────────────────────────────────────────


async def test_auth_AC5_deactivated_client(client):
    """AC-5: Deactivated client cannot obtain tokens."""
    data = await register_client(client, name="to-deactivate")
    client_id = data["client_id"]

    # Deactivate
    resp = await client.delete(f"/admin/clients/{client_id}", headers=ADMIN_HEADERS)
    assert resp.status_code == 204

    # Token request must fail
    resp = await client.post(
        "/oauth2/token",
        data={
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": data["client_secret"],
            "scope": "inference:read",
        },
    )
    assert resp.status_code == 401
    assert resp.json()["error"] == "unauthorized_client"


# ── AC-6 through AC-9: role-based TTL ────────────────────────────────────────


@pytest.mark.parametrize(
    "role,expected_ttl",
    [
        ("admin", 10800),
        ("cognitive", 3600),
        ("agent", 600),
        ("app", 300),
    ],
)
async def test_auth_AC6to9_role_ttl(client, role, expected_ttl, settings):
    """AC-6..9: Token TTL matches the role's configured default."""
    data = await register_client(client, role=role, scopes=["inference:read"], name=f"role-{role}")
    assert data["token_ttl_seconds"] == expected_ttl

    resp = await client.post(
        "/oauth2/token",
        data={
            "grant_type": "client_credentials",
            "client_id": data["client_id"],
            "client_secret": data["client_secret"],
            "scope": "inference:read",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["expires_in"] == expected_ttl

    payload = jwt.get_unverified_claims(body["access_token"])
    assert payload["exp"] - payload["iat"] == expected_ttl


# ── AC-13: rotate secret ──────────────────────────────────────────────────────


async def test_auth_AC13_rotate_secret(client):
    """AC-13: Rotating secret invalidates old secret and provides new one."""
    data = await register_client(client, name="rotate-me")
    old_secret = data["client_secret"]

    resp = await client.post(
        f"/admin/clients/{data['client_id']}/rotate-secret",
        headers=ADMIN_HEADERS,
    )
    assert resp.status_code == 200
    new_secret = resp.json()["client_secret"]
    assert new_secret != old_secret
    assert new_secret.startswith("pmt_live_")

    # Old secret no longer works
    resp = await client.post(
        "/oauth2/token",
        data={
            "grant_type": "client_credentials",
            "client_id": data["client_id"],
            "client_secret": old_secret,
            "scope": "inference:read",
        },
    )
    assert resp.status_code == 401

    # New secret works
    resp = await client.post(
        "/oauth2/token",
        data={
            "grant_type": "client_credentials",
            "client_id": data["client_id"],
            "client_secret": new_secret,
            "scope": "inference:read",
        },
    )
    assert resp.status_code == 200


# ── AC-19: missing AUTH_ADMIN_API_KEY at startup ──────────────────────────────


def test_auth_AC19_missing_admin_key_startup(rsa_key_pem_files):
    """AC-19: Missing AUTH_ADMIN_API_KEY causes startup-time validation error."""
    priv, pub = rsa_key_pem_files
    with pytest.raises(Exception, match="AUTH_ADMIN_API_KEY"):
        from prometheus_auth.config import Settings as S

        S(
            auth_private_key_file=priv,
            auth_public_key_file=pub,
            auth_jwt_issuer="https://test",
            auth_admin_api_key="",  # empty triggers validation
        )
