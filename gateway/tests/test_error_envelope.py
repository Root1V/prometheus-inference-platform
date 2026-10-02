"""PRM-174: a route that does not exist answers in the gateway's own envelope.

Implements: docs/roadmap.md — PRM-174.

The defect was reported as "`/admin/<not-api>` leaks Starlette's body". Measuring
it to answer Axonium moved the boundary: `JWTAuthMiddleware` runs **before**
routing, so on a protected path an unmapped URL is a `401` in our envelope and
the router's 404 never happens. What leaked was the middleware's *complement* —
the unauthenticated surfaces, which are the only ones that could ever show it.

So the first test here is the class and not the instance, and it fails if someone
adds an exempt path and forgets this.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from starlette.exceptions import HTTPException as StarletteHTTPException

from prometheus_gateway.auth.middleware import _EXEMPT_PREFIXES, EXEMPT_PATHS
from prometheus_gateway.main import _ROUTING_PROBLEMS
from tests.conftest import dashboard_settings, make_token

PROBLEM = "application/problem+json"


@pytest.fixture
def client(multi_model_app):
    """The real `create_app`, not conftest's hand-built `build_test_app`.

    Worth naming: the minimal app has neither the handler nor the admin mount,
    so a probe against it reports every surface as broken and every fix as
    ineffective. An instrument that cannot see the defect is not a measurement.
    """
    return TestClient(multi_model_app, raise_server_exceptions=False)


@pytest.fixture
def admin_client(rsa_keys, tmp_path):
    """The dashboard-enabled app, which is the only one with PRM-172's catch-all.

    Built from `dashboard_settings` (PRM-168's one place for this list) rather than
    importing `test_admin`'s fixtures: taking an imported fixture as a parameter
    shadows the import, and ruff rejects that.
    """
    from prometheus_gateway.main import create_app
    from prometheus_gateway.models.registry import ModelRegistry

    key_file = tmp_path / "public.pem"
    key_file.write_text(rsa_keys["public"])
    registry = ModelRegistry.__new__(ModelRegistry)
    registry._models = {}
    app = create_app(settings=dashboard_settings(key_file), registry=registry)
    return TestClient(app, raise_server_exceptions=False)


def _assert_envelope(resp, status: int, slug: str) -> None:
    assert resp.status_code == status
    assert resp.headers["content-type"].startswith(PROBLEM)
    body = resp.json()
    assert body["type"] == f"https://prometheus.internal/errors/{slug}"
    assert body["status"] == status
    # The three members an SDK types, correlates and logs on. RM-65's 422 fix
    # exists because a caller got none of them.
    assert body["request_id"]
    assert body["trace_id"]
    assert body["instance"]


# ── the class ────────────────────────────────────────────────────────────────


def test_no_unauthenticated_surface_answers_outside_the_envelope(client):
    """Every path the auth middleware exempts, refused by routing, uses our envelope.

    The middleware was the envelope's floor by accident: it answers first on
    everything else, so these paths are the whole exposed surface. TRACE is used
    because no route declares it, which makes routing — not a handler — the thing
    that refuses.
    """
    paths = sorted(EXEMPT_PATHS) + [p if p.endswith("/") else p + "/" for p in _EXEMPT_PREFIXES]
    for path in paths:
        resp = client.request("TRACE", path)
        assert resp.headers["content-type"].startswith(PROBLEM), (
            f"{path} answered {resp.status_code} as "
            f"{resp.headers.get('content-type')}: {resp.text[:120]}"
        )


def test_a_protected_unmapped_path_is_still_a_401_not_a_404(client):
    """Not a regression — the reason the leak stayed invisible, pinned.

    Routing never runs on a protected path, so this handler is not what answers
    there. If that ever changes, the test above stops covering what it claims to.
    """
    resp = client.get("/v1/does-not-exist")
    assert resp.status_code == 401
    assert resp.json()["type"].endswith("/missing-credentials")


# ── the instances ────────────────────────────────────────────────────────────


def test_the_reported_path_answers_in_the_envelope(client):
    """`/admin/<not-api>` — what Axonium probed in A-34 and could not interpret."""
    _assert_envelope(client.get("/admin/clients"), 404, "unknown-route")


def test_the_reported_405_answers_in_the_envelope(client):
    """The other half of their probe. `POST` to the same path."""
    resp = client.post("/admin/clients")
    assert resp.headers["content-type"].startswith(PROBLEM)
    assert resp.json()["type"].endswith(("/unknown-route", "/method-not-allowed"))


def test_a_wrong_verb_on_health_answers_in_the_envelope(client):
    """The same hole outside `/admin` entirely, which is why the fix is app-wide."""
    _assert_envelope(client.put("/health"), 405, "method-not-allowed")


def test_a_405_still_says_which_verbs_are_allowed(client):
    """Starlette's `Allow` header must survive being re-enveloped."""
    assert "GET" in client.put("/health").headers.get("allow", "")


def test_the_detail_names_the_method_and_path(client):
    """A caller who mistyped a URL should be told which URL."""
    body = client.get("/admin/typo-here").json()
    assert "GET" in body["detail"]
    assert "/admin/typo-here" in body["detail"]


def test_an_unknown_admin_api_path_answers_in_the_envelope(admin_client, rsa_keys):
    """PRM-172's catch-all shipped without a test for its envelope. This is it.

    On `admin_client`, not `client`: `multi_model_app` has the dashboard **off**, so
    `/admin/api/...` has no catch-all and no mount, and this assertion passed there
    against the app-wide handler instead. It was green and it tested the wrong thing
    — found while measuring routes for PRM-175, and the third time in two days that
    the instrument, not the code, was the thing that was wrong.

    The `detail` is what tells them apart: the catch-all says "No admin endpoint".
    """
    token = make_token(rsa_keys["private"], scope="admin:write")
    resp = admin_client.get("/admin/api/nope", headers={"Authorization": f"Bearer {token}"})
    _assert_envelope(resp, 404, "unknown-route")
    assert resp.json()["detail"] == "No admin endpoint at GET /admin/api/nope."


# ── the slug ─────────────────────────────────────────────────────────────────


def test_unknown_route_is_not_the_same_type_as_a_missing_usage_row():
    """`not-found` is documented as one data condition; a bad URL is not that.

    The SDK guide defines `not-found` as "no usage row with that id belonging to
    this client". An SDK that cannot tell that from "this URL does not exist"
    retries the wrong one.
    """
    assert _ROUTING_PROBLEMS[404][0] == "unknown-route"
    assert _ROUTING_PROBLEMS[404][0] != "not-found"


def test_the_two_routes_that_do_not_exist_agree_on_their_type(admin_client, rsa_keys):
    """The catch-all and the app-wide handler cannot answer with two `type`s."""
    token = make_token(rsa_keys["private"], scope="admin:write")
    hdr = {"Authorization": f"Bearer {token}"}
    via_catch_all = admin_client.get("/admin/api/nope", headers=hdr)
    via_handler = admin_client.get("/admin/nope")
    assert via_catch_all.json()["detail"].startswith("No admin endpoint")
    assert via_catch_all.json()["type"] == via_handler.json()["type"]


def test_an_unexpected_status_still_produces_an_envelope(multi_model_app):
    """The fallback cannot be the thing that raises inside an error handler.

    Mounted under `/ui`, an exempt prefix, because the auth middleware would
    otherwise answer `401` before routing and the probe would test nothing.
    """

    @multi_model_app.get("/ui/_prm174_probe")
    async def _probe() -> None:
        raise StarletteHTTPException(status_code=418)

    resp = TestClient(multi_model_app, raise_server_exceptions=False).get("/ui/_prm174_probe")
    assert resp.status_code == 418
    assert resp.headers["content-type"].startswith(PROBLEM)
    assert resp.json()["type"].endswith("/http-error")
