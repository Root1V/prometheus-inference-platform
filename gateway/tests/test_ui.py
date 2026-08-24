"""Tests for spec 013 — Web Chat UI — Browser Authentication & Reverse Proxy.

Each test corresponds 1-to-1 with an Acceptance Criterion in memory/specs/013-web-chat-ui-proxy.md.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import respx
from httpx import ASGITransport, AsyncClient, Response

from prometheus_gateway.config import Settings
from prometheus_gateway.main import create_app
from prometheus_gateway.models.registry import ModelEntry, ModelRegistry
from prometheus_gateway.ui.router import _reset_login_limiter_for_testing

from tests.conftest import make_token


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

TOKEN_URL = "http://auth-service.test/oauth2/token"
BACKEND_URL = "http://127.0.0.1:18080"  # allowed loopback host


@pytest.fixture
def ui_registry():
    """Registry with two discoverable and one non-discoverable model."""
    reg = ModelRegistry.__new__(ModelRegistry)
    reg._models = {
        "llama3-8b": ModelEntry(
            id="llama3-8b",
            path="/models/llama3.gguf",
            context_length=8192,
            family="llama3",
            quantization="Q4_0",
            backend_url=BACKEND_URL,
            backend_status="active",
            discovery=True,
        ),
        "hidden-model": ModelEntry(
            id="hidden-model",
            path="/models/hidden.gguf",
            context_length=4096,
            family="llama3",
            quantization="Q4_0",
            backend_url=BACKEND_URL,
            backend_status="active",
            discovery=False,  # AC-17, AC-18
        ),
        "no-backend-model": ModelEntry(
            id="no-backend-model",
            path="/models/nobackend.gguf",
            context_length=4096,
            family="llama3",
            quantization="Q4_0",
            backend_url=None,
            backend_status="inactive",
            discovery=True,
        ),
    }
    return reg


@pytest.fixture
def ui_settings(rsa_keys, tmp_path):
    """Settings with UI enabled and a real RSA key file."""
    key_file = tmp_path / "public.pem"
    key_file.write_text(rsa_keys["public"])
    return Settings(
        jwt_issuer="https://auth.test",
        jwt_audience="prometheus-gateway",
        jwt_public_key_file=str(key_file),
        jwt_clock_skew_seconds=30,
        jwt_revocation_redis_url=None,
        rate_limit_strict=False,
        ui_enabled=True,
        auth_service_token_url=TOKEN_URL,
    )


@pytest.fixture
def disabled_settings(rsa_keys, tmp_path):
    """Settings with UI disabled (default)."""
    key_file = tmp_path / "public.pem"
    key_file.write_text(rsa_keys["public"])
    return Settings(
        jwt_issuer="https://auth.test",
        jwt_audience="prometheus-gateway",
        jwt_public_key_file=str(key_file),
        jwt_clock_skew_seconds=30,
        jwt_revocation_redis_url=None,
        rate_limit_strict=False,
        ui_enabled=False,
    )


@pytest.fixture
def ui_app(ui_settings, ui_registry):
    return create_app(settings=ui_settings, registry=ui_registry)


@pytest.fixture
def disabled_app(disabled_settings, ui_registry):
    return create_app(settings=disabled_settings, registry=ui_registry)


@pytest.fixture
async def ui_client(ui_app):
    async with AsyncClient(
        transport=ASGITransport(app=ui_app),
        base_url="https://test",
        follow_redirects=False,
    ) as client:
        yield client


@pytest.fixture
async def disabled_client(disabled_app):
    async with AsyncClient(
        transport=ASGITransport(app=disabled_app),
        base_url="https://test",
        follow_redirects=False,
    ) as client:
        yield client


def _make_ui_token(rsa_keys, scope="ui:chat", exp_delta=3600):
    """Mint a JWT with ui:chat scope."""
    return make_token(rsa_keys["private"], scope=scope, exp_delta=exp_delta)


@pytest.fixture(autouse=True)
def reset_rate_limiter():
    """Clear in-memory rate limiter before each test."""
    _reset_login_limiter_for_testing()
    yield
    _reset_login_limiter_for_testing()


# ─────────────────────────────────────────────────────────────────────────────
# AC-1: UI_ENABLED=false → all /ui/* return 404
# ─────────────────────────────────────────────────────────────────────────────


class TestAC1DisabledUI:
    async def test_login_returns_404_when_disabled(self, disabled_client):
        resp = await disabled_client.get("/ui/login")
        assert resp.status_code == 404

    async def test_logout_returns_404_when_disabled(self, disabled_client):
        resp = await disabled_client.post("/ui/logout")
        assert resp.status_code == 404

    async def test_proxy_returns_404_when_disabled(self, disabled_client):
        resp = await disabled_client.get("/ui/llama3-8b/index.html")
        assert resp.status_code == 404


# ─────────────────────────────────────────────────────────────────────────────
# AC-2: No session cookie → 302 to /ui/login?next=<path>
# ─────────────────────────────────────────────────────────────────────────────


class TestAC2RedirectWithoutCookie:
    async def test_redirects_to_login_when_no_cookie(self, ui_client):
        resp = await ui_client.get("/ui/llama3-8b/")
        assert resp.status_code == 302
        location = resp.headers["location"]
        assert location.startswith("/ui/login")
        assert "next=" in location


# ─────────────────────────────────────────────────────────────────────────────
# AC-3: GET /ui/login returns HTML form with required fields
# ─────────────────────────────────────────────────────────────────────────────


class TestAC3LoginForm:
    async def test_login_form_html(self, ui_client):
        resp = await ui_client.get("/ui/login")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]
        body = resp.text
        assert 'name="client_id"' in body
        assert 'name="client_secret"' in body
        assert 'name="model_id"' in body
        assert 'name="next"' in body
        # Only discoverable models appear — backend_url is not required
        assert "llama3-8b" in body
        assert "hidden-model" not in body  # discovery=False
        assert "no-backend-model" in body  # discovery=True, no backend_url → still shown


# ─────────────────────────────────────────────────────────────────────────────
# AC-4: Valid login sets HttpOnly Secure SameSite=Lax cookie and redirects
# ─────────────────────────────────────────────────────────────────────────────


class TestAC4ValidLogin:
    @respx.mock
    async def test_valid_login_sets_cookie_and_redirects(self, ui_client, rsa_keys):
        token = _make_ui_token(rsa_keys)
        respx.post(TOKEN_URL).mock(
            return_value=Response(200, json={"access_token": token, "token_type": "bearer"})
        )

        resp = await ui_client.post(
            "/ui/login",
            data={
                "client_id": "my-client",
                "client_secret": "my-secret",
                "model_id": "llama3-8b",
                "next": "/ui/",
            },
        )

        assert resp.status_code == 302
        assert resp.headers["location"] == "/ui/llama3-8b/"

        set_cookie = resp.headers.get("set-cookie", "")
        assert "prometheus_session=" in set_cookie
        assert "HttpOnly" in set_cookie
        assert "Secure" in set_cookie
        assert "samesite=lax" in set_cookie.lower()


# ─────────────────────────────────────────────────────────────────────────────
# AC-5: Invalid credentials → 200 with error message (no stack trace)
# ─────────────────────────────────────────────────────────────────────────────


class TestAC5InvalidCredentials:
    @respx.mock
    async def test_invalid_credentials_renders_form_with_error(self, ui_client):
        respx.post(TOKEN_URL).mock(return_value=Response(401, json={"error": "invalid_client"}))

        resp = await ui_client.post(
            "/ui/login",
            data={
                "client_id": "bad",
                "client_secret": "bad",
                "model_id": "llama3-8b",
            },
        )

        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]
        body = resp.text
        assert "Invalid credentials" in body
        # No internal details leaked
        assert TOKEN_URL not in body
        assert "Traceback" not in body


# ─────────────────────────────────────────────────────────────────────────────
# AC-6: Token missing ui:chat scope → form with error
# ─────────────────────────────────────────────────────────────────────────────


class TestAC6MissingScope:
    @respx.mock
    async def test_token_without_ui_scope_shows_error(self, ui_client, rsa_keys):
        token = _make_ui_token(rsa_keys, scope="inference:read")  # no ui:chat
        respx.post(TOKEN_URL).mock(
            return_value=Response(200, json={"access_token": token, "token_type": "bearer"})
        )

        resp = await ui_client.post(
            "/ui/login",
            data={
                "client_id": "svc",
                "client_secret": "s",
                "model_id": "llama3-8b",
            },
        )

        assert resp.status_code == 200
        body = resp.text
        assert "ui:chat" in body or "UI access" in body


# ─────────────────────────────────────────────────────────────────────────────
# AC-7: Valid session cookie proxies request to backend
# ─────────────────────────────────────────────────────────────────────────────


class TestAC7ProxyWithValidSession:
    @respx.mock
    async def test_proxies_to_backend_with_valid_session(self, ui_client, rsa_keys, ui_settings):
        token = _make_ui_token(rsa_keys)
        respx.get(f"{BACKEND_URL}/index.html").mock(
            return_value=Response(
                200, text="<html>chat</html>", headers={"content-type": "text/html"}
            )
        )

        resp = await ui_client.get(
            "/ui/llama3-8b/index.html",
            cookies={ui_settings.ui_session_cookie_name: token},
        )

        assert resp.status_code == 200
        assert "chat" in resp.text


# ─────────────────────────────────────────────────────────────────────────────
# AC-8: Expired JWT in session cookie → 302 to login (not 401 JSON)
# ─────────────────────────────────────────────────────────────────────────────


class TestAC8ExpiredCookie:
    async def test_expired_cookie_redirects_to_login(self, ui_client, rsa_keys, ui_settings):
        expired_token = _make_ui_token(rsa_keys, exp_delta=-60)  # already expired

        resp = await ui_client.get(
            "/ui/llama3-8b/",
            cookies={ui_settings.ui_session_cookie_name: expired_token},
        )

        assert resp.status_code == 302
        assert resp.headers["location"].startswith("/ui/login")
        assert "application/problem+json" not in resp.headers.get("content-type", "")


# ─────────────────────────────────────────────────────────────────────────────
# AC-9: POST /ui/logout clears cookie and redirects to /ui/login
# ─────────────────────────────────────────────────────────────────────────────


class TestAC9Logout:
    async def test_logout_clears_cookie_and_redirects(self, ui_client, rsa_keys, ui_settings):
        token = _make_ui_token(rsa_keys)
        resp = await ui_client.post(
            "/ui/logout",
            cookies={ui_settings.ui_session_cookie_name: token},
        )

        assert resp.status_code == 302
        assert resp.headers["location"] == "/ui/login"
        set_cookie = resp.headers.get("set-cookie", "")
        # Cookie cleared: Max-Age=0 or expires in the past
        assert "Max-Age=0" in set_cookie or "max-age=0" in set_cookie.lower()


# ─────────────────────────────────────────────────────────────────────────────
# AC-10: Open-redirect prevention — next=https://evil.com is sanitised
# ─────────────────────────────────────────────────────────────────────────────


class TestAC10OpenRedirectPrevention:
    @respx.mock
    async def test_evil_next_param_redirects_to_ui_root(self, ui_client, rsa_keys):
        token = _make_ui_token(rsa_keys)
        respx.post(TOKEN_URL).mock(
            return_value=Response(200, json={"access_token": token, "token_type": "bearer"})
        )

        resp = await ui_client.post(
            "/ui/login",
            data={
                "client_id": "c",
                "client_secret": "s",
                "model_id": "llama3-8b",
                "next": "https://evil.com/steal",
            },
        )

        assert resp.status_code == 302
        location = resp.headers["location"]
        assert "evil.com" not in location
        assert location.startswith("/ui/")

    @respx.mock
    async def test_relative_evil_path_is_sanitised(self, ui_client, rsa_keys):
        token = _make_ui_token(rsa_keys)
        respx.post(TOKEN_URL).mock(
            return_value=Response(200, json={"access_token": token, "token_type": "bearer"})
        )

        resp = await ui_client.post(
            "/ui/login",
            data={
                "client_id": "c",
                "client_secret": "s",
                "model_id": "llama3-8b",
                "next": "/other/path",  # valid relative but NOT under /ui/
            },
        )

        assert resp.status_code == 302
        location = resp.headers["location"]
        assert "/other/" not in location


# ─────────────────────────────────────────────────────────────────────────────
# AC-11: Login rate limiting per source IP
# ─────────────────────────────────────────────────────────────────────────────


class TestAC11RateLimiting:
    @respx.mock
    async def test_rate_limited_after_rpm_attempts(self, ui_app, ui_settings):
        respx.post(TOKEN_URL).mock(return_value=Response(401))

        # Use a fresh client to get a consistent IP
        async with AsyncClient(
            transport=ASGITransport(app=ui_app),
            base_url="https://test",
            follow_redirects=False,
        ) as client:
            # Exhaust the rate limit
            rpm = ui_settings.ui_login_rate_limit_rpm
            for _ in range(rpm):
                await client.post(
                    "/ui/login",
                    data={"client_id": "x", "client_secret": "y", "model_id": "llama3-8b"},
                )

            # Next attempt should be rate limited
            resp = await client.post(
                "/ui/login", data={"client_id": "x", "client_secret": "y", "model_id": "llama3-8b"}
            )

        assert resp.status_code == 429
        assert "Retry-After" in resp.headers


# ─────────────────────────────────────────────────────────────────────────────
# AC-12: Sensitive headers NOT forwarded to backend
# ─────────────────────────────────────────────────────────────────────────────


class TestAC12HeaderStripping:
    @respx.mock
    async def test_cookie_and_auth_headers_stripped(self, ui_client, rsa_keys, ui_settings):
        token = _make_ui_token(rsa_keys)
        captured_headers: dict = {}

        def capture(request):
            captured_headers.update(dict(request.headers))
            return Response(200, text="ok")

        respx.get(f"{BACKEND_URL}/").mock(side_effect=capture)

        await ui_client.get(
            "/ui/llama3-8b/",
            cookies={ui_settings.ui_session_cookie_name: token},
            headers={"Authorization": "Bearer some-other-token"},
        )

        assert "cookie" not in captured_headers
        assert "authorization" not in captured_headers
        # host is set by httpx to the backend address — not the browser's original host
        assert captured_headers.get("host", "") == "127.0.0.1:18080"


# ─────────────────────────────────────────────────────────────────────────────
# AC-13: Auth-service unreachable → generic error, no internal URL exposed
# ─────────────────────────────────────────────────────────────────────────────


class TestAC13AuthServiceUnreachable:
    @respx.mock
    async def test_unreachable_auth_service_generic_error(self, ui_client):
        import httpx as _httpx

        respx.post(TOKEN_URL).mock(side_effect=_httpx.ConnectError("connection refused"))

        resp = await ui_client.post(
            "/ui/login",
            data={
                "client_id": "c",
                "client_secret": "s",
                "model_id": "llama3-8b",
            },
        )

        assert resp.status_code == 200
        body = resp.text
        assert TOKEN_URL not in body
        assert "temporarily unavailable" in body.lower() or "try again" in body.lower()
        assert "Traceback" not in body


# ─────────────────────────────────────────────────────────────────────────────
# AC-14: GATEWAY_TLS_CERT_FILE + KEY set → gateway starts (settings validate)
#        and Set-Cookie includes Secure attribute
# ─────────────────────────────────────────────────────────────────────────────


class TestAC14TLSCertConfig:
    @respx.mock
    async def test_secure_flag_always_set_on_cookie(self, ui_client, rsa_keys):
        token = _make_ui_token(rsa_keys)
        respx.post(TOKEN_URL).mock(
            return_value=Response(200, json={"access_token": token, "token_type": "bearer"})
        )

        resp = await ui_client.post(
            "/ui/login",
            data={
                "client_id": "c",
                "client_secret": "s",
                "model_id": "llama3-8b",
            },
        )

        assert resp.status_code == 302
        assert "Secure" in resp.headers.get("set-cookie", "")


# ─────────────────────────────────────────────────────────────────────────────
# AC-15: Only one TLS variable set → Settings raises ValueError at construction
# ─────────────────────────────────────────────────────────────────────────────


class TestAC15IncompleteTLSConfig:
    def test_only_cert_file_raises(self, rsa_keys, tmp_path):
        key_file = tmp_path / "public.pem"
        key_file.write_text(rsa_keys["public"])
        with pytest.raises(Exception, match="GATEWAY_TLS"):
            Settings(
                jwt_issuer="https://auth.test",
                jwt_public_key_file=str(key_file),
                ui_enabled=True,
                auth_service_token_url=TOKEN_URL,
                gateway_tls_cert_file="/some/cert.crt",
                gateway_tls_key_file=None,
            )

    def test_only_key_file_raises(self, rsa_keys, tmp_path):
        key_file = tmp_path / "public.pem"
        key_file.write_text(rsa_keys["public"])
        with pytest.raises(Exception, match="GATEWAY_TLS"):
            Settings(
                jwt_issuer="https://auth.test",
                jwt_public_key_file=str(key_file),
                ui_enabled=True,
                auth_service_token_url=TOKEN_URL,
                gateway_tls_cert_file=None,
                gateway_tls_key_file="/some/key.key",
            )


# ─────────────────────────────────────────────────────────────────────────────
# AC-16: UI_ENABLED=true with no TLS → startup warning logged
# ─────────────────────────────────────────────────────────────────────────────


class TestAC16TLSWarningAtStartup:
    def test_warning_logged_when_no_tls(self, rsa_keys, tmp_path, capsys):
        import prometheus_gateway.telemetry as tel

        tel._CONFIGURED = False  # reset for this test

        key_file = tmp_path / "public.pem"
        key_file.write_text(rsa_keys["public"])
        settings = Settings(
            _env_file=None,  # prevent gateway/.env from injecting TLS cert paths
            jwt_issuer="https://auth.test",
            jwt_public_key_file=str(key_file),
            rate_limit_strict=False,
            ui_enabled=True,
            auth_service_token_url=TOKEN_URL,
        )
        reg = ModelRegistry.__new__(ModelRegistry)
        reg._models = {}
        create_app(settings=settings, registry=reg)
        captured = capsys.readouterr()
        # structlog writes JSON to stdout — look for the tls warning
        assert (
            "tls" in captured.out.lower()
            or "https" in captured.out.lower()
            or "secure" in captured.out.lower()
        )


# ─────────────────────────────────────────────────────────────────────────────
# AC-17: model_id with discovery=False in login body → form with error
# ─────────────────────────────────────────────────────────────────────────────


class TestAC17HiddenModelLogin:
    async def test_discovery_false_model_not_allowed_in_login(self, ui_client):
        resp = await ui_client.post(
            "/ui/login",
            data={
                "client_id": "c",
                "client_secret": "s",
                "model_id": "hidden-model",
            },
        )

        assert resp.status_code == 200
        body = resp.text
        assert "not available" in body.lower() or "ui access" in body.lower()


# ─────────────────────────────────────────────────────────────────────────────
# AC-18: /ui/nonexistent-model/ → 404; /ui/hidden-model/ → 404
# ─────────────────────────────────────────────────────────────────────────────


class TestAC18UnknownOrHiddenModelProxy:
    async def test_unknown_model_returns_404(self, ui_client, rsa_keys, ui_settings):
        token = _make_ui_token(rsa_keys)
        resp = await ui_client.get(
            "/ui/nonexistent-model/",
            cookies={ui_settings.ui_session_cookie_name: token},
        )
        assert resp.status_code == 404

    async def test_discovery_false_model_returns_404_in_proxy(
        self, ui_client, rsa_keys, ui_settings
    ):
        token = _make_ui_token(rsa_keys)
        resp = await ui_client.get(
            "/ui/hidden-model/",
            cookies={ui_settings.ui_session_cookie_name: token},
        )
        assert resp.status_code == 404


# ─────────────────────────────────────────────────────────────────────────────
# AC-19: No discoverable models → empty combobox with notice
# ─────────────────────────────────────────────────────────────────────────────


class TestAC19NoDiscoverableModels:
    async def test_empty_combobox_with_notice(self, rsa_keys, tmp_path):
        key_file = tmp_path / "public.pem"
        key_file.write_text(rsa_keys["public"])
        settings = Settings(
            jwt_issuer="https://auth.test",
            jwt_public_key_file=str(key_file),
            rate_limit_strict=False,
            ui_enabled=True,
            auth_service_token_url=TOKEN_URL,
        )
        reg = ModelRegistry.__new__(ModelRegistry)
        reg._models = {
            "hidden": ModelEntry(
                id="hidden",
                path="/m.gguf",
                context_length=4096,
                family="llama3",
                quantization="Q4_0",
                backend_url=BACKEND_URL,
                backend_status="active",
                discovery=False,
            )
        }
        app = create_app(settings=settings, registry=reg)
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="https://test",
            follow_redirects=False,
        ) as client:
            resp = await client.get("/ui/login")

        assert resp.status_code == 200
        body = resp.text
        assert (
            "discovery" in body.lower()
            or "no models" in body.lower()
            or "discoverable" in body.lower()
        )
        # The model ID "hidden" must not appear as a <select> option
        assert '<option value="hidden">' not in body


# ─────────────────────────────────────────────────────────────────────────────
# AC-20: gen-dev-cert.sh generates cert with correct SANs
# ─────────────────────────────────────────────────────────────────────────────


class TestAC20GenDevCert:
    def test_gen_dev_cert_produces_files_with_correct_sans(self, tmp_path):
        script = Path(__file__).parents[1] / "certs" / "gen-dev-cert.sh"
        assert script.exists(), "gen-dev-cert.sh not found"

        # Run the script in a temp dir, overriding SCRIPT_DIR via symlink trick
        # We copy the script to tmp_path and run it there
        import shutil

        tmp_script = tmp_path / "gen-dev-cert.sh"
        shutil.copy(str(script), str(tmp_script))
        tmp_script.chmod(0o755)

        result = subprocess.run(
            ["bash", str(tmp_script)],
            capture_output=True,
            text=True,
            cwd=str(tmp_path),
        )
        assert result.returncode == 0, f"Script failed: {result.stderr}"

        cert_file = tmp_path / "dev.crt"
        key_file = tmp_path / "dev.key"
        assert cert_file.exists()
        assert key_file.exists()

        # Inspect the certificate SANs
        san_result = subprocess.run(
            ["openssl", "x509", "-text", "-noout", "-in", str(cert_file)],
            capture_output=True,
            text=True,
        )
        assert san_result.returncode == 0
        cert_text = san_result.stdout
        assert "DNS:localhost" in cert_text
        assert "IP Address:127.0.0.1" in cert_text or "IP:127.0.0.1" in cert_text


# ─────────────────────────────────────────────────────────────────────────────
# spec-014 tests — Login Page UX Redesign
# AC refs: memory/specs/014-login-page-ux-redesign.md
# ─────────────────────────────────────────────────────────────────────────────


class TestSpec014AC1TemplateRendered:
    """AC-1: Page is rendered from login.html template, not a Python f-string."""

    async def test_login_returns_html_from_template(self, ui_client):
        resp = await ui_client.get("/ui/login")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]
        # Template-specific landmark present (not in old f-string)
        body = resp.text
        assert "prometheus-theme" in body  # localStorage key from template JS
        assert "theme-toggle" in body  # dark mode button id

    async def test_router_has_no_render_login_page_function(self):
        import prometheus_gateway.ui.router as _router

        assert not hasattr(_router, "_render_login_page"), (
            "_render_login_page must not exist in router.py (AC-1)"
        )

    async def test_router_has_no_inline_html_style_blocks(self):
        import inspect
        import prometheus_gateway.ui.router as _router

        src = inspect.getsource(_router)
        assert "<style>" not in src, "router.py must not contain <style> blocks (AC-12)"
        assert "<!DOCTYPE" not in src, "router.py must not contain <!DOCTYPE> (AC-12)"


class TestSpec014AC2StaticCSS:
    """AC-2: GET /ui/static/login.css returns 200 with text/css."""

    async def test_static_css_served(self, ui_client):
        resp = await ui_client.get("/ui/static/login.css")
        assert resp.status_code == 200
        assert "text/css" in resp.headers["content-type"]

    async def test_static_css_not_empty(self, ui_client):
        resp = await ui_client.get("/ui/static/login.css")
        assert len(resp.text) > 100


class TestSpec014AC3LightModeColors:
    """AC-3: Light-mode page background is Grey-1 (#E2E6EA), card is Sand (#F7F8F8)."""

    async def test_css_contains_light_mode_bg_token(self, ui_client):
        resp = await ui_client.get("/ui/static/login.css")
        css = resp.text
        # :root block defines light-mode tokens
        assert "#E2E6EA" in css, "--color-bg light should be #E2E6EA (Grey-1)"
        assert "#F7F8F8" in css, "--color-surface light should be #F7F8F8 (Sand)"


class TestSpec014AC4DarkModeColors:
    """AC-4: Dark-mode bg is Grey-5 (#000519), card is Midnight (#060E46)."""

    async def test_css_contains_dark_mode_bg_token(self, ui_client):
        resp = await ui_client.get("/ui/static/login.css")
        css = resp.text
        assert "#000519" in css, "--color-bg dark should be #000519 (Grey-5)"
        assert "#060E46" in css, "--color-surface dark should be #060E46 (Midnight)"

    async def test_fouc_prevention_script_in_template(self, ui_client):
        resp = await ui_client.get("/ui/login")
        body = resp.text
        # The anti-FOUC inline script must appear before the stylesheet link
        fouc_idx = body.find("prometheus-theme")
        css_link_idx = body.find("login.css")
        assert fouc_idx != -1, "FOUC prevention script missing"
        assert css_link_idx != -1, "CSS <link> missing"
        assert fouc_idx < css_link_idx, "FOUC prevention script must appear before <link>"


class TestSpec014AC5AC6DarkModeToggle:
    """AC-5/AC-6: Dark mode toggle button present; JS sets/reads localStorage."""

    async def test_toggle_button_present(self, ui_client):
        resp = await ui_client.get("/ui/login")
        body = resp.text
        assert 'id="theme-toggle"' in body

    async def test_toggle_js_sets_localstorage(self, ui_client):
        resp = await ui_client.get("/ui/login")
        body = resp.text
        assert "localStorage.setItem" in body
        assert "prometheus-theme" in body

    async def test_toggle_js_reads_localstorage_on_load(self, ui_client):
        resp = await ui_client.get("/ui/login")
        body = resp.text
        assert "localStorage.getItem" in body


class TestSpec014AC7AC8ButtonColors:
    """AC-7/AC-8: Button uses Electric Blue (light) / Serene Blue (dark); text is Sand/Midnight."""

    async def test_button_colors_in_css(self, ui_client):
        resp = await ui_client.get("/ui/static/login.css")
        css = resp.text
        # Light: Electric Blue button bg
        assert "#001391" in css, "Electric Blue (#001391) must appear in CSS"
        # Dark: Serene Blue button bg
        assert "#85C8FF" in css, "Serene Blue (#85C8FF) must appear in CSS"
        # Button text: Sand (light) / Midnight (dark)
        assert "#F7F8F8" in css
        assert "#060E46" in css


class TestSpec014AC9ErrorColor:
    """AC-9: Error message uses Mandarin (#FFB56B)."""

    @respx.mock
    async def test_error_message_mandarin_color_in_css(self, ui_client):
        resp = await ui_client.get("/ui/static/login.css")
        css = resp.text
        assert "#FFB56B" in css, "Mandarin (#FFB56B) must be the error colour"

    @respx.mock
    async def test_error_rendered_in_template(self, ui_client):
        respx.post(TOKEN_URL).mock(return_value=Response(401))
        resp = await ui_client.post(
            "/ui/login",
            data={"client_id": "x", "client_secret": "y", "model_id": "llama3-8b"},
        )
        assert resp.status_code == 200
        body = resp.text
        assert "msg-error" in body or "Invalid credentials" in body


class TestSpec014AC10WarnColor:
    """AC-10: Warning (no models) uses Canary (#FFE761)."""

    async def test_warn_color_in_css(self, ui_client):
        resp = await ui_client.get("/ui/static/login.css")
        css = resp.text
        assert "#FFE761" in css, "Canary (#FFE761) must be the warning colour"


class TestSpec014AC11Escaping:
    """AC-11: All template values are Jinja2 auto-escaped (no | safe in template)."""

    async def test_template_has_no_safe_filter(self):
        from pathlib import Path

        template = Path(__file__).parents[1] / "src/prometheus_gateway/ui/templates/login.html"
        content = template.read_text()
        # Strip HTML comments before checking — comments may mention | safe
        import re

        without_comments = re.sub(r"<!--.*?-->", "", content, flags=re.DOTALL)
        assert "| safe" not in without_comments, "Template must not use | safe filter (AC-11)"

    @respx.mock
    async def test_xss_in_error_is_escaped(self, ui_client):
        """Injected HTML in model_id must be escaped, not rendered."""
        resp = await ui_client.post(
            "/ui/login",
            data={
                "client_id": "x",
                "client_secret": "y",
                "model_id": "<script>alert(1)</script>",
            },
        )
        assert resp.status_code == 200
        body = resp.text
        assert "<script>alert(1)</script>" not in body


class TestSpec014AC12NoInlineHTMLinRouter:
    """AC-12: router.py contains no inline HTML strings, no hex colour literals."""

    async def test_no_hex_colors_in_router(self):
        import re
        import inspect
        import prometheus_gateway.ui.router as _router

        src = inspect.getsource(_router)
        # Find any hex colour not in a comment
        hex_in_code = re.findall(r"(?<!#)#[0-9a-fA-F]{6}\b", src)
        assert hex_in_code == [], f"router.py contains hex colours: {hex_in_code}"


class TestSpec014AC13NoExternalDeps:
    """AC-13: Login HTML references no external CDNs, fonts, or icon libraries."""

    async def test_no_external_urls_in_template(self, ui_client):
        resp = await ui_client.get("/ui/login")
        body = resp.text
        for external in ("cdn.", "fonts.googleapis", "unpkg.com", "jsdelivr", "fontawesome"):
            assert external not in body, f"External dependency '{external}' found in login page"


class TestSpec014AC14CSSTokensOnly:
    """AC-14: All colours in CSS are custom properties defined in :root / html.dark."""

    async def test_css_uses_var_references_outside_token_blocks(self, ui_client):
        import re

        resp = await ui_client.get("/ui/static/login.css")
        css = resp.text

        # Strip the :root { } and html.dark { } token definition blocks
        # so we only check the rule bodies for bare hex literals
        without_tokens = re.sub(r"(?::root|html\.dark)\s*\{[^}]+\}", "", css, flags=re.DOTALL)
        bare_hex = re.findall(r":\s*#[0-9a-fA-F]{3,6}\b", without_tokens)
        assert bare_hex == [], (
            f"CSS contains bare hex literals outside token definitions: {bare_hex}"
        )


class TestSpec014AC16PaletteRestriction:
    """AC-16: Only the 14 approved hex values appear anywhere in login.css."""

    APPROVED = {
        "#001391",
        "#85C8FF",
        "#060E46",
        "#F7F8F8",
        "#E2E6EA",
        "#CAD1D8",
        "#ADB8C2",
        "#46536D",
        "#000519",
        "#88E783",
        "#FFB56B",
        "#FFE761",
        "#8BE1E9",
        "#9694FF",
    }

    async def test_only_approved_colors_in_css(self, ui_client):
        import re

        resp = await ui_client.get("/ui/static/login.css")
        css = resp.text.upper()
        found = set(re.findall(r"#[0-9A-F]{6}\b", css))
        unapproved = found - {c.upper() for c in self.APPROVED}
        assert unapproved == set(), f"Unapproved hex colours found in login.css: {unapproved}"


# ─────────────────────────────────────────────────────────────────────────────
# AC-32 (spec-018): _validate_session structured log failure paths
# ─────────────────────────────────────────────────────────────────────────────


class TestAC32ValidateSessionLogPaths:
    """AC-32: _validate_session redirects to login on all failure paths.

    These tests exercise previously uncovered branches in ui/router.py:
    - Lines 134-135: except JWTError → continue (wrong-key signature)
    - Lines 138-139: invalid_signature warning after all keys exhausted
    - Lines 154-156: outer except Exception handler (key file not found)
    """

    async def test_wrong_key_token_redirects_to_login(
        self, ui_registry, alt_rsa_keys, rsa_keys, tmp_path
    ):
        """Token signed by a different RSA key → JWTError caught, invalid_signature logged.

        Covers ui/router.py lines 134-135 (except JWTError: continue) and
        138-139 (logger.warning('ui.validate_session.invalid_signature')).
        """
        key_file = tmp_path / "public.pem"
        key_file.write_text(rsa_keys["public"])
        s = Settings(
            jwt_issuer="https://auth.test",
            jwt_audience="prometheus-gateway",
            jwt_public_key_file=str(key_file),
            jwt_clock_skew_seconds=30,
            jwt_revocation_redis_url=None,
            rate_limit_strict=False,
            ui_enabled=True,
            auth_service_token_url=TOKEN_URL,
        )
        app = create_app(settings=s, registry=ui_registry)
        # Token signed with the *wrong* key — signature verification will raise JWTError
        wrong_token = _make_ui_token(alt_rsa_keys, scope="ui:chat")

        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="https://test",
            follow_redirects=False,
        ) as client:
            resp = await client.get(
                "/ui/llama3-8b/",
                cookies={s.ui_session_cookie_name: wrong_token},
            )

        assert resp.status_code == 302
        assert resp.headers["location"].startswith("/ui/login")

    async def test_key_file_removed_after_startup_redirects_to_login(
        self, ui_registry, rsa_keys, tmp_path
    ):
        """Key file removed after first request → FileNotFoundError in _validate_session.

        Starlette builds the middleware stack lazily (on first request).
        JWTAuthMiddleware.__init__ reads the file once into memory.
        _validate_session in ui/router.py opens the file again for every request.
        Deleting the file after the middleware is built exercises the outer
        except Exception: handler.

        Covers ui/router.py lines 154-156 (except Exception: outer handler).
        """
        key_file = tmp_path / "public.pem"
        key_file.write_text(rsa_keys["public"])
        s = Settings(
            jwt_issuer="https://auth.test",
            jwt_audience="prometheus-gateway",
            jwt_public_key_file=str(key_file),
            jwt_clock_skew_seconds=30,
            jwt_revocation_redis_url=None,
            rate_limit_strict=False,
            ui_enabled=True,
            auth_service_token_url=TOKEN_URL,
        )
        app = create_app(settings=s, registry=ui_registry)

        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="https://test",
            follow_redirects=False,
        ) as client:
            # Warm-up: the first request triggers Starlette's lazy middleware build
            # (JWTAuthMiddleware.__init__ reads and caches the public key)
            await client.get("/ui/login")

            # Now remove the file — _validate_session opens it again on every request
            key_file.unlink()

            dummy_token = _make_ui_token(rsa_keys, scope="ui:chat")
            resp = await client.get(
                "/ui/llama3-8b/",
                cookies={s.ui_session_cookie_name: dummy_token},
            )

        assert resp.status_code == 302
        assert resp.headers["location"].startswith("/ui/login")
