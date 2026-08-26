"""Tests for RM-20 — node registry (/admin/nodes).

Implements: docs/roadmap.md — RM-20
"""

from .conftest import ADMIN_HEADERS


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
