"""Tests for RM-33 — static per-model pricing (prometheus_gateway/pricing.py)."""

from __future__ import annotations

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
