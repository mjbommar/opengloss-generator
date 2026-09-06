"""Build the frozen sample for the ``relation-reconcile --only retype`` measurement (D-73).

The ``retype`` step acts on a ``related_differently`` contrast whose edge is **still
live and still symmetric**. On ``data/core-store`` that population is empty: D-68's
``verdicts`` step demoted every one of those edges to ``see_also`` and ``tombstone``
removed it in the same sweep, weeks before this step existed (measured 2026-09-06: of
24,611 ``related_differently`` contrasts in the store, **zero** still match a live edge).
The step is therefore forward-looking — it earns its keep on entries whose contrasts are
newer than their last reconcile — and a pilot needs the pre-``verdicts`` state back.

This script rebuilds exactly that state, and rebuilds it from evidence rather than from a
guess. A contrast's key *is* the edge it was written about: ``falcon:noun:0-synonym->
peregrine`` names the asserting sense, the type the edge carried, and the target lexeme.
Where that edge is gone from the entry, re-adding a relation of that type toward that
target restores what the ``contrasts`` stage saw — nothing is invented, because every
field comes out of the stored contrast (the target's *sense* comes from
``Contrast.target_sense_id``, and its surface form from the target entry's own headword,
so no slug is un-slugged).

The reverse edge is restored the same way when the far entry no longer holds one, because
a contrast is written once per *undirected* pair (D-57 §1) and the far-side inverse retype
is the half of the step that cannot be measured otherwise.

Copies entries read-only from the main checkout's ``data/core-store`` into this worktree's
``data/sample-retype/``, preserving the store's own blake2b shard layout so the copy is a
valid ``LexemeStore`` on its own. **It never writes to the source store.**

Usage:
    uv run python scripts/build_sample_retype.py
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from opengloss_generator.config import StoreConfig
from opengloss_generator.identity import shard_for, slugify
from opengloss_generator.schema import ContrastVerdict, Relation, RelationTarget, RelationType
from opengloss_generator.store import LexemeStore

_MAIN_CHECKOUT = Path("/home/mjbommar/projects/personal/opengloss-generator")
_SOURCE_STORE = _MAIN_CHECKOUT / "data/core-store"
_WORD_LISTS = (
    _MAIN_CHECKOUT / "data/core/core_10k.tsv",
    _MAIN_CHECKOUT / "data/core/tier2_50k.tsv",
)
_DEST_STORE = Path("data/sample-retype")
_HEADWORD_LIST = Path("data/sample-retype.tsv")

#: How many contrast-bearing seed entries the sample holds.
_SEEDS = 400

#: The two symmetric types the step judges. ``confusable_with`` is excluded here rather
#: than in the step: its schema requires a note, and a rebuilt edge has none to give.
_KINDS = frozenset({RelationType.SYNONYM.value, RelationType.ANTONYM.value})


def _source_path(lexeme_id: str) -> Path:
    """Return the read-only source file for one lexeme id."""
    return _SOURCE_STORE.joinpath(*shard_for(lexeme_id), f"{lexeme_id}.json")


def _words() -> list[str]:
    """Return the headwords of both core lists, in rank order, deduplicated."""
    seen: dict[str, None] = {}
    for path in _WORD_LISTS:
        for line in path.read_text(encoding="utf-8").splitlines()[1:]:
            fields = line.split("\t")
            if len(fields) > 1:
                seen.setdefault(slugify(fields[1]), None)
    return list(seen)


def _split_edge(edge_id: str) -> tuple[str, str, str] | None:
    """Return ``(sense id, relation type, target lexeme)`` for one contrast key."""
    source, separator, target = edge_id.partition("->")
    if not separator:
        return None
    sense_id, dash, relation_type = source.rpartition("-")
    if not dash:
        return None
    return sense_id, relation_type, target


def _rewindable(payload: dict) -> list[tuple[str, str, str, str]]:
    """Return the contrasts on one entry whose edge this script can restore.

    Args:
        payload: The stored entry, as JSON.

    Returns:
        One ``(sense id, relation type, target lexeme, target sense id)`` per
        ``related_differently`` contrast keyed on a live sense of the entry, typed
        ``synonym`` or ``antonym``, resolved to a target sense, and **not** currently
        asserted by that sense — the edge D-68's ``verdicts`` step took away.
    """
    live_senses = {
        f"{payload['lexeme_id']}:{pos_entry['pos']}:{sense['index']}"
        for pos_entry in payload.get("pos_entries", [])
        for sense in pos_entry.get("senses", [])
        if not sense.get("retired")
    }
    asserted = {
        (
            f"{payload['lexeme_id']}:{pos_entry['pos']}:{sense['index']}",
            slugify(relation["target"]["term"]),
        )
        for pos_entry in payload.get("pos_entries", [])
        for sense in pos_entry.get("senses", [])
        if not sense.get("retired")
        for relation in sense.get("relations", [])
    }
    out: list[tuple[str, str, str, str]] = []
    for contrast in payload.get("contrasts") or []:
        if contrast.get("verdict") != ContrastVerdict.RELATED_DIFFERENTLY.value:
            continue
        parts = _split_edge(contrast["edge_id"])
        target_sense = contrast.get("target_sense_id")
        if parts is None or target_sense is None:
            continue
        sense_id, relation_type, target_lexeme = parts
        if relation_type not in _KINDS or sense_id not in live_senses:
            continue
        if (sense_id, target_lexeme) in asserted:
            continue
        out.append((sense_id, relation_type, target_lexeme, target_sense))
    return out


def _copy(lexeme_id: str) -> None:
    """Copy one entry into the destination store, preserving the shard layout."""
    dest_dir = _DEST_STORE.joinpath(*shard_for(lexeme_id))
    dest_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(_source_path(lexeme_id), dest_dir / f"{lexeme_id}.json")


def _restore(store: LexemeStore, lexeme_id: str, plan: list[tuple[str, str, str, str]]) -> int:
    """Re-add the edges one seed's contrasts were written about. Returns how many."""
    entry = store.read(lexeme_id)
    if entry is None:
        return 0
    restored = 0
    for sense_id, relation_type, target_lexeme, target_sense in plan:
        target_entry = store.read(target_lexeme)
        if target_entry is None:
            continue
        for _, sense, sid in entry.iter_senses():
            if sid != sense_id or sense.retired:
                continue
            sense.relations.append(
                Relation(
                    type=RelationType(relation_type),
                    target=RelationTarget(
                        term=target_entry.headword, sense_id=target_sense, confidence=0.9
                    ),
                )
            )
            restored += 1
    if restored:
        store.write(entry)
    return restored


def _restore_reverse(
    store: LexemeStore, lexeme_id: str, plan: list[tuple[str, str, str, str]]
) -> int:
    """Re-add the reverse of each restored edge on the far entry. Returns how many."""
    seed = store.read(lexeme_id)
    if seed is None:
        return 0
    restored = 0
    for sense_id, relation_type, target_lexeme, target_sense in plan:
        far = store.read(target_lexeme)
        if far is None:
            continue
        for _, sense, sid in far.iter_senses():
            if sid != target_sense or sense.retired:
                continue
            if any(r.target.lexeme_id == lexeme_id for r in sense.relations):
                continue
            sense.relations.append(
                Relation(
                    type=RelationType(relation_type),
                    target=RelationTarget(term=seed.headword, sense_id=sense_id, confidence=0.9),
                )
            )
            restored += 1
        if restored:
            store.write(far)
    return restored


def main() -> None:
    """Copy 400 rewindable contrast-bearing entries, their far sides, and rewind them."""
    known = {path.stem for path in _SOURCE_STORE.rglob("*.json")}
    plans: dict[str, list[tuple[str, str, str, str]]] = {}
    neighbours: set[str] = set()
    for lexeme_id in _words():
        if len(plans) >= _SEEDS:
            break
        if lexeme_id not in known:
            continue
        payload = json.loads(_source_path(lexeme_id).read_text(encoding="utf-8"))
        plan = [item for item in _rewindable(payload) if item[2] in known]
        if not plan:
            continue
        plans[lexeme_id] = plan
        neighbours.update(item[2] for item in plan)
        # Everything the entry's own resolved edges point at, so the far-side halves of
        # every step in the pass have somewhere to land, not just this step's.
        for pos_entry in payload.get("pos_entries", []):
            for sense in pos_entry.get("senses", []):
                if sense.get("retired"):
                    continue
                for relation in sense.get("relations", []):
                    sense_id = (relation.get("target") or {}).get("sense_id")
                    if sense_id and sense_id.split(":")[0] in known:
                        neighbours.add(sense_id.split(":")[0])

    chosen = sorted(set(plans) | neighbours)
    _DEST_STORE.mkdir(parents=True, exist_ok=True)
    for lexeme_id in chosen:
        _copy(lexeme_id)

    store = LexemeStore(StoreConfig(root=_DEST_STORE, fsync_on_write=False))
    edges = sum(_restore(store, lexeme_id, plan) for lexeme_id, plan in sorted(plans.items()))
    reverses = sum(
        _restore_reverse(store, lexeme_id, plan) for lexeme_id, plan in sorted(plans.items())
    )

    _HEADWORD_LIST.write_text("\n".join(sorted(plans)) + "\n", encoding="utf-8")
    print(  # noqa: T201 - reports to stdout
        f"copied {len(chosen)} entries into {_DEST_STORE} ({len(plans)} seeds carrying "
        f"rewindable contrasts, {len(chosen) - len(plans)} far sides); "
        f"restored {edges} contrast edges and {reverses} reverses"
    )


if __name__ == "__main__":
    main()
