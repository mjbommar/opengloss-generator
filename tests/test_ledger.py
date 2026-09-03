"""The ledger must be a single writer producing well-formed JSONL."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from opengloss_generator.ledger import LedgerRecord, LedgerWriter


async def test_concurrent_emitters_produce_intact_lines(tmp_path: Path):
    path = tmp_path / "ledger.jsonl"
    async with LedgerWriter(path) as writer:
        await asyncio.gather(
            *(
                writer.emit(LedgerRecord(run_id="r", kind="generate", target=f"w{i}", outcome="ok"))
                for i in range(200)
            )
        )
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 200
    parsed = [json.loads(line) for line in lines]
    assert {p["target"] for p in parsed} == {f"w{i}" for i in range(200)}


async def test_appends_across_sessions(tmp_path: Path):
    path = tmp_path / "ledger.jsonl"
    for _ in range(2):
        async with LedgerWriter(path) as writer:
            await writer.emit(LedgerRecord(run_id="r", kind="k", target="t", outcome="ok"))
    assert len(path.read_text(encoding="utf-8").strip().splitlines()) == 2
