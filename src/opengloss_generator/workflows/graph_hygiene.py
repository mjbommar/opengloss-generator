"""Workflow 6 — make the hypernym graph a hierarchy again, deterministically and for $0.

``audit.py`` measures two graph defects (D-40) and writes nothing. This workflow is the
repair side of the same two measurements, and it makes **no model calls at all**: every
decision here is a function of relation types, resolved sense ids, and confidences that
are already on disk. ``runner`` is accepted only so the signature stays parallel to
:func:`~opengloss_generator.workflows.retrofit.run_retrofit`; it is never used, and
``None`` is the expected value.

What is wrong with the graph (``docs/CORE-DIARY.md`` Iteration 6, defect 1, measured on
the 10K core store over resolved relations only): 58,292 hypernym edges carry 40
same-lexeme self-loops, 458 mutual pairs where ``A`` and ``B`` each claim the other as
their hypernym (mean confidence 0.87 in *both* directions — ``resource ↔ supply``,
``explanation ↔ reasoning``: sibling terms the model could not order), 134 cyclic
components of size 2, 41 of size 3, about 25 of sizes 4-11, and one tangled component of
2,840 senses. Separately, ``synonym`` is reciprocated 24% of the time and ``antonym``
13%, although both types are symmetric by definition — if ``vow`` calls ``promise`` a
synonym then ``promise`` is a synonym of ``vow``, whether or not anyone wrote it down.

Four steps, in this order, all on the whole store's *resolved* relations::

1. self-loops        hypernym/hyponym whose target sense belongs to the same lexeme
2. mutual hypernymy  A → B and B → A, however the two edges were asserted
3. remaining cycles  Tarjan SCCs, then a greedy feedback-arc removal inside each
4. reciprocity       add the implied reverse of a symmetric relation

Nothing is ever deleted (D-1's spirit: identifiers are positional and information is
tombstoned, not removed). A defective hypernym is **demoted** to a weaker relation type
that says something still true — ``see_also`` for a self-loop or a cycle-breaking back
edge, ``synonym`` for a mutual pair, which is what a mutual hypernym claim actually
means — and the reason is written to ``Relation.note``. The demoted types are outside
:func:`~opengloss_generator.audit._build_hypernym_graph`'s projection, which is what
makes the pass idempotent: a second sweep rebuilds the graph, finds the demoted edges
gone from it, and plans nothing.

Step 2's one sharp edge: an assertion whose sense *already* carries a ``synonym``
relation toward the same lexeme cannot become a second one — that would be a duplicate
edge under the same ``edge_id``. Such an assertion is demoted to ``see_also`` instead of
being dropped outright, so the D-1 rule ("nothing is lost") holds for it too; the note
says which case it was.

The cycle breaker
-----------------

Only an edge *internal to a non-trivial strongly connected component* can lie on a cycle:
if ``u -> v`` and both are in the same component then a path ``v ~> u`` exists, and if
they are not, no cycle passes through the edge at all. So step 3 needs no cycle
enumeration — it needs the components. An iterative Tarjan finds them (explicit stack: at
core-list scale the graph holds ~40K sense nodes, far past a safe Python recursion depth,
exactly as ``audit._find_hypernym_cycles`` argues), and each component's internal edges
are then offered *best-first* to :class:`_Ordering`, a topological order maintained
incrementally by Pearce and Kelly's dynamic-topological-sort algorithm. An edge that fits
the order is kept; one that would close a cycle against the edges already kept is refused,
and refusing it is the removal.

That is the greedy minimum feedback arc set, stated the other way round: the refused edge
is always the *last* edge of its cycle in best-first order, which is to say the worst one
on it. The direct phrasing — find a cycle, remove its lowest-confidence edge, repeat — is
the same answer computed at ``O(k · (V + E))`` for ``k`` removals, and that is not
affordable: measured on a synthetic 2,840-node component with 10,506 internal edges it
took 32 s and removed 7,084 edges, against 0.09 s and 1,708 edges for the incremental
order, which touches only the region of the order an edge actually disturbs.

"Best" is a total order, so no choice ever depends on dictionary or set iteration order:
edges are ranked by ``(confidence, -out_degree(source), source_sense_id,
target_sense_id)`` and removed lowest-first — least confident first, then the source with
more outgoing hypernyms (it can afford to lose one), then lexicographically. An edge with
no confidence at all is scored :data:`_UNSCORED_CONFIDENCE` (``1.0``), so a hand-written,
never-resolved edge is the last thing the breaker will take. Out-degree is measured once,
before the first removal, so it does not drift as edges come out from under it.

Concurrency and locking (D-31)
------------------------------

Loading is a single sequential pass over ``store.iter_entries()``, exactly as
``audit_store`` reads: the entries are projected into :class:`_RelationRef` rows and the
parsed :class:`~opengloss_generator.schema.Lexeme` objects are dropped, so a 10K-entry
store costs the relations, not the renditions. Nothing is held under a lock for that
pass, because nothing is written during it.

Applying is the pooled handler every other workflow uses: one entry per work item,
through :func:`~opengloss_generator.runner.run_pool`, with the entry re-read and written
inside one hold of its own lock::

    async with store.locked(lexeme_id):
        entry = store.read(lexeme_id)
        ...
        store.write(entry)

Because the plan was computed from a read taken *outside* that lock, each edit names the
relation it means by position **and** by content; a relation that has moved or changed
underneath the plan is re-located by content, and one that is gone entirely is skipped
rather than mis-applied. Counters go through :class:`_Tally`, which mutates them only
while holding an ``asyncio.Lock``, for the same reason ``retrofit.py``'s does.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from opengloss_generator.log import get_logger
from opengloss_generator.prompts import PROMPT_VERSION
from opengloss_generator.runner import run_pool
from opengloss_generator.schema import (
    Provenance,
    Relation,
    RelationTarget,
    RelationType,
    StageName,
)

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence

    from opengloss_generator.schema import Lexeme, Sense
    from opengloss_generator.stages import StageRunner
    from opengloss_generator.store import LexemeStore

__all__ = ["GraphHygieneOutcome", "run_graph_hygiene"]

_LOG = get_logger(__name__)

#: Provenance ``model`` for the three demotion steps, and for step 4's additions. Both
#: are rules, not models, and are named the way ``retrofit.DETERMINISTIC_MODEL`` is.
DEMOTION_MODEL = "rule:graph_hygiene"
RECIPROCITY_MODEL = "rule:reciprocity"

#: Relation types that hold in both directions by definition, and whose missing reverse
#: step 4 writes. Identical to ``audit._SYMMETRIC_RELATION_TYPES`` — the pass repairs
#: exactly what that check measures.
SYMMETRIC_RELATION_TYPES: tuple[RelationType, ...] = (
    RelationType.SYNONYM,
    RelationType.ANTONYM,
    RelationType.CONFUSABLE_WITH,
)

#: Note text prefixes, one per step. They are also the audit trail: every relation this
#: pass touched can be found again by its note.
SELF_LOOP_NOTE = "demoted: self-loop"
MUTUAL_NOTE = "demoted: mutual hypernym"
MUTUAL_DUPLICATE_NOTE = "demoted: mutual hypernym (synonym already present)"
CYCLE_NOTE = "demoted: cycle break"
RECIPROCAL_NOTE = "reciprocal of"

#: Score given to an edge whose ``RelationTarget.confidence`` is ``None``. The cycle
#: breaker removes the lowest-scoring edge first, so an unscored edge — hand-written, or
#: written before ``resolve`` recorded confidences — is the last thing it will take.
_UNSCORED_CONFIDENCE = 1.0

#: How many nodes a strongly connected component needs before it can hold a cycle. Every
#: node is its own trivial component; two mutually reachable ones are the smallest tangle.
_NON_TRIVIAL_SCC = 2

#: SCC size buckets the outcome reports edges-removed against. The core store's own
#: distribution (134 of size 2, 41 of 3, ~25 of 4-11, one of 2,840) is why the small
#: sizes get their own buckets and everything past a hundred shares one.
_SCC_BUCKETS: tuple[tuple[int, str], ...] = (
    (2, "2"),
    (3, "3"),
    (11, "4-11"),
    (99, "12-99"),
)
_LARGEST_SCC_BUCKET = "100+"

#: How often the apply phase logs its progress, in entries. Mirrors
#: ``retrofit.PROGRESS_EVERY`` so the two passes read the same in a run log.
PROGRESS_EVERY = 500


def _scc_bucket(size: int) -> str:
    """Return the reporting bucket for a strongly connected component of ``size`` nodes.

    Args:
        size: Number of sense nodes in the component.

    Returns:
        One of the labels in :data:`_SCC_BUCKETS`, or :data:`_LARGEST_SCC_BUCKET`.
    """
    for upper, label in _SCC_BUCKETS:
        if size <= upper:
            return label
    return _LARGEST_SCC_BUCKET


def _lexeme_of_sense(sid: str) -> str:
    """Return the lexeme id embedded in a sense id (``"lexeme:pos:index"``).

    Mirrors ``audit._lexeme_of_sense``; duplicated rather than imported so this workflow
    does not depend on a private name in a read-only measurement module.
    """
    return sid.rsplit(":", 2)[0]


# --------------------------------------------------------------------------------------
# The outcome
# --------------------------------------------------------------------------------------


@dataclass(slots=True)
class GraphHygieneOutcome:
    """What one graph-hygiene sweep planned, and what it wrote.

    In a ``dry_run`` every count is what *would* have been written; nothing reached disk.

    Attributes:
        entries_scanned: Entries read in the load pass.
        entries_changed: Entries with at least one edit applied (or planned, if dry).
        hypernym_edges: Size of the projected hypernym graph before any repair.
        self_loops_demoted: Step 1 — relations demoted to ``see_also``.
        mutual_demoted: Step 2 — relations demoted to ``synonym`` or ``see_also``.
        cycle_edges_demoted: Step 3 — relations demoted to ``see_also``.
        sccs_broken: Step 3 — how many non-trivial components were broken, per size
            bucket (see :func:`_scc_bucket`).
        cycle_edges_by_scc_size: Step 3 — graph edges removed, per size bucket. Larger
            than :attr:`cycle_edges_demoted` is impossible; smaller happens when one
            removed edge was asserted by two relations at once.
        reciprocal_added: Step 4 — relations added, per symmetric relation type.
        dry_run: Whether the plan was computed and discarded.
        stopped_reason: ``"stopped"`` when the caller's stop event ended the apply pass
            early, otherwise ``None``. A stopped sweep leaves a consistent store: every
            entry it did write, it wrote completely.
    """

    entries_scanned: int = 0
    entries_changed: int = 0
    hypernym_edges: int = 0
    self_loops_demoted: int = 0
    mutual_demoted: int = 0
    cycle_edges_demoted: int = 0
    sccs_broken: dict[str, int] = field(default_factory=dict)
    cycle_edges_by_scc_size: dict[str, int] = field(default_factory=dict)
    reciprocal_added: dict[str, int] = field(default_factory=dict)
    dry_run: bool = False
    stopped_reason: str | None = None

    @property
    def relations_demoted(self) -> int:
        """Return the total number of relations the three demotion steps rewrote."""
        return self.self_loops_demoted + self.mutual_demoted + self.cycle_edges_demoted

    @property
    def relations_added(self) -> int:
        """Return the total number of reciprocal relations step 4 wrote."""
        return sum(self.reciprocal_added.values())

    @property
    def changed(self) -> bool:
        """Return whether the sweep found anything at all to do."""
        return bool(self.relations_demoted or self.relations_added)

    def as_dict(self) -> dict[str, object]:
        """Return a JSON-able view for the run summary and the CLI."""
        return {
            "entries_scanned": self.entries_scanned,
            "entries_changed": self.entries_changed,
            "hypernym_edges": self.hypernym_edges,
            "self_loops_demoted": self.self_loops_demoted,
            "mutual_demoted": self.mutual_demoted,
            "cycle_edges_demoted": self.cycle_edges_demoted,
            "sccs_broken": dict(sorted(self.sccs_broken.items())),
            "cycle_edges_by_scc_size": dict(sorted(self.cycle_edges_by_scc_size.items())),
            "reciprocal_added": dict(sorted(self.reciprocal_added.items())),
            "relations_demoted": self.relations_demoted,
            "relations_added": self.relations_added,
            "dry_run": self.dry_run,
            "stopped_reason": self.stopped_reason,
        }


# --------------------------------------------------------------------------------------
# The store projection
# --------------------------------------------------------------------------------------


@dataclass(slots=True)
class _RelationRef:
    """One relation, addressed well enough to find it again without holding the entry.

    ``type`` is mutated as the plan is built, so that step 4 sees the graph as steps 1-3
    will leave it: a mutual hypernym demoted to ``synonym`` is already reciprocated by
    its partner and must not also gain a reciprocal. ``original_type`` is what will be
    matched against the entry on disk when the edit is applied.
    """

    lexeme_id: str
    sense_id: str
    index: int
    type: RelationType
    original_type: RelationType
    term: str
    target_lexeme: str
    target_sense: str | None
    confidence: float | None
    note: str | None


@dataclass(slots=True)
class _StoreView:
    """Every non-retired sense's relations, projected out of the entries.

    Attributes:
        headwords: ``lexeme_id -> headword``, for building a reciprocal's target term.
        relations: ``sense_id -> relations``, in the order they appear on the sense. A
            sense with no relations is still present, with an empty list, so that "does
            this sense exist, and is it live?" is one lookup.
        entries_scanned: How many entries the load pass read.
    """

    headwords: dict[str, str] = field(default_factory=dict)
    relations: dict[str, list[_RelationRef]] = field(default_factory=dict)
    entries_scanned: int = 0

    def iter_refs(self) -> Iterator[_RelationRef]:
        """Yield every projected relation, in a fixed order independent of load order."""
        for sense_id in sorted(self.relations):
            yield from self.relations[sense_id]


def _load_view(store: LexemeStore) -> _StoreView:
    """Project every entry's non-retired relations into a :class:`_StoreView`.

    The parsed entries are dropped as they are read: only the relation rows survive, so
    the working set is the graph rather than the whole store.

    Args:
        store: The store to read. Never written by this function, and read without
            locks — the same discipline ``audit_store`` uses.

    Returns:
        The projection.
    """
    view = _StoreView()
    for entry in store.iter_entries():
        view.entries_scanned += 1
        view.headwords[entry.lexeme_id] = entry.headword
        for _, sense, sid in entry.iter_senses():
            if sense.retired:
                continue
            view.relations[sid] = [
                _RelationRef(
                    lexeme_id=entry.lexeme_id,
                    sense_id=sid,
                    index=index,
                    type=relation.type,
                    original_type=relation.type,
                    term=relation.target.term,
                    target_lexeme=relation.target.lexeme_id,
                    target_sense=relation.target.sense_id,
                    confidence=relation.target.confidence,
                    note=relation.note,
                )
                for index, relation in enumerate(sense.relations)
            ]
    return view


# --------------------------------------------------------------------------------------
# The hypernym graph
# --------------------------------------------------------------------------------------


@dataclass(slots=True)
class _HypernymGraph:
    """The resolved hypernym digraph, with each edge's asserting relations kept.

    ``audit._build_hypernym_graph`` throws the assertions away, because a measurement
    only needs the shape. A repair needs to know *which relation* to rewrite, and one
    edge can be asserted twice — once as ``A hypernym B`` and once as ``B hyponym A`` —
    so both have to come out together or the edge survives its own removal.

    Attributes:
        successors: ``sense_id -> sorted successor sense ids``. Sorted lists, not sets:
            string hashing is randomised per process, so set iteration order would make
            the whole pass non-reproducible between runs.
        assertions: ``(source, target) -> the relations that asserted that edge``.
    """

    successors: dict[str, list[str]] = field(default_factory=dict)
    assertions: dict[tuple[str, str], list[_RelationRef]] = field(default_factory=dict)

    @property
    def edge_count(self) -> int:
        """Return the number of distinct edges."""
        return len(self.assertions)

    def confidence(self, edge: tuple[str, str]) -> float:
        """Return an edge's confidence: the most confident assertion that made it.

        An edge asserted twice is as well-evidenced as its better assertion, so the
        cycle breaker weighs it that way rather than punishing it for the weaker one.

        Args:
            edge: The ``(source, target)`` pair.

        Returns:
            A confidence in ``[0, 1]``; :data:`_UNSCORED_CONFIDENCE` when no assertion
            carries one.
        """
        return max(
            (
                _UNSCORED_CONFIDENCE if ref.confidence is None else ref.confidence
                for ref in self.assertions[edge]
            ),
            default=_UNSCORED_CONFIDENCE,
        )

    def drop(self, edge: tuple[str, str]) -> list[_RelationRef]:
        """Remove an edge from the graph and return the relations that asserted it.

        Args:
            edge: The ``(source, target)`` pair.

        Returns:
            The asserting relations, or an empty list if the edge was already gone.
        """
        refs = self.assertions.pop(edge, [])
        source, target = edge
        neighbours = self.successors.get(source)
        if neighbours is not None and target in neighbours:
            neighbours.remove(target)
        return refs


def _build_graph(view: _StoreView) -> _HypernymGraph:
    """Project resolved hypernym/hyponym relations onto one sense-to-sense graph.

    The projection is ``audit._build_hypernym_graph``'s, edge for edge: a resolved
    ``hypernym`` relation contributes the edge in the direction it already points
    (specific → general), a resolved ``hyponym`` relation contributes the reverse (``A``
    calling ``B`` its hyponym means ``B`` is the more specific of the two), and an
    unresolved target contributes nothing at all, since it cannot close a cycle.

    Args:
        view: The store projection.

    Returns:
        The graph, with every edge's asserting relations attached.
    """
    graph = _HypernymGraph()
    edges: dict[tuple[str, str], list[_RelationRef]] = {}
    for ref in view.iter_refs():
        if ref.target_sense is None:
            continue
        if ref.type is RelationType.HYPERNYM:
            edge = (ref.sense_id, ref.target_sense)
        elif ref.type is RelationType.HYPONYM:
            edge = (ref.target_sense, ref.sense_id)
        else:
            continue
        edges.setdefault(edge, []).append(ref)

    graph.assertions = edges
    successors: dict[str, set[str]] = {}
    for source, target in edges:
        successors.setdefault(source, set()).add(target)
        successors.setdefault(target, set())
    graph.successors = {node: sorted(targets) for node, targets in sorted(successors.items())}
    return graph


def _tarjan_scc(nodes: Sequence[str], successors: dict[str, list[str]]) -> list[list[str]]:
    """Return the strongly connected components of a digraph, iteratively.

    An explicit ``(node, neighbour iterator)`` stack rather than recursion, for the
    reason ``audit._find_hypernym_cycles`` gives: the graph holds on the order of 40K
    sense nodes at core-list scale, well past a safe Python recursion depth.

    Args:
        nodes: The nodes to consider, in the order roots should be tried. Determines the
            output order, so pass a sorted sequence for a reproducible result.
        successors: Adjacency; a node absent from it has no successors.

    Returns:
        One list of sense ids per component, each component in Tarjan's own (reverse
        topological) discovery order.
    """
    index_of: dict[str, int] = {}
    low: dict[str, int] = {}
    on_stack: set[str] = set()
    stack: list[str] = []
    components: list[list[str]] = []
    counter = 0

    for root in nodes:
        if root in index_of:
            continue
        index_of[root] = low[root] = counter
        counter += 1
        stack.append(root)
        on_stack.add(root)
        work: list[tuple[str, Iterator[str]]] = [(root, iter(successors.get(root, ())))]
        while work:
            node, neighbours = work[-1]
            neighbour = next(neighbours, None)
            if neighbour is not None:
                if neighbour not in index_of:
                    index_of[neighbour] = low[neighbour] = counter
                    counter += 1
                    stack.append(neighbour)
                    on_stack.add(neighbour)
                    work.append((neighbour, iter(successors.get(neighbour, ()))))
                elif neighbour in on_stack:
                    low[node] = min(low[node], index_of[neighbour])
                continue
            work.pop()
            if work:
                parent = work[-1][0]
                low[parent] = min(low[parent], low[node])
            if low[node] == index_of[node]:
                component: list[str] = []
                while True:
                    member = stack.pop()
                    on_stack.discard(member)
                    component.append(member)
                    if member == node:
                        break
                components.append(component)

    return components


class _Ordering:
    """A topological order maintained as edges are added, one at a time.

    Pearce and Kelly's dynamic topological sort (*A Dynamic Topological Sort Algorithm
    for Directed Acyclic Graphs*, ACM JEA 11, 2006). Every node holds a position, and
    the invariant is that an accepted edge always runs from a lower position to a higher
    one. Adding an edge that already runs that way costs nothing. Adding one that runs
    backwards searches only the *affected region* — the descendants of the target that
    sit before the source, and the ancestors of the source that sit after the target —
    and re-packs exactly those nodes into the positions they already occupied between
    them. If the forward search reaches the source, the edge would close a cycle and is
    refused instead.

    That refusal is the whole point here: fed the edges of one strongly connected
    component best-first, :meth:`add` accepts every edge that can be kept and refuses
    exactly those that close a cycle against edges already kept — which is the greedy
    minimum feedback arc set, since the refused edge is always the worst edge on the
    cycle it closes. It is also what makes the pass affordable: the naive form of the
    same greedy (find a cycle, remove its worst edge, repeat) re-walks the whole
    component per removal, and measured 32 s on a synthetic 2,840-node component where
    this runs in 0.09 s -- and removes 1,708 of the 10,506 edges where the naive form
    removed 7,084, so it is the better feedback arc set as well as the affordable one.

    Args:
        nodes: The component's nodes. The initial order is the order given, so pass a
            sorted sequence for a reproducible result.
    """

    def __init__(self, nodes: Sequence[str]) -> None:
        """Seed every node with a distinct position and no edges."""
        self._ord: dict[str, int] = {node: position for position, node in enumerate(nodes)}
        self._succ: dict[str, list[str]] = {node: [] for node in nodes}
        self._pred: dict[str, list[str]] = {node: [] for node in nodes}

    def add(self, source: str, target: str) -> bool:
        """Accept ``source -> target`` unless it would close a cycle.

        Args:
            source: The edge's tail.
            target: The edge's head.

        Returns:
            ``True`` if the edge was accepted and the order updated, ``False`` if it
            would have closed a cycle and was refused.
        """
        lower, upper = self._ord[target], self._ord[source]
        if upper < lower:
            self._link(source, target)
            return True
        if upper == lower:
            return False
        forward = self._descendants_before(target, upper)
        if forward is None:
            return False
        self._shift(self._ancestors_after(source, lower), forward)
        self._link(source, target)
        return True

    def _link(self, source: str, target: str) -> None:
        """Record an accepted edge in both adjacency directions."""
        self._succ[source].append(target)
        self._pred[target].append(source)

    def _descendants_before(self, start: str, upper: int) -> list[str] | None:
        """Return ``start``'s descendants positioned before ``upper``, or ``None``.

        Args:
            start: The head of the edge being added.
            upper: The tail's position; a node found exactly there *is* the tail, which
                means the edge closes a cycle.

        Returns:
            The affected descendants, ``start`` included, or ``None`` on a cycle.
        """
        seen = [start]
        visited = {start}
        stack = [start]
        while stack:
            for node in self._succ[stack.pop()]:
                if node in visited:
                    continue
                position = self._ord[node]
                if position == upper:
                    return None
                if position < upper:
                    visited.add(node)
                    seen.append(node)
                    stack.append(node)
        return seen

    def _ancestors_after(self, start: str, lower: int) -> list[str]:
        """Return ``start``'s ancestors positioned after ``lower``, ``start`` included."""
        seen = [start]
        visited = {start}
        stack = [start]
        while stack:
            for node in self._pred[stack.pop()]:
                if node not in visited and self._ord[node] > lower:
                    visited.add(node)
                    seen.append(node)
                    stack.append(node)
        return seen

    def _shift(self, ancestors: list[str], descendants: list[str]) -> None:
        """Re-pack the affected region so every ancestor precedes every descendant.

        The two sets are disjoint — a shared node would mean a path from the new edge's
        head back to its tail, which :meth:`_descendants_before` already refused — so
        the positions they occupy can simply be pooled and handed out in order.

        Args:
            ancestors: The tail's affected ancestors.
            descendants: The head's affected descendants.
        """
        moved = sorted(ancestors, key=self._ord.__getitem__)
        moved += sorted(descendants, key=self._ord.__getitem__)
        for node, position in zip(moved, sorted(self._ord[node] for node in moved), strict=True):
            self._ord[node] = position


def _break_cycles(
    graph: _HypernymGraph, *, out_degree: dict[str, int]
) -> tuple[list[list[_RelationRef]], dict[str, int], dict[str, int]]:
    """Remove a feedback arc set from every non-trivial component, greedily.

    Only edges *internal to a non-trivial strongly connected component* can lie on a
    cycle, so those are the only ones considered: an edge between two components, or out
    of one, is never a candidate however low its confidence. Each component's internal
    edges are sorted best-first and offered to a :class:`_Ordering`, which keeps what it
    can and refuses what would close a cycle — see that class for why this is the greedy
    minimum feedback arc set and why it is fast.

    Args:
        graph: The graph, mutated in place as refused edges come out.
        out_degree: Each node's outgoing edge count, measured before the first removal.

    Returns:
        The relations asserting each removed edge, in removal order; the number of
        components broken per size bucket; and the number of edges removed per bucket.
    """
    removed: list[list[_RelationRef]] = []
    broken: dict[str, int] = {}
    per_bucket: dict[str, int] = {}

    for component in _tarjan_scc(sorted(graph.successors), graph.successors):
        if len(component) < _NON_TRIVIAL_SCC:
            continue
        bucket = _scc_bucket(len(component))
        broken[bucket] = broken.get(bucket, 0) + 1
        members = sorted(component)
        inside = set(members)
        internal = [
            (source, target)
            for source in members
            for target in graph.successors[source]
            if target in inside
        ]
        internal.sort(
            key=lambda edge: (
                graph.confidence(edge),
                -out_degree.get(edge[0], 0),
                edge[0],
                edge[1],
            ),
            reverse=True,
        )
        ordering = _Ordering(members)
        for edge in internal:
            if ordering.add(*edge):
                continue
            removed.append(graph.drop(edge))
            per_bucket[bucket] = per_bucket.get(bucket, 0) + 1

    return removed, broken, per_bucket


# --------------------------------------------------------------------------------------
# The edit plan
# --------------------------------------------------------------------------------------


@dataclass(slots=True)
class _Demotion:
    """One relation to retype in place, addressed by position and by content."""

    sense_id: str
    index: int
    original_type: RelationType
    term: str
    target_sense: str | None
    new_type: RelationType
    note: str


@dataclass(slots=True)
class _Addition:
    """One relation to append to a sense."""

    sense_id: str
    type: RelationType
    term: str
    target_sense: str
    confidence: float | None
    note: str


@dataclass(slots=True)
class _EntryPlan:
    """Every edit destined for one entry."""

    demotions: list[_Demotion] = field(default_factory=list)
    additions: list[_Addition] = field(default_factory=list)

    @property
    def edits(self) -> int:
        """Return how many relations this plan touches."""
        return len(self.demotions) + len(self.additions)


@dataclass(slots=True)
class _Plan:
    """The whole store's edit plan, with the counts the outcome reports."""

    entries: dict[str, _EntryPlan] = field(default_factory=dict)
    self_loops: int = 0
    mutual: int = 0
    cycle_edges: int = 0
    sccs_broken: dict[str, int] = field(default_factory=dict)
    cycle_edges_by_scc_size: dict[str, int] = field(default_factory=dict)
    reciprocal_added: dict[str, int] = field(default_factory=dict)
    hypernym_edges: int = 0

    def for_entry(self, lexeme_id: str) -> _EntryPlan:
        """Return (creating if needed) the plan for one entry."""
        return self.entries.setdefault(lexeme_id, _EntryPlan())


def _demote(plan: _Plan, ref: _RelationRef, new_type: RelationType, note: str) -> None:
    """Record a demotion, and mutate the projection so later steps see the new type.

    Args:
        plan: The plan to append to.
        ref: The relation to retype. Its ``type`` is updated in place; its
            ``original_type`` is left alone, since that is what the on-disk relation is
            matched against.
        new_type: The type to demote to.
        note: The reason, written to ``Relation.note``.
    """
    plan.for_entry(ref.lexeme_id).demotions.append(
        _Demotion(
            sense_id=ref.sense_id,
            index=ref.index,
            original_type=ref.original_type,
            term=ref.term,
            target_sense=ref.target_sense,
            new_type=new_type,
            note=note,
        )
    )
    ref.type = new_type


def _asserted_pairs(view: _StoreView) -> set[tuple[str, RelationType, str]]:
    """Return every ``(lexeme, type, target lexeme)`` triple the store already asserts.

    Entry-level, not sense-level, mirroring ``audit._relation_targets_lexeme``: the
    reciprocity question is whether the *other side* made the matching claim anywhere in
    its own senses, not whether one particular sense did.

    Args:
        view: The store projection, after steps 1-3 have retyped what they will.

    Returns:
        The set of triples.
    """
    pairs: set[tuple[str, RelationType, str]] = set()
    for ref in view.iter_refs():
        pairs.add((ref.lexeme_id, ref.type, ref.target_lexeme))
        # A pair that a hygiene pass demoted (note "demoted: ...") must not be re-created
        # from the far side as a reciprocal of any symmetric type: the demotion was a
        # judgement about the pair, and this step only completes assertions, never
        # overrules them (see relation_hygiene's "Run order" note).
        if ref.type is RelationType.SEE_ALSO and (ref.note or "").startswith("demoted:"):
            for symmetric in SYMMETRIC_RELATION_TYPES:
                pairs.add((ref.lexeme_id, symmetric, ref.target_lexeme))
    return pairs


def _synonym_targets(view: _StoreView) -> dict[str, set[str]]:
    """Return, per sense, the lexemes it already asserts a ``synonym`` relation toward.

    Step 2 consults and extends this so that no sense ends up with two ``synonym``
    relations toward the same lexeme — which would be two edges under one ``edge_id``.

    Args:
        view: The store projection.

    Returns:
        ``sense_id -> target lexeme ids``.
    """
    targets: dict[str, set[str]] = {}
    for ref in view.iter_refs():
        if ref.type is RelationType.SYNONYM:
            targets.setdefault(ref.sense_id, set()).add(ref.target_lexeme)
    return targets


def _plan_self_loops(plan: _Plan, graph: _HypernymGraph) -> None:
    """Step 1 — demote every same-lexeme hypernym edge to ``see_also``.

    Nothing is its own hypernym, whichever of its senses does the pointing and whichever
    of the two relation types said so (D-40). Both ends of such an edge are on the same
    entry, so this can never contradict another entry's view of it.

    Args:
        plan: The plan to extend.
        graph: The graph, mutated: the offending edges come out of it, so steps 2 and 3
            never see them.
    """
    for edge in sorted(graph.assertions):
        source, target = edge
        if _lexeme_of_sense(source) != _lexeme_of_sense(target):
            continue
        for ref in graph.drop(edge):
            _demote(plan, ref, RelationType.SEE_ALSO, SELF_LOOP_NOTE)
            plan.self_loops += 1


def _plan_mutual(plan: _Plan, graph: _HypernymGraph, synonyms: dict[str, set[str]]) -> None:
    """Step 2 — turn each ``A → B`` / ``B → A`` hypernym pair into a synonym pair.

    458 such pairs exist on the core store at mean confidence 0.87 in both directions
    (``docs/CORE-DIARY.md`` Iteration 6): these are not a hierarchy the model got
    backwards, they are sibling terms it could not order, and ``synonym`` is what the two
    claims together actually say. An assertion whose sense already asserts ``synonym``
    toward the same lexeme becomes ``see_also`` instead — see the module docstring.

    Args:
        plan: The plan to extend.
        graph: The graph, mutated: both edges of every mutual pair come out.
        synonyms: ``sense_id -> synonym target lexemes``, extended as pairs are planned.
    """
    for edge in sorted(graph.assertions):
        source, target = edge
        reverse = (target, source)
        if reverse not in graph.assertions:
            continue
        for pair in (edge, reverse):
            for ref in graph.drop(pair):
                existing = synonyms.setdefault(ref.sense_id, set())
                if ref.target_lexeme in existing:
                    _demote(plan, ref, RelationType.SEE_ALSO, MUTUAL_DUPLICATE_NOTE)
                else:
                    existing.add(ref.target_lexeme)
                    _demote(plan, ref, RelationType.SYNONYM, MUTUAL_NOTE)
                plan.mutual += 1


def _plan_cycles(plan: _Plan, graph: _HypernymGraph) -> None:
    """Step 3 — demote a feedback arc set of every remaining cycle to ``see_also``.

    Args:
        plan: The plan to extend.
        graph: The graph, mutated by :func:`_break_cycles`. It is acyclic afterwards.
    """
    out_degree = {node: len(targets) for node, targets in graph.successors.items()}
    removed, broken, per_bucket = _break_cycles(graph, out_degree=out_degree)
    plan.sccs_broken = broken
    plan.cycle_edges_by_scc_size = per_bucket
    for refs in removed:
        for ref in refs:
            scored = _UNSCORED_CONFIDENCE if ref.confidence is None else ref.confidence
            _demote(plan, ref, RelationType.SEE_ALSO, f"{CYCLE_NOTE} (conf={scored:.2f})")
            plan.cycle_edges += 1


def _plan_reciprocity(plan: _Plan, view: _StoreView) -> None:
    """Step 4 — add the implied reverse of every one-sided symmetric relation.

    For a resolved relation ``A -> B`` of a symmetric type whose target entry asserts
    nothing of that type back toward ``A``'s lexeme, the reverse relation is written on
    ``B``'s own sense: the source entry's headword as the term, ``A``'s sense id already
    filled in (the reverse of a resolved relation needs no resolution of its own), and
    the same confidence, since it is the same claim read the other way round. A
    ``confusable_with`` reverse carries the original note as well as its own, because
    that type's note *is* its content and the schema requires one.

    Args:
        plan: The plan to extend.
        view: The store projection, with steps 1-3's retyping already applied.
    """
    asserted = _asserted_pairs(view)
    for ref in view.iter_refs():
        if ref.type not in SYMMETRIC_RELATION_TYPES or ref.target_sense is None:
            continue
        if ref.target_lexeme == ref.lexeme_id:
            continue
        if ref.target_lexeme not in view.headwords or ref.target_sense not in view.relations:
            continue
        triple = (ref.target_lexeme, ref.type, ref.lexeme_id)
        if triple in asserted:
            continue
        asserted.add(triple)
        note = f"{RECIPROCAL_NOTE} {ref.sense_id}"
        if ref.type is RelationType.CONFUSABLE_WITH and ref.note:
            note = f"{note}: {ref.note}"
        plan.for_entry(ref.target_lexeme).additions.append(
            _Addition(
                sense_id=ref.target_sense,
                type=ref.type,
                term=view.headwords[ref.lexeme_id],
                target_sense=ref.sense_id,
                confidence=ref.confidence,
                note=note,
            )
        )
        plan.reciprocal_added[ref.type.value] = plan.reciprocal_added.get(ref.type.value, 0) + 1


def _build_plan(view: _StoreView) -> _Plan:
    """Run all four steps over the projection and return the edit plan.

    Args:
        view: The store projection. Its :class:`_RelationRef` rows are retyped in place
            as steps 1-3 decide, so step 4 sees the graph as it will be left.

    Returns:
        The plan, keyed by the entry each edit belongs to.
    """
    plan = _Plan()
    graph = _build_graph(view)
    plan.hypernym_edges = graph.edge_count
    _plan_self_loops(plan, graph)
    _plan_mutual(plan, graph, _synonym_targets(view))
    _plan_cycles(plan, graph)
    _plan_reciprocity(plan, view)
    return plan


# --------------------------------------------------------------------------------------
# Applying the plan
# --------------------------------------------------------------------------------------


def _provenance(model: str) -> Provenance:
    """Return the zero-cost provenance record this pass stamps an edit with.

    Args:
        model: :data:`DEMOTION_MODEL` or :data:`RECIPROCITY_MODEL` — a rule name, since
            no model was called.

    Returns:
        A :class:`~opengloss_generator.schema.Provenance` with every cost and token
        field at zero, so a naive sum over an entry's provenance table is unaffected by
        this pass having run.
    """
    return Provenance(
        stage=StageName.HYGIENE,
        model=model,
        prompt_version=PROMPT_VERSION,
        cost_usd=0.0,
        attempts=0,
    )


def _locate(sense: Sense, demotion: _Demotion) -> Relation | None:
    """Find the relation a demotion names, by position first and by content second.

    The plan was computed from a read taken outside the entry's lock, so the relation may
    have moved (or gone) since. The position is a hint; the content — original type,
    term, and resolved sense — is the identity.

    Args:
        sense: The sense the relation should be on.
        demotion: The planned edit.

    Returns:
        The relation, or ``None`` if nothing on the sense still matches.
    """

    def matches(relation: Relation) -> bool:
        return (
            relation.type is demotion.original_type
            and relation.target.term == demotion.term
            and relation.target.sense_id == demotion.target_sense
        )

    if 0 <= demotion.index < len(sense.relations) and matches(sense.relations[demotion.index]):
        return sense.relations[demotion.index]
    return next((relation for relation in sense.relations if matches(relation)), None)


def _apply_plan(entry: Lexeme, plan: _EntryPlan) -> int:
    """Apply one entry's planned edits in place.

    Args:
        entry: The freshly-read entry, mutated.
        plan: Its planned edits.

    Returns:
        How many edits were actually applied. Zero means the entry must not be written:
        every edit was aimed at something no longer there.
    """
    senses = {sid: sense for _, sense, sid in entry.iter_senses()}
    applied = 0
    provenance_ids: dict[str, str] = {}

    def provenance_id(model: str) -> str:
        """Return the id of this entry's record for ``model``, adding it on first use."""
        if model not in provenance_ids:
            provenance_ids[model] = entry.add_provenance(_provenance(model))
        return provenance_ids[model]

    for demotion in plan.demotions:
        sense = senses.get(demotion.sense_id)
        if sense is None:
            continue
        relation = _locate(sense, demotion)
        if relation is None:
            continue
        relation.type = demotion.new_type
        relation.note = (
            demotion.note if relation.note is None else f"{demotion.note} | {relation.note}"
        )
        relation.provenance_id = provenance_id(DEMOTION_MODEL)
        applied += 1

    for addition in plan.additions:
        sense = senses.get(addition.sense_id)
        if sense is None:
            continue
        sense.relations.append(
            Relation(
                type=addition.type,
                target=RelationTarget(
                    term=addition.term,
                    sense_id=addition.target_sense,
                    confidence=addition.confidence,
                ),
                note=addition.note,
                provenance_id=provenance_id(RECIPROCITY_MODEL),
            )
        )
        applied += 1

    return applied


class _Tally:
    """The apply pass's counters, mutated only under a lock.

    Single-threaded asyncio does make ``counter += 1`` atomic on its own, but these
    counters are touched by many handlers around awaits, and that is a property of the
    interpreter rather than of this code — the same argument ``retrofit._Tally`` makes.
    """

    def __init__(self) -> None:
        """Start an empty tally."""
        self._lock = asyncio.Lock()
        self.entries_changed = 0
        self.edits_applied = 0
        self._visited = 0

    async def record(self, *, edits: int) -> None:
        """Fold one visited entry into the counters.

        Args:
            edits: How many edits were applied to it; zero leaves it uncounted as
                changed.
        """
        async with self._lock:
            self._visited += 1
            if edits:
                self.entries_changed += 1
                self.edits_applied += edits
            if self._visited % PROGRESS_EVERY == 0:
                _LOG.info(
                    "graph_hygiene_progress",
                    entries_done=self._visited,
                    entries_changed=self.entries_changed,
                    edits_applied=self.edits_applied,
                )


async def _apply(
    store: LexemeStore,
    plan: _Plan,
    *,
    workers: int,
    stop_event: asyncio.Event | None,
) -> _Tally:
    """Write every planned edit, one entry at a time, through the bounded pool.

    Args:
        store: The store to write.
        plan: The edit plan.
        workers: Pool size.
        stop_event: When set, workers finish the entry in hand and stop pulling.

    Returns:
        The tally of what was written.
    """
    tally = _Tally()

    async def handle(lexeme_id: str) -> None:
        async with store.locked(lexeme_id):
            entry = store.read(lexeme_id)
            if entry is None:
                return
            edits = _apply_plan(entry, plan.entries[lexeme_id])
            if edits:
                store.write(entry)
        await tally.record(edits=edits)

    await run_pool(sorted(plan.entries), handle, workers=workers, stop_event=stop_event)
    return tally


async def run_graph_hygiene(
    store: LexemeStore,
    runner: StageRunner | None = None,
    *,
    workers: int,
    stop_event: asyncio.Event | None = None,
    dry_run: bool = False,
) -> GraphHygieneOutcome:
    """Repair the hypernym graph and complete the symmetric relations, for $0.

    Four deterministic steps over the whole store's resolved relations — self-loops,
    mutual hypernymy, remaining cycles, reciprocity — described in full in the module
    docstring. The store is read once, the plan is computed in memory, and only then is
    anything written, one entry at a time under its own lock.

    Running it twice is running it once: every demotion moves an edge out of the hypernym
    projection, and every addition is exactly what the reciprocity check looks for, so
    the second sweep's plan is empty.

    Args:
        store: The store to repair. Read in full, then written per entry.
        runner: Accepted for signature parity with
            :func:`~opengloss_generator.workflows.retrofit.run_retrofit` and never used:
            this workflow makes no model calls, so ``None`` is the expected value.
        workers: Pool size for the apply pass.
        stop_event: Shared stop event. A caller may set it to end the apply pass after
            the entries in hand; the outcome then reports ``stopped_reason="stopped"``.
        dry_run: Compute the plan and report it without writing anything.

    Returns:
        The :class:`GraphHygieneOutcome` for the sweep.
    """
    del runner  # No model call is made anywhere in this workflow.

    view = _load_view(store)
    plan = _build_plan(view)

    outcome = GraphHygieneOutcome(
        entries_scanned=view.entries_scanned,
        hypernym_edges=plan.hypernym_edges,
        self_loops_demoted=plan.self_loops,
        mutual_demoted=plan.mutual,
        cycle_edges_demoted=plan.cycle_edges,
        sccs_broken=plan.sccs_broken,
        cycle_edges_by_scc_size=plan.cycle_edges_by_scc_size,
        reciprocal_added=plan.reciprocal_added,
        dry_run=dry_run,
    )

    if dry_run:
        outcome.entries_changed = sum(1 for entry in plan.entries.values() if entry.edits)
    else:
        tally = await _apply(store, plan, workers=workers, stop_event=stop_event)
        outcome.entries_changed = tally.entries_changed
        if stop_event is not None and stop_event.is_set():
            outcome.stopped_reason = "stopped"

    _LOG.info("graph_hygiene_complete", workers=workers, **outcome.as_dict())
    return outcome
