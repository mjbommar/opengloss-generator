"""Measure meta-reference leakage and gloss-echo among stored ``qa_pairs`` output (D-69).

Zero model calls: this only reads a store and scans QA pairs already on disk with
:func:`~opengloss_generator.workflows.qa_pairs.meta_reference` and the same
``echoes_gloss`` comparison ``run_qa_pairs`` uses. It exists to measure the two defects
D-58's pilot recorded but did not fix — 7.9% meta-reference leakage, 11.6% verbatim gloss
echoes in ``definition`` answers — against a fresh sample, both before D-69's post-checks
ship (to confirm the historic rate holds) and after a whole-store regeneration (to confirm
the post-checks caught it going forward).

Usage:
    uv run python scripts/qa_pairs_meta_audit.py --store data/sample-qameta
    uv run python scripts/qa_pairs_meta_audit.py --store data/sample-qameta --json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from opengloss_generator.config import StoreConfig
from opengloss_generator.schema import QuestionType
from opengloss_generator.store import LexemeStore
from opengloss_generator.workflows.qa_pairs import (
    GLOSS_ECHO_PREFIX_LENGTH,
    _echo_key,
    _repair_meta_reference,
    meta_reference,
)


def _is_repairable(answer: str) -> bool:
    """Return whether the stage's own free repair removes every meta-reference in ``answer``."""
    stripped = _repair_meta_reference(answer)
    return stripped != answer and meta_reference(stripped) is None


def main() -> None:
    """Scan a store's existing ``qa_pairs`` output and report the two D-58 defect rates."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store", type=Path, default=Path("data/sample-qameta"))
    parser.add_argument("--examples", type=int, default=10, help="meta-reference matches to print")
    parser.add_argument("--json", action="store_true", help="print machine-readable output only")
    args = parser.parse_args()

    store = LexemeStore(StoreConfig(root=args.store, fsync_on_write=False))

    scanned = 0
    definition_scanned = 0
    meta_matches: list[dict[str, str]] = []
    repairable = 0
    echoes_gloss = 0

    for lexeme_id in sorted(store.iter_ids()):
        entry = store.read(lexeme_id)
        if entry is None:
            continue
        for _, sense, owner_sense_id in entry.iter_senses():
            if sense.retired:
                continue
            gloss = sense.canonical_gloss()
            for pair in sense.qa:
                scanned += 1
                phrase = meta_reference(pair.answer)
                if phrase is not None:
                    # Collected in full, regardless of --examples, so the printed slice
                    # below is a prefix of every match rather than a biased sample.
                    meta_matches.append(
                        {
                            "sense": owner_sense_id,
                            "question_type": pair.question_type.value,
                            "phrase": phrase,
                            "answer": pair.answer,
                        }
                    )
                    if _is_repairable(pair.answer):
                        repairable += 1
                if pair.question_type == QuestionType.DEFINITION:
                    definition_scanned += 1
                    answer_key = _echo_key(pair.answer)[:GLOSS_ECHO_PREFIX_LENGTH]
                    gloss_key = _echo_key(gloss)[:GLOSS_ECHO_PREFIX_LENGTH]
                    if answer_key == gloss_key:
                        echoes_gloss += 1

    meta_count = len(meta_matches)
    report = {
        "store": str(args.store),
        "pairs_scanned": scanned,
        "meta_reference_matches": meta_count,
        "meta_reference_rate": round(meta_count / scanned, 4) if scanned else 0.0,
        "meta_reference_repairable": repairable,
        "meta_reference_repairable_rate": round(repairable / meta_count, 4) if meta_count else 0.0,
        "definition_pairs_scanned": definition_scanned,
        "echoes_gloss": echoes_gloss,
        "echoes_gloss_rate": (
            round(echoes_gloss / definition_scanned, 4) if definition_scanned else 0.0
        ),
    }

    if args.json:
        output = json.dumps({**report, "examples": meta_matches[: args.examples]}, indent=2)
        print(output)  # noqa: T201 - this script's whole job is to report to stdout
        return

    shown = meta_matches[: args.examples]
    lines = [
        f"store: {report['store']}",
        f"pairs scanned: {report['pairs_scanned']}",
        f"meta-reference matches: {report['meta_reference_matches']} "
        f"({report['meta_reference_rate']:.2%})",
        f"  of which repairable by the leading-clause strip: "
        f"{report['meta_reference_repairable']} "
        f"({report['meta_reference_repairable_rate']:.2%} of matches)",
        f"definition pairs scanned: {report['definition_pairs_scanned']}",
        f"echoes_gloss (verbatim first {GLOSS_ECHO_PREFIX_LENGTH} chars): "
        f"{report['echoes_gloss']} ({report['echoes_gloss_rate']:.2%})",
        f"\n{len(shown)} example meta-reference matches:",
    ]
    for match in shown:
        lines.append(f"  [{match['sense']}] ({match['question_type']}, phrase={match['phrase']!r})")
        lines.append(f"    {match['answer']}")
    print("\n".join(lines))  # noqa: T201 - this script's whole job is to report to stdout


if __name__ == "__main__":
    main()
