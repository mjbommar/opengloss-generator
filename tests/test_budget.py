"""The budget guard must not overshoot under concurrency."""

from __future__ import annotations

import asyncio

import pytest

from opengloss_generator.budget import BudgetGuard, CostMeter
from opengloss_generator.config import ModelPolicy
from opengloss_generator.errors import BudgetExceededError
from opengloss_generator.pricing import ServiceTier, estimate_cost
from opengloss_generator.router import estimate_tokens


async def test_reservations_prevent_concurrent_overshoot():
    meter = CostMeter()
    guard = BudgetGuard(1.0, meter)
    # Ten workers each reserving $0.20 must not all be admitted against a $1.00 ceiling.
    results = await asyncio.gather(
        *(guard.reserve(0.20) for _ in range(10)), return_exceptions=True
    )
    admitted = [r for r in results if not isinstance(r, BaseException)]
    refused = [r for r in results if isinstance(r, BudgetExceededError)]
    assert len(admitted) == 5
    assert len(refused) == 5
    assert guard.exhausted.is_set()


async def test_release_frees_capacity():
    meter = CostMeter()
    guard = BudgetGuard(1.0, meter)
    reservation = await guard.reserve(0.9)
    with pytest.raises(BudgetExceededError):
        await guard.reserve(0.9)
    await guard.release(reservation)
    assert await guard.reserve(0.9) is not None


async def test_release_is_idempotent():
    guard = BudgetGuard(1.0, CostMeter())
    reservation = await guard.reserve(0.5)
    await guard.release(reservation)
    await guard.release(reservation)
    assert guard.remaining_usd() == pytest.approx(1.0)


async def test_unlimited_budget_never_refuses():
    guard = BudgetGuard(None, CostMeter())
    for _ in range(100):
        await guard.reserve(1000.0)
    assert guard.remaining_usd() is None


def test_meter_attributes_cost_by_model_and_stage():
    meter = CostMeter()
    meter.record(stage="senses", model="gpt-5.6-luna", input_tokens=1000, output_tokens=500)
    meter.record(stage="overview", model="gpt-5.4-nano", input_tokens=1000, output_tokens=100)
    summary = meter.summary()
    assert summary.calls == 2
    assert set(summary.by_stage) == {"senses", "overview"}
    assert set(summary.by_model) == {"gpt-5.6-luna", "gpt-5.4-nano"}
    assert summary.total_usd == pytest.approx(sum(summary.by_stage.values()))


def test_cache_hit_rate():
    meter = CostMeter()
    meter.record(
        stage="senses",
        model="gpt-5.6-luna",
        input_tokens=1000,
        cached_input_tokens=250,
        output_tokens=10,
    )
    assert meter.summary().cache_hit_rate() == pytest.approx(0.25)


async def test_reservation_uses_expected_output_tokens_not_max_tokens():
    """D-41: the reservation must price at the measured typical output, not the ceiling.

    Reserving at `max_tokens` for a call that measures far less (the RENDITIONS policy:
    8192 max_tokens, ~250 measured output tokens) overstates every in-flight
    reservation and starves dispatch long before the budget is actually spent.
    """
    policy = ModelPolicy(model="gpt-5.6-luna", max_tokens=8192, expected_output_tokens=400)
    instructions = "x" * 300
    body = "y" * 900

    estimated_tokens = estimate_tokens(body, instructions, policy.max_tokens)
    at_expected = estimate_cost(
        policy.model,
        input_tokens=estimated_tokens - policy.max_tokens,
        output_tokens=policy.expected_output_tokens,
        tier=ServiceTier.FLEX,
    ).total_usd
    at_max = estimate_cost(
        policy.model,
        input_tokens=estimated_tokens - policy.max_tokens,
        output_tokens=policy.max_tokens,
        tier=ServiceTier.FLEX,
    ).total_usd

    # The reservation formula this test guards must land on the expected-tokens
    # figure, and that figure must be materially cheaper than reserving at the ceiling.
    assert at_expected < at_max


async def test_128_concurrent_renditions_shaped_reservations_fit_an_8_dollar_budget():
    """Reproduces the observed defect at the reported scale.

    An `enrich` sweep at `--budget 8 --concurrency 128` stopped with
    `stop_reason=budget` at $4.96 actually spent, because each in-flight RENDITIONS
    call (`max_tokens=8192`, ~250 tokens measured) was reserved at its ceiling rather
    than its typical output.

    A run that has already committed $7.50 of an $8 ceiling (close enough that the
    reservation size decides the outcome) must still admit 128 concurrent reservations
    of a RENDITIONS-shaped call once each is priced at `expected_output_tokens`; priced
    at `max_tokens`, the same 128 reservations overshoot and some are refused.
    """
    policy = ModelPolicy(model="gpt-5.6-luna", max_tokens=8192, expected_output_tokens=400)
    # Same shape as docs/COST-MODEL.md's renditions arithmetic: ~600 cached instruction
    # tokens, ~30 tokens of volatile canonical input.
    instructions = "x" * 1800
    body = "y" * 90

    estimated_tokens = estimate_tokens(body, instructions, policy.max_tokens)
    input_tokens = estimated_tokens - policy.max_tokens

    def reservation_cost(output_tokens: int) -> float:
        return estimate_cost(
            policy.model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            tier=ServiceTier.FLEX,
        ).total_usd

    at_expected = reservation_cost(policy.expected_output_tokens)
    at_max = reservation_cost(policy.max_tokens)
    assert at_expected < at_max  # the whole point of D-41

    committed_already = 7.5

    meter_fixed = CostMeter()
    meter_fixed.total_usd = committed_already
    guard_fixed = BudgetGuard(8.0, meter_fixed)
    results_fixed = await asyncio.gather(
        *(guard_fixed.reserve(at_expected) for _ in range(128)), return_exceptions=True
    )
    assert [r for r in results_fixed if isinstance(r, BaseException)] == []

    meter_before_fix = CostMeter()
    meter_before_fix.total_usd = committed_already
    guard_before_fix = BudgetGuard(8.0, meter_before_fix)
    results_before_fix = await asyncio.gather(
        *(guard_before_fix.reserve(at_max) for _ in range(128)), return_exceptions=True
    )
    refused_before_fix = [r for r in results_before_fix if isinstance(r, BaseException)]
    assert len(refused_before_fix) > 0
