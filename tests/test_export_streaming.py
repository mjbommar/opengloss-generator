"""The four derived exporters stream their rows without changing one of them (D-77).

``export-hf`` used to build each derived training set whole before writing it, and on a
release-sized store that cost more memory than the machine had. The builders are now
generators, and the only thing these tests care about is that turning them inside out
changed *nothing a consumer sees*: same rows, same order, same JSONL bytes.

Each reference implementation below is the pre-D-77 aggregation verbatim -- the whole
list of entries loaded first, the whole result assembled, then written -- kept here
rather than in a fixture file so the comparison is against code, not against a snapshot
that would silently stop meaning anything the first time a mining rule changed. The
mining helpers themselves (``classify``, ``positive_options``, ``_graded_candidates`` and
friends) were not touched by D-77, so the references call the same ones the exporters do:
what is being compared is the shape of the pass, which is exactly what changed.

The rich store fixtures are imported from the per-exporter test modules, and every
comparison additionally runs against ``data/sample-300`` when that store is present --
300 real entries exercise far more relation shapes than a hand-built world can.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from opengloss_generator.config import StoreConfig
from opengloss_generator.export.pairs import (
    Pair,
    PairKind,
    _live_senses,
    _pairs_for_entry,
    _representative_example,
    export_pairs,
    iter_pairs,
)
from opengloss_generator.export.pretrain import (
    TEMPLATES,
    documents_for_entry,
    export_pretrain,
    iter_pretrain,
)
from opengloss_generator.export.qrels import (
    GradedCandidate,
    ListwiseCandidate,
    ListwiseQuery,
    QrelEntry,
    _graded_candidates,
    build_qrels,
    iter_listwise,
    stream_qrels,
    write_qrels,
)
from opengloss_generator.export.triples import (
    Triple,
    _rng,
    _triples_for_query,
    build_triples,
    classify,
    live_sense_count,
    load_corpus,
    positive_options,
    stream_triples,
    write_triples,
)
from opengloss_generator.schema import (
    Example,
    ReadingLevel,
    Renditions,
    Sense,
    canonical_rendition,
)
from opengloss_generator.store import LexemeStore
from opengloss_generator.taxonomy import DomainTag
from tests import test_export_qrels as _qrels_tests
from tests import test_export_triples as _triples_tests
from tests.test_export_pairs import _domain_entry, _entry, _example

#: The two hand-built worlds the per-exporter suites already maintain, re-registered as
#: fixtures of this module. Rebuilding them here would be a second copy of a fixture
#: whose whole point is to be the one description of every graph shape that matters.
qrels_world = _qrels_tests.world
triples_world = _triples_tests.world

if TYPE_CHECKING:
    from collections.abc import Sequence

    from opengloss_generator.export.pretrain import PretrainRecord
    from opengloss_generator.export.triples import Corpus

#: The real 300-entry store, when this checkout has one. Every equality test runs against
#: it as well as against the hand-built worlds: 300 published entries carry relation and
#: rendition shapes no fixture is going to think of.
_SAMPLE_300 = Path("data/sample-300")

_HAS_SAMPLE_300 = _SAMPLE_300.is_dir() and any(_SAMPLE_300.rglob("*.json"))

_needs_sample_300 = pytest.mark.skipif(
    not _HAS_SAMPLE_300, reason="data/sample-300 is not present in this checkout"
)


def _sample_300() -> LexemeStore:
    """Return the read-only 300-entry sample store."""
    return LexemeStore(StoreConfig(root=_SAMPLE_300, fsync_on_write=False))


def _empty_store(tmp_path: Path) -> LexemeStore:
    """Return a store with no entries at all."""
    return LexemeStore(StoreConfig(root=tmp_path / "empty", fsync_on_write=False))


# --------------------------------------------------------------------------------------
# Reference implementations: the pre-D-77 builders, verbatim
# --------------------------------------------------------------------------------------


def _reference_pairs(
    store: LexemeStore,
    *,
    lexeme_ids: Sequence[str] | None = None,
    easy_negatives: int = 0,
    seed: int = 0,
) -> list[Pair]:
    """Mine every pair the way ``export_pairs`` did before D-77: all entries first."""
    if lexeme_ids is not None:
        entries = [entry for lid in lexeme_ids if (entry := store.read(lid)) is not None]
    else:
        entries = list(store.iter_entries())
    entries = sorted(entries, key=lambda entry: entry.lexeme_id)

    pairs: list[Pair] = []
    for entry in entries:
        pairs.extend(_pairs_for_entry(entry))

    if easy_negatives <= 0:
        return pairs

    by_domain: dict[str, list[tuple[str, str, object, int]]] = {}
    for entry in entries:
        entry_live = _live_senses(entry)
        entry_live_senses = len(entry_live)
        for sense, sid in entry_live:
            if sense.domain is None:
                continue
            rep = _representative_example(sense.examples)
            if rep is None:
                continue
            by_domain.setdefault(sense.domain.value, []).append(
                (entry.headword, sid, rep, entry_live_senses)
            )

    import random  # noqa: PLC0415 - the reference implementation's own import

    for domain, pool in sorted(by_domain.items()):
        ordered_pool = sorted(pool, key=lambda item: item[1])
        for headword, sid, rep, live_senses in ordered_pool:
            candidates = [c for c in ordered_pool if c[0] != headword]
            if not candidates:
                continue
            rng = random.Random(f"{seed}:{domain}:{sid}")  # noqa: S311 - sampling, not crypto
            chosen = rng.sample(candidates, min(easy_negatives, len(candidates)))
            for other_headword, other_sid, other_rep, _other_live in chosen:
                pairs.append(
                    Pair(
                        headword=headword,
                        headword_b=other_headword,
                        sense_a=sid,
                        sense_b=other_sid,
                        text_a=rep.content.text,
                        text_b=other_rep.content.text,
                        span_a=rep.content.span,
                        span_b=other_rep.content.span,
                        label=0,
                        level_a=rep.reading_level.value,
                        level_b=other_rep.reading_level.value,
                        kind=PairKind.WIC_EASY_NEGATIVE.value,
                        live_senses=live_senses,
                    )
                )
    return pairs


def _reference_triples(corpus: Corpus, *, seed: int = 0, easy_negatives: int = 1) -> list[Triple]:
    """Build every triple the way ``build_triples`` did before D-77: into one list."""
    triples: list[Triple] = []
    for sense_id in sorted(corpus.gloss):
        info = classify(corpus, sense_id)
        options = positive_options(corpus, sense_id)
        positive = _rng(seed, sense_id, "positive").choice(options)
        lexeme_id = corpus.lexeme_of[sense_id]
        senses_in_lexeme = live_sense_count(corpus, lexeme_id)
        for query in corpus.queries.get(sense_id, ()):
            triples.extend(
                _triples_for_query(
                    query,
                    positive,
                    info,
                    corpus,
                    seed=seed,
                    sense_id=sense_id,
                    easy_negatives=easy_negatives,
                    easy_lexeme_id=lexeme_id,
                    live_senses=senses_in_lexeme,
                )
            )
    return triples


def _reference_qrels(
    corpus: Corpus, *, seed: int = 0
) -> tuple[list[QrelEntry], dict[str, str], list[ListwiseQuery]]:
    """Grade every query the way ``build_qrels`` did before D-77: three whole structures."""
    qrels: list[QrelEntry] = []
    docs: dict[str, str] = {}
    listwise: list[ListwiseQuery] = []
    for sense_id in sorted(corpus.gloss):
        info = classify(corpus, sense_id)
        graded: list[GradedCandidate] = _graded_candidates(
            info, corpus, sense_id, seed, corpus.lexeme_of[sense_id]
        )
        for candidate in graded:
            docs.setdefault(candidate.doc_id, candidate.text)
        for query in corpus.queries.get(sense_id, ()):
            candidates = [
                ListwiseCandidate(id=c.doc_id, text=c.text, grade=c.grade) for c in graded
            ]
            for candidate in candidates:
                qrels.append(
                    QrelEntry(query_id=query.query_id, doc_id=candidate.id, grade=candidate.grade)
                )
            listwise.append(
                ListwiseQuery(
                    query=query.text,
                    query_id=query.query_id,
                    query_source=query.source,
                    candidates=candidates,
                )
            )
    return qrels, docs, listwise


def _reference_pretrain(
    store: LexemeStore,
    *,
    levels: Sequence[ReadingLevel],
    seed: int = 0,
    lexeme_ids: Sequence[str] | None = None,
) -> list[PretrainRecord]:
    """Render every document the way ``export_pretrain`` did before D-77."""
    if lexeme_ids is not None:
        entries = [
            entry
            for lexeme_id in sorted(set(lexeme_ids))
            if (entry := store.read(lexeme_id)) is not None
        ]
    else:
        entries = sorted(store.iter_entries(), key=lambda e: e.lexeme_id)
    return [
        doc
        for entry in entries
        for doc in documents_for_entry(entry, templates=TEMPLATES, levels=levels, seed=seed)
    ]


# --------------------------------------------------------------------------------------
# pairs
# --------------------------------------------------------------------------------------


@pytest.fixture
def pairs_world(tmp_path: Path) -> LexemeStore:
    """A store carrying every shape the pairs and pretraining passes branch on.

    Two shared domain leaves with several headwords each (so the easy-negative pools are
    big enough that the sample actually samples), a polysemous entry and a monosemous one
    with an encyclopedia (the two sides of D-71), a sense with several example renditions
    (the ``wic_positive`` combinations), a sense with no domain, and a retired sense.
    """
    store = LexemeStore(StoreConfig(root=tmp_path / "pairs", fsync_on_write=False))
    for index in range(6):
        store.write(
            _domain_entry(f"reed{index}", DomainTag.ARTS_MUSIC, f"Reed {index} sounds a note.")
        )
    for index in range(4):
        store.write(
            _domain_entry(f"ledger{index}", DomainTag.BUSINESS_FINANCE, f"Ledger {index} balances.")
        )

    polysemous = [
        Sense(
            index=0,
            gloss=Renditions[str](root=[canonical_rendition("A financial institution.")]),
            examples=Renditions[Example](
                root=[
                    _example("The bank approved the loan."),
                    _example("The bank shut early.", level=ReadingLevel.GRADE_5),
                ]
            ),
            domain=DomainTag.BUSINESS_FINANCE,
        ),
        Sense(
            index=1,
            gloss=Renditions[str](root=[canonical_rendition("The land beside a river.")]),
            examples=Renditions[Example](root=[_example("We sat on the bank.")]),
        ),
        Sense(
            index=2,
            gloss=Renditions[str](root=[canonical_rendition("A retired sense.")]),
            examples=Renditions[Example](root=[_example("Never exported.")]),
            retired=True,
        ),
    ]
    store.write(_entry("bank", polysemous, encyclopedia="Banks, at length."))

    monosemous = Sense(
        index=0,
        gloss=Renditions[str](root=[canonical_rendition("A small hand tool.")]),
        examples=Renditions[Example](root=[_example("She reached for the gadget.")]),
        domain=DomainTag.ARTS_MUSIC,
    )
    store.write(_entry("gadget", [monosemous], encyclopedia="Gadgets, at length."))
    return store


@pytest.mark.parametrize("easy_negatives", [0, 3])
def test_iter_pairs_matches_the_reference_builder(
    pairs_world: LexemeStore, easy_negatives: int
) -> None:
    assert list(iter_pairs(pairs_world, easy_negatives=easy_negatives, seed=5)) == _reference_pairs(
        pairs_world, easy_negatives=easy_negatives, seed=5
    )


@_needs_sample_300
@pytest.mark.parametrize("easy_negatives", [0, 3])
def test_iter_pairs_matches_the_reference_builder_on_sample_300(easy_negatives: int) -> None:
    store = _sample_300()
    assert list(iter_pairs(store, easy_negatives=easy_negatives, seed=3)) == _reference_pairs(
        store, easy_negatives=easy_negatives, seed=3
    )


def test_iter_pairs_matches_the_reference_builder_for_a_word_list(
    pairs_world: LexemeStore,
) -> None:
    # The list is deliberately unsorted, carries a duplicate, and names an absent entry:
    # the pass is defined to visit ids in sorted order, to honour the duplicate, and to
    # skip what is not there, exactly as loading every entry first did.
    words = ["reed3", "bank", "nosuchword", "reed0", "bank"]
    assert list(
        iter_pairs(pairs_world, lexeme_ids=words, easy_negatives=2, seed=5)
    ) == _reference_pairs(pairs_world, lexeme_ids=words, easy_negatives=2, seed=5)


def test_iter_pairs_on_an_empty_store_yields_nothing(tmp_path: Path) -> None:
    store = _empty_store(tmp_path)
    assert list(iter_pairs(store, easy_negatives=3, seed=0)) == []


def test_export_pairs_still_writes_the_reference_file(
    pairs_world: LexemeStore, tmp_path: Path
) -> None:
    import orjson  # noqa: PLC0415 - only this test needs the serialiser

    out = tmp_path / "pairs.jsonl"
    export_pairs(pairs_world, out, easy_negatives=3, seed=5)
    reference = _reference_pairs(pairs_world, easy_negatives=3, seed=5)
    expected = b"".join(orjson.dumps(pair) + b"\n" for pair in reference)
    assert out.read_bytes() == expected


# --------------------------------------------------------------------------------------
# triples
# --------------------------------------------------------------------------------------


def test_iter_triples_matches_the_reference_builder(triples_world: LexemeStore) -> None:
    corpus = load_corpus(triples_world)
    assert build_triples(triples_world, seed=7, easy_negatives=2).triples == _reference_triples(
        corpus, seed=7, easy_negatives=2
    )


@_needs_sample_300
def test_iter_triples_matches_the_reference_builder_on_sample_300() -> None:
    store = _sample_300()
    corpus = load_corpus(store)
    assert build_triples(store, seed=3, easy_negatives=2).triples == _reference_triples(
        corpus, seed=3, easy_negatives=2
    )


def test_iter_triples_on_an_empty_store_yields_nothing(tmp_path: Path) -> None:
    result = build_triples(_empty_store(tmp_path), seed=0, easy_negatives=1)
    assert result.triples == []
    assert result.as_summary()["triples_written"] == 0


def test_stream_triples_writes_the_same_file_as_write_triples(
    triples_world: LexemeStore, tmp_path: Path
) -> None:
    streamed = tmp_path / "streamed.jsonl"
    materialised = tmp_path / "materialised.jsonl"
    summary = stream_triples(triples_world, streamed, seed=7, easy_negatives=2)
    result = build_triples(triples_world, seed=7, easy_negatives=2)
    write_triples(result, materialised)
    assert streamed.read_bytes() == materialised.read_bytes()
    assert summary.as_summary() == result.as_summary()


def test_stream_triples_on_an_empty_store_writes_an_empty_file(tmp_path: Path) -> None:
    out = tmp_path / "triples.jsonl"
    summary = stream_triples(_empty_store(tmp_path), out, seed=0)
    assert out.read_bytes() == b""
    assert summary.as_summary()["triples_written"] == 0


# --------------------------------------------------------------------------------------
# qrels
# --------------------------------------------------------------------------------------


def test_iter_listwise_matches_the_reference_builder(qrels_world: LexemeStore) -> None:
    corpus = load_corpus(qrels_world)
    expected_qrels, expected_docs, expected_listwise = _reference_qrels(corpus, seed=5)
    result = build_qrels(qrels_world, seed=5)
    assert result.listwise == expected_listwise
    assert result.qrels == expected_qrels
    assert result.docs == expected_docs


@_needs_sample_300
def test_iter_listwise_matches_the_reference_builder_on_sample_300() -> None:
    store = _sample_300()
    corpus = load_corpus(store)
    expected_qrels, expected_docs, expected_listwise = _reference_qrels(corpus, seed=3)
    result = build_qrels(store, seed=3)
    assert result.listwise == expected_listwise
    assert result.qrels == expected_qrels
    assert result.docs == expected_docs


def test_iter_listwise_on_an_empty_store_yields_nothing(tmp_path: Path) -> None:
    corpus = load_corpus(_empty_store(tmp_path))
    assert list(iter_listwise(corpus, seed=0)) == []


def test_stream_qrels_writes_the_same_three_files_as_write_qrels(
    qrels_world: LexemeStore, tmp_path: Path
) -> None:
    streamed = tmp_path / "streamed"
    materialised = tmp_path / "materialised"
    summary = stream_qrels(qrels_world, streamed, seed=5)
    result = build_qrels(qrels_world, seed=5)
    write_qrels(result, materialised)
    for name in ("qrels.trec", "docs.jsonl", "listwise.jsonl"):
        assert (streamed / name).read_bytes() == (materialised / name).read_bytes(), name
    assert summary.as_summary() == result.as_summary()


def test_stream_qrels_on_an_empty_store_writes_three_empty_files(tmp_path: Path) -> None:
    out = tmp_path / "qrels"
    summary = stream_qrels(_empty_store(tmp_path), out, seed=0)
    for name in ("qrels.trec", "docs.jsonl", "listwise.jsonl"):
        assert (out / name).read_bytes() == b"", name
    assert summary.as_summary()["docs_written"] == 0


# --------------------------------------------------------------------------------------
# pretrain
# --------------------------------------------------------------------------------------


def test_iter_pretrain_matches_the_reference_builder(pairs_world: LexemeStore) -> None:
    levels = (ReadingLevel.NEUTRAL, ReadingLevel.GRADE_5)
    assert list(iter_pretrain(pairs_world, levels=levels, seed=4)) == _reference_pretrain(
        pairs_world, levels=levels, seed=4
    )


@_needs_sample_300
def test_iter_pretrain_matches_the_reference_builder_on_sample_300() -> None:
    store = _sample_300()
    levels = (ReadingLevel.NEUTRAL, ReadingLevel.GRADE_5, ReadingLevel.COLLEGE)
    assert list(iter_pretrain(store, levels=levels, seed=3)) == _reference_pretrain(
        store, levels=levels, seed=3
    )


def test_iter_pretrain_on_an_empty_store_yields_nothing(tmp_path: Path) -> None:
    assert list(iter_pretrain(_empty_store(tmp_path), levels=(ReadingLevel.NEUTRAL,))) == []


def test_export_pretrain_still_writes_the_reference_file(
    pairs_world: LexemeStore, tmp_path: Path
) -> None:
    import json  # noqa: PLC0415 - only this test needs the serialiser

    levels = (ReadingLevel.NEUTRAL, ReadingLevel.GRADE_5)
    out = tmp_path / "pretrain.jsonl"
    summary = export_pretrain(pairs_world, out, levels=levels, seed=4)
    expected = "".join(
        json.dumps(doc.as_dict(), ensure_ascii=False) + "\n"
        for doc in _reference_pretrain(pairs_world, levels=levels, seed=4)
    )
    assert out.read_text(encoding="utf-8") == expected
    assert summary.documents_written == len(expected.splitlines())
