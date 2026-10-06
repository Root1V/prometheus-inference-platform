"""Make a schema llama.cpp's grammar converter can read, without changing it.

PRM-197, from repo2deck: valid schemas came back
`400 JSON schema conversion failed: Unrecognized schema: false`. They worked
around it by turning their tuples into loosely-typed arrays, which costs the
thing a tuple is for — with `items: {"type": "string"}` the second element stops
being an integer.

**The gap is one keyword, measured rather than assumed.** Sixteen schema
features were probed against a running server; only `items` fails when given a
*boolean*, and it fails for `true` as well as `false`. `additionalProperties`,
`propertyNames`, `contains` and `not` all take booleans happily, as do `enum`,
`oneOf`, `anyOf`, `allOf`, `$ref`/`$defs`, `pattern`, `format`, `const`,
`minimum`/`maximum`, `uniqueItems` and `minItems`/`maxItems`. So this normalises
exactly one keyword and leaves everything else alone — a broader rewrite would
be a second, drifting copy of someone else's validator.

It is a *translation*, not a relaxation. In draft 2020-12 `items` applies to the
elements after `prefixItems`, so:

    {"prefixItems": [A, B], "items": false}   ==   {"prefixItems": [A, B], "maxItems": 2}
    {"prefixItems": [A, B], "items": true}    ==   {"prefixItems": [A, B]}

`false` forbids anything past the prefix, which is exactly a maximum length;
`true` permits anything, which is what an absent `items` already means.

**`minItems` is deliberately not added**, and that is the easy mistake here. A
tuple schema does not require its elements to be present — `["Ana"]` satisfies
the schema above — so adding `minItems` would hand the engine a *stricter*
schema than the caller wrote, and a gateway that quietly tightens a contract is
worse than one that rejects it.
"""

from __future__ import annotations

from typing import Any

#: Where a subschema can hide. Walked so a tuple nested in `properties` or in
#: `$defs` is reached too — both were measured failing before this existed.
_MAPPING_OF_SCHEMAS = ("properties", "$defs", "definitions", "patternProperties")
_LIST_OF_SCHEMAS = ("prefixItems", "allOf", "anyOf", "oneOf")
_SINGLE_SCHEMA = ("items", "contains", "not", "if", "then", "else", "additionalProperties")


def normalise_schema(node: Any) -> Any:
    """Return `node` with boolean `items` translated, recursively.

    Pure, and returns new containers rather than mutating: the caller's
    `response_format` is their object, and a request that is retried should send
    what they wrote the second time too.
    """
    if isinstance(node, list):
        return [normalise_schema(item) for item in node]
    if not isinstance(node, dict):
        return node

    out: dict[str, Any] = {}
    for key, value in node.items():
        if key == "items" and isinstance(value, bool):
            continue  # replaced below, or dropped when it means "anything"
        if key in _MAPPING_OF_SCHEMAS and isinstance(value, dict):
            out[key] = {k: normalise_schema(v) for k, v in value.items()}
        elif key in _LIST_OF_SCHEMAS and isinstance(value, list):
            out[key] = [normalise_schema(v) for v in value]
        elif key in _SINGLE_SCHEMA:
            out[key] = normalise_schema(value)
        else:
            out[key] = value

    items = node.get("items")
    if items is False:
        # No element past the prefix — a maximum length, and nothing else.
        prefix = node.get("prefixItems")
        limit = len(prefix) if isinstance(prefix, list) else 0
        existing = out.get("maxItems")
        # If the caller already set a maximum, the stricter of the two is what
        # both constraints together mean. Never loosen it.
        out["maxItems"] = min(existing, limit) if isinstance(existing, int) else limit

    return out


def normalise_response_format(response_format: Any) -> Any:
    """Normalise the schema inside a `response_format`, if there is one.

    `{"type": "json_object"}` and `{"type": "text"}` carry no schema and pass
    through untouched.
    """
    if not isinstance(response_format, dict):
        return response_format
    json_schema = response_format.get("json_schema")
    if not isinstance(json_schema, dict) or "schema" not in json_schema:
        return response_format
    return {
        **response_format,
        "json_schema": {**json_schema, "schema": normalise_schema(json_schema["schema"])},
    }
