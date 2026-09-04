"""Build the frozen sample for the ``relation-reconcile --only verdicts`` measurement (D-68).

Unlike ``build_sample_reconcile.py``, which walks a breadth-first neighbourhood, this one
starts from the only population the step has anything to say about: the entries that
actually carry a :class:`~opengloss_generator.schema.Contrast`. The ``contrasts`` pilot
(D-57) ran over the first 300 headwords of ``data/core/tier2_50k.tsv`` and **271** of them
came back with at least one paragraph — the whole core store holds no others — so the
sample is those 271 rather than a round 300.

Every entry a demoted edge resolves to is copied as well, whether or not it carries a
contrast of its own. That is not padding: a contrast is written once per *undirected* pair
(D-57 §1), so the far side of every judged edge has no contrast, and the far-side
demotions this step queues are only measurable if the target entry is in the copy.

Copies entries read-only from the main checkout's ``data/core-store`` into this worktree's
``data/sample-verdicts/``, preserving the store's own blake2b shard layout so the copy is a
valid ``LexemeStore`` on its own. This never writes to the source store.

Usage:
    uv run python scripts/build_sample_verdicts.py
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from opengloss_generator.identity import shard_for

_MAIN_CHECKOUT = Path("/home/mjbommar/projects/personal/opengloss-generator")
_SOURCE_STORE = _MAIN_CHECKOUT / "data/core-store"
_DEST_STORE = Path("data/sample-verdicts")
_HEADWORD_LIST = Path("data/sample-verdicts.tsv")


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
                sense_id = relation.get("target", {}).get("sense_id")
                if sense_id:
                    out.append(sense_id.split(":")[0])
    return out


def main() -> None:
    """Copy every contrast-bearing entry and every entry its resolved edges point at."""
    known = {path.stem for path in _SOURCE_STORE.rglob("*.json")}
    seeds: list[str] = []
    neighbours: set[str] = set()
    for path in sorted(_SOURCE_STORE.rglob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not payload.get("contrasts"):
            continue
        seeds.append(payload["lexeme_id"])
        neighbours.update(n for n in _resolved_neighbours(payload) if n in known)

    chosen = sorted(set(seeds) | neighbours)
    _DEST_STORE.mkdir(parents=True, exist_ok=True)
    for lexeme_id in chosen:
        dest_dir = _DEST_STORE.joinpath(*shard_for(lexeme_id))
        dest_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(_source_path(lexeme_id), dest_dir / f"{lexeme_id}.json")

    _HEADWORD_LIST.write_text("\n".join(sorted(seeds)) + "\n", encoding="utf-8")
    print(  # noqa: T201 - reports to stdout
        f"copied {len(chosen)} entries into {_DEST_STORE} "
        f"({len(seeds)} carrying contrasts, {len(chosen) - len(seeds)} far sides)"
    )


if __name__ == "__main__":
    main()
