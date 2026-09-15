"""Tests for GET /share/{token} — the gateway fronting auth-service's one-time
credential page, and the link that points at it.

See docs/roadmap.md PRM-102. Opening a share link was the last auth-service
surface a person outside the platform had to reach, and the reason the service
still needed a published port.
"""

from __future__ import annotations

import httpx
import pytest
import respx
from httpx import ASGITransport, AsyncClient, Response

AUTH_TOKEN_URL = "https://auth.test/oauth2/token"
AUTH_SHARE_URL = "https://auth.test/share"
AUTH_ADMIN_URL = "https://auth.test/admin"

_PAGE = "<html><body>pmt_live_s3cr3t</body></html>"
_SECURITY_HEADERS = {
    "Content-Type": "text/html; charset=utf-8",
    "Cache-Control": "no-store, private",
    "X-Robots-Tag": "noindex",
    "Referrer-Policy": "no-referrer",
}


@pytest.fixture
def share_settings(rsa_keys, tmp_path):
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
        manager_client_id="gw-service",
        manager_client_secret="secret",
        auth_service_token_url=AUTH_TOKEN_URL,
        auth_service_share_url=AUTH_SHARE_URL,
        auth_service_admin_url=AUTH_ADMIN_URL,
        auth_service_admin_api_key="test-admin-secret",
    )


@pytest.fixture
async def gw(share_settings):
    from prometheus_gateway.main import create_app
    from prometheus_gateway.models.registry import ModelRegistry

    registry = ModelRegistry.__new__(ModelRegistry)
    registry._models = {}
    app = create_app(settings=share_settings, registry=registry)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


# ── The exemption ──────────────────────────────────────────────────────────


def test_share_path_needs_no_bearer_token():
    """The link *is* the credential — whoever opens it has no token yet."""
    from prometheus_gateway.auth.middleware import _is_exempt

    assert _is_exempt("/share/abc123") is True
    # The prefix must not swallow anything else that starts with the word.
    assert _is_exempt("/shared-secrets") is False


# ── Serving the page ───────────────────────────────────────────────────────


async def test_page_and_its_security_headers_pass_through(gw):
    """The response is a secret rendered in a browser. no-store/noindex/
    no-referrer are the point of it, not decoration."""
    with respx.mock:
        respx.get(f"{AUTH_SHARE_URL}/tok-1").mock(
            return_value=Response(200, text=_PAGE, headers=_SECURITY_HEADERS)
        )
        resp = await gw.get("/share/tok-1")

    assert resp.status_code == 200
    assert "pmt_live_s3cr3t" in resp.text
    assert resp.headers["cache-control"] == "no-store, private"
    assert resp.headers["x-robots-tag"] == "noindex"
    assert resp.headers["referrer-policy"] == "no-referrer"
    assert resp.headers["content-type"].startswith("text/html")


@pytest.mark.parametrize("status", [404, 410])
async def test_gone_and_missing_keep_their_status(gw, status):
    """A used, revoked or expired link renders its own page with 410; an unknown
    one 404s. Both are auth-service's answer, not ours to reinterpret."""
    with respx.mock:
        respx.get(f"{AUTH_SHARE_URL}/tok-2").mock(
            return_value=Response(status, text="<html>gone</html>", headers=_SECURITY_HEADERS)
        )
        resp = await gw.get("/share/tok-2")
    assert resp.status_code == status
    assert "gone" in resp.text


async def test_the_real_visitor_is_forwarded_for_the_audit_trail(gw):
    """auth-service stamps used_by_ip on the row — the record of who read a
    secret. Proxying would make that the gateway on every read."""
    with respx.mock:
        route = respx.get(f"{AUTH_SHARE_URL}/tok-3").mock(
            return_value=Response(200, text=_PAGE, headers=_SECURITY_HEADERS)
        )
        await gw.get("/share/tok-3", headers={"User-Agent": "Mozilla/5.0 (probe)"})

    sent = route.calls[0].request.headers
    # ASGITransport reports the client as 127.0.0.1; the point is that the
    # header carries the *caller*, and that it is present at all.
    assert sent["x-forwarded-for"] == "127.0.0.1"
    assert sent["user-agent"] == "Mozilla/5.0 (probe)"


async def test_a_visitor_cannot_forge_whose_read_it_was(gw):
    """X-Forwarded-For is overwritten, never appended to — otherwise the visitor
    chooses what the audit log says."""
    with respx.mock:
        route = respx.get(f"{AUTH_SHARE_URL}/tok-4").mock(
            return_value=Response(200, text=_PAGE, headers=_SECURITY_HEADERS)
        )
        await gw.get("/share/tok-4", headers={"X-Forwarded-For": "1.2.3.4"})

    forwarded = route.calls[0].request.headers["x-forwarded-for"]
    assert forwarded == "127.0.0.1"
    assert "1.2.3.4" not in forwarded


async def test_unreachable_auth_service_is_503(gw):
    with respx.mock:
        respx.get(f"{AUTH_SHARE_URL}/tok-5").mock(side_effect=httpx.ConnectError("refused"))
        resp = await gw.get("/share/tok-5")
    assert resp.status_code == 503
    assert resp.json()["type"].endswith("/upstream-unavailable")


# ── The link the operator is handed ────────────────────────────────────────


async def test_share_link_points_at_the_gateway_not_auth_service(gw, rsa_keys):
    """auth-service builds the link from the base URL of the request that asked
    for it — which arrives from the gateway, so the operator used to be shown an
    internal hostname the recipient could not resolve."""
    from tests.conftest import make_token

    token = make_token(rsa_keys["private"], scope="admin:read admin:write")
    with respx.mock:
        respx.post(f"{AUTH_ADMIN_URL}/clients/c-1/share").mock(
            return_value=Response(
                200,
                json={
                    "share_url": "https://auth-service:9000/share/raw-token-abc",
                    "expires_at": "2026-09-15T00:00:00Z",
                },
            )
        )
        resp = await gw.post(
            "/admin/api/users/c-1/share",
            headers={"Authorization": f"Bearer {token}"},
            json={"secret": "s3cr3t"},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["share_url"] == "http://test/share/raw-token-abc"
    assert body["expires_at"] == "2026-09-15T00:00:00Z"


async def test_an_error_from_the_share_request_is_left_alone(gw, rsa_keys):
    from tests.conftest import make_token

    token = make_token(rsa_keys["private"], scope="admin:read admin:write")
    with respx.mock:
        respx.post(f"{AUTH_ADMIN_URL}/clients/c-2/share").mock(
            return_value=Response(404, json={"detail": "no such client"})
        )
        resp = await gw.post(
            "/admin/api/users/c-2/share",
            headers={"Authorization": f"Bearer {token}"},
            json={"secret": "s3cr3t"},
        )
    assert resp.status_code == 404
