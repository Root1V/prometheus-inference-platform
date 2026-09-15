"""Tests for POST /v1/rerank — PRM-106.

A reranker is a cross-encoder, not a text generator. Serving it as one is how
this reached us: a client filed three bugs — logprobs discarded, requests
"hanging" 30-60s, and a chat template needing manual prefill — which were one
problem wearing three hats. These tests pin the endpoint that makes all three
go away, and the modality rules that stop the wrong model reaching it.
"""

from __future__ import annotations

import httpx
import pytest
import respx
from httpx import ASGITransport, AsyncClient, Response

from prometheus_gateway.models.registry import ModelEntry, ModelRegistry
from tests.conftest import make_token

pytestmark = pytest.mark.asyncio

BACKEND_URL = "http://127.0.0.1:18095"

# The shape llama.cpp actually returns, captured from a live --reranking server.
RERANK_RESPONSE = {
    "model": "rr",
    "object": "list",
    "usage": {"prompt_tokens": 265, "total_tokens": 265},
    "results": [
        {"index": 0, "relevance_score": 0.9999444484710693},
        {"index": 2, "relevance_score": 0.9991564750671387},
        {"index": 1, "relevance_score": 0.00019684404833242297},
    ],
}


def _entry(entry_id: str, modality: str) -> ModelEntry:
    return ModelEntry(
        id=entry_id,
        path=f"/m/{entry_id}.gguf",
        context_length=4096,
        family="qwen3",
        quantization="Q4_K_M",
        backend_url=BACKEND_URL,
        backend_status="active",
        node="local",
        modality=modality,
        model_id=entry_id,
        model_slug=entry_id,
    )


@pytest.fixture
def app(settings):
    from prometheus_gateway.main import create_app

    registry = ModelRegistry.__new__(ModelRegistry)
    registry._models = {
        "rr": _entry("rr", "rerank"),
        "chatty": _entry("chatty", "text"),
    }
    return create_app(settings=settings, registry=registry)


@pytest.fixture
async def gw(app):
    from prometheus_gateway import db

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        await db.create_tables(db.get_engine())
        yield c


def _headers(rsa_keys, scope: str = "inference:read model:rr model:chatty") -> dict[str, str]:
    return {"Authorization": f"Bearer {make_token(rsa_keys['private'], scope=scope)}"}


def _body(model: str = "rr") -> dict:
    return {
        "model": model,
        "query": "What is a reranker?",
        "documents": [
            "A reranker reorders candidates by relevance.",
            "The capital of Peru is Lima.",
            "Rerankers score query-document pairs.",
        ],
    }


# ── The scores the client had to rebuild from logprobs ─────────────────────


@respx.mock
async def test_scores_come_back_with_their_original_indices(gw, rsa_keys):
    """`relevance_score` is the P(yes)/(P(yes)+P(no)) the client was computing
    by hand. `index` is what makes the answer usable: it maps back to the
    caller's own list, so a reordered result is still attributable."""
    respx.post(f"{BACKEND_URL}/v1/rerank").mock(return_value=Response(200, json=RERANK_RESPONSE))
    resp = await gw.post("/v1/rerank", json=_body(), headers=_headers(rsa_keys))

    assert resp.status_code == 200
    results = resp.json()["results"]
    assert [r["index"] for r in results] == [0, 2, 1]
    # Ordered by score, best first — and the irrelevant document is far away,
    # not marginally lower. A binary yes/no could not express that.
    assert results[0]["relevance_score"] > results[-1]["relevance_score"]
    assert results[-1]["relevance_score"] < 0.01


@respx.mock
async def test_the_whole_document_set_is_one_request(gw, rsa_keys):
    """The rate-limit half of the bug report. Scoring N documents through chat
    completions cost N requests against a 60 RPM budget; here it is one."""
    route = respx.post(f"{BACKEND_URL}/v1/rerank").mock(
        return_value=Response(200, json=RERANK_RESPONSE)
    )
    await gw.post("/v1/rerank", json=_body(), headers=_headers(rsa_keys))

    assert route.call_count == 1
    sent = httpx.Response(200, request=route.calls[0].request).request.content
    import json as _json

    payload = _json.loads(sent)
    assert len(payload["documents"]) == 3
    assert payload["query"] == "What is a reranker?"


@respx.mock
async def test_top_n_is_forwarded_only_when_asked_for(gw, rsa_keys):
    route = respx.post(f"{BACKEND_URL}/v1/rerank").mock(
        return_value=Response(200, json=RERANK_RESPONSE)
    )
    import json as _json

    await gw.post("/v1/rerank", json=_body(), headers=_headers(rsa_keys))
    assert "top_n" not in _json.loads(route.calls[0].request.content)

    await gw.post("/v1/rerank", json={**_body(), "top_n": 2}, headers=_headers(rsa_keys))
    assert _json.loads(route.calls[1].request.content)["top_n"] == 2


# ── Modality, in both directions ───────────────────────────────────────────


@respx.mock
async def test_a_chat_model_is_refused_here(gw, rsa_keys):
    """Forwarding a rerank request to a text model would not fail — llama.cpp
    would answer 501, or worse, some engine would generate something."""
    resp = await gw.post("/v1/rerank", json=_body("chatty"), headers=_headers(rsa_keys))
    assert resp.status_code == 400
    assert resp.json()["type"].endswith("/modality-mismatch")


@respx.mock
async def test_a_rerank_model_is_refused_by_chat_completions(gw, rsa_keys):
    """The other direction, which is how the client ended up here. RM-66 made
    this a 400; before it, the weights ran and billed for confident nonsense."""
    resp = await gw.post(
        "/v1/chat/completions",
        json={"model": "rr", "messages": [{"role": "user", "content": "hi"}]},
        headers=_headers(rsa_keys),
    )
    assert resp.status_code == 400
    assert resp.json()["type"].endswith("/modality-mismatch")


# ── The ordinary gateway guarantees ────────────────────────────────────────


async def test_an_unknown_model_is_a_400_not_a_404(gw, rsa_keys):
    resp = await gw.post("/v1/rerank", json=_body("nope"), headers=_headers(rsa_keys))
    assert resp.status_code == 400
    assert resp.json()["type"].endswith("/unknown-model")


async def test_a_token_without_the_model_scope_is_refused(gw, rsa_keys):
    resp = await gw.post(
        "/v1/rerank", json=_body(), headers=_headers(rsa_keys, scope="inference:read")
    )
    assert resp.status_code == 403


async def test_no_token_at_all_is_refused(gw):
    resp = await gw.post("/v1/rerank", json=_body())
    assert resp.status_code == 401


async def test_an_empty_document_list_is_rejected_before_the_backend(gw, rsa_keys):
    """Nothing to score is a caller mistake, and forwarding it would spend a
    backend round-trip to find that out."""
    resp = await gw.post(
        "/v1/rerank", json={**_body(), "documents": []}, headers=_headers(rsa_keys)
    )
    assert resp.status_code == 400
    assert resp.json()["type"].endswith("/validation-error")


@respx.mock
async def test_an_unreachable_backend_is_503(gw, rsa_keys):
    respx.post(f"{BACKEND_URL}/v1/rerank").mock(side_effect=httpx.ConnectError("refused"))
    resp = await gw.post("/v1/rerank", json=_body(), headers=_headers(rsa_keys))
    assert resp.status_code == 503
    assert resp.json()["type"].endswith("/backend-unavailable")


# ── Accounting ─────────────────────────────────────────────────────────────


@respx.mock
async def test_the_request_is_billed_and_recorded_as_rerank(gw, rsa_keys):
    """Embeddings and images were both blind to usage recording once (RM-60).
    A new request type is exactly when that happens again."""
    from datetime import datetime, timezone

    from prometheus_gateway import db

    respx.post(f"{BACKEND_URL}/v1/rerank").mock(return_value=Response(200, json=RERANK_RESPONSE))
    await gw.post("/v1/rerank", json=_body(), headers=_headers(rsa_keys))

    today = datetime.now(timezone.utc).date()
    events = await db.query_usage_events_range(today, today)
    assert len(events) == 1
    assert events[0].request_kind == "rerank"
    # A reranker generates nothing; the cost is all on the prompt side.
    assert events[0].prompt_tokens == 265
    assert events[0].completion_tokens == 0
