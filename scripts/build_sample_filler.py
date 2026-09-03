"""Build the frozen 300-entry sample for the filler-rewrite pilot (D-66).

Draws headwords from ``data/core/tier2_50k.tsv`` rows 15,000-31,887 (0-indexed slice,
disjoint from ``build_sample_writers.py``'s 5,000-25,000 window), seed 66, and copies the
matching entries read-only from the main checkout's ``data/core-store`` into this
worktree's ``data/sample-filler/``, preserving the store's own blake2b shard layout so the
copy is a valid ``LexemeStore`` on its own. Same copier pattern as
``build_sample_writers.py``.

This never writes to the source store. Run once; the pilot runs (``qc filler --flag`` then
``content-hygiene --only filler_examples``) act only on this worktree's copy.

Usage:
    uv run python scripts/build_sample_filler.py
"""

from __future__ import annotations

import random
import shutil
from pathlib import Path

from opengloss_generator.identity import shard_for, slugify

_MAIN_CHECKOUT = Path("/home/mjbommar/projects/personal/opengloss-generator")
_SOURCE_TSV = _MAIN_CHECKOUT / "data/core/tier2_50k.tsv"
_SOURCE_STORE = _MAIN_CHECKOUT / "data/core-store"
_DEST_STORE = Path("data/sample-filler")
_HEADWORD_LIST = Path("data/sample-filler.tsv")
_ROW_START, _ROW_END = 15_000, 31_887
_SAMPLE_SIZE = 300
_SEED = 66


def main() -> None:
    """Sample 300 headwords and copy their entries into the pilot store."""
    lines = _SOURCE_TSV.read_text(encoding="utf-8").splitlines()
    window = lines[_ROW_START:_ROW_END]
    headwords = [line.split("\t")[1] for line in window if line.strip()]

    rng = random.Random(_SEED)  # noqa: S311 - sampling, not crypto
    chosen = rng.sample(headwords, k=_SAMPLE_SIZE)

    _DEST_STORE.mkdir(parents=True, exist_ok=True)
    copied, missing = [], []
    for headword in chosen:
        lexeme_id = slugify(headword)
        shard = shard_for(lexeme_id)
        src = _SOURCE_STORE.joinpath(*shard, f"{lexeme_id}.json")
        if not src.is_file():
            missing.append(headword)
            continue
        dest_dir = _DEST_STORE.joinpath(*shard)
        dest_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest_dir / f"{lexeme_id}.json")
        copied.append(headword)

    _HEADWORD_LIST.write_text("\n".join(sorted(copied)) + "\n", encoding="utf-8")
    print(  # noqa: T201 - this script's whole job is to report to stdout
        f"sampled {len(chosen)} headwords, copied {len(copied)}, missing {len(missing)}"
    )
    if missing:
        print("missing:", missing)  # noqa: T201 - this script's whole job is to report to stdout


if __name__ == "__main__":
    main()
