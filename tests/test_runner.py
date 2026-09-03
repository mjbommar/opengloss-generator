"""Worker pool semantics: bounded, stoppable, resumable."""

from __future__ import annotations

import asyncio

import pytest

from opengloss_generator.errors import BudgetExceededError, GenerationError
from opengloss_generator.runner import RunSession, iter_batches, run_pool


async def test_pool_is_bounded():
    in_flight = 0
    peak = 0

    async def handler(_: int) -> None:
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0.01)
        in_flight -= 1

    ok, failed = await run_pool(list(range(20)), handler, workers=3)
    assert (ok, failed) == (20, 0)
    assert peak <= 3


async def test_stop_event_halts_dispatch_but_finishes_in_flight():
    stop = asyncio.Event()
    done: list[int] = []

    async def handler(item: int) -> None:
        if item == 2:
            stop.set()
        await asyncio.sleep(0.005)
        done.append(item)

    ok, _ = await run_pool(list(range(50)), handler, workers=2, stop_event=stop)
    assert ok < 50
    assert 2 in done  # the item that requested the stop still completed


async def test_our_errors_are_counted_not_raised():
    async def handler(item: int) -> None:
        if item % 2:
            raise GenerationError("odd")

    ok, failed = await run_pool(list(range(10)), handler, workers=4)
    assert (ok, failed) == (5, 5)


async def test_fail_fast_reraises():
    async def handler(_: int) -> None:
        raise GenerationError("boom")

    with pytest.raises(BaseExceptionGroup):
        await run_pool([1, 2], handler, workers=2, fail_fast=True)


async def test_budget_error_stops_the_pool():
    stop = asyncio.Event()
    calls = 0

    async def handler(_: int) -> None:
        nonlocal calls
        calls += 1
        raise BudgetExceededError(1.0, 1.0)

    await run_pool(list(range(100)), handler, workers=1, stop_event=stop)
    assert stop.is_set()
    assert calls == 1


def test_iter_batches():
    assert list(iter_batches(range(5), 2)) == [[0, 1], [2, 3], [4]]
    assert list(iter_batches([], 3)) == []


async def test_session_writes_ledger_and_summary(config, scripted_model):
    async with RunSession(config, model_override=scripted_model, run_id="ledger-run") as s:
        await s.emit(s.record_for("generate", "abseil", "ok", cost_usd=0.01))
    ledger = config.log_dir / "ledger-run.ledger.jsonl"
    assert ledger.exists()
    assert '"target":"abseil"' in ledger.read_text(encoding="utf-8")
    assert s.summary().stop_reason == "completed"
    assert s.summary().as_dict()["run_id"] == "ledger-run"


async def test_session_records_budget_stop_reason(config, scripted_model):
    with pytest.raises(BudgetExceededError):
        async with RunSession(config, model_override=scripted_model, run_id="b") as s:
            raise BudgetExceededError(1.0, 1.0)
    assert s.stop_reason == "budget"
