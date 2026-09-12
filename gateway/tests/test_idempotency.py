"""RM-78 — client-supplied idempotency keys.

A retry is otherwise always a new, billable generation, so an SDK can only
retry where the platform proves nothing ran — which excludes the commonest
case, retrying after its own timeout.

These cover the part that has to be right for billing: a replay must not reach
the model, must not record usage, and must never answer a different request.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import httpx
import pytest
import respx
from httpx import ASGITransport, AsyncClient, Response

from prometheus_gateway import db, idempotency
from prometheus_gateway.models.registry import ModelEntry, ModelRegistry
from tests.conftest import make_token

pytestmark = pytest.mark.asyncio

BACKEND_URL = "http://127.0.0.1:18090"

CHAT_RESPONSE = {
    "id": "chatcmpl-1",
    "object": "chat.completion",
    "created": 1,
    "model": "solo",
    "choices": [
        {"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}
    ],
    "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
}


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
        await db.create_tables(db.get_engine())
        yield client


def _headers(rsa_keys, key: str | None = None) -> dict[str, str]:
    h = {
        "Authorization": f"Bearer {make_token(rsa_keys['private'], scope='inference:read model:solo')}"
    }
    if key:
        h[idempotency.HEADER] = key
    return h


def _chat(**overrides):
    return {
        "model": "solo",
        "messages": [{"role": "user", "content": "hi"}],
        "max_tokens": 5,
        **overrides,
    }


@respx.mock
async def test_a_replay_never_reaches_the_model(gw, rsa_keys):
    """The whole point: the second call must not generate again."""
    route = respx.post(f"{BACKEND_URL}/v1/chat/completions").mock(
        return_value=Response(200, json=CHAT_RESPONSE)
    )

    first = await gw.post("/v1/chat/completions", json=_chat(), headers=_headers(rsa_keys, "k1"))
    second = await gw.post("/v1/chat/completions", json=_chat(), headers=_headers(rsa_keys, "k1"))

    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert route.call_count == 1
    assert second.headers["Idempotent-Replay"] == "true"
    assert "Idempotent-Replay" not in first.headers


@respx.mock
async def test_a_replay_is_not_billed_again(gw, rsa_keys):
    """A replay isn't a second use of the model, so it must not produce a
    second usage row — otherwise the key would prevent the duplicate
    generation but keep the duplicate charge."""
    respx.post(f"{BACKEND_URL}/v1/chat/completions").mock(
        return_value=Response(200, json=CHAT_RESPONSE)
    )

    await gw.post("/v1/chat/completions", json=_chat(), headers=_headers(rsa_keys, "k2"))
    await gw.post("/v1/chat/completions", json=_chat(), headers=_headers(rsa_keys, "k2"))

    events = await db.query_usage_events_range(date.today(), date.today())
    assert len(events) == 1


@respx.mock
async def test_without_a_key_nothing_is_deduplicated(gw, rsa_keys):
    """Opt-in: a client that sends no key keeps today's behaviour exactly."""
    route = respx.post(f"{BACKEND_URL}/v1/chat/completions").mock(
        return_value=Response(200, json=CHAT_RESPONSE)
    )

    await gw.post("/v1/chat/completions", json=_chat(), headers=_headers(rsa_keys))
    await gw.post("/v1/chat/completions", json=_chat(), headers=_headers(rsa_keys))

    assert route.call_count == 2


@respx.mock
async def test_reusing_a_key_for_a_different_request_is_refused(gw, rsa_keys):
    """Answering it with the first result would be a wrong answer, not a
    duplicate one."""
    respx.post(f"{BACKEND_URL}/v1/chat/completions").mock(
        return_value=Response(200, json=CHAT_RESPONSE)
    )
    await gw.post("/v1/chat/completions", json=_chat(), headers=_headers(rsa_keys, "k3"))

    resp = await gw.post(
        "/v1/chat/completions",
        json=_chat(messages=[{"role": "user", "content": "something else"}]),
        headers=_headers(rsa_keys, "k3"),
    )

    assert resp.status_code == 409
    assert resp.json()["type"].endswith(idempotency.KEY_REUSE)


@respx.mock
async def test_a_failed_request_hands_its_key_back(gw, rsa_keys):
    """A failure is exactly what a client should be able to retry, and there's
    nothing stored to replay to it."""
    route = respx.post(f"{BACKEND_URL}/v1/chat/completions").mock(
        side_effect=httpx.ConnectError("refused")
    )

    failed = await gw.post("/v1/chat/completions", json=_chat(), headers=_headers(rsa_keys, "k4"))
    assert failed.status_code >= 500

    route.mock(return_value=Response(200, json=CHAT_RESPONSE))
    retried = await gw.post("/v1/chat/completions", json=_chat(), headers=_headers(rsa_keys, "k4"))

    assert retried.status_code == 200
    assert "Idempotent-Replay" not in retried.headers


@respx.mock
async def test_one_client_s_key_cannot_answer_another_s(gw, rsa_keys):
    respx.post(f"{BACKEND_URL}/v1/chat/completions").mock(
        return_value=Response(200, json=CHAT_RESPONSE)
    )
    await gw.post("/v1/chat/completions", json=_chat(), headers=_headers(rsa_keys, "shared"))

    other = {
        "Authorization": f"Bearer {make_token(rsa_keys['private'], scope='inference:read model:solo', azp='other-client')}",
        idempotency.HEADER: "shared",
    }
    resp = await gw.post("/v1/chat/completions", json=_chat(), headers=other)

    assert resp.status_code == 200
    assert "Idempotent-Replay" not in resp.headers


@respx.mock
async def test_streaming_is_not_deduplicated(gw, rsa_keys):
    """Out of scope: replaying a stream means storing every chunk."""
    route = respx.post(f"{BACKEND_URL}/v1/chat/completions").mock(
        return_value=Response(
            200,
            text='data: {"choices":[{"delta":{"content":"hi"}}]}\n\ndata: [DONE]\n\n',
            headers={"Content-Type": "text/event-stream"},
        )
    )
    headers = {
        "Authorization": f"Bearer {make_token(rsa_keys['private'], scope='inference:stream model:solo')}",
        idempotency.HEADER: "k5",
    }

    await gw.post("/v1/chat/completions", json=_chat(stream=True), headers=headers)
    await gw.post("/v1/chat/completions", json=_chat(stream=True), headers=headers)

    assert route.call_count == 2


# ── the store itself ────────────────────────────────────────────────────────


async def test_a_request_still_running_is_the_one_worth_waiting_for(gw):
    """Two concurrent retries would otherwise generate twice. This is also the
    only refusal a client should ever retry, which is why it needs its own
    type rather than sharing one with the reuse cases."""
    claim = await idempotency.begin("c", "k", "/v1/chat/completions", {"a": 1})
    assert isinstance(claim, idempotency.Claim)

    second = await idempotency.begin("c", "k", "/v1/chat/completions", {"a": 1})

    assert isinstance(second, idempotency.Refusal)
    assert second.kind == idempotency.IN_PROGRESS


async def test_an_oversized_response_is_recorded_but_not_replayable(gw):
    """Losing the guarantee silently would be worse than saying so: a replay
    has to learn the original succeeded rather than generate again."""
    claim = await idempotency.begin("c", "big", "/v1/images/generations", {"a": 1})
    assert isinstance(claim, idempotency.Claim)
    await idempotency.complete(claim, 200, {"data": "x" * (1024 * 1024 + 10)})

    outcome = await idempotency.begin("c", "big", "/v1/images/generations", {"a": 1})

    assert isinstance(outcome, idempotency.Refusal)
    assert outcome.kind == idempotency.NOT_RETAINED


async def test_a_key_past_the_window_can_be_claimed_again(gw):
    claim = await idempotency.begin("c", "old", "/v1/chat/completions", {"a": 1})
    assert isinstance(claim, idempotency.Claim)
    await idempotency.complete(claim, 200, {"ok": True})

    async with db.get_session_factory()() as session:
        row = await session.get(db.IdempotencyRecord, ("c", "old"))
        row.created_at = datetime.now(timezone.utc) - timedelta(hours=25)
        await session.commit()

    outcome = await idempotency.begin("c", "old", "/v1/chat/completions", {"a": 1})

    assert isinstance(outcome, idempotency.Claim)


async def test_expired_records_are_purged(gw):
    claim = await idempotency.begin("c", "stale", "/v1/chat/completions", {"a": 1})
    assert isinstance(claim, idempotency.Claim)
    async with db.get_session_factory()() as session:
        row = await session.get(db.IdempotencyRecord, ("c", "stale"))
        row.created_at = datetime.now(timezone.utc) - timedelta(hours=25)
        await session.commit()

    assert await idempotency.purge_expired() == 1
    assert await idempotency.purge_expired() == 0


async def test_an_overlong_key_is_refused_rather_than_truncated(gw):
    """Truncating would make two different keys collide, which is worse than
    refusing one."""
    outcome = await idempotency.begin(
        "c", "x" * (idempotency.MAX_KEY_LENGTH + 1), "/v1/chat/completions", {"a": 1}
    )

    assert isinstance(outcome, idempotency.Refusal)
    assert outcome.kind == idempotency.INVALID_KEY


def test_the_fingerprint_ignores_key_order():
    """An SDK that serialises its retry's JSON in a different order must still
    match, or every retry would look like a different request."""
    a = idempotency.fingerprint("/p", {"x": 1, "y": 2})
    b = idempotency.fingerprint("/p", {"y": 2, "x": 1})

    assert a == b
    assert a != idempotency.fingerprint("/p", {"x": 1, "y": 3})


@respx.mock
async def test_an_overlong_key_is_a_bad_request_not_a_conflict(gw, rsa_keys):
    """RM-80: a client branching on "conflict" would conclude it had repeated
    a request, when its key simply didn't fit."""
    respx.post(f"{BACKEND_URL}/v1/chat/completions").mock(
        return_value=Response(200, json=CHAT_RESPONSE)
    )

    resp = await gw.post(
        "/v1/chat/completions",
        json=_chat(),
        headers=_headers(rsa_keys, "x" * (idempotency.MAX_KEY_LENGTH + 1)),
    )

    assert resp.status_code == 400
    assert resp.json()["type"].endswith(idempotency.INVALID_KEY)


@respx.mock
async def test_the_four_refusals_are_told_apart_by_type(gw, rsa_keys):
    """Each needs opposite handling, so each needs its own type — matching on
    the prose would break silently the first time a message is reworded."""
    respx.post(f"{BACKEND_URL}/v1/chat/completions").mock(
        return_value=Response(200, json=CHAT_RESPONSE)
    )
    await gw.post("/v1/chat/completions", json=_chat(), headers=_headers(rsa_keys, "k9"))

    reuse = await gw.post(
        "/v1/chat/completions",
        json=_chat(messages=[{"role": "user", "content": "different"}]),
        headers=_headers(rsa_keys, "k9"),
    )
    bad_key = await gw.post(
        "/v1/chat/completions", json=_chat(), headers=_headers(rsa_keys, "y" * 300)
    )

    assert reuse.status_code == 409
    assert bad_key.status_code == 400
    assert reuse.json()["type"] != bad_key.json()["type"]
