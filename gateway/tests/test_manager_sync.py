"""Tests for ManagerRegistrySync — RM-08 phase 2 (distributed inference across hosts)
and RM-20 (dynamic node registry, replacing the old static MANAGER_NODES).

See docs/roadmap.md RM-08, RM-20.
"""

from __future__ import annotations

from collections.abc import Collection

from datetime import datetime, timezone
from unittest.mock import patch

import httpx
import respx
from httpx import Response

from prometheus_gateway import db
from prometheus_gateway.models.manager_sync import ManagerRegistrySync
from prometheus_gateway.models.registry import ModelEntry, ModelRegistry

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


async def test_sync_passes_through_model_id_from_manager_api():
    """RM-51: a /v1/backends response that includes model_id (the catalog FK)
    must carry it through to the gateway's ModelEntry unchanged."""
    registry = ModelRegistry.__new__(ModelRegistry)
    registry._models = {}
    sync = _sync(registry)

    backend = _backend("model-a-2", 8080)
    backend["model_id"] = "model-a"

    with respx.mock:
        _mock_nodes(("mac", "http://mac.local:8090"))
        respx.get("http://mac.local:8090/v1/backends").mock(
            return_value=Response(200, json={"backends": [backend]})
        )
        await sync._sync()

    assert registry._models["model-a-2"].model_id == "model-a"


async def test_sync_model_id_falls_back_to_instance_id_when_absent():
    """A not-yet-upgraded manager node (pre-RM-51) sends no model_id at all —
    the gateway must not crash or silently drop the entry, just default
    model_id to the instance's own id (matching pre-RM-51 semantics: one row,
    one id, serving as both)."""
    registry = ModelRegistry.__new__(ModelRegistry)
    registry._models = {}
    sync = _sync(registry)

    with respx.mock:
        _mock_nodes(("mac", "http://mac.local:8090"))
        respx.get("http://mac.local:8090/v1/backends").mock(
            return_value=Response(200, json={"backends": [_backend("legacy-model", 8080)]})
        )
        await sync._sync()

    assert registry._models["legacy-model"].model_id == "legacy-model"


@respx.mock
async def test_an_unreachable_manager_does_not_spin(monkeypatch):
    """RM-97: the first version of the blocking-query loop skipped its sleep on
    a *failed* request, so an unreachable manager became a busy loop — measured
    at 1455 sync cycles in ten seconds and 85% of a core. An outage is precisely
    when a gateway must not spin.
    """
    from prometheus_gateway.models import manager_sync as ms

    sync = ms.ManagerRegistrySync.__new__(ms.ManagerRegistrySync)
    sync._blocking_supported = True
    sync._held_last_request = True  # as if the previous cycle had been held
    sync._node_index = {"local": "abc"}

    respx.get("http://manager.test/v1/backends").mock(side_effect=httpx.ConnectError("refused"))

    async def _headers():
        return {}

    sync._get_auth_headers = _headers  # type: ignore[method-assign]
    result = await ms.ManagerRegistrySync._fetch_node_backends(sync, "local", "http://manager.test")

    # RM-98: None, not [] — a node that could not be asked has to be
    # distinguishable from one that genuinely serves nothing, or the caller
    # cannot keep its last known good state.
    assert result is None
    assert sync._held_last_request is False, "a failed request must fall back to sleeping"


async def test_a_manager_outage_keeps_the_last_known_catalog():
    """RM-98: fail static. The manager is a control plane — where a human
    registers models — and inference is the data plane. Restarting the former
    must not stop the latter. This used to replace the catalog with nothing, so
    the gateway served zero models: a control-plane component causing an
    inference outage.
    """
    sync = _sync()
    with respx.mock:
        _mock_nodes(("mac", "http://mac.local:8090"))
        route = respx.get("http://mac.local:8090/v1/backends")
        route.mock(return_value=Response(200, json={"backends": [_backend("model-a", 8080)]}))
        await sync._sync()
        assert set(sync._registry._models) == {"model-a"}

        # The manager goes away.
        route.mock(side_effect=httpx.ConnectError("refused"))
        await sync._sync()

    assert set(sync._registry._models) == {"model-a"}, "the catalog must survive the manager"


async def test_a_node_that_answers_with_nothing_really_has_nothing():
    """The other half: an empty answer is an answer. Holding stale entries for a
    node that successfully reported an empty list would make retiring the last
    model on a node impossible.
    """
    sync = _sync()
    with respx.mock:
        _mock_nodes(("mac", "http://mac.local:8090"))
        route = respx.get("http://mac.local:8090/v1/backends")
        route.mock(return_value=Response(200, json={"backends": [_backend("model-a", 8080)]}))
        await sync._sync()

        route.mock(return_value=Response(200, json={"backends": []}))
        await sync._sync()

    assert sync._registry._models == {}


async def test_the_snapshot_is_only_read_when_a_start_finds_no_manager():
    """RM-99: the disk copy is the exceptional case, not a second source of
    truth. It is read when the first sync of a process produced nothing, and
    never again — the moment the manager answers, its answer replaces
    everything, including dropping what it no longer serves.
    """
    sync = _sync()
    sync._registry._models = {"from-snapshot": object()}  # type: ignore[dict-item]

    called = False

    async def _should_not_run():
        nonlocal called
        called = True

    sync._restore_from_snapshot = _should_not_run  # type: ignore[method-assign]

    with respx.mock:
        _mock_nodes(("mac", "http://mac.local:8090"))
        respx.get("http://mac.local:8090/v1/backends").mock(
            return_value=Response(200, json={"backends": [_backend("model-a", 8080)]})
        )
        await sync.start()
        await sync.stop()

    assert called is False, "a start that reached the manager must not read the snapshot"
    assert set(sync._registry._models) == {"model-a"}


async def test_a_start_with_no_manager_restores_the_snapshot():
    sync = _sync()
    stored = {"mac": [_backend("model-a", 8080)]}

    async def _load():
        return stored, datetime.now(timezone.utc)

    with (
        respx.mock,
        patch.object(db, "load_catalog_snapshot", _load),
    ):
        _mock_nodes(("mac", "http://mac.local:8090"))
        respx.get("http://mac.local:8090/v1/backends").mock(
            side_effect=httpx.ConnectError("refused")
        )
        await sync.start()
        await sync.stop()

    assert set(sync._registry._models) == {"model-a"}


# ── A node list never fetched is not an empty one — PRM-146 ───────────────────


def _snapshot_entry(entry_id: str) -> ModelEntry:
    """A model already in the registry — as RM-99's snapshot restore leaves it."""
    return ModelEntry(
        id=entry_id,
        path=f"/models/{entry_id}.gguf",
        context_length=4096,
        family="llama3",
        quantization="Q4_0",
        backend_url="http://127.0.0.1:9001",
        backend_status="active",
        node="mac",
        model_id=entry_id,
        model_slug=entry_id,
    )


async def test_a_registry_that_never_answered_does_not_empty_the_catalog():
    """The test above protects a node list it already had. This one is the gap.

    On the first cycle after a restart there is no previous list, so a failed
    fetch leaves `_nodes` empty — and everything downstream then runs correctly
    over zero nodes, finds zero models, and replaces the catalog with nothing.
    RM-99 had restored a good snapshot into that same registry seconds earlier.

    Measured live: a gateway with a wrong AUTH_SERVICE_ADMIN_API_KEY came up
    healthy, served its 10 snapshot models, and then served zero, with
    `manager_sync.refreshed count=0` as the only trace.
    """
    registry = ModelRegistry.__new__(ModelRegistry)
    registry._models = {"from-snapshot": _snapshot_entry("from-snapshot")}
    sync = _sync(registry)

    with respx.mock:
        respx.get(f"{AUTH_ADMIN_URL}/nodes").mock(side_effect=ConnectionError("down"))
        await sync._sync()

    assert "from-snapshot" in registry._models, (
        "a node registry that could not be asked was read as a registry with no nodes"
    )


async def test_a_registry_that_answers_with_no_nodes_does_empty_the_catalog():
    """The other half, and the reason this is a flag and not a length check.

    Zero nodes registered is a legitimate answer that means zero models. If the
    fix refused to sync on an empty `_nodes` regardless of why, removing the
    last node would leave its models served forever.
    """
    registry = ModelRegistry.__new__(ModelRegistry)
    registry._models = {"stale": _snapshot_entry("stale")}
    sync = _sync(registry)

    with respx.mock:
        _mock_nodes()  # 200 OK, an empty list
        await sync._sync()

    assert registry._models == {}, "an empty node registry must still empty the catalog"
