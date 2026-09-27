"""Traffic splits — PRM-160.

Replacing a checkpoint was a cut: one public name, one model, so pointing the name
somewhere new moved every request at once and a regression was found by all of the
traffic rather than a tenth of it. With seven engines and models that move weekly —
Laya published 0.3.2 through 0.3.7 in four days — that is not hypothetical.

What the industry calls this: SageMaker's production variants with
`InitialVariantWeight`, KServe's `canaryTrafficPercent`, Istio's weighted clusters.

**A new checkpoint is a new model, not a version of one.** That is RM-70's rule,
not a new one, and it is why billing needed no changes: PRM-113 had already split
"the name the caller sent" from "the id that bills", and until now those were
always the same model.
"""

from __future__ import annotations

import ast
import pathlib
from collections import Counter

import pytest

from prometheus_gateway.traffic_split import (
    SplitError,
    SplitTable,
    parse_variants,
    serialise,
)

pytestmark = pytest.mark.asyncio


# ── A split that would misroute is refused where it is written ────────────────


async def test_an_empty_split_is_refused():
    with pytest.raises(SplitError, match="at least one variant"):
        parse_variants([])


async def test_a_zero_weight_is_refused():
    """A variant that can never be chosen reads as configured and is not. Removing
    it is how you express that."""
    with pytest.raises(SplitError, match="above zero"):
        parse_variants([{"model_id": "a", "weight": 0}])


async def test_a_negative_weight_is_refused():
    with pytest.raises(SplitError, match="above zero"):
        parse_variants([{"model_id": "a", "weight": -5}])


async def test_a_boolean_weight_is_refused():
    """`True` is an int in Python, and `weight: true` is a mistake rather than a
    weight of one."""
    with pytest.raises(SplitError, match="above zero"):
        parse_variants([{"model_id": "a", "weight": True}])


async def test_a_duplicated_variant_is_refused():
    """Its effective weight would be the sum, and both displayed weights wrong."""
    with pytest.raises(SplitError, match="appears twice"):
        parse_variants([{"model_id": "a", "weight": 1}, {"model_id": "a", "weight": 2}])


async def test_a_variant_without_a_model_id_is_refused():
    with pytest.raises(SplitError, match="missing model_id"):
        parse_variants([{"weight": 10}])


async def test_variants_round_trip_through_storage():
    variants = parse_variants([{"model_id": "a", "weight": 3}, {"model_id": "b", "weight": 1}])
    assert parse_variants(serialise(variants)) == variants


# ── The weights are respected, and an unsplit name is untouched ───────────────


async def test_a_name_with_no_split_returns_none():
    """Which is how every model behaves until somebody defines a split — the
    default path, and it must cost nothing."""
    assert SplitTable().choose("anything") is None


async def test_a_single_variant_always_wins():
    table = SplitTable()
    table.set("m", parse_variants([{"model_id": "only", "weight": 7}]))
    assert {table.choose("m") for _ in range(50)} == {"only"}


async def test_the_weights_are_respected():
    """20k draws on a 90/10 split. The tolerance is wide because this is random by
    design — the assertion is that the split happens at roughly the weights, not
    that a PRNG is exact."""
    table = SplitTable()
    table.set(
        "m",
        parse_variants(
            [{"model_id": "stable", "weight": 90}, {"model_id": "canary", "weight": 10}]
        ),
    )
    picks = Counter(table.choose("m") for _ in range(20_000))
    canary_share = picks["canary"] / 20_000 * 100
    assert 8 < canary_share < 12, f"canary got {canary_share:.1f}% of traffic, expected ~10%"
    assert picks["stable"] + picks["canary"] == 20_000


async def test_integer_weights_need_not_add_to_a_hundred():
    """1:2 is a valid split, and making an operator turn it into 33:67 invites the
    arithmetic error that a stored percentage would then preserve."""
    table = SplitTable()
    table.set("m", parse_variants([{"model_id": "a", "weight": 1}, {"model_id": "b", "weight": 2}]))
    picks = Counter(table.choose("m") for _ in range(12_000))
    assert 0.28 < picks["a"] / 12_000 < 0.38


async def test_removing_a_split_restores_an_ordinary_name():
    """What ending a rollout means."""
    table = SplitTable()
    table.set("m", parse_variants([{"model_id": "a", "weight": 1}]))
    assert table.choose("m") == "a"
    table.remove("m")
    assert table.choose("m") is None


# ── One malformed row must not take the table down ───────────────────────────


async def test_an_unparseable_row_is_dropped_and_the_rest_load():
    """A gateway that refused to start over one bad split would stop serving every
    model that has none — which is all of them, in the normal case."""
    table = SplitTable()
    table.load({"good": '[{"model_id": "a", "weight": 1}]', "bad": "not json at all"})
    assert table.choose("good") == "a"
    assert table.choose("bad") is None


# ── The guard: no handler may resolve a name without the split ───────────────


async def test_no_handler_resolves_a_name_behind_the_splits_back():
    """Five forwarding handlers, one rule, and no structure making it true at all
    five — which is exactly what PRM-142 and PRM-131 each cost. So the rule is
    asserted instead of remembered.

    `_resolve_requested` is the only caller of `registry.resolve` allowed on a
    request path. The admin router's validation call is exempt by name: it is
    checking whether a variant exists, and routing through the split to do that
    would be circular.
    """
    source = pathlib.Path(__file__).resolve().parents[1] / "src/prometheus_gateway/router.py"
    tree = ast.parse(source.read_text())

    offenders: list[str] = []
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if fn.name == "_resolve_requested":
            continue
        stack = list(ast.iter_child_nodes(fn))
        while stack:
            node = stack.pop()
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "resolve"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "registry"
            ):
                offenders.append(f"{fn.name}:{node.lineno}")
            stack.extend(ast.iter_child_nodes(node))

    assert not offenders, (
        f"these resolve a model name without going through the traffic split: {offenders}. "
        "A request that bypasses _resolve_requested ignores any canary on that name."
    )


async def test_that_guard_is_looking_at_something():
    """It passes vacuously if the extraction finds nothing."""
    source = pathlib.Path(__file__).resolve().parents[1] / "src/prometheus_gateway/router.py"
    text = source.read_text()
    assert "_resolve_requested(registry," in text, "no handler calls the split-aware resolver"
    assert text.count("_resolve_requested(registry,") == 5, (
        "expected the five forwarding handlers to resolve through the split"
    )
