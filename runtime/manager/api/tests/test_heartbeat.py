"""A node reporting that it is up — PRM-152.

Two halves, tested apart because they fail apart: `assert_may_heartbeat` is the
rule the coordinator enforces, and `NodeHeartbeat` is the node's side of it. The
rule is tested without a token on purpose — validating a JWT needs a key set, and
what has to be right here is who may report for whom.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import HTTPException
from prometheus_manager_core.config import FleetConfig
from prometheus_manager_core.fleet import FleetRegistry, Node

from prometheus_manager_api import heartbeat as hb_module
from prometheus_manager_api.auth import assert_may_heartbeat
from prometheus_manager_api.heartbeat import NodeHeartbeat

NODE = "cdf36458-ca33-4d4a-917b-91822774d853"
OTHER = "8ed68951-d6cb-44f8-9335-6ddba3dde1fc"


# ── The rule ─────────────────────────────────────────────────────────────────


def test_a_node_may_report_for_itself():
    assert_may_heartbeat({"scope": f"fleet:heartbeat node:{NODE}"}, NODE)


def test_a_node_may_not_report_for_another():
    """The reason there is one credential per node rather than one for the fleet."""
    with pytest.raises(HTTPException) as exc:
        assert_may_heartbeat({"scope": f"fleet:heartbeat node:{NODE}"}, OTHER)
    assert exc.value.status_code == 403
    detail = exc.value.detail["detail"]
    assert f"node:{OTHER}" in detail, "the refusal should name the grant it needs"
    assert f"node:{NODE}" in detail, "and the one it holds, so the fix is obvious"


def test_the_heartbeat_scope_is_required_too():
    """A `node:<id>` grant alone is identity without permission."""
    with pytest.raises(HTTPException) as exc:
        assert_may_heartbeat({"scope": f"node:{NODE}"}, NODE)
    assert exc.value.status_code == 403
    assert "fleet:heartbeat" in exc.value.detail["detail"]


def test_backend_registry_write_no_longer_carries_it():
    """The point of the item. This scope registers, cordons and deletes any node
    and starts and stops instances on every manager — it used to be what the
    heartbeat required, so the credential a node needed to say "I am alive" also
    handed it the fleet."""
    with pytest.raises(HTTPException) as exc:
        assert_may_heartbeat({"scope": "backend-registry:read backend-registry:write"}, NODE)
    assert exc.value.status_code == 403


def test_a_scope_claim_that_is_a_list_is_read_too():
    """auth-service issues a space-delimited string; the claim is permitted to be
    a list and `_require_scope` has always accepted both."""
    assert_may_heartbeat({"scope": ["fleet:heartbeat", f"node:{NODE}"]}, NODE)


def test_holding_no_node_grant_at_all_says_so():
    with pytest.raises(HTTPException) as exc:
        assert_may_heartbeat({"scope": "fleet:heartbeat"}, NODE)
    assert "no node grant at all" in exc.value.detail["detail"]


# ── The node's side ──────────────────────────────────────────────────────────


def _fleet(tmp_path: Path) -> tuple[FleetRegistry, str]:
    fleet = FleetRegistry(tmp_path / "fleet.db")
    node = fleet.add(Node(name="local", manager_url="http://127.0.0.1:8090", node_type="mac"))
    return fleet, node.id


def test_the_coordinator_needs_only_its_own_id(tmp_path, monkeypatch):
    """It owns fleet.db, so it stamps its own row rather than being issued a
    credential to authenticate to itself."""
    fleet, node_id = _fleet(tmp_path)
    monkeypatch.setenv("PMGR_FLEET_NODE_ID", node_id)
    assert NodeHeartbeat(FleetConfig(coordinator=True), fleet=fleet).missing() == []


def test_the_coordinator_stamps_its_own_row(tmp_path, monkeypatch):
    fleet, node_id = _fleet(tmp_path)
    monkeypatch.setenv("PMGR_FLEET_NODE_ID", node_id)
    assert fleet.get(node_id).last_seen_at is None

    assert await_sync(NodeHeartbeat(FleetConfig(coordinator=True), fleet=fleet).beat_once()) is True
    assert fleet.get(node_id).last_seen_at is not None


def test_a_coordinator_with_an_id_it_does_not_hold_reports_nothing(tmp_path, monkeypatch):
    """RM-98 again: a failure must not read as a success. A wrong
    `PMGR_FLEET_NODE_ID` stamps no row and says so by returning False."""
    fleet, _ = _fleet(tmp_path)
    monkeypatch.setenv("PMGR_FLEET_NODE_ID", OTHER)
    beat = NodeHeartbeat(FleetConfig(coordinator=True), fleet=fleet)
    assert await_sync(beat.beat_once()) is False


def test_a_plain_node_names_everything_it_is_missing(monkeypatch):
    for var in ("PMGR_FLEET_NODE_ID", "PMGR_FLEET_CLIENT_ID", "PMGR_FLEET_CLIENT_SECRET"):
        monkeypatch.delenv(var, raising=False)
    assert NodeHeartbeat(FleetConfig()).missing() == [
        "PMGR_FLEET_NODE_ID",
        "PMGR_FLEET_CLIENT_ID",
        "PMGR_FLEET_CLIENT_SECRET",
        "[fleet] coordinator_url",
        "[fleet] auth_token_url",
    ]


def _configured(monkeypatch) -> FleetConfig:
    monkeypatch.setenv("PMGR_FLEET_NODE_ID", NODE)
    monkeypatch.setenv("PMGR_FLEET_CLIENT_ID", "client-local")
    monkeypatch.setenv("PMGR_FLEET_CLIENT_SECRET", "s3cret")
    return FleetConfig(
        coordinator=False,
        coordinator_url="http://coordinator:8090",
        auth_token_url="http://auth:9000/oauth2/token",
    )


class _Resp:
    def __init__(self, status_code: int, body: dict | None = None, text: str = "") -> None:
        self.status_code = status_code
        self._body = body or {}
        self.text = text

    def json(self) -> dict:
        return self._body

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _Client:
    """Stands in for httpx.AsyncClient inside heartbeat.py only."""

    calls: list[tuple[str, dict, dict]] = []
    heartbeat_status = 200

    def __init__(self, **_kw) -> None:
        pass

    async def __aenter__(self) -> _Client:
        return self

    async def __aexit__(self, *_exc) -> None:
        return None

    async def post(self, url: str, data=None, headers=None) -> _Resp:
        _Client.calls.append((url, data or {}, headers or {}))
        if url.endswith("/oauth2/token"):
            return _Resp(200, {"access_token": "tok", "expires_in": 600})
        return _Resp(_Client.heartbeat_status, text="refused")


@pytest.fixture
def stub_http(monkeypatch):
    _Client.calls = []
    _Client.heartbeat_status = 200

    class _Httpx:
        AsyncClient = _Client

    monkeypatch.setattr(hb_module, "httpx", _Httpx)
    return _Client


def test_a_plain_node_mints_a_token_then_reports(monkeypatch, stub_http):
    cfg = _configured(monkeypatch)
    assert await_sync(NodeHeartbeat(cfg).beat_once()) is True

    token_call, beat_call = stub_http.calls
    # The token asks for exactly this node's grant and nothing more.
    assert token_call[1]["scope"] == f"fleet:heartbeat node:{NODE}"
    assert beat_call[0] == f"http://coordinator:8090/v1/fleet/nodes/{NODE}/heartbeat"
    assert beat_call[2]["Authorization"] == "Bearer tok"


def test_the_token_is_reused_across_beats(monkeypatch, stub_http):
    cfg = _configured(monkeypatch)
    node = NodeHeartbeat(cfg)
    await_sync(node.beat_once())
    await_sync(node.beat_once())

    minted = [c for c in stub_http.calls if c[0].endswith("/oauth2/token")]
    assert len(minted) == 1, "a token good for ten minutes was re-minted every beat"


def test_a_refusal_is_reported_as_one_and_never_raises(monkeypatch, stub_http):
    """A node that cannot report is still serving inference, so this returns
    False rather than taking anything down."""
    cfg = _configured(monkeypatch)
    stub_http.heartbeat_status = 404
    assert await_sync(NodeHeartbeat(cfg).beat_once()) is False


def test_a_rejected_token_is_dropped_rather_than_retried(monkeypatch, stub_http):
    """A 401 means the coordinator refused this credential. Keeping it would
    re-send the same rejected token every ten seconds."""
    cfg = _configured(monkeypatch)
    node = NodeHeartbeat(cfg)
    stub_http.heartbeat_status = 401
    await_sync(node.beat_once())
    await_sync(node.beat_once())

    minted = [c for c in stub_http.calls if c[0].endswith("/oauth2/token")]
    assert len(minted) == 2


def test_an_unreachable_coordinator_is_not_an_exception(monkeypatch, stub_http):
    cfg = _configured(monkeypatch)

    async def _explode(*_a, **_kw):
        raise OSError("no route to host")

    monkeypatch.setattr(_Client, "post", _explode)
    assert await_sync(NodeHeartbeat(cfg).beat_once()) is False


def await_sync(coro):
    """Run one coroutine to completion from a sync test.

    These cases are about one call's behaviour, not about concurrency, and a sync
    test keeps `monkeypatch.setenv` and the assertion in the same frame.
    """
    import asyncio

    return asyncio.run(coro)


# ── What the process starts ──────────────────────────────────────────────────


@pytest.fixture
def coordinator_app(tmp_path, monkeypatch):
    """The real app, taken through its lifespan with a coordinator's state.

    `TestClient(app)` only runs the lifespan when used as a context manager,
    which is why the rest of the suite can share this module-level app without
    starting background tasks.
    """
    from types import SimpleNamespace

    from prometheus_manager_api.app import app

    fleet, node_id = _fleet(tmp_path)
    monkeypatch.setenv("PMGR_FLEET_NODE_ID", node_id)
    app.state.fleet = fleet

    def _with(cfg: FleetConfig):
        app.state.config = SimpleNamespace(fleet=cfg)
        return app

    try:
        yield _with
    finally:
        for attr in ("fleet", "config", "fleet_sweep", "fleet_heartbeat"):
            if hasattr(app.state, attr):
                delattr(app.state, attr)
        fleet.close()


def test_the_sweep_can_be_turned_off(coordinator_app):
    """Off is the end state, not an option: while a probe also stamps
    `last_seen_at`, a node that is down and one we could not reach stay
    indistinguishable."""
    from fastapi.testclient import TestClient

    app = coordinator_app(FleetConfig(coordinator=True, sweep=False))
    with TestClient(app):
        assert not hasattr(app.state, "fleet_sweep")
        assert hasattr(app.state, "fleet_heartbeat"), "the node should still report itself"


def test_a_node_without_credentials_still_starts(coordinator_app, monkeypatch):
    """PRM-147's lesson: inference does not depend on the heartbeat, so a missing
    environment variable disables it and is logged by name — it never stops the
    process."""
    from fastapi.testclient import TestClient

    monkeypatch.delenv("PMGR_FLEET_NODE_ID", raising=False)
    app = coordinator_app(FleetConfig(coordinator=True))
    with TestClient(app) as client:
        assert not hasattr(app.state, "fleet_heartbeat")
        assert client.get("/health").status_code == 200
