"""Tests for RM-07 — fine-grained per-model authorization scopes.

See memory/roadmap.md RM-07 and memory/wiki/auth-model.md.
"""

from jose import jwt

from prometheus_auth.schemas import invalid_scopes, is_valid_scope

from .conftest import ADMIN_HEADERS, register_client

# ── is_valid_scope / invalid_scopes ──────────────────────────────────────────


def test_platform_scope_still_valid():
    assert is_valid_scope("inference:read")


def test_model_scope_valid():
    assert is_valid_scope("model:llama3-8b-q4-local")


def test_model_scope_requires_id_after_prefix():
    assert not is_valid_scope("model:")


def test_unknown_scope_invalid():
    assert not is_valid_scope("not:a:scope")


def test_invalid_scopes_returns_only_the_bad_ones():
    scopes = {"inference:read", "model:llama3-8b", "bogus:scope"}
    assert invalid_scopes(scopes) == {"bogus:scope"}


# ── Client registration (admin API) ──────────────────────────────────────────


async def test_register_client_with_model_scope(client):
    """A model:<id> scope is accepted alongside platform scopes."""
    data = await register_client(client, scopes=["inference:read", "model:llama3-8b-q4-local"])
    assert "model:llama3-8b-q4-local" in data["allowed_scopes"]


async def test_register_client_with_malformed_model_scope_rejected(client):
    resp = await client.post(
        "/admin/clients",
        json={"client_name": "bad", "role": "app", "allowed_scopes": ["model:"]},
        headers=ADMIN_HEADERS,
    )
    assert resp.status_code == 422


# ── Token issuance ────────────────────────────────────────────────────────────


async def test_token_carries_model_scope(client):
    """A model:<id> scope granted to the client is included in the issued JWT."""
    data = await register_client(client, scopes=["inference:read", "model:llama3-8b-q4-local"])
    resp = await client.post(
        "/oauth2/token",
        data={
            "grant_type": "client_credentials",
            "client_id": data["client_id"],
            "client_secret": data["client_secret"],
            "scope": "inference:read model:llama3-8b-q4-local",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "model:llama3-8b-q4-local" in body["scope"].split()

    payload = jwt.get_unverified_claims(body["access_token"])
    assert "model:llama3-8b-q4-local" in payload["scope"].split()


async def test_token_rejects_model_scope_not_granted_to_client(client):
    """AC-4-equivalent: requesting a model:* scope outside allowed_scopes → 400."""
    data = await register_client(client, scopes=["inference:read", "model:model-a"])
    resp = await client.post(
        "/oauth2/token",
        data={
            "grant_type": "client_credentials",
            "client_id": data["client_id"],
            "client_secret": data["client_secret"],
            "scope": "inference:read model:model-b",
        },
    )
    assert resp.status_code == 400
    assert resp.json()["error"] == "invalid_scope"
