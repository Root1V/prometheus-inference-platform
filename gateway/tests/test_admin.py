"""Tests for the RM-10 admin dashboard JSON API (/admin/api/*) and the
_is_exempt() split between the public SPA shell and the protected API.

See memory/roadmap.md RM-10 and memory/wiki/model-registry.md.
"""

from __future__ import annotations

import pytest
import respx
from httpx import ASGITransport, AsyncClient, Response

from prometheus_gateway.auth.middleware import _is_exempt
from tests.conftest import make_token

NODE_URL = "http://mac.local:8090"
AUTH_TOKEN_URL = "https://auth.test/token"


# ── _is_exempt() — SPA shell public, /admin/api/* protected ─────────────────


class TestAdminExemption:
    def test_admin_root_is_exempt(self):
        assert _is_exempt("/admin") is True

    def test_admin_spa_route_is_exempt(self):
        """Client-side routes (React Router) — e.g. deep-linked /admin/instances."""
        assert _is_exempt("/admin/instances") is True

    def test_admin_assets_are_exempt(self):
        assert _is_exempt("/admin/assets/index-abc123.js") is True

    def test_admin_api_is_not_exempt(self):
        assert _is_exempt("/admin/api/instances") is False

    def test_admin_api_root_is_not_exempt(self):
        assert _is_exempt("/admin/api") is False

    def test_admin_login_is_exempt(self):
        """No Bearer token exists yet at login time by definition."""
        assert _is_exempt("/admin/api/auth/login") is True


# ── App fixture ────────────────────────────────────────────────────────────


@pytest.fixture
def admin_settings(rsa_keys, tmp_path):
    from prometheus_gateway.config import Settings

    key_file = tmp_path / "public.pem"
    key_file.write_text(rsa_keys["public"])
    return Settings(
        jwt_issuer="https://auth.test",
        jwt_audience="prometheus-gateway",
        jwt_public_key_file=str(key_file),
        jwt_revocation_redis_url=None,
        rate_limit_strict=False,
        admin_dashboard_enabled=True,
        manager_nodes=f"mac={NODE_URL}",
        manager_client_id="gw-service",
        manager_client_secret="secret",
        auth_service_token_url=AUTH_TOKEN_URL,
        auth_service_tls_verify=True,
    )


@pytest.fixture
def admin_app(admin_settings):
    from prometheus_gateway.main import create_app
    from prometheus_gateway.models.registry import ModelRegistry

    registry = ModelRegistry.__new__(ModelRegistry)
    registry._models = {}
    return create_app(settings=admin_settings, registry=registry)


@pytest.fixture
async def gw(admin_app):
    async with AsyncClient(transport=ASGITransport(app=admin_app), base_url="http://test") as c:
        yield c


def _headers(rsa_keys, scope: str) -> dict[str, str]:
    token = make_token(rsa_keys["private"], scope=scope)
    return {"Authorization": f"Bearer {token}"}


def _mock_manager_token():
    respx.post(AUTH_TOKEN_URL).mock(
        return_value=Response(200, json={"access_token": "manager-token", "expires_in": 300})
    )


# ── POST /admin/api/auth/login ────────────────────────────────────────────────


async def test_login_succeeds_without_any_bearer_token(gw):
    """Proves the exemption actually works end-to-end, not just in _is_exempt()."""
    with respx.mock:
        respx.post(AUTH_TOKEN_URL).mock(
            return_value=Response(200, json={"access_token": "operator-jwt", "expires_in": 10800})
        )
        resp = await gw.post(
            "/admin/api/auth/login",
            json={"client_id": "op", "client_secret": "s3cr3t"},
        )
    assert resp.status_code == 200
    assert resp.json() == {"access_token": "operator-jwt", "expires_in": 10800}


async def test_login_forwards_admin_scope_request(gw):
    with respx.mock:
        route = respx.post(AUTH_TOKEN_URL).mock(
            return_value=Response(200, json={"access_token": "t", "expires_in": 10800})
        )
        await gw.post("/admin/api/auth/login", json={"client_id": "op", "client_secret": "s3cr3t"})
    sent = route.calls[0].request.content.decode()
    assert "scope=admin%3Aread+admin%3Awrite" in sent


async def test_login_invalid_credentials_normalized_to_401(gw):
    with respx.mock:
        respx.post(AUTH_TOKEN_URL).mock(
            return_value=Response(
                401,
                json={
                    "error": "invalid_client",
                    "error_description": "Invalid client credentials.",
                },
            )
        )
        resp = await gw.post(
            "/admin/api/auth/login",
            json={"client_id": "op", "client_secret": "wrong"},
        )
    assert resp.status_code == 401
    body = resp.json()
    assert body["type"].endswith("invalid-credentials")
    assert body["detail"] == "Invalid client credentials."


# ── GET /admin/api/nodes ──────────────────────────────────────────────────────


async def test_list_nodes_requires_admin_read(gw, rsa_keys):
    resp = await gw.get("/admin/api/nodes", headers=_headers(rsa_keys, "inference:read"))
    assert resp.status_code == 403


async def test_list_nodes_returns_configured_names(gw, rsa_keys):
    resp = await gw.get("/admin/api/nodes", headers=_headers(rsa_keys, "admin:read"))
    assert resp.status_code == 200
    assert resp.json() == {"nodes": ["mac"]}


# ── GET /admin/api/instances ──────────────────────────────────────────────────


async def test_list_instances_aggregates_and_tags_node(gw, rsa_keys):
    with respx.mock:
        _mock_manager_token()
        respx.get(f"{NODE_URL}/v1/backends").mock(
            return_value=Response(
                200,
                json={
                    "backends": [
                        {
                            "id": "model-a",
                            "state": "ready",
                            "backend_url": f"{NODE_URL.replace('8090', '8080')}",
                        }
                    ]
                },
            )
        )
        resp = await gw.get("/admin/api/instances", headers=_headers(rsa_keys, "admin:read"))
    assert resp.status_code == 200
    body = resp.json()
    assert body["unreachable_nodes"] == []
    assert body["instances"][0]["id"] == "model-a"
    assert body["instances"][0]["node"] == "mac"


async def test_list_instances_marks_unreachable_node(gw, rsa_keys):
    import httpx

    with respx.mock:
        _mock_manager_token()
        respx.get(f"{NODE_URL}/v1/backends").mock(side_effect=httpx.ConnectError("refused"))
        resp = await gw.get("/admin/api/instances", headers=_headers(rsa_keys, "admin:read"))
    assert resp.status_code == 200
    body = resp.json()
    assert body["instances"] == []
    assert body["unreachable_nodes"] == ["mac"]


# ── POST /admin/api/nodes/{node}/models (register) ───────────────────────────


async def test_register_requires_admin_write(gw, rsa_keys):
    resp = await gw.post(
        "/admin/api/nodes/mac/models",
        json={"id": "new-model", "port": 8090},
        headers=_headers(rsa_keys, "admin:read"),
    )
    assert resp.status_code == 403


async def test_register_unknown_node_returns_400(gw, rsa_keys):
    resp = await gw.post(
        "/admin/api/nodes/does-not-exist/models",
        json={"id": "new-model", "port": 8090},
        headers=_headers(rsa_keys, "admin:write"),
    )
    assert resp.status_code == 400
    assert resp.json()["type"].endswith("unknown-node")


async def test_register_success_proxies_to_node(gw, rsa_keys):
    with respx.mock:
        _mock_manager_token()
        respx.post(f"{NODE_URL}/v1/backends").mock(
            return_value=Response(201, json={"id": "new-model", "port": 8090})
        )
        resp = await gw.post(
            "/admin/api/nodes/mac/models",
            json={"id": "new-model", "port": 8090},
            headers=_headers(rsa_keys, "admin:write"),
        )
    assert resp.status_code == 201
    assert resp.json()["id"] == "new-model"


async def test_register_node_unreachable_returns_503(gw, rsa_keys):
    import httpx

    with respx.mock:
        _mock_manager_token()
        respx.post(f"{NODE_URL}/v1/backends").mock(side_effect=httpx.ConnectError("refused"))
        resp = await gw.post(
            "/admin/api/nodes/mac/models",
            json={"id": "new-model", "port": 8090},
            headers=_headers(rsa_keys, "admin:write"),
        )
    assert resp.status_code == 503
    assert resp.json()["type"].endswith("backend-unavailable")


# ── DELETE /admin/api/nodes/{node}/models/{id} (deregister) ──────────────────


async def test_deregister_success(gw, rsa_keys):
    with respx.mock:
        _mock_manager_token()
        respx.delete(f"{NODE_URL}/v1/backends/old-model").mock(return_value=Response(204))
        resp = await gw.delete(
            "/admin/api/nodes/mac/models/old-model", headers=_headers(rsa_keys, "admin:write")
        )
    assert resp.status_code == 204


# ── POST /admin/api/nodes/{node}/instances/{id}/{action} ─────────────────────


async def test_control_instance_start_success(gw, rsa_keys):
    with respx.mock:
        _mock_manager_token()
        respx.post(f"{NODE_URL}/v1/backends/model-a/start").mock(
            return_value=Response(200, json={"id": "model-a", "state": "ready"})
        )
        resp = await gw.post(
            "/admin/api/nodes/mac/instances/model-a/start",
            headers=_headers(rsa_keys, "admin:write"),
        )
    assert resp.status_code == 200
    assert resp.json()["state"] == "ready"


async def test_control_instance_unknown_action_returns_404(gw, rsa_keys):
    resp = await gw.post(
        "/admin/api/nodes/mac/instances/model-a/frobnicate",
        headers=_headers(rsa_keys, "admin:write"),
    )
    assert resp.status_code == 404


async def test_control_instance_lifecycle_conflict_proxied(gw, rsa_keys):
    with respx.mock:
        _mock_manager_token()
        respx.post(f"{NODE_URL}/v1/backends/model-a/stop").mock(
            return_value=Response(
                409,
                json={
                    "detail": {
                        "type": "https://prometheus.local/errors/lifecycle-conflict",
                        "title": "Lifecycle Conflict",
                        "status": 409,
                        "detail": "No running instance found for model-a",
                    }
                },
            )
        )
        resp = await gw.post(
            "/admin/api/nodes/mac/instances/model-a/stop",
            headers=_headers(rsa_keys, "admin:write"),
        )
    assert resp.status_code == 409
    # manager-api's {"detail": {...}} wrapping is flattened to match the
    # gateway's own RFC 9457 shape — one error contract regardless of origin.
    body = resp.json()
    assert body["type"].endswith("lifecycle-conflict")
    assert body["detail"] == "No running instance found for model-a"
