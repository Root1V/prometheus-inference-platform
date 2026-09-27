"""One public name, several models behind it, with weights — PRM-160.

Implements: docs/roadmap.md — PRM-160.

## What this is for

Replacing a checkpoint was a cut. A model is reachable by one public name, and
pointing that name at new weights meant every request moved at once — so a
regression was discovered by all of the traffic rather than by a tenth of it. With
seven engines and models that move weekly (Laya published 0.3.2 through 0.3.7 in
four days) that is not a hypothetical.

What the industry calls this: SageMaker's **production variants** with
`InitialVariantWeight`, KServe's `canaryTrafficPercent`, Istio's weighted
clusters. All the same shape — a route fans out to real, separately deployed
things, with weights.

## A new checkpoint is a new model, not a version of one

That is RM-70's rule rather than a new one. There, a rename is a new model because
`model:<slug>` grants and usage rows have to keep meaning something. A different
checkpoint prices differently, performs differently and fails differently, so
usage blended under one name would be two things averaged into a figure that looks
plausible and describes neither — the failure mode this codebase keeps meeting.

So a variant is an ordinary model with its own catalog id, its own price and its
own usage rows. This adds a routing layer and nothing else, which is why billing
needed no changes: PRM-113 had already separated *the name the caller sent* from
*the id that bills*, and until now those were always the same model. A split is
exactly the case that separation was built for.

## A broken canary must look broken

When the chosen variant has no usable replica, the request fails **naming that
variant** — it does not quietly fall back to the stable one.

That is deliberate and it is the whole point of a canary. Falling back would mean a
canary can never fail its rollout: it would be sent traffic, be unable to serve
it, and report perfect health while production carried the load. A 10% variant
that is down should produce 10% failures, because that is the signal a rollout
exists to read (RM-98, again: a failure must not look like an absence).

## Held in memory, backed by the table

Same shape as the pricing table: loaded at startup, updated when an admin writes,
read on the request path without touching the database. `resolve()` is synchronous
and called on every request, and a split lookup that awaited a query would put the
database in front of every inference.
"""

from __future__ import annotations

import json
import random
import threading
from dataclasses import dataclass

from .telemetry import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class Variant:
    model_id: str
    weight: int


class SplitError(ValueError):
    """A split that would misroute, refused at the point it is defined."""


def parse_variants(raw: object) -> tuple[Variant, ...]:
    """Validate and normalise a variant list — PRM-160.

    Refused rather than repaired, because every one of these is a split that would
    route somewhere the author did not mean:

    * **no variants** — a name that resolves to nothing;
    * **a weight of zero or less** — a variant that can never be chosen, which
      reads as configured and is not. Removing it is how you express that;
    * **a duplicated model id** — two entries for one model, where the effective
      weight is their sum and the displayed weights are both wrong;
    * **a total weight of zero** — nothing to divide by.
    """
    if isinstance(raw, str):
        raw = json.loads(raw)
    if not isinstance(raw, list) or not raw:
        raise SplitError("a split needs at least one variant")

    variants: list[Variant] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            raise SplitError(f"each variant is an object with model_id and weight, got {item!r}")
        model_id = item.get("model_id")
        weight = item.get("weight")
        if not isinstance(model_id, str) or not model_id:
            raise SplitError(f"variant is missing model_id: {item!r}")
        if not isinstance(weight, int) or isinstance(weight, bool) or weight <= 0:
            raise SplitError(
                f"variant {model_id!r} needs an integer weight above zero, got {weight!r}. "
                "A variant that can never be chosen reads as configured and is not — "
                "remove it instead."
            )
        if model_id in seen:
            raise SplitError(
                f"variant {model_id!r} appears twice. Its effective weight would be the sum "
                "and both displayed weights would be wrong."
            )
        seen.add(model_id)
        variants.append(Variant(model_id=model_id, weight=weight))
    return tuple(variants)


def serialise(variants: tuple[Variant, ...]) -> str:
    return json.dumps([{"model_id": v.model_id, "weight": v.weight} for v in variants])


class SplitTable:
    """The live splits. One instance per process, like the pricing table."""

    def __init__(self) -> None:
        self._splits: dict[str, tuple[Variant, ...]] = {}
        self._lock = threading.RLock()

    def load(self, rows: dict[str, str]) -> None:
        """Replace the table from `{name: variants_json}` — called at startup.

        A row that will not parse is dropped with a loud line rather than taking
        the whole table down: one malformed split must not stop a gateway from
        serving every other model.
        """
        parsed: dict[str, tuple[Variant, ...]] = {}
        for name, raw in rows.items():
            try:
                parsed[name] = parse_variants(raw)
            except (SplitError, json.JSONDecodeError) as exc:
                logger.error("traffic_split.unparseable", name=name, error=str(exc))
        with self._lock:
            self._splits = parsed

    def set(self, name: str, variants: tuple[Variant, ...]) -> None:
        with self._lock:
            self._splits[name] = variants

    def remove(self, name: str) -> None:
        with self._lock:
            self._splits.pop(name, None)

    def get(self, name: str) -> tuple[Variant, ...] | None:
        with self._lock:
            return self._splits.get(name)

    def all(self) -> dict[str, tuple[Variant, ...]]:
        with self._lock:
            return dict(self._splits)

    def choose(self, name: str) -> str | None:
        """The model id this request goes to, or None when `name` is not split.

        Weighted random, which is what SageMaker and KServe do. Not sticky: an
        inference request carries no session, so pinning a caller to a variant
        would need state nobody asked for and would skew the split by traffic
        shape rather than by weight.
        """
        variants = self.get(name)
        if not variants:
            return None
        if len(variants) == 1:
            return variants[0].model_id
        total = sum(v.weight for v in variants)
        pick = random.uniform(0, total)  # noqa: S311 — routing, not cryptography
        upto = 0.0
        for variant in variants:
            upto += variant.weight
            if pick <= upto:
                return variant.model_id
        return variants[-1].model_id  # float arithmetic landed past the end


_table = SplitTable()


def get_split_table() -> SplitTable:
    return _table
