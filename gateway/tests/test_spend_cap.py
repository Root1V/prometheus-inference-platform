"""End-to-end tests for RM-60 — hard per-client monthly spend cap.

Mirrors test_rate_limiting.py's fixture pattern (fake_redis, small_registry,
rl_settings-style Settings, respx-mocked backend).
"""

from __future__ import annotations

from datetime import date

import fakeredis.aioredis as fakeredis
import pytest
import respx
from httpx import ASGITransport, AsyncClient, Response

from prometheus_gateway import db
from prometheus_gateway.config import Settings
from prometheus_gateway.main import create_app
from prometheus_gateway.models.registry import ModelRegistry
from tests.conftest import make_token

VALID_BODY = {
    "model": "small-model",
    "messages": [{"role": "user", "content": "hi"}],
    "stream": False,
    "max_tokens": 10,
}

LLAMA_RESPONSE = {
    "id": "test-id",
    "object": "chat.completion",
    "model": "small-model",
    "choices": [
        {"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}
    ],
    "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
}


@pytest.fixture
def fake_redis():
    return fakeredis.FakeRedis()


@pytest.fixture
def small_registry(tmp_path):
    yaml_content = """models:
  - id: small-model
    path: /dev/null
    context_length: 4096
    family: llama3
    quantization: Q4_0
    backend_url: "http://127.0.0.1:18081"
"""
    f = tmp_path / "registry.yaml"
    f.write_text(yaml_content)
    return ModelRegistry(f)


@pytest.fixture
def priced_settings(rsa_keys, tmp_path):
    key_file = tmp_path / "public.pem"
    key_file.write_text(rsa_keys["public"])
    pricing_file = tmp_path / "pricing.yaml"
    # $1 / 1M prompt tokens, $1 / 1M completion tokens
    pricing_file.write_text(
        "models:\n  - id: small-model\n    prompt_price_per_1m: 1.0\n"
        "    completion_price_per_1m: 1.0\n"
    )
    return Settings(
        jwt_issuer="https://auth.test",
        jwt_audience="prometheus-gateway",
        jwt_public_key_file=str(key_file),
        jwt_revocation_redis_url=None,
        rate_limit_strict=False,
        pricing_file=str(pricing_file),
    )


@pytest.fixture
def app(priced_settings, small_registry, fake_redis):
    return create_app(settings=priced_settings, registry=small_registry, redis_client=fake_redis)


@pytest.fixture
def auth_headers(rsa_keys):
    token = make_token(
        rsa_keys["private"],
        scope="inference:read inference:stream model:small-model",
        sub="user-x",
        azp="client-a",
    )
    return {"Authorization": f"Bearer {token}"}


async def _set_cap(cap_usd: float) -> None:
    await db.create_tables(db.get_engine())
    await db.upsert_client_billing_settings(
        "client-a",
        monthly_spend_cap_usd=cap_usd,
        alert_thresholds_percent=None,
        tax_rate_percent=0.0,
        preferred_currency="USD",
    )


async def test_request_allowed_when_under_cap(app, auth_headers):
    await _set_cap(1000.0)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        with respx.mock:
            respx.post("http://127.0.0.1:18081/v1/chat/completions").mock(
                return_value=Response(200, json=LLAMA_RESPONSE)
            )
            r = await c.post("/v1/chat/completions", json=VALID_BODY, headers=auth_headers)

    assert r.status_code == 200


async def test_request_denied_with_402_when_cap_exceeded(app, auth_headers):
    # Cap smaller than a single request's worst-case reservation.
    await _set_cap(0.000001)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        with respx.mock:
            respx.post("http://127.0.0.1:18081/v1/chat/completions").mock(
                return_value=Response(200, json=LLAMA_RESPONSE)
            )
            r = await c.post("/v1/chat/completions", json=VALID_BODY, headers=auth_headers)

    assert r.status_code == 402
    assert r.json()["type"].endswith("spend-cap-exceeded")


async def test_denied_request_never_recorded_as_usage(app, auth_headers):
    await _set_cap(0.000001)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        with respx.mock:
            respx.post("http://127.0.0.1:18081/v1/chat/completions").mock(
                return_value=Response(200, json=LLAMA_RESPONSE)
            )
            r = await c.post("/v1/chat/completions", json=VALID_BODY, headers=auth_headers)

    assert r.status_code == 402
    events = await db.query_usage_events_range(date(2000, 1, 1), date(2100, 1, 1))
    assert events == []


async def test_reservation_settles_down_to_real_cost(app, auth_headers, fake_redis):
    """The worst-case reservation (max_tokens=10) is settled down to the real
    completion_tokens=3 once the (mocked) backend responds.
    """
    from prometheus_gateway.budget import BudgetTracker

    await _set_cap(1000.0)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        with respx.mock:
            respx.post("http://127.0.0.1:18081/v1/chat/completions").mock(
                return_value=Response(200, json=LLAMA_RESPONSE)
            )
            r = await c.post("/v1/chat/completions", json=VALID_BODY, headers=auth_headers)

    assert r.status_code == 200
    tracker = BudgetTracker(fake_redis)
    spend = await tracker.get_spend("client-a")
    # prompt=5, completion=3, both @ $1/1M => (5+3)/1_000_000 = 0.000008
    assert spend == pytest.approx(0.000008, abs=1e-9)


async def test_streaming_request_also_enforces_cap(app, auth_headers):
    await _set_cap(0.000001)
    stream_body = {**VALID_BODY, "stream": True}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        with respx.mock:
            respx.post("http://127.0.0.1:18081/v1/chat/completions").mock(
                return_value=Response(200, json=LLAMA_RESPONSE)
            )
            r = await c.post("/v1/chat/completions", json=stream_body, headers=auth_headers)

    assert r.status_code == 402


async def test_unconfigured_client_has_no_cap(app, auth_headers):
    """No ClientBillingSettings row at all => no cap enforced (opt-in)."""
    await db.create_tables(db.get_engine())

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        with respx.mock:
            respx.post("http://127.0.0.1:18081/v1/chat/completions").mock(
                return_value=Response(200, json=LLAMA_RESPONSE)
            )
            r = await c.post("/v1/chat/completions", json=VALID_BODY, headers=auth_headers)

    assert r.status_code == 200


# ── PRM-118: the cap has to find the price of a model that was renamed ──────


@pytest.fixture
def renamed_registry():
    """A model whose public name is not its catalog id — RM-70's rename.

    Built in memory rather than from registry.yaml because the point is the
    gap between the two identifiers, and the yaml fixture above has only one.
    """
    from prometheus_gateway.models.registry import ModelEntry

    registry = ModelRegistry.__new__(ModelRegistry)
    registry._models = {
        "inst-1": ModelEntry(
            id="inst-1",
            path="/dev/null",
            context_length=4096,
            family="llama3",
            quantization="Q4_0",
            backend_url="http://127.0.0.1:18081",
            backend_status="active",
            node="local",
            modality="text",
            model_id="cat-id-0-6b-local-2",  # what prices are keyed on
            model_slug="pretty-name",  # what the client sends
        )
    }
    return registry


@pytest.fixture
def renamed_app(rsa_keys, tmp_path, renamed_registry, fake_redis):
    key_file = tmp_path / "public.pem"
    key_file.write_text(rsa_keys["public"])
    pricing_file = tmp_path / "pricing.yaml"
    # Priced under the CATALOG ID, which is how the dashboard writes prices.
    pricing_file.write_text(
        "models:\n  - id: cat-id-0-6b-local-2\n    prompt_price_per_1m: 1.0\n"
        "    completion_price_per_1m: 1.0\n"
    )
    settings = Settings(
        jwt_issuer="https://auth.test",
        jwt_audience="prometheus-gateway",
        jwt_public_key_file=str(key_file),
        jwt_revocation_redis_url=None,
        rate_limit_strict=False,
        pricing_file=str(pricing_file),
    )
    return create_app(settings=settings, registry=renamed_registry, redis_client=fake_redis)


@pytest.fixture
def renamed_headers(rsa_keys):
    token = make_token(
        rsa_keys["private"],
        scope="inference:read inference:stream model:pretty-name",
        sub="user-x",
        azp="client-a",
    )
    return {"Authorization": f"Bearer {token}"}


async def test_the_cap_engages_for_a_model_that_was_renamed(renamed_app, renamed_headers):
    """The defect PRM-118 fixes, and the reason it was invisible.

    The reserve resolved the price by the public name only, while prices are
    keyed on the catalog id. For any renamed model it therefore found no price,
    `est_cost` was None, no reservation was made — and the request sailed past
    a cap it should have hit. Nothing failed and nothing was logged: the model
    was simply free and uncapped, which is exactly what RM-69's own comment
    warned would happen if this key were ever wrong.

    Measured on the live deployment before the fix: qwen3-embedding and
    qwen3-vl-8b both reserved nothing.
    """
    await _set_cap(0.000001)

    body = {**VALID_BODY, "model": "pretty-name"}
    async with AsyncClient(transport=ASGITransport(app=renamed_app), base_url="http://test") as c:
        with respx.mock:
            respx.post("http://127.0.0.1:18081/v1/chat/completions").mock(
                return_value=Response(200, json=LLAMA_RESPONSE)
            )
            r = await c.post("/v1/chat/completions", json=body, headers=renamed_headers)

    assert r.status_code == 402, "a renamed model must be capped like any other"
    assert r.json()["type"].endswith("spend-cap-exceeded")


async def test_a_renamed_model_under_its_cap_still_bills(renamed_app, renamed_headers):
    """The other half: finding the price must not start denying legitimate
    traffic, and the row it writes must carry the cost the reserve used."""
    await _set_cap(1000.0)

    body = {**VALID_BODY, "model": "pretty-name"}
    async with AsyncClient(transport=ASGITransport(app=renamed_app), base_url="http://test") as c:
        with respx.mock:
            respx.post("http://127.0.0.1:18081/v1/chat/completions").mock(
                return_value=Response(200, json=LLAMA_RESPONSE)
            )
            r = await c.post("/v1/chat/completions", json=body, headers=renamed_headers)

    assert r.status_code == 200
    events = await db.query_usage_events_range(date(2000, 1, 1), date(2100, 1, 1))
    assert len(events) == 1
    # 5 prompt + 3 completion at $1/1M each.
    assert events[0].cost_usd == pytest.approx(8 / 1_000_000)
    assert events[0].model_id == "cat-id-0-6b-local-2"
    assert events[0].model_slug == "pretty-name"
