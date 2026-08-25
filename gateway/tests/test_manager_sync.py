"""Tests for ManagerRegistrySync — RM-08 phase 2 (distributed inference across hosts).

See docs/roadmap.md RM-08 and memory/wiki/model-registry.md.
"""

from __future__ import annotations

import pytest
import respx
from httpx import Response

from prometheus_gateway.config import Settings
from prometheus_gateway.models.manager_sync import ManagerRegistrySync
from prometheus_gateway.models.registry import ModelRegistry

# ── Settings.resolved_manager_nodes ──────────────────────────────────────────


def _settings(**overrides) -> Settings:
    # manager_url/manager_nodes explicitly None by default so a developer's real
    # local gateway/.env (which may set MANAGER_URL) can't leak into these tests.
    base = dict(
        jwt_issuer="https://auth.test",
        jwt_public_key_file="/dev/null",
        manager_url=None,
        manager_nodes=None,
    )
    base.update(overrides)
    return Settings(**base)


def test_no_nodes_configured_returns_empty_list():
    assert _settings().resolved_manager_nodes == []


def test_single_manager_url_backward_compat():
    """Existing single-node deployments (MANAGER_URL only) keep working unchanged."""
    s = _settings(manager_url="http://127.0.0.1:8090")
    assert s.resolved_manager_nodes == [("default", "http://127.0.0.1:8090")]


def test_manager_nodes_parses_multiple():
    s = _settings(manager_nodes="mac=http://mac.local:8090,dgx=http://dgx.local:8090")
    assert s.resolved_manager_nodes == [
        ("mac", "http://mac.local:8090"),
        ("dgx", "http://dgx.local:8090"),
    ]


def test_manager_nodes_takes_priority_over_manager_url():
    s = _settings(
        manager_url="http://127.0.0.1:8090",
        manager_nodes="mac=http://mac.local:8090",
    )
    assert s.resolved_manager_nodes == [("mac", "http://mac.local:8090")]


def test_manager_nodes_malformed_entry_raises():
    s = _settings(manager_nodes="not-a-valid-entry")
    with pytest.raises(ValueError, match="MANAGER_NODES"):
        _ = s.resolved_manager_nodes


# ── ManagerRegistrySync ───────────────────────────────────────────────────────


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


def test_allowed_backend_hosts_includes_node_hostnames():
    """Only the specific configured node hostnames are trusted, not arbitrary hosts."""
    sync = ManagerRegistrySync(
        nodes=[("mac", "http://mac.local:8090"), ("dgx", "http://dgx.local:8090")],
        registry=ModelRegistry.__new__(ModelRegistry),
    )
    assert "mac.local" in sync._allowed_backend_hosts
    assert "dgx.local" in sync._allowed_backend_hosts
    assert "127.0.0.1" in sync._allowed_backend_hosts  # base loopback always trusted
    assert "some-random-host.example.com" not in sync._allowed_backend_hosts


async def test_sync_merges_models_from_two_nodes():
    registry = ModelRegistry.__new__(ModelRegistry)
    registry._models = {}
    sync = ManagerRegistrySync(
        nodes=[("mac", "http://mac.local:8090"), ("dgx", "http://dgx.local:8090")],
        registry=registry,
    )

    with respx.mock:
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
    # dgx.local is trusted (it's a configured node hostname) — backend_url stays active
    assert registry._models["model-b"].backend_status == "active"


async def test_sync_one_node_unreachable_others_still_sync():
    """Partial availability: a down node's models disappear, others are unaffected."""
    registry = ModelRegistry.__new__(ModelRegistry)
    registry._models = {}
    sync = ManagerRegistrySync(
        nodes=[("mac", "http://mac.local:8090"), ("dgx", "http://dgx.local:8090")],
        registry=registry,
    )

    with respx.mock:
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
    sync = ManagerRegistrySync(
        nodes=[("mac", "http://mac.local:8090"), ("dgx", "http://dgx.local:8090")],
        registry=registry,
    )

    with respx.mock:
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
    """A backend_url pointing outside every configured node's host is rejected."""
    registry = ModelRegistry.__new__(ModelRegistry)
    registry._models = {}
    sync = ManagerRegistrySync(
        nodes=[("mac", "http://mac.local:8090")],
        registry=registry,
    )

    with respx.mock:
        respx.get("http://mac.local:8090/v1/backends").mock(
            return_value=Response(
                200,
                json={"backends": [_backend("sneaky-model", 8080, host="evil.example.com")]},
            )
        )
        await sync._sync()

    assert registry._models["sneaky-model"].backend_status == "invalid"
    assert registry._models["sneaky-model"].backend_url is None
