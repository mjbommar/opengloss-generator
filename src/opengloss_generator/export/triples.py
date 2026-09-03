"""F3 — MS MARCO-style ``(query, positive, negative)`` triples, for $0 (D-56).

Every fact this module needs is already on disk: a sense's own gloss/example/
encyclopedia text is the positive, and the resolved semantic graph (other senses of the
same headword, ``confusable_with`` targets, co-hyponyms, and synonyms-of-synonyms) hands
back hard negatives that would otherwise cost a model call or a human annotator to mine.
No model is called anywhere in this module.

**Where the query comes from.** F2's ``Sense.queries`` (doc2query-style synthetic
queries) does not exist on ``main`` yet. Every query is read defensively with
``getattr(sense, "queries", [])``, so this module runs unchanged once F2 lands. When a
sense carries no queries, its ``grade_5/plain`` gloss rendition stands in as a
pseudo-query (or the canonical ``neutral/plain`` gloss, if ``grade_5`` is missing); either
way ``query_source`` on every record says which happened (``"generated"`` or
``"gloss_pseudo"``). A pseudo-query is, admittedly, close to the gloss text it retrieves —
that is the honest cost of not having F2 yet, not a bug, and it is exactly what
``query_source="gloss_pseudo"`` flags for a downstream trainer to filter or reweight.

**Hard negatives, in priority order.** For one query, the *single* hard negative is the
first non-empty candidate set in this order: another live sense of the same headword (the
classic WSD confusion — same surface form, wrong meaning); a ``confusable_with`` target;
a co-hyponym (a sibling sense sharing a direct hypernym); a synonym-of-a-synonym at graph
distance 2 (related by two hops, not a paraphrase). "Priority order" is a fallback chain,
not an enumeration to exhaust: one hard negative per query keeps this a triples format,
the way ``export-qrels`` (F4, same module family, ``export/qrels.py``) is not — that
export grades many candidates per query instead of picking one. A direct (distance-1)
synonym is deliberately never offered as a negative: it is close enough to the query's own
meaning that using it as a negative would teach the model something false; ``export-qrels``
gives it partial credit (grade 2) instead of leaving it out. ``--easy-negatives`` (default
1) adds that many additional triples per query whose negative is a random live sense from
a different headword — the "obviously wrong" contrast every triples set also needs.
Candidate sets are disjoint by construction (:func:`_classify`): a sense that qualifies for
two tiers is kept only in the higher-priority one, so the same target is never offered
under two different ``negative_kind`` values for one query.

**Determinism.** Every random choice — which positive text, which candidate within a
tied tier, which easy negative — is made by a fresh ``random.Random`` seeded from
``f"{seed}:{sense_id}:{...}"`` (:func:`_rng`), never from dict/set iteration order or a
single shared generator whose state would depend on visitation order. The same
``(store, seed)`` therefore always produces the same triples file, regardless of
filesystem order or worker count (there are no workers here — this is a single-pass,
in-memory export).

:func:`load_corpus` (also imported by ``export/qrels.py``, which grades the same
candidate tiers instead of picking one) is the one place the store is read; everything
downstream is pure computation over that projection, mirroring the load-once-then-plan
shape ``workflows/graph_hygiene.py`` uses for the same reason: a read taken once is
cheaper and easier to reason about than one repeated per sense.
"""

from __future__ import annotations

import random
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING

import orjson

from opengloss_generator.identity import encyclopedia_owner_id, rendition_id
from opengloss_generator.schema import ReadingLevel, Register, RelationType

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from opengloss_generator.schema import Sense
    from opengloss_generator.store import LexemeStore

__all__ = [
    "Corpus",
    "Triple",
    "TriplesResult",
    "build_triples",
    "load_corpus",
    "write_triples",
]

#: The four graph-derived hard-negative kinds, in fallback priority: the first one with a
#: live candidate for a given sense wins. Names double as :class:`SenseGraphInfo` field
#: names, so callers select a tier with ``getattr(info, kind)`` instead of a branch per
#: kind.
HARD_NEGATIVE_PRIORITY: tuple[str, ...] = (
    "other_sense",
    "confusable",
    "co_hyponym",
    "synonym_of_synonym",
)

#: ``negative_kind`` for a random, ungraphed contrast (a different headword entirely).
EASY_NEGATIVE_KIND = "easy"

#: ``query_source`` values (see the module docstring's "where the query comes from").
SOURCE_GENERATED = "generated"
SOURCE_GLOSS_PSEUDO = "gloss_pseudo"

#: Sense relation types the corpus loader keeps; every other type is irrelevant to the
#: hard-negative graph and is skipped while scanning to keep the projection small.
_GRAPH_RELATION_TYPES: tuple[RelationType, ...] = (
    RelationType.HYPERNYM,
    RelationType.HYPONYM,
    RelationType.SYNONYM,
    RelationType.CONFUSABLE_WITH,
)


def _rng(seed: int, *parts: str) -> random.Random:
    """Return a generator seeded deterministically from ``seed`` and ``parts``.

    Keying on the sense id (and a sub-key for what is being chosen) rather than
    advancing one shared generator means the result for one sense never depends on the
    order senses happen to be visited in — the property that makes the whole export
    reproducible independent of filesystem iteration order.

    Args:
        seed: The run's ``--seed``.
        parts: Additional key components, e.g. a sense id and a candidate-tier name.

    Returns:
        A fresh, independently seeded :class:`random.Random`.
    """
    key = ":".join((str(seed), *parts))
    return random.Random(key)  # noqa: S311 - deterministic sampling, not crypto


@dataclass(slots=True)
class QueryRecord:
    """One query this export will pair with a positive and a negative.

    Attributes:
        query_id: ``<sense_id>#q<n>`` for an F2 query (matching F2's own positional id
            scheme); a rendition id (``<sense_id>#grade_5/plain`` or
            ``<sense_id>#neutral/plain``) for a pseudo-query, since it *is* that
            rendition's text.
        text: The query text.
        source: :data:`SOURCE_GENERATED` or :data:`SOURCE_GLOSS_PSEUDO`.
    """

    query_id: str
    text: str
    source: str


@dataclass(slots=True)
class _RawRelation:
    """One relation edge, before it is known whether both ends are live."""

    sense_id: str
    type: RelationType
    target_sense: str | None


@dataclass(slots=True)
class Corpus:
    """A sense-level projection of the store, built once and read by both F3 and F4.

    Every mapping is keyed by (or, for ``senses_by_lexeme``/``children``, valued with)
    live sense ids only — retired senses are dropped while loading, so nothing
    downstream has to check ``retired`` again.

    Attributes:
        entries_scanned: How many entries :func:`load_corpus` read.
        headwords: ``lexeme_id -> headword``.
        lexeme_of: ``sense_id -> owning lexeme_id``.
        gloss: ``sense_id -> canonical (neutral/plain) gloss text``.
        example: ``sense_id -> one example's text``, when the sense has at least one.
        encyclopedia: ``lexeme_id -> canonical (neutral/plain) encyclopedia text``, when
            the lexeme has one.
        queries: ``sense_id -> its query list`` (see :func:`_queries_for`).
        senses_by_lexeme: ``lexeme_id -> live sense ids under it, any part of speech``.
        synonyms: ``sense_id -> directly synonymous live sense ids``, both directions
            recorded regardless of which side of the relation was stored (synonymy is
            symmetric by definition, per :data:`~opengloss_generator.schema.RelationType`).
        hypernyms: ``sense_id -> its direct hypernym live sense ids`` (specific -> more
            general), folding in ``hyponym`` relations read the other way round.
        children: The inverse of ``hypernyms``: ``hypernym_sense_id -> its direct
            hyponyms``. Two senses under the same hypernym are co-hyponyms.
        confusables: ``sense_id -> confusable live sense ids``, both directions.
        all_live_sense_ids: Every live sense id, sorted once, for easy-negative sampling.
    """

    entries_scanned: int = 0
    headwords: dict[str, str] = field(default_factory=dict)
    lexeme_of: dict[str, str] = field(default_factory=dict)
    gloss: dict[str, str] = field(default_factory=dict)
    example: dict[str, str] = field(default_factory=dict)
    encyclopedia: dict[str, str] = field(default_factory=dict)
    queries: dict[str, list[QueryRecord]] = field(default_factory=dict)
    senses_by_lexeme: dict[str, list[str]] = field(default_factory=dict)
    synonyms: dict[str, set[str]] = field(default_factory=dict)
    hypernyms: dict[str, set[str]] = field(default_factory=dict)
    children: dict[str, set[str]] = field(default_factory=dict)
    confusables: dict[str, set[str]] = field(default_factory=dict)
    all_live_sense_ids: tuple[str, ...] = ()

    def co_hyponyms_of(self, sense_id: str) -> set[str]:
        """Return every live sense sharing a direct hypernym with ``sense_id``."""
        siblings: set[str] = set()
        for hypernym in self.hypernyms.get(sense_id, ()):
            siblings |= self.children.get(hypernym, set())
        siblings.discard(sense_id)
        return siblings

    def synonym_of_synonym(self, sense_id: str) -> set[str]:
        """Return live senses reachable by two ``synonym`` hops, excluding direct ones."""
        direct = self.synonyms.get(sense_id, set())
        distance_two: set[str] = set()
        for synonym in direct:
            distance_two |= self.synonyms.get(synonym, set())
        distance_two -= direct
        distance_two.discard(sense_id)
        return distance_two


def _queries_for(sense: Sense, sense_id: str) -> list[QueryRecord]:
    """Return ``sense``'s F2 queries, or a single gloss pseudo-query when it has none.

    Args:
        sense: The sense to build queries for.
        sense_id: Its derived sense id.

    Returns:
        One or more :class:`QueryRecord`, never empty: every sense has a canonical
        gloss, so the pseudo-query fallback always has something to fall back to.
    """
    raw_queries = getattr(sense, "queries", [])
    if raw_queries:
        return [
            QueryRecord(
                query_id=f"{sense_id}#q{index + 1}", text=query.text, source=SOURCE_GENERATED
            )
            for index, query in enumerate(raw_queries)
        ]
    grade_5 = sense.gloss.get(ReadingLevel.GRADE_5, Register.PLAIN)
    if grade_5 is not None:
        return [
            QueryRecord(
                query_id=rendition_id(sense_id, ReadingLevel.GRADE_5.value, Register.PLAIN.value),
                text=grade_5.content,
                source=SOURCE_GLOSS_PSEUDO,
            )
        ]
    return [
        QueryRecord(
            query_id=rendition_id(sense_id, ReadingLevel.NEUTRAL.value, Register.PLAIN.value),
            text=sense.canonical_gloss(),
            source=SOURCE_GLOSS_PSEUDO,
        )
    ]


def load_corpus(store: LexemeStore, *, limit: int | None = None) -> Corpus:
    """Project the store's live senses and resolved graph into a :class:`Corpus`.

    Two passes over the data the store already holds, neither calling a model: the
    first reads every entry once and collects each live sense's own text plus its raw
    relation edges; the second resolves those edges against the now-complete set of live
    sense ids (a target seen before its own lexeme was scanned would otherwise look
    unresolved). This mirrors ``workflows/graph_hygiene.py``'s load-then-build shape.

    Args:
        store: The store to read. Never written.
        limit: Cap on the number of entries scanned, for a fast smoke run.

    Returns:
        The corpus both :func:`build_triples` and ``export.qrels.build_qrels`` are
        built from.
    """
    corpus = Corpus()
    raw_relations: list[_RawRelation] = []
    live: set[str] = set()

    for entry in store.iter_entries():
        if limit is not None and corpus.entries_scanned >= limit:
            break
        corpus.entries_scanned += 1
        corpus.headwords[entry.lexeme_id] = entry.headword
        canonical_encyclopedia = entry.encyclopedia.canonical()
        if canonical_encyclopedia is not None:
            corpus.encyclopedia[entry.lexeme_id] = canonical_encyclopedia.content
        for _pos_entry, sense, sid in entry.iter_senses():
            if sense.retired:
                continue
            live.add(sid)
            corpus.lexeme_of[sid] = entry.lexeme_id
            corpus.gloss[sid] = sense.canonical_gloss()
            corpus.senses_by_lexeme.setdefault(entry.lexeme_id, []).append(sid)
            first_example = next(iter(sense.examples), None)
            if first_example is not None:
                corpus.example[sid] = first_example.content.text
            corpus.queries[sid] = _queries_for(sense, sid)
            for relation in sense.relations:
                if relation.type in _GRAPH_RELATION_TYPES:
                    raw_relations.append(_RawRelation(sid, relation.type, relation.target.sense_id))

    _resolve_graph(corpus, raw_relations, live)
    corpus.all_live_sense_ids = tuple(sorted(live))
    return corpus


def _resolve_graph(corpus: Corpus, raw_relations: list[_RawRelation], live: set[str]) -> None:
    """Fold resolved, live-to-live relation edges into ``corpus``'s graph maps.

    Args:
        corpus: The corpus to fill in, mutated in place.
        raw_relations: Every ``hypernym``/``hyponym``/``synonym``/``confusable_with``
            relation seen while loading, before liveness of the target was known.
        live: Every live sense id, now complete.
    """
    for ref in raw_relations:
        if ref.target_sense is None or ref.target_sense not in live or ref.sense_id not in live:
            continue
        if ref.type is RelationType.HYPERNYM:
            corpus.hypernyms.setdefault(ref.sense_id, set()).add(ref.target_sense)
        elif ref.type is RelationType.HYPONYM:
            corpus.hypernyms.setdefault(ref.target_sense, set()).add(ref.sense_id)
        elif ref.type is RelationType.SYNONYM:
            corpus.synonyms.setdefault(ref.sense_id, set()).add(ref.target_sense)
            corpus.synonyms.setdefault(ref.target_sense, set()).add(ref.sense_id)
        elif ref.type is RelationType.CONFUSABLE_WITH:
            corpus.confusables.setdefault(ref.sense_id, set()).add(ref.target_sense)
            corpus.confusables.setdefault(ref.target_sense, set()).add(ref.sense_id)

    for sense_id, hypernym_set in corpus.hypernyms.items():
        for hypernym in hypernym_set:
            corpus.children.setdefault(hypernym, set()).add(sense_id)


@dataclass(slots=True)
class SenseGraphInfo:
    """One sense's candidate pool, partitioned into disjoint priority tiers.

    Each live sense related to the owning sense appears in exactly one field: the
    highest-priority tier it qualifies for. A sense that is both a direct synonym and a
    co-hyponym, for instance, is kept only in ``synonym`` — the stronger claim — so it
    can never be offered twice under two different ``negative_kind`` values.

    Attributes:
        synonym: Direct (distance-1) synonym targets. Never used as a negative (see the
            module docstring); ``export.qrels`` grades these 2.
        hypernym: Direct hypernym targets, excluding anything already in ``synonym``.
        co_hyponym: Siblings sharing a direct hypernym, excluding ``synonym``/``hypernym``.
        other_sense: Other live senses of the same headword, excluding higher tiers.
        confusable: ``confusable_with`` targets, excluding higher tiers.
        synonym_of_synonym: Distance-2 synonym targets, excluding higher tiers.
    """

    synonym: set[str]
    hypernym: set[str]
    co_hyponym: set[str]
    other_sense: set[str]
    confusable: set[str]
    synonym_of_synonym: set[str]


def classify(corpus: Corpus, sense_id: str) -> SenseGraphInfo:
    """Partition every sense related to ``sense_id`` into disjoint priority tiers.

    Args:
        corpus: The loaded corpus.
        sense_id: The sense to classify relative to.

    Returns:
        The tiered candidate pools; see :class:`SenseGraphInfo`.
    """
    synonym = set(corpus.synonyms.get(sense_id, ()))
    synonym.discard(sense_id)

    hypernym = set(corpus.hypernyms.get(sense_id, ())) - synonym
    hypernym.discard(sense_id)

    co_hyponym = corpus.co_hyponyms_of(sense_id) - synonym - hypernym

    lexeme_id = corpus.lexeme_of[sense_id]
    other_sense = set(corpus.senses_by_lexeme.get(lexeme_id, ())) - synonym - hypernym - co_hyponym
    other_sense.discard(sense_id)

    confusable = (
        set(corpus.confusables.get(sense_id, ())) - synonym - hypernym - co_hyponym - other_sense
    )
    confusable.discard(sense_id)

    synonym_of_synonym = (
        corpus.synonym_of_synonym(sense_id)
        - synonym
        - hypernym
        - co_hyponym
        - other_sense
        - confusable
    )

    return SenseGraphInfo(
        synonym=synonym,
        hypernym=hypernym,
        co_hyponym=co_hyponym,
        other_sense=other_sense,
        confusable=confusable,
        synonym_of_synonym=synonym_of_synonym,
    )


@dataclass(slots=True)
class PositiveOption:
    """One candidate positive text for a sense: gloss, example, or encyclopedia."""

    source: str
    doc_id: str
    text: str


def positive_options(corpus: Corpus, sense_id: str) -> list[PositiveOption]:
    """Return the available positive texts for ``sense_id``.

    The canonical gloss is always available (every sense has one); one example and the
    owning lexeme's neutral encyclopedia entry are added when present.

    Args:
        corpus: The loaded corpus.
        sense_id: The sense to draw positives for.

    Returns:
        At least one option, gloss first.
    """
    lexeme_id = corpus.lexeme_of[sense_id]
    options = [PositiveOption(source="gloss", doc_id=sense_id, text=corpus.gloss[sense_id])]
    example = corpus.example.get(sense_id)
    if example is not None:
        options.append(PositiveOption(source="example", doc_id=f"{sense_id}#example", text=example))
    encyclopedia = corpus.encyclopedia.get(lexeme_id)
    if encyclopedia is not None:
        options.append(
            PositiveOption(
                source="encyclopedia", doc_id=encyclopedia_owner_id(lexeme_id), text=encyclopedia
            )
        )
    return options


def easy_negative_pool(
    corpus: Corpus, lexeme_id: str, cache: dict[str, tuple[str, ...]]
) -> tuple[str, ...]:
    """Return live sense ids outside ``lexeme_id``, the pool an easy negative is drawn from.

    Memoised per lexeme in ``cache``: many senses share a lexeme, and the pool is the
    same (store-wide minus that lexeme's own senses) for all of them.

    Args:
        corpus: The loaded corpus.
        lexeme_id: The lexeme whose own senses must be excluded.
        cache: ``lexeme_id -> pool``, filled in on first use.

    Returns:
        A sorted tuple of eligible sense ids; empty only for a single-lexeme store.
    """
    pool = cache.get(lexeme_id)
    if pool is None:
        own = set(corpus.senses_by_lexeme.get(lexeme_id, ()))
        pool = tuple(sid for sid in corpus.all_live_sense_ids if sid not in own)
        cache[lexeme_id] = pool
    return pool


def select_hard_negative(info: SenseGraphInfo, seed: int, sense_id: str) -> tuple[str, str] | None:
    """Return ``(kind, target_sense_id)`` for the first non-empty tier, in priority order.

    Args:
        info: The sense's tiered candidates.
        seed: The run's ``--seed``.
        sense_id: The sense a negative is being chosen for.

    Returns:
        The chosen ``(negative_kind, target_sense_id)``, or ``None`` if every tier is
        empty (a sense with no graph relations at all, e.g. the sole sense of an
        otherwise unconnected headword).
    """
    for kind in HARD_NEGATIVE_PRIORITY:
        candidates = sorted(getattr(info, kind))
        if candidates:
            return kind, _rng(seed, sense_id, kind).choice(candidates)
    return None


@dataclass(slots=True)
class Triple:
    """One ``(query, positive, negative)`` training row."""

    query: str
    positive: str
    negative: str
    negative_kind: str
    query_id: str
    positive_id: str
    negative_id: str
    query_source: str


@dataclass(slots=True)
class TriplesResult:
    """What one :func:`build_triples` call produced."""

    triples: list[Triple]
    entries_scanned: int
    senses_considered: int
    queries_considered: int
    by_negative_kind: dict[str, int]

    def as_summary(self) -> dict[str, object]:
        """Return a JSON-able view for the CLI's run summary."""
        return {
            "entries_scanned": self.entries_scanned,
            "senses_considered": self.senses_considered,
            "queries_considered": self.queries_considered,
            "triples_written": len(self.triples),
            "by_negative_kind": dict(sorted(self.by_negative_kind.items())),
        }


def _triples_for_query(
    query: QueryRecord,
    positive: PositiveOption,
    info: SenseGraphInfo,
    corpus: Corpus,
    *,
    seed: int,
    sense_id: str,
    easy_negatives: int,
    easy_pool: tuple[str, ...],
) -> Iterator[Triple]:
    """Yield the hard-negative triple (if any) and the easy-negative triples for one query.

    Args:
        query: The query the triples are anchored on.
        positive: The sense's chosen positive text.
        info: The sense's tiered candidates.
        corpus: The loaded corpus, for negative doc text.
        seed: The run's ``--seed``.
        sense_id: The query's own sense id.
        easy_negatives: How many easy-negative triples to emit for this query.
        easy_pool: This sense's lexeme-excluded easy-negative pool.

    Yields:
        One :class:`Triple` per negative produced.
    """
    hard = select_hard_negative(info, seed, sense_id)
    if hard is not None:
        kind, target = hard
        yield Triple(
            query=query.text,
            positive=positive.text,
            negative=corpus.gloss[target],
            negative_kind=kind,
            query_id=query.query_id,
            positive_id=positive.doc_id,
            negative_id=target,
            query_source=query.source,
        )

    for attempt in range(easy_negatives):
        if not easy_pool:
            break
        target = _rng(seed, sense_id, "easy", str(attempt)).choice(easy_pool)
        yield Triple(
            query=query.text,
            positive=positive.text,
            negative=corpus.gloss[target],
            negative_kind=EASY_NEGATIVE_KIND,
            query_id=query.query_id,
            positive_id=positive.doc_id,
            negative_id=target,
            query_source=query.source,
        )


def build_triples(
    store: LexemeStore, *, seed: int = 0, easy_negatives: int = 1, limit: int | None = None
) -> TriplesResult:
    """Build every ``(query, positive, negative)`` triple the store's live senses support.

    One hard-negative triple per query (when the sense has any graph-derived candidate
    at all) plus ``easy_negatives`` random-headword triples per query. A sense with no
    queries of its own never happens — :func:`_queries_for` always returns at least the
    gloss pseudo-query — so every live sense contributes at least ``easy_negatives``
    triples, and one more when it has any graph relation to draw a hard negative from.

    Args:
        store: The store to read. Never written.
        seed: Seed for every deterministic sampling decision (see :func:`_rng`).
        easy_negatives: Easy-negative triples to emit per query.
        limit: Cap on entries scanned, for a fast smoke run.

    Returns:
        The triples plus the counts the CLI reports.
    """
    corpus = load_corpus(store, limit=limit)
    triples: list[Triple] = []
    by_kind: dict[str, int] = {}
    easy_pool_cache: dict[str, tuple[str, ...]] = {}
    senses_considered = 0
    queries_considered = 0

    for sense_id in sorted(corpus.gloss):
        senses_considered += 1
        info = classify(corpus, sense_id)
        options = positive_options(corpus, sense_id)
        positive = _rng(seed, sense_id, "positive").choice(options)
        pool = easy_negative_pool(corpus, corpus.lexeme_of[sense_id], easy_pool_cache)

        for query in corpus.queries.get(sense_id, ()):
            queries_considered += 1
            for triple in _triples_for_query(
                query,
                positive,
                info,
                corpus,
                seed=seed,
                sense_id=sense_id,
                easy_negatives=easy_negatives,
                easy_pool=pool,
            ):
                triples.append(triple)
                by_kind[triple.negative_kind] = by_kind.get(triple.negative_kind, 0) + 1

    return TriplesResult(
        triples=triples,
        entries_scanned=corpus.entries_scanned,
        senses_considered=senses_considered,
        queries_considered=queries_considered,
        by_negative_kind=by_kind,
    )


def write_triples(result: TriplesResult, out_path: Path) -> None:
    """Write ``result``'s triples as JSONL, one object per line.

    Args:
        result: The triples to write.
        out_path: Destination path; parent directories are created.
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("wb") as handle:
        for triple in result.triples:
            handle.write(orjson.dumps(asdict(triple)) + b"\n")
