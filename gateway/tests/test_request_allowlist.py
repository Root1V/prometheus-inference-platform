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
#
# PRM-127 changed the answer here and the change was deliberate. PRM-126 made an
# unrecognised field a 400 outright, copying OpenAI. That refuses a request the
# caller usually still wants served, and it breaks anything already sending a
# harmless extra. OpenRouter — a gateway over providers that differ in what they
# honour, which is this system's shape — routes anyway and lets the parameter be
# ignored, with `require_parameters` for callers who would rather fail. What it
# gets for free is discoverability; we have to supply that ourselves, which is
# what the header below is for.


@respx.mock
async def test_an_unknown_parameter_is_served_and_named_in_a_header(gw, rsa_keys):
    respx.post(f"{BACKEND_URL}/v1/chat/completions").mock(
        return_value=Response(200, json=LLAMA_RESPONSE)
    )
    resp = await gw.post("/v1/chat/completions", json=_body(seed=1), headers=_headers(rsa_keys))

    assert resp.status_code == 200, "the caller still wanted the completion"
    assert resp.headers["X-Prometheus-Ignored-Parameters"] == "seed"


@respx.mock
async def test_an_ignored_parameter_never_reaches_the_engine(gw, rsa_keys):
    """Accepting a field is not forwarding it. llama.cpp honours `seed`, so
    passing it through would silently change what the caller gets — and the
    allowlist exists (AC-5/AC-6) precisely so client-controlled fields do not
    reach the engine unexamined. Reporting it is the change; forwarding it is a
    separate decision nobody has made."""
    route = respx.post(f"{BACKEND_URL}/v1/chat/completions").mock(
        return_value=Response(200, json=LLAMA_RESPONSE)
    )
    await gw.post("/v1/chat/completions", json=_body(seed=1), headers=_headers(rsa_keys))

    assert "seed" not in json.loads(route.calls[0].request.content)


@respx.mock
async def test_every_ignored_parameter_is_named_not_just_the_first(gw, rsa_keys):
    respx.post(f"{BACKEND_URL}/v1/chat/completions").mock(
        return_value=Response(200, json=LLAMA_RESPONSE)
    )
    resp = await gw.post(
        "/v1/chat/completions",
        json=_body(seed=1, logit_bias={"1": 1}, presence_penalty=0.5),
        headers=_headers(rsa_keys),
    )

    assert resp.headers["X-Prometheus-Ignored-Parameters"] == "logit_bias, presence_penalty, seed"


@respx.mock
async def test_a_clean_request_carries_no_such_header(gw, rsa_keys):
    """An always-present header saying "nothing" is noise a client learns to
    skip, and then misses the one time it says something."""
    respx.post(f"{BACKEND_URL}/v1/chat/completions").mock(
        return_value=Response(200, json=LLAMA_RESPONSE)
    )
    resp = await gw.post("/v1/chat/completions", json=_body(), headers=_headers(rsa_keys))

    assert "X-Prometheus-Ignored-Parameters" not in resp.headers


async def test_require_parameters_turns_the_same_request_into_a_400(gw, rsa_keys):
    """OpenRouter's escape hatch: for a caller who would rather fail than be
    quietly given something else — reproducibility, structured extraction."""
    resp = await gw.post(
        "/v1/chat/completions",
        json=_body(seed=1, require_parameters=True),
        headers=_headers(rsa_keys),
    )

    assert resp.status_code == 400
    body = resp.json()
    assert body["type"].endswith("/unknown-parameter")
    assert "seed" in body["detail"]
    assert resp.headers["content-type"].startswith("application/problem+json")
    assert set(body) >= {"type", "title", "status", "detail", "instance", "request_id", "trace_id"}


@respx.mock
async def test_require_parameters_alone_is_not_itself_an_extra(gw, rsa_keys):
    """Asking to be told cannot be one of the things you are told about."""
    respx.post(f"{BACKEND_URL}/v1/chat/completions").mock(
        return_value=Response(200, json=LLAMA_RESPONSE)
    )
    resp = await gw.post(
        "/v1/chat/completions", json=_body(require_parameters=True), headers=_headers(rsa_keys)
    )

    assert resp.status_code == 200
    assert "X-Prometheus-Ignored-Parameters" not in resp.headers


async def test_a_wrong_value_is_still_a_422_not_a_400(gw, rsa_keys):
    """The two failures have different fixes — "that field does not exist" versus
    "that value is wrong" — so they must stay different errors."""
    resp = await gw.post(
        "/v1/chat/completions", json=_body(temperature=99.0), headers=_headers(rsa_keys)
    )

    assert resp.status_code == 422
    assert resp.json()["type"].endswith("/validation-error")


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


def test_every_handler_that_takes_a_request_body_checks_its_parameters():
    """PRM-127's rule lives in `_parameter_check`, but it has to be *called*, and
    it is called from four handlers.

    That shape is exactly what PRM-118 cost: a one-line rule repeated at nine
    call sites, wrong at four of them, and silent about it. A new endpoint that
    forgets this does not fail — it goes back to dropping parameters without
    saying so, which is the bug this whole item exists to close. So the rule that
    every body-taking handler checks its parameters lives here.
    """
    import re
    from pathlib import Path

    src = (
        Path(__file__).resolve().parents[2] / "gateway/src/prometheus_gateway/router.py"
    ).read_text()

    # Handlers whose signature takes one of the allowlist request models.
    handlers = re.findall(
        r"async def (\w+)\(\s*body: (?:ChatCompletionRequest|EmbeddingsRequest|"
        r"RerankRequest|ImageGenerationRequest)[^)]*\)(.*?)(?=\n    @router\.|\Z)",
        src,
        re.DOTALL,
    )
    assert len(handlers) >= 4, f"found {len(handlers)} body-taking handlers — pattern has drifted"

    missing = [name for name, body in handlers if "_parameter_check(request, body)" not in body]
    assert not missing, (
        f"these handlers take a request body and never check its parameters, so anything "
        f"outside their allowlist is dropped in silence again: {missing}"
    )
