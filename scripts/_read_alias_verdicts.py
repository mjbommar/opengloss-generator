"""Scratch script (D-81/D-84 pilot): read every alias_of / see_also / none answer.

For each candidate pair in the sample's candidate TSV, reads the post-run store and
reports: the verdict written (alias_of / see_also / none), whether it was free (the
target's own gloss already names the long headword -- D-8) or a bought nano verdict, and
whether the pair is a known TRUE alias (the person/abbreviation pairs added by
``_add_true_alias_pairs.py``) or a compound-head pair (D-81's original 100, where the
correct answer is see_also/none, never alias_of).

Usage:
    uv run python scripts/_read_alias_verdicts.py data/sample-ner-before
    uv run python scripts/_read_alias_verdicts.py data/sample-ner-after
"""

from __future__ import annotations

import sys
from pathlib import Path

from opengloss_generator.config import StoreConfig
from opengloss_generator.schema import RelationType
from opengloss_generator.store import LexemeStore
from opengloss_generator.wordnet_import import read_candidate_rows
from opengloss_generator.workflows.lexeme_hygiene import _live_glosses, _target_names_headword

_CANDIDATE_LIST = Path("data/sample-ner-candidates.tsv")

#: The slugs `_add_true_alias_pairs.py` paired a long name against -- the true-alias
#: population. Everything else in the candidate list is a D-81 compound-head pair.
_TRUE_ALIAS_TARGETS = {
    "lincoln",
    "darwin",
    "einstein",
    "shakespeare",
    "mozart",
    "beethoven",
    "bach",
    "picasso",
    "freud",
    "marx",
    "gandhi",
    "hitler",
    "stalin",
    "tolstoy",
    "dickens",
    "lenin",
    "luther",
    "galileo",
    "aristotle",
    "socrates",
    "nero",
    "cleopatra",
    "mandela",
    "nato",
    "fbi",
    "cia",
    "nasa",
    "unesco",
    "fifa",
    "nba",
    "nfl",
}


def main() -> None:
    store_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("data/sample-ner-before")
    store = LexemeStore(StoreConfig(root=store_path))
    rows = read_candidate_rows(_CANDIDATE_LIST)

    counts: dict[str, int] = {"alias_of": 0, "see_also": 0, "none": 0}
    true_alias_rows = []
    compound_alias_rows = []

    for row in rows:
        if row.alias_target is None:
            continue
        entry = store.read(row.lexeme_id)
        if entry is None:
            continue
        target_entry = store.read(row.alias_target)
        relations = [
            (rel.type, rel.target.term)
            for _, sense, _ in entry.iter_senses()
            if not sense.retired
            for rel in sense.relations
        ]
        alias_rel = next((t for t, _ in relations if t is RelationType.ALIAS_OF), None)
        see_also_rel = next((t for t, _ in relations if t is RelationType.SEE_ALSO), None)
        if alias_rel is not None:
            verdict = "alias_of"
        elif see_also_rel is not None:
            verdict = "see_also"
        else:
            verdict = "none"
        counts[verdict] += 1

        free = (
            target_entry is not None
            and _live_glosses(entry)
            and _target_names_headword(target_entry, entry.headword)
        )
        line = (
            f"{verdict:10s} {'FREE' if free else 'BOUGHT':6s} "
            f"{row.word!r:55s} -> {row.alias_target!r}"
        )
        if row.alias_target in _TRUE_ALIAS_TARGETS:
            true_alias_rows.append((verdict, line))
        else:
            compound_alias_rows.append((verdict, line))

    report = [f"=== {store_path} ===", f"counts: {counts}", ""]
    report.append("--- TRUE ALIAS subset (expect alias_of) ---")
    report.extend(line for _, line in true_alias_rows)
    tp = sum(1 for v, _ in true_alias_rows if v == "alias_of")
    report.append(f"true-alias recall: {tp}/{len(true_alias_rows)}")
    report.append("")
    report.append("--- alias_of answers on the COMPOUND-HEAD subset (false positives if any) ---")
    report.extend(line for verdict, line in compound_alias_rows if verdict == "alias_of")
    fp = sum(1 for v, _ in compound_alias_rows if v == "alias_of")
    report.append(f"compound-head false positives: {fp}/{len(compound_alias_rows)}")
    print("\n".join(report))  # noqa: T201 - reports to stdout


if __name__ == "__main__":
    main()
