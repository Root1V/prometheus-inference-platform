"""PRM-152 — a node's credential says which node it is.

See docs/roadmap.md PRM-152. Two scopes together: `fleet:heartbeat` is what the
credential may *do* and `node:<id>` is who it may do it *for*. Separating them is
the point — the heartbeat endpoint used to require `backend-registry:write`, so a
credential handed to a node so it could say "I am alive" also let it register,
cordon and delete every other node and start and stop instances on every manager.

`node:<id>` is pattern-matched rather than enumerated for RM-07's reason: node ids
live in the fleet coordinator's registry, and a copy of that list here would be a
second answer to "which nodes exist".
"""

from jose import jwt

from prometheus_auth.schemas import invalid_scopes, is_valid_scope

from .conftest import ADMIN_HEADERS, register_client

# A UUID, because that is what the coordinator assigns at registration and so what
# a `node:<id>` grant has to accommodate. Deliberately not one this fleet holds:
# this repository is public, and a test does not need the id to be real to pin the
# pattern.
NODE_ID = "11111111-1111-4111-8111-111111111111"


# ── The scopes themselves ─────────────────────────────────────────────────────


def test_fleet_heartbeat_is_a_platform_scope():
    assert is_valid_scope("fleet:heartbeat")


def test_node_scope_valid():
    assert is_valid_scope(f"node:{NODE_ID}")


def test_node_scope_requires_an_id_after_the_prefix():
    assert not is_valid_scope("node:")


def test_a_node_scope_is_not_a_model_scope():
    """Both are pattern-matched; neither pattern may accept the other's prefix."""
    assert not is_valid_scope("nodes:abc")
    assert invalid_scopes({"node:abc", "model:abc", "nope:abc"}) == {"nope:abc"}


# ── Granting them to a node's own client ──────────────────────────────────────


async def test_a_node_client_can_be_registered_with_both(client):
    data = await register_client(
        client, name="node-local", scopes=["fleet:heartbeat", f"node:{NODE_ID}"]
    )
    assert set(data["allowed_scopes"]) == {"fleet:heartbeat", f"node:{NODE_ID}"}


async def test_a_malformed_node_scope_is_rejected(client):
    resp = await client.post(
        "/admin/clients",
        json={"client_name": "bad-node", "role": "app", "allowed_scopes": ["node:"]},
        headers=ADMIN_HEADERS,
    )
    assert resp.status_code == 422


async def test_the_token_carries_the_node_it_speaks_for(client):
    data = await register_client(
        client, name="node-beats", scopes=["fleet:heartbeat", f"node:{NODE_ID}"]
    )
    resp = await client.post(
        "/oauth2/token",
        data={
            "grant_type": "client_credentials",
            "client_id": data["client_id"],
            "client_secret": data["client_secret"],
            "scope": f"fleet:heartbeat node:{NODE_ID}",
        },
    )
    assert resp.status_code == 200
    payload = jwt.get_unverified_claims(resp.json()["access_token"])
    assert set(payload["scope"].split()) == {"fleet:heartbeat", f"node:{NODE_ID}"}


async def test_a_node_cannot_ask_for_another_nodes_grant(client):
    """The whole reason there is one client per node: `local`'s credential must
    not be able to obtain a token that speaks for `lab`."""
    data = await register_client(
        client, name="node-only-itself", scopes=["fleet:heartbeat", f"node:{NODE_ID}"]
    )
    resp = await client.post(
        "/oauth2/token",
        data={
            "grant_type": "client_credentials",
            "client_id": data["client_id"],
            "client_secret": data["client_secret"],
            "scope": "fleet:heartbeat node:22222222-2222-4222-8222-222222222222",
        },
    )
    assert resp.status_code == 400
    assert resp.json()["error"] == "invalid_scope"
