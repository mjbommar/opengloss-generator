"""Cost accounting and the run budget guard.

The guard tracks committed spend *plus a reservation for in-flight work*. Checking only
committed spend would overshoot the ceiling by roughly the concurrency level, because N
calls can be dispatched before the first one reports its cost.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from dataclasses import dataclass, field

from opengloss_generator.errors import BudgetExceededError
from opengloss_generator.log import get_logger
from opengloss_generator.pricing import ServiceTier, UsageCost, estimate_cost

__all__ = ["BudgetGuard", "CostMeter", "CostSummary", "Reservation"]

_LOG = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class CostSummary:
    """Aggregated spend for a run."""

    total_usd: float
    calls: int
    input_tokens: int
    cached_input_tokens: int
    output_tokens: int
    by_model: dict[str, float]
    by_stage: dict[str, float]

    def cache_hit_rate(self) -> float:
        """Return the share of prompt tokens served from cache."""
        if self.input_tokens == 0:
            return 0.0
        return self.cached_input_tokens / self.input_tokens


@dataclass
class CostMeter:
    """Accumulates the cost of every model call in a run."""

    total_usd: float = 0.0
    calls: int = 0
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    by_model: dict[str, float] = field(default_factory=lambda: defaultdict(float))
    by_stage: dict[str, float] = field(default_factory=lambda: defaultdict(float))

    def record(
        self,
        *,
        stage: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        cached_input_tokens: int = 0,
        tier: ServiceTier = ServiceTier.FLEX,
    ) -> UsageCost:
        """Price one call and fold it into the running totals.

        Args:
            stage: Stage name, for per-stage attribution.
            model: Model identifier.
            input_tokens: Reported prompt tokens.
            output_tokens: Reported completion tokens.
            cached_input_tokens: Reported cached prompt tokens.
            tier: Service tier used for the call.

        Returns:
            The :class:`~opengloss_generator.pricing.UsageCost` for this call.
        """
        cost = estimate_cost(
            model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached_input_tokens=cached_input_tokens,
            tier=tier,
        )
        self.total_usd += cost.total_usd
        self.calls += 1
        self.input_tokens += input_tokens
        self.cached_input_tokens += cached_input_tokens
        self.output_tokens += output_tokens
        self.by_model[cost.model] += cost.total_usd
        self.by_stage[stage] += cost.total_usd
        return cost

    def summary(self) -> CostSummary:
        """Return an immutable snapshot of the accumulated spend."""
        return CostSummary(
            total_usd=self.total_usd,
            calls=self.calls,
            input_tokens=self.input_tokens,
            cached_input_tokens=self.cached_input_tokens,
            output_tokens=self.output_tokens,
            by_model=dict(self.by_model),
            by_stage=dict(self.by_stage),
        )


@dataclass(slots=True)
class Reservation:
    """A provisional hold on budget for one unit of in-flight work."""

    amount_usd: float
    released: bool = False


class BudgetGuard:
    """Enforces a run's spending ceiling across concurrent workers.

    Args:
        budget_usd: The ceiling. ``None`` means unlimited, which is the only way to
            disable the guard — there is no implicit default ceiling.
        meter: The meter that accumulates actual spend.
    """

    def __init__(self, budget_usd: float | None, meter: CostMeter) -> None:
        """Store the ceiling and initialise the reservation pool."""
        if budget_usd is not None and budget_usd <= 0:
            raise ValueError("budget_usd must be positive or None")
        self._budget = budget_usd
        self._meter = meter
        self._reserved = 0.0
        self._lock = asyncio.Lock()
        self._exhausted = asyncio.Event()

    @property
    def exhausted(self) -> asyncio.Event:
        """Return an event that is set once the ceiling has been reached."""
        return self._exhausted

    @property
    def budget_usd(self) -> float | None:
        """Return the configured ceiling, or ``None`` if unlimited."""
        return self._budget

    def remaining_usd(self) -> float | None:
        """Return the unspent, unreserved budget, or ``None`` if unlimited."""
        if self._budget is None:
            return None
        return self._budget - self._meter.total_usd - self._reserved

    async def reserve(self, estimated_usd: float) -> Reservation:
        """Hold budget for a unit of work about to be dispatched.

        Args:
            estimated_usd: Projected cost of the work.

        Returns:
            A :class:`Reservation` to pass to :meth:`release`.

        Raises:
            BudgetExceededError: If the reservation would breach the ceiling. The
                guard's :attr:`exhausted` event is set before raising, so the runner
                can stop dispatching without polling.
        """
        async with self._lock:
            if self._budget is None:
                self._reserved += estimated_usd
                return Reservation(estimated_usd)
            projected = self._meter.total_usd + self._reserved + estimated_usd
            if projected > self._budget:
                self._exhausted.set()
                # Logged so a stop is diagnosable from the run log: if `reserved` is
                # far larger than `committed`, the reservation estimate (not the
                # ceiling) is what actually stopped dispatch.
                _LOG.info(
                    "budget_reservation_refused",
                    committed_usd=round(self._meter.total_usd, 6),
                    reserved_usd=round(self._reserved, 6),
                    estimate_usd=round(estimated_usd, 6),
                    budget_usd=self._budget,
                )
                raise BudgetExceededError(self._budget, self._meter.total_usd)
            self._reserved += estimated_usd
            return Reservation(estimated_usd)

    async def release(self, reservation: Reservation) -> None:
        """Drop a reservation once its actual cost has been recorded on the meter.

        Args:
            reservation: The reservation returned by :meth:`reserve`.
        """
        async with self._lock:
            if reservation.released:
                return
            reservation.released = True
            self._reserved = max(0.0, self._reserved - reservation.amount_usd)
            if self._budget is not None and self._meter.total_usd >= self._budget:
                self._exhausted.set()
