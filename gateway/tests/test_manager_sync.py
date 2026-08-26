"""Tests for ManagerRegistrySync — RM-08 phase 2 (distributed inference across hosts)
and RM-20 (dynamic node registry, replacing the old static MANAGER_NODES).

See docs/roadmap.md RM-08, RM-20.
"""

from __future__ import annotations

from collections.abc import Collection

import respx
from httpx import Response

from prometheus_gateway.models.manager_sync import ManagerRegistrySync
from prometheus_gateway.models.registry import ModelRegistry

AUTH_ADMIN_URL = "http://auth.test/admin"
AUTH_ADMIN_KEY = "test-admin-key"


def _mock_nodes(*nodes: tuple[str, str], inactive: Collection[str] = ()) -> None:
    """Mock auth-service's GET /admin/nodes — nodes as (name, manager_url) pairs.

    `inactive` names a subset of node names to mark is_active=False, matching
    the shape of a node that failed its connectivity check (RM-20 follow-up).
    """
    respx.get(f"{AUTH_ADMIN_URL}/nodes").mock(
        return_value=Response(
            200,
            json=[
                {
                    "id": name,
                    "name": name,
                    "manager_url": url,
                    "node_type": "mac",
                    "tag": None,
                    "is_active": name not in inactive,
                    "created_at": "2026-01-01T00:00:00Z",
                    "updated_at": None,
                }
                for name, url in nodes
            ],
        )
    )


def _sync(registry: ModelRegistry | None = None) -> ManagerRegistrySync:
    if registry is None:
        registry = ModelRegistry.__new__(ModelRegistry)
        registry._models = {}
    return ManagerRegistrySync(
        auth_service_admin_url=AUTH_ADMIN_URL,
        auth_service_admin_api_key=AUTH_ADMIN_KEY,
        registry=registry,
    )


def _backend(model_id: str, port: int, host: str = "127.0.0.1") -> dict:
    return {
        "id": model_id,
        "path": f"/models/{model_id}.gguf",
        "context_length": 4096,
        "family": "llama3",
        "quantization": "Q4_0",
        "backend_url": f"http://{host}:{port}",
        "state": "ready",
        "discovery": True,
    }


async def test_refresh_nodes_populates_allowed_backend_hosts():
    """Only the specific registered node hostnames are trusted, not arbitrary hosts."""
    sync = _sync()
    with respx.mock:
        _mock_nodes(("mac", "http://mac.local:8090"), ("dgx", "http://dgx.local:8090"))
        await sync._refresh_nodes()

    assert "mac.local" in sync._allowed_backend_hosts
    assert "dgx.local" in sync._allowed_backend_hosts
    assert "127.0.0.1" in sync._allowed_backend_hosts  # base loopback always trusted
    assert "some-random-host.example.com" not in sync._allowed_backend_hosts


async def test_refresh_nodes_filters_out_inactive_nodes():
    """A node that failed its connectivity check is excluded from routing/polling."""
    sync = _sync()
    with respx.mock:
        _mock_nodes(
            ("mac", "http://mac.local:8090"),
            ("dgx", "http://dgx.local:8090"),
            inactive={"dgx"},
        )
        await sync._refresh_nodes()

    assert sync._nodes == [("mac", "http://mac.local:8090")]
    assert "dgx.local" not in sync._allowed_backend_hosts


async def test_refresh_nodes_unreachable_keeps_previous_list():
    """A blip fetching the node registry doesn't wipe out the last-known node list."""
    sync = _sync()
    with respx.mock:
        _mock_nodes(("mac", "http://mac.local:8090"))
        await sync._refresh_nodes()
    assert sync._nodes == [("mac", "http://mac.local:8090")]

    with respx.mock:
        respx.get(f"{AUTH_ADMIN_URL}/nodes").mock(side_effect=ConnectionError("down"))
        await sync._refresh_nodes()
    assert sync._nodes == [("mac", "http://mac.local:8090")]  # unchanged


async def test_sync_merges_models_from_two_nodes():
    registry = ModelRegistry.__new__(ModelRegistry)
    registry._models = {}
    sync = _sync(registry)

    with respx.mock:
        _mock_nodes(("mac", "http://mac.local:8090"), ("dgx", "http://dgx.local:8090"))
        respx.get("http://mac.local:8090/v1/backends").mock(
            return_value=Response(200, json={"backends": [_backend("model-a", 8080)]})
        )
        respx.get("http://dgx.local:8090/v1/backends").mock(
            return_value=Response(
                200, json={"backends": [_backend("model-b", 8081, host="dgx.local")]}
            )
        )
        await sync._sync()

    assert set(registry._models.keys()) == {"model-a", "model-b"}
    assert registry._models["model-a"].node == "mac"
    assert registry._models["model-b"].node == "dgx"
    # dgx.local is trusted (it's a registered node hostname) — backend_url stays active
    assert registry._models["model-b"].backend_status == "active"


async def test_sync_one_node_unreachable_others_still_sync():
    """Partial availability: a down node's models disappear, others are unaffected."""
    registry = ModelRegistry.__new__(ModelRegistry)
    registry._models = {}
    sync = _sync(registry)

    with respx.mock:
        _mock_nodes(("mac", "http://mac.local:8090"), ("dgx", "http://dgx.local:8090"))
        respx.get("http://mac.local:8090/v1/backends").mock(
            return_value=Response(200, json={"backends": [_backend("model-a", 8080)]})
        )
        respx.get("http://dgx.local:8090/v1/backends").mock(side_effect=ConnectionError("down"))
        await sync._sync()

    assert set(registry._models.keys()) == {"model-a"}


async def test_sync_model_id_collision_keeps_first_node():
    """Same model_id on two nodes is ambiguous — keep the first, drop + warn on the rest."""
    registry = ModelRegistry.__new__(ModelRegistry)
    registry._models = {}
    sync = _sync(registry)

    with respx.mock:
        _mock_nodes(("mac", "http://mac.local:8090"), ("dgx", "http://dgx.local:8090"))
        respx.get("http://mac.local:8090/v1/backends").mock(
            return_value=Response(200, json={"backends": [_backend("dup-model", 8080)]})
        )
        respx.get("http://dgx.local:8090/v1/backends").mock(
            return_value=Response(
                200, json={"backends": [_backend("dup-model", 8081, host="dgx.local")]}
            )
        )
        await sync._sync()

    assert len(registry._models) == 1
    assert registry._models["dup-model"].node == "mac"  # first node in the list wins


async def test_untrusted_backend_host_marked_invalid():
    """A backend_url pointing outside every registered node's host is rejected."""
    registry = ModelRegistry.__new__(ModelRegistry)
    registry._models = {}
    sync = _sync(registry)

    with respx.mock:
        _mock_nodes(("mac", "http://mac.local:8090"))
        respx.get("http://mac.local:8090/v1/backends").mock(
            return_value=Response(
                200,
                json={"backends": [_backend("sneaky-model", 8080, host="evil.example.com")]},
            )
        )
        await sync._sync()

    assert registry._models["sneaky-model"].backend_status == "invalid"
    assert registry._models["sneaky-model"].backend_url is None


async def test_sync_with_no_nodes_registered_yields_empty_registry():
    registry = ModelRegistry.__new__(ModelRegistry)
    registry._models = {}
    sync = _sync(registry)

    with respx.mock:
        _mock_nodes()
        await sync._sync()

    assert registry._models == {}
