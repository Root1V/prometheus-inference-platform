"""PRM-187 — `logprobs` reaches the engine, and the pairing rule is enforced here.

Apeiron's `P2` #12 asks how confident the grounder was, so an agent can decide
when to escalate to a human. llama.cpp already answers that question; the
gateway was the only thing in the way, because an undeclared field is dropped
and then reported as ignored on every engine, including the one that honours it.

Both facts these tests pin were measured against a running llama-server before
any of this was written:

  * `logprobs: true` with `top_logprobs: 3` comes back as an OpenAI-shaped
    `logprobs.content[]`, one entry per token, each carrying `logprob` and its
    `top_logprobs` alternatives.
  * `top_logprobs` without `logprobs: true` is refused —
    `400 top_logprobs requires logprobs to be set to true` — and sending
    `logprobs: false` alongside it is refused identically. The flag has to be
    present *and* true.

The second one is why this gateway checks the pairing instead of forwarding it.
That 400 is llama.cpp's, in llama.cpp's error shape, and PRM-174 exists to stop
errors leaving by a door other than the problem+json envelope.
"""

from __future__ import annotations

import json

import pytest
import respx
from httpx import ASGITransport, AsyncClient, Response

from prometheus_gateway.models.registry import ModelEntry, ModelRegistry
from prometheus_gateway.models.schemas import ChatCompletionRequest
from tests.conftest import make_token

BACKEND_URL = "http://127.0.0.1:18101"

# Captured from a live llama-server (Qwen3-0.6B) answering with logprobs on.
LLAMA_RESPONSE = {
    "id": "x",
    "object": "chat.completion",
    "model": "small",
    "choices": [
        {
            "index": 0,
            "message": {"role": "assistant", "content": "yes"},
            "finish_reason": "stop",
            "logprobs": {
                "content": [
                    {
                        "id": 9693,
                        "token": "yes",
                        "logprob": -0.0005426090792752802,
                        "top_logprobs": [
                            {"id": 9693, "token": "yes", "logprob": -0.0005426090792752802},
                            {"id": 2152, "token": "no", "logprob": -7.6},
                        ],
                    }
                ]
            },
        }
    ],
    "usage": {"prompt_tokens": 5, "completion_tokens": 1, "total_tokens": 6},
}


@pytest.fixture
def app(settings):
    from prometheus_gateway.main import create_app

    registry = ModelRegistry.__new__(ModelRegistry)
    registry._models = {
        "small": ModelEntry(
            id="small",
            path="/m.gguf",
            context_length=4096,
            family="qwen3",
            quantization="Q4",
            backend_url=BACKEND_URL,
            backend_status="active",
            node="local",
            modality="text",
            model_id="small",
            model_slug="small",
        )
    }
    return create_app(settings=settings, registry=registry)


@pytest.fixture
async def gw(app):
    from prometheus_gateway import db

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        await db.create_tables(db.get_engine())
        yield c


def _headers(rsa_keys):
    return {
        "Authorization": f"Bearer {make_token(rsa_keys['private'], scope='inference:read model:small')}"
    }


def _body(**extra):
    return {"model": "small", "messages": [{"role": "user", "content": "hi"}], **extra}


# ── the fields reach the engine ────────────────────────────────────────────


@respx.mock
async def test_logprobs_is_forwarded_to_the_backend(gw, rsa_keys):
    route = respx.post(f"{BACKEND_URL}/v1/chat/completions").mock(
        return_value=Response(200, json=LLAMA_RESPONSE)
    )
    resp = await gw.post(
        "/v1/chat/completions",
        json=_body(logprobs=True, top_logprobs=3),
        headers=_headers(rsa_keys),
    )

    assert resp.status_code == 200
    sent = json.loads(route.calls[0].request.content)
    assert sent["logprobs"] is True
    assert sent["top_logprobs"] == 3


@respx.mock
async def test_logprobs_alone_is_forwarded_without_inventing_top_logprobs(gw, rsa_keys):
    """`logprobs` is useful on its own — it returns the chosen token's own
    probability. Supplying a default `top_logprobs` would change what the engine
    computes, on behalf of a caller who did not ask."""
    route = respx.post(f"{BACKEND_URL}/v1/chat/completions").mock(
        return_value=Response(200, json=LLAMA_RESPONSE)
    )
    await gw.post("/v1/chat/completions", json=_body(logprobs=True), headers=_headers(rsa_keys))

    sent = json.loads(route.calls[0].request.content)
    assert sent["logprobs"] is True
    assert "top_logprobs" not in sent


@respx.mock
async def test_the_engines_logprobs_reach_the_caller_intact(gw, rsa_keys):
    """The whole point is the number. If the gateway reshaped the response the
    caller would get a confidence it cannot compare to the engine's own."""
    respx.post(f"{BACKEND_URL}/v1/chat/completions").mock(
        return_value=Response(200, json=LLAMA_RESPONSE)
    )
    resp = await gw.post(
        "/v1/chat/completions",
        json=_body(logprobs=True, top_logprobs=2),
        headers=_headers(rsa_keys),
    )

    got = resp.json()["choices"][0]["logprobs"]["content"][0]
    assert got["logprob"] == -0.0005426090792752802
    assert [a["token"] for a in got["top_logprobs"]] == ["yes", "no"]


@respx.mock
async def test_omitting_them_sends_neither_field(gw, rsa_keys):
    """An absent option must not become a present false — that would turn
    logprobs off explicitly on an engine whose default may differ."""
    route = respx.post(f"{BACKEND_URL}/v1/chat/completions").mock(
        return_value=Response(200, json=LLAMA_RESPONSE)
    )
    await gw.post("/v1/chat/completions", json=_body(), headers=_headers(rsa_keys))

    sent = json.loads(route.calls[0].request.content)
    assert "logprobs" not in sent
    assert "top_logprobs" not in sent


@respx.mock
async def test_logprobs_false_is_forwarded_as_false_not_dropped(gw, rsa_keys):
    """Explicitly off is a request, not an absence — a caller turning it off
    against an engine configured to return them has to be obeyed."""
    route = respx.post(f"{BACKEND_URL}/v1/chat/completions").mock(
        return_value=Response(200, json=LLAMA_RESPONSE)
    )
    await gw.post("/v1/chat/completions", json=_body(logprobs=False), headers=_headers(rsa_keys))

    assert json.loads(route.calls[0].request.content)["logprobs"] is False


# ── the pairing rule, which is the engine's and is enforced here ───────────


@respx.mock
async def test_top_logprobs_without_logprobs_is_refused_by_the_gateway(gw, rsa_keys):
    """Measured: llama.cpp answers this 400. Letting it travel would deliver an
    error in the engine's shape, which is exactly what PRM-174 closed."""
    route = respx.post(f"{BACKEND_URL}/v1/chat/completions").mock(
        return_value=Response(200, json=LLAMA_RESPONSE)
    )
    resp = await gw.post(
        "/v1/chat/completions", json=_body(top_logprobs=3), headers=_headers(rsa_keys)
    )

    assert resp.status_code == 422
    assert not route.calls, "the engine must never see a request it would refuse"


@respx.mock
async def test_top_logprobs_with_logprobs_false_is_refused_too(gw, rsa_keys):
    """The engine wants the flag present *and* true — false is not good enough,
    which a second live request confirmed rather than assumed."""
    route = respx.post(f"{BACKEND_URL}/v1/chat/completions").mock(
        return_value=Response(200, json=LLAMA_RESPONSE)
    )
    resp = await gw.post(
        "/v1/chat/completions",
        json=_body(logprobs=False, top_logprobs=3),
        headers=_headers(rsa_keys),
    )

    assert resp.status_code == 422
    assert not route.calls


@respx.mock
async def test_the_refusal_leaves_in_the_gateways_envelope(gw, rsa_keys):
    """PRM-174's rule applied to this route: whatever the reason, the body is
    problem+json and carries the type/title the SDKs already parse."""
    resp = await gw.post(
        "/v1/chat/completions", json=_body(top_logprobs=1), headers=_headers(rsa_keys)
    )

    assert resp.headers["content-type"].startswith("application/problem+json")
    body = resp.json()
    assert body["type"].startswith("https://")
    assert "title" in body and "status" in body


# ── the bounds, and the schema's own shape ─────────────────────────────────


@pytest.mark.parametrize("n", [-1, 21])
async def test_top_logprobs_outside_the_engines_range_is_refused(gw, rsa_keys, n):
    resp = await gw.post(
        "/v1/chat/completions",
        json=_body(logprobs=True, top_logprobs=n),
        headers=_headers(rsa_keys),
    )
    assert resp.status_code == 422


@pytest.mark.parametrize("n", [0, 20])
def test_the_range_endpoints_themselves_are_accepted(n):
    req = ChatCompletionRequest(
        model="small", messages=[{"role": "user", "content": "hi"}], logprobs=True, top_logprobs=n
    )
    assert req.to_llama_payload()["top_logprobs"] == n


def test_neither_field_is_reported_as_an_ignored_parameter():
    """The reason these are declared rather than left to `extra`: an undeclared
    field is named in `X-Prometheus-Ignored-Parameters` on every engine, telling
    a caller it was dropped when llama.cpp honours it."""
    req = ChatCompletionRequest(
        model="small",
        messages=[{"role": "user", "content": "hi"}],
        logprobs=True,
        top_logprobs=5,
    )
    extras = set(req.model_dump().keys()) - set(ChatCompletionRequest.model_fields)
    assert not extras, f"declared fields must not land in extras: {extras}"
