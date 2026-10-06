"""PRM-195 — the template switch reaches the engine.

repo2deck measured `qwen36-35b-a3b-q4` spending 1,500-6,000 tokens thinking
before every slide — 80 to 140 seconds a call — found the switch that turns it
off, and found it worked against the engine and not through this gateway. The
allowlist drops what it does not name, which is `PRM-127` working as designed
and costing a tenfold difference.

Measured against a running llama-server before any of this was written:

    without                       215 tokens   6.91 s
    chat_template_kwargs          16 tokens    0.71 s   (enable_thinking: false)

Same useful answer.

**They asked for three fields and this forwards one**, which is the part worth
keeping. Top-level `reasoning_effort` and `reasoning_budget` changed nothing
against the same server — identical output, identical reasoning length, with and
without. Declaring them would have moved them out of
`X-Prometheus-Ignored-Parameters` and reported as honoured what the engine
discards: the inverse of the bug being fixed, and harder to notice.
`reasoning_effort` does work *inside* the mapping, where the template reads it —
414 to 23 characters of reasoning on `low`, 583 on `high`.
"""

from __future__ import annotations

import json

import pytest
import respx
from httpx import ASGITransport, AsyncClient, Response

from prometheus_gateway.models.registry import ModelEntry, ModelRegistry
from prometheus_gateway.models.schemas import ChatCompletionRequest
from tests.conftest import make_token

BACKEND_URL = "http://127.0.0.1:18104"

LLAMA_RESPONSE = {
    "id": "x",
    "object": "chat.completion",
    "model": "small",
    "choices": [
        {
            "index": 0,
            "message": {"role": "assistant", "content": "hola"},
            "finish_reason": "stop",
        }
    ],
    "usage": {"prompt_tokens": 5, "completion_tokens": 16, "total_tokens": 21},
}

NO_THINKING = {"enable_thinking": False}


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
    return {"model": "small", "messages": [{"role": "user", "content": "hola"}], **extra}


# ── it reaches the engine ──────────────────────────────────────────────────


@respx.mock
async def test_chat_template_kwargs_is_forwarded(gw, rsa_keys):
    route = respx.post(f"{BACKEND_URL}/v1/chat/completions").mock(
        return_value=Response(200, json=LLAMA_RESPONSE)
    )
    resp = await gw.post(
        "/v1/chat/completions",
        json=_body(chat_template_kwargs=NO_THINKING),
        headers=_headers(rsa_keys),
    )

    assert resp.status_code == 200
    assert json.loads(route.calls[0].request.content)["chat_template_kwargs"] == NO_THINKING


@respx.mock
async def test_the_mapping_arrives_whole_and_unexamined(gw, rsa_keys):
    """Its keys belong to each model's own template, not to this gateway. A
    whitelist of them here would be a second copy of someone else's Jinja."""
    kwargs = {
        "enable_thinking": False,
        "reasoning_effort": "low",
        "custom_future_key": {"nested": [1, 2]},
    }
    route = respx.post(f"{BACKEND_URL}/v1/chat/completions").mock(
        return_value=Response(200, json=LLAMA_RESPONSE)
    )
    await gw.post(
        "/v1/chat/completions", json=_body(chat_template_kwargs=kwargs), headers=_headers(rsa_keys)
    )

    assert json.loads(route.calls[0].request.content)["chat_template_kwargs"] == kwargs


@respx.mock
async def test_omitting_it_sends_no_such_field(gw, rsa_keys):
    """An absent mapping must not become an empty one — an empty dict is a
    request to render the template with no variables, which is not the same as
    not asking."""
    route = respx.post(f"{BACKEND_URL}/v1/chat/completions").mock(
        return_value=Response(200, json=LLAMA_RESPONSE)
    )
    await gw.post("/v1/chat/completions", json=_body(), headers=_headers(rsa_keys))

    assert "chat_template_kwargs" not in json.loads(route.calls[0].request.content)


@respx.mock
async def test_an_empty_mapping_is_still_sent(gw, rsa_keys):
    """Asked for explicitly, it travels — the caller said something, and `{}` is
    distinguishable from absence in `to_llama_payload`'s None check."""
    route = respx.post(f"{BACKEND_URL}/v1/chat/completions").mock(
        return_value=Response(200, json=LLAMA_RESPONSE)
    )
    await gw.post(
        "/v1/chat/completions", json=_body(chat_template_kwargs={}), headers=_headers(rsa_keys)
    )

    assert json.loads(route.calls[0].request.content)["chat_template_kwargs"] == {}


# ── it stops being reported as dropped ─────────────────────────────────────


@respx.mock
async def test_it_is_no_longer_named_as_an_ignored_parameter(gw, rsa_keys):
    """The symptom repo2deck hit: the field was named in the header, correctly,
    because it genuinely never reached the engine."""
    respx.post(f"{BACKEND_URL}/v1/chat/completions").mock(
        return_value=Response(200, json=LLAMA_RESPONSE)
    )
    resp = await gw.post(
        "/v1/chat/completions",
        json=_body(chat_template_kwargs=NO_THINKING),
        headers=_headers(rsa_keys),
    )

    assert "chat_template_kwargs" not in resp.headers.get("x-prometheus-ignored-parameters", "")


@respx.mock
async def test_require_parameters_no_longer_refuses_it(gw, rsa_keys):
    """The workaround offered while this was pending turned the silent drop into
    a 400. Now the request simply works."""
    respx.post(f"{BACKEND_URL}/v1/chat/completions").mock(
        return_value=Response(200, json=LLAMA_RESPONSE)
    )
    resp = await gw.post(
        "/v1/chat/completions",
        json=_body(chat_template_kwargs=NO_THINKING, require_parameters=True),
        headers=_headers(rsa_keys),
    )

    assert resp.status_code == 200


# ── and the two that do nothing stay dropped ───────────────────────────────


@pytest.mark.parametrize("field", ["reasoning_effort", "reasoning_budget"])
@respx.mock
async def test_the_fields_the_engine_ignores_are_still_reported_as_dropped(gw, rsa_keys, field):
    """Measured, not assumed: at the top level these changed nothing against a
    running server — same output, same reasoning length, with and without.
    Declaring them would report as honoured what the engine discards, which is
    the inverse of the bug this fixes and harder to spot. `reasoning_effort`
    belongs *inside* `chat_template_kwargs`, where the template reads it."""
    respx.post(f"{BACKEND_URL}/v1/chat/completions").mock(
        return_value=Response(200, json=LLAMA_RESPONSE)
    )
    resp = await gw.post(
        "/v1/chat/completions", json=_body(**{field: "low"}), headers=_headers(rsa_keys)
    )

    assert field in resp.headers.get("x-prometheus-ignored-parameters", "")


def test_the_declared_field_is_not_an_extra():
    req = ChatCompletionRequest(
        model="small",
        messages=[{"role": "user", "content": "hola"}],
        chat_template_kwargs=NO_THINKING,
    )
    extras = set(req.model_dump().keys()) - set(ChatCompletionRequest.model_fields)
    assert not extras


def test_a_non_mapping_is_refused(gw=None):
    """`"enable_thinking=false"` as a string is a plausible mistake, and letting
    it through would reach the engine as a type it cannot render."""
    import pydantic

    with pytest.raises(pydantic.ValidationError):
        ChatCompletionRequest(
            model="small",
            messages=[{"role": "user", "content": "hola"}],
            chat_template_kwargs="enable_thinking=false",
        )
