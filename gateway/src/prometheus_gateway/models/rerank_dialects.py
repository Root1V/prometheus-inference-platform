"""Per-engine `/v1/rerank` dialects — PRM-183.

`/v1/rerank`'s *client* contract is Cohere-shaped and does not change: one query,
N documents, scores back with the original indices. What changes is that the
**upstream** shape is now the engine's, which it always was in fact and was not
in code.

Measured against both engines on 2026-10-03, not read from documentation:

```
llama.cpp  POST /v1/rerank  {"model","query","documents","top_n"?}
  → {"model":…, "object":"list",
     "usage":{"prompt_tokens":176,"total_tokens":176},
     "results":[{"index":0,"relevance_score":0.9997715}, …]}

TEI        POST /rerank     {"query","texts","raw_scores"?,"return_text"?}
  → [{"index":0,"score":0.9993574}, {"index":1,"score":4.0859417e-05}]
  with raw_scores: [{"index":0,"score":7.349291}, {"index":1,"score":-10.105332}]
```

Four differences, and only the first is cosmetic:

1. `score` against `relevance_score`.
2. **TEI answers a bare array**, with no envelope at all — no `object`, no `model`.
3. **TEI reports no `usage`.** The handler reads `resp_body["usage"]["prompt_tokens"]`
   to meter and to settle a budget reservation, so a TEI reranker would have been
   metered at **zero tokens** and written a junk row. That is PRM-142's defect
   exactly — *"the backend's own usage object → 0 tokens, a junk row"* — and it is
   the reason this module exists rather than a `if engine == "tei"` at the call
   site.
4. **TEI has no `top_n`.** Its schema is `query`/`texts`/`raw_scores`/`return_text`
   and nothing else, so trimming is ours to do for that engine.

And one capability that is not a difference but a gap: `raw_scores` returns the
logit before the sigmoid, which is what Centinela asked for in `C-01 §2` because
their scores saturate near 1.0 and cannot be calibrated. llama.cpp has no such
flag. A parameter one engine honours and another cannot is what
`X-Prometheus-Ignored-Parameters` already exists to report, so it is reported
rather than silently dropped.

**An unknown engine gets `None`**, not a guess. `payload_schema_for` takes the
same line for the same reason: a reranker on some new engine is not llama.cpp's
shape just because the last one was.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


class _RerankBody(Protocol):
    query: str
    documents: list[str]
    top_n: int | None
    raw_scores: bool | None


@dataclass(frozen=True)
class RerankDialect:
    """How one engine wants a rerank asked, and how its answer is read back."""

    engine: str
    path: str
    supports_raw_scores: bool
    # True when the engine already answers in this platform's own shape, so the
    # body is forwarded to the client untouched. Only llama.cpp does, because
    # this endpoint was built against it.
    native: bool

    def request(self, body: _RerankBody) -> dict[str, Any]:
        if self.native:
            payload: dict[str, Any] = {
                "model": "",  # filled by the caller, which knows the served name
                "query": body.query,
                "documents": body.documents,
            }
            if body.top_n is not None:
                payload["top_n"] = body.top_n
            return payload
        payload = {"query": body.query, "texts": body.documents, "return_text": False}
        if self.supports_raw_scores and getattr(body, "raw_scores", None):
            payload["raw_scores"] = True
        return payload

    def normalise(
        self, upstream: Any, *, model: str, estimated_prompt_tokens: int, top_n: int | None
    ) -> dict[str, Any] | None:
        """The engine's answer in this platform's shape, or `None` if unreadable.

        `None` means "this is not the shape this dialect describes" and the caller
        must surface the body rather than invent a reranking from it — the same
        rule `_backend_refused` follows one layer up.
        """
        if self.native:
            return upstream if isinstance(upstream, dict) else None
        if not isinstance(upstream, list):
            return None
        results: list[dict[str, Any]] = []
        for item in upstream:
            if not isinstance(item, dict) or "index" not in item or "score" not in item:
                return None
            results.append({"index": item["index"], "relevance_score": item["score"]})
        if top_n is not None:
            # TEI has no top_n, so the trim happens here. Sorted first, because
            # "the best n" is only meaningful on a ranking and this endpoint's
            # contract does not promise the engine returned one.
            results.sort(key=lambda r: r["relevance_score"], reverse=True)
            results = results[:top_n]
        return {
            "model": model,
            "object": "list",
            # Estimated, and said so rather than written as if it were counted —
            # the convention `/v1/models/{m}/predict` already follows for engines
            # that report no usage. Without this the row is a confident zero.
            "usage": {
                "prompt_tokens": estimated_prompt_tokens,
                "total_tokens": estimated_prompt_tokens,
                "prometheus_estimated": True,
            },
            "results": results,
        }


_DIALECTS: dict[str, RerankDialect] = {
    "llama_cpp": RerankDialect(
        engine="llama_cpp", path="/v1/rerank", supports_raw_scores=False, native=True
    ),
    "tei": RerankDialect(engine="tei", path="/rerank", supports_raw_scores=True, native=False),
}


def dialect_for(engine: str) -> RerankDialect | None:
    """The dialect for *engine*, or `None` when this platform has not recorded one."""
    return _DIALECTS.get(engine)


def engines_with_raw_scores() -> frozenset[str]:
    """Engines whose rerank can return the pre-sigmoid logit."""
    return frozenset(e for e, d in _DIALECTS.items() if d.supports_raw_scores)
