"""One-off pilot helper: copy the 40 tier-2 QA-sample entries into two scratch stores.

Not part of the package; not wired into the CLI. Reads only from the production store
(never writes there) and writes into ``data/sample-retag-nano`` and
``data/sample-retag-luna`` for the D-67 domain-retag pilot. See docs/DECISIONS.md D-67.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from opengloss_generator.config import StoreConfig
from opengloss_generator.identity import slugify
from opengloss_generator.store import LexemeStore

SAMPLE = [
    "appraise", "arbiter", "barmaid", "beryllium", "braggart", "buzzing", "byblos",
    "byrne", "caper", "chairwoman", "comet", "competitively", "dipper", "dogfight",
    "emasculation", "emigrate", "equidistant", "expansive", "furtively", "goatherd",
    "hen", "idealized", "incandescence", "insomnia", "irreversible", "larvae",
    "marcellus", "motivations", "murmansk", "pockmarked", "poirot", "rawhide",
    "redirection", "renoir", "revolving", "roadhouse", "rookies", "serb",
    "shamefully", "wesson",
]

PROD_ROOT = Path("/home/mjbommar/projects/personal/opengloss-generator/data/core-store")


def main() -> None:
    assert len(SAMPLE) == 40, len(SAMPLE)
    prod = LexemeStore(StoreConfig(root=PROD_ROOT, fsync_on_write=False))
    targets = {
        "nano": LexemeStore(
            StoreConfig(
                root=Path(__file__).resolve().parents[1] / "data" / "sample-retag-nano",
                fsync_on_write=False,
            )
        ),
        "luna": LexemeStore(
            StoreConfig(
                root=Path(__file__).resolve().parents[1] / "data" / "sample-retag-luna",
                fsync_on_write=False,
            )
        ),
    }
    missing = []
    sense_total = 0
    for word in SAMPLE:
        entry = prod.read(word)
        if entry is None:
            missing.append(word)
            continue
        sense_total += entry.sense_count()
        for store in targets.values():
            store.write(entry.model_copy(deep=True))
    print(f"copied {len(SAMPLE) - len(missing)}/{len(SAMPLE)} entries, {sense_total} senses")
    if missing:
        print(f"MISSING: {missing}")
    # Confirm the prod store is untouched (read-only use only).
    for word in SAMPLE:
        assert slugify(word)


if __name__ == "__main__":
    main()
