"""Print before/after canonical-gloss pairs for every sense the circular_gloss pilot rewrote."""

from __future__ import annotations

from pathlib import Path

from opengloss_generator.config import StoreConfig
from opengloss_generator.store import LexemeStore

BEFORE_ROOT = Path(__file__).resolve().parent.parent / "data" / "sample-circular-before"
AFTER_ROOT = Path(__file__).resolve().parent.parent / "data" / "sample-circular"


def main() -> None:
    """Diff every sense's canonical gloss between the before/after pilot store copies."""
    before = LexemeStore(StoreConfig(root=BEFORE_ROOT))
    after = LexemeStore(StoreConfig(root=AFTER_ROOT))

    pairs = []
    for lexeme_id in sorted(after.iter_ids()):
        old_entry = before.read(lexeme_id)
        new_entry = after.read(lexeme_id)
        if old_entry is None or new_entry is None:
            continue
        old_senses = {sid: sense for _, sense, sid in old_entry.iter_senses()}
        for _, new_sense, sid in new_entry.iter_senses():
            old_sense = old_senses.get(sid)
            if old_sense is None:
                continue
            old_text = old_sense.canonical_gloss()
            new_text = new_sense.canonical_gloss()
            if old_text != new_text:
                pairs.append((sid, old_text, new_text))

    # This script's whole job is to report to stdout.
    print(f"total rewritten senses: {len(pairs)}\n")  # noqa: T201
    for sid, old_text, new_text in pairs:
        print(sid)  # noqa: T201
        print(f"  before: {old_text}")  # noqa: T201
        print(f"  after:  {new_text}")  # noqa: T201
        print()  # noqa: T201


if __name__ == "__main__":
    main()
