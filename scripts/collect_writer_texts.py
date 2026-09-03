"""Collect each writer's generated text from a pilot arm's store into one JSONL (D-63).

"Generated" means any rendition in a live sense's ``examples`` set other than
(neutral, plain) — the pilot's two tasks never target that pair (see
``reset_writer_arm.py``), so it is exactly the pre-existing canonical content, in every
arm including ``luna``. Output rows: one per generated sentence, with enough fields to
drive the attribution classifier, lexical-diversity measures, and the shared-sense
example table in the pilot writeup.

Usage:
    uv run python scripts/collect_writer_texts.py --arm qwen --writer qwen/qwen3.5-397b-a17b
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from opengloss_generator.config import StoreConfig
from opengloss_generator.schema import ReadingLevel, Register
from opengloss_generator.store import LexemeStore


def main() -> None:
    """Write one JSONL row per generated sentence in one arm's store."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arm", required=True)
    parser.add_argument("--writer", required=True, help="Model id to stamp on every row.")
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()
    out = args.out or Path(f"data/writer-texts-{args.arm}.jsonl")

    store = LexemeStore(StoreConfig(root=Path(f"data/sample-writers-{args.arm}")))
    words = Path("data/sample-writers.tsv").read_text(encoding="utf-8").split()

    rows = []
    for word in words:
        entry = store.read(word)
        if entry is None:
            continue
        for _pos_entry, sense, sense_id in entry.iter_senses():
            if sense.retired:
                continue
            canonical = sense.canonical_gloss()
            for r in sense.examples:
                if r.reading_level is ReadingLevel.NEUTRAL and r.style is Register.PLAIN:
                    continue  # pre-existing canonical content, not generated
                rows.append(
                    {
                        "arm": args.arm,
                        "writer": args.writer,
                        "lexeme_id": entry.lexeme_id,
                        "headword": entry.headword,
                        "sense_id": sense_id,
                        "reading_level": r.reading_level.value,
                        "style": r.style.value,
                        "text": r.content.text,
                        "canonical_gloss": canonical,
                    }
                )

    out.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
    print(f"{args.arm}: {len(rows)} generated sentences -> {out}")


if __name__ == "__main__":
    main()
