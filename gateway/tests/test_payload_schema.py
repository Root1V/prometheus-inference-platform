"""`payload_schema` in the catalog — PRM-144.

P-23 offered Axonium the engine name so a consumer could deduce a body shape
from it. A-27 refused, with a better argument than ours: replacing an engine
with a contract-identical one would break every consumer whose dispatch table
is keyed on our implementation's name, although nothing observable changed.

So the catalog publishes a versioned contract id and never the engine. These
tests pin the three properties that make it worth having — it is present, it is
derived from a single place, and it says nothing when it cannot say the truth.
"""

from __future__ import annotations

import pytest

from prometheus_gateway.models.registry import (
    ModelEntry,
    ModelResolution,
    payload_schema_for,
    payload_schema_of,
)


def _entry(entry_id: str, modality: str, backend: str) -> ModelEntry:
    return ModelEntry(
        id=entry_id,
        path=f"/m/{entry_id}",
        context_length=512,
        family="",
        quantization="",
        backend_url="http://127.0.0.1:18099",
        backend_status="active",
        modality=modality,
        backend=backend,
        model_id=entry_id,
        model_slug=entry_id,
    )


def _group(modality: str, *backends: str) -> ModelResolution:
    members = tuple(_entry(f"m{i}", modality, b) for i, b in enumerate(backends))
    return ModelResolution(
        name="m", members=members, modality=modality, context_length=512, model_key="m"
    )


# ── The contract, not the implementation ─────────────────────────────────────


@pytest.mark.parametrize(
    "modality,engine,expected",
    [
        # Ours: the gateway defines the body, so the engine is irrelevant to it.
        ("text", "llama_cpp", "prometheus.chat.v1"),
        ("text", "mlx", "prometheus.chat.v1"),
        ("text", "vllm", "prometheus.chat.v1"),
        ("vision", "llama_cpp", "prometheus.chat.v1"),
        ("embedding", "hf_serve", "prometheus.embeddings.v1"),
        ("rerank", "llama_cpp", "prometheus.rerank.v1"),
        ("image", "sd_cpp", "prometheus.images.v1"),
        # Theirs: pass-through, so the engine *is* what determines the shape.
        ("classification", "hf_serve", "hf-inference.text-classification.v1"),
        ("zero_shot", "hf_serve", "hf-inference.zero-shot-classification.v1"),
        ("typed_decision", "laya", "typed-decision.v1"),
    ],
)
def test_the_schema_is_the_contract_not_the_engine(modality, engine, expected) -> None:
    assert payload_schema_for(modality, engine) == expected


def test_swapping_an_engine_that_keeps_the_contract_does_not_change_the_schema() -> None:
    """A-27's deciding case, asserted directly.

    This is the property `engine` could not have: moving a chat model from
    llama.cpp to MLX changes nothing a consumer sends, so it must change
    nothing a consumer reads.
    """
    assert payload_schema_for("text", "llama_cpp") == payload_schema_for("text", "mlx")


def test_a_pass_through_modality_on_an_unknown_engine_says_nothing() -> None:
    """The half that must not be guessed.

    A `zero_shot` model on some new engine is not an `hf-inference` body just
    because the last one was. Falling back to the known shape would hand a
    consumer a confident wrong answer, which is worse than no answer.
    """
    assert payload_schema_for("zero_shot", "some_new_engine") is None
    assert payload_schema_for("typed_decision", "hf_serve") is None


def test_an_unmapped_modality_says_nothing() -> None:
    assert payload_schema_for("holographic_telepathy", "llama_cpp") is None


# ── A group whose replicas disagree ──────────────────────────────────────────


def test_replicas_agreeing_report_the_shared_schema() -> None:
    assert payload_schema_of(_group("text", "llama_cpp", "mlx")) == "prometheus.chat.v1"


def test_replicas_disagreeing_report_nothing_rather_than_the_first_answer() -> None:
    """RM-98's lesson: a failure must never read as an absence — or as a fact.

    Two replicas of one pass-through model on engines with different body
    shapes is a misconfiguration. The catalog is a list and cannot refuse it
    the way `mismatch` refuses a request, so it states nothing instead of
    picking a member and making it look settled.
    """
    group = _group("zero_shot", "hf_serve", "some_new_engine")
    assert payload_schema_of(group) is None


# ── The two lists that must not drift ────────────────────────────────────────


def test_every_pass_through_modality_has_a_schema_for_every_engine_that_serves_it() -> None:
    """The guard. A new backend or modality must not reach the catalog mute.

    `_PAYLOAD_SCHEMAS` is a second list of the same facts the router and the
    manager's registry already hold, which is the shape of defect this
    codebase keeps meeting. So it is asserted against them rather than
    maintained beside them.
    """
    from prometheus_gateway.models.registry import _ENGINE_SHAPED, _PAYLOAD_SCHEMAS
    from prometheus_gateway.router import _PASS_THROUGH_MODALITIES

    assert _ENGINE_SHAPED == _PASS_THROUGH_MODALITIES, (
        "the engine-shaped modalities and the router's pass-through modalities "
        "are the same set of facts and have drifted"
    )

    # Every non-pass-through modality the manager can register needs a schema,
    # because a model of that modality is reachable the moment it is created.
    from prometheus_manager_core.registry import MODALITIES

    missing = [
        m for m in MODALITIES if m not in _ENGINE_SHAPED and payload_schema_for(m, "") is None
    ]
    assert not missing, f"modalities the catalog cannot describe a body for: {missing}"

    # And every pass-through modality must have at least one engine mapped, or
    # the field is silently absent on exactly the models it exists for.
    unmapped = [m for m in _ENGINE_SHAPED if not any(k[0] == m for k in _PAYLOAD_SCHEMAS)]
    assert not unmapped, f"pass-through modalities with no engine mapped: {unmapped}"
