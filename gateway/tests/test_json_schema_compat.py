"""PRM-197 — a tuple schema survives the grammar converter.

repo2deck sent valid schemas and got
`400 JSON schema conversion failed: Unrecognized schema: false`. They worked
around it by turning tuples into loosely-typed arrays, which costs the thing a
tuple is for: with `items: {"type": "string"}` the second element stops being an
integer.

The scope was measured, not guessed. Sixteen features were probed against a
running server and **only `items` fails when given a boolean** — for `true` as
well as `false`. `additionalProperties`, `propertyNames`, `contains` and `not`
all accept booleans; so do `enum`, `oneOf`/`anyOf`/`allOf`, `$ref`/`$defs`,
`pattern`, `format`, `const`, `minimum`/`maximum`, `uniqueItems` and
`minItems`/`maxItems`. Nesting was measured too: the same tuple fails inside
`properties` and inside `$defs`, so the walk has to be recursive.

The translation is exact rather than lenient, and the test that matters most is
the one asserting `minItems` is *not* added.
"""

from __future__ import annotations

import pytest

from prometheus_gateway.models.json_schema_compat import (
    normalise_response_format,
    normalise_schema,
)
from prometheus_gateway.models.schemas import ChatCompletionRequest

TUPLE = {"type": "array", "prefixItems": [{"type": "string"}, {"type": "integer"}]}


# ── the translation ────────────────────────────────────────────────────────


def test_items_false_becomes_a_maximum_length():
    """`items` applies past `prefixItems`, so forbidding it is a maximum."""
    got = normalise_schema({**TUPLE, "items": False})
    assert got["maxItems"] == 2
    assert "items" not in got
    assert got["prefixItems"] == TUPLE["prefixItems"]


def test_minitems_is_not_added():
    """The mistake this test exists to prevent. A tuple schema does not require
    its elements to be present — `["Ana"]` satisfies it — so adding `minItems`
    would hand the engine a stricter schema than the caller wrote. A gateway
    that quietly tightens a contract is worse than one that rejects it."""
    assert "minItems" not in normalise_schema({**TUPLE, "items": False})


def test_items_false_with_no_prefix_means_an_empty_array():
    assert normalise_schema({"type": "array", "items": False})["maxItems"] == 0


def test_items_true_is_simply_dropped():
    """`true` permits anything past the prefix, which is what an absent `items`
    already means — so there is nothing to translate it into."""
    got = normalise_schema({**TUPLE, "items": True})
    assert "items" not in got
    assert "maxItems" not in got


def test_an_existing_maximum_is_never_loosened():
    """Both constraints at once mean the stricter of the two."""
    assert normalise_schema({**TUPLE, "items": False, "maxItems": 1})["maxItems"] == 1
    assert normalise_schema({**TUPLE, "items": False, "maxItems": 9})["maxItems"] == 2


def test_a_real_items_subschema_is_left_alone():
    """Only the *boolean* form is the problem; an ordinary `items` schema is
    what the converter has always handled."""
    schema = {"type": "array", "items": {"type": "string"}}
    assert normalise_schema(schema) == schema


# ── the walk ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("name", "schema", "path"),
    [
        (
            "properties",
            {"type": "object", "properties": {"par": {**TUPLE, "items": False}}},
            lambda s: s["properties"]["par"],
        ),
        (
            "$defs",
            {"$defs": {"P": {**TUPLE, "items": False}}, "type": "object"},
            lambda s: s["$defs"]["P"],
        ),
        (
            "anyOf",
            {"anyOf": [{"type": "null"}, {**TUPLE, "items": False}]},
            lambda s: s["anyOf"][1],
        ),
        (
            "an array of tuples",
            {"type": "array", "items": {**TUPLE, "items": False}},
            lambda s: s["items"],
        ),
        (
            "prefixItems of prefixItems",
            {"type": "array", "prefixItems": [{**TUPLE, "items": False}]},
            lambda s: s["prefixItems"][0],
        ),
    ],
)
def test_a_nested_tuple_is_reached(name, schema, path):
    """Measured failing in `properties` and `$defs` before this existed — a
    normaliser that only looked at the top level would have fixed the example
    and left the real schemas broken."""
    inner = path(normalise_schema(schema))
    assert inner["maxItems"] == 2
    assert "items" not in inner


def test_a_property_literally_named_items_is_not_mistaken_for_the_keyword():
    """`properties: {"items": ...}` is a property *called* items, not the
    keyword. Confusing the two would silently rewrite a caller's data model."""
    schema = {"type": "object", "properties": {"items": {"type": "array"}}}
    assert normalise_schema(schema) == schema


def test_a_boolean_in_a_position_the_engine_accepts_is_untouched():
    """Only `items` was measured failing. Rewriting the others would be a
    second, drifting copy of someone else's validator."""
    schema = {
        "type": "object",
        "properties": {"a": {"type": "string"}},
        "additionalProperties": False,
        "propertyNames": False,
        "not": False,
    }
    got = normalise_schema(schema)
    assert got["additionalProperties"] is False
    assert got["propertyNames"] is False
    assert got["not"] is False


# ── the wrapper, and the caller's own object ───────────────────────────────


def test_a_response_format_without_a_schema_passes_through():
    for rf in ({"type": "json_object"}, {"type": "text"}, None, "nonsense"):
        assert normalise_response_format(rf) == rf


def test_the_callers_object_is_not_mutated():
    """Their object is theirs, and a retried request should send what they wrote
    the second time too."""
    schema = {**TUPLE, "items": False}
    rf = {"type": "json_schema", "json_schema": {"name": "p", "schema": schema}}
    normalise_response_format(rf)
    assert schema["items"] is False
    assert "maxItems" not in schema


def test_the_request_forwards_the_translated_schema():
    """End to end through the payload builder, which is where it has to happen —
    verified live against the engine as `["Ana", 30]`, types kept by position."""
    req = ChatCompletionRequest(
        model="m",
        messages=[{"role": "user", "content": "x"}],
        response_format={
            "type": "json_schema",
            "json_schema": {"name": "par", "schema": {**TUPLE, "items": False}},
        },
    )
    sent = req.to_llama_payload()["response_format"]["json_schema"]["schema"]
    assert sent["maxItems"] == 2
    assert "items" not in sent
    assert sent["prefixItems"][1]["type"] == "integer", "the point of a tuple"
