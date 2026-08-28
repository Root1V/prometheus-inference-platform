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
    prompt_price_per_1m: float
    completion_price_per_1m: float


class PricingTable:
    """Loaded from pricing.yaml; empty (no prices) if the file doesn't exist."""

    def __init__(self, path: Path | str | None = None) -> None:
        pricing_path = Path(path) if path else _DEFAULT_PRICING_PATH
        self._prices: dict[str, ModelPrice] = {}
        if pricing_path.exists():
            self._load(pricing_path)
        else:
            logger.debug("pricing.no_file", path=str(pricing_path))

    def _load(self, path: Path) -> None:
        with path.open() as f:
            data = yaml.safe_load(f) or {}
        for entry in data.get("models", []):
            self._prices[entry["id"]] = ModelPrice(
                prompt_price_per_1m=float(entry["prompt_price_per_1m"]),
                completion_price_per_1m=float(entry["completion_price_per_1m"]),
            )

    def estimate_cost_usd(
        self, model_id: str, prompt_tokens: int, completion_tokens: int
    ) -> float | None:
        """None means "no price configured for this model", not "free"."""
        price = self._prices.get(model_id)
        if price is None:
            return None
        return (
            prompt_tokens * price.prompt_price_per_1m
            + completion_tokens * price.completion_price_per_1m
        ) / 1_000_000


_table: PricingTable | None = None


def init_pricing_table(path: str | None = None) -> PricingTable:
    global _table
    _table = PricingTable(path)
    return _table


def get_pricing_table() -> PricingTable:
    if _table is None:
        raise RuntimeError("Pricing table not initialised. Call init_pricing_table() first.")
    return _table
