"""The audit log — PRM-157.

The architecture review put this among the three gaps that decide whether the
platform can be sold: there was no record of who changed a price, started an
instance or revoked a client, and it was impossible even in principle while the
gateway spoke to auth-service as a blanket admin (PRM-134 fixed that half).

Two properties carry the weight, and they are what most of this file asserts.
**The row is the record and Argus gets a copy** — an observability pipeline is
lossy by design, so a trail that can drop events is not a trail. And **no request
body is ever stored**: login carries a password and a secret rotation returns a
secret, so a log that kept bodies would be the largest credential store here.
"""

from __future__ import annotations

import json

import pytest
import respx
from httpx import ASGITransport, AsyncClient, Response

from prometheus_gateway import audit, db
from tests.conftest import make_token

pytestmark = pytest.mark.asyncio

AUTH_TOKEN_URL = "https://auth.test/token"
AUTH_ADMIN_URL = "https://auth.test/admin"
FLEET_URL = "http://coordinator.test:8090"


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
        manager_client_id="gw-service",
        manager_client_secret="secret",
        auth_service_token_url=AUTH_TOKEN_URL,
        auth_service_tls_verify=True,
        auth_service_admin_url=AUTH_ADMIN_URL,
        auth_service_admin_api_key="test-admin-secret",
        manager_fleet_url=FLEET_URL,
    )


@pytest.fixture
async def gw(admin_settings):
    from prometheus_gateway.main import create_app
    from prometheus_gateway.models.registry import ModelRegistry

    registry = ModelRegistry.__new__(ModelRegistry)
    registry._models = {}
    app = create_app(settings=admin_settings, registry=registry)
    await db.create_tables(db.get_engine())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


def _headers(rsa_keys, scope: str = "admin:read admin:write") -> dict[str, str]:
    return {"Authorization": f"Bearer {make_token(rsa_keys['private'], scope=scope)}"}


def _token_mock() -> None:
    respx.post(AUTH_TOKEN_URL).mock(
        return_value=Response(200, json={"access_token": "t", "expires_in": 300})
    )


# ── What is audited, and what is not ─────────────────────────────────────────


async def test_only_mutating_admin_requests_are_audited():
    assert audit.should_audit("POST", "/admin/api/nodes") is True
    assert audit.should_audit("PATCH", "/admin/api/nodes/n1") is True
    assert audit.should_audit("DELETE", "/admin/api/nodes/n1") is True
    # A read changes nothing, and recording every dashboard poll would bury the
    # writes in noise — which is how audit logs stop being read.
    assert audit.should_audit("GET", "/admin/api/instances") is False
    # Inference is metered and billed, not audited: it is not an administrative
    # change, and one row per request would be a second usage table.
    assert audit.should_audit("POST", "/v1/chat/completions") is False
    # The login route records itself, from the handler that parsed the body.
    assert audit.should_audit("POST", "/admin/api/auth/login") is False


async def test_a_refusal_is_an_error_outcome_not_a_success():
    """A 403 is the thing an auditor is looking for. Calling it `ok` because the
    system behaved correctly would hide the attempt."""
    assert audit.outcome_for(200) == "ok"
    assert audit.outcome_for(204) == "ok"
    assert audit.outcome_for(403) == "error"
    assert audit.outcome_for(500) == "error"


async def test_the_outcome_words_come_from_argus_not_from_us():
    """Taken from their tuple rather than retyped, so a change on their side is an
    import error here instead of two systems disagreeing about what `ok` means."""
    from argus_semconv import attributes as argus

    assert audit.outcome_for(200) in argus.ARGUS_OUTCOME_VALUES
    assert audit.outcome_for(500) in argus.ARGUS_OUTCOME_VALUES


# ── PRM-162: the vocabulary is theirs, and the event hangs where the facts are ──


def _event_attrs(monkeypatch) -> dict:
    """Capture what `record` puts on the current span, without a real provider."""
    captured: dict = {}

    class _Span:
        def add_event(self, name, attributes=None):
            captured["name"] = name
            captured.update(attributes or {})

    monkeypatch.setattr(audit.trace, "get_current_span", lambda: _Span())
    return captured


async def test_the_event_goes_on_the_span_that_is_already_current(monkeypatch):
    """The defect this item found. The previous version called `start_span`, which
    makes a second span per admin action — the doubling its own comment said it
    avoided — and hung the event off that, where no HTTP attribute exists. A-32's
    "you already emit the route template" was true of the request and false of the
    event."""
    attrs = _event_attrs(monkeypatch)
    await audit.record(action="POST /admin/api/test", outcome="ok", status_code=200)
    assert attrs.get("name") == "audit.admin_action", "the event never reached the current span"


async def test_the_actor_attributes_are_the_standards_not_ours(monkeypatch):
    from argus_semconv import attributes as argus

    attrs = _event_attrs(monkeypatch)
    await audit.record(
        action="POST /admin/api/test",
        outcome="ok",
        status_code=200,
        actor_user_id="u-1",
        actor_email="a@b.c",
        actor_client_id="c-1",
    )
    assert attrs["user.id"] == "u-1"
    assert attrs["user.email"] == "a@b.c"
    assert attrs[argus.ARGUS_TENANT] == "c-1"
    assert attrs[argus.ARGUS_ACTOR_KIND] == "user"
    assert not [k for k in attrs if k.startswith("prometheus.audit.actor")], (
        "an actor attribute is still in our own namespace"
    )


async def test_a_machine_credential_is_the_subject_and_says_so(monkeypatch):
    """One `user.id` whoever acted, and `argus.actor.kind` is what makes it
    readable — A-32's own argument: `svc-7` does not mean the same thing both ways."""
    from argus_semconv import attributes as argus

    attrs = _event_attrs(monkeypatch)
    await audit.record(
        action="POST /admin/api/test", outcome="ok", status_code=200, actor_client_id="c-1"
    )
    assert attrs["user.id"] == "c-1"
    assert attrs[argus.ARGUS_ACTOR_KIND] == "service"


async def test_no_actor_at_all_is_unknown_and_that_is_a_value(monkeypatch):
    """`unknown` is legitimate rather than filler: in an audit record "not stated"
    is a fact, and it has to be distinguishable from "nobody set this"."""
    from argus_semconv import attributes as argus

    attrs = _event_attrs(monkeypatch)
    await audit.record(action="POST /admin/api/test", outcome="ok", status_code=200)
    assert attrs[argus.ARGUS_ACTOR_KIND] == "unknown"
    assert "user.id" not in attrs


async def test_the_actor_kind_values_come_from_their_tuple():
    from argus_semconv import attributes as argus

    assert (
        audit.KIND_USER,
        audit.KIND_SERVICE,
        audit.KIND_UNKNOWN,
    ) == argus.ARGUS_ACTOR_KIND_VALUES


async def test_the_event_leaves_to_the_span_what_the_span_carries(monkeypatch):
    """Measured on the server span: `http.request.method` + `http.route`,
    `http.response.status_code` and `client.address` are all there. Repeating them
    on an event that hangs off that span would be the same fact twice."""
    attrs = _event_attrs(monkeypatch)
    await audit.record(
        action="POST /admin/api/nodes/{node_id}/check",
        outcome="ok",
        status_code=200,
        source_ip="127.0.0.1",
    )
    assert "prometheus.audit.action" not in attrs
    assert "prometheus.audit.status_code" not in attrs
    assert "prometheus.audit.source_ip" not in attrs


async def test_but_the_log_line_states_them_itself(monkeypatch):
    """A log line reaches no span. Nothing joins it, so what it does not say is not
    written down anywhere its reader can get to.

    Captured with structlog's own helper rather than `caplog`: these lines are
    structlog's and never become stdlib records, so `caplog` sees an empty list and
    the assertion would pass or fail for reasons unrelated to the log line.
    """
    from structlog.testing import capture_logs

    _event_attrs(monkeypatch)
    with capture_logs() as logs:
        await audit.record(
            action="POST /admin/api/nodes/{node_id}/check",
            outcome="ok",
            status_code=403,
            source_ip="10.0.0.9",
        )
    line = next(e for e in logs if e.get("event") == "audit.admin_action")
    assert line["action"] == "POST /admin/api/nodes/{node_id}/check"
    assert line["status_code"] == 403
    assert line["source_ip"] == "10.0.0.9"


# ── PRM-164: the target is their pair, and the type is what protects a person ──


def _req(template: str, params: dict):
    class _R:
        def __init__(self):
            self.scope = {
                "route": type("x", (), {"path": template})(),
                "path_params": params,
            }
            self.url = type("u", (), {"path": template})()

    return _R()


async def test_the_type_describes_the_id_not_the_object():
    """A-34's semantics, and it settles the cases where the two differ. `PATCH
    /admin/api/nodes/{node}/models/config` changes a node's model configuration and
    the id it carries is a node — what was done is already in `http.route`."""
    assert audit.target_for(_req("/admin/api/nodes/{node}/models/config", {"node": "lab"})) == (
        "node",
        "lab",
    )


async def test_the_most_specific_parameter_is_the_target():
    assert audit.target_for(
        _req("/admin/api/nodes/{node}/models/{model_id}", {"node": "lab", "model_id": "m-1"})
    ) == ("model", "m-1")


async def test_a_verb_in_the_path_is_never_the_target():
    """`/instances/{model_id}/{action}` is start, stop or restart. The last
    parameter is the most specific and this one is not an object at all."""
    assert audit.target_for(
        _req(
            "/admin/api/nodes/{node}/instances/{model_id}/{action}",
            {"node": "lab", "model_id": "m-1", "action": "start"},
        )
    ) == ("instance", "m-1")


async def test_administering_a_principal_says_user_whatever_the_parameter_is_called():
    """The privacy-critical one. Their pipeline hashes `argus.target.id` where the
    type says `user`, so the type is what stands between a person's identifier and
    their store. Our parameter is called `client_id` and the resource is `users`;
    taking the resource is what makes the rule fire."""
    assert audit.target_for(_req("/admin/api/users/{client_id}", {"client_id": "c-1"})) == (
        "user",
        "c-1",
    )


async def test_the_same_identifier_gets_the_same_treatment_everywhere():
    """`client_id` appears under two resources. A pseudonym is worth what the least
    protected place that subject's identifier appears is worth, so emitting the hash
    in one row and the clear value in another would leave the clear one — which is
    the whole thing the rule prevents."""
    principal = audit.target_for(_req("/admin/api/users/{client_id}", {"client_id": "c-1"}))
    billing = audit.target_for(
        _req("/admin/api/billing/clients/{client_id}/settings", {"client_id": "c-1"})
    )
    assert principal == billing == ("user", "c-1")


async def test_a_route_with_no_parameters_has_no_target():
    assert audit.target_for(_req("/admin/api/nodes", {})) == (None, None)


async def test_a_hyphenated_resource_is_normalised():
    assert audit.target_for(_req("/admin/api/traffic-splits/{name}", {"name": "chat"})) == (
        "traffic_split",
        "chat",
    )


async def test_every_mutating_admin_route_yields_a_declared_type(settings):
    """The guard that makes the cardinality closed, which is what A-34 asked for: a
    set nothing enforces is not closed. A new admin route producing a new type fails
    here rather than putting an undeclared value in their store — and a declared type
    no route produces fails too, because a set that lists what does not happen stops
    describing anything."""
    import re

    from prometheus_gateway.main import create_app

    app = create_app(settings=settings)
    produced: set[str] = set()
    for route in app.routes:
        path = getattr(route, "path", "")
        methods = getattr(route, "methods", set()) or set()
        if not path.startswith("/admin/api/") or not (methods & {"POST", "PUT", "PATCH", "DELETE"}):
            continue
        names = re.findall(r"\{([^}]+)\}", path)
        if not names:
            continue
        target_type, _ = audit.target_for(_req(path, {n: "x" for n in names}))
        if target_type is not None:
            produced.add(target_type)

    assert produced == audit.TARGET_TYPES, (
        f"undeclared: {sorted(produced - audit.TARGET_TYPES)}; "
        f"declared but produced by no route: {sorted(audit.TARGET_TYPES - produced)}"
    )


async def test_the_log_line_carries_the_correlation_ids_itself(monkeypatch):
    """They were on the line by accident, as structlog contextvars that
    `TraceIDMiddleware` clears when the response is done — and `record_request` runs
    after that, so every admin action except the self-auditing login emitted
    `trace_id: "none"`. The row was always right; the copy Argus correlates by was
    not."""
    from structlog.testing import capture_logs

    _event_attrs(monkeypatch)
    with capture_logs() as logs:
        await audit.record(
            action="POST /admin/api/nodes/{node_id}/check",
            outcome="ok",
            status_code=200,
            trace_id="744c92ad7994e25e297206be944db1eb",
            request_id="4755342b-a42a-47f1-ab8c-5bba91b7ca23",
        )
    line = next(e for e in logs if e.get("event") == "audit.admin_action")
    assert line["trace_id"] == "744c92ad7994e25e297206be944db1eb"
    assert line["request_id"] == "4755342b-a42a-47f1-ab8c-5bba91b7ca23"


async def test_the_pair_reaches_the_event_and_the_json_does_not(monkeypatch):
    attrs = _event_attrs(monkeypatch)
    await audit.record(
        action="POST /admin/api/nodes/{node_id}/deactivate",
        outcome="ok",
        status_code=200,
        target='{"node_id": "8ed6"}',
        target_type="node",
        target_id="8ed6",
    )
    assert attrs["argus.target.type"] == "node"
    assert attrs["argus.target.id"] == "8ed6"
    assert "prometheus.audit.target" not in attrs


async def test_the_row_keeps_every_parameter(gw, rsa_keys):
    """The pair loses the outer parameter where a route carries two that both
    matter. The row is the record of truth and keeps the full set — not the same
    fact twice, a fuller one in the place that must not lose it."""
    assert "target" in db.AuditEvent.__table__.columns


# ── An administrative change lands a row, through one door ───────────────────


@respx.mock
async def test_a_mutating_admin_request_is_recorded(gw, rsa_keys):
    _token_mock()
    respx.post(f"{FLEET_URL}/v1/fleet/nodes/n1/deactivate").mock(
        return_value=Response(200, json={"id": "n1", "enabled": False})
    )

    resp = await gw.post("/admin/api/nodes/n1/deactivate", headers=_headers(rsa_keys))
    assert resp.status_code == 200

    events = await db.list_audit_events()
    assert len(events) == 1
    event = events[0]
    assert event.action == "POST /admin/api/nodes/{node_id}/deactivate", (
        "the route template, not the resolved path — otherwise every id is its own "
        "action and nothing can be counted"
    )
    assert json.loads(event.target) == {"node_id": "n1"}
    assert event.outcome == "ok"
    assert event.status_code == 200
    assert event.request_id


@respx.mock
async def test_a_denied_change_is_recorded_too(gw, rsa_keys):
    """The attempt is the point. A log of what succeeded cannot show someone
    trying to do what they are not allowed to."""
    resp = await gw.post(
        "/admin/api/nodes/n1/deactivate", headers=_headers(rsa_keys, scope="admin:read")
    )
    assert resp.status_code == 403

    events = await db.list_audit_events()
    assert len(events) == 1
    assert events[0].outcome == "error"
    assert events[0].status_code == 403


@respx.mock
async def test_a_read_leaves_no_row(gw, rsa_keys):
    _token_mock()
    respx.get(f"{FLEET_URL}/v1/fleet/nodes").mock(return_value=Response(200, json=[]))
    await gw.get("/admin/api/nodes", headers=_headers(rsa_keys))
    assert await db.list_audit_events() == []


@respx.mock
async def test_the_actor_is_recorded_from_the_token(gw, rsa_keys):
    _token_mock()
    respx.delete(f"{FLEET_URL}/v1/fleet/nodes/n9").mock(return_value=Response(204))
    token = make_token(rsa_keys["private"], scope="admin:write", sub="op-7", azp="client-42")

    await gw.delete("/admin/api/nodes/n9", headers={"Authorization": f"Bearer {token}"})

    event = (await db.list_audit_events())[0]
    assert event.actor_client_id == "client-42"
    assert event.actor_user_id == "op-7"


# ── No credential ever reaches the table ─────────────────────────────────────


@respx.mock
async def test_a_login_records_the_email_and_never_the_password(gw):
    """The one identifier read from a body, by the one handler that parsed it."""
    respx.post(AUTH_TOKEN_URL).mock(
        return_value=Response(200, json={"access_token": "t", "expires_in": 300})
    )
    await gw.post(
        "/admin/api/auth/login",
        json={"email": "op@example.com", "password": "correct horse battery staple"},
    )

    events = await db.list_audit_events()
    assert len(events) == 1, "a login must be audited exactly once, not twice"
    event = events[0]
    assert event.action == "POST /admin/api/auth/login"
    assert event.actor_email == "op@example.com"
    assert event.outcome == "ok"

    # The assertion that matters most in this file.
    row = " ".join(str(v) for v in vars(event).values() if v is not None)
    assert "correct horse battery staple" not in row, (
        "a password reached the audit table — the log would be the largest "
        "credential store in the platform"
    )


@respx.mock
async def test_a_failed_login_is_recorded(gw):
    """A run of failures against one address is the pattern an auditor looks for,
    and a log of successes cannot show it."""
    respx.post(AUTH_TOKEN_URL).mock(return_value=Response(401, json={"error": "invalid_grant"}))
    await gw.post("/admin/api/auth/login", json={"email": "op@example.com", "password": "wrong"})

    event = (await db.list_audit_events())[0]
    assert event.outcome == "error"
    assert event.status_code == 401
    assert event.actor_email == "op@example.com"
    row = " ".join(str(v) for v in vars(event).values() if v is not None)
    assert "wrong" not in row


@respx.mock
async def test_no_request_body_is_stored_for_an_ordinary_change(gw, rsa_keys):
    """Path parameters say what was touched; the payload is never needed and a
    secret rotation's response would otherwise be sitting in this table."""
    _token_mock()
    respx.patch(f"{FLEET_URL}/v1/fleet/nodes/n1").mock(
        return_value=Response(200, json={"id": "n1"})
    )
    await gw.patch(
        "/admin/api/nodes/n1",
        json={"tag": "SECRET-SENTINEL-VALUE"},
        headers=_headers(rsa_keys),
    )

    event = (await db.list_audit_events())[0]
    row = " ".join(str(v) for v in vars(event).values() if v is not None)
    assert "SECRET-SENTINEL-VALUE" not in row
    assert json.loads(event.target) == {"node_id": "n1"}


# ── The row is the record; a failure to emit does not lose it ────────────────


async def test_the_row_survives_a_broken_emission(monkeypatch):
    """Argus is a consumer. If the span or the log line fails, the record stands —
    which is the entire reason the write comes first."""

    def _explode(*args, **kwargs):
        raise RuntimeError("collector down")

    # PRM-162: the event goes on the span that is already current, so what is
    # broken here is getting at that span — not starting a new one, which is what
    # this patched before and is the second span the module should never have made.
    monkeypatch.setattr(audit.trace, "get_current_span", _explode)
    await audit.record(action="POST /admin/api/test", outcome="ok", status_code=200)

    events = await db.list_audit_events()
    assert any(e.action == "POST /admin/api/test" for e in events)


async def test_a_failed_row_does_not_fail_the_action(monkeypatch):
    """By the time this runs the change has happened. Turning a successful
    mutation into a 500 would make the log's own failure the more damaging event —
    it is logged loudly instead.

    The database is broken rather than `record_audit_event` replaced: the guard
    lives inside that function, and patching it out and then asserting the guard
    works is a test of nothing. The first version of this test did exactly that
    and passed for the wrong reason until it did not.
    """

    def _explode():
        raise RuntimeError("database gone")

    monkeypatch.setattr(db, "get_session_factory", _explode)
    # Must not raise.
    await audit.record(action="POST /admin/api/test", outcome="ok", status_code=200)


# ── It is readable, because a log nobody can read is not a control ────────────


@respx.mock
async def test_the_trail_is_readable_with_admin_read(gw, rsa_keys):
    _token_mock()
    respx.post(f"{FLEET_URL}/v1/fleet/nodes/n1/check").mock(
        return_value=Response(200, json={"id": "n1"})
    )
    await gw.post("/admin/api/nodes/n1/check", headers=_headers(rsa_keys))

    resp = await gw.get("/admin/api/audit", headers=_headers(rsa_keys, scope="admin:read"))
    assert resp.status_code == 200
    events = resp.json()["events"]
    assert events[0]["action"] == "POST /admin/api/nodes/{node_id}/check"
    assert events[0]["target"] == {"node_id": "n1"}


async def test_reading_the_trail_needs_read_not_write(gw, rsa_keys):
    """Requiring write to see who wrote would mean only the people who can alter
    the system are able to check it."""
    resp = await gw.get("/admin/api/audit", headers=_headers(rsa_keys, scope="admin:read"))
    assert resp.status_code == 200


async def test_reading_the_trail_without_a_scope_is_refused(gw, rsa_keys):
    resp = await gw.get("/admin/api/audit", headers=_headers(rsa_keys, scope="inference:read"))
    assert resp.status_code == 403
