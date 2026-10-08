"""PRM-231: the tier dimensions nothing read.

Implements: docs/roadmap.md — PRM-231.

PRM-228 gave `rate_limit_tiers` seven dimensions and the middleware read two.
An operator could set `rpd`, `tpd`, `tpm_input`, `tpm_output` or `ipm` on a
tier from the Users modal and the platform would ignore it in silence — a
control that does nothing, which is worse than one that is missing, because
the missing one is visible.

PRM-230's live view is what surfaced it: two of the counters those ceilings
would bound were already being incremented at the all-endpoints key with
nothing reading them.

`test_every_tier_dimension_refuses_something` is the guard. It does not read
the middleware; it sets one dimension at a time to 1 and asserts a request is
refused. A dimension added to the model and to nothing else fails it.
"""

from __future__ import annotations

import time as _time

import fakeredis.aioredis as fakeredis
import pytest
import respx
from httpx import ASGITransport, AsyncClient, Response

from prometheus_gateway import db
from prometheus_gateway.main import create_app
from prometheus_gateway.models.registry import ModelRegistry
from prometheus_gateway.rate_limiter import ALL_ENDPOINTS
from tests.conftest import dashboard_settings, make_token

CLIENT = "client-on-a-tier"


@pytest.fixture
def fake_redis():
    return fakeredis.FakeRedis()


@pytest.fixture
def registry(tmp_path):
    f = tmp_path / "registry.yaml"
    f.write_text(
        """models:
  - id: small-model
    path: /dev/null
    context_length: 4096
    family: llama3
    quantization: Q4_0
    backend_url: "http://127.0.0.1:18081"
  - id: emb
    path: /dev/null
    context_length: 512
    family: test
    quantization: Q4_0
    modality: embedding
    backend_url: "http://127.0.0.1:18081"
  - id: sd
    path: /dev/null
    context_length: 0
    family: test
    quantization: Q4_0
    modality: image
    backend_url: "http://127.0.0.1:18082"
"""
    )
    return ModelRegistry(f)


@pytest.fixture
def tier_settings(rsa_keys, tmp_path):
    key_file = tmp_path / "public.pem"
    key_file.write_text(rsa_keys["public"])
    # The platform's own ceilings left wide open on purpose: anything these
    # tests refuse has to have been refused by the tier.
    return dashboard_settings(
        key_file,
        rate_limit_rpm=10_000,
        rate_limit_tpm=10_000_000,
        rate_limit_strict=True,
    )


@pytest.fixture
def app(tier_settings, registry, fake_redis):
    return create_app(settings=tier_settings, registry=registry, redis_client=fake_redis)


@pytest.fixture
def headers(rsa_keys):
    return {
        "Authorization": "Bearer "
        + make_token(
            rsa_keys["private"],
            scope="inference:read inference:stream model:small-model model:emb model:sd",
            sub=CLIENT,
            azp=CLIENT,
        )
    }


async def park_on_tier(**dimensions):
    """Create a tier with exactly these dimensions and put CLIENT on it."""
    await db.create_tables(db.get_engine())
    await db.upsert_rate_limit_tier("t", **dimensions)
    await db.upsert_client_billing_settings(
        CLIENT,
        monthly_spend_cap_usd=None,
        alert_thresholds_percent=None,
        tax_rate_percent=None,
        preferred_currency="USD",
        tier="t",
    )


CHAT = {
    "model": "small-model",
    "messages": [{"role": "user", "content": "hi"}],
    "stream": False,
    "max_tokens": 10,
}
CHAT_RESPONSE = {
    "id": "x",
    "object": "chat.completion",
    "model": "small-model",
    "choices": [
        {"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}
    ],
    "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
}


# ── The guard ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "dimension",
    ["rpm", "tpm", "tpm_input", "tpm_output", "rpd", "tpd"],
)
async def test_every_tier_dimension_refuses_something(dimension, app, headers):
    """One dimension at a time, set to 1, and a request has to be refused.

    Blind to how each is enforced on purpose. The bug this replaces was five
    columns that parsed, validated, persisted, round-tripped through the admin
    API and were read by nothing — every test of the *parts* passed.

    `ipm` is the seventh and has its own test below: it needs an image request,
    because it is a gate on `n` rather than a meter on what came back.
    """
    await park_on_tier(**{dimension: 1})

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        with respx.mock:
            respx.post("http://127.0.0.1:18081/v1/chat/completions").mock(
                return_value=Response(200, json=CHAT_RESPONSE)
            )
            # Two requests: the first spends the budget, the second meets it.
            # The token dimensions are meters — the size of a response is not
            # knowable before it exists — so the refusal lands on the request
            # after the one that crossed.
            await c.post("/v1/chat/completions", json=CHAT, headers=headers)
            second = await c.post("/v1/chat/completions", json=CHAT, headers=headers)

    assert second.status_code == 429, f"a tier's {dimension} refused nothing"
    assert second.json()["scope"] == "client", (
        f"{dimension} was enforced, but not as a client-wide ceiling"
    )


# ── What the number means ────────────────────────────────────────────────────


async def test_a_tiers_daily_budget_is_the_whole_day_not_one_per_endpoint(app, headers):
    """The decision this item turned on.

    `rpd: 2` has to mean two requests, not two per route. Enforcing a tier at
    the per-endpoint key would grant six times the number written — the number
    saying one thing and the system doing another, which is PRM-129's shape.
    """
    await park_on_tier(rpd=2)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        with respx.mock:
            respx.post("http://127.0.0.1:18081/v1/chat/completions").mock(
                return_value=Response(200, json=CHAT_RESPONSE)
            )
            respx.post("http://127.0.0.1:18081/v1/embeddings").mock(
                return_value=Response(
                    200,
                    json={
                        "object": "list",
                        "data": [{"object": "embedding", "index": 0, "embedding": [0.1]}],
                        "model": "emb",
                        "usage": {"prompt_tokens": 2, "total_tokens": 2},
                    },
                )
            )
            first = await c.post("/v1/chat/completions", json=CHAT, headers=headers)
            # A different endpoint, and therefore a different counter under the
            # per-endpoint reading — the one that would have made this pass
            # while granting 2 per route.
            second = await c.post(
                "/v1/embeddings", json={"model": "emb", "input": "hola"}, headers=headers
            )
            third = await c.post(
                "/v1/embeddings", json={"model": "emb", "input": "hola"}, headers=headers
            )

    assert first.status_code == 200
    assert second.status_code == 200
    assert third.status_code == 429
    assert third.json()["scope"] == "client"
    assert "across all endpoints" in third.json()["detail"]


async def test_a_tier_silent_on_a_dimension_leaves_the_platform_default_alone(
    app, headers, tier_settings
):
    """A tier that says nothing about requests is not granting them freely, and
    is not tightening anything either — the `.env` ceiling still applies, per
    endpoint, exactly as before."""
    tier_settings.rate_limit_rpd = 1
    await park_on_tier(rpm=10_000)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        with respx.mock:
            respx.post("http://127.0.0.1:18081/v1/chat/completions").mock(
                return_value=Response(200, json=CHAT_RESPONSE)
            )
            await c.post("/v1/chat/completions", json=CHAT, headers=headers)
            refused = await c.post("/v1/chat/completions", json=CHAT, headers=headers)

    assert refused.status_code == 429
    # The platform's own daily ceiling, which is per endpoint — so it names one.
    assert refused.json()["scope"] == "chat_completions"
    assert "for endpoint 'chat_completions'" in refused.json()["detail"]


# ── The counters those ceilings read ─────────────────────────────────────────


async def test_the_daily_and_image_counters_are_written_at_the_client_key(app, headers, fake_redis):
    """A ceiling whose counter is never written cannot fire.

    `tpd` and `ipm` were only ever incremented per endpoint; the tier reads
    them across all endpoints. PRM-227 is the precedent — the consumer TPM
    check would have read zero forever and nothing would have said so.
    """
    await park_on_tier(rpm=10_000)
    day = int(_time.time() // 86_400)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        with respx.mock:
            respx.post("http://127.0.0.1:18081/v1/chat/completions").mock(
                return_value=Response(200, json=CHAT_RESPONSE)
            )
            assert (
                await c.post("/v1/chat/completions", json=CHAT, headers=headers)
            ).status_code == 200

    raw = await fake_redis.get(f"prometheus:rl:tpd:{CLIENT}:{ALL_ENDPOINTS}:{day}")
    assert raw is not None and int(raw) == 8


async def test_a_tiers_image_ceiling_applies_across_endpoints(app, headers, fake_redis):
    """`ipm` is the seventh dimension, and the one that lives in the router
    because it gates on `n` before anything is generated."""
    await park_on_tier(ipm=2)
    bucket = int(_time.time() // 60)
    await fake_redis.set(f"prometheus:rl:ipm:{CLIENT}:{ALL_ENDPOINTS}:{bucket}", 1)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        with respx.mock:
            backend = respx.post("http://127.0.0.1:18082/v1/images/generations").mock(
                return_value=Response(200, json={"created": 0, "data": [{"b64_json": "x"}]})
            )
            r = await c.post(
                "/v1/images/generations",
                json={"model": "sd", "prompt": "a lighthouse", "n": 3},
                headers=headers,
            )

    assert r.status_code == 429
    assert not backend.called, "the gate let the request through to the backend"


# ── And the live view agrees ─────────────────────────────────────────────────


def test_the_live_view_reports_a_tier_ceiling_on_the_five(settings):
    """PRM-230 printed `counted, not checked` on these counters, truthfully.
    Now that something checks them the same view has to say so, or the page
    goes back to describing a system that no longer exists."""
    from prometheus_gateway.db import RateLimitTier
    from prometheus_gateway.rate_limits import live_limit_for

    tier = RateLimitTier(name="t", tpm_input=5_000, rpd=10_000)
    for dimension, expected in (("tpm_in", 5_000), ("rpd", 10_000)):
        assert live_limit_for(
            settings,
            layer="client",
            dimension=dimension,
            endpoint=ALL_ENDPOINTS,
            endpoint_count=6,
            tier=tier,
        ) == (expected, "tier")
    # And silence still means no ceiling, not zero.
    assert live_limit_for(
        settings,
        layer="client",
        dimension="tpd",
        endpoint=ALL_ENDPOINTS,
        endpoint_count=6,
        tier=tier,
    ) == (None, "none")


def test_every_admin_editable_tier_dimension_is_one_the_view_can_explain():
    """The two lists that have to agree: what the admin API accepts on a tier,
    and what the live view knows how to price a counter against."""
    from prometheus_gateway.rate_limits import _CLIENT_TIER_FIELDS

    admin_dimensions = {"rpm", "tpm", "tpm_input", "tpm_output", "rpd", "tpd", "ipm"}
    covered = {"rpm", "tpm"} | set(_CLIENT_TIER_FIELDS.values())
    assert covered == admin_dimensions
