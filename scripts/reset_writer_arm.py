"""Reset a writer-diversity pilot arm's store to its pre-enrichment baseline (D-63).

Each arm's store copy starts as a straight copy of the production ``luna`` output
(``build_sample_writers.py``), which already carries the D-42/D-45 graded EXAMPLES-field
renditions and the D-53 per-sense example sentences luna wrote in production. Since both
workflows are idempotent per target, running a different writer against that copy
unchanged would find every target already present and cost nothing — not a comparison of
writers, a no-op. This strips exactly what those two tasks would (re)write — the non-
canonical EXAMPLES-field renditions (grade_1/5/10/college; the D-53 marker's own
provenance record so ``run_examples`` is due again; everything in each live sense's
``examples`` set beyond its original canonical (neutral, plain) items) — so every arm,
luna included, starts the pilot from the same place: canonical glosses and canonical
examples only, nothing graded, nothing generated for D-53.

Never touches the ``luna`` arm (used as-is per the pilot spec) or any store outside
``data/sample-writers-*``.

Usage:
    uv run python scripts/reset_writer_arm.py --arm qwen
"""

from __future__ import annotations

import argparse
from pathlib import Path

from opengloss_generator.schema import Example, ReadingLevel, Register, Renditions
from opengloss_generator.store import LexemeStore
from opengloss_generator.config import StoreConfig
from opengloss_generator.workflows.examples import MARKER_PREFIX


def _reset_entry(entry) -> bool:  # noqa: ANN001 - Lexeme, imported only for typing elsewhere
    """Strip an entry's non-canonical examples and D-53 marker. Return whether it changed."""
    changed = False
    for pos_entry in entry.pos_entries:
        for sense in pos_entry.senses:
            kept = [
                r
                for r in sense.examples
                if r.reading_level is ReadingLevel.NEUTRAL and r.style is Register.PLAIN
            ]
            if len(kept) != len(sense.examples):
                sense.examples = Renditions[Example](root=kept)
                changed = True
    before = len(entry.provenance)
    entry.provenance = {
        pid: rec
        for pid, rec in entry.provenance.items()
        if not (rec.note and rec.note.startswith(f"{MARKER_PREFIX}:"))
    }
    if len(entry.provenance) != before:
        changed = True
    return changed


def main() -> None:
    """Reset every entry in one arm's store to the pilot's common starting point."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arm", required=True)
    args = parser.parse_args()
    if args.arm == "luna":
        raise SystemExit("refusing to reset the luna arm: it is used as-is (see module docstring)")

    root = Path(f"data/sample-writers-{args.arm}")
    store = LexemeStore(StoreConfig(root=root))
    words = Path("data/sample-writers.tsv").read_text(encoding="utf-8").split()

    reset_count = 0
    for word in words:
        entry = store.read(word)
        if entry is None:
            continue
        if _reset_entry(entry):
            store.write(entry)
            reset_count += 1
    print(f"reset {reset_count}/{len(words)} entries in {root}")


if __name__ == "__main__":
    main()
