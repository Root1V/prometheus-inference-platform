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
