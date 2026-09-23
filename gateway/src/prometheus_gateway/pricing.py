"""Static per-model token pricing — turns usage counts into an estimated cost.

Implements: docs/roadmap.md — RM-33 (pricing table + real cost).
Optional by design: a model with no configured price has no cost figure (never
silently reported as $0) — same as the pricing file itself being optional, since
most deployments won't bother pricing self-hosted models at all.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from .telemetry import get_logger

logger = get_logger(__name__)

# gateway/pricing.yaml — sibling to pyproject.toml, mirrors registry.py's
# repo-relative default (parents[2] from src/prometheus_gateway/pricing.py -> gateway/).
_DEFAULT_PRICING_PATH = Path(__file__).parents[2] / "pricing.yaml"


# PRM-120: what a model is worth before anyone has measured it.
#
# A base price per modality, deliberately flat. The alternative considered was
# deriving one from the model's size and the node's cost with RM-62's formula,
# and it was dropped after measuring it against this fleet: decode throughput
# does not track file size closely enough to price from it. Three real models,
# tokens/s x GB — the quantity that would have to be roughly constant:
#
#     qwen3-0.6b   (dense, 0.36 GB)    363 tok/s ->   131
#     qwen3-8b-q6  (dense, 6.26 GB)     63 tok/s ->   396
#     gpt-oss-20b  (MoE,  11.28 GB)    115 tok/s ->  1295
#
# A 10x spread, because a MoE model reads only its active experts and a very
# small model is bound by overhead rather than bandwidth. A price derived that
# way would have been wrong by an order of magnitude *and* would have looked
# measured. A flat base price claims nothing it cannot support: it is a
# starting point an operator replaces with the real one, using the throughput
# calculator on the pricing page once the model has served traffic.
#
# The figures are aligned with published per-1M-token rates for hosted
# small-to-mid open models (roughly the gpt-4o-mini / small-open-model tier)
# rather than with this deployment's own costs, which vary per node. They are
# a defensible starting point, not a market quote — check them against current
# rates before invoicing anyone on them.
DEFAULT_PRICES_BY_MODALITY: dict[str, "ModelPrice"] = {}


def _default_prices() -> dict[str, "ModelPrice"]:
    """Built lazily so ModelPrice is defined before this runs."""
    if not DEFAULT_PRICES_BY_MODALITY:
        DEFAULT_PRICES_BY_MODALITY.update(
            {
                # Chat and vision bill identically — a vision model charges for
                # the tokens an image is worth, not for the image.
                "text": ModelPrice(prompt_price_per_1m=0.20, completion_price_per_1m=0.60),
                "vision": ModelPrice(prompt_price_per_1m=0.20, completion_price_per_1m=0.60),
                # Embeddings are prompt-only; the completion side exists so the
                # price is complete (half a price is treated as unpriced).
                "embedding": ModelPrice(prompt_price_per_1m=0.02, completion_price_per_1m=0.0),
                # A reranker generates nothing either: the cost is all prompt.
                "rerank": ModelPrice(prompt_price_per_1m=0.02, completion_price_per_1m=0.0),
                # PRM-139: the two PRM-136/137 added, at the rerank rate for the
                # reason rerank has it — a classifier and a zero-shot decider
                # are prompt-only encoder passes, generating nothing. Shipping
                # the modalities without this billed every one of those calls
                # at nothing: 11 rows of live usage with a NULL cost before it
                # was noticed, which is the exact case A-22 was asking about.
                #
                # One caveat the token count does not capture: a zero-shot call
                # runs the text once per candidate label, so its real compute
                # scales with the option count while the billed input does not.
                # Priced per input token like the rest until that is worth
                # solving; flagged here rather than left to be rediscovered.
                "classification": ModelPrice(prompt_price_per_1m=0.02, completion_price_per_1m=0.0),
                "zero_shot": ModelPrice(prompt_price_per_1m=0.02, completion_price_per_1m=0.0),
                # PRM-140: Laya answers several typed questions from one
                # forward pass and reports its own `usage.input_tokens`,
                # so unlike zero-shot the billed input does track the work.
                "typed_decision": ModelPrice(prompt_price_per_1m=0.02, completion_price_per_1m=0.0),
                # Per image, not per token.
                "image": ModelPrice(image_price=0.01),
            }
        )
    return DEFAULT_PRICES_BY_MODALITY


def default_prices() -> dict[str, "ModelPrice"]:
    """Every modality's base price — PRM-125 exposes this to the dashboard so
    its reset button can fill the inputs without writing anything."""
    return dict(_default_prices())


def default_price_for(modality: str) -> "ModelPrice | None":
    """The base price a newly catalogued model starts on, or None for a
    modality we have no published reference for — better no price than an
    invented one."""
    return _default_prices().get(modality)


@dataclass(frozen=True)
class ModelPrice:
    prompt_price_per_1m: float | None = None
    completion_price_per_1m: float | None = None
    image_price: float | None = None  # USD per generated image


class PricingTable:
    """Loaded from pricing.yaml; empty (no prices) if the file doesn't exist."""

    def __init__(self, path: Path | str | None = None) -> None:
        pricing_path = Path(path) if path else _DEFAULT_PRICING_PATH
        # _file_prices is a read-only snapshot of what pricing.yaml said at
        # startup — remove_price() falls back to it so deleting an admin
        # (DB) override restores the file's own price rather than clearing
        # it entirely, matching "DB overrides the file, never replaces it."
        self._file_prices: dict[str, ModelPrice] = {}
        if pricing_path.exists():
            self._load(pricing_path)
        else:
            logger.debug("pricing.no_file", path=str(pricing_path))
        self._prices: dict[str, ModelPrice] = dict(self._file_prices)

    def _load(self, path: Path) -> None:
        with path.open() as f:
            data = yaml.safe_load(f) or {}
        for entry in data.get("models", []):
            has_prompt_price = "prompt_price_per_1m" in entry
            has_completion_price = "completion_price_per_1m" in entry
            if has_prompt_price != has_completion_price:
                logger.warning("pricing.incomplete_token_price", model_id=entry["id"])
            token_priced = has_prompt_price and has_completion_price
            self._file_prices[entry["id"]] = ModelPrice(
                prompt_price_per_1m=float(entry["prompt_price_per_1m"]) if token_priced else None,
                completion_price_per_1m=float(entry["completion_price_per_1m"])
                if token_priced
                else None,
                image_price=float(entry["image_price"]) if "image_price" in entry else None,
            )

    def get_price(self, model_id: str) -> ModelPrice | None:
        return self._prices.get(model_id)

    def list_prices(self) -> dict[str, ModelPrice]:
        return dict(self._prices)

    def set_price(
        self,
        model_id: str,
        *,
        prompt_price_per_1m: float | None,
        completion_price_per_1m: float | None,
        image_price: float | None,
    ) -> None:
        """Admin-driven update (RM-60 follow-up) — mutates the live in-memory
        table directly so the new price applies to the very next request,
        no restart needed. Callers are also responsible for persisting to
        ModelPriceConfig (db.py) so it survives a restart.
        """
        self._prices[model_id] = ModelPrice(
            prompt_price_per_1m=prompt_price_per_1m,
            completion_price_per_1m=completion_price_per_1m,
            image_price=image_price,
        )

    def remove_price(self, model_id: str) -> None:
        """Clears an admin (DB) override. If pricing.yaml also priced this
        model, that file price reappears — otherwise the model is unpriced.
        """
        file_price = self._file_prices.get(model_id)
        if file_price is not None:
            self._prices[model_id] = file_price
        else:
            self._prices.pop(model_id, None)

    def _lookup(self, *names: str | None) -> ModelPrice | None:
        """First configured price among the names this model answers to.

        PRM-113: a model has a catalog id and a slug, and RM-70 lets the slug be
        named once. Looking up by one name only meant naming a model made it
        miss its pricing.yaml entry — the price did not change, the key did, and
        from then on it billed nothing at all with nothing failing. Verified
        directly: a table keyed on the old name returns 0.621 for it and None
        for the new one.
        """
        for name in names:
            if name:
                price = self._prices.get(name)
                if price is not None:
                    return price
        return None

    def estimate_cost_usd(
        self,
        model_id: str,
        prompt_tokens: int,
        completion_tokens: int,
        model_slug: str | None = None,
    ) -> float | None:
        """None means "no price configured for this model", not "free"."""
        price = self._lookup(model_id, model_slug)
        if (
            price is None
            or price.prompt_price_per_1m is None
            or price.completion_price_per_1m is None
        ):
            return None
        return (
            prompt_tokens * price.prompt_price_per_1m
            + completion_tokens * price.completion_price_per_1m
        ) / 1_000_000

    def estimate_image_cost_usd(
        self, model_id: str, num_images: int, model_slug: str | None = None
    ) -> float | None:
        """None means "no price configured for this model", not "free"."""
        price = self._lookup(model_id, model_slug)
        if price is None or price.image_price is None:
            return None
        return price.image_price * num_images


_table: PricingTable | None = None


def init_pricing_table(path: str | None = None) -> PricingTable:
    global _table
    _table = PricingTable(path)
    return _table


def get_pricing_table() -> PricingTable:
    if _table is None:
        raise RuntimeError("Pricing table not initialised. Call init_pricing_table() first.")
    return _table
