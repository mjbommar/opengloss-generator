"""Build the frozen sample the ``sense-hygiene --only phantom_pos`` pilot runs over (D-76).

The defect this step exists for is a *tier-4 inheritance*: OpenGloss v1.3 wrote a sense under
every part of speech its generator guessed at, so a compound headword routinely carries an
adjective or verb POS entry whose glosses define one *component word* rather than the compound
(``docs/QA-DIARY.md`` iteration 18 — ``blank cell`` with an adjective entry defining "blank").
Tier 4 is where those entries live and 63% of it is multiword, so the sample is drawn from
``data/core/tier4.tsv`` with multiword headwords deliberately over-weighted: 300 of the 400 are
multiword, against the population's 63%, because a single-word entry is only in the sample to
keep the false-retire risk on ordinary polysemous entries measurable.

Copies entries read-only from the main checkout's ``data/core-store`` into this worktree's
``data/sample-phantom/``, preserving the store's own blake2b shard layout so the copy is a valid
``LexemeStore`` on its own. This never writes to the source store, which a chain is using.

Usage:
    uv run python scripts/build_sample_phantom.py
"""

from __future__ import annotations

import csv
import random
import shutil
from pathlib import Path

from opengloss_generator.identity import shard_for, slugify

_MAIN_CHECKOUT = Path("/home/mjbommar/projects/personal/opengloss-generator")
_SOURCE_STORE = _MAIN_CHECKOUT / "data/core-store"
_WORD_LIST = _MAIN_CHECKOUT / "data/core/tier4.tsv"
_DEST_STORE = Path("data/sample-phantom")
_HEADWORD_LIST = Path("data/sample-phantom.tsv")

#: The draw: 400 entries, 300 of them multiword. Seeded so the pilot is repeatable.
_SEED = 18
_MULTIWORD = 300
_SINGLE_WORD = 100


def _source_path(lexeme_id: str) -> Path:
    """Return the read-only source file for one lexeme id."""
    return _SOURCE_STORE.joinpath(*shard_for(lexeme_id), f"{lexeme_id}.json")


def _candidates() -> tuple[list[str], list[str]]:
    """Return the multiword and single-word tier-4 headwords the store actually holds."""
    multiword: list[str] = []
    single: list[str] = []
    with _WORD_LIST.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle, delimiter="\t"):
            word = row["word"]
            try:
                lexeme_id = slugify(word)
            except ValueError:
                continue
            if not _source_path(lexeme_id).exists():
                continue
            (multiword if " " in word else single).append(word)
    return multiword, single


def main() -> None:
    """Draw the sample, copy every entry in it, and write the headword list beside it."""
    multiword, single = _candidates()
    rng = random.Random(_SEED)  # noqa: S311 - sampling, not crypto
    chosen = rng.sample(multiword, min(_MULTIWORD, len(multiword)))
    chosen += rng.sample(single, min(_SINGLE_WORD, len(single)))
    chosen.sort()

    _DEST_STORE.mkdir(parents=True, exist_ok=True)
    for word in chosen:
        lexeme_id = slugify(word)
        dest_dir = _DEST_STORE.joinpath(*shard_for(lexeme_id))
        dest_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(_source_path(lexeme_id), dest_dir / f"{lexeme_id}.json")

    _HEADWORD_LIST.write_text("\n".join(chosen) + "\n", encoding="utf-8")
    print(  # noqa: T201 - reports to stdout
        f"copied {len(chosen)} entries into {_DEST_STORE} "
        f"({sum(1 for w in chosen if ' ' in w)} multiword) "
        f"from {len(multiword)} multiword / {len(single)} single-word candidates"
    )


if __name__ == "__main__":
    main()
