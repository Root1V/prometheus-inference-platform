"""PRM-245: how long it took, kept on the usage row.

Implements: docs/roadmap.md — PRM-245.

The router has computed `duration_s` and `ttft_s` since PRM-131 and handed
them to the GenAI metrics; the row kept neither. So latency lived only in the
gateway's process memory, a restart erased it, and the dashboard reported
`p95 17,351 ms · p99 17,351 ms` — identical, because they came from five
requests since the last restart. Two numbers that are always equal is a
distribution with one point in it.
"""

from __future__ import annotations

import datetime as _dt

import fakeredis.aioredis as fakeredis
import pytest
import respx
from httpx import ASGITransport, AsyncClient, Response

from prometheus_gateway import db
from prometheus_gateway.main import create_app
from prometheus_gateway.models.registry import ModelRegistry
from tests.conftest import dashboard_settings, make_token

CLIENT = "timed-client"


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
"""
    )
    return ModelRegistry(f)


@pytest.fixture
def app(rsa_keys, tmp_path, registry, fake_redis):
    key_file = tmp_path / "public.pem"
    key_file.write_text(rsa_keys["public"])
    return create_app(
        settings=dashboard_settings(key_file), registry=registry, redis_client=fake_redis
    )


@pytest.fixture
def caller(rsa_keys):
    return {
        "Authorization": "Bearer "
        + make_token(
            rsa_keys["private"],
            scope="inference:read inference:stream model:small-model",
            sub=CLIENT,
            azp=CLIENT,
        )
    }


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


async def test_a_billed_request_records_how_long_it_took(app, caller):
    """The number existed and was thrown away at the one place that persists."""
    await db.create_tables(db.get_engine())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        with respx.mock:
            respx.post("http://127.0.0.1:18081/v1/chat/completions").mock(
                return_value=Response(200, json=CHAT_RESPONSE)
            )
            assert (
                await c.post("/v1/chat/completions", json=CHAT, headers=caller)
            ).status_code == 200

    async with db.get_session_factory()() as session:
        from sqlalchemy import select

        row = (
            await session.execute(select(db.UsageEvent).where(db.UsageEvent.client_id == CLIENT))
        ).scalar_one()
    assert row.duration_ms is not None
    assert row.duration_ms >= 0


async def test_an_untimed_row_is_skipped_not_counted_as_zero():
    """A request nobody timed is not a fast request. Averaging it in is how a
    p95 improves the more instrumentation is missing."""
    await db.create_tables(db.get_engine())
    today = _dt.datetime.now(_dt.timezone.utc).date()
    for ms in (100, 200, 300, None):
        await db.record_usage("c", "m", 1, 1, model_slug="m", duration_ms=ms, request_id=f"r{ms}")

    rows = await db.query_latency_by_model(today, today)
    row = next(r for r in rows if r["model"] == "m")
    assert row["count"] == 3, "the untimed row was counted"
    assert row["p50_ms"] in (100, 200)
    assert row["p99_ms"] == 300


async def test_the_percentile_is_a_duration_that_actually_happened():
    """Nearest-rank, not interpolated: an interpolated p95 is a number no
    request produced, which is a bad thing to put beside a model's name."""
    await db.create_tables(db.get_engine())
    today = _dt.datetime.now(_dt.timezone.utc).date()
    for i, ms in enumerate([10, 20, 30, 40, 1000]):
        await db.record_usage("c2", "mm", 1, 1, model_slug="mm", duration_ms=ms, request_id=f"p{i}")

    row = next(r for r in await db.query_latency_by_model(today, today) if r["model"] == "mm")
    assert row["p95_ms"] in (10, 20, 30, 40, 1000)
    assert row["p99_ms"] == 1000, "the outlier must be visible at p99"
