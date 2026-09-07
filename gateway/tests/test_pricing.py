"""Tests for RM-33 — static per-model pricing (prometheus_gateway/pricing.py)."""

from __future__ import annotations

import pytest

from prometheus_gateway.pricing import PricingTable


def test_missing_file_yields_empty_table(tmp_path):
    table = PricingTable(tmp_path / "does-not-exist.yaml")

    assert table.estimate_cost_usd("any-model", 1000, 1000) is None


def test_unpriced_model_returns_none(tmp_path):
    pricing_file = tmp_path / "pricing.yaml"
    pricing_file.write_text(
        "models:\n  - id: priced-model\n    prompt_price_per_1m: 1.0\n"
        "    completion_price_per_1m: 2.0\n"
    )
    table = PricingTable(pricing_file)

    assert table.estimate_cost_usd("other-model", 1000, 1000) is None


def test_estimates_cost_from_prompt_and_completion_prices(tmp_path):
    pricing_file = tmp_path / "pricing.yaml"
    pricing_file.write_text(
        "models:\n  - id: priced-model\n    prompt_price_per_1m: 1.0\n"
        "    completion_price_per_1m: 2.0\n"
    )
    table = PricingTable(pricing_file)

    cost = table.estimate_cost_usd("priced-model", 1_000_000, 500_000)

    assert cost == 1.0 + 1.0  # 1M @ $1/1M + 500k @ $2/1M


def test_zero_tokens_still_priced_model_returns_zero_not_none(tmp_path):
    pricing_file = tmp_path / "pricing.yaml"
    pricing_file.write_text(
        "models:\n  - id: priced-model\n    prompt_price_per_1m: 1.0\n"
        "    completion_price_per_1m: 2.0\n"
    )
    table = PricingTable(pricing_file)

    assert table.estimate_cost_usd("priced-model", 0, 0) == 0.0


# ── RM-60: image pricing ─────────────────────────────────────────────────────


def test_image_price_parsed_independently_of_token_price(tmp_path):
    pricing_file = tmp_path / "pricing.yaml"
    pricing_file.write_text("models:\n  - id: image-model\n    image_price: 0.02\n")
    table = PricingTable(pricing_file)

    price = table.get_price("image-model")
    assert price is not None
    assert price.image_price == 0.02
    assert price.prompt_price_per_1m is None
    assert price.completion_price_per_1m is None


def test_estimate_image_cost_usd(tmp_path):
    pricing_file = tmp_path / "pricing.yaml"
    pricing_file.write_text("models:\n  - id: image-model\n    image_price: 0.02\n")
    table = PricingTable(pricing_file)

    assert table.estimate_image_cost_usd("image-model", 3) == pytest.approx(0.06)
    assert table.estimate_image_cost_usd("other-model", 3) is None


def test_unpriced_image_model_returns_none_not_zero(tmp_path):
    pricing_file = tmp_path / "pricing.yaml"
    pricing_file.write_text(
        "models:\n  - id: text-only-model\n    prompt_price_per_1m: 1.0\n"
        "    completion_price_per_1m: 2.0\n"
    )
    table = PricingTable(pricing_file)

    assert table.estimate_image_cost_usd("text-only-model", 1) is None


def test_incomplete_token_price_entry_treated_as_unpriced(tmp_path):
    """An entry with only one of prompt/completion price is unpriced for
    tokens (never guess the missing half) — logged as a warning.
    """
    pricing_file = tmp_path / "pricing.yaml"
    pricing_file.write_text("models:\n  - id: half-priced\n    prompt_price_per_1m: 1.0\n")
    table = PricingTable(pricing_file)

    assert table.estimate_cost_usd("half-priced", 1000, 1000) is None
    price = table.get_price("half-priced")
    assert price is not None
    assert price.prompt_price_per_1m is None
    assert price.completion_price_per_1m is None


def test_model_with_both_token_and_image_price(tmp_path):
    pricing_file = tmp_path / "pricing.yaml"
    pricing_file.write_text(
        "models:\n  - id: hybrid-model\n    prompt_price_per_1m: 1.0\n"
        "    completion_price_per_1m: 2.0\n    image_price: 0.05\n"
    )
    table = PricingTable(pricing_file)

    assert table.estimate_cost_usd("hybrid-model", 1_000_000, 0) == 1.0
    assert table.estimate_image_cost_usd("hybrid-model", 2) == pytest.approx(0.10)
