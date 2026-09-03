"""Measure a store against the "pristine entry" definition (``docs/CORE-DIARY.md``).

``audit_store`` computes, over the entries it is given, exactly the checklist the
diary's "Definition of pristine" enumerates: kind classification, sense domain tagging,
example spans, relation resolution, suspected artifact relations, rendition coverage,
and a handful of free consistency checks -- including two graph checks over the
relations, reported under ``as_dict()["graph"]``: hypernym acyclicity (cycles and
same-lexeme self-loops) and symmetric-relation reciprocity (D-40). It makes no model
calls and mutates nothing; it is meant to run before and after every retrofit/enrich
iteration so the diary can report what changed.

:meth:`AuditReport.top_gaps` turns the numbers back into the question an iteration
starts from: which part of the pristine definition is furthest from done right now.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from opengloss_generator.hygiene import is_headword_initial
from opengloss_generator.identity import slugify
from opengloss_generator.schema import (
    LexemeKind,
    QAFlag,
    ReadingLevel,
    Register,
    RelationType,
    StageName,
)

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator

    from opengloss_generator.schema import Lexeme, Relation, Rendition, Sense
    from opengloss_generator.store import LexemeStore

__all__ = ["ARTIFACT_STOPLIST", "AuditReport", "audit_store"]

#: Surface forms that are almost always a leftover hypernym-slot label ("what kind of
#: thing is this?") rather than a real word the model meant to name — diary item 5.
ARTIFACT_STOPLIST: frozenset[str] = frozenset(
    {
        "descriptor",
        "descriptive term",
        "descriptive adjective",
        "descriptive word",
        "term",
        "word",
        "thing",
        "adjective",
        "noun",
        "verb",
        "concept",
        "general term",
    }
)

#: A migration placeholder kind: the fallback ``migrate.py`` assigns when it cannot
#: decide a headword's real kind. An entry still carrying one of these, with no
#: ``classify_kind`` provenance record, has not been examined by the retrofit pass yet.
_PLACEHOLDER_KINDS = frozenset({LexemeKind.SIMPLEX, LexemeKind.COMPOUND})

#: Rendition targets the diary's "pristine" definition requires per owner (items 6-8):
#: glosses get 4 graded levels plus 4 registers, examples and the encyclopedia get the
#: 4 graded levels only (registers are off for both — cost, for the encyclopedia; the
#: reader sees one example, for examples).
_GLOSS_TARGETS: tuple[tuple[ReadingLevel, Register], ...] = (
    (ReadingLevel.GRADE_1, Register.PLAIN),
    (ReadingLevel.GRADE_5, Register.PLAIN),
    (ReadingLevel.GRADE_10, Register.PLAIN),
    (ReadingLevel.COLLEGE, Register.PLAIN),
    (ReadingLevel.NEUTRAL, Register.INFORMAL),
    (ReadingLevel.NEUTRAL, Register.TECHNICAL),
    (ReadingLevel.NEUTRAL, Register.FORMAL),
    (ReadingLevel.NEUTRAL, Register.MARKETING),
)
_LEVEL_ONLY_TARGETS: tuple[tuple[ReadingLevel, Register], ...] = (
    (ReadingLevel.GRADE_1, Register.PLAIN),
    (ReadingLevel.GRADE_5, Register.PLAIN),
    (ReadingLevel.GRADE_10, Register.PLAIN),
    (ReadingLevel.COLLEGE, Register.PLAIN),
)
_PRISTINE_RENDITION_TARGETS: dict[str, tuple[tuple[ReadingLevel, Register], ...]] = {
    "gloss": _GLOSS_TARGETS,
    "examples": _LEVEL_ONLY_TARGETS,
    "encyclopedia": _LEVEL_ONLY_TARGETS,
}

_RENDITION_FIELDS: tuple[str, ...] = ("gloss", "examples", "encyclopedia", "lexical_explanation")

_WORD_RE = re.compile(r"[^\W_]+", re.UNICODE)

#: Relation types a pristine pair should assert both ways (diary item 9's reciprocity
#: check): if sense A asserts one of these toward lexeme B, B should assert the same
#: type back toward A somewhere in its own senses.
_SYMMETRIC_RELATION_TYPES: tuple[RelationType, ...] = (
    RelationType.SYNONYM,
    RelationType.ANTONYM,
    RelationType.CONFUSABLE_WITH,
)


def _pct(count: int, total: int) -> float:
    """Return ``count`` as a percentage of ``total``, or ``0.0`` if ``total`` is zero."""
    return round(count / total * 100.0, 2) if total else 0.0


def _first_word(text: str) -> str:
    """Return the lowercased first word-like token of ``text``, or ``""`` if none."""
    match = _WORD_RE.search(text)
    return match.group(0).lower() if match else ""


def _rendition_key(reading_level: ReadingLevel, style: Register) -> str:
    """Return the ``"level/register"`` key used across the coverage matrix."""
    return f"{reading_level.value}/{style.value}"


def _is_kind_classified(entry: Lexeme) -> bool:
    """Mirror ``workflows.retrofit``'s idempotence signal for the ``classify_kind`` pass.

    An entry is classified once it either carries the pass's provenance marker, or its
    kind is not one of the migration placeholders that fallback rule 6 assigns
    (``migrate.classify_kind_deterministic``): anything more specific than
    simplex/compound was decided with real signal, whatever wrote it.
    """
    if entry.kind not in _PLACEHOLDER_KINDS:
        return True
    return any(record.stage is StageName.CLASSIFY_KIND for record in entry.provenance.values())


#: A target longer than this many words is treated as a description, not a headword.
_ARTIFACT_MAX_WORDS = 4


def _is_artifact_target(term: str) -> bool:
    """Return whether a relation target looks like a leftover slot label, not a word."""
    normalized = term.strip().lower()
    if normalized in ARTIFACT_STOPLIST:
        return True
    return len(normalized.split()) > _ARTIFACT_MAX_WORDS


@dataclass(slots=True)
class AuditReport:
    """Counts backing every metric in the diary's "Definition of pristine".

    Every field here is a raw count; percentages are computed on demand in
    :meth:`as_dict` and :meth:`top_gaps` so the two never have to be kept in sync by
    hand.
    """

    entries_total: int = 0
    core_restricted: bool = False

    kind_classified: int = 0

    senses_total: int = 0
    senses_with_domain: int = 0

    examples_total: int = 0
    examples_with_span: int = 0

    relations_total: int = 0
    relations_resolved: int = 0
    relation_location_buckets: dict[str, dict[str, int]] = field(default_factory=dict)

    artifact_relations: int = 0

    rendition_coverage: dict[str, dict[str, int]] = field(
        default_factory=lambda: {name: {} for name in _RENDITION_FIELDS}
    )
    owners_total: dict[str, int] = field(
        default_factory=lambda: dict.fromkeys(_RENDITION_FIELDS, 0)
    )

    gloss_starts_with_headword: int = 0
    gloss_renditions_checked: int = 0
    gloss_renditions_headword_initial: int = 0
    duplicate_canonical_gloss_entries: int = 0
    senses_zero_relations: int = 0
    entries_zero_examples: int = 0
    renditions_with_readability_miss_flag: int = 0

    hypernym_cycle_count: int = 0
    hypernym_cycle_examples: list[list[str]] = field(default_factory=list)
    hypernym_self_loops: int = 0
    reciprocity: dict[str, dict[str, int]] = field(default_factory=dict)

    def as_dict(self) -> dict[str, object]:
        """Return a JSON-able view of every metric, counts and percentages together."""
        rendition_coverage = {
            name: {
                "owners_total": self.owners_total[name],
                "targets": {
                    key: {"count": count, "pct": _pct(count, self.owners_total[name])}
                    for key, count in sorted(counts.items())
                },
            }
            for name, counts in self.rendition_coverage.items()
        }
        by_target_location = {
            bucket: {
                "total": values["total"],
                "resolved": values["resolved"],
                "resolved_pct": _pct(values["resolved"], values["total"]),
            }
            for bucket, values in sorted(self.relation_location_buckets.items())
        }
        reciprocity = {
            rel_type: {
                "asserted": counts["asserted"],
                "reciprocated": counts["reciprocated"],
                "pct": _pct(counts["reciprocated"], counts["asserted"]),
            }
            for rel_type, counts in sorted(self.reciprocity.items())
        }
        return {
            "entries_total": self.entries_total,
            "core_restricted": self.core_restricted,
            "kind_classified": {
                "count": self.kind_classified,
                "total": self.entries_total,
                "pct": _pct(self.kind_classified, self.entries_total),
            },
            "senses_with_domain": {
                "count": self.senses_with_domain,
                "total": self.senses_total,
                "pct": _pct(self.senses_with_domain, self.senses_total),
            },
            "canonical_examples_with_span": {
                "count": self.examples_with_span,
                "total": self.examples_total,
                "pct": _pct(self.examples_with_span, self.examples_total),
            },
            "relations": {
                "total": self.relations_total,
                "resolved": self.relations_resolved,
                "resolved_pct": _pct(self.relations_resolved, self.relations_total),
                "by_target_location": by_target_location,
            },
            "artifact_relations": {
                "count": self.artifact_relations,
                "total": self.relations_total,
                "pct": _pct(self.artifact_relations, self.relations_total),
            },
            "rendition_coverage": rendition_coverage,
            "consistency": {
                "gloss_starts_with_headword": self.gloss_starts_with_headword,
                "gloss_renditions_headword_initial": {
                    "count": self.gloss_renditions_headword_initial,
                    "total": self.gloss_renditions_checked,
                    "pct": _pct(
                        self.gloss_renditions_headword_initial, self.gloss_renditions_checked
                    ),
                },
                "duplicate_canonical_gloss_entries": self.duplicate_canonical_gloss_entries,
                "senses_zero_relations": self.senses_zero_relations,
                "entries_zero_examples": self.entries_zero_examples,
                "renditions_with_readability_miss_flag": self.renditions_with_readability_miss_flag,
            },
            "graph": {
                "hypernym_cycles": {
                    "count": self.hypernym_cycle_count,
                    "examples": self.hypernym_cycle_examples,
                },
                "hypernym_self_loops": self.hypernym_self_loops,
                "reciprocity": reciprocity,
            },
        }

    def top_gaps(self, n: int = 3) -> list[str]:
        """Return the ``n`` largest shortfalls against the pristine definition.

        Each candidate is one line item of the diary's checklist, scored by how far
        short of 100% it is (or, for artifact relations, how far above 0% it is — a
        defect rate is already its own shortfall). This is where a diary iteration
        starts: fix whatever comes back first.

        Args:
            n: How many gaps to return.

        Returns:
            Up to ``n`` human-readable descriptions, largest shortfall first.
        """
        gaps: list[tuple[str, float]] = []

        def add(label: str, count: int, total: int) -> None:
            shortfall = 100.0 - _pct(count, total)
            text = f"{label}: {count}/{total} ({shortfall:.1f}% short of pristine)"
            gaps.append((text, shortfall))

        add("kind classified", self.kind_classified, self.entries_total)
        add("senses tagged with domain", self.senses_with_domain, self.senses_total)
        add("canonical examples with a span", self.examples_with_span, self.examples_total)

        if self.core_restricted:
            core_bucket = self.relation_location_buckets.get("in_core", {"total": 0, "resolved": 0})
            add("core-target relations resolved", core_bucket["resolved"], core_bucket["total"])
        else:
            add("relations resolved", self.relations_resolved, self.relations_total)

        for name, targets in _PRISTINE_RENDITION_TARGETS.items():
            owners = self.owners_total[name]
            counts = self.rendition_coverage[name]
            have = sum(counts.get(_rendition_key(level, style), 0) for level, style in targets)
            add(f"{name} rendition coverage", have, owners * len(targets))

        artifact_pct = _pct(self.artifact_relations, self.relations_total)
        gaps.append(
            (
                f"suspected artifact relations: {self.artifact_relations}/{self.relations_total} "
                f"({artifact_pct:.1f}%)",
                artifact_pct,
            )
        )

        gaps.sort(key=lambda item: item[1], reverse=True)
        labels = [label for label, _ in gaps]

        # A hypernym cycle is a defect, not a coverage shortfall -- it has no percentage
        # to rank against the others, so it is not folded into the sort above. It is
        # simply the first thing an iteration should look at whenever one exists.
        if self.hypernym_cycle_count > 0:
            cycle_label = f"hypernym cycles: {self.hypernym_cycle_count} found (not acyclic)"
            labels = [cycle_label, *labels]

        return labels[:n]


def _bump(coverage: dict[str, int], key: str) -> None:
    """Increment a coverage counter, initialising it on first use."""
    coverage[key] = coverage.get(key, 0) + 1


def _bucket(buckets: dict[str, dict[str, int]], name: str) -> dict[str, int]:
    """Return (creating if needed) the ``{total, resolved}`` counters for a bucket."""
    return buckets.setdefault(name, {"total": 0, "resolved": 0})


def _bump_field_coverage(
    report: AuditReport, field_name: str, renditions: Iterable[Rendition[Any]]
) -> None:
    """Tally coverage for one owner's renditions of one field.

    Also counts (never writes) renditions already carrying
    :data:`~opengloss_generator.schema.QAFlag.OG_READABILITY_MISS`, so a diary iteration
    can see how much of that flag ``workflows/enrich.py`` has populated
    (docs/STANDARDS-PLAN.md § 3, B3) without this read-only pass ever setting it itself.
    """
    for rendition in renditions:
        key = _rendition_key(rendition.reading_level, rendition.style)
        _bump(report.rendition_coverage[field_name], key)
        if rendition.assessment is not None and QAFlag.OG_READABILITY_MISS in (
            rendition.assessment.qa_flags
        ):
            report.renditions_with_readability_miss_flag += 1


def _audit_relation(
    report: AuditReport, relation: Relation, *, core_ids: set[str] | None, store: LexemeStore
) -> None:
    """Fold one relation into the resolution, location, and artifact counts."""
    report.relations_total += 1
    resolved = relation.target.sense_id is not None
    if resolved:
        report.relations_resolved += 1
    if _is_artifact_target(relation.target.term):
        report.artifact_relations += 1

    target_id = relation.target.lexeme_id
    if core_ids is not None and target_id in core_ids:
        location = "in_core"
    elif store.exists(target_id):
        location = "in_store_not_core" if core_ids is not None else "in_store"
    else:
        location = "not_in_store"
    bucket = _bucket(report.relation_location_buckets, location)
    bucket["total"] += 1
    if resolved:
        bucket["resolved"] += 1


def _audit_sense(
    report: AuditReport,
    entry: Lexeme,
    sense: Sense,
    *,
    core_ids: set[str] | None,
    store: LexemeStore,
) -> tuple[str, int]:
    """Fold one non-retired sense into the report.

    Returns:
        Its canonical gloss text and its example count, for the entry-level checks.
    """
    if sense.domain is not None:
        report.senses_with_domain += 1
    if not sense.relations:
        report.senses_zero_relations += 1

    canonical_gloss = sense.canonical_gloss()
    # Proper-noun definitions legitimately name their entity ("The Congo River is a
    # major central African river…"); the headword-initial check only flags common
    # words. See CORE-DIARY iteration 2 and D-30.
    if (
        entry.kind is not LexemeKind.PROPER_NOUN
        and _first_word(canonical_gloss)
        and _first_word(canonical_gloss) == _first_word(entry.headword)
    ):
        report.gloss_starts_with_headword += 1

    # The same exemption, one level down: a rendition of a proper-noun definition is as
    # entitled to name its entity as the definition is. Canonical renditions are counted
    # by the check above, not here, so the two numbers stay separable -- iteration 4
    # measured the canonical rate at 2.7% and the rendition rate at 10-15% (D-39).
    if entry.kind is not LexemeKind.PROPER_NOUN:
        for rendition in sense.gloss:
            if rendition.is_canonical:
                continue
            report.gloss_renditions_checked += 1
            if is_headword_initial(rendition.content, entry.headword):
                report.gloss_renditions_headword_initial += 1

    _bump_field_coverage(report, "gloss", sense.gloss)
    _bump_field_coverage(report, "examples", sense.examples)

    canonical_example = sense.examples.canonical()
    if canonical_example is not None:
        report.examples_total += 1
        if canonical_example.content.span is not None:
            report.examples_with_span += 1

    for relation in sense.relations:
        _audit_relation(report, relation, core_ids=core_ids, store=store)

    return canonical_gloss, len(sense.examples)


def _audit_entry(
    report: AuditReport, entry: Lexeme, *, core_ids: set[str] | None, store: LexemeStore
) -> None:
    """Fold one entry, and every non-retired sense on it, into the report."""
    if _is_kind_classified(entry):
        report.kind_classified += 1

    non_retired = [sense for _, sense, _ in entry.iter_senses() if not sense.retired]
    report.senses_total += len(non_retired)
    report.owners_total["gloss"] += len(non_retired)
    report.owners_total["examples"] += len(non_retired)
    report.owners_total["encyclopedia"] += 1
    report.owners_total["lexical_explanation"] += 1

    results = [
        _audit_sense(report, entry, sense, core_ids=core_ids, store=store) for sense in non_retired
    ]
    canonical_glosses = [gloss for gloss, _ in results]
    if sum(count for _, count in results) == 0:
        report.entries_zero_examples += 1
    if len(canonical_glosses) != len(set(canonical_glosses)):
        report.duplicate_canonical_gloss_entries += 1

    _bump_field_coverage(report, "encyclopedia", entry.encyclopedia)
    _bump_field_coverage(report, "lexical_explanation", entry.lexical_explanation)


def _lexeme_of_sense(sid: str) -> str:
    """Return the lexeme id embedded in a sense id (``"lexeme:pos:index"``)."""
    return sid.rsplit(":", 2)[0]


def _build_hypernym_graph(entries: Iterable[Lexeme]) -> dict[str, set[str]]:
    """Project resolved hypernym/hyponym relations onto one sense-to-sense graph.

    A resolved ``hypernym`` relation contributes an edge in the direction it already
    points (specific sense -> general sense). A resolved ``hyponym`` relation points the
    other way round -- ``A`` calling ``B`` its hyponym means ``B`` is more specific -- so
    it is folded in reversed: diary item 9's acyclicity check wants one graph in the
    hypernym direction regardless of which of the two relation types asserted an edge.
    Unresolved targets contribute nothing: they cannot close a cycle.

    Args:
        entries: The audited entries (retired senses are skipped).

    Returns:
        An adjacency map from a sense id to the set of sense ids it points at.
    """
    graph: dict[str, set[str]] = {}
    for entry in entries:
        for _, sense, sid in entry.iter_senses():
            if sense.retired:
                continue
            for relation in sense.relations:
                target_sense_id = relation.target.sense_id
                if target_sense_id is None:
                    continue
                if relation.type is RelationType.HYPERNYM:
                    graph.setdefault(sid, set()).add(target_sense_id)
                elif relation.type is RelationType.HYPONYM:
                    graph.setdefault(target_sense_id, set()).add(sid)
    return graph


def _count_hypernym_self_loops(graph: dict[str, set[str]]) -> int:
    """Count graph edges whose two ends belong to the same lexeme.

    A sense whose hypernym (however it was asserted -- directly, or via a reversed
    hyponym) resolves to another sense of its own lexeme is never legitimate: nothing is
    its own hypernym, whichever sense of it does the pointing.

    Args:
        graph: The adjacency map built by :func:`_build_hypernym_graph`.

    Returns:
        The number of such edges.
    """
    return sum(
        1
        for source, targets in graph.items()
        for target in targets
        if _lexeme_of_sense(target) == _lexeme_of_sense(source)
    )


_UNVISITED, _IN_PROGRESS, _DONE = 0, 1, 2


def _find_hypernym_cycles(
    graph: dict[str, set[str]], *, max_examples: int = 5
) -> tuple[int, list[list[str]]]:
    """Find cycles in a sense-to-sense hypernym graph with an iterative DFS.

    The graph can hold on the order of 40K nodes, so this deliberately avoids Python
    recursion: each DFS frame is an explicit ``(node, neighbor iterator)`` pair on an
    explicit stack, and the current root-to-frontier path doubles as the node's
    depth-first ancestry. A "gray" neighbor (still on the stack) closes a cycle; a
    "black" one (already fully explored elsewhere) is a harmless cross edge.

    Args:
        graph: The adjacency map built by :func:`_build_hypernym_graph`.
        max_examples: The maximum number of example cycles to materialise.

    Returns:
        The total number of back edges found (one per cycle closed) and up to
        ``max_examples`` of the cycles themselves, each as the list of sense ids on it.
    """
    all_targets = (target for targets in graph.values() for target in targets)
    nodes = list(dict.fromkeys([*graph.keys(), *all_targets]))
    color: dict[str, int] = {}
    count = 0
    examples: list[list[str]] = []

    for start in nodes:
        if color.get(start, _UNVISITED) != _UNVISITED:
            continue
        stack: list[tuple[str, Iterator[str]]] = [(start, iter(graph.get(start, ())))]
        color[start] = _IN_PROGRESS
        while stack:
            node, neighbors = stack[-1]
            neighbor = next(neighbors, None)
            if neighbor is None:
                color[node] = _DONE
                stack.pop()
                continue
            state = color.get(neighbor, _UNVISITED)
            if state == _UNVISITED:
                color[neighbor] = _IN_PROGRESS
                stack.append((neighbor, iter(graph.get(neighbor, ()))))
            elif state == _IN_PROGRESS:
                count += 1
                if len(examples) < max_examples:
                    path = [frame_node for frame_node, _ in stack]
                    examples.append(path[path.index(neighbor) :])
            # state == _DONE: a cross edge into an already-finished subtree, not a cycle.

    return count, examples


def _relation_targets_lexeme(sense: Sense, rel_type: RelationType, lexeme_id: str) -> bool:
    """Return whether ``sense`` asserts ``rel_type`` toward ``lexeme_id``.

    Resolution does not matter here (diary item 9): the reciprocity check only needs to
    know that the *other* side made the matching claim at all, not that it was resolved.

    Args:
        sense: The sense whose relations to scan.
        rel_type: The relation type to look for.
        lexeme_id: The lexeme id the relation must target.

    Returns:
        Whether a matching relation exists.
    """
    return any(
        relation.type is rel_type and relation.target.lexeme_id == lexeme_id
        for relation in sense.relations
    )


def _audit_reciprocity(entries: Iterable[Lexeme]) -> dict[str, dict[str, int]]:
    """Measure how often a symmetric relation is asserted back by its target.

    For every resolved relation of a type in :data:`_SYMMETRIC_RELATION_TYPES` whose
    target lexeme is also among ``entries``, check whether that target entry asserts the
    same type back toward the source lexeme, in any of its own senses, resolved or not.
    This is a read-only measurement -- it never adds the missing relation.

    Args:
        entries: The audited entries. Only relations whose target is also in this set
            can be checked, since checking the far side means reading its own relations.

    Returns:
        Per relation type (as its string value), the ``{"asserted", "reciprocated"}``
        counts.
    """
    entries = list(entries)
    by_lexeme = {entry.lexeme_id: entry for entry in entries}
    counts: dict[str, dict[str, int]] = {
        rel_type.value: {"asserted": 0, "reciprocated": 0} for rel_type in _SYMMETRIC_RELATION_TYPES
    }

    for entry in entries:
        for _, sense, _ in entry.iter_senses():
            if sense.retired:
                continue
            for relation in sense.relations:
                if relation.type not in _SYMMETRIC_RELATION_TYPES:
                    continue
                if relation.target.sense_id is None:
                    continue
                target_entry = by_lexeme.get(relation.target.lexeme_id)
                if target_entry is None:
                    continue
                bucket = counts[relation.type.value]
                bucket["asserted"] += 1
                if any(
                    _relation_targets_lexeme(target_sense, relation.type, entry.lexeme_id)
                    for _, target_sense, _ in target_entry.iter_senses()
                    if not target_sense.retired
                ):
                    bucket["reciprocated"] += 1

    return counts


def audit_store(store: LexemeStore, core_words: set[str] | None = None) -> AuditReport:
    """Measure a store against the pristine-entry checklist.

    Args:
        store: The store to read. Never written.
        core_words: When given, restrict the audit to these headwords (as read from
            ``enrich --from-list``'s file) and use them as the "core set" that relation
            targets are checked against.

    Returns:
        An :class:`AuditReport` with the full set of counts.
    """
    core_ids = {slugify(word) for word in core_words} if core_words is not None else None

    if core_ids is not None:
        entries: list[Lexeme] = [
            entry for lexeme_id in sorted(core_ids) if (entry := store.read(lexeme_id)) is not None
        ]
    else:
        entries = list(store.iter_entries())

    report = AuditReport(entries_total=len(entries), core_restricted=core_ids is not None)
    for entry in entries:
        _audit_entry(report, entry, core_ids=core_ids, store=store)

    graph = _build_hypernym_graph(entries)
    report.hypernym_self_loops = _count_hypernym_self_loops(graph)
    report.hypernym_cycle_count, report.hypernym_cycle_examples = _find_hypernym_cycles(graph)
    report.reciprocity = _audit_reciprocity(entries)

    return report
