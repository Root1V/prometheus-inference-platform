"""PRM-126 — structured outputs, and an allowlist that says what it dropped.

The request schema is an allowlist on purpose (AC-5/AC-6: client-controlled
fields must not reach the engine unexamined). What was wrong was the silence.
Pydantic's default is to discard an unrecognised field, and llama.cpp accepts
unknown fields without complaint, so a client asking for `response_format` got
unconstrained prose and no signal at all — the guide even documented it as
"silently dropped if sent", which made it a decision rather than an oversight,
and the decision was the wrong one.

Measured against a live backend before writing any of this: llama.cpp honours
`response_format` with a JSON schema (returned `{"capital": "Lima"}` for a
two-field schema), so the capability was there and only the gateway withheld it.
"""

from __future__ import annotations

import json

import pytest
import respx
from httpx import ASGITransport, AsyncClient, Response

from prometheus_gateway.models.registry import ModelEntry, ModelRegistry
from tests.conftest import make_token

BACKEND_URL = "http://127.0.0.1:18099"

SCHEMA = {
    "type": "json_schema",
    "json_schema": {
        "name": "capital",
        "schema": {
            "type": "object",
            "properties": {"capital": {"type": "string"}},
            "required": ["capital"],
            "additionalProperties": False,
        },
    },
}

LLAMA_RESPONSE = {
    "id": "x",
    "object": "chat.completion",
    "model": "small",
    "choices": [
        {
            "index": 0,
            "message": {"role": "assistant", "content": '{"capital":"Lima"}'},
            "finish_reason": "stop",
        }
    ],
    "usage": {"prompt_tokens": 5, "completion_tokens": 7, "total_tokens": 12},
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


# ── response_format reaches the engine ─────────────────────────────────────


@respx.mock
async def test_response_format_is_forwarded_to_the_backend(gw, rsa_keys):
    route = respx.post(f"{BACKEND_URL}/v1/chat/completions").mock(
        return_value=Response(200, json=LLAMA_RESPONSE)
    )
    resp = await gw.post(
        "/v1/chat/completions", json=_body(response_format=SCHEMA), headers=_headers(rsa_keys)
    )

    assert resp.status_code == 200
    sent = json.loads(route.calls[0].request.content)
    assert sent["response_format"] == SCHEMA, (
        "the schema has to arrive intact or it constrains nothing"
    )


@respx.mock
async def test_omitting_response_format_sends_no_such_field(gw, rsa_keys):
    """An absent option must not become a present null — llama.cpp would have to
    decide what a null format means, and that is not a decision to delegate."""
    route = respx.post(f"{BACKEND_URL}/v1/chat/completions").mock(
        return_value=Response(200, json=LLAMA_RESPONSE)
    )
    await gw.post("/v1/chat/completions", json=_body(), headers=_headers(rsa_keys))

    assert "response_format" not in json.loads(route.calls[0].request.content)


# ── the allowlist stops being silent ───────────────────────────────────────


async def test_an_unknown_parameter_is_a_400_that_names_it(gw, rsa_keys):
    resp = await gw.post(
        "/v1/chat/completions", json=_body(defnitely_not_a_field=1), headers=_headers(rsa_keys)
    )

    assert resp.status_code == 400
    body = resp.json()
    assert body["type"].endswith("/unknown-parameter")
    assert "defnitely_not_a_field" in body["detail"], "naming it is the whole point"


async def test_every_rejected_parameter_is_named_not_just_the_first(gw, rsa_keys):
    """A client fixing them one round trip at a time is a client we made wait."""
    resp = await gw.post(
        "/v1/chat/completions",
        json=_body(seed=1, logit_bias={"1": 1}, presence_penalty=0.5),
        headers=_headers(rsa_keys),
    )

    assert resp.status_code == 400
    detail = resp.json()["detail"]
    for name in ("seed", "logit_bias", "presence_penalty"):
        assert name in detail


async def test_a_wrong_value_is_still_a_422_not_a_400(gw, rsa_keys):
    """The two failures have different fixes — "that field does not exist" versus
    "that value is out of range" — so they must stay different errors. RM-65's
    envelope and the 422 contract are unchanged for the second."""
    resp = await gw.post(
        "/v1/chat/completions", json=_body(temperature=99.0), headers=_headers(rsa_keys)
    )

    assert resp.status_code == 422
    assert resp.json()["type"].endswith("/validation-error")


async def test_the_error_keeps_the_problem_envelope(gw, rsa_keys):
    """RM-65: an SDK types errors by `type` and correlates by `request_id`. A new
    status code is not a licence to drop the shape everything else uses."""
    resp = await gw.post("/v1/chat/completions", json=_body(nope=1), headers=_headers(rsa_keys))

    body = resp.json()
    assert resp.headers["content-type"].startswith("application/problem+json")
    assert set(body) >= {"type", "title", "status", "detail", "instance", "request_id", "trace_id"}
    assert body["status"] == 400


@respx.mock
async def test_the_documented_fields_all_still_pass(gw, rsa_keys):
    """The blast radius, asserted: forbidding extras must not reject anything the
    guide says is accepted."""
    respx.post(f"{BACKEND_URL}/v1/chat/completions").mock(
        return_value=Response(200, json=LLAMA_RESPONSE)
    )
    resp = await gw.post(
        "/v1/chat/completions",
        json=_body(
            stream=False,
            max_tokens=16,
            temperature=0.7,
            top_p=0.9,
            stop=["\n"],
            tools=[{"type": "function", "function": {"name": "f", "parameters": {}}}],
            tool_choice="auto",
            response_format=SCHEMA,
        ),
        headers=_headers(rsa_keys),
    )
    assert resp.status_code == 200
