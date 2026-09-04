"""Build the frozen 300-entry sample for the ``circular_gloss`` pilot (D-70).

Unlike ``build_sample_writers.py``/``build_sample_filler.py``, which draw headwords from
a fixed ``tier2_50k.tsv`` window, this one samples directly and uniformly from every
lexeme id in the production store: ``circular_gloss``'s offenders are not concentrated
in any particular frequency band, so there is no window to disjoin from other pilots.

Read-only against the production store: it only ever calls ``LexemeStore.read`` on
``PRODUCTION_ROOT`` and writes into a separate :class:`LexemeStore` rooted at
``SAMPLE_ROOT``, so pair stages running against the production store are never touched.
Not part of the package.

Usage:
    uv run python scripts/build_sample_circular.py
"""

from __future__ import annotations

import random
from pathlib import Path

from opengloss_generator.config import StoreConfig
from opengloss_generator.store import LexemeStore

PRODUCTION_ROOT = Path("/home/mjbommar/projects/personal/opengloss-generator/data/core-store")
SAMPLE_ROOT = Path(__file__).resolve().parent.parent / "data" / "sample-circular"
SAMPLE_SIZE = 300
SEED = 21


def main() -> None:
    """Sample 300 lexeme ids and copy their entries into the pilot store."""
    production = LexemeStore(StoreConfig(root=PRODUCTION_ROOT))
    ids = sorted(production.iter_ids())
    print(f"production store: {len(ids)} entries")  # noqa: T201 - this script reports to stdout

    rng = random.Random(SEED)  # noqa: S311 - sampling, not crypto
    chosen = rng.sample(ids, SAMPLE_SIZE)

    sample = LexemeStore(StoreConfig(root=SAMPLE_ROOT, fsync_on_write=False))
    missing = []
    for lexeme_id in chosen:
        entry = production.read(lexeme_id)
        if entry is None:
            missing.append(lexeme_id)
            continue
        sample.write(entry)

    print(  # noqa: T201 - this script reports to stdout
        f"wrote {sample.count()} entries to {SAMPLE_ROOT}"
    )
    if missing:
        print("missing:", missing)  # noqa: T201 - this script reports to stdout


if __name__ == "__main__":
    main()
