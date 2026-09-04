"""F4 — ``export.qrels``: TREC-style graded qrels, docs, and listwise JSONL (D-56).

Everything here is offline: no model is called anywhere in ``export/qrels.py``. The
hand-built world mirrors ``tests/test_export_triples.py``'s (own sense, a direct
synonym, a hypernym/co-hyponym pair, and every graph hard-negative kind) so the two
files can be read side by side; it is rebuilt here rather than imported, matching the
rest of this codebase's practice of a self-contained fixture per test file (see
``tests/test_graph_hygiene.py``'s own ``_store``/``_entry``).
"""

from __future__ import annotations

from pathlib import Path

import orjson
import pytest

from opengloss_generator.config import StoreConfig
from opengloss_generator.export.qrels import (
    GRADE_ENCYCLOPEDIA_RELATED,
    GRADE_HYPERNYM_OR_COHYPONYM,
    GRADE_OWN_SENSE,
    GRADE_SYNONYM,
    GRADE_UNRELATED,
    MAX_GRADE_1,
    MAX_GRADE_2,
    ListwiseQuery,
    QrelsResult,
    build_qrels,
    write_qrels,
)
from opengloss_generator.schema import (
    Example,
    Lexeme,
    LexemeKind,
    PartOfSpeech,
    POSEntry,
    Relation,
    RelationTarget,
    RelationType,
    Renditions,
    Sense,
    canonical_rendition,
)
from opengloss_generator.store import LexemeStore

# --------------------------------------------------------------------------------------
# Same small world as tests/test_export_triples.py (see that file's diagram); this one
# additionally gives `lender` four synonyms so grade-2 sampling (MAX_GRADE_2) is exercised.
# --------------------------------------------------------------------------------------


def _sense(
    index: int, gloss: str, *, relations: list[Relation] | None = None, retired: bool = False
) -> Sense:
    """Build a sense with just a canonical gloss and, optionally, relations."""
    return Sense(
        index=index,
        gloss=Renditions[str](root=[canonical_rendition(gloss)]),
        examples=Renditions[Example](root=[]),
        relations=relations or [],
        retired=retired,
    )


def _entry(headword: str, senses: list[Sense], *, encyclopedia: str | None = None) -> Lexeme:
    """Build a single-POS (noun) entry from a list of senses."""
    encyclopedia_renditions = Renditions[str](root=[])
    if encyclopedia is not None:
        encyclopedia_renditions = Renditions[str](root=[canonical_rendition(encyclopedia)])
    return Lexeme.empty(
        headword,
        kind=LexemeKind.SIMPLEX,
        pos_entries=[POSEntry(pos=PartOfSpeech.NOUN, senses=senses)],
        encyclopedia=encyclopedia_renditions,
    )


def _resolved(
    relation_type: RelationType, term: str, sense_id: str, *, note: str | None = None
) -> Relation:
    """Build a relation already resolved to ``sense_id``."""
    return Relation(
        type=relation_type,
        target=RelationTarget(term=term, sense_id=sense_id, confidence=0.9),
        note=note,
    )


def _store(tmp_path: Path) -> LexemeStore:
    """Return an empty store rooted under ``tmp_path``."""
    return LexemeStore(StoreConfig(root=tmp_path / "store", fsync_on_write=False))


@pytest.fixture
def world(tmp_path: Path) -> LexemeStore:
    """Build and persist the hand-crafted store described above."""
    store = _store(tmp_path)

    bank = _entry(
        "bank",
        [
            _sense(
                0,
                "A financial institution that holds money for customers.",
                relations=[
                    _resolved(RelationType.SYNONYM, "lender", "lender:noun:0"),
                    _resolved(
                        RelationType.CONFUSABLE_WITH,
                        "embankment",
                        "embankment:noun:0",
                        note="a bank is an institution; an embankment is an earthwork",
                    ),
                ],
            ),
            _sense(
                1,
                "The land alongside a river.",
                relations=[_resolved(RelationType.HYPERNYM, "landform", "landform:noun:0")],
            ),
        ],
        # Two live senses (D-71): the encyclopedia is entry-level, so it must be graded 1
        # (GRADE_ENCYCLOPEDIA_RELATED), never 3 or 0.
        encyclopedia="A bank is a financial institution licensed to accept deposits.",
    )
    landform = _entry("landform", [_sense(0, "Any natural feature of the earth's surface.")])
    shore = _entry(
        "shore",
        [
            _sense(
                0,
                "Land along the edge of a sea or lake.",
                relations=[_resolved(RelationType.HYPERNYM, "landform", "landform:noun:0")],
            )
        ],
    )
    # A second co-hyponym of bank:noun:1/shore:noun:0, so grade-1 sampling can be tested.
    coast = _entry(
        "coast",
        [
            _sense(
                0,
                "Land next to the sea.",
                relations=[_resolved(RelationType.HYPERNYM, "landform", "landform:noun:0")],
            )
        ],
    )
    lender = _entry("lender", [_sense(0, "A person or organization that lends money.")])
    # Four synonyms of lender (only one, `bank`, points back), so bank:noun:0's synonym
    # tier exceeds MAX_GRADE_2 and the deterministic sampler in `_sample` is exercised.
    financier = _entry(
        "financier",
        [
            _sense(
                0,
                "A person who manages large amounts of money.",
                relations=[_resolved(RelationType.SYNONYM, "lender", "lender:noun:0")],
            )
        ],
    )
    creditor = _entry(
        "creditor",
        [
            _sense(
                0,
                "One to whom a debt is owed.",
                relations=[_resolved(RelationType.SYNONYM, "lender", "lender:noun:0")],
            )
        ],
    )
    moneylender = _entry(
        "moneylender",
        [
            _sense(
                0,
                "A person whose business is lending money.",
                relations=[_resolved(RelationType.SYNONYM, "lender", "lender:noun:0")],
            )
        ],
    )
    embankment = _entry("embankment", [_sense(0, "A wall or bank built to prevent flooding.")])
    pebble = _entry("pebble", [_sense(0, "A small stone.")])
    # A monosemous entry with an encyclopedia, so grade 3 for a lexeme's own encyclopedia
    # doc (D-71) can be exercised alongside bank's grade-1 case above.
    gadget = _entry(
        "gadget",
        [_sense(0, "A small mechanical device or tool.")],
        encyclopedia="A gadget is a small mechanical or electronic device.",
    )

    for entry in (
        bank,
        landform,
        shore,
        coast,
        lender,
        financier,
        creditor,
        moneylender,
        embankment,
        pebble,
        gadget,
    ):
        store.write(entry)
    return store


# --------------------------------------------------------------------------------------
# Grades
# --------------------------------------------------------------------------------------


def _listwise_for(result: QrelsResult, sense_id: str) -> ListwiseQuery:
    """Return the (single, gloss-pseudo-query) listwise entry anchored on ``sense_id``."""
    matches = [q for q in result.listwise if q.query_id.startswith(sense_id)]
    assert len(matches) == 1
    return matches[0]


def test_own_sense_is_graded_3(world: LexemeStore) -> None:
    result = build_qrels(world, seed=0)
    listwise = _listwise_for(result, "bank:noun:0")
    own = next(c for c in listwise.candidates if c.id == "bank:noun:0")
    assert own.grade == GRADE_OWN_SENSE
    assert own.text == "A financial institution that holds money for customers."


def test_direct_synonym_is_graded_2(world: LexemeStore) -> None:
    result = build_qrels(world, seed=0)
    listwise = _listwise_for(result, "bank:noun:0")
    grades = {c.id: c.grade for c in listwise.candidates}
    assert grades["lender:noun:0"] == GRADE_SYNONYM


def test_hypernym_and_co_hyponym_are_graded_1(world: LexemeStore) -> None:
    result = build_qrels(world, seed=0)
    listwise = _listwise_for(result, "bank:noun:1")
    grades = {c.id: c.grade for c in listwise.candidates}
    assert grades["landform:noun:0"] == GRADE_HYPERNYM_OR_COHYPONYM
    # shore and coast are both co-hyponyms of bank:noun:1; MAX_GRADE_1 caps how many
    # of them are sampled in, but every one present must carry grade 1.
    for candidate_id in ("shore:noun:0", "coast:noun:0"):
        if candidate_id in grades:
            assert grades[candidate_id] == GRADE_HYPERNYM_OR_COHYPONYM


def test_hard_negative_kinds_are_all_graded_0(world: LexemeStore) -> None:
    result = build_qrels(world, seed=0)
    listwise = _listwise_for(result, "bank:noun:0")
    grades = {c.id: c.grade for c in listwise.candidates}
    # other_sense (bank:noun:1) and confusable (embankment:noun:0) both land here.
    assert grades["bank:noun:1"] == GRADE_UNRELATED
    assert grades["embankment:noun:0"] == GRADE_UNRELATED


def test_grade_2_tier_is_capped_at_max_grade_2(world: LexemeStore) -> None:
    # lender is synonymous with bank, financier, creditor, and moneylender: 4 candidates
    # for its own grade-2 tier, more than MAX_GRADE_2.
    result = build_qrels(world, seed=0)
    listwise = _listwise_for(result, "lender:noun:0")
    grade_2_candidates = [c for c in listwise.candidates if c.grade == GRADE_SYNONYM]
    assert len(grade_2_candidates) == MAX_GRADE_2


def test_grade_1_tier_is_capped_at_max_grade_1(world: LexemeStore) -> None:
    result = build_qrels(world, seed=0)
    listwise = _listwise_for(result, "bank:noun:1")
    # The MAX_GRADE_1 cap applies to the hypernym/co-hyponym tier; bank's own (uncapped,
    # at-most-one-per-lexeme) encyclopedia doc also grades 1 here since bank is
    # polysemous (D-71) and is excluded from this count on purpose.
    grade_1_candidates = [
        c
        for c in listwise.candidates
        if c.grade == GRADE_HYPERNYM_OR_COHYPONYM and not c.id.endswith(":encyclopedia")
    ]
    assert len(grade_1_candidates) <= MAX_GRADE_1


def test_a_candidate_never_appears_at_two_grades_for_one_query(world: LexemeStore) -> None:
    result = build_qrels(world, seed=0)
    for listwise in result.listwise:
        ids = [c.id for c in listwise.candidates]
        assert len(ids) == len(set(ids))


def test_isolated_sense_grade_0_is_filled_entirely_by_easy_negatives(world: LexemeStore) -> None:
    result = build_qrels(world, seed=0)
    listwise = _listwise_for(result, "pebble:noun:0")
    grade_0 = [c for c in listwise.candidates if c.grade == GRADE_UNRELATED]
    assert grade_0  # the pool is large enough that at least one easy negative is found
    for candidate in grade_0:
        assert not candidate.id.startswith("pebble:")


# --------------------------------------------------------------------------------------
# The encyclopedia doc is entry-level, not sense-level (D-71)
# --------------------------------------------------------------------------------------


def test_polysemous_entrys_encyclopedia_doc_is_graded_1_never_0_or_3(world: LexemeStore) -> None:
    # bank has two live senses; its encyclopedia doc must appear at grade 1 for both of
    # their queries, and never at grade 3 (reserved for the query's own sense) or 0.
    result = build_qrels(world, seed=0)
    for sense_id in ("bank:noun:0", "bank:noun:1"):
        listwise = _listwise_for(result, sense_id)
        grades = {c.id: c.grade for c in listwise.candidates}
        assert grades["bank:encyclopedia"] == GRADE_ENCYCLOPEDIA_RELATED


def test_monosemous_entrys_encyclopedia_doc_is_graded_3(world: LexemeStore) -> None:
    # gadget has exactly one live sense, so its encyclopedia doc is as relevant as the
    # query's own sense.
    result = build_qrels(world, seed=0)
    listwise = _listwise_for(result, "gadget:noun:0")
    grades = {c.id: c.grade for c in listwise.candidates}
    assert grades["gadget:encyclopedia"] == GRADE_OWN_SENSE


def test_encyclopedia_doc_text_and_id_are_correct(world: LexemeStore) -> None:
    result = build_qrels(world, seed=0)
    assert (
        result.docs["gadget:encyclopedia"] == "A gadget is a small mechanical or electronic device."
    )
    assert (
        result.docs["bank:encyclopedia"]
        == "A bank is a financial institution licensed to accept deposits."
    )


def test_entries_without_an_encyclopedia_never_get_an_encyclopedia_doc(
    world: LexemeStore,
) -> None:
    result = build_qrels(world, seed=0)
    listwise = _listwise_for(result, "pebble:noun:0")
    assert all(not c.id.endswith(":encyclopedia") for c in listwise.candidates)
    assert not any(doc_id.endswith(":encyclopedia") for doc_id in result.docs if "pebble" in doc_id)


# --------------------------------------------------------------------------------------
# qrels.trec / docs.jsonl / listwise.jsonl consistency
# --------------------------------------------------------------------------------------


def test_qrels_and_listwise_agree_on_every_grade(world: LexemeStore) -> None:
    result = build_qrels(world, seed=0)
    from_qrels = {(entry.query_id, entry.doc_id): entry.grade for entry in result.qrels}
    for listwise in result.listwise:
        for candidate in listwise.candidates:
            assert from_qrels[(listwise.query_id, candidate.id)] == candidate.grade


def test_every_doc_referenced_by_qrels_is_in_docs(world: LexemeStore) -> None:
    result = build_qrels(world, seed=0)
    for entry in result.qrels:
        assert entry.doc_id in result.docs


def test_grade_histogram_matches_the_qrels_count(world: LexemeStore) -> None:
    result = build_qrels(world, seed=0)
    assert sum(result.grade_histogram.values()) == len(result.qrels)
    summary = result.as_summary()
    assert summary["qrels_written"] == len(result.qrels)


def test_build_qrels_is_deterministic_for_a_fixed_seed(world: LexemeStore) -> None:
    first = build_qrels(world, seed=5)
    second = build_qrels(world, seed=5)
    assert first.qrels == second.qrels
    assert first.docs == second.docs


def test_build_qrels_respects_limit(world: LexemeStore) -> None:
    limited = build_qrels(world, seed=0, limit=1)
    full = build_qrels(world, seed=0)
    assert limited.entries_scanned == 1
    assert limited.senses_considered < full.senses_considered


def test_write_qrels_produces_three_consistent_files(world: LexemeStore, tmp_path: Path) -> None:
    result = build_qrels(world, seed=0)
    out_dir = tmp_path / "out"
    write_qrels(result, out_dir)

    qrels_lines = (out_dir / "qrels.trec").read_text(encoding="utf-8").splitlines()
    assert len(qrels_lines) == len(result.qrels)
    first_fields = qrels_lines[0].split(" ")
    assert len(first_fields) == 4
    assert first_fields[1] == "0"

    docs_lines = (out_dir / "docs.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(docs_lines) == len(result.docs)
    doc_row = orjson.loads(docs_lines[0])
    assert set(doc_row) == {"id", "text"}

    listwise_lines = (out_dir / "listwise.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(listwise_lines) == len(result.listwise)
    listwise_row = orjson.loads(listwise_lines[0])
    assert set(listwise_row) == {"query", "query_id", "query_source", "candidates"}
    assert set(listwise_row["candidates"][0]) == {"id", "text", "grade"}
