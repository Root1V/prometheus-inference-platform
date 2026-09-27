"""Admission control — PRM-158.

The architecture review called this the most serious of the three gaps that decide
whether the platform competes, because it is the one that shows up under real
load, which is exactly when a product is evaluated. A burst arrived in full, the
rate limiter counted requests without knowing how full the engine was, and a
saturated GPU turned latency into timeouts **for everybody** rather than a clean
refusal for the last arrivals.

Two decisions carry this, and neither is a queue.

**It bounds, it does not queue.** The engine already has one — llama.cpp has slots
and a pending list, vLLM has `max_num_seqs` — so a gateway queue would move the
wait and give it a second place to be accounted for. What was missing is a limit
on how deep we let the engine's queue grow, which is what Envoy's
`max_pending_requests` is.

**Saturation is a reason a replica cannot take a request**, so it lives beside
"unreachable" and "circuit open" in `_healthy_members` rather than as a sixth
check on each of the five forwarding handlers. A saturated replica with an idle
sibling means the request goes to the sibling; the 503 happens only when every
replica is full, and it says which.
"""

from __future__ import annotations

import pytest
import respx
from httpx import ASGITransport, AsyncClient, Response

from prometheus_gateway.models.backends import BackendPool
from prometheus_gateway.models.registry import ModelEntry, ModelRegistry
from tests.conftest import make_token

pytestmark = pytest.mark.asyncio

BUSY_URL = "http://127.0.0.1:18301"
IDLE_URL = "http://127.0.0.1:18302"

LLAMA_RESPONSE = {
    "id": "chatcmpl-1",
    "object": "chat.completion",
    "model": "m",
    "choices": [
        {"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}
    ],
    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
}


def _entry(entry_id: str, url: str) -> ModelEntry:
    return ModelEntry(
        id=entry_id,
        path=f"/m/{entry_id}",
        context_length=4096,
        family="qwen",
        quantization="Q4",
        backend_url=url,
        backend_status="active",
        modality="text",
        model_id="m",
        model_slug="m",
    )


# ── The arithmetic: a multiple of what the backend says it can take ──────────


async def test_capacity_that_is_not_reported_is_never_refused_against():
    """sd.cpp reports no slots. There is no number to bound against, and inventing
    one would refuse real traffic on a guess."""
    pool = BackendPool()
    pool.set_reported_busy("sd", 99)
    assert pool.saturated("sd", headroom=2.0) is False


async def test_a_backend_below_its_headroom_is_admitted():
    pool = BackendPool()
    pool.set_slot_capacity("b", 4)
    pool.set_reported_busy("b", 4)  # every slot working, nothing queued yet
    assert pool.saturated("b", headroom=2.0) is False


async def test_a_backend_at_its_headroom_is_refused():
    pool = BackendPool()
    pool.set_slot_capacity("b", 4)
    pool.set_reported_busy("b", 8)  # a full queue behind a full engine
    assert pool.saturated("b", headroom=2.0) is True


async def test_the_signal_is_the_same_one_least_loaded_routing_uses():
    """PRM-156's numerator: the larger of what this process sent and what the
    backend reports. Admission control must not use a *different* view of load
    from the one selection uses, or the two would disagree about which backend is
    full."""
    pool = BackendPool()
    pool.set_slot_capacity("b", 2)
    pool.set_reported_busy("b", 0)
    for _ in range(4):
        pool.acquire("b")
    assert pool.load_ratio("b") == 2.0
    assert pool.saturated("b", headroom=2.0) is True


async def test_a_headroom_of_zero_disables_it():
    """The default, and deliberately so: turning a limit on by default would start
    refusing traffic on an existing deployment the first time it restarted, on a
    number nobody chose."""
    pool = BackendPool()
    pool.set_slot_capacity("b", 1)
    pool.set_reported_busy("b", 500)
    assert pool.saturated("b", headroom=0.0) is False


# ── An idle sibling beats a saturated one, which is why replicas exist ────────


@pytest.fixture
def app_with(request):
    """An app whose registry holds the members the test asks for."""

    def _build(settings, *members: ModelEntry):
        from prometheus_gateway.main import create_app

        registry = ModelRegistry.__new__(ModelRegistry)
        registry._models = {m.id: m for m in members}
        return create_app(settings=settings, registry=registry)

    return _build


@pytest.fixture
def settings_with_admission(rsa_keys, tmp_path):
    from prometheus_gateway.config import Settings

    key_file = tmp_path / "public.pem"
    key_file.write_text(rsa_keys["public"])
    return Settings(
        jwt_issuer="https://auth.test",
        jwt_audience="prometheus-gateway",
        jwt_public_key_file=str(key_file),
        jwt_revocation_redis_url=None,
        rate_limit_strict=False,
        admission_headroom=2.0,
    )


def _headers(rsa_keys) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {make_token(rsa_keys['private'], scope='inference:read model:m')}"
    }


@respx.mock
async def test_a_saturated_replica_is_skipped_for_an_idle_one(
    app_with, settings_with_admission, rsa_keys
):
    """The behaviour that makes this a routing decision rather than a refusal."""
    from prometheus_gateway import db

    app = app_with(settings_with_admission, _entry("busy", BUSY_URL), _entry("idle", IDLE_URL))
    await db.create_tables(db.get_engine())

    pool = app.state.backend_pool
    pool.set_slot_capacity("busy", 2)
    pool.set_reported_busy("busy", 4)  # at headroom
    pool.set_slot_capacity("idle", 2)
    pool.set_reported_busy("idle", 0)

    respx.post(f"{IDLE_URL}/v1/chat/completions").mock(
        return_value=Response(200, json=LLAMA_RESPONSE)
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.post(
            "/v1/chat/completions",
            json={"model": "m", "messages": [{"role": "user", "content": "hi"}]},
            headers=_headers(rsa_keys),
        )
    assert resp.status_code == 200, resp.text


@respx.mock
async def test_every_replica_saturated_is_a_capacity_refusal_not_a_broken_backend(
    app_with, settings_with_admission, rsa_keys
):
    """The type matters. "Every replica is down" and "every replica is busy" need
    different actions — page somebody, versus back off — so they cannot share a
    type. `backend-unavailable` already carries two causes that only a Retry-After
    distinguishes; saturation as a third would leave a client unable to tell a
    broken model from a busy one.
    """
    from prometheus_gateway import db

    app = app_with(settings_with_admission, _entry("busy", BUSY_URL), _entry("also", IDLE_URL))
    await db.create_tables(db.get_engine())

    pool = app.state.backend_pool
    for backend_id in ("busy", "also"):
        pool.set_slot_capacity(backend_id, 2)
        pool.set_reported_busy(backend_id, 4)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.post(
            "/v1/chat/completions",
            json={"model": "m", "messages": [{"role": "user", "content": "hi"}]},
            headers=_headers(rsa_keys),
        )

    assert resp.status_code == 503
    body = resp.json()
    assert body["type"].endswith("/capacity-exhausted")
    assert "at capacity" in body["detail"]
    # Both replicas named, because "capacity exhausted" on a model with two tells
    # an operator nothing about which to add to.
    assert "busy" in body["detail"] and "also" in body["detail"]
    # A retry hint, and short: a slot frees when a request finishes.
    assert resp.headers.get("Retry-After") == "1"


@respx.mock
async def test_with_admission_off_a_saturated_backend_is_still_used(app_with, rsa_keys, tmp_path):
    """The default path. A deployment that never opted in must behave exactly as
    it did before, however loaded its backends are."""
    from prometheus_gateway import db
    from prometheus_gateway.config import Settings

    key_file = tmp_path / "public.pem"
    key_file.write_text(rsa_keys["public"])
    settings = Settings(
        jwt_issuer="https://auth.test",
        jwt_audience="prometheus-gateway",
        jwt_public_key_file=str(key_file),
        jwt_revocation_redis_url=None,
        rate_limit_strict=False,
        # admission_headroom left at its default
    )
    assert settings.admission_headroom == 0.0

    app = app_with(settings, _entry("busy", BUSY_URL))
    await db.create_tables(db.get_engine())
    pool = app.state.backend_pool
    pool.set_slot_capacity("busy", 1)
    pool.set_reported_busy("busy", 100)

    respx.post(f"{BUSY_URL}/v1/chat/completions").mock(
        return_value=Response(200, json=LLAMA_RESPONSE)
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.post(
            "/v1/chat/completions",
            json={"model": "m", "messages": [{"role": "user", "content": "hi"}]},
            headers=_headers(rsa_keys),
        )
    assert resp.status_code == 200, "admission control changed behaviour while disabled"
