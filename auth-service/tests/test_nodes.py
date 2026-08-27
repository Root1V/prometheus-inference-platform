"""Tests for RM-20 — node registry (/admin/nodes).

Implements: docs/roadmap.md — RM-20

Connectivity checks (_check_node_reachable) hit the network — patched to a fixed
result in most tests here so CRUD behavior doesn't depend on real reachability;
the dedicated `test_nodes_connectivity_*` tests below exercise the check itself.
"""

import pytest

from prometheus_auth.routers import admin as admin_router

from .conftest import ADMIN_HEADERS


@pytest.fixture(autouse=True)
def _reachable(monkeypatch):
    """Default all connectivity checks to "reachable" unless a test overrides it."""

    async def _fake_check(manager_url: str) -> bool:
        return True

    monkeypatch.setattr(admin_router, "_check_node_reachable", _fake_check)


async def _create_node(
    client, name="mac-studio-1", manager_url="http://127.0.0.1:8090", node_type="mac", tag=None
):
    payload = {"name": name, "manager_url": manager_url, "node_type": node_type}
    if tag is not None:
        payload["tag"] = tag
    resp = await client.post("/admin/nodes", json=payload, headers=ADMIN_HEADERS)
    assert resp.status_code == 201, resp.text
    return resp.json()


async def test_nodes_create(client):
    node = await _create_node(client, tag="primary")
    assert node["name"] == "mac-studio-1"
    assert node["manager_url"] == "http://127.0.0.1:8090"
    assert node["node_type"] == "mac"
    assert node["tag"] == "primary"
    assert node["is_active"] is True


async def test_nodes_admin_key_required(client):
    resp = await client.post(
        "/admin/nodes", json={"name": "x", "manager_url": "http://x", "node_type": "mac"}
    )
    assert resp.status_code == 403


async def test_nodes_duplicate_name_rejected(client):
    await _create_node(client, name="dup-node")
    resp = await client.post(
        "/admin/nodes",
        json={"name": "dup-node", "manager_url": "http://other:8090", "node_type": "nvidia"},
        headers=ADMIN_HEADERS,
    )
    assert resp.status_code == 409


async def test_nodes_list(client):
    await _create_node(client, name="list-node-1")
    await _create_node(client, name="list-node-2", node_type="nvidia")
    resp = await client.get("/admin/nodes", headers=ADMIN_HEADERS)
    assert resp.status_code == 200
    names = {n["name"] for n in resp.json()}
    assert {"list-node-1", "list-node-2"}.issubset(names)


async def test_nodes_update(client):
    node = await _create_node(client, name="update-node")
    resp = await client.patch(
        f"/admin/nodes/{node['id']}",
        json={"manager_url": "http://new-host:9999", "tag": "renamed"},
        headers=ADMIN_HEADERS,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["manager_url"] == "http://new-host:9999"
    assert body["tag"] == "renamed"
    assert body["name"] == "update-node"  # name is immutable


async def test_nodes_update_not_found(client):
    resp = await client.patch(
        "/admin/nodes/does-not-exist", json={"tag": "x"}, headers=ADMIN_HEADERS
    )
    assert resp.status_code == 404


async def test_nodes_delete(client):
    node = await _create_node(client, name="delete-node")
    resp = await client.delete(f"/admin/nodes/{node['id']}", headers=ADMIN_HEADERS)
    assert resp.status_code == 204

    listed = await client.get("/admin/nodes", headers=ADMIN_HEADERS)
    names = {n["name"] for n in listed.json()}
    assert "delete-node" not in names


async def test_nodes_delete_not_found(client):
    resp = await client.delete("/admin/nodes/does-not-exist", headers=ADMIN_HEADERS)
    assert resp.status_code == 404


async def test_nodes_create_unreachable_is_inactive(client, monkeypatch):
    async def _fake_check(manager_url: str) -> bool:
        return False

    monkeypatch.setattr(admin_router, "_check_node_reachable", _fake_check)

    node = await _create_node(client, name="unreachable-node")
    assert node["is_active"] is False


async def test_nodes_update_manager_url_rechecks_connectivity(client, monkeypatch):
    node = await _create_node(client, name="recheck-on-update")
    assert node["is_active"] is True

    async def _fake_check(manager_url: str) -> bool:
        return False

    monkeypatch.setattr(admin_router, "_check_node_reachable", _fake_check)
    resp = await client.patch(
        f"/admin/nodes/{node['id']}",
        json={"manager_url": "http://now-down:8090"},
        headers=ADMIN_HEADERS,
    )
    assert resp.status_code == 200
    assert resp.json()["is_active"] is False


async def test_nodes_check_endpoint_updates_status(client, monkeypatch):
    node = await _create_node(client, name="check-endpoint-node")

    async def _fake_check(manager_url: str) -> bool:
        return False

    monkeypatch.setattr(admin_router, "_check_node_reachable", _fake_check)
    resp = await client.post(f"/admin/nodes/{node['id']}/check", headers=ADMIN_HEADERS)
    assert resp.status_code == 200
    assert resp.json()["is_active"] is False


async def test_nodes_check_endpoint_not_found(client):
    resp = await client.post("/admin/nodes/does-not-exist/check", headers=ADMIN_HEADERS)
    assert resp.status_code == 404


async def test_nodes_deactivate_is_manual_override_independent_of_connectivity(client):
    """/deactivate marks a node inactive even though it's reachable (_reachable fixture)."""
    node = await _create_node(client, name="manual-toggle-node")
    assert node["is_active"] is True

    resp = await client.post(f"/admin/nodes/{node['id']}/deactivate", headers=ADMIN_HEADERS)
    assert resp.status_code == 200
    assert resp.json()["is_active"] is False


async def test_nodes_activate_succeeds_when_reachable(client):
    """/activate re-probes the node — succeeds when the probe is reachable."""
    node = await _create_node(client, name="activate-when-reachable")

    # deactivate first (the reachable-by-default fixture would make this a no-op check)
    await client.post(f"/admin/nodes/{node['id']}/deactivate", headers=ADMIN_HEADERS)

    resp = await client.post(f"/admin/nodes/{node['id']}/activate", headers=ADMIN_HEADERS)
    assert resp.status_code == 200
    assert resp.json()["is_active"] is True


async def test_nodes_activate_refuses_when_unreachable(client, monkeypatch):
    """/activate can't just flip the flag — an unreachable node stays inactive."""

    async def _fake_check(manager_url: str) -> bool:
        return False

    monkeypatch.setattr(admin_router, "_check_node_reachable", _fake_check)
    node = await _create_node(client, name="activate-when-unreachable")
    assert node["is_active"] is False

    resp = await client.post(f"/admin/nodes/{node['id']}/activate", headers=ADMIN_HEADERS)
    assert resp.status_code == 200
    assert resp.json()["is_active"] is False


async def test_nodes_deactivate_not_found(client):
    resp = await client.post("/admin/nodes/does-not-exist/deactivate", headers=ADMIN_HEADERS)
    assert resp.status_code == 404


async def test_nodes_activate_not_found(client):
    resp = await client.post("/admin/nodes/does-not-exist/activate", headers=ADMIN_HEADERS)
    assert resp.status_code == 404
