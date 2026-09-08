"""Build the frozen sample for the tier-6 schema pilots (D-81).

Two populations, because the two passes measured on it ask different questions.

``aliases`` needs **pairs**: a long name and the single-word entry the store already holds
for part of it. The production tier-6 candidate list proposes 9,475 such pairs, but not one
of them can be piloted yet — the long-name half is precisely what the tier does not have
(``in_store = 0`` on 12,718 of its 15,000 rows), so stage 1 and stage 2 have to run before
a single pair exists. The store already holds **3,475 pairs of exactly the same shape**
from the earlier tiers, though: a multi-word proper noun whose last token is a live entry
of its own (*Golden Horde* / *horde*, *Royal Society* / *society*, *Black Plague* /
*plague*). Those are what the pass will face, so those are what it is measured on. The
sample copies the first :data:`_PAIRS` of them in id order, both halves, and writes a
candidate TSV in the production file's own column shape so the pass reads it unchanged.

``entity_type`` needs **proper nouns the candidate list does not name**, or the model half
of the pass is never exercised. So :data:`_OTHERS` more are drawn from the store at large,
in id order for reproducibility.

Copies entries read-only from the main checkout's ``data/core-store`` into this worktree's
``data/sample-ner/``, preserving the store's own blake2b shard layout so the copy is a
valid ``LexemeStore`` on its own. This never writes to the source store.

Usage:
    uv run python scripts/build_sample_ner.py
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from opengloss_generator.identity import shard_for, slugify
from opengloss_generator.schema import LexemeKind

_MAIN_CHECKOUT = Path("/home/mjbommar/projects/personal/opengloss-generator")
_SOURCE_STORE = _MAIN_CHECKOUT / "data/core-store"
_DEST_STORE = Path("data/sample-ner")
_HEADWORD_LIST = Path("data/sample-ner.tsv")
_CANDIDATE_LIST = Path("data/sample-ner-candidates.tsv")

#: Alias pairs to copy, both halves each.
_PAIRS = 100
#: Further proper nouns, for the entity_type pass's model half.
_OTHERS = 150

#: The production candidate file's header, so the pilot file is read by exactly the same
#: header-driven reader with no special case anywhere.
_HEADER = (
    "name\tword\tentity_type\tsource\tsources\timportance_score\tvital_level\tsitelinks\t"
    "in_wordnet\tin_v13\tin_store\tstore_slug\tqid\tscore_terms\tnotes"
)


def _source_path(lexeme_id: str) -> Path:
    """Return the read-only source file for one lexeme id."""
    return _SOURCE_STORE.joinpath(*shard_for(lexeme_id), f"{lexeme_id}.json")


def main() -> None:
    """Copy the alias pairs and a proper-noun control set, and write the pilot TSV."""
    known = {path.stem: path for path in _SOURCE_STORE.rglob("*.json")}
    proper: dict[str, str] = {}
    for lexeme_id in sorted(known):
        payload = json.loads(known[lexeme_id].read_text(encoding="utf-8"))
        if payload.get("kind") == LexemeKind.PROPER_NOUN.value:
            proper[lexeme_id] = payload["headword"]

    pairs: list[tuple[str, str, str]] = []
    for lexeme_id, headword in proper.items():
        if len(pairs) >= _PAIRS:
            break
        tokens = headword.split()
        if len(tokens) < 2:
            continue
        target = slugify(tokens[-1])
        if target != lexeme_id and target in known:
            pairs.append((lexeme_id, headword, target))

    paired = {half for lexeme_id, _, target in pairs for half in (lexeme_id, target)}
    others = [
        lexeme_id for lexeme_id in proper if lexeme_id not in paired
    ][:_OTHERS]

    _DEST_STORE.mkdir(parents=True, exist_ok=True)
    for lexeme_id in sorted(paired | set(others)):
        dest_dir = _DEST_STORE.joinpath(*shard_for(lexeme_id))
        dest_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(_source_path(lexeme_id), dest_dir / f"{lexeme_id}.json")

    _HEADWORD_LIST.write_text(
        "\n".join(sorted(paired | set(others))) + "\n", encoding="utf-8"
    )
    rows = [
        "\t".join(
            (
                headword,
                headword,
                "",  # entity_type: unknown for a store-derived row, which is the point
                "name_seed",
                "store",
                "0",
                "",
                "0",
                "0",
                "0",
                "1",
                lexeme_id,
                "",
                "",
                f"alias_of candidate: store has '{target}'",
            )
        )
        for lexeme_id, headword, target in pairs
    ]
    _CANDIDATE_LIST.write_text("\n".join([_HEADER, *rows]) + "\n", encoding="utf-8")
    print(  # noqa: T201 - reports to stdout
        f"copied {len(paired | set(others))} entries into {_DEST_STORE} "
        f"({len(pairs)} alias pairs, {len(others)} further proper nouns); "
        f"pilot candidate list at {_CANDIDATE_LIST}"
    )


if __name__ == "__main__":
    main()
