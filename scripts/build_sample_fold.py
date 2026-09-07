"""Build the frozen sample the ``lexeme-hygiene`` pilot runs over (D-79).

The two defects this pass exists for are measured in
``opengloss-paper/docs/v2.0/review/F-wordnet-diff.md``: 13,139 of the v2.1 release's headwords
are a plural / past tense / participle / comparative of *another* lexeme in the same store, and
306 multiword headwords begin or end with a function word. So the draw is 300 of the first kind
and 300 controls, half of them the second kind and half of them ordinary entries that must
survive both steps untouched.

The one thing this script does that ``build_sample_phantom.py`` did not have to: it copies
**supporting entries** as well as sampled ones. ``inflection_fold`` is the project's first
cross-entry question, so a candidate whose lemma is not in the sample store resolves to
``skipped_lemma_absent`` and measures nothing; and its ``skipped_is_lemma`` guard asks whether
some *other* live entry is an inflection of the candidate, which is likewise unanswerable from
the candidate alone. Both are therefore pulled in for every sampled candidate, so that the
sample store answers each guard exactly as the production store does. Supporting entries are
not in ``sample-fold.tsv`` and are never visited: the pilot passes that list to ``--from-list``.

Copies entries read-only from the main checkout's ``data/core-store`` into this worktree's
``data/sample-fold/``, preserving the store's own blake2b shard layout so the copy is a valid
``LexemeStore`` on its own. This never writes to the source store.

The five plurals that are the failure to avoid — "glasses", "arms", "customs", "goods",
"manners" — are forced into the draw whenever the production store holds them as candidates, so
the pilot's false-fold risk is measured on the words most likely to expose it rather than on
whatever the seed happened to pick.

Usage:
    uv run python scripts/build_sample_fold.py
"""

from __future__ import annotations

import json
import random
import shutil
from collections import defaultdict
from pathlib import Path

from opengloss_generator.identity import shard_for
from opengloss_generator.workflows.lexeme_hygiene import (
    INFLECTION_RELATIONS,
    LEADING_FUNCTION_WORDS,
    TRAILING_FUNCTION_WORDS,
)

_MAIN_CHECKOUT = Path("/home/mjbommar/projects/personal/opengloss-generator")
_SOURCE_STORE = _MAIN_CHECKOUT / "data/core-store"
_DEST_STORE = Path("data/sample-fold")
_HEADWORD_LIST = Path("data/sample-fold.tsv")

#: The draw. Seeded so the pilot is repeatable.
_SEED = 79
_FOLD_CANDIDATES = 300
_FRAGMENT_CONTROLS = 150
_PLAIN_CONTROLS = 150

#: Forced into the fold draw: five plurals whose entries carry a meaning the singular does not.
#: A pilot that folded any of these would be a pilot that had failed, so they are measured every
#: time rather than when the seed obliges.
_MUST_KEEP = ("glasses", "arms", "customs", "goods", "manners")


def _live(record: dict) -> bool:
    """Return whether a raw entry payload still has a live sense anywhere."""
    return any(
        not sense.get("retired", False)
        for pos_entry in record.get("pos_entries", [])
        for sense in pos_entry.get("senses", [])
    )


def _read_all() -> list[dict]:
    """Return every entry payload in the source store, read-only.

    Returns:
        The parsed payloads, in filesystem order.
    """
    return [json.loads(path.read_bytes()) for path in _SOURCE_STORE.rglob("*.json")]


def _fragment_candidate(headword: str) -> bool:
    """Return whether a headword is bounded by a function word, the ``fragments`` gate."""
    tokens = headword.lower().split()
    if len(tokens) < 2:
        return False
    leading = frozenset().union(*LEADING_FUNCTION_WORDS.values())
    trailing = frozenset().union(*TRAILING_FUNCTION_WORDS.values())
    return tokens[0] in leading or tokens[-1] in trailing


def main() -> None:
    """Draw the sample, copy every entry it needs, and write the headword list beside it."""
    records = _read_all()
    live = [record for record in records if _live(record)]
    by_key = {" ".join(record["headword"].lower().split()): record for record in live}

    # form key -> the live entries recording it as an inflection, and the reverse.
    recorded: dict[str, set[str]] = defaultdict(set)
    for record in live:
        own = " ".join(record["headword"].lower().split())
        for pos_entry in record.get("pos_entries", []):
            morphology = pos_entry.get("morphology") or {}
            for relation in INFLECTION_RELATIONS:
                form = morphology.get(relation)
                if form:
                    key = " ".join(form.lower().split())
                    if key != own:
                        recorded[key].add(own)

    fold_pool = sorted(key for key in by_key if key in recorded)
    fragment_pool = sorted(key for key in by_key if _fragment_candidate(key))
    plain_pool = sorted(
        key for key in by_key if key not in recorded and not _fragment_candidate(key)
    )

    rng = random.Random(_SEED)  # noqa: S311 - sampling, not crypto
    forced = [key for key in _MUST_KEEP if key in fold_pool]
    rest = [key for key in fold_pool if key not in set(forced)]
    folds = forced + rng.sample(rest, min(_FOLD_CANDIDATES - len(forced), len(rest)))
    fragments = rng.sample(fragment_pool, min(_FRAGMENT_CONTROLS, len(fragment_pool)))
    plains = rng.sample(plain_pool, min(_PLAIN_CONTROLS, len(plain_pool)))
    visited = sorted({*folds, *fragments, *plains})

    # Supporting entries: every lemma a sampled candidate would fold onto, and every live entry
    # that makes a sampled candidate itself a lemma. Both decide a guard, neither is visited.
    supporting: set[str] = set()
    for key in folds:
        supporting |= recorded.get(key, set())
        record = by_key[key]
        for pos_entry in record.get("pos_entries", []):
            morphology = pos_entry.get("morphology") or {}
            for relation in INFLECTION_RELATIONS:
                form = morphology.get(relation)
                if form and " ".join(form.lower().split()) in by_key:
                    supporting.add(" ".join(form.lower().split()))
    supporting -= set(visited)

    _DEST_STORE.mkdir(parents=True, exist_ok=True)
    for key in sorted({*visited, *supporting}):
        lexeme_id = by_key[key]["lexeme_id"]
        source = _SOURCE_STORE.joinpath(*shard_for(lexeme_id), f"{lexeme_id}.json")
        dest_dir = _DEST_STORE.joinpath(*shard_for(lexeme_id))
        dest_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, dest_dir / f"{lexeme_id}.json")

    # A real two-column TSV, not one headword per bare line: `cli._read_word_list` only reads
    # a whole multi-word headword when the file declares a `word` column, and falls back to the
    # first whitespace-separated token otherwise ("academic institution" -> "academic").
    groups = {
        **{key: "plain" for key in plains},
        **{key: "fragment" for key in fragments},
        **{key: "fold" for key in folds},
    }
    _HEADWORD_LIST.write_text(
        "word\tgroup\n"
        + "\n".join(f"{by_key[key]['headword']}\t{groups[key]}" for key in visited)
        + "\n",
        encoding="utf-8",
    )
    print(  # noqa: T201 - reports to stdout
        f"{len(visited)} visited entries into {_DEST_STORE} "
        f"({len(folds)} fold candidates, {len(fragments)} fragment candidates, "
        f"{len(plains)} plain controls) plus {len(supporting)} supporting entries; "
        f"pools: fold {len(fold_pool)}, fragment {len(fragment_pool)}, plain {len(plain_pool)}"
    )


if __name__ == "__main__":
    main()
