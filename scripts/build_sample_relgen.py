"""Build the frozen sample for the ``relation-regen`` pilot (D-74).

The population this pass has anything to say about is not "some entries" — it is
entries carrying at least one *live* sense whose ``relations`` list is empty, the state
the 2026-09-05 store-wide audit counted 3,709 of 137,314 senses in
(``docs/CORE-DIARY.md``, "Goal 2 complete", open item 1). Every one of those senses had
every edge it ever held demoted by ``relation-hygiene`` or ``relation-reconcile`` and
then tombstoned, so the sample also needs the tombstone provenance records this pass
reads to build its "already rejected" set per sense — which is exactly what copying the
live entry, unmodified, preserves.

Walks ``data/core-store`` in shard order and stops at 300 qualifying entries rather than
scanning the whole store: at a ~2.7% incidence the population is not so sparse that a
prefix of shard order would bias it in any way that matters for a pilot, and stopping
early is a few seconds against several minutes for a store past 100K entries mid-chain.

Copies entries **read-only** from the main checkout's live ``data/core-store`` into this
worktree's ``data/sample-relgen/``, preserving the store's own blake2b shard layout so the
copy is a valid ``LexemeStore`` on its own. This never writes to the source store, which
a tier-4 chain is writing to concurrently — safe to read regardless, because
``LexemeStore.write`` swaps a complete file into place atomically (``store.py``), so a
concurrent read only ever sees a whole old or new file, never a partial one.

Usage:
    uv run python scripts/build_sample_relgen.py [--limit N]
"""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from opengloss_generator.identity import shard_for

_MAIN_CHECKOUT = Path("/home/mjbommar/projects/personal/opengloss-generator")
_SOURCE_STORE = _MAIN_CHECKOUT / "data/core-store"
_DEST_STORE = Path("data/sample-relgen")
_HEADWORD_LIST = Path("data/sample-relgen.tsv")

_DEFAULT_LIMIT = 300


def _source_path(lexeme_id: str) -> Path:
    """Return the read-only source file for one lexeme id."""
    return _SOURCE_STORE.joinpath(*shard_for(lexeme_id), f"{lexeme_id}.json")


def _has_zero_relation_live_sense(payload: dict) -> bool:
    """Return whether the entry carries at least one live sense with no relations.

    Mirrors ``audit.py``'s own ``senses_zero_relations`` check: a non-retired sense whose
    ``relations`` list is empty. Read straight off the raw JSON payload rather than
    through ``Lexeme.model_validate`` — this script only ever reads, and validating
    100K+ payloads just to check one boolean would be the slow part of the scan.

    Args:
        payload: The parsed entry JSON.

    Returns:
        Whether the entry qualifies for the sample.
    """
    for pos_entry in payload.get("pos_entries", []):
        for sense in pos_entry.get("senses", []):
            if sense.get("retired"):
                continue
            if not sense.get("relations"):
                return True
    return False


def main() -> None:
    """Copy up to ``--limit`` entries carrying a zero-relation live sense."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=_DEFAULT_LIMIT)
    args = parser.parse_args()

    chosen: list[str] = []
    scanned = 0
    for path in sorted(_SOURCE_STORE.rglob("*.json")):
        scanned += 1
        payload = json.loads(path.read_text(encoding="utf-8"))
        if _has_zero_relation_live_sense(payload):
            chosen.append(payload["lexeme_id"])
        if len(chosen) >= args.limit:
            break

    _DEST_STORE.mkdir(parents=True, exist_ok=True)
    for lexeme_id in sorted(chosen):
        dest_dir = _DEST_STORE.joinpath(*shard_for(lexeme_id))
        dest_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(_source_path(lexeme_id), dest_dir / f"{lexeme_id}.json")

    _HEADWORD_LIST.write_text("\n".join(sorted(chosen)) + "\n", encoding="utf-8")
    print(  # noqa: T201 - reports to stdout
        f"copied {len(chosen)} entries into {_DEST_STORE} (scanned {scanned} of {_SOURCE_STORE})"
    )


if __name__ == "__main__":
    main()
