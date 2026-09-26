"""The fleet registry endpoints — PRM-134.

These 25 behaviour tests were auth-service's (`auth-service/tests/test_nodes.py`)
until the node registry moved here. They are **ported, not rewritten**: what they
pin is the contract the dashboard depends on, and the point of the move was that
the contract did not change. Two asymmetries in particular were deliberate there
and are deliberate here:

  * `/activate` re-probes and only activates on success; `/deactivate` just flips
    the flag. Showing "Active" for a node that cannot be reached is a lie an
    operator would act on; taking a node out of rotation needs no permission from
    the node.
  * `engines` has three states — `None` (never declared), `[]` (declared none),
    and a list. An unrelated edit must not collapse the first into the second.

New here, and specific to this design: a manager-api that is not the coordinator
holds no registry and says so with a 409.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from prometheus_manager_core.fleet import FleetRegistry

from prometheus_manager_api import fleet_routes
from prometheus_manager_api.app import app
from prometheus_manager_api.auth import (
    require_backend_registry_read,
    require_backend_registry_write,
)

_CLAIMS = {"sub": "gateway", "scope": "backend-registry:read backend-registry:write"}


@pytest.fixture
def client(tmp_path: Path, monkeypatch):
    """A coordinator with an empty registry, auth bypassed, node reachable.

    The probe is patched to succeed by default so CRUD behaviour does not depend
    on anything being on the network; the tests that care about reachability
    override it.
    """

    async def _reachable(manager_url: str) -> bool:
        return True

    monkeypatch.setattr(fleet_routes, "_probe", _reachable)

    fleet = FleetRegistry(tmp_path / "fleet.db")
    app.state.fleet = fleet
    app.dependency_overrides[require_backend_registry_read] = lambda: _CLAIMS
    app.dependency_overrides[require_backend_registry_write] = lambda: _CLAIMS
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(require_backend_registry_read, None)
        app.dependency_overrides.pop(require_backend_registry_write, None)
        if hasattr(app.state, "fleet"):
            del app.state.fleet
        fleet.close()


@pytest.fixture
def plain_node_client():
    """A manager-api that is NOT the coordinator — no `app.state.fleet` at all."""
    app.dependency_overrides[require_backend_registry_read] = lambda: _CLAIMS
    app.dependency_overrides[require_backend_registry_write] = lambda: _CLAIMS
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(require_backend_registry_read, None)
        app.dependency_overrides.pop(require_backend_registry_write, None)


def _create(
    client, name="mac-studio-1", manager_url="http://127.0.0.1:8090", node_type="mac", tag=None
):
    payload = {"name": name, "manager_url": manager_url, "node_type": node_type}
    if tag is not None:
        payload["tag"] = tag
    resp = client.post("/v1/fleet/nodes", json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()


# ── CRUD ─────────────────────────────────────────────────────────────────────


def test_create(client):
    node = _create(client, tag="primary")
    assert node["name"] == "mac-studio-1"
    assert node["manager_url"] == "http://127.0.0.1:8090"
    assert node["node_type"] == "mac"
    assert node["tag"] == "primary"
    assert node["is_active"] is True


def test_duplicate_name_rejected(client):
    _create(client, name="dup-node")
    resp = client.post(
        "/v1/fleet/nodes",
        json={"name": "dup-node", "manager_url": "http://other:8090", "node_type": "nvidia"},
    )
    assert resp.status_code == 409


def test_list(client):
    _create(client, name="list-node-1")
    _create(client, name="list-node-2", node_type="nvidia")
    resp = client.get("/v1/fleet/nodes")
    assert resp.status_code == 200
    assert {"list-node-1", "list-node-2"}.issubset({n["name"] for n in resp.json()})


def test_update(client):
    node = _create(client, name="update-node")
    resp = client.patch(
        f"/v1/fleet/nodes/{node['id']}",
        json={"manager_url": "http://new-host:9999", "tag": "renamed"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["manager_url"] == "http://new-host:9999"
    assert body["tag"] == "renamed"
    assert body["name"] == "update-node"  # name is immutable


def test_update_not_found(client):
    assert client.patch("/v1/fleet/nodes/does-not-exist", json={"tag": "x"}).status_code == 404


def test_delete(client):
    node = _create(client, name="delete-node")
    assert client.delete(f"/v1/fleet/nodes/{node['id']}").status_code == 204
    assert "delete-node" not in {n["name"] for n in client.get("/v1/fleet/nodes").json()}


def test_delete_not_found(client):
    assert client.delete("/v1/fleet/nodes/does-not-exist").status_code == 404


def test_hourly_cost_is_the_sum_of_its_two_halves(client):
    """Computed, never stored — so the total cannot disagree with its parts."""
    node = _create(client, name="cost-node")
    assert node["hourly_cost_usd"] == pytest.approx(
        node["hardware_amortization_usd_per_hour"] + node["electricity_usd_per_hour"]
    )


# ── Connectivity: what the probe decides and what it does not ────────────────


def test_create_unreachable_is_inactive(client, monkeypatch):
    """Registered anyway, so the operator does not lose the entry they typed."""

    async def _down(manager_url: str) -> bool:
        return False

    monkeypatch.setattr(fleet_routes, "_probe", _down)
    assert _create(client, name="unreachable-node")["is_active"] is False


def test_update_manager_url_rechecks_connectivity(client, monkeypatch):
    node = _create(client, name="recheck-on-update")
    assert node["is_active"] is True

    async def _down(manager_url: str) -> bool:
        return False

    monkeypatch.setattr(fleet_routes, "_probe", _down)
    resp = client.patch(
        f"/v1/fleet/nodes/{node['id']}", json={"manager_url": "http://now-down:8090"}
    )
    assert resp.status_code == 200
    assert resp.json()["is_active"] is False


def test_check_updates_status(client, monkeypatch):
    node = _create(client, name="check-endpoint-node")

    async def _down(manager_url: str) -> bool:
        return False

    monkeypatch.setattr(fleet_routes, "_probe", _down)
    resp = client.post(f"/v1/fleet/nodes/{node['id']}/check")
    assert resp.status_code == 200
    assert resp.json()["is_active"] is False


def test_check_not_found(client):
    assert client.post("/v1/fleet/nodes/does-not-exist/check").status_code == 404


def test_deactivate_is_a_manual_override_independent_of_connectivity(client):
    """The node is reachable, and /deactivate takes it out anyway."""
    node = _create(client, name="manual-toggle-node")
    assert node["is_active"] is True
    resp = client.post(f"/v1/fleet/nodes/{node['id']}/deactivate")
    assert resp.status_code == 200
    assert resp.json()["is_active"] is False


def test_activate_succeeds_when_reachable(client):
    node = _create(client, name="activate-when-reachable")
    client.post(f"/v1/fleet/nodes/{node['id']}/deactivate")
    resp = client.post(f"/v1/fleet/nodes/{node['id']}/activate")
    assert resp.status_code == 200
    assert resp.json()["is_active"] is True


def test_activate_refuses_when_unreachable(client, monkeypatch):
    """/activate cannot just flip the flag — the asymmetry with /deactivate."""

    async def _down(manager_url: str) -> bool:
        return False

    monkeypatch.setattr(fleet_routes, "_probe", _down)
    node = _create(client, name="activate-when-unreachable")
    assert node["is_active"] is False
    resp = client.post(f"/v1/fleet/nodes/{node['id']}/activate")
    assert resp.status_code == 200
    assert resp.json()["is_active"] is False


def test_deactivate_not_found(client):
    assert client.post("/v1/fleet/nodes/does-not-exist/deactivate").status_code == 404


def test_activate_not_found(client):
    assert client.post("/v1/fleet/nodes/does-not-exist/activate").status_code == 404


# ── PRM-133: declared engines, and the three states ──────────────────────────


def test_a_node_created_without_engines_is_undeclared_not_empty(client):
    """null, not []. The instance form reads this and must not be told the node
    can launch nothing when nobody has said anything about it yet."""
    assert _create(client, name="undeclared-1")["engines"] is None


def test_engines_round_trip(client):
    resp = client.post(
        "/v1/fleet/nodes",
        json={
            "name": "declared-1",
            "manager_url": "http://127.0.0.1:8090",
            "node_type": "mac",
            "engines": ["llama_cpp", "mlx"],
        },
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["engines"] == ["llama_cpp", "mlx"]

    row = next(n for n in client.get("/v1/fleet/nodes").json() if n["name"] == "declared-1")
    assert row["engines"] == ["llama_cpp", "mlx"]


def test_a_node_can_declare_it_has_none(client):
    """`[]` is an answer, and a different one from never having been asked."""
    resp = client.post(
        "/v1/fleet/nodes",
        json={
            "name": "empty-1",
            "manager_url": "http://127.0.0.1:8090",
            "node_type": "other",
            "engines": [],
        },
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["engines"] == []


def test_an_unrelated_edit_does_not_declare_engines(client):
    """The trap `tag` already had: an `is not None` check on a field whose None is
    meaningful turns "leave it alone" into "set it to nothing"."""
    node = _create(client, name="untouched-1")
    assert node["engines"] is None
    resp = client.patch(f"/v1/fleet/nodes/{node['id']}", json={"tag": "edited"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["tag"] == "edited"
    assert resp.json()["engines"] is None


def test_engines_can_be_cleared_explicitly(client):
    node = _create(client, name="clearable-1")
    client.patch(f"/v1/fleet/nodes/{node['id']}", json={"engines": ["vllm"]})
    resp = client.patch(f"/v1/fleet/nodes/{node['id']}", json={"engines": []})
    assert resp.status_code == 200, resp.text
    assert resp.json()["engines"] == []


def test_engines_are_deduplicated_preserving_order(client):
    resp = client.post(
        "/v1/fleet/nodes",
        json={
            "name": "dupes-1",
            "manager_url": "http://127.0.0.1:8090",
            "node_type": "mac",
            "engines": ["mlx", "llama_cpp", "mlx"],
        },
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["engines"] == ["mlx", "llama_cpp"]


def test_an_engine_id_that_is_not_an_identifier_is_refused(client):
    resp = client.post(
        "/v1/fleet/nodes",
        json={
            "name": "bad-1",
            "manager_url": "http://127.0.0.1:8090",
            "node_type": "mac",
            "engines": ["llama cpp; drop table nodes"],
        },
    )
    assert resp.status_code == 422, resp.text


def test_an_unknown_but_well_formed_engine_is_stored(client):
    """A shape check, not a membership one — and here the reason is sharper than
    it was in auth-service. BACKENDS is what *this* node can launch; a fleet row
    describes what some *other* node has installed, so a coordinator that
    rejected an engine it does not have itself would refuse a good node."""
    resp = client.post(
        "/v1/fleet/nodes",
        json={
            "name": "future-1",
            "manager_url": "http://127.0.0.1:8090",
            "node_type": "mac",
            "engines": ["some_engine_from_2027"],
        },
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["engines"] == ["some_engine_from_2027"]


def test_an_unknown_node_type_is_refused(client):
    resp = client.post(
        "/v1/fleet/nodes",
        json={"name": "bad-type", "manager_url": "http://x:8090", "node_type": "quantum"},
    )
    assert resp.status_code == 422, resp.text


# ── New in PRM-134: only the coordinator holds a registry ─────────────────────


def test_a_plain_node_says_it_is_not_the_coordinator(plain_node_client):
    """409, not 404, and the reason matters: a 404 is indistinguishable from a
    wrong path, and an operator debugging a fleet that will not list needs to be
    told which of the two it is."""
    resp = plain_node_client.get("/v1/fleet/nodes")
    assert resp.status_code == 409
    assert "coordinator" in resp.json()["detail"]


def test_a_plain_node_refuses_every_fleet_write(plain_node_client):
    """All seven, not just the read — a node that cannot hold the registry must
    not accept a write to it either."""
    node_id = "any-id"
    calls = [
        plain_node_client.post(
            "/v1/fleet/nodes",
            json={"name": "x", "manager_url": "http://x:8090", "node_type": "mac"},
        ),
        plain_node_client.patch(f"/v1/fleet/nodes/{node_id}", json={"tag": "x"}),
        plain_node_client.post(f"/v1/fleet/nodes/{node_id}/check"),
        plain_node_client.post(f"/v1/fleet/nodes/{node_id}/activate"),
        plain_node_client.post(f"/v1/fleet/nodes/{node_id}/deactivate"),
        plain_node_client.delete(f"/v1/fleet/nodes/{node_id}"),
    ]
    assert [r.status_code for r in calls] == [409] * 6
