"""One-off pilot helper: diff domain tags between the untouched nano copy and the
luna-retagged copy, for the D-67 domain-retag pilot. Not part of the package.
"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from opengloss_generator.config import StoreConfig
from opengloss_generator.store import LexemeStore

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    nano = LexemeStore(StoreConfig(root=ROOT / "data" / "sample-retag-nano", fsync_on_write=False))
    luna = LexemeStore(StoreConfig(root=ROOT / "data" / "sample-retag-luna", fsync_on_write=False))

    changed = 0
    unchanged = 0
    total = 0
    transitions: Counter[tuple[str, str]] = Counter()
    changed_rows: list[tuple[str, str, str, str]] = []

    nano_ids = sorted(e.lexeme_id for e in nano.iter_entries())
    for lexeme_id in nano_ids:
        old_entry = nano.read(lexeme_id)
        new_entry = luna.read(lexeme_id)
        for (pos_entry, sense, _), (_, new_sense, _) in zip(
            old_entry.iter_senses(), new_entry.iter_senses(), strict=True
        ):
            if sense.retired:
                continue
            total += 1
            old_d = sense.domain.value if sense.domain else "<none>"
            new_d = new_sense.domain.value if new_sense.domain else "<none>"
            if old_d == new_d:
                unchanged += 1
            else:
                changed += 1
                transitions[(old_d, new_d)] += 1
                changed_rows.append(
                    (lexeme_id, f"{pos_entry.pos.value} {sense.index}", old_d, new_d)
                )

    print(f"total live senses: {total}")
    print(f"changed: {changed} ({changed / total:.1%})")
    print(f"unchanged: {unchanged} ({unchanged / total:.1%})")
    print()
    print("Top transitions (old -> new):")
    for (old, new), count in transitions.most_common(15):
        print(f"  {count:3d}  {old} -> {new}")
    print()
    print("All changed senses:")
    for lexeme_id, label, old_d, new_d in changed_rows:
        print(f"  {lexeme_id} ({label}): {old_d} -> {new_d}")


if __name__ == "__main__":
    main()
