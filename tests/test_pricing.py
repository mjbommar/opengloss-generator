"""Cost arithmetic and price-table coverage."""

from __future__ import annotations

import pytest

from opengloss_generator.config import _default_policies
from opengloss_generator.pricing import ServiceTier, estimate_cost, known_models, price_for


def test_every_default_policy_model_is_priced():
    # FR-6.5: a model without a price would make every run report as free.
    for stage, policy in _default_policies().items():
        bare = policy.model.split(":", 1)[-1]
        assert bare in known_models(), f"{stage.value} selects unpriced model {bare}"
        price_for(bare, policy.service_tier)


def test_unknown_model_raises_rather_than_costing_zero():
    with pytest.raises(KeyError, match="no price for model"):
        price_for("gpt-does-not-exist")


def test_flex_is_half_of_standard():
    std = price_for("gpt-5.6-luna", ServiceTier.DEFAULT)
    flex = price_for("gpt-5.6-luna", ServiceTier.FLEX)
    assert flex.input_usd == pytest.approx(std.input_usd / 2)
    assert flex.output_usd == pytest.approx(std.output_usd / 2)


def test_cached_tokens_are_not_double_charged():
    # Providers report cached tokens inside input_tokens. Billing the full input at the
    # fresh rate would overstate a cache-heavy call by an order of magnitude.
    all_fresh = estimate_cost("gpt-5.6-luna", input_tokens=1_000_000, output_tokens=0)
    all_cached = estimate_cost(
        "gpt-5.6-luna",
        input_tokens=1_000_000,
        cached_input_tokens=1_000_000,
        output_tokens=0,
    )
    assert all_fresh.total_usd == pytest.approx(0.10)
    assert all_cached.total_usd == pytest.approx(0.01)
    assert all_cached.total_usd < all_fresh.total_usd


def test_cost_decomposition_sums():
    cost = estimate_cost(
        "gpt-5.6-luna", input_tokens=1000, cached_input_tokens=400, output_tokens=500
    )
    assert cost.total_usd == pytest.approx(cost.input_usd + cost.cached_input_usd + cost.output_usd)


def test_rejects_impossible_usage():
    with pytest.raises(ValueError, match="exceeds"):
        estimate_cost("gpt-5.6-luna", input_tokens=10, cached_input_tokens=20, output_tokens=0)
    with pytest.raises(ValueError, match="non-negative"):
        estimate_cost("gpt-5.6-luna", input_tokens=-1, output_tokens=0)
