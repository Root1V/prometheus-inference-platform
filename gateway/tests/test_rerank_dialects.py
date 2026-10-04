"""PRM-183: the rerank upstream shape is the engine's.

Implements: docs/roadmap.md — PRM-183.

`/v1/rerank`'s client contract is Cohere-shaped and unchanged. What changed is
that the shape sent upstream, and the shape read back, now depend on which engine
serves the model — which was always true and was not in code, because the
endpoint was built against llama.cpp and forwarded its body verbatim.

Both shapes in here were captured from running servers on 2026-10-03, not from
documentation: llama.cpp through this gateway, and TEI
(`cross-encoder/ms-marco-MiniLM-L-6-v2`) direct.
"""

from __future__ import annotations

import pytest
import respx
from httpx import ASGITransport, AsyncClient, Response

from prometheus_gateway.models import rerank_dialects
from prometheus_gateway.models.registry import ModelEntry, ModelRegistry
from prometheus_gateway.models.schemas import RerankRequest
from tests.conftest import make_token

TEI_URL = "http://127.0.0.1:18096"

# Captured from a running TEI: a bare array, `score`, and no usage of any kind.
TEI_RESPONSE = [
    {"index": 0, "score": 0.9993574},
    {"index": 1, "score": 4.0859417e-05},
]
# The same server with raw_scores: the logit, which is what C-01 §2 asked for.
TEI_RAW_RESPONSE = [
    {"index": 0, "score": 7.349291},
    {"index": 1, "score": -10.105332},
]


def _body(**over) -> RerankRequest:
    return RerankRequest(
        model="rr", query="dog on the sofa", documents=["A dog sleeps.", "A jacket."], **over
    )


# ── the dialects themselves ──────────────────────────────────────────────────


def test_an_unrecorded_engine_gets_no_dialect():
    """`None` rather than a guess — the line `payload_schema_for` takes too."""
    assert rerank_dialects.dialect_for("some-new-engine") is None
    assert rerank_dialects.dialect_for("hf_serve") is None


def test_llama_cpp_keeps_its_own_path_and_body():
    d = rerank_dialects.dialect_for("llama_cpp")
    assert d is not None and d.native
    assert d.path == "/v1/rerank"
    payload = d.request(_body(top_n=2))
    assert payload["query"] == "dog on the sofa"
    assert payload["documents"] == ["A dog sleeps.", "A jacket."]
    assert payload["top_n"] == 2


def test_tei_sends_texts_not_documents():
    """The difference that makes a TEI reranker unreachable without this."""
    d = rerank_dialects.dialect_for("tei")
    assert d is not None and not d.native
    assert d.path == "/rerank"
    payload = d.request(_body())
    assert payload["texts"] == ["A dog sleeps.", "A jacket."]
    assert "documents" not in payload
    assert payload["return_text"] is False


def test_tei_asks_for_raw_scores_only_when_requested():
    d = rerank_dialects.dialect_for("tei")
    assert "raw_scores" not in d.request(_body())
    assert d.request(_body(raw_scores=True))["raw_scores"] is True


def test_llama_cpp_passes_its_answer_through_untouched():
    """The existing contract, byte for byte."""
    d = rerank_dialects.dialect_for("llama_cpp")
    upstream = {"model": "rr", "object": "list", "usage": {}, "results": []}
    assert d.normalise(upstream, model="rr", estimated_prompt_tokens=9, top_n=None) is upstream


def test_tei_is_normalised_into_this_platforms_shape():
    d = rerank_dialects.dialect_for("tei")
    out = d.normalise(TEI_RESPONSE, model="rr", estimated_prompt_tokens=11, top_n=None)
    assert out["object"] == "list"
    assert out["model"] == "rr"
    assert out["results"] == [
        {"index": 0, "relevance_score": 0.9993574},
        {"index": 1, "relevance_score": 4.0859417e-05},
    ]


def test_a_tei_reranking_is_never_metered_at_zero():
    """The defect this module exists to stop.

    TEI reports no usage at all, and the handler meters from
    `resp_body["usage"]["prompt_tokens"]`. Without an estimate here a real
    request is billed as zero tokens and the row says so confidently — PRM-142's
    finding, one engine later.
    """
    d = rerank_dialects.dialect_for("tei")
    out = d.normalise(TEI_RESPONSE, model="rr", estimated_prompt_tokens=11, top_n=None)
    assert out["usage"]["prompt_tokens"] == 11
    assert out["usage"]["total_tokens"] == 11
    # And it says the number is an estimate rather than passing as counted.
    assert out["usage"]["prometheus_estimated"] is True


def test_top_n_is_applied_for_an_engine_that_has_no_top_n():
    """TEI's schema has no `top_n`, so the trim is ours — on a sorted ranking,
    because "the best n" is only meaningful once it is one."""
    d = rerank_dialects.dialect_for("tei")
    out = d.normalise(TEI_RESPONSE, model="rr", estimated_prompt_tokens=11, top_n=1)
    assert out["results"] == [{"index": 0, "relevance_score": 0.9993574}]


def test_raw_scores_survive_normalisation_unchanged():
    """A logit is not a probability and must not be rescaled on the way out."""
    d = rerank_dialects.dialect_for("tei")
    out = d.normalise(TEI_RAW_RESPONSE, model="rr", estimated_prompt_tokens=11, top_n=None)
    assert out["results"][0]["relevance_score"] == 7.349291
    assert out["results"][1]["relevance_score"] == -10.105332


@pytest.mark.parametrize(
    "upstream",
    [
        {"results": []},
        ["not a dict"],
        [{"index": 0}],
        [{"score": 0.5}],
        None,
    ],
)
def test_an_unreadable_shape_returns_none_rather_than_a_ranking(upstream):
    """No ranking is guessed from a body this dialect does not describe."""
    d = rerank_dialects.dialect_for("tei")
    assert d.normalise(upstream, model="rr", estimated_prompt_tokens=1, top_n=None) is None


def test_only_tei_advertises_raw_scores():
    assert rerank_dialects.engines_with_raw_scores() == frozenset({"tei"})


# ── through the route ────────────────────────────────────────────────────────


def _entry(entry_id: str, backend: str, url: str) -> ModelEntry:
    return ModelEntry(
        id=entry_id,
        path=f"/m/{entry_id}.gguf",
        context_length=4096,
        family="qwen3",
        quantization="Q4_K_M",
        backend_url=url,
        backend_status="active",
        node="local",
        modality="rerank",
        model_id=entry_id,
        model_slug=entry_id,
        backend=backend,
    )


@pytest.fixture
def app(settings):
    from prometheus_gateway.main import create_app

    registry = ModelRegistry.__new__(ModelRegistry)
    registry._models = {
        "tei-rr": _entry("tei-rr", "tei", TEI_URL),
        "mystery-rr": _entry("mystery-rr", "some-new-engine", TEI_URL),
    }
    return create_app(settings=settings, registry=registry)


@pytest.fixture
async def gw(app):
    from prometheus_gateway import db

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        await db.create_tables(db.get_engine())
        yield c


def _headers(rsa_keys) -> dict[str, str]:
    scope = "inference:read model:tei-rr model:mystery-rr"
    return {"Authorization": f"Bearer {make_token(rsa_keys['private'], scope=scope)}"}


@pytest.mark.asyncio
@respx.mock
async def test_a_tei_reranker_is_reachable_and_answers_in_our_shape(gw, rsa_keys):
    respx.get(f"{TEI_URL}/health").mock(return_value=Response(200))
    route = respx.post(f"{TEI_URL}/rerank").mock(return_value=Response(200, json=TEI_RESPONSE))

    r = await gw.post(
        "/v1/rerank",
        json={"model": "tei-rr", "query": "dog", "documents": ["a dog", "a jacket"]},
        headers=_headers(rsa_keys),
    )
    assert r.status_code == 200, r.text
    sent = route.calls[0].request.content
    assert b'"texts"' in sent, sent
    assert b'"documents"' not in sent, sent
    body = r.json()
    assert body["object"] == "list"
    assert body["results"][0]["relevance_score"] == 0.9993574
    assert body["usage"]["prompt_tokens"] > 0


@pytest.mark.asyncio
@respx.mock
async def test_an_unrecorded_engine_is_refused_with_its_own_type(gw, rsa_keys):
    respx.get(f"{TEI_URL}/health").mock(return_value=Response(200))
    r = await gw.post(
        "/v1/rerank",
        json={"model": "mystery-rr", "query": "dog", "documents": ["a dog"]},
        headers=_headers(rsa_keys),
    )
    assert r.status_code == 503
    assert r.json()["type"].endswith("/rerank-dialect-unknown")
    assert "some-new-engine" in r.json()["detail"]


@pytest.mark.asyncio
@respx.mock
async def test_an_unreadable_upstream_body_is_a_502_not_an_empty_ranking(gw, rsa_keys):
    respx.get(f"{TEI_URL}/health").mock(return_value=Response(200))
    respx.post(f"{TEI_URL}/rerank").mock(return_value=Response(200, json={"oops": True}))
    r = await gw.post(
        "/v1/rerank",
        json={"model": "tei-rr", "query": "dog", "documents": ["a dog"]},
        headers=_headers(rsa_keys),
    )
    assert r.status_code == 502
    assert r.json()["type"].endswith("/upstream-error")


# ── the two places this path lives ───────────────────────────────────────────


def test_the_manager_probes_the_same_rerank_path_the_gateway_forwards_to():
    """One fact, two packages, and a test instead of a shared dependency.

    The manager's readiness probe sends a real rerank to decide whether an
    instance is ready, so it needs the engine's path — the same path this
    gateway forwards to. Making manager-core import the gateway for two strings
    would be the wrong dependency, so they are stated twice and tied here.

    This is not hypothetical: the first TEI instance the manager launched came up
    healthy and was reported `not_ready`, because the probe asked `/v1/rerank`
    and TEI answers 404 there.
    """
    from prometheus_manager_core.readiness import _probe_body
    from prometheus_manager_core.registry import RegistryEntry

    for engine in ("llama_cpp", "tei"):
        entry = RegistryEntry(
            id="rr",
            path="",
            context_length=512,
            port=1,
            backend=engine,
            modality="rerank",
            model_slug="rr",
        )
        probe_path, _ = _probe_body(entry)
        dialect = rerank_dialects.dialect_for(engine)
        assert dialect is not None
        assert probe_path == dialect.path, (
            f"{engine}: the manager probes {probe_path} and the gateway forwards to {dialect.path}"
        )
