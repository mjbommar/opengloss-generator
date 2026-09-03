"""Run orchestration: the session, the worker pool, and the stop conditions.

A run is a scope. Entering it mints a ``run_id``, configures logging, opens the ledger,
and builds the meter, guard, router, store, and stage runner. Leaving it drains the
ledger and writes a summary — on the success path and on every failure path, including
``SIGINT``.

The worker pool uses ``asyncio.TaskGroup`` rather than ``gather`` because a failure
cancels siblings deterministically and the group's exit is a real synchronisation point:
no task can still be writing when the ledger closes.
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
import signal
import time
import uuid
from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Self

from opengloss_generator.budget import BudgetGuard, CostMeter, CostSummary
from opengloss_generator.errors import BudgetExceededError, OpenGlossError
from opengloss_generator.ledger import LedgerRecord, LedgerWriter
from opengloss_generator.log import bind_run, configure_logging, get_logger
from opengloss_generator.router import ModelRouter
from opengloss_generator.stages import StageRunner
from opengloss_generator.store import LexemeStore

if TYPE_CHECKING:
    from pydantic_ai.models import Model

    from opengloss_generator.config import AppConfig

__all__ = ["RunSession", "RunSummary", "new_run_id", "run_pool"]

_LOG = get_logger(__name__)


def new_run_id() -> str:
    """Return a sortable, unique run identifier."""
    stamp = dt.datetime.now(tz=dt.UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{uuid.uuid4().hex[:8]}"


@dataclass(slots=True)
class RunSummary:
    """End-of-run accounting."""

    run_id: str
    duration_seconds: float
    cost: CostSummary
    items_succeeded: int = 0
    items_failed: int = 0
    stop_reason: str = "completed"
    flex_downgraded: bool = False
    extra: dict[str, object] = field(default_factory=dict)

    def as_dict(self) -> dict[str, object]:
        """Return a JSON-serialisable view for logging and the CLI."""
        return {
            "run_id": self.run_id,
            "duration_seconds": round(self.duration_seconds, 3),
            "stop_reason": self.stop_reason,
            "items_succeeded": self.items_succeeded,
            "items_failed": self.items_failed,
            "flex_downgraded": self.flex_downgraded,
            "cost_usd": round(self.cost.total_usd, 6),
            "calls": self.cost.calls,
            "input_tokens": self.cost.input_tokens,
            "cached_input_tokens": self.cost.cached_input_tokens,
            "output_tokens": self.cost.output_tokens,
            "cache_hit_rate": round(self.cost.cache_hit_rate(), 4),
            "cost_by_model": {k: round(v, 6) for k, v in self.cost.by_model.items()},
            "cost_by_stage": {k: round(v, 6) for k, v in self.cost.by_stage.items()},
            **self.extra,
        }


class RunSession:
    """Everything one run needs, wired together and torn down safely.

    Args:
        config: The run configuration.
        model_override: Substitute model for every stage. Tests pass a pydantic-ai
            ``TestModel``/``FunctionModel`` here so the pipeline runs offline.
        run_id: Explicit run id; one is minted if omitted.
        install_signal_handler: Install a ``SIGINT`` handler that requests a clean stop.
            Off by default so library and test use never touch process-global state.
    """

    def __init__(
        self,
        config: AppConfig,
        *,
        model_override: Model | None = None,
        run_id: str | None = None,
        install_signal_handler: bool = False,
    ) -> None:
        """Build the run's components without performing any I/O."""
        self.config = config
        self.run_id = run_id or new_run_id()
        self.meter = CostMeter()
        self.guard = BudgetGuard(config.budget_usd, self.meter)
        self.router = ModelRouter(config)
        self.store = LexemeStore(config.store)
        self.stages = StageRunner(
            config=config,
            router=self.router,
            meter=self.meter,
            guard=self.guard,
            run_id=self.run_id,
            model_override=model_override,
        )
        self.stop_event = asyncio.Event()
        self.stop_reason = "completed"
        self._ledger = LedgerWriter(config.log_dir / f"{self.run_id}.ledger.jsonl")
        self._started = 0.0
        self._install_signal_handler = install_signal_handler
        self._previous_sigint: object = None

    async def __aenter__(self) -> Self:
        """Configure logging, open the ledger, and start the clock."""
        configure_logging(
            level=self.config.log_level, log_dir=self.config.log_dir, run_id=self.run_id
        )
        bind_run(self.run_id)
        self._started = time.monotonic()
        await self._ledger.start()
        if self._install_signal_handler:
            self._arm_sigint()
        _LOG.info(
            "run_started",
            budget_usd=self.config.budget_usd,
            workers=self.config.concurrency.workers,
            store=str(self.store.root),
            dry_run=self.config.dry_run,
        )
        return self

    async def __aexit__(self, exc_type: type[BaseException] | None, *_: object) -> None:
        """Drain the ledger, restore signals, and log the summary."""
        if self._install_signal_handler:
            self._disarm_sigint()
        if exc_type is BudgetExceededError:
            self.stop_reason = "budget"
        elif exc_type is asyncio.CancelledError:
            self.stop_reason = "cancelled"
        elif exc_type is not None:
            self.stop_reason = f"error:{exc_type.__name__}"
        await self._ledger.stop()
        _LOG.info("run_finished", **self.summary().as_dict())

    async def emit(self, record: LedgerRecord) -> None:
        """Append a record to the run ledger."""
        await self._ledger.emit(record)

    def record_for(
        self,
        kind: str,
        target: str,
        outcome: str,
        **fields: Any,  # noqa: ANN401 - LedgerRecord field values
    ) -> LedgerRecord:
        """Build a ledger record stamped with this run's id."""
        return LedgerRecord(
            run_id=self.run_id,
            kind=kind,
            target=target,
            outcome=outcome,
            **fields,
        )

    def summary(self, **extra: object) -> RunSummary:
        """Return the current accounting snapshot."""
        return RunSummary(
            run_id=self.run_id,
            duration_seconds=time.monotonic() - self._started,
            cost=self.meter.summary(),
            stop_reason=self.stop_reason,
            flex_downgraded=self.router.flex_disabled,
            extra=extra,
        )

    def request_stop(self, reason: str) -> None:
        """Ask the run to stop dispatching new work."""
        if not self.stop_event.is_set():
            self.stop_reason = reason
            self.stop_event.set()
            _LOG.warning("run_stop_requested", reason=reason)

    def _arm_sigint(self) -> None:
        """Route the first ``SIGINT`` to a clean stop; a second one re-raises."""
        loop = asyncio.get_running_loop()
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(signal.SIGINT, self._on_sigint)

    def _disarm_sigint(self) -> None:
        """Remove the handler installed by :meth:`_arm_sigint`."""
        loop = asyncio.get_running_loop()
        with contextlib.suppress(NotImplementedError, RuntimeError):
            loop.remove_signal_handler(signal.SIGINT)

    def _on_sigint(self) -> None:
        """Handle ``SIGINT``: first one stops cleanly, second one aborts."""
        if self.stop_event.is_set():
            self._disarm_sigint()
            signal.raise_signal(signal.SIGINT)
            return
        self.request_stop("interrupt")


async def run_pool[T](
    items: Sequence[T],
    handler: Callable[[T], Awaitable[None]],
    *,
    workers: int,
    stop_event: asyncio.Event | None = None,
    fail_fast: bool = False,
) -> tuple[int, int]:
    """Run ``handler`` over ``items`` with a bounded worker pool.

    Args:
        items: Work items.
        handler: Coroutine function applied to each item. It is responsible for its own
            ledger emission; exceptions are counted and (unless ``fail_fast``) swallowed.
        workers: Number of concurrent workers.
        stop_event: When set, workers finish the item in hand and then stop pulling.
        fail_fast: Re-raise the first handler exception, cancelling the group.

    Returns:
        ``(succeeded, failed)`` counts.

    Raises:
        BaseExceptionGroup: If ``fail_fast`` and a handler raised.
    """
    queue: asyncio.Queue[T] = asyncio.Queue()
    for item in items:
        queue.put_nowait(item)

    succeeded = 0
    failed = 0

    async def worker() -> None:
        nonlocal succeeded, failed
        while True:
            if stop_event is not None and stop_event.is_set():
                return
            try:
                item = queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            try:
                await handler(item)
            except BudgetExceededError:
                failed += 1
                if stop_event is not None:
                    stop_event.set()
                return
            except (TimeoutError, OpenGlossError) as exc:
                failed += 1
                _LOG.warning("work_item_failed", item=str(item), error=str(exc))
                if fail_fast:
                    raise
            else:
                succeeded += 1

    async with asyncio.TaskGroup() as group:
        for index in range(max(1, min(workers, len(items) or 1))):
            group.create_task(worker(), name=f"worker-{index}")

    return succeeded, failed


def iter_batches[T](items: Iterable[T], size: int) -> Iterable[list[T]]:
    """Yield ``items`` in lists of at most ``size``."""
    batch: list[T] = []
    for item in items:
        batch.append(item)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch
