"""Free, read-only baseline scan: how many senses and how many are circular, in the pilot sample."""

from __future__ import annotations

from pathlib import Path

from opengloss_generator.config import StoreConfig
from opengloss_generator.store import LexemeStore
from opengloss_generator.workflows import content_hygiene as module

SAMPLE_ROOT = Path(__file__).resolve().parent.parent / "data" / "sample-circular"


def main() -> None:
    """Scan the pilot sample store and report circular-gloss detection counts."""
    store = LexemeStore(StoreConfig(root=SAMPLE_ROOT))
    entries_scanned = 0
    senses_scanned = 0
    circular_senses = 0
    circular_entries = 0
    examples: list[str] = []
    for lexeme_id in sorted(store.iter_ids()):
        entry = store.read(lexeme_id)
        if entry is None:
            continue
        entries_scanned += 1
        live = module._live_senses(entry)
        senses_scanned += len(live)
        offenders = module._circular_glosses(entry)
        if offenders:
            circular_entries += 1
            circular_senses += len(offenders)
            for offender in offenders[:1]:
                examples.append(f"{offender.ref_id}: {offender.rendition.content!r}")

    # This script's whole job is to report to stdout.
    print(f"entries scanned:  {entries_scanned}")  # noqa: T201
    print(f"senses scanned:   {senses_scanned}")  # noqa: T201
    print(f"entries with >=1 circular sense: {circular_entries}")  # noqa: T201
    print(f"circular senses:  {circular_senses}")  # noqa: T201
    print(f"rate (senses):    {circular_senses / senses_scanned:.4f}")  # noqa: T201
    print("sample offenders:")  # noqa: T201
    for line in examples[:15]:
        print(f"  {line}")  # noqa: T201


if __name__ == "__main__":
    main()
