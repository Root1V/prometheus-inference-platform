"""RM-78 — client-supplied idempotency keys.

A retry is otherwise always a new, billable generation, so an SDK can only
retry where the platform proves nothing ran — which excludes the commonest
case, retrying after its own timeout.

These cover the part that has to be right for billing: a replay must not reach
the model, must not record usage, and must never answer a different request.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

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
        # Before as well as after. The engine is a module global, so a straggler
        # from the previous test that is still awaiting a session writes into
        # whichever database is current when it wakes — which is this test's.
        # Draining after alone left that window open, and it showed: roughly one
        # run in four.
        await _drain_detached()
        await db.create_tables(db.get_engine())
        yield client
        # RM-87 made a streamed request's accounting a detached task, precisely
        # so a client hanging up cannot cancel it. The same independence means a
        # task from one test can still be writing while the next one counts
        # rows. Drain them here rather than letting the order of the file decide
        # whether a test passes.
        await _drain_detached()


async def _drain_detached() -> None:
    from prometheus_gateway import router

    while router._detached:
        await asyncio.gather(*list(router._detached), return_exceptions=True)


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

    # UTC, not date.today(): record_usage stamps the day in UTC because billing
    # periods are UTC calendar months (RM-60). Querying the local date makes
    # this test fail for the five hours a day the two disagree — which is
    # exactly how it was found.
    utc_today = datetime.now(timezone.utc).date()
    events = await db.query_usage_events_range(utc_today, utc_today)
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
async def test_a_completed_stream_is_replayed_not_regenerated(gw, rsa_keys):
    """RM-82: a streaming retry used to regenerate and bill twice — the same
    exposure RM-78 closed for non-streaming, in the busier path."""
    chunks = (
        'data: {"model":"solo","choices":[{"delta":{"content":"hi"}}]}\n\n'
        'data: {"model":"solo","choices":[{"delta":{}}],"timings":{"prompt_n":3,"predicted_n":1}}\n\n'
        "data: [DONE]\n\n"
    )
    route = respx.post(f"{BACKEND_URL}/v1/chat/completions").mock(
        return_value=Response(200, text=chunks, headers={"Content-Type": "text/event-stream"})
    )
    headers = {
        "Authorization": f"Bearer {make_token(rsa_keys['private'], scope='inference:stream model:solo')}",
        idempotency.HEADER: "s1",
    }

    first = await gw.post("/v1/chat/completions", json=_chat(stream=True), headers=headers)
    second = await gw.post("/v1/chat/completions", json=_chat(stream=True), headers=headers)

    assert route.call_count == 1
    assert first.text == second.text
    assert second.headers["Idempotent-Replay"] == "true"
    assert second.headers["content-type"].startswith("text/event-stream")
    assert "[DONE]" in second.text


@respx.mock
async def test_a_broken_stream_hands_its_key_back(gw, rsa_keys):
    """Nothing complete was produced, so there is nothing to replay — and a
    retry after a break is exactly what a client should be able to make."""
    route = respx.post(f"{BACKEND_URL}/v1/chat/completions").mock(
        side_effect=httpx.ConnectError("refused")
    )
    headers = {
        "Authorization": f"Bearer {make_token(rsa_keys['private'], scope='inference:stream model:solo')}",
        idempotency.HEADER: "s2",
    }

    broken = await gw.post("/v1/chat/completions", json=_chat(stream=True), headers=headers)
    assert "error" in broken.text

    route.mock(
        return_value=Response(
            200,
            text='data: {"choices":[{"delta":{"content":"ok"}}]}\n\ndata: [DONE]\n\n',
            headers={"Content-Type": "text/event-stream"},
        )
    )
    retried = await gw.post("/v1/chat/completions", json=_chat(stream=True), headers=headers)

    assert route.call_count == 2
    assert "Idempotent-Replay" not in retried.headers


@respx.mock
async def test_a_streaming_replay_is_not_billed_again(gw, rsa_keys):
    from datetime import datetime, timezone

    respx.post(f"{BACKEND_URL}/v1/chat/completions").mock(
        return_value=Response(
            200,
            text='data: {"choices":[{"delta":{"content":"hi"}}],"timings":{"prompt_n":3,"predicted_n":1}}\n\ndata: [DONE]\n\n',
            headers={"Content-Type": "text/event-stream"},
        )
    )
    headers = {
        "Authorization": f"Bearer {make_token(rsa_keys['private'], scope='inference:stream model:solo')}",
        idempotency.HEADER: "s3",
    }

    await gw.post("/v1/chat/completions", json=_chat(stream=True), headers=headers)
    await gw.post("/v1/chat/completions", json=_chat(stream=True), headers=headers)

    await _drain_detached()
    utc_today = datetime.now(timezone.utc).date()
    events = await db.query_usage_events_range(utc_today, utc_today)
    assert len(events) == 1


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


# ── RM-81: the in-flight refusal says how long to wait ──────────────────────


async def test_in_progress_carries_a_wait_estimated_from_the_model(gw):
    """Not the backend timeout — that's an upper bound, and would have a client
    wait ten minutes for something that usually takes two seconds."""
    from prometheus_gateway.telemetry import metrics_store

    await metrics_store.record_inference(
        prompt_tokens=1, completion_tokens=1, latency_ms=9000, backend_id="b", model_id="slow"
    )
    claim = await idempotency.begin("c", "w1", "/v1/chat/completions", {"a": 1}, "slow")
    assert isinstance(claim, idempotency.Claim)

    second = await idempotency.begin("c", "w1", "/v1/chat/completions", {"a": 1}, "slow")

    assert isinstance(second, idempotency.Refusal)
    assert second.kind == idempotency.IN_PROGRESS
    # ~9s observed, barely any elapsed, so the hint lands near the observation.
    assert 5 <= (second.retry_after_seconds or 0) <= 9


async def test_a_model_with_no_observations_still_gets_a_hint(gw):
    """The counters are in process memory and reset with the gateway, so "no
    samples" is a normal state, not an error."""
    second_claim = await idempotency.begin("c", "w2", "/v1/chat/completions", {"a": 1}, "unseen")
    assert isinstance(second_claim, idempotency.Claim)

    refusal = await idempotency.begin("c", "w2", "/v1/chat/completions", {"a": 1}, "unseen")

    assert isinstance(refusal, idempotency.Refusal)
    assert refusal.retry_after_seconds == idempotency._DEFAULT_RETRY_AFTER_S


async def test_only_the_waitable_refusal_carries_a_hint(gw):
    """A Retry-After on a refusal that never resolves would invite exactly the
    retry we're telling the client not to make."""
    reuse = await idempotency.begin("c", "w3", "/v1/chat/completions", {"a": 1})
    assert isinstance(reuse, idempotency.Claim)
    await idempotency.complete(reuse, 200, {"ok": True})

    outcome = await idempotency.begin("c", "w3", "/v1/chat/completions", {"different": True})

    assert isinstance(outcome, idempotency.Refusal)
    assert outcome.kind == idempotency.KEY_REUSE
    assert outcome.retry_after_seconds is None


@respx.mock
async def test_the_http_refusal_sets_the_retry_after_header(gw, rsa_keys):
    # Same payload shape the handler fingerprints: Pydantic's dump, defaults
    # included — not the raw dict a caller writes.
    from prometheus_gateway.models.schemas import ChatCompletionRequest

    claim = await idempotency.begin(
        "client-abc",
        "http-wait",
        "/v1/chat/completions",
        ChatCompletionRequest(**_chat()).model_dump(),
        "solo",
    )
    assert isinstance(claim, idempotency.Claim)

    resp = await gw.post(
        "/v1/chat/completions", json=_chat(), headers=_headers(rsa_keys, "http-wait")
    )

    assert resp.status_code == 409
    assert resp.json()["type"].endswith(idempotency.IN_PROGRESS)
    assert int(resp.headers["Retry-After"]) >= 1


# ── RM-83: a charge for a half-delivered answer has to be visible ───────────


@respx.mock
async def test_a_stream_that_produced_nothing_is_not_billed(gw, rsa_keys):
    """Before the marking question even arises: a stream that broke before the
    model emitted anything generated no tokens, so there is nothing to charge."""
    from datetime import datetime, timezone

    respx.post(f"{BACKEND_URL}/v1/chat/completions").mock(side_effect=httpx.ConnectError("refused"))
    headers = {
        "Authorization": f"Bearer {make_token(rsa_keys['private'], scope='inference:stream model:solo')}",
    }

    resp = await gw.post("/v1/chat/completions", json=_chat(stream=True), headers=headers)

    assert "error" in resp.text
    utc_today = datetime.now(timezone.utc).date()
    assert await db.query_usage_events_range(utc_today, utc_today) == []


@respx.mock
async def test_a_clean_stream_is_not_marked_interrupted(gw, rsa_keys):
    from datetime import datetime, timezone

    respx.post(f"{BACKEND_URL}/v1/chat/completions").mock(
        return_value=Response(
            200,
            text='data: {"choices":[{"delta":{"content":"hi"}}],"timings":{"prompt_n":3,"predicted_n":1}}\n\ndata: [DONE]\n\n',
            headers={"Content-Type": "text/event-stream"},
        )
    )
    headers = {
        "Authorization": f"Bearer {make_token(rsa_keys['private'], scope='inference:stream model:solo')}",
    }

    await gw.post("/v1/chat/completions", json=_chat(stream=True), headers=headers)

    await _drain_detached()
    utc_today = datetime.now(timezone.utc).date()
    events = await db.query_usage_events_range(utc_today, utc_today)
    assert [e.interrupted for e in events] == [False]
    assert [e.termination_reason for e in events] == [db.TERMINATION_COMPLETE]


@respx.mock
async def test_a_stream_that_broke_after_producing_tokens_is_billed_and_marked(gw, rsa_keys):
    """The case the flag exists for. llama.cpp reports token counts only on its
    final `timings` frame, so a stream that dies mid-answer used to be billed as
    zero and leave no row — the tokens were generated, the GPU ran, and nothing
    recorded it."""
    from datetime import datetime, timezone

    async def half_a_stream(request):
        async def body():
            yield b'data: {"choices":[{"delta":{"content":"the"}}]}\n\n'
            yield b'data: {"choices":[{"delta":{"content":" quick"}}]}\n\n'
            yield b'data: {"choices":[{"delta":{"content":" brown"}}]}\n\n'
            raise httpx.ReadError("connection died mid-answer")

        return Response(200, stream=body(), headers={"Content-Type": "text/event-stream"})

    respx.post(f"{BACKEND_URL}/v1/chat/completions").mock(side_effect=half_a_stream)
    headers = {
        "Authorization": f"Bearer {make_token(rsa_keys['private'], scope='inference:stream model:solo')}",
    }

    resp = await gw.post("/v1/chat/completions", json=_chat(stream=True), headers=headers)

    assert "stream interrupted" in resp.text

    await _drain_detached()
    utc_today = datetime.now(timezone.utc).date()
    events = await db.query_usage_events_range(utc_today, utc_today)
    assert len(events) == 1
    assert events[0].completion_tokens == 3
    assert events[0].interrupted is True
    assert events[0].termination_reason == db.TERMINATION_UPSTREAM_ERROR


async def test_an_interrupted_charge_is_recorded_as_such(gw):
    """We charge for the tokens actually sent to the caller. What was missing is
    the record: a client disputing a charge for a half-delivered answer had
    nothing to point at, and neither did we."""
    from datetime import datetime, timezone

    await db.record_usage(
        "c", "m", 100, 40, instance_id="i1", termination_reason=db.TERMINATION_UPSTREAM_ERROR
    )
    await db.record_usage("c", "m", 100, 90, instance_id="i1")

    utc_today = datetime.now(timezone.utc).date()
    events = await db.query_usage_events_range(utc_today, utc_today)

    assert sorted(e.interrupted for e in events) == [False, True]


async def test_the_three_ways_a_request_ends_stay_distinguishable(gw):
    """RM-88: the boolean could not tell an answer we broke from one the caller
    walked away from, and those are opposite conversations to have when a charge
    is questioned. A chat UI's stop button produces the second all day."""
    from datetime import datetime, timezone

    for reason in (
        db.TERMINATION_COMPLETE,
        db.TERMINATION_UPSTREAM_ERROR,
        db.TERMINATION_CLIENT_DISCONNECTED,
    ):
        await db.record_usage("c", "m", 10, 10, instance_id="i1", termination_reason=reason)

    utc_today = datetime.now(timezone.utc).date()
    events = await db.query_usage_events_range(utc_today, utc_today)

    assert sorted(e.termination_reason for e in events) == [
        "client_disconnected",
        "complete",
        "upstream_error",
    ]
    # The published boolean keeps meaning what we told SDK clients it means:
    # "you were charged for an answer you did not receive whole."
    by_reason = {e.termination_reason: e.interrupted for e in events}
    assert by_reason["complete"] is False
    assert by_reason["upstream_error"] is True
    assert by_reason["client_disconnected"] is True


# ── RM-87: what the client does with the body must not change what we record ─


@respx.mock
async def test_the_terminal_frame_is_sent_once(gw, rsa_keys):
    """The backend ends its own stream with `[DONE]`, and we used to forward it
    and then add ours. A client is right to stop reading at the first terminal
    frame — and that one arrived before anything had been accounted for, which
    is how streamed requests went unbilled."""
    respx.post(f"{BACKEND_URL}/v1/chat/completions").mock(
        return_value=Response(
            200,
            text=(
                'data: {"choices":[{"delta":{"content":"hi"}}],"timings":{"prompt_n":3,"predicted_n":1}}\n\n'
                "data: [DONE]\n\n"
            ),
            headers={"Content-Type": "text/event-stream"},
        )
    )
    headers = {
        "Authorization": f"Bearer {make_token(rsa_keys['private'], scope='inference:stream model:solo')}",
    }

    resp = await gw.post("/v1/chat/completions", json=_chat(stream=True), headers=headers)

    assert resp.text.count("[DONE]") == 1


@respx.mock
async def test_reasoning_tokens_are_counted_when_a_stream_breaks(gw, rsa_keys):
    """A reasoning model streams its thinking as `reasoning_content`, not
    `content`. RM-83's fallback counted only the latter, so a stream that broke
    while the model was still reasoning tallied zero generated tokens and was
    billed nothing — with the GPU having run the whole time."""
    from datetime import datetime, timezone

    async def reasoning_then_break(request):
        async def body():
            for word in ("Okay", ", ", "let"):
                yield (
                    b'data: {"choices":[{"delta":{"reasoning_content":"'
                    + word.encode()
                    + b'"}}]}\n\n'
                )
            raise httpx.ReadError("died while still thinking")

        return Response(200, stream=body(), headers={"Content-Type": "text/event-stream"})

    respx.post(f"{BACKEND_URL}/v1/chat/completions").mock(side_effect=reasoning_then_break)
    headers = {
        "Authorization": f"Bearer {make_token(rsa_keys['private'], scope='inference:stream model:solo')}",
    }

    await gw.post("/v1/chat/completions", json=_chat(stream=True), headers=headers)

    await _drain_detached()
    utc_today = datetime.now(timezone.utc).date()
    events = await db.query_usage_events_range(utc_today, utc_today)
    assert len(events) == 1
    assert events[0].completion_tokens == 3
    assert events[0].interrupted is True
    assert events[0].termination_reason == db.TERMINATION_UPSTREAM_ERROR


# ── RM-95: the GenAI semantic conventions Argus asked for ────────────────────


def test_genai_request_attributes_use_the_conventional_names():
    from prometheus_gateway.router import _genai_request_attrs

    attrs = _genai_request_attrs("chat", "qwen3-0.6b", "llama_cpp")

    assert attrs == {
        "gen_ai.operation.name": "chat",
        "gen_ai.request.model": "qwen3-0.6b",
        "gen_ai.provider.name": "llama.cpp",
    }


def test_a_stream_the_caller_abandoned_says_so_in_finish_reasons():
    """Argus called this the most valuable attribute we have and said almost
    nobody measures it: it separates "we failed" from "they stopped waiting",
    which are different problems with different fixes."""
    from prometheus_gateway.router import _genai_response_attrs

    attrs = _genai_response_attrs(
        response_model="qwen3-0.6b",
        input_tokens=10,
        output_tokens=5,
        finish_reason=db.TERMINATION_CLIENT_DISCONNECTED,
        backend_id="qwen3-0-6b-1",
        ttft_ms=180,
    )

    assert attrs["gen_ai.response.finish_reasons"] == ["client_disconnected"]
    assert attrs["gen_ai.usage.output_tokens"] == 5
    assert attrs["argus.inference.backend_id"] == "qwen3-0-6b-1"
    assert attrs["argus.inference.ttft_ms"] == 180


def test_attributes_nobody_measured_are_left_out_rather_than_guessed():
    from prometheus_gateway.router import _genai_response_attrs

    assert _genai_response_attrs(output_tokens=3) == {"gen_ai.usage.output_tokens": 3}


@respx.mock
async def test_the_genai_attributes_actually_reach_a_span(gw, rsa_keys):
    """The three tests above check that the helpers build the right dictionaries
    and never that anything uses them. Deleting the call that puts them on the
    span left all 455 tests green — verified by mutating it.

    Axonium hit the same class of gap on their side and named it: a corpus that
    asserts the raw payload cannot see anything the code derives from it. This
    asserts the derived output, which is the part a consumer reads.
    """
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    previous = trace.get_tracer_provider()
    trace._TRACER_PROVIDER = provider  # type: ignore[attr-defined]
    try:
        import prometheus_gateway.router as router_module

        router_module._tracer = trace.get_tracer("test")
        respx.post(f"{BACKEND_URL}/v1/chat/completions").mock(
            return_value=Response(
                200,
                json={
                    "model": "solo",
                    "choices": [{"message": {"role": "assistant", "content": "hi"}}],
                    "usage": {"prompt_tokens": 3, "completion_tokens": 1},
                },
            )
        )
        headers = {
            "Authorization": f"Bearer {make_token(rsa_keys['private'], scope='inference:read model:solo')}",
        }

        await gw.post("/v1/chat/completions", json=_chat(), headers=headers)

        attrs: dict = {}
        for span in exporter.get_finished_spans():
            if "gen_ai.request.model" in (span.attributes or {}):
                attrs = dict(span.attributes or {})
        assert attrs, "no span carried the GenAI attributes"
        assert attrs["gen_ai.operation.name"] == "chat"
        assert attrs["gen_ai.usage.output_tokens"] == 1
        assert attrs["gen_ai.response.finish_reasons"] == ("complete",)
    finally:
        trace._TRACER_PROVIDER = previous  # type: ignore[attr-defined]
        router_module._tracer = trace.get_tracer("prometheus_gateway")


def _genai_span(exporter):
    for span in exporter.get_finished_spans():
        if "gen_ai.request.model" in (span.attributes or {}):
            return span
    return None


@respx.mock
async def test_streaming_and_not_produce_the_same_kind_of_span(gw, rsa_keys):
    """PRM-104, from Argus A-21. They reported `inference.request` as Internal.
    It was worse than that: the same data arrived under two span kinds and two
    names depending on whether the caller asked for a stream, so their
    client-side RED metrics saw half the traffic and which half was the client's
    choice. Asserting one path could never have caught it — this asserts both
    and compares them.
    """
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    import prometheus_gateway.router as router_module

    headers = {
        "Authorization": f"Bearer {make_token(rsa_keys['private'], scope='inference:stream inference:read model:solo')}",
    }
    seen = {}
    for streaming in (False, True):
        exporter = InMemorySpanExporter()
        provider = TracerProvider()
        provider.add_span_processor(SimpleSpanProcessor(exporter))
        previous = trace.get_tracer_provider()
        trace._TRACER_PROVIDER = provider  # type: ignore[attr-defined]
        router_module._tracer = trace.get_tracer("test")
        try:
            if streaming:
                respx.post(f"{BACKEND_URL}/v1/chat/completions").mock(
                    return_value=Response(
                        200,
                        text=(
                            'data: {"choices":[{"delta":{"content":"hi"}}],'
                            '"timings":{"prompt_n":3,"predicted_n":1}}\n\n'
                            "data: [DONE]\n\n"
                        ),
                        headers={"Content-Type": "text/event-stream"},
                    )
                )
            else:
                respx.post(f"{BACKEND_URL}/v1/chat/completions").mock(
                    return_value=Response(
                        200,
                        json={
                            "model": "solo",
                            "choices": [{"message": {"role": "assistant", "content": "hi"}}],
                            "usage": {"prompt_tokens": 3, "completion_tokens": 1},
                        },
                    )
                )
            await gw.post("/v1/chat/completions", json=_chat(stream=streaming), headers=headers)
            await _drain_detached()
            span = _genai_span(exporter)
            assert span is not None, f"no GenAI span (streaming={streaming})"
            seen[streaming] = (span.kind, span.name)
        finally:
            trace._TRACER_PROVIDER = previous  # type: ignore[attr-defined]
            router_module._tracer = trace.get_tracer("prometheus_gateway")

    assert seen[False][0] == trace.SpanKind.CLIENT, "calling a model is a client operation"
    assert seen[False] == seen[True], (
        f"the two paths disagree: non-streaming={seen[False]}, streaming={seen[True]}"
    )
    assert seen[False][1] == "chat solo"


@respx.mock
async def test_the_internal_span_no_longer_carries_genai_attributes(gw, rsa_keys):
    """`inference.request` describes the gateway's own work. Leaving the GenAI
    attributes on it too would report every call twice to anything aggregating
    by attribute rather than by span kind."""
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    import prometheus_gateway.router as router_module

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    previous = trace.get_tracer_provider()
    trace._TRACER_PROVIDER = provider  # type: ignore[attr-defined]
    router_module._tracer = trace.get_tracer("test")
    try:
        respx.post(f"{BACKEND_URL}/v1/chat/completions").mock(
            return_value=Response(
                200,
                json={
                    "model": "solo",
                    "choices": [{"message": {"role": "assistant", "content": "hi"}}],
                    "usage": {"prompt_tokens": 3, "completion_tokens": 1},
                },
            )
        )
        await gw.post(
            "/v1/chat/completions",
            json=_chat(),
            headers={
                "Authorization": f"Bearer {make_token(rsa_keys['private'], scope='inference:read model:solo')}"
            },
        )
        internal = [s for s in exporter.get_finished_spans() if s.name == "inference.request"]
        assert len(internal) == 1
        attrs = dict(internal[0].attributes or {})
        assert not any(k.startswith("gen_ai.") for k in attrs)
        assert attrs["model"] == "solo"  # its own description is untouched
    finally:
        trace._TRACER_PROVIDER = previous  # type: ignore[attr-defined]
        router_module._tracer = trace.get_tracer("prometheus_gateway")


# ── PRM-105: the two things 30 minutes of traffic showed Argus would never see ──


@respx.mock
async def test_request_model_is_what_the_caller_asked_for(settings, rsa_keys):
    """Both halves used to come from the resolved name, so the pair could never
    differ — and the pair is the whole point: Argus uses it to spot "asked for
    one model, served another", which they say explains half their incidents.
    Verified live first by asking for an alias and watching the span report the
    resolved name on both.
    """
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    import prometheus_gateway.router as router_module
    from prometheus_gateway.main import create_app

    # Its own registry rather than the shared fixture: an entry whose instance
    # id differs from its slug is exactly the RM-70 alias shape, and adding one
    # to the shared fixture would turn "solo" into a two-replica group and
    # change what every other test in this file is exercising.
    registry = ModelRegistry.__new__(ModelRegistry)
    registry._models = {
        "solo-alias": ModelEntry(
            id="solo-alias",
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
    app = create_app(settings=settings, registry=registry)

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    previous = trace.get_tracer_provider()
    trace._TRACER_PROVIDER = provider  # type: ignore[attr-defined]
    router_module._tracer = trace.get_tracer("test")
    try:
        await db.create_tables(db.get_engine())
        respx.post(f"{BACKEND_URL}/v1/chat/completions").mock(
            return_value=Response(
                200,
                json={
                    "model": "solo",
                    "choices": [{"message": {"role": "assistant", "content": "hi"}}],
                    "usage": {"prompt_tokens": 3, "completion_tokens": 1},
                },
            )
        )
        body = _chat()
        body["model"] = "solo-alias"
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post(
                "/v1/chat/completions",
                json=body,
                headers={
                    "Authorization": f"Bearer {make_token(rsa_keys['private'], scope='inference:read model:solo-alias model:solo')}"
                },
            )
        span = _genai_span(exporter)
        assert span is not None
        attrs = dict(span.attributes or {})
        assert attrs["gen_ai.request.model"] == "solo-alias", "what the caller asked for"
        assert attrs["gen_ai.response.model"] == "solo", "what was actually served"
        assert attrs["gen_ai.request.model"] != attrs["gen_ai.response.model"]
        # Argus asked for the convention here, cardinality being their problem.
        assert span.name == "chat solo-alias"
    finally:
        trace._TRACER_PROVIDER = previous  # type: ignore[attr-defined]
        router_module._tracer = trace.get_tracer("prometheus_gateway")


@respx.mock
async def test_first_token_ms_is_set_even_when_nothing_visible_is_streamed(gw, rsa_keys):
    """A reasoning model streams `reasoning_content` before any `content`.
    ttft_ms deliberately waits for visible output, which left it on 13 of 247
    spans — and the missing ones chosen by how much the model reasons, not by
    anything Argus controls. first_token_ms answers the other question.
    """
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    import prometheus_gateway.router as router_module

    async def only_reasoning(request):
        async def body():
            for word in ("Let", " me", " think"):
                yield (
                    b'data: {"choices":[{"delta":{"reasoning_content":"'
                    + word.encode()
                    + b'"}}]}\n\n'
                )
            raise httpx.ReadError("gone while still reasoning")

        return Response(200, stream=body(), headers={"Content-Type": "text/event-stream"})

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    previous = trace.get_tracer_provider()
    trace._TRACER_PROVIDER = provider  # type: ignore[attr-defined]
    router_module._tracer = trace.get_tracer("test")
    try:
        respx.post(f"{BACKEND_URL}/v1/chat/completions").mock(side_effect=only_reasoning)
        await gw.post(
            "/v1/chat/completions",
            json=_chat(stream=True),
            headers={
                "Authorization": f"Bearer {make_token(rsa_keys['private'], scope='inference:stream model:solo')}"
            },
        )
        await _drain_detached()
        span = _genai_span(exporter)
        assert span is not None
        attrs = dict(span.attributes or {})
        assert "argus.inference.first_token_ms" in attrs, "no measure of when the model started"
        assert attrs["argus.inference.first_token_ms"] >= 0
        # ttft_ms keeps its meaning: nothing visible was ever streamed.
        assert "argus.inference.ttft_ms" not in attrs
    finally:
        trace._TRACER_PROVIDER = previous  # type: ignore[attr-defined]
        router_module._tracer = trace.get_tracer("prometheus_gateway")


# ── A-23: what happens to a key whose request ended in error ─────────────────

CLIENT = "client-abc"  # what make_token() puts in `azp`, which is the key's owner


async def _record(key: str):
    async with db.get_session_factory()() as session:
        return await session.get(db.IdempotencyRecord, (CLIENT, key))


@respx.mock
async def test_a_key_is_released_when_the_backend_fails(gw, rsa_keys):
    """The handled error path: the backend answers, badly.

    A failed request must hand its key back — the client's retry is exactly
    what the key exists to make safe, and there is nothing stored to replay.
    """
    respx.post(f"{BACKEND_URL}/v1/chat/completions").mock(
        return_value=Response(500, json={"error": "engine exploded"})
    )

    first = await gw.post("/v1/chat/completions", json=_chat(), headers=_headers(rsa_keys, "k-502"))

    assert first.status_code >= 400
    row = await _record("k-502")
    assert row is None, (
        "the key is still held after a failed request: "
        f"state={getattr(row, 'state', None)!r} status={getattr(row, 'status_code', None)!r}. "
        "A stored 5xx replays for 24h, instantly, without ever reaching the model again."
    )


async def test_a_key_is_released_when_the_handler_raises(gw, rsa_keys, monkeypatch):
    """The unhandled 500 — the case Synaptum reported (A-23).

    The settle runs in a middleware precisely so that none of the handlers'
    dozen exit paths can forget it. But `await call_next(request)` *raises*
    when an exception escapes the route, because Starlette's
    ServerErrorMiddleware sits outside this one. If the settle is skipped the
    record stays IN_PROGRESS for the full 24h window, and every later call with
    that key is refused in milliseconds without reaching a backend — which from
    outside is indistinguishable from the platform being down.
    """
    from prometheus_gateway import router

    async def _boom(*args, **kwargs):
        raise RuntimeError("something nobody caught")

    monkeypatch.setattr(router, "_healthy_members", _boom)

    with pytest.raises(RuntimeError):
        await gw.post("/v1/chat/completions", json=_chat(), headers=_headers(rsa_keys, "k-500"))

    row = await _record("k-500")
    assert row is None, (
        f"a 500 left the key held: state={getattr(row, 'state', None)!r}. "
        "Every retry for the next 24h is refused without reaching the model."
    )
