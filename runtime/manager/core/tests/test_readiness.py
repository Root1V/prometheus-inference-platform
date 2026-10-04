"""Readiness: did the model answer, or did a port open? — PRM-150."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from prometheus_manager_core import readiness
from prometheus_manager_core.registry import MODALITIES, RegistryEntry


def _entry(modality: str, backend: str = "llama_cpp") -> RegistryEntry:
    return RegistryEntry(
        id="probe-model",
        path="/models/probe.gguf",
        context_length=512,
        port=9099,
        backend=backend,
        modality=modality,
        model_id="probe-model",
        model_slug="probe-model",
    )


# ── The verdict is three-valued, and `skipped` is not a pass ─────────────────


def test_a_2xx_is_ready():
    with patch("prometheus_manager_core.readiness.httpx.post") as post:
        post.return_value = MagicMock(status_code=200)
        verdict = readiness.check(_entry("text"), "127.0.0.1", 9099)
    assert verdict.ready is True
    assert verdict.failed is False
    assert "200" in verdict.detail


def test_image_is_skipped_and_skipped_is_not_ready():
    """The distinction that matters: no probe ran, so nothing was proven.

    A caller that reads `skipped` as a pass is the bug this shape prevents —
    `ready` is False and `failed` is False at the same time, on purpose.
    """
    verdict = readiness.check(_entry("image", backend="sd_cpp"), "127.0.0.1", 9099)
    assert verdict.skipped is True
    assert verdict.ready is False
    assert verdict.failed is False


def test_an_http_error_is_a_failure_and_carries_the_engine_s_words():
    """What an operator reads to find out what is wrong — this is the hf-serve
    case: the engine is up and cannot do the task it was launched for."""
    with (
        patch("prometheus_manager_core.readiness.httpx.post") as post,
        patch("prometheus_manager_core.readiness.time.sleep"),
    ):
        post.return_value = MagicMock(
            status_code=422, text='{"detail":[{"loc":["body","inputs"],"msg":"Field required"}]}'
        )
        verdict = readiness.check(_entry("classification", backend="hf_serve"), "127.0.0.1", 9099)
    assert verdict.failed is True
    assert "422" in verdict.detail
    assert "Field required" in verdict.detail


def test_a_transport_failure_is_a_failure_not_an_exception():
    """Never raises: "we could not tell" must not become a crashed start."""
    with (
        patch("prometheus_manager_core.readiness.httpx.post", side_effect=OSError("refused")),
        patch("prometheus_manager_core.readiness.time.sleep"),
    ):
        verdict = readiness.check(_entry("embedding"), "127.0.0.1", 9099)
    assert verdict.failed is True
    assert "refused" in verdict.detail


def test_it_retries_before_concluding():
    """A lazy load can still be in flight when health first answers, so one
    failure is not a verdict."""
    with (
        patch("prometheus_manager_core.readiness.httpx.post") as post,
        patch("prometheus_manager_core.readiness.time.sleep"),
    ):
        post.side_effect = [
            MagicMock(status_code=503, text="loading"),
            MagicMock(status_code=200),
        ]
        verdict = readiness.check(_entry("text"), "127.0.0.1", 9099)
    assert verdict.ready is True
    assert post.call_count == 2


def test_it_stops_retrying_once_it_succeeds():
    with (
        patch("prometheus_manager_core.readiness.httpx.post") as post,
        patch("prometheus_manager_core.readiness.time.sleep"),
    ):
        post.return_value = MagicMock(status_code=200)
        readiness.check(_entry("text"), "127.0.0.1", 9099)
    assert post.call_count == 1


# ── The probe is a real request of the right shape ────────────────────────────


@pytest.mark.parametrize(
    "modality,backend,path",
    [
        ("text", "llama_cpp", "/v1/chat/completions"),
        ("vision", "llama_cpp", "/v1/chat/completions"),
        ("embedding", "hf_serve", "/v1/embeddings"),
        ("rerank", "llama_cpp", "/v1/rerank"),
        ("classification", "hf_serve", "/predict"),
        ("zero_shot", "hf_serve", "/predict"),
        ("typed_decision", "laya", "/v1/systemone"),
    ],
)
def test_each_modality_posts_to_its_own_path(modality, backend, path):
    with patch("prometheus_manager_core.readiness.httpx.post") as post:
        post.return_value = MagicMock(status_code=200)
        readiness.check(_entry(modality, backend=backend), "127.0.0.1", 9099)
    assert post.call_args[0][0] == f"http://127.0.0.1:9099{path}"


def test_the_typed_decision_body_is_the_shape_laya_actually_accepts():
    """PRM-140 got this wrong first time: options go in `criteria`, not
    `options`, and `instructions` is required. A probe sending the README's shape
    would fail against a perfectly good model and report it broken."""
    with patch("prometheus_manager_core.readiness.httpx.post") as post:
        post.return_value = MagicMock(status_code=200)
        readiness.check(_entry("typed_decision", backend="laya"), "127.0.0.1", 9099)
    body = post.call_args[1]["json"]
    question = next(iter(body["questions"].values()))
    assert "criteria" in question
    assert "options" not in question
    assert question["instructions"]


def test_the_chat_probe_asks_for_one_token():
    """The cheapest generation that still proves a token was produced."""
    with patch("prometheus_manager_core.readiness.httpx.post") as post:
        post.return_value = MagicMock(status_code=200)
        readiness.check(_entry("text"), "127.0.0.1", 9099)
    assert post.call_args[1]["json"]["max_tokens"] == 1


def test_the_rerank_probe_sends_two_documents():
    """One document has nothing to order, and some implementations short-circuit."""
    with patch("prometheus_manager_core.readiness.httpx.post") as post:
        post.return_value = MagicMock(status_code=200)
        readiness.check(_entry("rerank"), "127.0.0.1", 9099)
    assert len(post.call_args[1]["json"]["documents"]) == 2


def test_the_probe_input_is_not_empty():
    """Deliberate: some engines special-case an empty input and would pass a
    probe they should fail."""
    with patch("prometheus_manager_core.readiness.httpx.post") as post:
        post.return_value = MagicMock(status_code=200)
        readiness.check(_entry("embedding"), "127.0.0.1", 9099)
    assert post.call_args[1]["json"]["input"]


# ── The guard: a new modality must not arrive mute ───────────────────────────


def test_every_modality_is_either_probed_or_explicitly_skipped():
    """The list of modalities lives in registry.py and this file has to keep up.

    A modality nobody wrote a probe for falls through to `skipped`, which is
    honest — but it must be a decision, not an oversight. So every modality is
    named here, and adding one to MODALITIES without deciding fails this.
    """
    probed = {
        "text",
        "vision",
        "embedding",
        "rerank",
        "classification",
        "zero_shot",
        "typed_decision",
    }
    skipped = {"image"}
    assert probed | skipped == set(MODALITIES), (
        "MODALITIES changed. Add the new one to `probed` with a probe in "
        "readiness._probe_body, or to `skipped` with the reason in the module "
        "docstring — a modality that silently skips is a model reported ready "
        "on no evidence."
    )


def test_an_unknown_modality_is_skipped_not_passed():
    verdict = readiness.check(_entry("holographic_telepathy"), "127.0.0.1", 9099)
    assert verdict.skipped is True
    assert verdict.ready is False


# ── PRM-184: the zero_shot probe has to prove the modality ───────────────────


def test_the_zero_shot_probe_is_engine_shaped():
    """A probe that proves "the engine responds" is not a probe of the modality.

    Measured: TEI answers hf-serve's zero-shot body with **200**, discarding
    `parameters` rather than refusing it — so this probe passed against TEI while
    the `candidate_labels` were thrown away, and reported ready on evidence of
    nothing. For TEI it now sends what a zero-shot request to TEI actually is, a
    (premise, hypothesis) pair, whose answer depends on the hypothesis
    (`entailment 0.982` against one, `contradiction 0.994` against another).
    """
    from prometheus_manager_core.readiness import _probe_body
    from prometheus_manager_core.registry import RegistryEntry

    def entry(backend: str) -> RegistryEntry:
        return RegistryEntry(
            id="zs",
            path="",
            context_length=512,
            port=1,
            backend=backend,
            modality="zero_shot",
            model_slug="zs",
        )

    hf_path, hf_body = _probe_body(entry("hf_serve"))
    tei_path, tei_body = _probe_body(entry("tei"))

    assert hf_path == tei_path == "/predict"
    assert "parameters" in hf_body
    # TEI has no candidate labels at all, so sending them would prove nothing.
    assert "parameters" not in tei_body
    inputs = tei_body["inputs"]
    assert isinstance(inputs, list) and isinstance(inputs[0], list) and len(inputs[0]) == 2, (
        "a zero-shot probe on TEI must be a (premise, hypothesis) pair, because that is "
        "the only body whose answer depends on the hypothesis"
    )


def test_a_tei_batch_is_a_list_of_lists():
    """Pins the trap in `tei.predict.v1`, so the probe never regresses into it.

    Measured against a running server: a flat `["a","b"]` is read as one **pair**
    and answered once, and `["a","b","c"]` is a 422. Only a list of lists batches.
    So the probe's own body must be nested even for a single pair.
    """
    from prometheus_manager_core.readiness import _probe_body
    from prometheus_manager_core.registry import RegistryEntry

    _, body = _probe_body(
        RegistryEntry(
            id="zs",
            path="",
            context_length=512,
            port=1,
            backend="tei",
            modality="zero_shot",
            model_slug="zs",
        )
    )
    assert all(isinstance(x, list) for x in body["inputs"])
