"""Workflow 10 — relation reconcile: make the *list* say what the passes decided.

``workflows/relation_hygiene.py`` (D-50) judges whether an edge is *true*, and repairs
what it finds by **demoting** it to ``see_also`` with the reason on ``Relation.note`` —
never by deleting it (D-1's spirit, restated in D-31 and D-47's markers: this resource
throws nothing away). That was the right call for the judgement, and it left two defects
in the thing every downstream reader actually consumes, ``Sense.relations``.

**The judge still reads the demoted edges.** ``workflows/qa.py`` renders every relation
on a sense as ``type->term`` — every one, ``see_also`` included — and asks whether the
sense's relations are valid. A ``see_also`` carrying ``demoted: nano invalid`` is exactly
the edge a previous pass agreed was wrong, and it is still in the list the judge is shown.
The validity pass demoted ~430K edges across the core store and the judge went on marking
84% of senses as carrying an invalid relation, because from inside the prompt nothing had
changed. Nothing had: the demotion is a *note*, and the note is not rendered.

**The two sides of a symmetric pair disagree.** ``validity``'s verdicts are directional —
one call judges ``A --synonym--> B``, another judges ``B --synonym--> A``, and the two
answers regularly differ. Measured on the whole core store (41,886 entries), synonym
reciprocity fell 98.4% → 93.7% and antonym 99.7% → 96.9% after the validity passes: about
4,400 synonym edges are asserted on one side while the reverse was demoted on the other.
D-50's own far-side phase repairs the demotions *it* makes; it cannot repair a
disagreement between two verdicts it made deliberately.

Three steps, selectable by name through ``only=``, every one of them **free** — no model
call is made anywhere in this module — and every one idempotent:

``asymmetric``
    For each symmetric type (``synonym``, ``antonym``, ``confusable_with``): where a live
    sense of ``A`` holds a live typed edge ``A -> B`` resolved to a sense ``T`` of ``B``,
    and ``B`` holds the reverse **already demoted** (a ``see_also`` toward ``A`` carrying
    one of :data:`DEMOTION_NOTE_PREFIXES`) with no live edge of that type back toward
    ``A`` anywhere in its own live senses, the stricter of the two directional verdicts
    wins and the near side is demoted too, with the note ``reconcile:asymmetric:<T>``.
    Counted per relation type. The pairing is entry-level rather than sense-level, for the
    reason :func:`_collect_demoted_pairs` gives and the reason
    ``graph_hygiene._asserted_pairs`` gives.

``tombstone``
    Removes from ``Sense.relations`` every ``see_also`` edge that carries a demotion note
    — one that was *not authored* as a ``see_also`` but arrived there through a hygiene
    pass. Each removed edge is written to a provenance record on its own sense, one line
    per edge, so nothing is lost and every D-1 edge id can be reconstructed from the
    record. An originally-authored ``see_also`` (no demotion note) stays: it is content,
    not a tombstone. **This is the step that actually shortens the list the judge reads.**

``cap``
    Per sense, per relation type, keeps at most :class:`RelationCaps`' allowance and
    tombstones the overflow the same way ``tombstone`` does. The measured store has a
    mean of 13.4 relations per sense (median 10, p90 22) with no ceiling anywhere in the
    schema, and a sense listing eleven synonyms is not eleven times as useful as one
    listing five — it is a longer prompt and a longer read. The keep order is: **resolved
    targets before unresolved** (a resolved target is a real entry and the only kind that
    can carry a reciprocal), then **edges the ``validity`` step accepted before
    never-judged ones**, then original document order.

Nothing is deleted in the sense D-1 forbids
-------------------------------------------

``tombstone`` and ``cap`` remove list entries, which is a stronger edit than anything
``relation_hygiene`` does, so both write the removal down. One provenance record per
(sense, step) carries a header line naming the sense and then one line per removed edge::

    reconcile:tombstone abseil:verb:0
    reconcile:tombstone: see_also -> banners [demoted: inflection of headword]
    reconcile:tombstone: see_also -> flags [retyped: nano synonym→see_also]

    reconcile:cap abseil:verb:0
    reconcile:cap:synonym -> rappel [-]

The type written is the type the edge carried **when it was removed**, because that is
what :func:`~opengloss_generator.identity.edge_id` is built from and therefore what a
reader needs to reconstruct the edge id; the bracketed note is the edge's own note, which
is where the pre-demotion history already lives (``retyped: nano synonym→see_also`` names
the type it came from; a ``demoted: ...`` note names the reason but not the type — that
information was already gone before this pass ran, and this pass does not invent it).
The record that originally authored the edge is untouched: provenance ids are never
reused or removed, so the edge's ``provenance_id`` still resolves.

``Lexeme.contrasts`` are keyed by edge id and are deliberately *not* cross-checked
against the entry's live edges (D-62, :class:`~opengloss_generator.schema.Contrast`): "a
contrast whose edge has gone is evidence about a removed relation rather than a
validation error". A contrast for a tombstoned edge therefore survives this pass intact,
which is the behaviour that schema note was written for.

Reciprocity, and ``graph_hygiene``
----------------------------------

The pass must not create new one-sidedness. Two places could:

1. ``graph_hygiene`` step 4 re-adding what this pass removed. Its
   :func:`~opengloss_generator.workflows.graph_hygiene._asserted_pairs` blocks a pair
   whose ``see_also`` note starts with ``"demoted:"`` from being re-created as a symmetric
   reciprocal — and ``tombstone`` deletes exactly those notes along with their edges. That
   is safe **only because ``asymmetric`` runs first**: after a full sweep neither side of
   such a pair asserts anything symmetric, and step 4 only ever infers *from* a live
   symmetric edge. Running ``--only tombstone`` on its own would remove B's tombstone
   while A's live synonym still stands, and the next ``graph-hygiene`` would write B's
   edge straight back. Selecting ``tombstone`` without ``asymmetric`` therefore logs a
   warning; the steps' fixed order (:attr:`RelationReconcileStep.ALL`) makes the default
   run safe whatever order ``--only`` lists them in.
2. ``cap`` dropping one half of a live reciprocated pair. The keep order puts resolved
   targets first precisely so this is rare, but a sense with more resolved synonyms than
   its cap will still lose one. Once a whole entry has been capped, every symmetric,
   resolved pair it no longer asserts **anywhere in its live senses** queues a
   :class:`_FarSideRemoval` (:func:`_far_side_removals`), and a second pooled phase — run
   once the main sweep has fully drained, so no two entry locks are ever held at once
   (D-31) — removes the reverse edges from the target entry, with the same provenance
   record.

**The far-side phase is given no stop event**, for D-50's second amendment's reason and
by the same mechanism: ``run_pool``'s workers return before pulling their first item once
the event is set, so a phase that honoured it would silently do nothing and leave the
store asserting one half of a pair this pass has just taken apart. It buys nothing (no
model call, one local read-modify-write per target) and is bounded by the removals the
run actually made, so there is nothing for a stop to save. :func:`_remove_far_side_all`
takes no ``stop_event`` parameter at all, so no future caller can reintroduce the bug by
passing one.

Idempotence, and the marker
---------------------------

All three steps are idempotent *by construction*, the way ``relation_hygiene``'s free
steps are — they leave nothing behind for themselves to find. A demoted edge is a
``see_also`` and ``asymmetric`` only looks at live typed ones; a tombstoned edge is gone;
a capped type is at or under its cap. Re-running the pass over an untouched store changes
nothing and reports zeroes.

The D-47-style marker on top of that is a *skip*, not the mechanism:
``relation_reconcile:<digest>`` on a zero-cost provenance record, where the digest covers
the selected step names together with the entry's live edge ids **as this run leaves
them**. Steps are in the digest because a marker written by ``--only tombstone`` must not
stop a later full sweep from capping the same entry. Written only on entries this pass
actually changed, so a store does not grow one provenance record per entry per sweep; an
unchanged entry is re-examined next time and, being idempotent, costs nothing but the
read.

The marker keys on the near side only, so an entry whose *far* side moved after this pass
ran — a later ``relation-hygiene`` sweep demoting the reverse of one of its synonyms — is
skipped by digest even though ``asymmetric`` now has something to say about it. That is
the same limitation ``relation_hygiene``'s validity marker has, and it has the same
answer: ``--from-list`` names the remainder without a whole-store re-sweep.

Concurrency and locking (D-31)
------------------------------

Unlike ``relation_hygiene``, which drives one pooled sweep per step, all three steps here
run inside **one** handler under **one** hold of the entry's lock. They are free, they are
ordered (``tombstone`` must see what ``asymmetric`` demoted; ``cap`` must count what
``tombstone`` removed), and three sweeps would mean three read-modify-write cycles per
entry to reach the same state. ``asymmetric``'s far-side *input* is collected up front by
:func:`_collect_demoted_pairs`, a read-only projection of the whole store taken without
locks — the discipline ``graph_hygiene._load_view`` and ``audit_store`` already use — and
never restricted by ``lexeme_ids``, because the far side of an edge on the list is very
often not on the list.

:class:`~opengloss_generator.workflows.content_hygiene.StepResult` is imported from
``content_hygiene``: it is that module's public surface and this pass reports the same
shape. The tally, the pool driver, the provenance helpers and the digest are module-private
there and in ``relation_hygiene``, so they are mirrored here; the note constants this pass
must recognise are public in ``relation_hygiene`` and are imported rather than restated, so
a new demotion note in that pass cannot silently stop being tombstoned here.
"""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from opengloss_generator.errors import BudgetExceededError
from opengloss_generator.log import get_logger
from opengloss_generator.prompts import PROMPT_VERSION
from opengloss_generator.runner import run_pool
from opengloss_generator.schema import Provenance, RelationType, StageName
from opengloss_generator.workflows.content_hygiene import PROGRESS_EVERY, StepResult
from opengloss_generator.workflows.relation_hygiene import (
    FAR_SIDE_NOTE_PREFIX,
    HEADWORD_INFLECTION_NOTE,
    HEADWORD_PHRASE_NOTE,
    META_LABEL_NOTE,
    NANO_INVALID_NOTE,
    NANO_RETYPE_NOTE,
    SIBLING_INFLECTION_NOTE,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Iterable, Sequence

    from opengloss_generator.schema import Lexeme, POSEntry, Relation, Sense
    from opengloss_generator.store import LexemeStore

__all__ = [
    "ASYMMETRIC_NOTE_PREFIX",
    "CAP_LINE_PREFIX",
    "CAP_RECORD_PREFIX",
    "DEMOTION_NOTE_PREFIXES",
    "MARKER_PREFIX",
    "TOMBSTONE_LINE_PREFIX",
    "TOMBSTONE_RECORD_PREFIX",
    "RelationCaps",
    "RelationReconcileOutcome",
    "RelationReconcileStep",
    "RelationReconcileStepResult",
    "is_demotion_note",
    "run_relation_reconcile",
]

_LOG = get_logger(__name__)

#: Provenance ``model`` for every edit this pass makes. A rule, not a model, named the
#: way ``relation_hygiene.DETERMINISTIC_MODEL`` and ``graph_hygiene.DEMOTION_MODEL`` are.
DETERMINISTIC_MODEL = "rule:relation_reconcile"

#: Note written on a near-side edge the ``asymmetric`` step demotes, completed with the
#: far sense id whose already-demoted reverse decided it. Deliberately *not* prefixed
#: ``"demoted: "``: that prefix is ``graph_hygiene._asserted_pairs``' signal to block a
#: pair from being re-created, and this pass's own ``tombstone`` step removes the edge in
#: the same sweep, so there is no surviving ``see_also`` for that signal to protect. The
#: prefix is instead one of :data:`DEMOTION_NOTE_PREFIXES`, so ``tombstone`` removes it.
ASYMMETRIC_NOTE_PREFIX = "reconcile:asymmetric:"

#: Header line of the provenance record ``tombstone`` writes for one sense, completed
#: with the sense id. One record per sense per sweep, however many edges it removed.
TOMBSTONE_RECORD_PREFIX = "reconcile:tombstone "

#: One removed-edge line inside that record: ``<prefix><type> -> <term> [<note>]``.
TOMBSTONE_LINE_PREFIX = "reconcile:tombstone: "

#: Header line of the provenance record ``cap`` writes for one sense, completed with the
#: sense id.
CAP_RECORD_PREFIX = "reconcile:cap "

#: One removed-edge line inside that record: ``<prefix><type> -> <term> [<note>]``.
CAP_LINE_PREFIX = "reconcile:cap:"

#: The marker sentinel's prefix (D-47's shape, without an attempt counter — this pass
#: makes no model call, so there is nothing to bound).
MARKER_PREFIX = "relation_reconcile"

#: The generic shape every demotion note in the project starts with. It is not a guess:
#: :func:`~opengloss_generator.workflows.graph_hygiene._asserted_pairs` tests exactly this
#: prefix to recognise "a hygiene pass judged this pair", which is the same question
#: :func:`is_demotion_note` asks. It covers ``graph_hygiene``'s ``SELF_LOOP_NOTE`` /
#: ``MUTUAL_NOTE`` / ``CYCLE_NOTE`` and ``content_hygiene``'s ``SELF_SYNONYM_NOTE`` /
#: ``SYNONYM_ANTONYM_NOTE`` / ``PROPER_NOUN_DEMOTE_NOTE`` without importing from either.
_GENERIC_DEMOTION_PREFIX = "demoted: "

#: Every prefix that marks a ``see_also`` as a tombstone rather than authored content.
#: The ``relation_hygiene`` entries are imported from that module's ``__all__`` rather
#: than restated, so a note it renames cannot silently stop being recognised here.
#: :data:`NANO_RETYPE_NOTE` is in the list because ``validity``'s retype path can name
#: ``see_also`` as the better type — an edge that reached ``see_also`` through a model
#: verdict is a tombstone however the note phrases it. :data:`ASYMMETRIC_NOTE_PREFIX` is
#: this pass's own, so a ``--only asymmetric`` sweep followed by a ``--only tombstone``
#: one reaches the same state as one full sweep.
DEMOTION_NOTE_PREFIXES: tuple[str, ...] = (
    ASYMMETRIC_NOTE_PREFIX,
    FAR_SIDE_NOTE_PREFIX,
    HEADWORD_INFLECTION_NOTE,
    HEADWORD_PHRASE_NOTE,
    META_LABEL_NOTE,
    NANO_INVALID_NOTE,
    NANO_RETYPE_NOTE,
    SIBLING_INFLECTION_NOTE,
    _GENERIC_DEMOTION_PREFIX,
)

#: Relation types that hold in both directions by definition, mirrored from
#: :data:`~opengloss_generator.workflows.graph_hygiene.SYMMETRIC_RELATION_TYPES` (not part
#: of that module's ``__all__``) exactly as ``relation_hygiene`` mirrors it. Only these
#: have a reverse edge that can disagree, so only these interest ``asymmetric``, and only
#: these can be left one-sided by ``cap``.
_SYMMETRIC_RELATION_TYPES: frozenset[RelationType] = frozenset(
    {RelationType.SYNONYM, RelationType.ANTONYM, RelationType.CONFUSABLE_WITH}
)

#: ``relation_hygiene``'s ``validity`` marker prefix, mirrored (module-private there).
#: An entry carrying one has had every live typed edge on it judged and kept, which is
#: what :func:`_accepted_by_validity` reads for ``cap``'s second sort key.
_VALIDITY_MARKER_PREFIX = "relation_hygiene:validity:"

#: How often a running sweep logs its progress, in entries. Imported from
#: ``content_hygiene`` so every sweep in the project reads the same in a run log.
_PROGRESS_EVERY = PROGRESS_EVERY


class RelationReconcileStep:
    """Names of the steps :func:`run_relation_reconcile` can select between."""

    ASYMMETRIC = "asymmetric"
    TOMBSTONE = "tombstone"
    CAP = "cap"

    #: The order the steps are applied in, whatever order ``--only`` lists them: a
    #: tombstone must be able to see what ``asymmetric`` just demoted, and a cap must
    #: count what ``tombstone`` has already taken out of the list.
    ALL: tuple[str, ...] = (ASYMMETRIC, TOMBSTONE, CAP)


# --------------------------------------------------------------------------------------
# Caps
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RelationCaps:
    """How many relations of one type a single sense may keep.

    The defaults are read off the measured shape of the core store (41,886 entries: mean
    13.4 relations per sense, median 10, p90 22; see_also 49%, synonym 17%, hypernym 11%,
    antonym 11%, hyponym 9%) and off what each type is *for*. ``hypernym`` is the tightest
    at 3 because a sense has one or two genuine parents and the rest are the model padding
    a list; ``synonym`` and ``hyponym`` are the loosest at 8 because those are the two
    types where a long tail is real information. Every unnamed type takes
    :attr:`default`.

    Attributes:
        synonym: Cap for ``synonym``.
        antonym: Cap for ``antonym``.
        hypernym: Cap for ``hypernym``.
        hyponym: Cap for ``hyponym``.
        instance_of: Cap for ``instance_of``.
        meronym: Cap for ``meronym``.
        holonym: Cap for ``holonym``.
        default: Cap for every other type, ``see_also`` included — by then the only
            ``see_also`` edges left are the ones an author wrote deliberately, and four of
            those is already a generous cross-reference list.
    """

    synonym: int = 8
    antonym: int = 4
    hypernym: int = 3
    hyponym: int = 8
    instance_of: int = 4
    meronym: int = 4
    holonym: int = 4
    default: int = 4

    def for_type(self, relation_type: RelationType) -> int:
        """Return the cap for one relation type.

        Args:
            relation_type: The type being counted.

        Returns:
            The named cap for that type, or :attr:`default`.
        """
        named = {
            RelationType.SYNONYM: self.synonym,
            RelationType.ANTONYM: self.antonym,
            RelationType.HYPERNYM: self.hypernym,
            RelationType.HYPONYM: self.hyponym,
            RelationType.INSTANCE_OF: self.instance_of,
            RelationType.MERONYM: self.meronym,
            RelationType.HOLONYM: self.holonym,
        }
        return named.get(relation_type, self.default)

    def as_dict(self) -> dict[str, int]:
        """Return the caps as a JSON-able mapping of relation type to allowance."""
        return {member.value: self.for_type(member) for member in RelationType}


# --------------------------------------------------------------------------------------
# The outcome
# --------------------------------------------------------------------------------------


@dataclass(slots=True)
class RelationReconcileStepResult(StepResult):
    """``StepResult`` plus the per-type breakdown each step is measured by.

    Attributes:
        by_type: The step's edits per relation type — demotions for ``asymmetric``,
            removals for ``tombstone`` and ``cap`` — keyed by the type's string value.
        senses_capped: How many senses lost at least one relation to a cap (``cap`` only).
        far_side_removed: How many of ``removed`` were the reverse of an edge this step
            capped on another entry (``cap`` only), folded into ``removed`` as well so
            that figure keeps meaning "how many edges did this step take out".
    """

    by_type: dict[str, int] = field(default_factory=dict)
    senses_capped: int = 0
    far_side_removed: int = 0

    def as_dict(self) -> dict[str, object]:
        """Return :meth:`StepResult.as_dict`'s view plus this pass's own counters."""
        data = super().as_dict()
        data["by_type"] = dict(sorted(self.by_type.items()))
        data["senses_capped"] = self.senses_capped
        data["far_side_removed"] = self.far_side_removed
        return data


@dataclass(slots=True)
class RelationReconcileOutcome:
    """What one :func:`run_relation_reconcile` sweep did, per step.

    Attributes:
        steps: One :class:`RelationReconcileStepResult` per selected step, keyed by name.
            ``retyped``, ``rewritten``, ``calls`` and ``cost_usd`` are always zero: this
            pass demotes and removes, and it makes no model call.
        entries_scanned: Entries the single pooled sweep visited.
        entries_changed: Distinct entries written, across every step and the far-side
            phase — not the sum of the per-step figures, which counts an entry twice when
            two steps both touched it.
        entries_skipped: Entries skipped because their marker digest already matched.
        dry_run: Whether the sweep computed its edits without writing them.
    """

    steps: dict[str, RelationReconcileStepResult] = field(default_factory=dict)
    entries_scanned: int = 0
    entries_changed: int = 0
    entries_skipped: int = 0
    dry_run: bool = False

    @property
    def demoted(self) -> int:
        """Return how many relations were demoted to ``see_also`` across every step."""
        return sum(result.demoted for result in self.steps.values())

    @property
    def removed(self) -> int:
        """Return how many relations were taken out of a sense across every step."""
        return sum(result.removed for result in self.steps.values())

    @property
    def stopped_reason(self) -> str | None:
        """Return why the sweep stopped early, or ``None`` if it ran to the end."""
        for result in self.steps.values():
            if result.stopped_reason is not None:
                return result.stopped_reason
        return None

    @property
    def changed(self) -> bool:
        """Return whether the sweep found anything at all to do."""
        return any(result.changed for result in self.steps.values())

    def as_dict(self) -> dict[str, object]:
        """Return a JSON-able view for the run summary and the CLI."""
        return {
            "entries_scanned": self.entries_scanned,
            "entries_changed": self.entries_changed,
            "entries_skipped": self.entries_skipped,
            "demoted": self.demoted,
            "removed": self.removed,
            "dry_run": self.dry_run,
            "stopped_reason": self.stopped_reason,
            "steps": {name: result.as_dict() for name, result in self.steps.items()},
        }


# --------------------------------------------------------------------------------------
# Tally and pool driver
# --------------------------------------------------------------------------------------


@dataclass(slots=True)
class _EntryEdits:
    """What the three steps did to one entry, before it is folded into the tally.

    Attributes:
        demoted: ``asymmetric``'s demotions, per relation type value.
        tombstoned: ``tombstone``'s removals, per relation type value.
        capped: ``cap``'s removals, per relation type value.
        senses_capped: Senses that lost at least one relation to a cap.
        far_side: Reverse edges ``cap``'s removals imply, for the second phase.
    """

    demoted: dict[str, int] = field(default_factory=dict)
    tombstoned: dict[str, int] = field(default_factory=dict)
    capped: dict[str, int] = field(default_factory=dict)
    senses_capped: int = 0
    far_side: list[_FarSideRemoval] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        """Return whether any step edited this entry."""
        return bool(self.demoted or self.tombstoned or self.capped)


def _add(counts: dict[str, int], relation_type: RelationType, amount: int = 1) -> None:
    """Fold ``amount`` into ``counts`` under a relation type's string value."""
    key = relation_type.value
    counts[key] = counts.get(key, 0) + amount


class _Tally:
    """The sweep's counters, mutated only while holding an ``asyncio.Lock``.

    Mirrors ``relation_hygiene._Tally``, which mirrors ``content_hygiene``'s, for the
    reason ``retrofit._Tally`` gives: these counters are touched by many handlers around
    many awaits, and ``+=`` being atomic in single-threaded asyncio is a property of the
    interpreter rather than of this code. One tally covers all three steps here, because
    all three run inside one handler under one lock hold.

    Args:
        steps: The step names selected for this sweep.
    """

    def __init__(self, steps: Sequence[str]) -> None:
        """Start an empty result per selected step."""
        self._lock = asyncio.Lock()
        self._results = {name: RelationReconcileStepResult(name=name) for name in steps}
        self._changed: set[str] = set()
        self._scanned = 0
        self._skipped = 0
        self._stopped_reason: str | None = None

    @property
    def results(self) -> dict[str, RelationReconcileStepResult]:
        """Return the accumulated per-step results; read once the pool has drained."""
        return dict(self._results)

    @property
    def entries_changed(self) -> int:
        """Return how many distinct entries were written."""
        return len(self._changed)

    @property
    def entries_scanned(self) -> int:
        """Return how many entries the main sweep visited."""
        return self._scanned

    @property
    def entries_skipped(self) -> int:
        """Return how many entries the marker let the sweep skip."""
        return self._skipped

    async def skipped(self) -> None:
        """Record one entry skipped by its marker."""
        async with self._lock:
            self._scanned += 1
            self._skipped += 1
            self._note_scan()

    async def entry(self, lexeme_id: str, edits: _EntryEdits) -> None:
        """Fold one visited entry's edits into the per-step results.

        Args:
            lexeme_id: The entry visited.
            edits: What the steps did to it.
        """
        async with self._lock:
            self._scanned += 1
            self._fold(lexeme_id, edits)
            self._note_scan()

    async def far_side(self, lexeme_id: str, edits: _EntryEdits) -> None:
        """Fold one far-side visit into ``cap``'s result, without counting it as a scan.

        The target entry a cap's reverse-removal visits is not one of the ids the sweep
        was driven over, and counting it as a scan would inflate ``entries_scanned`` past
        the length of the id list the caller gave (``relation_hygiene._Tally.entry``'s
        ``scanned=False`` makes the same distinction).

        Args:
            lexeme_id: The far entry visited.
            edits: What was removed from it.
        """
        async with self._lock:
            self._fold(lexeme_id, edits)
            result = self._results.get(RelationReconcileStep.CAP)
            if result is not None:
                result.far_side_removed += sum(edits.capped.values())

    def _fold(self, lexeme_id: str, edits: _EntryEdits) -> None:
        """Apply one entry's edits to the results. Caller holds the lock."""
        self._apply(RelationReconcileStep.ASYMMETRIC, edits.demoted, demoted=True)
        self._apply(RelationReconcileStep.TOMBSTONE, edits.tombstoned)
        self._apply(RelationReconcileStep.CAP, edits.capped)
        cap = self._results.get(RelationReconcileStep.CAP)
        if cap is not None:
            cap.senses_capped += edits.senses_capped
        if edits.changed:
            self._changed.add(lexeme_id)
            for result in self._results.values():
                result.entries_changed = len(self._changed)

    def _apply(self, step: str, counts: dict[str, int], *, demoted: bool = False) -> None:
        """Fold one step's per-type counts into its result. Caller holds the lock."""
        result = self._results.get(step)
        if result is None:
            return
        for key, amount in counts.items():
            result.by_type[key] = result.by_type.get(key, 0) + amount
            if demoted:
                result.demoted += amount
            else:
                result.removed += amount

    def _note_scan(self) -> None:
        """Record the scan on every step and log progress. Caller holds the lock."""
        for result in self._results.values():
            result.entries_scanned = self._scanned
        if self._scanned % _PROGRESS_EVERY == 0:
            _LOG.info(
                "relation_reconcile_progress",
                entries_done=self._scanned,
                entries_changed=len(self._changed),
                entries_skipped=self._skipped,
            )

    async def note_stop(self, reason: str) -> None:
        """Record why the sweep stopped early, keeping the first reason given."""
        async with self._lock:
            if self._stopped_reason is None:
                self._stopped_reason = reason
            for result in self._results.values():
                result.stopped_reason = self._stopped_reason


async def _drive[T](
    items: Sequence[T],
    handler: Callable[[T], Awaitable[None]],
    tally: _Tally,
    *,
    workers: int,
    stop_event: asyncio.Event | None,
) -> None:
    """Run one phase's handler over ``items`` through the bounded pool.

    Mirrors ``relation_hygiene._drive``: ``run_pool`` already treats
    :class:`BudgetExceededError` as a clean stop of the whole pool rather than an error to
    propagate, so this wrapper exists only to record *why* the sweep stopped before the
    exception is swallowed. Nothing here spends money, so a budget stop can only arrive
    from a shared session that some other workflow exhausted.

    Args:
        items: The work items — lexeme ids, or the far-side removals.
        handler: The per-item coroutine function.
        tally: The tally, which learns the stop reason.
        workers: Pool size.
        stop_event: Shared stop event, or ``None`` for a phase that must always drain.
    """

    async def guarded(item: T) -> None:
        try:
            await handler(item)
        except BudgetExceededError:
            await tally.note_stop("budget")
            raise

    await run_pool(items, guarded, workers=workers, stop_event=stop_event)
    if stop_event is not None and stop_event.is_set():
        await tally.note_stop("stopped")


# --------------------------------------------------------------------------------------
# Shared helpers
# --------------------------------------------------------------------------------------


def _rule_provenance(note: str | None = None) -> Provenance:
    """Return the zero-cost provenance record every edit here is stamped with.

    Args:
        note: Free text to preserve on the record — for this pass, either the removal
            listing or the idempotence marker.

    Returns:
        A :class:`~opengloss_generator.schema.Provenance` with every cost and token field
        at zero, so a naive sum over an entry's provenance table is unaffected by this
        pass having run. Mirrors ``relation_hygiene._rule_provenance``.
    """
    return Provenance(
        stage=StageName.HYGIENE,
        model=DETERMINISTIC_MODEL,
        prompt_version=PROMPT_VERSION,
        cost_usd=0.0,
        attempts=0,
        note=note,
    )


def _retype(relation: Relation, new_type: RelationType, note: str, provenance_id: str) -> None:
    """Retype one relation in place, keeping any note it already carried.

    Mirrors ``relation_hygiene._retype`` (module-private there): the old note is kept
    after the new one, so an edge demoted twice still names both reasons and the far side
    of a pair can be traced from either end.

    Args:
        relation: The relation to retype, mutated in place.
        new_type: What it becomes.
        note: Why, prepended to whatever note the relation already had.
        provenance_id: The entry's record for this edit.
    """
    relation.type = new_type
    relation.note = note if relation.note is None else f"{note} | {relation.note}"
    relation.provenance_id = provenance_id


def _live_senses(entry: Lexeme) -> list[tuple[POSEntry, Sense, str]]:
    """Return ``(pos_entry, sense, sense_id)`` for every non-retired sense.

    A retired sense is never read and never written by this pass: its relations are the
    record of what that sense claimed before it was merged away (D-52), and shortening
    that record would be rewriting history rather than reconciling it.
    """
    return [triple for triple in entry.iter_senses() if not triple[1].retired]


def is_demotion_note(note: str | None) -> bool:
    """Return whether a note marks its relation as a hygiene tombstone.

    A ``see_also`` carrying one of :data:`DEMOTION_NOTE_PREFIXES` did not start life as a
    ``see_also``: some pass moved it there because the edge it used to be was wrong. One
    with any other note, or none, is content an author wrote.

    Args:
        note: The relation's note, possibly ``None``.

    Returns:
        Whether the note starts with a known demotion prefix.
    """
    if not note:
        return False
    return note.startswith(DEMOTION_NOTE_PREFIXES)


def _has_validity_marker(entry: Lexeme) -> bool:
    """Return whether ``relation_hygiene``'s ``validity`` step has judged this entry.

    Args:
        entry: The entry to inspect.

    Returns:
        Whether any provenance record carries that step's D-47 sentinel. The sentinel's
        digest covers the ref set as the verdicts left it, so its presence means every
        live typed relation on the entry survived a model verdict.
    """
    return any(
        (record.note or "").startswith(_VALIDITY_MARKER_PREFIX)
        for record in entry.provenance.values()
    )


def _accepted_by_validity(relation: Relation, *, judged: bool) -> bool:
    """Return whether ``validity`` looked at this relation and kept it.

    D-50's ``validity`` step never lists a ``see_also`` and never leaves an accepted
    relation carrying a demotion note, so "present, typed, not demoted" on an entry that
    carries the step's marker is exactly the set it accepted.

    Args:
        relation: The relation under consideration.
        judged: Whether the entry carries the ``validity`` marker at all.

    Returns:
        Whether the relation was judged and kept.
    """
    if not judged or relation.type is RelationType.SEE_ALSO:
        return False
    return not is_demotion_note(relation.note)


class _Editor:
    """Adds provenance records to one entry lazily, and hands out their ids.

    A sweep that finds nothing must not write a provenance record, or every entry in the
    store would grow one per sweep; a sweep that finds three things in one sense wants one
    record for the sense, not three. Mirrors ``relation_hygiene._Editor``, with a second
    method for the removal listings, which are per (sense, step) rather than per entry.

    Args:
        entry: The entry being edited.
    """

    __slots__ = ("_entry", "_provenance_id")

    def __init__(self, entry: Lexeme) -> None:
        """Note the entry; no record is created until one is asked for."""
        self._entry = entry
        self._provenance_id: str | None = None

    def provenance_id(self) -> str:
        """Return this entry's shared record for in-place edits, creating it on demand."""
        if self._provenance_id is None:
            self._provenance_id = self._entry.add_provenance(_rule_provenance())
        return self._provenance_id

    def record_removals(self, header: str, sense_id: str, lines: Sequence[str]) -> None:
        """Write one record listing the edges a step removed from one sense.

        Args:
            header: :data:`TOMBSTONE_RECORD_PREFIX` or :data:`CAP_RECORD_PREFIX`.
            sense_id: The sense the edges were removed from — needed to rebuild their
                D-1 edge ids, which are ``<sense id>-<type>-><target lexeme>``.
            lines: One line per removed edge, already formatted.
        """
        if not lines:
            return
        self._entry.add_provenance(_rule_provenance("\n".join([f"{header}{sense_id}", *lines])))


def _removal_line(prefix: str, relation: Relation) -> str:
    """Return the provenance line describing one removed edge.

    Args:
        prefix: :data:`TOMBSTONE_LINE_PREFIX`, or :data:`CAP_LINE_PREFIX` for ``cap``,
            which spells the type into the prefix itself.
        relation: The relation being removed, read before it leaves the list.

    Returns:
        ``<prefix><type> -> <term> [<note>]`` — the type as stored, because that is what
        the edge id is built from, and the note verbatim (``-`` when there is none).
    """
    return f"{prefix}{relation.type.value} -> {relation.target.term} [{relation.note or '-'}]"


# --------------------------------------------------------------------------------------
# The marker
# --------------------------------------------------------------------------------------


def _digest(parts: Iterable[str]) -> str:
    """Return a stable short hash of a set of strings.

    Args:
        parts: The strings, in any order.

    Returns:
        Sixteen hex characters of SHA-256 over the sorted, newline-joined parts. Sorted so
        the digest does not depend on document order, and SHA-256 rather than :func:`hash`
        because the value is written to disk and compared across processes. Mirrors
        ``relation_hygiene._ref_digest``.
    """
    return hashlib.sha256("\n".join(sorted(parts)).encode("utf-8")).hexdigest()[:16]


def _edge_refs(entry: Lexeme) -> list[str]:
    """Return the D-1 edge id of every relation on every live sense.

    Args:
        entry: The entry to project.

    Returns:
        One ``<sense id>-<type>-><target lexeme>`` per live relation. Read live off the
        entry rather than frozen at collection time, for the reason D-50 gives: a marker
        must describe the set as it stands, or the next sweep re-examines a set that no
        longer exists.
    """
    return [edge.edge_id for edge in entry.edges()]


def _marker_note(steps: Sequence[str], entry: Lexeme) -> str:
    """Return the sentinel to stamp on an entry this sweep changed.

    Args:
        steps: The step names this sweep selected. In the digest because a marker written
            by a ``--only tombstone`` sweep must not stop a later full sweep from capping
            the same entry.
        entry: The entry, in the state the sweep is leaving it.

    Returns:
        ``relation_reconcile:<digest>``.
    """
    return f"{MARKER_PREFIX}:{_digest([*(f'step={s}' for s in steps), *_edge_refs(entry)])}"


def _record_order(provenance_id: str) -> int:
    """Return a provenance id's insertion rank, as an integer.

    Ids are ``p1``, ``p2``, … assigned in insertion order (``Lexeme.add_provenance``), but
    the table is **not** in that order once it has been round-tripped through the store:
    ``LexemeStore.write`` serialises with ``orjson.OPT_SORT_KEYS``, so an entry with a
    hundred records reads back ``p1, p10, p100, p101, p11, …``. Ordering by the number
    rather than by position is what makes "the most recent marker" mean what it says.
    (``relation_hygiene._latest_marker`` and ``content_hygiene``'s equivalent take the last
    record in table order and are wrong on exactly those entries; noted in D-65, not fixed
    here, since those files' markers are their own passes' business.)

    Args:
        provenance_id: The table key, ``p<n>``.

    Returns:
        ``n``, or ``-1`` for a key that does not have that shape.
    """
    digits = provenance_id.removeprefix("p")
    return int(digits) if digits.isdigit() else -1


def _latest_marker(entry: Lexeme) -> str | None:
    """Return the most recent ``relation_reconcile`` sentinel on an entry, or ``None``.

    Args:
        entry: The entry to inspect.

    Returns:
        The note verbatim, or ``None`` when this pass has never written on the entry.
    """
    notes = [
        (_record_order(key), record.note)
        for key, record in entry.provenance.items()
        if (record.note or "").startswith(f"{MARKER_PREFIX}:")
    ]
    return max(notes)[1] if notes else None


def _already_reconciled(entry: Lexeme, marker: str) -> bool:
    """Return whether this exact sweep has already been applied to this entry.

    Args:
        entry: The entry to inspect.
        marker: The sentinel :func:`_marker_note` would write for the entry as it stands.

    Returns:
        Whether the most recent ``relation_reconcile`` record on the entry says the same
        thing. Provenance ids are assigned in insertion order and never reused, so the
        last matching record is the most recently written one.
    """
    return _latest_marker(entry) == marker


# --------------------------------------------------------------------------------------
# Step 1 — asymmetric
# --------------------------------------------------------------------------------------


def _collect_demoted_pairs(store: LexemeStore) -> set[tuple[str, str, str]]:
    """Index every lexeme pair whose reverse assertion a hygiene pass has already demoted.

    A read-only projection of the **whole** store, taken without locks — the discipline
    ``graph_hygiene._load_view`` and ``audit_store`` already use — and never restricted by
    the caller's ``lexeme_ids``, because the far side of an edge on that list is very
    often not on it. Only ``see_also`` edges carrying a demotion note count, and only
    those whose own target is an entry in the store: a demotion toward "banners" or "slang
    term" has no near side that could ever look it up, and D-50 measured those to be the
    overwhelming majority, which is what keeps this index small enough to hold in memory
    for a 41,886-entry store.

    **Entry-level, not sense-level**, exactly as ``graph_hygiene._asserted_pairs`` and
    ``audit._relation_targets_lexeme`` are: "the reciprocity question is whether the other
    side made the matching claim anywhere in its own senses, not whether one particular
    sense did". A sense-level index was measured on the 300-entry sample and missed 47 of
    the 61 one-sided pairs there, nearly all of them because the near side's target
    ``sense_id`` names a sense D-52 has since retired and merged away, so the surviving
    reverse lives on a *different* sense id than the one the near side resolved to.

    A pair is only indexed when the far entry holds **no live relation of that type**
    back toward the near lexeme: a far side that still asserts it is reciprocated, and
    reciprocated is what this pass is trying to produce.

    Args:
        store: The store to project. Never written.

    Returns:
        One ``(far lexeme, relation type value, near lexeme)`` triple per pair whose far
        side has been demoted and not re-asserted. A near side looks itself up by the
        lexeme its own edge resolves to.
    """
    known = set(store.iter_ids())
    index: set[tuple[str, str, str]] = set()
    for entry in store.iter_entries():
        demoted_toward: set[str] = set()
        live_toward: set[tuple[str, str]] = set()
        for _, sense, _ in _live_senses(entry):
            for relation in sense.relations:
                target_lexeme = relation.target.lexeme_id
                if target_lexeme == entry.lexeme_id or target_lexeme not in known:
                    continue
                if relation.type is RelationType.SEE_ALSO:
                    if is_demotion_note(relation.note):
                        demoted_toward.add(target_lexeme)
                elif relation.type in _SYMMETRIC_RELATION_TYPES:
                    live_toward.add((relation.type.value, target_lexeme))
        for near_lexeme in demoted_toward:
            for relation_type in _SYMMETRIC_RELATION_TYPES:
                if (relation_type.value, near_lexeme) in live_toward:
                    continue
                index.add((entry.lexeme_id, relation_type.value, near_lexeme))
    return index


def _demote_asymmetric(
    entry: Lexeme, editor: _Editor, index: set[tuple[str, str, str]], edits: _EntryEdits
) -> None:
    """Apply the stricter of two disagreeing directional verdicts, on the near side.

    Args:
        entry: The entry being edited (A), mutated in place.
        editor: The entry's provenance editor.
        index: :func:`_collect_demoted_pairs`' projection.
        edits: The running per-entry counts, extended in place.
    """
    for _, sense, _ in _live_senses(entry):
        for relation in sense.relations:
            if relation.type not in _SYMMETRIC_RELATION_TYPES:
                continue
            far_sense = relation.target.sense_id
            if far_sense is None:
                continue
            far_lexeme = relation.target.lexeme_id
            if far_lexeme == entry.lexeme_id:
                continue
            if (far_lexeme, relation.type.value, entry.lexeme_id) not in index:
                continue
            _add(edits.demoted, relation.type)
            _retype(
                relation,
                RelationType.SEE_ALSO,
                f"{ASYMMETRIC_NOTE_PREFIX}{far_sense}",
                editor.provenance_id(),
            )


# --------------------------------------------------------------------------------------
# Step 2 — tombstone
# --------------------------------------------------------------------------------------


def _tombstone(entry: Lexeme, editor: _Editor, edits: _EntryEdits) -> None:
    """Take every demoted ``see_also`` out of the list, writing each one down first.

    Args:
        entry: The entry being edited, mutated in place.
        editor: The entry's provenance editor.
        edits: The running per-entry counts, extended in place.
    """
    for _, sense, sense_id in _live_senses(entry):
        kept: list[Relation] = []
        lines: list[str] = []
        for relation in sense.relations:
            if relation.type is RelationType.SEE_ALSO and is_demotion_note(relation.note):
                lines.append(_removal_line(TOMBSTONE_LINE_PREFIX, relation))
                _add(edits.tombstoned, relation.type)
                continue
            kept.append(relation)
        if lines:
            editor.record_removals(TOMBSTONE_RECORD_PREFIX, sense_id, lines)
            sense.relations = kept


# --------------------------------------------------------------------------------------
# Step 3 — cap
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _FarSideRemoval:
    """One reverse edge a cap's removal implies, for the second phase.

    Attributes:
        lexeme_id: The entry to visit — the capped relation's target lexeme (B).
        source_lexeme: The entry the cap happened on (A), which no longer asserts this
            type toward ``lexeme_id`` anywhere in its live senses.
        relation_type: The type the capped relation had. Only a reverse still carrying it
            is a candidate, which is what makes a second sweep over this a no-op.
    """

    lexeme_id: str
    source_lexeme: str
    relation_type: RelationType


def _cap_sort_key(relation: Relation, index: int, *, judged: bool) -> tuple[int, int, int]:
    """Return the keep-order key for one relation inside its (sense, type) group.

    Resolved first: a resolved target is a real entry, the only kind that can carry a
    reciprocal and the only kind a reader can follow. Then what ``validity`` accepted: an
    edge a model has already looked at and kept outranks one nothing has ever judged.
    Then document order, so the result is stable and a rerun keeps the same edges.

    Args:
        relation: The relation being ordered.
        index: Its position in the sense's list.
        judged: Whether the entry carries ``relation_hygiene``'s ``validity`` marker.

    Returns:
        The sort key, ascending.
    """
    return (
        0 if relation.target.resolved else 1,
        0 if _accepted_by_validity(relation, judged=judged) else 1,
        index,
    )


def _cap(entry: Lexeme, editor: _Editor, caps: RelationCaps, edits: _EntryEdits) -> None:
    """Trim every over-long per-type run of relations on every live sense.

    Args:
        entry: The entry being edited, mutated in place.
        editor: The entry's provenance editor.
        caps: The per-type allowances.
        edits: The running per-entry counts and far-side requests, extended in place.
    """
    judged = _has_validity_marker(entry)
    capped_pairs: set[tuple[RelationType, str]] = set()
    for _, sense, sense_id in _live_senses(entry):
        groups: dict[RelationType, list[tuple[tuple[int, int, int], int, Relation]]] = {}
        for index, relation in enumerate(sense.relations):
            key = _cap_sort_key(relation, index, judged=judged)
            groups.setdefault(relation.type, []).append((key, index, relation))

        dropped: set[int] = set()
        lines: list[str] = []
        for relation_type, members in sorted(groups.items(), key=lambda item: item[0].value):
            cap = caps.for_type(relation_type)
            if len(members) <= cap:
                continue
            for _, index, relation in sorted(members, key=lambda item: item[0])[cap:]:
                dropped.add(index)
                lines.append(_removal_line(CAP_LINE_PREFIX, relation))
                _add(edits.capped, relation_type)
                if relation.type in _SYMMETRIC_RELATION_TYPES and relation.target.resolved:
                    capped_pairs.add((relation.type, relation.target.lexeme_id))
        if not dropped:
            continue
        editor.record_removals(CAP_RECORD_PREFIX, sense_id, lines)
        sense.relations = [r for i, r in enumerate(sense.relations) if i not in dropped]
        edits.senses_capped += 1

    edits.far_side.extend(_far_side_removals(entry, capped_pairs))


def _far_side_removals(
    entry: Lexeme, capped_pairs: set[tuple[RelationType, str]]
) -> list[_FarSideRemoval]:
    """Return the reverse-edge removals a capped entry implies, entry-level.

    Called once the whole entry has been capped, not per removal: a sense may lose its
    ``synonym`` toward *beta* while another sense of the same entry still asserts one, and
    the reciprocity every downstream measure asks about is whether the **entry** still
    makes the claim (``audit._relation_targets_lexeme``, ``graph_hygiene._asserted_pairs``).
    A pair the entry still asserts somewhere is reciprocated and needs no repair.

    Args:
        entry: The entry, already capped.
        capped_pairs: The ``(type, target lexeme)`` pairs the cap removed, restricted to
            symmetric types with a resolved target — an asymmetric type has no reverse to
            strand, and an unresolved target is not counted as an assertion by ``audit``
            and is never inferred from by ``graph_hygiene``.

    Returns:
        One removal per pair the entry no longer asserts anywhere.
    """
    if not capped_pairs:
        return []
    still: set[tuple[RelationType, str]] = {
        (relation.type, relation.target.lexeme_id)
        for _, sense, _ in _live_senses(entry)
        for relation in sense.relations
    }
    return [
        _FarSideRemoval(
            lexeme_id=target_lexeme,
            source_lexeme=entry.lexeme_id,
            relation_type=relation_type,
        )
        for relation_type, target_lexeme in sorted(
            capped_pairs, key=lambda pair: (pair[0].value, pair[1])
        )
        if (relation_type, target_lexeme) not in still and target_lexeme != entry.lexeme_id
    ]


def _is_far_side_of(relation: Relation, removal: _FarSideRemoval) -> bool:
    """Return whether a far-side relation is a reverse a cap's removal stranded.

    Entry-level, for the reason :func:`_far_side_removals` gives and the reason
    ``graph_hygiene._asserted_pairs`` gives: the claim the near side has stopped making is
    "these two lexemes are related this way", and it is that claim, wherever the far entry
    keeps it, that would otherwise be left asserted by one side alone. This is the one
    place this pass deliberately departs from ``relation_hygiene._is_far_side_of``, whose
    sense-level test is right for a *demotion* — a judgement about one sense pair — and
    wrong for a *cap*, which is a judgement about how long a list may be.

    Args:
        relation: The far-side relation under consideration.
        removal: The cap that prompted the check.

    Returns:
        Whether it still carries the capped type and points back at the source lexeme.
    """
    return (
        relation.type is removal.relation_type
        and relation.target.lexeme_id == removal.source_lexeme
    )


async def _remove_far_side(
    removal: _FarSideRemoval,
    store: LexemeStore,
    tally: _Tally,
    *,
    steps: Sequence[str],
    dry_run: bool,
) -> None:
    """Remove the reverse edge one cap implies, for $0.

    Visits ``removal.lexeme_id`` (B) under its own lock, and only ever after the phase
    that queued it has fully drained and released A's lock, so no two entry locks are ever
    held at once (D-31). Idempotent: an edge already removed is not there to match.

    Args:
        removal: The reverse removal to perform.
        store: The store.
        tally: The tally to fold the visit into, as a far-side visit rather than a scan.
        steps: The sweep's selected steps, for refreshing the entry's marker. The marker
            is only *refreshed*, never created here: an entry this phase reaches may never
            have been swept itself (``lexeme_ids`` named a list this one was not on), and
            stamping a marker on it would make a later full sweep skip an entry no step has
            ever run over.
        dry_run: Compute the removal and report it without writing.
    """
    edits = _EntryEdits()
    async with store.locked(removal.lexeme_id):
        entry = store.read(removal.lexeme_id)
        if entry is None:
            return
        editor = _Editor(entry)
        for _, sense, sense_id in _live_senses(entry):
            kept: list[Relation] = []
            lines: list[str] = []
            for relation in sense.relations:
                if _is_far_side_of(relation, removal):
                    lines.append(_removal_line(CAP_LINE_PREFIX, relation))
                    _add(edits.capped, relation.type)
                    continue
                kept.append(relation)
            if lines:
                editor.record_removals(CAP_RECORD_PREFIX, sense_id, lines)
                sense.relations = kept
        if edits.changed and not dry_run:
            if _latest_marker(entry) is not None:
                entry.add_provenance(_rule_provenance(_marker_note(steps, entry)))
            store.write(entry)
    if edits.changed:
        await tally.far_side(removal.lexeme_id, edits)


async def _remove_far_side_all(
    removals: Sequence[_FarSideRemoval],
    store: LexemeStore,
    tally: _Tally,
    *,
    workers: int,
    steps: Sequence[str],
    dry_run: bool,
) -> None:
    """Run the far-side phase over every removal ``cap``'s main sweep queued.

    A second pooled sweep, run only once the first has fully drained: every lock the main
    sweep held has been released before this one acquires any (D-31).

    **Takes no stop event, deliberately** (D-50's second amendment, and its mechanism):
    ``run_pool``'s workers return before pulling their first item once the event is set,
    so a phase given the event would silently do nothing and leave the store asserting
    exactly one half of a pair this sweep has just taken apart. It buys nothing — no model
    call, one local read-modify-write per target — and is bounded by the removals the run
    actually made, so no stop, however urgent, is worth a knowingly one-sided store.

    Args:
        removals: Every reverse removal the main sweep queued, in any order and with
            duplicates; deduplicated here and processed in a stable order so the same
            store produces the same result whatever order the workers finished in.
        store: The store.
        tally: The tally to accumulate into. Its ``stopped_reason`` is already set by the
            main sweep when that one stopped, and this phase never clears it.
        workers: Pool size.
        steps: The sweep's selected steps, for refreshing markers on the entries it edits.
        dry_run: Compute the removals and report them without writing.
    """
    ordered = sorted(
        set(removals),
        key=lambda r: (r.lexeme_id, r.source_lexeme, r.relation_type.value),
    )

    async def visit(removal: _FarSideRemoval) -> None:
        await _remove_far_side(removal, store, tally, steps=steps, dry_run=dry_run)

    await _drive(ordered, visit, tally, workers=workers, stop_event=None)


# --------------------------------------------------------------------------------------
# The sweep
# --------------------------------------------------------------------------------------


def _reconcile_entry(
    entry: Lexeme,
    *,
    steps: Sequence[str],
    index: set[tuple[str, str, str]],
    caps: RelationCaps,
) -> _EntryEdits:
    """Apply every selected step to one entry, in :attr:`RelationReconcileStep.ALL` order.

    Args:
        entry: The entry, mutated in place.
        steps: The selected step names.
        index: :func:`_collect_demoted_pairs`' projection, empty when ``asymmetric``
            was not selected.
        caps: The per-type allowances.

    Returns:
        What the steps did.
    """
    edits = _EntryEdits()
    editor = _Editor(entry)
    if RelationReconcileStep.ASYMMETRIC in steps:
        _demote_asymmetric(entry, editor, index, edits)
    if RelationReconcileStep.TOMBSTONE in steps:
        _tombstone(entry, editor, edits)
    if RelationReconcileStep.CAP in steps:
        _cap(entry, editor, caps, edits)
    return edits


async def run_relation_reconcile(
    store: LexemeStore,
    *,
    workers: int,
    stop_event: asyncio.Event | None = None,
    only: set[str] | None = None,
    lexeme_ids: Sequence[str] | None = None,
    caps: RelationCaps | None = None,
    dry_run: bool = False,
) -> RelationReconcileOutcome:
    """Reconcile the relation lists the hygiene passes' demotions left behind (D-65).

    Three free steps, described in full in the module docstring: ``asymmetric`` applies
    the stricter of two disagreeing directional verdicts on a symmetric pair,
    ``tombstone`` takes every demoted ``see_also`` out of ``Sense.relations`` and writes it
    to provenance instead, and ``cap`` trims each sense's per-type runs to
    :class:`RelationCaps`. No model call is made anywhere.

    Args:
        store: The store to reconcile.
        workers: Pool size for the main sweep and the far-side phase.
        stop_event: Shared stop event. A caller may set it to end the main sweep after the
            entries in hand; the outcome then reports ``stopped_reason="stopped"``. The
            far-side phase is deliberately not given it (see :func:`_remove_far_side_all`).
        only: Step names to run; defaults to all of :attr:`RelationReconcileStep.ALL`.
            Steps are applied in that attribute's order whatever order they are given in.
        lexeme_ids: Ids to visit; defaults to every id in the store, sorted. ``asymmetric``
            still reads the *whole* store for its index, because the far side of an edge on
            the list is very often not on it.
        caps: Per-type allowances for ``cap``; defaults to :class:`RelationCaps`' own.
        dry_run: Compute every edit and report it without writing anything.

    Returns:
        A :class:`RelationReconcileOutcome` carrying per-step counts and per-type
        breakdowns.

    Raises:
        ValueError: If ``only`` names a step that does not exist.
    """
    selected = set(only) if only is not None else set(RelationReconcileStep.ALL)
    unknown = sorted(selected - set(RelationReconcileStep.ALL))
    if unknown:
        raise ValueError(f"unknown relation reconcile step(s): {unknown}")
    steps = tuple(name for name in RelationReconcileStep.ALL if name in selected)

    if RelationReconcileStep.TOMBSTONE in steps and RelationReconcileStep.ASYMMETRIC not in steps:
        # Removing a demotion tombstone also removes graph_hygiene's own signal not to
        # re-create the pair (_asserted_pairs). That is safe only once `asymmetric` has
        # demoted the live half, which is why the default sweep runs both.
        _LOG.warning("relation_reconcile_tombstone_without_asymmetric", steps=list(steps))

    caps = caps or RelationCaps()
    ids = list(lexeme_ids) if lexeme_ids is not None else sorted(store.iter_ids())
    index = _collect_demoted_pairs(store) if RelationReconcileStep.ASYMMETRIC in steps else set()
    _LOG.info(
        "relation_reconcile_index",
        demoted_reverses=len(index),
        entries=len(ids),
        steps=list(steps),
    )

    tally = _Tally(steps)
    removals: list[_FarSideRemoval] = []
    removals_lock = asyncio.Lock()

    async def reconcile(lexeme_id: str) -> None:
        edits = _EntryEdits()
        async with store.locked(lexeme_id):
            entry = store.read(lexeme_id)
            if entry is None:
                return
            marker = _marker_note(steps, entry)
            if _already_reconciled(entry, marker):
                await tally.skipped()
                return
            edits = _reconcile_entry(entry, steps=steps, index=index, caps=caps)
            if edits.changed and not dry_run:
                entry.add_provenance(_rule_provenance(_marker_note(steps, entry)))
                store.write(entry)
        if edits.far_side:
            async with removals_lock:
                removals.extend(edits.far_side)
        await tally.entry(lexeme_id, edits)

    await _drive(ids, reconcile, tally, workers=workers, stop_event=stop_event)
    await _remove_far_side_all(
        removals, store, tally, workers=workers, steps=steps, dry_run=dry_run
    )

    outcome = RelationReconcileOutcome(
        steps=tally.results,
        entries_scanned=tally.entries_scanned,
        entries_changed=tally.entries_changed,
        entries_skipped=tally.entries_skipped,
        dry_run=dry_run,
    )
    _LOG.info("relation_reconcile_complete", workers=workers, **outcome.as_dict())
    return outcome
