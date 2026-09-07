"""Copy the first 20,000 ids of the read-only core store into ``data/sample-20k``.

A measurement slice for the ``export-hf`` memory work (D-77): big enough that the peak
RSS of a full export extrapolates, small enough to run in minutes. Never writes to the
source store.

Usage:
    uv run python scripts/build_sample_20k.py
"""

from __future__ import annotations

import shutil
from pathlib import Path

from opengloss_generator.identity import shard_for

_SOURCE_STORE = Path("/home/mjbommar/projects/personal/opengloss-generator/data/core-store")
_DEST_STORE = Path("data/sample-20k")
_N = 20_000


def main() -> None:
    """Copy the first ``_N`` ids in sorted order, preserving the shard layout."""
    ids = sorted(path.stem for path in _SOURCE_STORE.rglob("*.json") if not path.name.startswith("."))
    chosen = ids[:_N]
    copied = 0
    for lexeme_id in chosen:
        source = _SOURCE_STORE.joinpath(*shard_for(lexeme_id), f"{lexeme_id}.json")
        dest = _DEST_STORE.joinpath(*shard_for(lexeme_id), f"{lexeme_id}.json")
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, dest)
        copied += 1
    print(f"store has {len(ids)} entries; copied {copied} into {_DEST_STORE}")


if __name__ == "__main__":
    main()
