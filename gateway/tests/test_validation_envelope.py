"""PRM-188 — a validator's refusal leaves in the envelope, like every other error.

RM-65 put body-validation failures into the RFC 9457 envelope so an SDK could
type an error by `type` and correlate it by `request_id`. The handler worked for
the errors it was tested with and failed for a whole class it was not.

Pydantic attaches the original exception to every error a *validator* raises:
`{'type': 'value_error', 'ctx': {'error': ValueError('...')}}`. A `ValueError`
is not JSON-serialisable, so building the response threw inside the handler and
the request left as an unhandled exception — a 500, in no envelope, from the
code whose job is the envelope. A `ge=`/`le=` constraint carries `{'ge': 0}`
instead, which serialises, which is why the gap stayed invisible.

Two validators could reach it long before this was found, and both are in
`ChatCompletionRequest` today:

  * `role` outside {system, user, assistant, tool}
  * RM-09's rule that an image must be a `data:` URI — the SSRF guard, so the
    gateway answered its own security refusal with a 500.

What let it survive is the shape of the tests, not the absence of them: the
existing cases call the schema directly under `pytest.raises(ValidationError)`.
That proves the model refuses. It cannot say what the *route* answers. Every
test here goes over HTTP for that reason.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from prometheus_gateway.models.registry import ModelEntry, ModelRegistry
from tests.conftest import make_token

BACKEND_URL = "http://127.0.0.1:18103"
PROBLEM = "application/problem+json"


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
            modality="vision",
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


BAD_ROLE = {"model": "small", "messages": [{"role": "wizard", "content": "hi"}]}
HTTP_IMAGE = {
    "model": "small",
    "messages": [
        {
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": "http://evil.example.com/x.png"}}
            ],
        }
    ],
}


@pytest.mark.parametrize(
    ("name", "body"),
    [("a role the schema does not allow", BAD_ROLE), ("an http image url", HTTP_IMAGE)],
)
async def test_a_validators_refusal_is_a_422_in_the_envelope(gw, rsa_keys, name, body):
    """Before PRM-188 both of these left as an unhandled RequestValidationError."""
    resp = await gw.post("/v1/chat/completions", json=body, headers=_headers(rsa_keys))

    assert resp.status_code == 422, f"{name} must be a 422, not a 500"
    assert resp.headers["content-type"].startswith(PROBLEM)


async def test_the_ssrf_refusal_still_says_what_it_refused(gw, rsa_keys):
    """RM-09's guard is the reason this matters most: a 500 tells a caller the
    gateway broke, when what happened is that it protected itself."""
    resp = await gw.post("/v1/chat/completions", json=HTTP_IMAGE, headers=_headers(rsa_keys))

    assert "data: URI" in resp.json()["detail"]


async def test_the_body_carries_the_fields_an_sdk_types_on(gw, rsa_keys):
    resp = await gw.post("/v1/chat/completions", json=BAD_ROLE, headers=_headers(rsa_keys))

    body = resp.json()
    assert body["type"] == "https://prometheus.internal/errors/validation-error"
    assert body["title"] == "Validation Error"
    assert body["status"] == 422
    assert body["instance"] == "/v1/chat/completions"
    assert "request_id" in body and "trace_id" in body


async def test_the_per_field_errors_survive_the_encoding(gw, rsa_keys):
    """`errors` is the half that needed encoding, so it is the half worth
    pinning: it must still be there, and still describe the failure."""
    resp = await gw.post("/v1/chat/completions", json=BAD_ROLE, headers=_headers(rsa_keys))

    errors = resp.json()["errors"]
    assert errors, "the per-field detail is the reason RM-65 kept this key"
    assert errors[0]["type"] == "value_error"
    assert "role must be one of" in errors[0]["msg"]


async def test_the_validators_own_words_survive_the_encoding(gw, rsa_keys):
    """Where the message ends up, measured rather than assumed.

    `jsonable_encoder` renders the exception in `ctx.error` as `{}` — it is an
    object with no fields, not a string. That is FastAPI's own behaviour in its
    default handler, and it loses nothing, because Pydantic has already copied
    the validator's text into `msg`. So `msg` is what a caller reads and what
    this test pins; `ctx` is kept only because dropping a key to make a payload
    encode is how detail quietly goes missing.
    """
    resp = await gw.post("/v1/chat/completions", json=BAD_ROLE, headers=_headers(rsa_keys))

    err = resp.json()["errors"][0]
    assert "role must be one of" in err["msg"], "the validator's text has to reach the caller"
    assert "ctx" in err, "ctx is kept, not stripped"


async def test_a_field_constraint_error_is_unchanged(gw, rsa_keys):
    """The class that always worked has to keep working — this fix must not be
    paid for by the errors the handler already got right."""
    resp = await gw.post(
        "/v1/chat/completions",
        json={"model": "small"},  # messages is required
        headers=_headers(rsa_keys),
    )

    assert resp.status_code == 422
    assert resp.headers["content-type"].startswith(PROBLEM)
    assert resp.json()["errors"][0]["type"] == "missing"


async def test_every_error_in_a_multi_error_body_is_reported(gw, rsa_keys):
    """The http-image body raises two at once. A handler that serialised only
    the first would hide the rest, which is how the original bug felt from the
    outside — one visible symptom over a class of failures."""
    resp = await gw.post("/v1/chat/completions", json=HTTP_IMAGE, headers=_headers(rsa_keys))

    body = resp.json()
    assert len(body["errors"]) == 2, "the union tries both arms, and both failures are real"
    for err in body["errors"]:
        assert err["msg"] in body["detail"], "every error has to appear in the summary line"
