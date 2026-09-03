"""Append-only run ledger.

Workers never touch the ledger file. They put records on a queue and a single writer task
owns the handle, which is why interleaved or torn JSONL lines cannot occur however many
workers are running.
"""

from __future__ import annotations

import asyncio
import datetime as dt
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Self

import orjson

__all__ = ["LedgerRecord", "LedgerWriter"]


@dataclass(slots=True)
class LedgerRecord:
    """One unit of work and its outcome."""

    run_id: str
    kind: str
    target: str
    outcome: str
    cost_usd: float = 0.0
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    duration_seconds: float = 0.0
    attempts: int = 1
    detail: dict[str, Any] = field(default_factory=dict)
    at: str = field(default_factory=lambda: dt.datetime.now(tz=dt.UTC).isoformat())


class LedgerWriter:
    """Single-writer JSONL sink for :class:`LedgerRecord`.

    Args:
        path: File to append to. Parent directories are created.
        queue_size: Bound on the in-memory queue; back-pressures workers if the writer
            falls behind rather than growing without limit.
    """

    def __init__(self, path: Path, queue_size: int = 1024) -> None:
        """Prepare the sink without opening the file; :meth:`start` does that."""
        self._path = path
        self._queue: asyncio.Queue[LedgerRecord | None] = asyncio.Queue(maxsize=queue_size)
        self._task: asyncio.Task[None] | None = None
        self._records = 0

    @property
    def records_written(self) -> int:
        """Return how many records have been flushed to disk."""
        return self._records

    async def __aenter__(self) -> Self:
        """Start the writer task."""
        await self.start()
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        """Drain the queue and stop the writer task."""
        await self.stop()

    async def start(self) -> None:
        """Open the file and start the background writer."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._task = asyncio.create_task(self._run(), name="ledger-writer")

    async def emit(self, record: LedgerRecord) -> None:
        """Enqueue a record for writing.

        Args:
            record: The record to append.
        """
        await self._queue.put(record)

    async def stop(self) -> None:
        """Signal end-of-stream and wait for the writer to drain."""
        if self._task is None:
            return
        await self._queue.put(None)
        await self._task
        self._task = None

    async def _run(self) -> None:
        """Consume the queue until the sentinel arrives, flushing as it goes."""
        with self._path.open("ab") as handle:
            while True:
                record = await self._queue.get()
                if record is None:
                    handle.flush()
                    return
                handle.write(orjson.dumps(asdict(record)) + b"\n")
                self._records += 1
                if self._queue.empty():
                    handle.flush()
