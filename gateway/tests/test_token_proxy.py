"""Tests for POST /oauth2/token — the gateway's proxy to the auth-service.

See docs/roadmap.md PRM-96: an SDK should reach the gateway and nothing else.
The point of these tests is that the gateway is a *transparent* token endpoint:
same path, same form body, same OAuth2 error shape as the service behind it.
"""

from __future__ import annotations

import httpx
import pytest
import respx
from httpx import ASGITransport, AsyncClient, Response

AUTH_TOKEN_URL = "https://auth.test/oauth2/token"


@pytest.fixture
def token_settings(rsa_keys, tmp_path):
    """A plain inference gateway — no admin dashboard, no UI.

    Deliberately not the admin fixture: the deployment an SDK talks to has the
    dashboard switched off, and the token path has to work there too.
    """
    from prometheus_gateway.config import Settings

    key_file = tmp_path / "public.pem"
    key_file.write_text(rsa_keys["public"])
    return Settings(
        jwt_issuer="https://auth.test",
        jwt_audience="prometheus-gateway",
        jwt_public_key_file=str(key_file),
        jwt_revocation_redis_url=None,
        rate_limit_strict=False,
        admin_dashboard_enabled=False,
        auth_service_token_url=AUTH_TOKEN_URL,
    )


def _build(settings):
    from prometheus_gateway.main import create_app
    from prometheus_gateway.models.registry import ModelRegistry

    registry = ModelRegistry.__new__(ModelRegistry)
    registry._models = {}
    return create_app(settings=settings, registry=registry)


@pytest.fixture
async def gw(token_settings):
    app = _build(token_settings)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


# ── The exemptions ─────────────────────────────────────────────────────────


def test_token_path_is_exempt_from_bearer_auth():
    from prometheus_gateway.auth.middleware import _is_exempt

    assert _is_exempt("/oauth2/token") is True


def test_token_path_is_exempt_from_rate_limiting():
    """The limiter keys on claims; this is the request that produces them."""
    from prometheus_gateway.rate_limit_middleware import RateLimitMiddleware

    assert "/oauth2/token" in RateLimitMiddleware._EXEMPT_PATHS


# ── Happy path ─────────────────────────────────────────────────────────────


async def test_token_issued_without_any_bearer_token(gw):
    """End-to-end proof of the exemption — not just _is_exempt() in isolation."""
    with respx.mock:
        respx.post(AUTH_TOKEN_URL).mock(
            return_value=Response(
                200,
                json={"access_token": "sdk-jwt", "token_type": "Bearer", "expires_in": 3600},
            )
        )
        resp = await gw.post(
            "/oauth2/token",
            data={
                "grant_type": "client_credentials",
                "client_id": "sdk",
                "client_secret": "s3cr3t",
            },
        )
    assert resp.status_code == 200
    assert resp.json() == {
        "access_token": "sdk-jwt",
        "token_type": "Bearer",
        "expires_in": 3600,
    }


async def test_requested_scope_reaches_the_auth_service_unchanged(gw):
    """The dashboard's login forces admin scopes. An SDK's request must not be
    rewritten — it asks for the scopes it needs and gets those or an error.
    """
    with respx.mock:
        route = respx.post(AUTH_TOKEN_URL).mock(
            return_value=Response(200, json={"access_token": "t", "expires_in": 3600})
        )
        await gw.post(
            "/oauth2/token",
            data={
                "grant_type": "client_credentials",
                "client_id": "sdk",
                "client_secret": "s3cr3t",
                "scope": "inference:read model:qwen3-0.6b",
            },
        )
    sent = dict(httpx.QueryParams(route.calls[0].request.content.decode()))
    assert sent == {
        "grant_type": "client_credentials",
        "client_id": "sdk",
        "client_secret": "s3cr3t",
        "scope": "inference:read model:qwen3-0.6b",
    }
    assert (
        route.calls[0]
        .request.headers["content-type"]
        .startswith("application/x-www-form-urlencoded")
    )


# ── Errors stay OAuth2 errors ──────────────────────────────────────────────


async def test_invalid_client_keeps_the_oauth2_error_body(gw):
    """RFC 6749 §5.2 — an OAuth2 client parses {"error": ...}. Translating that
    into problem+json would make this a worse token endpoint than the direct one.
    """
    with respx.mock:
        respx.post(AUTH_TOKEN_URL).mock(
            return_value=Response(
                401,
                json={"error": "invalid_client", "error_description": "Invalid client."},
            )
        )
        resp = await gw.post(
            "/oauth2/token",
            data={"grant_type": "client_credentials", "client_id": "x", "client_secret": "bad"},
        )
    assert resp.status_code == 401
    assert resp.json() == {"error": "invalid_client", "error_description": "Invalid client."}
    assert "type" not in resp.json()  # i.e. not an RFC 9457 problem document


async def test_unsupported_grant_type_passes_through_as_400(gw):
    with respx.mock:
        respx.post(AUTH_TOKEN_URL).mock(
            return_value=Response(400, json={"error": "unsupported_grant_type"})
        )
        resp = await gw.post("/oauth2/token", data={"grant_type": "implicit"})
    assert resp.status_code == 400
    assert resp.json()["error"] == "unsupported_grant_type"


async def test_unreachable_auth_service_is_503(gw):
    with respx.mock:
        respx.post(AUTH_TOKEN_URL).mock(side_effect=httpx.ConnectError("refused"))
        resp = await gw.post("/oauth2/token", data={"grant_type": "client_credentials"})
    assert resp.status_code == 503
    assert resp.json()["type"].endswith("/upstream-unavailable")


async def test_missing_configuration_is_503(rsa_keys, tmp_path):
    """A gateway with no AUTH_SERVICE_TOKEN_URL can't issue tokens — say so,
    rather than 404ing on a path the guide tells SDKs to call.
    """
    from prometheus_gateway.config import Settings

    key_file = tmp_path / "public.pem"
    key_file.write_text(rsa_keys["public"])
    app = _build(
        Settings(
            jwt_issuer="https://auth.test",
            jwt_audience="prometheus-gateway",
            jwt_public_key_file=str(key_file),
            jwt_revocation_redis_url=None,
            rate_limit_strict=False,
            admin_dashboard_enabled=False,
            # both explicit — otherwise a .env in the working tree supplies a
            # token URL and this test silently stops testing the missing case
            # (ui_enabled has a validator that *requires* the token URL)
            ui_enabled=False,
            auth_service_token_url=None,
        )
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.post("/oauth2/token", data={"grant_type": "client_credentials"})
    assert resp.status_code == 503
    assert resp.json()["type"].endswith("/not-configured")
