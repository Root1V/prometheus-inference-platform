"""POST /v1/models/{model}/predict — PRM-136.

Every other route here is OpenAI-shaped because every task it serves has an
OpenAI endpoint to be shaped like. Classification does not, and neither does
the class of non-autoregressive "System 1" decision models — Jev, Laya — that
answer typed questions with calibrated probabilities. There is no OpenAI
request body for that.

So the body passes through untouched. What does not pass through is everything
that makes this a gateway, and that is what these tests pin: the model still
resolves, the scope is still required, a model that has its own endpoint is
still refused here, and the request is still metered.
"""

from __future__ import annotations

import httpx
import pytest
import respx
from httpx import ASGITransport, AsyncClient, Response

from prometheus_gateway.models.registry import ModelEntry, ModelRegistry
from tests.conftest import make_token

pytestmark = pytest.mark.asyncio

BACKEND_URL = "http://127.0.0.1:18096"

# What hf-serve actually returns for text-classification, captured from a live
# server (distilbert-base-uncased-finetuned-sst-2-english on /predict).
PREDICT_RESPONSE = [{"label": "POSITIVE", "score": 0.991233766078949}]


def _entry(entry_id: str, modality: str) -> ModelEntry:
    return ModelEntry(
        id=entry_id,
        path=f"/m/{entry_id}",
        context_length=512,
        family="modernbert",
        quantization="",
        backend_url=BACKEND_URL,
        backend_status="active",
        node="lab",
        modality=modality,
        model_id=entry_id,
        model_slug=entry_id,
    )


@pytest.fixture
def app(settings):
    from prometheus_gateway.main import create_app

    registry = ModelRegistry.__new__(ModelRegistry)
    registry._models = {
        "clf": _entry("clf", "classification"),
        "chatty": _entry("chatty", "text"),
    }
    return create_app(settings=settings, registry=registry)


@pytest.fixture
async def gw(app):
    from prometheus_gateway import db

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        await db.create_tables(db.get_engine())
        yield c


def _headers(rsa_keys, scope: str = "inference:read model:clf model:chatty") -> dict[str, str]:
    return {"Authorization": f"Bearer {make_token(rsa_keys['private'], scope=scope)}"}


# ── The body is the backend's, in both directions ────────────────────────────


@respx.mock
async def test_the_body_reaches_the_backend_untouched(gw, rsa_keys):
    """No schema, no field renaming, no defaults added.

    The whole point of this route is that the gateway does not know what this
    engine's request looks like. A body it reshaped would be a body it had an
    opinion about.
    """
    route = respx.post(f"{BACKEND_URL}/predict").mock(
        return_value=Response(200, json=PREDICT_RESPONSE)
    )
    sent = {"inputs": "el servicio fue excelente", "top_k": 3, "anything_at_all": {"n": 1}}

    r = await gw.post("/v1/models/clf/predict", json=sent, headers=_headers(rsa_keys))

    assert r.status_code == 200
    assert r.json() == PREDICT_RESPONSE
    assert (
        route.calls[0].request.content.decode()
        and httpx.Request("POST", "http://x", json=sent).content == route.calls[0].request.content
    )


@respx.mock
async def test_the_backends_status_code_is_not_rewritten(gw, rsa_keys):
    """A 422 from the engine is the engine's answer, not a gateway failure.

    PRM-142 narrowed what this test asserts, deliberately. It used to require
    the engine's error *body* back verbatim too — PRM-136 pinned that on the
    same reasoning as the success path. Axonium's A-28 took that apart: the
    body of a pass-through request is the backend's, but the error envelope is
    policy, and forwarding theirs handed callers a 422 with no `type`, no
    `request_id` and no `trace_id`.

    So the status is still the engine's and the content still survives — under
    `backend_error`, inside our envelope. See the PRM-142 tests below.
    """
    respx.post(f"{BACKEND_URL}/predict").mock(
        return_value=Response(422, json={"detail": "inputs is required"})
    )

    r = await gw.post("/v1/models/clf/predict", json={}, headers=_headers(rsa_keys))

    assert r.status_code == 422
    assert r.json()["backend_error"] == {"detail": "inputs is required"}


# ── What does not pass through ───────────────────────────────────────────────


@respx.mock
async def test_a_model_with_its_own_endpoint_is_refused_here(gw, rsa_keys):
    """The inverse of every other handler's modality check, and on purpose.

    Without it a chat model is reachable two ways, with two billing paths and
    two rate-limit buckets — and the one that bills correctly is whichever the
    caller did not use.
    """
    r = await gw.post("/v1/models/chatty/predict", json={"x": 1}, headers=_headers(rsa_keys))

    assert r.status_code == 400
    assert r.json()["title"] == "Modality Mismatch"


async def test_an_unknown_model_is_a_400_not_a_proxy_attempt(gw, rsa_keys):
    r = await gw.post("/v1/models/nope/predict", json={"x": 1}, headers=_headers(rsa_keys))

    assert r.status_code == 400
    assert r.json()["title"] == "Unknown Model"


async def test_the_model_grant_is_enforced_like_everywhere_else(gw, rsa_keys):
    """RM-07 deny-by-default. A pass-through route that skipped this would be a
    way around every per-model grant on the platform.
    """
    r = await gw.post(
        "/v1/models/clf/predict",
        json={"inputs": "x"},
        headers=_headers(rsa_keys, scope="inference:read model:chatty"),
    )

    assert r.status_code == 403
    assert "not authorized" in r.json()["detail"]


async def test_inference_read_is_still_required(gw, rsa_keys):
    r = await gw.post(
        "/v1/models/clf/predict",
        json={"inputs": "x"},
        headers=_headers(rsa_keys, scope="model:clf"),
    )

    assert r.status_code == 403


@respx.mock
async def test_an_unreachable_backend_is_a_503_with_the_usual_envelope(gw, rsa_keys):
    respx.post(f"{BACKEND_URL}/predict").mock(side_effect=httpx.ConnectError("refused"))

    r = await gw.post("/v1/models/clf/predict", json={"inputs": "x"}, headers=_headers(rsa_keys))

    assert r.status_code == 503
    body = r.json()
    assert body["title"] == "Backend Unavailable"
    assert "trace_id" in body


# ── Still metered ────────────────────────────────────────────────────────────


@respx.mock
async def test_the_request_is_recorded_as_usage(gw, rsa_keys):
    """The engine reports no usage for these tasks, so the input is measured
    here. A route that forwarded without recording would be free inference.
    """
    from datetime import date, timezone

    from prometheus_gateway import db

    respx.post(f"{BACKEND_URL}/predict").mock(return_value=Response(200, json=PREDICT_RESPONSE))

    await gw.post(
        "/v1/models/clf/predict",
        json={"inputs": "una frase lo bastante larga como para estimar algo"},
        headers=_headers(rsa_keys),
    )

    from datetime import datetime

    rows = await db.query_usage_day(datetime.now(tz=timezone.utc).date())
    predicted = [r for r in rows if r.model_id == "clf"]
    assert predicted, f"no usage row for the predict call: {rows}"
    assert predicted[0].prompt_tokens > 0
    assert predicted[0].completion_tokens == 0
    assert isinstance(predicted[0].day, date)


# ── The bucket PRM-129 taught us not to forget ───────────────────────────────


async def test_the_predict_route_has_its_own_rate_limit_bucket():
    """PRM-129: `/v1/embeddings` and `/v1/rerank` shared `default` for months
    because neither was on the endpoint map, and a copilot missed its pilot
    capacity by 5%. This path carries the model name, so an exact-match map
    could never have seen it.
    """
    from prometheus_gateway.rate_limit_middleware import _endpoint_slug

    assert _endpoint_slug("/v1/models/clf/predict") == "predict"
    assert _endpoint_slug("/v1/models/some-other-model/predict") == "predict"
    # Not everything under /v1/models/ — the catalog listing is not inference.
    assert _endpoint_slug("/v1/models") != "predict"


# ── PRM-137: zero-shot, where the labels are the caller's ────────────────────


@respx.mock
async def test_a_zero_shot_model_routes_through_the_same_pass_through(gw, rsa_keys, app):
    """`zero_shot` differs from `classification` in where the labels come from:
    baked into the checkpoint, or supplied per call. Both have no OpenAI shape,
    so both take the same route — and the labels ride in the body untouched.
    """
    from prometheus_gateway.models.registry import ModelEntry

    app.state.registry._models["zs"] = ModelEntry(
        id="zs",
        path="/m/zs",
        context_length=512,
        family="modernbert",
        quantization="",
        backend_url=BACKEND_URL,
        backend_status="active",
        node="lab",
        modality="zero_shot",
        model_id="zs",
        model_slug="zs",
    )
    route = respx.post(f"{BACKEND_URL}/predict").mock(
        return_value=Response(
            200, json={"labels": ["facturacion", "ventas"], "scores": [0.84, 0.16]}
        )
    )

    r = await gw.post(
        "/v1/models/zs/predict",
        json={
            "inputs": "nos cobraron dos veces",
            "parameters": {"candidate_labels": ["facturacion", "ventas"]},
        },
        headers={
            "Authorization": f"Bearer {make_token(rsa_keys['private'], scope='inference:read model:zs')}"
        },
    )

    assert r.status_code == 200
    assert r.json()["labels"] == ["facturacion", "ventas"]
    forwarded = route.calls[0].request.content.decode()
    assert "candidate_labels" in forwarded, "the options must reach the engine"
    assert "facturacion" in forwarded


# ── A refused request is not a served one — PRM-142 ──────────────────────────
#
# Axonium measured a 422 from this route arriving with no `type`, no
# `request_id` and no `trace_id`, content-type `application/json`, and made the
# argument with the sentence PRM-136 wrote above: "the shape is the backend's;
# the policy is ours". An error envelope is what makes a failure correlatable,
# typable, and classifiable as retryable — that is policy, and it was leaking
# out with the body.
#
# The second half is ours and they did not find it: the same request was
# *billed*. This route estimates tokens from the request body, so a refusal
# produced a real, non-zero charge — measured live at 6 prompt tokens and
# 1.2e-07 USD, with `termination_reason: "complete"`.

# What hf-serve returns when the body is missing a field it requires — captured
# from the live server that prompted A-28.
ENGINE_422 = {
    "detail": [
        {
            "type": "missing",
            "loc": ["body", "inputs"],
            "msg": "Field required",
            "input": {"campo_inventado": "x"},
        }
    ],
    "body": {"campo_inventado": "x"},
}


@respx.mock
async def test_a_backend_rejection_comes_back_in_our_envelope(gw, rsa_keys):
    """The engine's 422 keeps its status and its content, inside our envelope."""
    respx.post(f"{BACKEND_URL}/predict").mock(return_value=Response(422, json=ENGINE_422))

    r = await gw.post(
        "/v1/models/clf/predict",
        json={"campo_inventado": "x"},
        headers=_headers(rsa_keys),
    )

    assert r.status_code == 422, "the caller's body was wrong, so the status is still theirs"
    assert r.headers["content-type"].startswith("application/problem+json")
    body = r.json()
    assert body["type"].endswith("/predict-backend-rejected")
    assert body["request_id"] and body["request_id"] != "unknown"
    assert body["trace_id"]
    assert body["instance"] == "/v1/models/clf/predict"
    # The engine's own error survives verbatim, under an extension member
    # rather than stuffed into `detail` — RFC 9457 defines `detail` as
    # human-readable text, and a caller needs the structure.
    assert body["backend_error"] == ENGINE_422
    assert isinstance(body["detail"], str)


@respx.mock
async def test_a_rejected_request_is_not_billed(gw, rsa_keys):
    """The row this used to leave said 6 tokens, a real cost, and "complete"."""
    from datetime import datetime, timezone

    from prometheus_gateway import db

    respx.post(f"{BACKEND_URL}/predict").mock(return_value=Response(422, json=ENGINE_422))

    await gw.post(
        "/v1/models/clf/predict",
        json={"campo_inventado": "x", "y": "texto suficiente para estimar tokens"},
        headers=_headers(rsa_keys),
    )

    rows = await db.query_usage_day(datetime.now(tz=timezone.utc).date())
    assert not [r for r in rows if r.model_id == "clf"], (
        "a request the engine refused was metered and billed like one it served"
    )


@respx.mock
async def test_a_backend_failure_is_an_upstream_error_not_a_rejection(gw, rsa_keys):
    """A 500 from the engine is ours to own, not the caller's body to blame."""
    respx.post(f"{BACKEND_URL}/predict").mock(
        return_value=Response(500, json={"detail": "CUDA out of memory"})
    )

    r = await gw.post(
        "/v1/models/clf/predict", json={"inputs": "hola"}, headers=_headers(rsa_keys)
    )

    assert r.status_code == 502, "an engine failure is not a 500 the caller caused"
    body = r.json()
    assert body["type"].endswith("/upstream-error")
    assert body["backend_status"] == 500
    assert body["backend_error"] == {"detail": "CUDA out of memory"}


@respx.mock
async def test_a_served_request_is_still_billed(gw, rsa_keys):
    """The guard must refuse refusals, not stop billing."""
    from datetime import datetime, timezone

    from prometheus_gateway import db

    respx.post(f"{BACKEND_URL}/predict").mock(return_value=Response(200, json=PREDICT_RESPONSE))

    r = await gw.post(
        "/v1/models/clf/predict",
        json={"inputs": "una frase lo bastante larga como para estimar algo"},
        headers=_headers(rsa_keys),
    )

    assert r.status_code == 200
    rows = await db.query_usage_day(datetime.now(tz=timezone.utc).date())
    assert [r for r in rows if r.model_id == "clf"], "the 2xx path stopped billing"
