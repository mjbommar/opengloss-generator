"""F3 — ``export.triples``: MS MARCO-style triples with graph hard negatives (D-56).

Everything here is offline: no model is called anywhere in ``export/triples.py``, so
these tests build a small store by hand and assert on the pure functions directly,
the same discipline ``tests/test_graph_hygiene.py`` uses for the other $0 workflow.
"""

from __future__ import annotations

from pathlib import Path

import orjson
import pytest

from opengloss_generator.config import StoreConfig
from opengloss_generator.export.triples import (
    EASY_NEGATIVE_KIND,
    HARD_NEGATIVE_PRIORITY,
    SOURCE_GENERATED,
    SOURCE_GLOSS_PSEUDO,
    SenseGraphInfo,
    _queries_for,
    build_triples,
    classify,
    load_corpus,
    positive_options,
    select_hard_negative,
    write_triples,
)
from opengloss_generator.schema import (
    Example,
    Lexeme,
    LexemeKind,
    PartOfSpeech,
    POSEntry,
    ReadingLevel,
    Register,
    Relation,
    RelationTarget,
    RelationType,
    Rendition,
    Renditions,
    Sense,
    canonical_rendition,
)
from opengloss_generator.store import LexemeStore

# --------------------------------------------------------------------------------------
# A small hand-built world exercising every hard-negative tier
# --------------------------------------------------------------------------------------
#
#   bank:noun:0 "financial institution"  -- synonym --------> lender:noun:0
#                                         -- confusable_with -> embankment:noun:0
#   bank:noun:1 "riverbank"              -- hypernym --------> landform:noun:0
#   shore:noun:0 "land by water"         -- hypernym --------> landform:noun:0  (co-hyp of bank:1)
#   financier:noun:0                     -- synonym --------> lender:noun:0  (syn-of-syn of bank:0)
#   creditor:noun:0                      -- synonym --------> lender:noun:0
#   moneylender:noun:0                   -- synonym --------> lender:noun:0
#   lender:noun:0, landform:noun:0, embankment:noun:0: no outgoing relations of their own
#   bank:noun:2: retired, and the (bogus) target of an edge from landform, to prove a
#   retired target is dropped rather than treated as live.
#   pebble:noun:0: fully isolated -- no relations, no example, no encyclopedia.


def _sense(
    index: int,
    gloss: str,
    *,
    relations: list[Relation] | None = None,
    example: str | None = None,
    grade_5: str | None = None,
    retired: bool = False,
) -> Sense:
    """Build a sense with a canonical gloss and, optionally, an example and a grade_5 gloss."""
    gloss_renditions = [canonical_rendition(gloss)]
    if grade_5 is not None:
        gloss_renditions.append(
            Rendition[str](
                reading_level=ReadingLevel.GRADE_5, style=Register.PLAIN, content=grade_5
            )
        )
    examples = Renditions[Example](root=[])
    if example is not None:
        examples = Renditions[Example](root=[canonical_rendition(Example(text=example))])
    return Sense(
        index=index,
        gloss=Renditions[str](root=gloss_renditions),
        examples=examples,
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
                example="She deposited her paycheck at the bank.",
            ),
            _sense(
                1,
                "The land alongside a river.",
                relations=[_resolved(RelationType.HYPERNYM, "landform", "landform:noun:0")],
            ),
            _sense(2, "A retired sense that must never surface.", retired=True),
        ],
        encyclopedia="A bank is a financial institution licensed to accept deposits.",
    )
    landform = _entry(
        "landform",
        [
            _sense(
                0,
                "Any natural feature of the earth's surface.",
                relations=[
                    # A bogus edge toward a retired sense: must be dropped, not treated as live.
                    _resolved(RelationType.SYNONYM, "bank", "bank:noun:2"),
                ],
            )
        ],
    )
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
    lender = _entry("lender", [_sense(0, "A person or organization that lends money.")])
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

    for entry in (
        bank,
        landform,
        shore,
        lender,
        financier,
        creditor,
        moneylender,
        embankment,
        pebble,
    ):
        store.write(entry)
    return store


# --------------------------------------------------------------------------------------
# Pseudo-queries and F2 defensiveness
# --------------------------------------------------------------------------------------


def test_pseudo_query_falls_back_to_canonical_gloss_when_grade_5_is_absent(
    world: LexemeStore,
) -> None:
    corpus = load_corpus(world)
    queries = corpus.queries["lender:noun:0"]
    assert len(queries) == 1
    assert queries[0].source == SOURCE_GLOSS_PSEUDO
    assert queries[0].text == "A person or organization that lends money."
    assert queries[0].query_id == "lender:noun:0#neutral/plain"


def test_pseudo_query_prefers_grade_5_gloss_when_present() -> None:
    sense = _sense(
        0, "A financial institution.", grade_5="A bank is a place that keeps money safe."
    )
    queries = _queries_for(sense, "bank:noun:0")
    assert len(queries) == 1
    assert queries[0].source == SOURCE_GLOSS_PSEUDO
    assert queries[0].text == "A bank is a place that keeps money safe."
    assert queries[0].query_id == "bank:noun:0#grade_5/plain"


class _FakeQuery:
    """A minimal stand-in for the not-yet-landed F2 ``Query`` model."""

    def __init__(self, text: str) -> None:
        self.text = text


class _FakeSenseWithQueries:
    """A duck-typed sense carrying ``queries``, since ``Sense`` cannot (F2 pending)."""

    def __init__(self, queries: list[_FakeQuery]) -> None:
        self.queries = queries


def test_generated_queries_are_used_when_present_and_positionally_ided() -> None:
    fake_sense = _FakeSenseWithQueries(
        [_FakeQuery("what holds my paycheck?"), _FakeQuery("where do I deposit cash?")]
    )
    records = _queries_for(fake_sense, "bank:noun:0")  # ty: ignore[invalid-argument-type]
    assert [r.query_id for r in records] == ["bank:noun:0#q1", "bank:noun:0#q2"]
    assert all(r.source == SOURCE_GENERATED for r in records)
    assert records[0].text == "what holds my paycheck?"


def test_getattr_defensiveness_means_a_plain_sense_never_raises(world: LexemeStore) -> None:
    # Every real Sense on `main` lacks `.queries` entirely; the defensive getattr must
    # never raise AttributeError, only fall back to the pseudo-query.
    corpus = load_corpus(world)
    assert corpus.queries["bank:noun:0"][0].source == SOURCE_GLOSS_PSEUDO


# --------------------------------------------------------------------------------------
# The corpus projection: liveness, retirement, and dangling edges
# --------------------------------------------------------------------------------------


def test_retired_sense_is_excluded_entirely(world: LexemeStore) -> None:
    corpus = load_corpus(world)
    assert "bank:noun:2" not in corpus.gloss
    assert "bank:noun:2" not in corpus.all_live_sense_ids
    assert "bank:noun:2" not in corpus.senses_by_lexeme["bank"]


def test_edge_toward_a_retired_target_is_dropped(world: LexemeStore) -> None:
    corpus = load_corpus(world)
    # landform asserted a synonym toward the retired bank:noun:2; it must not appear.
    assert "bank:noun:2" not in corpus.synonyms.get("landform:noun:0", set())


def test_hypernym_and_co_hyponym_maps_are_built_both_ways(world: LexemeStore) -> None:
    corpus = load_corpus(world)
    assert corpus.hypernyms["bank:noun:1"] == {"landform:noun:0"}
    assert corpus.hypernyms["shore:noun:0"] == {"landform:noun:0"}
    assert corpus.children["landform:noun:0"] == {"bank:noun:1", "shore:noun:0"}
    assert corpus.co_hyponyms_of("bank:noun:1") == {"shore:noun:0"}
    assert corpus.co_hyponyms_of("shore:noun:0") == {"bank:noun:1"}


def test_synonym_edges_are_symmetric_even_when_stored_one_directional(world: LexemeStore) -> None:
    corpus = load_corpus(world)
    # Only bank -> lender is stored, never lender -> bank, yet both directions resolve.
    assert "lender:noun:0" in corpus.synonyms["bank:noun:0"]
    assert "bank:noun:0" in corpus.synonyms["lender:noun:0"]


# --------------------------------------------------------------------------------------
# classify(): disjoint priority tiers
# --------------------------------------------------------------------------------------


def test_classify_bank_sense_0_covers_every_tier(world: LexemeStore) -> None:
    corpus = load_corpus(world)
    info = classify(corpus, "bank:noun:0")
    assert info.synonym == {"lender:noun:0"}
    assert info.other_sense == {"bank:noun:1"}
    assert info.confusable == {"embankment:noun:0"}
    # financier/creditor/moneylender -> lender -> bank are all distance-2 synonym paths.
    assert info.synonym_of_synonym == {"financier:noun:0", "creditor:noun:0", "moneylender:noun:0"}
    assert info.hypernym == set()
    assert info.co_hyponym == set()


def test_classify_never_puts_a_sense_in_two_tiers(world: LexemeStore) -> None:
    corpus = load_corpus(world)
    for sense_id in corpus.all_live_sense_ids:
        info = classify(corpus, sense_id)
        tiers = [
            info.synonym,
            info.hypernym,
            info.co_hyponym,
            info.other_sense,
            info.confusable,
            info.synonym_of_synonym,
        ]
        seen: set[str] = set()
        for tier in tiers:
            assert not (tier & seen), f"{sense_id}: a candidate appears in two tiers"
            seen |= tier
            assert sense_id not in tier, f"{sense_id} classified itself as its own candidate"


# --------------------------------------------------------------------------------------
# The hard-negative fallback chain
# --------------------------------------------------------------------------------------


def _require_hard_negative(info: SenseGraphInfo, *, seed: int, sense_id: str) -> tuple[str, str]:
    """Return :func:`select_hard_negative`'s result, asserting it is not ``None``."""
    chosen = select_hard_negative(info, seed, sense_id)
    assert chosen is not None
    return chosen


def test_hard_negative_prefers_other_sense_over_co_hyponym(world: LexemeStore) -> None:
    # bank:noun:1 has both an other_sense candidate (bank:noun:0) and a co_hyponym
    # (shore:noun:0); other_sense is first in HARD_NEGATIVE_PRIORITY and must win.
    corpus = load_corpus(world)
    info = classify(corpus, "bank:noun:1")
    kind, target = _require_hard_negative(info, seed=0, sense_id="bank:noun:1")
    assert kind == "other_sense"
    assert target == "bank:noun:0"


def test_hard_negative_falls_back_to_co_hyponym_when_higher_tiers_are_empty(
    world: LexemeStore,
) -> None:
    # shore has no sibling sense and no confusable target, so co_hyponym is reached.
    corpus = load_corpus(world)
    info = classify(corpus, "shore:noun:0")
    assert info.other_sense == set()
    assert info.confusable == set()
    kind, target = _require_hard_negative(info, seed=0, sense_id="shore:noun:0")
    assert kind == "co_hyponym"
    assert target == "bank:noun:1"


def test_hard_negative_falls_back_to_synonym_of_synonym_as_last_resort(world: LexemeStore) -> None:
    # financier has no other sense, no confusable, and no hypernym/co-hyponym, but it
    # does have a synonym-of-synonym path (financier -> lender -> bank).
    corpus = load_corpus(world)
    info = classify(corpus, "financier:noun:0")
    assert info.other_sense == set()
    assert info.confusable == set()
    assert info.co_hyponym == set()
    kind, target = _require_hard_negative(info, seed=0, sense_id="financier:noun:0")
    assert kind == "synonym_of_synonym"
    assert target in {"bank:noun:0", "creditor:noun:0", "moneylender:noun:0"}


def test_hard_negative_is_none_when_every_tier_is_empty(world: LexemeStore) -> None:
    corpus = load_corpus(world)
    info = classify(corpus, "pebble:noun:0")
    assert select_hard_negative(info, seed=0, sense_id="pebble:noun:0") is None


def test_direct_synonym_is_never_a_candidate_hard_negative(world: LexemeStore) -> None:
    corpus = load_corpus(world)
    for kind in HARD_NEGATIVE_PRIORITY:
        assert "lender:noun:0" not in getattr(classify(corpus, "bank:noun:0"), kind)


# --------------------------------------------------------------------------------------
# positive_options(): gloss always, example/encyclopedia when present
# --------------------------------------------------------------------------------------


def test_positive_options_include_example_and_encyclopedia_when_present(world: LexemeStore) -> None:
    corpus = load_corpus(world)
    options = positive_options(corpus, "bank:noun:0")
    sources = {option.source for option in options}
    assert sources == {"gloss", "example", "encyclopedia"}
    encyclopedia_option = next(o for o in options if o.source == "encyclopedia")
    assert encyclopedia_option.doc_id == "bank:encyclopedia"


def test_positive_options_are_gloss_only_for_an_isolated_sense(world: LexemeStore) -> None:
    corpus = load_corpus(world)
    options = positive_options(corpus, "pebble:noun:0")
    assert [option.source for option in options] == ["gloss"]


# --------------------------------------------------------------------------------------
# build_triples(): end to end
# --------------------------------------------------------------------------------------


def test_build_triples_is_deterministic_for_a_fixed_seed(world: LexemeStore) -> None:
    first = build_triples(world, seed=7, easy_negatives=1)
    second = build_triples(world, seed=7, easy_negatives=1)
    assert first.triples == second.triples


def test_build_triples_never_uses_the_positive_sense_as_its_own_negative(
    world: LexemeStore,
) -> None:
    result = build_triples(world, seed=3, easy_negatives=2)
    assert result.triples  # sanity: the world produces at least one triple
    for triple in result.triples:
        assert triple.negative_id != triple.positive_id
        assert not triple.positive_id.startswith(f"{triple.negative_id}#")


def test_build_triples_easy_negative_is_never_from_the_query_lexeme(world: LexemeStore) -> None:
    result = build_triples(world, seed=1, easy_negatives=2)
    easy_rows = [t for t in result.triples if t.negative_kind == EASY_NEGATIVE_KIND]
    assert easy_rows
    for triple in easy_rows:
        query_lexeme = triple.query_id.split(":", maxsplit=1)[0]
        negative_lexeme = triple.negative_id.split(":", maxsplit=1)[0]
        assert negative_lexeme != query_lexeme


def test_build_triples_pebble_has_only_easy_negatives(world: LexemeStore) -> None:
    result = build_triples(world, seed=0, easy_negatives=1)
    pebble_rows = [t for t in result.triples if t.query_id.startswith("pebble:")]
    assert len(pebble_rows) == 1
    assert pebble_rows[0].negative_kind == EASY_NEGATIVE_KIND


def test_build_triples_reports_a_by_negative_kind_histogram(world: LexemeStore) -> None:
    result = build_triples(world, seed=0, easy_negatives=1)
    summary = result.as_summary()
    assert summary["triples_written"] == len(result.triples)
    assert set(result.by_negative_kind) <= {*HARD_NEGATIVE_PRIORITY, EASY_NEGATIVE_KIND}
    assert sum(result.by_negative_kind.values()) == len(result.triples)


def test_build_triples_respects_limit(world: LexemeStore) -> None:
    limited = build_triples(world, seed=0, limit=1)
    full = build_triples(world, seed=0)
    assert limited.entries_scanned == 1
    assert limited.senses_considered < full.senses_considered


def test_write_triples_round_trips_as_jsonl(world: LexemeStore, tmp_path: Path) -> None:
    result = build_triples(world, seed=0, easy_negatives=1)
    out_path = tmp_path / "out" / "triples.jsonl"
    write_triples(result, out_path)

    lines = out_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == len(result.triples)
    first = orjson.loads(lines[0])
    assert set(first) == {
        "query",
        "positive",
        "negative",
        "negative_kind",
        "query_id",
        "positive_id",
        "negative_id",
        "query_source",
    }
