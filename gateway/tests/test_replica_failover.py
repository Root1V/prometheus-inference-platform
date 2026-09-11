"""RM-69 — replicas that actually buy fault tolerance.

RM-57 made several instances routable under one name, but the circuit breaker
was still consulted *after* a replica had been picked, retries re-hit the same
dead instance, and addressing a replica by its own id missed the price table.
These cover all four.
"""

from __future__ import annotations

import time
from types import SimpleNamespace

import httpx
import pytest
import respx
from httpx import ASGITransport, AsyncClient, Response

from prometheus_gateway.circuit_breaker import CircuitState
from prometheus_gateway.health_monitor import BackendHealthMonitor
from prometheus_gateway.models.registry import ModelEntry, ModelRegistry
from prometheus_gateway.router import _healthy_members
from tests.conftest import make_token

REPLICA_A_URL = "http://127.0.0.1:18081"
REPLICA_B_URL = "http://127.0.0.1:18082"

CHAT_RESPONSE = {
    "id": "chatcmpl-1",
    "object": "chat.completion",
    "created": 1,
    "model": "llama",
    "choices": [
        {"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}
    ],
    "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
}


def _entry(id: str, url: str | None, *, model_id: str = "llama") -> ModelEntry:
    return ModelEntry(
        id=id,
        path=f"/m/{id}.gguf",
        context_length=4096,
        family="llama",
        quantization="Q4_0",
        backend_url=url,
        backend_status="active" if url else "inactive",
        node="local",
        modality="text",
        model_id=model_id,
    )


class _StubBreaker:
    """Minimal CircuitBreaker stand-in. `probes` counts how often the
    probe-acquiring path was entered, which is the thing RM-69 must not waste.
    """

    def __init__(self, state: str, recovery_at: float | None = None, allow: bool = False) -> None:
        self._state = CircuitState(
            state=state, consecutive_failures=0, opened_at=None, recovery_at=recovery_at
        )
        self._allow = allow
        self.probes = 0

    async def get_state(self) -> CircuitState:
        return self._state

    async def allow_request(self) -> bool:
        self.probes += 1
        return self._allow


class _StubPool:
    def __init__(self, breakers: dict[str, _StubBreaker]) -> None:
        self._breakers = breakers

    def get_circuit_breaker(self, backend_id: str):
        return self._breakers.get(backend_id)


def _request(monitor: object = None):
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(health_monitor=monitor)))


# ── Finding A: one tripped replica must not take the model down ─────────────


async def test_open_circuit_on_one_replica_falls_through_to_the_healthy_one():
    members = [_entry("llama-a", REPLICA_A_URL), _entry("llama-b", REPLICA_B_URL)]
    pool = _StubPool(
        {
            "llama-a": _StubBreaker("open", recovery_at=time.time() + 30),
            "llama-b": _StubBreaker("closed"),
        }
    )

    health = await _healthy_members(pool, _request(), members)

    assert [m.id for m in health.usable] == ["llama-b"]
    assert "llama-a" in health.skipped


async def test_all_circuits_open_reports_every_replica_and_a_retry_after():
    recovery = time.time() + 30
    members = [_entry("llama-a", REPLICA_A_URL), _entry("llama-b", REPLICA_B_URL)]
    pool = _StubPool(
        {
            "llama-a": _StubBreaker("open", recovery_at=recovery + 60, allow=False),
            "llama-b": _StubBreaker("open", recovery_at=recovery, allow=False),
        }
    )

    health = await _healthy_members(pool, _request(), members)

    assert health.usable == []
    assert set(health.skipped) == {"llama-a", "llama-b"}
    # The soonest recovery, so Retry-After doesn't over-promise.
    assert health.soonest_recovery_at == recovery


async def test_a_healthy_sibling_does_not_burn_an_open_replicas_probe():
    """`allow_request()` takes a distributed probe lock held for the whole
    recovery timeout. Spending it on a replica we won't use would delay that
    replica's recovery purely because a sibling was healthy.
    """
    members = [_entry("llama-a", REPLICA_A_URL), _entry("llama-b", REPLICA_B_URL)]
    open_breaker = _StubBreaker("open", recovery_at=time.time() + 30)
    pool = _StubPool({"llama-a": _StubBreaker("closed"), "llama-b": open_breaker})

    health = await _healthy_members(pool, _request(), members)

    assert [m.id for m in health.usable] == ["llama-a"]
    assert open_breaker.probes == 0


async def test_an_unreadable_breaker_does_not_make_a_backend_unroutable():
    class _Broken(_StubBreaker):
        async def get_state(self):
            raise RuntimeError("redis down")

    members = [_entry("llama-a", REPLICA_A_URL)]
    health = await _healthy_members(_StubPool({"llama-a": _Broken("closed")}), _request(), members)

    assert [m.id for m in health.usable] == ["llama-a"]


async def test_a_backend_the_monitor_reports_unreachable_is_skipped():
    members = [_entry("llama-a", REPLICA_A_URL), _entry("llama-b", REPLICA_B_URL)]
    monitor = BackendHealthMonitor(registry=None)
    monitor._unreachable = {REPLICA_A_URL}

    health = await _healthy_members(_StubPool({}), _request(monitor), members)

    assert [m.id for m in health.usable] == ["llama-b"]
    assert health.skipped == {"llama-a": "unreachable"}


# ── Finding C: liveness means "answered", not "answered 200" ────────────────


@respx.mock
async def test_a_backend_without_a_health_route_counts_as_alive():
    """sd.cpp serves image generation but has no /health and replies 404
    (verified against a running sd-server). Treating that as dead would take
    image generation down entirely.
    """
    respx.get(f"{REPLICA_A_URL}/health").mock(return_value=Response(404))
    registry = ModelRegistry.__new__(ModelRegistry)
    registry._models = {"sd": _entry("sd", REPLICA_A_URL)}

    monitor = BackendHealthMonitor(registry=registry)
    await monitor.probe_once()

    assert monitor.unreachable(REPLICA_A_URL) is False
    await monitor.stop()


@respx.mock
async def test_an_unreachable_backend_is_marked_and_then_recovers():
    route = respx.get(f"{REPLICA_A_URL}/health")
    route.mock(side_effect=httpx.ConnectError("refused"))
    registry = ModelRegistry.__new__(ModelRegistry)
    registry._models = {"llama-a": _entry("llama-a", REPLICA_A_URL)}
    monitor = BackendHealthMonitor(registry=registry)

    await monitor.probe_once()
    assert monitor.unreachable(REPLICA_A_URL) is True

    route.mock(return_value=Response(200))
    await monitor.probe_once()
    assert monitor.unreachable(REPLICA_A_URL) is False
    await monitor.stop()


# ── Findings B and D, end to end ────────────────────────────────────────────


@pytest.fixture
def replica_app(settings):
    from prometheus_gateway.main import create_app

    registry = ModelRegistry.__new__(ModelRegistry)
    registry._models = {
        "llama-a": _entry("llama-a", REPLICA_A_URL),
        "llama-b": _entry("llama-b", REPLICA_B_URL),
    }
    return create_app(settings=settings, registry=registry)


@pytest.fixture
async def gw(replica_app):
    async with AsyncClient(
        transport=ASGITransport(app=replica_app), base_url="http://test"
    ) as client:
        yield client


def _headers(rsa_keys, scope: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {make_token(rsa_keys['private'], scope=scope)}"}


@respx.mock
async def test_a_dead_replica_fails_over_instead_of_503ing(gw, rsa_keys):
    """Finding B: retries used to re-hit the same dead instance, so all three
    attempts went nowhere and the client got a 503 with a healthy replica idle.
    """
    dead = respx.post(f"{REPLICA_A_URL}/v1/chat/completions").mock(
        side_effect=httpx.ConnectError("refused")
    )
    alive = respx.post(f"{REPLICA_B_URL}/v1/chat/completions").mock(
        return_value=Response(200, json=CHAT_RESPONSE)
    )

    resp = await gw.post(
        "/v1/chat/completions",
        json={"model": "llama", "messages": [{"role": "user", "content": "hi"}], "max_tokens": 5},
        headers=_headers(rsa_keys, "inference:read model:llama"),
    )

    assert resp.status_code == 200
    assert dead.called
    assert alive.called


def test_addressing_a_replica_directly_still_bills_the_group():
    """Finding D: pricing is a flat dict keyed by the exact string. Resolving a
    replica by its own instance id used to key off that id, which has no price
    — recording the request at NULL cost and skipping the budget reservation,
    so the same model was free and uncapped under one of its names.
    """
    registry = ModelRegistry.__new__(ModelRegistry)
    registry._models = {
        "llama-a": _entry("llama-a", REPLICA_A_URL),
        "llama-b": _entry("llama-b", REPLICA_B_URL),
    }

    by_group = registry.resolve("llama")
    by_instance = registry.resolve("llama-b")

    assert by_group is not None and by_instance is not None
    assert by_instance.name == "llama-b"  # routing still honours the instance
    assert by_instance.model_key == by_group.model_key == "llama"


def test_a_stopped_model_still_reports_a_billing_key():
    registry = ModelRegistry.__new__(ModelRegistry)
    registry._models = {"llama-a": _entry("llama-a", None)}

    resolved = registry.resolve("llama")

    assert resolved is not None
    assert resolved.members == ()
    assert resolved.model_key == "llama"
