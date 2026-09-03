"""F4 — TREC-style graded qrels, a docs corpus, and a listwise JSONL, for $0 (D-56).

Shares ``export/triples.py``'s corpus (:func:`~opengloss_generator.export.triples.load_corpus`)
and candidate classification (:func:`~opengloss_generator.export.triples.classify`), because
the two exports answer related but different questions over the same graph: F3 picks *one*
negative per query for a contrastive triple, F4 grades *several* candidates per query for a
ranking metric (nDCG, MRR-graded, listwise loss). Reusing the same tiers keeps their answers
consistent with each other — a sense F3 calls a hard negative is exactly a sense F4 grades 0,
never something F4 quietly disagrees with.

**Grades.** 3 = the query's own sense (its canonical gloss); 2 = a direct synonym target
(the strongest non-identical match — deliberately never offered as an F3 negative, for the
same reason); 1 = a direct hypernym or a co-hyponym (a broader or sibling concept: related,
not a match); 0 = everything else, which includes every one of F3's graph hard-negative
kinds (another sense of the same headword, a ``confusable_with`` target, a synonym-of-a-
synonym) plus random easy negatives from unrelated headwords. Grade-2 and grade-1 pools are
capped (:data:`MAX_GRADE_2`, :data:`MAX_GRADE_1`) and, when larger, sampled — deterministically,
via the same seeded-``random.Random``-per-decision discipline ``export/triples.py`` uses — so a
richly connected sense does not dominate the file with near-duplicate candidates.

**Where the query comes from.** Identical to F3: ``Sense.queries`` (F2) when present,
else the sense's ``grade_5/plain`` gloss (or its canonical gloss) as a pseudo-query, with
``query_source`` recorded on every listwise query and readable off the query id (a
pseudo-query's id *is* the gloss rendition it stands in for). See ``export/triples.py``'s
module docstring for the full reasoning; it is not repeated here.

**Three output files**, all under one ``--out-dir``:

* ``qrels.trec`` — one ``qid 0 docid grade`` line per (query, candidate) pair, the
  standard ``trec_eval`` qrels format.
* ``docs.jsonl`` — one ``{"id", "text"}`` line per distinct document referenced anywhere
  in the qrels file (every document is a sense's canonical gloss; ids are sense ids).
* ``listwise.jsonl`` — one ``{"query", "query_id", "candidates": [{"id", "text", "grade"}]}``
  line per query, the same information as the other two files but grouped for a listwise
  trainer that wants one query's whole ranked list at once.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING

import orjson

from opengloss_generator.export.triples import (
    HARD_NEGATIVE_PRIORITY,
    classify,
    easy_negative_pool,
    load_corpus,
)
from opengloss_generator.export.triples import (
    _rng as _seeded_rng,
)

if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path

    from opengloss_generator.export.triples import SenseGraphInfo
    from opengloss_generator.store import LexemeStore

__all__ = [
    "GRADE_HYPERNYM_OR_COHYPONYM",
    "GRADE_OWN_SENSE",
    "GRADE_SYNONYM",
    "GRADE_UNRELATED",
    "ListwiseCandidate",
    "ListwiseQuery",
    "QrelEntry",
    "QrelsResult",
    "build_qrels",
    "write_qrels",
]

#: Relevance grades, TREC-style: higher is more relevant. See the module docstring.
GRADE_OWN_SENSE = 3
GRADE_SYNONYM = 2
GRADE_HYPERNYM_OR_COHYPONYM = 1
GRADE_UNRELATED = 0

#: Caps on how many candidates each non-trivial tier contributes per query, so a densely
#: connected sense does not crowd out everything else in the listwise file.
MAX_GRADE_2 = 3
MAX_GRADE_1 = 3
MAX_GRADE_0 = 3

#: The graph hard-negative kinds grade-0 draws one representative from each of, before
#: padding with easy negatives. Deliberately **not** ``HARD_NEGATIVE_PRIORITY`` itself:
#: that tuple's ``co_hyponym`` entry is already fully spent on grade 1 above (a
#: co-hyponym is "related, not a match" per the module docstring, not "unrelated"), so
#: reusing it here would let one sense land at two different grades for the same query.
_GRADE_0_KINDS: tuple[str, ...] = tuple(
    kind for kind in HARD_NEGATIVE_PRIORITY if kind != "co_hyponym"
)

#: How many times the grade-0 padding loop will retry drawing an easy negative before
#: giving up on filling the tier — bounds the loop when the easy pool is smaller than
#: ``MAX_GRADE_0`` (a store with very few headwords).
_MAX_EASY_PAD_ATTEMPTS = MAX_GRADE_0 * 4


def _sample(candidates: Iterable[str], cap: int, rng_key: tuple[int, str, str]) -> list[str]:
    """Return up to ``cap`` of ``candidates``, sorted whole or sampled deterministically.

    Args:
        candidates: The tier's live sense ids.
        cap: Maximum number to return.
        rng_key: ``(seed, sense_id, tier_name)`` — the key a sampling decision is seeded
            from when the tier is larger than ``cap``.

    Returns:
        ``sorted(candidates)`` unchanged when it already fits within ``cap``; otherwise
        a deterministic sample of that size.
    """
    pool = sorted(candidates)
    if len(pool) <= cap:
        return pool
    seed, sense_id, tier_name = rng_key
    return _seeded_rng(seed, sense_id, tier_name).sample(pool, cap)


def _grade_0_candidates(
    info: SenseGraphInfo,
    sense_id: str,
    seed: int,
    easy_pool: tuple[str, ...],
    already_graded: set[str],
) -> list[str]:
    """Return up to :data:`MAX_GRADE_0` grade-0 (unrelated) candidates for one sense.

    One representative per non-empty tier in :data:`_GRADE_0_KINDS` (the same per-tier
    seeded choice ``export/triples.py`` uses for its single hard negative, minus
    ``co_hyponym``, already spent on grade 1), then padded with random easy negatives
    until the cap is reached or the pool and retry budget run out. ``already_graded``
    (the own sense plus whatever grade 2/1 already claimed) is excluded from the easy
    pad so a sense that happens to be both a co-hyponym *and* in the wider easy pool is
    never offered twice at two different grades for one query — the graph tiers
    themselves are already disjoint (:func:`classify`), but the easy pool knows nothing
    about them.

    Args:
        info: The sense's tiered candidates.
        sense_id: The sense being graded.
        seed: The run's ``--seed``.
        easy_pool: This sense's lexeme-excluded easy-negative pool.
        already_graded: Every sense id this query has already assigned a grade to.

    Returns:
        Up to :data:`MAX_GRADE_0` distinct sense ids.
    """
    picks: list[str] = []
    for kind in _GRADE_0_KINDS:
        if len(picks) >= MAX_GRADE_0:
            break
        candidates = sorted(getattr(info, kind))
        if candidates:
            picks.append(_seeded_rng(seed, sense_id, kind).choice(candidates))

    attempts = 0
    while len(picks) < MAX_GRADE_0 and easy_pool and attempts < _MAX_EASY_PAD_ATTEMPTS:
        target = _seeded_rng(seed, sense_id, "easy", str(attempts)).choice(easy_pool)
        if target not in picks and target not in already_graded:
            picks.append(target)
        attempts += 1

    return picks


@dataclass(slots=True)
class GradedCandidate:
    """One document graded relative to one query's own sense."""

    sense_id: str
    grade: int


def _graded_candidates(
    info: SenseGraphInfo,
    sense_id: str,
    seed: int,
    easy_pool: tuple[str, ...],
) -> list[GradedCandidate]:
    """Return the full graded candidate list for one sense's queries.

    Args:
        info: The sense's tiered candidates.
        sense_id: The sense the candidates are graded relative to.
        seed: The run's ``--seed``.
        easy_pool: This sense's lexeme-excluded easy-negative pool.

    Returns:
        One :class:`GradedCandidate` per document, own sense first.
    """
    graded = [GradedCandidate(sense_id, GRADE_OWN_SENSE)]
    graded += [
        GradedCandidate(target, GRADE_SYNONYM)
        for target in _sample(info.synonym, MAX_GRADE_2, (seed, sense_id, "grade2"))
    ]
    tier_one_pool = info.hypernym | info.co_hyponym
    graded += [
        GradedCandidate(target, GRADE_HYPERNYM_OR_COHYPONYM)
        for target in _sample(tier_one_pool, MAX_GRADE_1, (seed, sense_id, "grade1"))
    ]
    already_graded = {candidate.sense_id for candidate in graded}
    graded += [
        GradedCandidate(target, GRADE_UNRELATED)
        for target in _grade_0_candidates(info, sense_id, seed, easy_pool, already_graded)
    ]
    return graded


@dataclass(slots=True)
class QrelEntry:
    """One TREC qrels row: ``qid 0 docid grade``."""

    query_id: str
    doc_id: str
    grade: int

    def as_trec_line(self) -> str:
        """Return this entry formatted as one ``trec_eval``-format qrels line."""
        return f"{self.query_id} 0 {self.doc_id} {self.grade}"


@dataclass(slots=True)
class ListwiseCandidate:
    """One graded candidate document within a :class:`ListwiseQuery`."""

    id: str
    text: str
    grade: int


@dataclass(slots=True)
class ListwiseQuery:
    """One query and its full graded candidate list."""

    query: str
    query_id: str
    query_source: str
    candidates: list[ListwiseCandidate] = field(default_factory=list)


@dataclass(slots=True)
class QrelsResult:
    """What one :func:`build_qrels` call produced."""

    qrels: list[QrelEntry]
    docs: dict[str, str]
    listwise: list[ListwiseQuery]
    entries_scanned: int
    senses_considered: int
    queries_considered: int
    grade_histogram: dict[int, int]

    def as_summary(self) -> dict[str, object]:
        """Return a JSON-able view for the CLI's run summary."""
        return {
            "entries_scanned": self.entries_scanned,
            "senses_considered": self.senses_considered,
            "queries_considered": self.queries_considered,
            "qrels_written": len(self.qrels),
            "docs_written": len(self.docs),
            "listwise_queries_written": len(self.listwise),
            "grade_histogram": {
                str(grade): count for grade, count in sorted(self.grade_histogram.items())
            },
        }


def build_qrels(store: LexemeStore, *, seed: int = 0, limit: int | None = None) -> QrelsResult:
    """Build graded qrels, a docs corpus, and listwise candidate lists for every query.

    Args:
        store: The store to read. Never written.
        seed: Seed for every deterministic sampling decision.
        limit: Cap on entries scanned, for a fast smoke run.

    Returns:
        The three outputs plus the counts and grade histogram the CLI reports.
    """
    corpus = load_corpus(store, limit=limit)
    qrels: list[QrelEntry] = []
    docs: dict[str, str] = {}
    listwise: list[ListwiseQuery] = []
    histogram: dict[int, int] = {}
    easy_pool_cache: dict[str, tuple[str, ...]] = {}
    senses_considered = 0
    queries_considered = 0

    for sense_id in sorted(corpus.gloss):
        senses_considered += 1
        info = classify(corpus, sense_id)
        pool = easy_negative_pool(corpus, corpus.lexeme_of[sense_id], easy_pool_cache)
        graded = _graded_candidates(info, sense_id, seed, pool)
        for candidate in graded:
            docs.setdefault(candidate.sense_id, corpus.gloss[candidate.sense_id])

        for query in corpus.queries.get(sense_id, ()):
            queries_considered += 1
            candidates = [
                ListwiseCandidate(id=c.sense_id, text=corpus.gloss[c.sense_id], grade=c.grade)
                for c in graded
            ]
            for candidate in candidates:
                qrels.append(
                    QrelEntry(query_id=query.query_id, doc_id=candidate.id, grade=candidate.grade)
                )
                histogram[candidate.grade] = histogram.get(candidate.grade, 0) + 1
            listwise.append(
                ListwiseQuery(
                    query=query.text,
                    query_id=query.query_id,
                    query_source=query.source,
                    candidates=candidates,
                )
            )

    return QrelsResult(
        qrels=qrels,
        docs=docs,
        listwise=listwise,
        entries_scanned=corpus.entries_scanned,
        senses_considered=senses_considered,
        queries_considered=queries_considered,
        grade_histogram=histogram,
    )


def write_qrels(result: QrelsResult, out_dir: Path) -> None:
    """Write ``result`` as ``qrels.trec``, ``docs.jsonl``, and ``listwise.jsonl``.

    Args:
        result: The built qrels, docs, and listwise queries.
        out_dir: Destination directory; created if absent.
    """
    out_dir.mkdir(parents=True, exist_ok=True)

    qrels_path = out_dir / "qrels.trec"
    with qrels_path.open("w", encoding="utf-8") as handle:
        for entry in result.qrels:
            handle.write(f"{entry.as_trec_line()}\n")

    docs_path = out_dir / "docs.jsonl"
    with docs_path.open("wb") as handle:
        for doc_id, text in sorted(result.docs.items()):
            handle.write(orjson.dumps({"id": doc_id, "text": text}) + b"\n")

    listwise_path = out_dir / "listwise.jsonl"
    with listwise_path.open("wb") as handle:
        for query in result.listwise:
            payload = {
                "query": query.query,
                "query_id": query.query_id,
                "query_source": query.query_source,
                "candidates": [asdict(candidate) for candidate in query.candidates],
            }
            handle.write(orjson.dumps(payload) + b"\n")
