"""A refused streamed request is not a 200 — PRM-143.

`StreamingResponse` fixes its status when it is constructed, and the gateway
used to open the connection to the engine *inside* the generator, which does not
run until the response is consumed. So the status was not merely checked late —
it was never read at all. A backend that refused the request answered with
something that is not SSE, no line started with `data:`, nothing was forwarded,
and the caller received a `200` whose body was the terminal frame alone: a
refusal delivered as an empty success.

That made the streamed path the sixth instance of PRM-142 and the only one that a
status guard could not fix, because the fix is where the connection is opened.
These tests hold the new ordering in place — the status is known before anything
is returned — and the last one holds the healthy path unchanged, which is the
half a restructure is most likely to break.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest
import respx
from httpx import ASGITransport, AsyncClient, Response

from prometheus_gateway import db, idempotency
from prometheus_gateway.models.registry import ModelEntry, ModelRegistry
from tests.conftest import make_token

pytestmark = pytest.mark.asyncio

BACKEND_URL = "http://127.0.0.1:18091"

_SSE = {"Content-Type": "text/event-stream"}
_A_WHOLE_STREAM = (
    'data: {"choices":[{"delta":{"content":"hi"}}],'
    '"timings":{"prompt_n":3,"predicted_n":1}}\n\ndata: [DONE]\n\n'
)


@pytest.fixture
def app(settings):
    from prometheus_gateway.main import create_app

    registry = ModelRegistry.__new__(ModelRegistry)
    registry._models = {
        "solo": ModelEntry(
            id="solo",
            path="/m/solo.gguf",
            context_length=4096,
            family="test",
            quantization="Q4_0",
            backend_url=BACKEND_URL,
            backend_status="active",
            node="local",
            modality="text",
            model_id="solo",
            model_slug="solo",
        )
    }
    return create_app(settings=settings, registry=registry)


@pytest.fixture
async def gw(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await _drain_detached()
        await db.create_tables(db.get_engine())
        yield client
        await _drain_detached()


async def _drain_detached() -> None:
    from prometheus_gateway import router

    while router._detached:
        await asyncio.gather(*list(router._detached), return_exceptions=True)


def _headers(rsa_keys, key: str | None = None) -> dict[str, str]:
    h = {
        "Authorization": (
            f"Bearer {make_token(rsa_keys['private'], scope='inference:stream model:solo')}"
        )
    }
    if key:
        h[idempotency.HEADER] = key
    return h


def _chat(**overrides):
    return {
        "model": "solo",
        "messages": [{"role": "user", "content": "hi"}],
        "max_tokens": 5,
        "stream": True,
        **overrides,
    }


async def _rows_today():
    from datetime import datetime, timezone

    utc_today = datetime.now(timezone.utc).date()
    return await db.query_usage_events_range(utc_today, utc_today)


@respx.mock
async def test_a_refused_stream_answers_with_the_backends_status(gw, rsa_keys):
    """The defect itself. A 400 from the engine used to arrive as a 200."""
    respx.post(f"{BACKEND_URL}/v1/chat/completions").mock(
        return_value=Response(400, json={"error": {"message": "context length exceeded"}})
    )

    resp = await gw.post("/v1/chat/completions", json=_chat(), headers=_headers(rsa_keys))

    assert resp.status_code == 400
    # And it is an error the caller can read, not an SSE body it has to parse to
    # discover there is nothing in it.
    assert "event-stream" not in resp.headers.get("content-type", "")
    assert resp.json()["error"]["message"] == "context length exceeded"


@respx.mock
async def test_a_refused_stream_is_not_billed(gw, rsa_keys):
    """PRM-142's rule, on the route it could not reach: the model produced
    nothing, so there is nothing to charge and no row to explain later."""
    respx.post(f"{BACKEND_URL}/v1/chat/completions").mock(
        return_value=Response(422, json={"error": {"message": "no"}})
    )

    await gw.post("/v1/chat/completions", json=_chat(), headers=_headers(rsa_keys))

    await _drain_detached()
    assert await _rows_today() == []


@respx.mock
async def test_a_refused_stream_does_not_hold_a_backend_slot(app, gw, rsa_keys):
    """RM-72 claims a slot for as long as a stream generates. A stream that never
    starts must not claim one — an engine rejecting every request would otherwise
    read as the busiest replica in the fleet and be routed away from, which is
    the opposite of what least-loaded routing is for."""
    respx.post(f"{BACKEND_URL}/v1/chat/completions").mock(
        return_value=Response(400, json={"error": {"message": "no"}})
    )

    await gw.post("/v1/chat/completions", json=_chat(), headers=_headers(rsa_keys))

    assert app.state.backend_pool.in_flight("solo") == 0


@respx.mock
async def test_a_refused_stream_hands_its_idempotency_key_back(gw, rsa_keys):
    """RM-82 takes the claim off the request precisely so the generator settles
    it — and the generator is what no longer runs here. A refusal that kept the
    key would answer every retry of it from a record of nothing, for 24 hours."""
    route = respx.post(f"{BACKEND_URL}/v1/chat/completions").mock(
        return_value=Response(400, json={"error": {"message": "no"}})
    )

    first = await gw.post("/v1/chat/completions", json=_chat(), headers=_headers(rsa_keys, "k143"))
    second = await gw.post("/v1/chat/completions", json=_chat(), headers=_headers(rsa_keys, "k143"))

    assert first.status_code == second.status_code == 400
    assert route.call_count == 2, "the second call was answered from a stored refusal"
    assert "Idempotent-Replay" not in second.headers


@respx.mock
async def test_an_unreachable_engine_is_a_503_not_a_200(gw, rsa_keys):
    """The other half. A connection that never opened used to land in the
    generator's own `except` and be delivered as `{"error": "stream
    interrupted"}` inside a 200 — an SDK reading the status saw success."""
    respx.post(f"{BACKEND_URL}/v1/chat/completions").mock(side_effect=httpx.ConnectError("refused"))

    resp = await gw.post("/v1/chat/completions", json=_chat(), headers=_headers(rsa_keys))

    assert resp.status_code == 503
    assert resp.json()["type"].endswith("/backend-unavailable")

    await _drain_detached()
    assert await _rows_today() == []


@respx.mock
async def test_a_healthy_stream_is_unchanged(gw, rsa_keys):
    """The half a restructure breaks. The response still streams, still ends with
    its own terminal frame, and still produces exactly one usage row."""
    respx.post(f"{BACKEND_URL}/v1/chat/completions").mock(
        return_value=Response(200, text=_A_WHOLE_STREAM, headers=_SSE)
    )

    resp = await gw.post("/v1/chat/completions", json=_chat(), headers=_headers(rsa_keys))

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    assert resp.text.endswith("data: [DONE]\n\n")

    await _drain_detached()
    rows = await _rows_today()
    assert len(rows) == 1
    assert rows[0].termination_reason == db.TERMINATION_COMPLETE
