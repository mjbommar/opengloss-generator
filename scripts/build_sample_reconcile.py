"""Build the frozen 300-entry sample for the ``relation-reconcile`` measurement (D-65).

Unlike ``build_sample_writers.py``, which samples headwords independently, this one walks
the **resolved relation graph**: a random 300 entries out of 41,886 share almost no edges,
and every number this pass is measured by — reciprocity, one-sided symmetric pairs,
far-side removals — is a fact about a *pair* of entries. A breadth-first neighbourhood
from a seeded start keeps both ends of the edges it copies, so ``opengloss audit``'s
reciprocity figures mean something on the copy.

Copies entries read-only from the main checkout's ``data/core-store`` into this
worktree's ``data/sample-reconcile/``, preserving the store's own blake2b shard layout so
the copy is a valid ``LexemeStore`` on its own. This never writes to the source store.

Usage:
    uv run python scripts/build_sample_reconcile.py
"""

from __future__ import annotations

import json
import random
import shutil
from collections import deque
from pathlib import Path

from opengloss_generator.identity import shard_for

_MAIN_CHECKOUT = Path("/home/mjbommar/projects/personal/opengloss-generator")
_SOURCE_STORE = _MAIN_CHECKOUT / "data/core-store"
_DEST_STORE = Path("data/sample-reconcile")
_HEADWORD_LIST = Path("data/sample-reconcile.tsv")
_SAMPLE_SIZE = 300
_SEED = 65


def _source_path(lexeme_id: str) -> Path:
    """Return the read-only source file for one lexeme id."""
    return _SOURCE_STORE.joinpath(*shard_for(lexeme_id), f"{lexeme_id}.json")


def _resolved_neighbours(payload: dict) -> list[str]:
    """Return the lexeme ids this entry's *resolved* relations point at."""
    out: list[str] = []
    for pos_entry in payload.get("pos_entries", []):
        for sense in pos_entry.get("senses", []):
            if sense.get("retired"):
                continue
            for relation in sense.get("relations", []):
                target = relation.get("target", {})
                sense_id = target.get("sense_id")
                if sense_id:
                    out.append(sense_id.split(":")[0])
    return out


def main() -> None:
    """Walk a neighbourhood of 300 entries out of the core store and copy it."""
    all_ids = sorted(path.stem for path in _SOURCE_STORE.rglob("*.json"))
    rng = random.Random(_SEED)  # noqa: S311 - sampling, not crypto
    known = set(all_ids)

    seeds = rng.sample(all_ids, k=20)
    queue: deque[str] = deque(seeds)
    seen: set[str] = set(seeds)
    chosen: list[str] = []
    while queue and len(chosen) < _SAMPLE_SIZE:
        lexeme_id = queue.popleft()
        source = _source_path(lexeme_id)
        if not source.is_file():
            continue
        payload = json.loads(source.read_text(encoding="utf-8"))
        chosen.append(lexeme_id)
        for neighbour in _resolved_neighbours(payload):
            if neighbour in known and neighbour not in seen:
                seen.add(neighbour)
                queue.append(neighbour)

    _DEST_STORE.mkdir(parents=True, exist_ok=True)
    for lexeme_id in chosen:
        dest_dir = _DEST_STORE.joinpath(*shard_for(lexeme_id))
        dest_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(_source_path(lexeme_id), dest_dir / f"{lexeme_id}.json")

    _HEADWORD_LIST.write_text("\n".join(sorted(chosen)) + "\n", encoding="utf-8")
    print(f"copied {len(chosen)} entries into {_DEST_STORE}")  # noqa: T201 - reports to stdout


if __name__ == "__main__":
    main()
