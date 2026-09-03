"""Workflow 10 — sense hygiene: near-duplicate senses, and examples filed under the wrong one.

``workflows/content_hygiene.py`` repairs defects a rule can see, and
``workflows/relation_hygiene.py`` judges the far end of an edge. Neither can answer the two
questions the QA judge asked
about the *sense inventory itself*, and both of its answers were bad (``docs/QA-DIARY.md``,
Iteration 1 — ``claude-opus-5``, 58 core entries, 179 senses):

============================  ======  ==================================================
Judged dimension              Rate    What the judge saw
============================  ======  ==================================================
``examples_fit_sense``        34.1%   "Sense 2's examples are mostly non-religious
                                      (sibling, race, broken toy) and belong to sense 1";
                                      verb-sense examples that use the noun
``distinct_from_other_senses`` 25.7%  near-duplicate senses that exact-text dedup cannot
                                      see — two noun senses of *vow* differing only by
                                      the word "religious"
============================  ======  ==================================================

Both are invisible to every deterministic check the project has. ``retrofit``'s duplicate-sense
pass compares *normalised gloss text*, so "a solemn promise" and "a solemn religious promise"
are two different strings and survive it; nothing at all compares an example against the
definitions it was *not* filed under. Both questions are one model call per entry, on the cheap
model, because both are structural verdicts about definitions rather than prose for a reader.

Two steps, selectable by name through ``only=``, each idempotent, each its own pooled sweep over
the id list.

``distinctness`` (nano, ``HYGIENE`` policy)
    Entries with **two or more non-retired senses under one part of speech** get one call
    listing every live sense as ``[ref, pos, canonical gloss, first example]``, and the answer is
    ``{duplicate_groups: [[ref, ref, ...], ...]}`` — the groups of senses that are the *same*
    meaning, empty when they are all distinct. :data:`DISTINCTNESS_INSTRUCTIONS` sets the bar
    where WordNet sets it: a sense is distinct when a learner would need a *separate definition*
    for it, and a difference only in domain colouring, register, or specific-versus-generic
    phrasing is not a distinction. It carries a worked example of both answers — a true duplicate
    pair, and *bank* (river / institution) as a split that must survive.

    Within a group the **lowest sense index wins** (D-1: sense ids are positional and are never
    renumbered), and everything the survivor lacks is merged onto it before the others are
    retired: canonical examples that are not already there, example renditions at each
    ``(level, register)`` the survivor has no rendition for, relations it does not already assert
    by ``(type, target lexeme)``, and gloss renditions at each ``(level, register)`` it lacks.
    The losers are marked :attr:`~opengloss_generator.schema.Sense.retired` — **never deleted,
    never renumbered** — and a zero-cost provenance record on the entry says why: ``retired sense
    <sid>: duplicate of <survivor sid>``. A sense has no ``note`` field of its own, so the entry's
    provenance table is where the reason lives, which is where every other note this project
    writes lives too. A group whose members span more than one part of speech is refused whole: a
    noun sense and a verb sense are never the same meaning, whatever the words look like.

``example_fit`` (nano, ``HYGIENE`` policy)
    Entries with **two or more non-retired senses at all** get one call listing every live sense
    as ``[sense_ref, pos, gloss]`` and every **canonical** example across all of them as
    ``[example_ref, filed_under sense_ref, text]``, and the answer is
    ``{placements: [{example_ref, best_sense_ref: int|null}]}`` — which sense each example
    actually illustrates, or ``null`` when it illustrates none of them (including the measured
    shape where a noun use is filed under a verb sense).

    An example whose best sense is where it already sits is left alone, which is the common
    answer and is not a failure. An example that belongs elsewhere is **moved** — the canonical
    rendition and its level renditions together, the ``(level, register)`` siblings at the same
    position in that sense's example list — unless the destination already holds
    :data:`MAX_CANONICAL_EXAMPLES` canonical examples, in which case there is no room for it and
    it is dropped from the source into a zero-cost note (``moved-out example (no room): <text>``)
    rather than piled onto a sense that already has enough. A ``null`` answer removes it from the
    sense with the note ``removed example (fits no sense): <text>``. Every removed text is
    written to a note before the rendition comes out, so nothing is lost even here. Spans are
    re-found on the destination with :func:`~opengloss_generator.spans.find_span`: the headword is
    the same word either way, so they are usually unchanged, but re-finding costs nothing and the
    stored offsets are then measured against the text that is actually stored.

    A sense that this step leaves with **no canonical example at all** is reported as
    ``senses_emptied`` and is *not* repaired here: ``workflows/retrofit.py``'s ``repair`` pass
    step (b) already writes canonical examples for exactly that condition, so run ``retrofit
    --only repair`` after this workflow rather than duplicating it.

Idempotence
-----------

Neither step is idempotent by construction, so each carries D-47's sentinel on a zero-cost
provenance record — ``<prefix>:<digest>;attempts=<n>``, bounded at :data:`MAX_ATTEMPTS` attempts
per entry — over the *sense set* for ``distinctness`` and the *canonical example set* for
``example_fit``. Following ``relation_hygiene`` rather than ``content_hygiene``, the digest is
taken over the set **as the answers leave it**, not as they found it: taken the other way, a
sweep that merged a duplicate or moved an example would leave a marker describing a set that no
longer exists, and the very next sweep would buy a second opinion about senses it had already
passed. Taken this way the marker reads "I have judged exactly this set", a second sweep over an
unchanged entry is free, and an entry that later *gains* a sense or an example still earns one
further attempt. A sentinel rather than a bare :class:`~opengloss_generator.schema.StageName`
because both calls reuse the shared ``HYGIENE`` policy rather than adding a stage of their own.

An entry with one live sense is never listed, never called for, and costs $0 on every sweep.

Run order
---------

Run this pass **before** ``retrofit --only repair`` (which refills the senses ``example_fit``
empties) and after ``content_hygiene``'s ``garbage_examples`` (there is no point paying a model
to decide where ``'hypernyms(['`` belongs). Retiring a sense does not renumber anything, so no
downstream sense id moves and no edge is re-pointed; ``Lexeme.edges`` already skips retired
senses, so a retired duplicate leaves the projected graph on its own.

Concurrency and locking (D-31)
------------------------------

Both steps drive their ids through :func:`~opengloss_generator.runner.run_pool`, and the handler
holds the entry's lock across the whole of read → collect → model call → apply → write, so no
entry is ever read outside the lock it is written under. Neither step reads any *other* entry at
all: both questions are answered entirely from within one entry. Counters go through
:class:`_Tally`, mutated only while holding an ``asyncio.Lock``, for the reason
``retrofit._Tally`` gives.

:data:`~opengloss_generator.workflows.content_hygiene.PROGRESS_EVERY` is imported from
``content_hygiene`` so every sweep in the project reads the same in a run log. This pass keeps
its own :class:`StepResult` rather than importing that module's, because its counters are its
own — nothing here is demoted, retyped or rewritten. Its contract and its instructions are
module-private for D-49's and D-50's reason: ``contracts.py`` and ``prompts.py`` are edited
concurrently on this branch, and a self-contained module is what lets this work land without
conflicting with that.
"""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Annotated

from pydantic import BaseModel, ConfigDict, Field

from opengloss_generator import spans
from opengloss_generator.errors import BudgetExceededError, GenerationError
from opengloss_generator.log import get_logger
from opengloss_generator.prompts import PROMPT_VERSION
from opengloss_generator.runner import run_pool
from opengloss_generator.schema import CANONICAL_KEY, Provenance, StageName
from opengloss_generator.workflows.content_hygiene import PROGRESS_EVERY

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Iterable, Sequence

    from opengloss_generator.schema import (
        Example,
        Lexeme,
        POSEntry,
        ReadingLevel,
        Register,
        Rendition,
        Renditions,
        Sense,
    )
    from opengloss_generator.stages import StageRunner
    from opengloss_generator.store import LexemeStore

__all__ = [
    "DISTINCTNESS_INSTRUCTIONS",
    "EXAMPLE_FIT_INSTRUCTIONS",
    "MAX_ATTEMPTS",
    "MAX_CANONICAL_EXAMPLES",
    "MOVED_OUT_NOTE",
    "REMOVED_EXAMPLE_NOTE",
    "RETIRED_SENSE_NOTE",
    "SenseHygieneOutcome",
    "SenseHygieneStep",
    "StepResult",
    "run_sense_hygiene",
]

_LOG = get_logger(__name__)

#: Provenance ``model`` for every free edit this pass makes — the merges, the retirements and
#: the example moves are all applications of a model *answer* by a rule, and the priced record
#: for the answer itself is added separately. Named the way
#: ``content_hygiene.DETERMINISTIC_MODEL`` and ``relation_hygiene.DETERMINISTIC_MODEL`` are.
DETERMINISTIC_MODEL = "rule:sense_hygiene"

#: The note a retirement writes on the entry's provenance table. A
#: :class:`~opengloss_generator.schema.Sense` has no ``note`` field, so this is where the reason
#: lives; formatted with the retired and surviving sense ids.
RETIRED_SENSE_NOTE = "retired sense {retired}: duplicate of {survivor}"

#: Note prefixes for the two ways ``example_fit`` takes an example out of a sense. Each is
#: completed with the removed text, so nothing is lost by the removal.
MOVED_OUT_NOTE = "moved-out example (no room): "
REMOVED_EXAMPLE_NOTE = "removed example (fits no sense): "

#: How many canonical examples a sense may hold before ``example_fit`` stops moving more into
#: it. Three is what the generator writes and what ``retrofit``'s repair pass restores, so a
#: sense at three is not short of examples and a fourth would be pile-on rather than repair.
MAX_CANONICAL_EXAMPLES = 3

#: How many attempts a step makes on one entry before leaving what still offends alone rather
#: than billing a third answer for it (D-47's bound, per entry).
MAX_ATTEMPTS = 2

#: Separates the set digest from the attempt count inside a marker note.
_ATTEMPTS_SEPARATOR = ";attempts="

#: Sentinel prefixes, one per step. Both calls reuse the shared ``HYGIENE`` policy rather than
#: adding a stage of their own, so the stage alone would collide with ``content_hygiene``,
#: ``relation_hygiene``, ``retrofit`` and every other pass that does the same.
_DISTINCTNESS_PREFIX = "sense_hygiene:distinctness"
_EXAMPLE_FIT_PREFIX = "sense_hygiene:example_fit"

#: Shown in place of an example that a sense does not have.
_NO_EXAMPLE = "(none)"

#: The fewest live senses that make either question worth asking.
_MIN_SENSES = 2


class SenseHygieneStep:
    """Names of the steps :func:`run_sense_hygiene` can select between."""

    DISTINCTNESS = "distinctness"
    EXAMPLE_FIT = "example_fit"

    #: The order the steps run in. ``distinctness`` first, deliberately: it retires senses and
    #: merges their examples onto a survivor, so running it first means ``example_fit`` is never
    #: billed to decide where an example belongs among senses that were about to be merged, and
    #: never files an example under a sense that is retired a moment later.
    ALL: tuple[str, ...] = (DISTINCTNESS, EXAMPLE_FIT)


# --------------------------------------------------------------------------------------
# The outcome
# --------------------------------------------------------------------------------------


@dataclass(slots=True)
class StepResult:
    """Counts and cost for one sense-hygiene step.

    Attributes:
        name: The step this result belongs to.
        entries_scanned: Entries the step visited.
        entries_changed: Entries it actually wrote.
        groups_merged: Duplicate groups applied (``distinctness`` only) — one per group,
            whatever the number of senses in it.
        senses_retired: Senses marked retired as duplicates of a survivor.
        examples_moved: Canonical examples refiled under the sense they illustrate, each with
            its level renditions.
        examples_removed: Canonical examples taken out of a sense — either because they
            illustrate none of its senses, or because the sense they belong to already holds
            :data:`MAX_CANONICAL_EXAMPLES`. The text of each is preserved in a note first.
        senses_emptied: Senses this step left with no canonical example at all. A *report*, not
            an edit: ``retrofit --only repair`` regenerates them.
        rejected: Model answers refused — a group naming a ref that does not exist or spanning
            two parts of speech, a placement naming a sense or an example that was not listed.
        calls: Model calls made.
        cost_usd: What they cost.
        stopped_reason: ``None`` when the step ran to completion; ``"budget"`` when the run's
            ceiling was reached mid-step; ``"stopped"`` when the caller's stop event was set. A
            stopped step still reports everything it did, and everything it wrote is on disk.
    """

    name: str
    entries_scanned: int = 0
    entries_changed: int = 0
    groups_merged: int = 0
    senses_retired: int = 0
    examples_moved: int = 0
    examples_removed: int = 0
    senses_emptied: int = 0
    rejected: int = 0
    calls: int = 0
    cost_usd: float = 0.0
    stopped_reason: str | None = None

    @property
    def changed(self) -> int:
        """Return how many individual things this step changed.

        ``groups_merged`` is deliberately absent: it describes the same edits
        ``senses_retired`` counts, and adding both would count one merge twice.
        ``senses_emptied`` is absent because it is a report about an edit already counted.
        """
        return self.senses_retired + self.examples_moved + self.examples_removed

    def as_dict(self) -> dict[str, object]:
        """Return a JSON-able view for the run summary and the CLI."""
        return {
            "entries_scanned": self.entries_scanned,
            "entries_changed": self.entries_changed,
            "groups_merged": self.groups_merged,
            "senses_retired": self.senses_retired,
            "examples_moved": self.examples_moved,
            "examples_removed": self.examples_removed,
            "senses_emptied": self.senses_emptied,
            "rejected": self.rejected,
            "calls": self.calls,
            "cost_usd": round(self.cost_usd, 6),
            "stopped_reason": self.stopped_reason,
        }


@dataclass(slots=True)
class SenseHygieneOutcome:
    """What one :func:`run_sense_hygiene` sweep did, per step.

    Attributes:
        steps: One :class:`StepResult` per step that ran, keyed by step name.
        entries_changed: How many *distinct* entries were written across every step — not the
            sum of the per-step figures, which would count an entry twice when both steps
            touched it.
    """

    steps: dict[str, StepResult] = field(default_factory=dict)
    entries_changed: int = 0

    @property
    def cost_usd(self) -> float:
        """Return the total cost of every step that ran."""
        return sum(result.cost_usd for result in self.steps.values())

    @property
    def calls(self) -> int:
        """Return the total model calls made by every step that ran."""
        return sum(result.calls for result in self.steps.values())

    @property
    def stopped_reason(self) -> str | None:
        """Return why the run stopped early, or ``None`` if every selected step ran.

        A budget stop is reported here, not raised, so a caller's run summary can say "budget"
        rather than "completed" — the same convention ``run_retrofit`` follows.
        """
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
            "entries_changed": self.entries_changed,
            "calls": self.calls,
            "cost_usd": round(self.cost_usd, 6),
            "stopped_reason": self.stopped_reason,
            "steps": {name: result.as_dict() for name, result in self.steps.items()},
        }


# --------------------------------------------------------------------------------------
# Tally and pool driver
# --------------------------------------------------------------------------------------


class _Tally:
    """One step's counters, mutated only while holding an ``asyncio.Lock``.

    Mirrors ``content_hygiene._Tally`` and ``relation_hygiene._Tally``, both module-private.
    Single-threaded asyncio does make ``counter += 1`` atomic on its own, but these counters are
    touched by many handlers around many awaits and that guarantee is a property of the
    interpreter rather than of this code.

    Args:
        name: The step this tally belongs to.
        changed_ids: The run-level set of entry ids written by any step, shared so
            :attr:`SenseHygieneOutcome.entries_changed` counts distinct entries rather than
            entry-visits.
    """

    def __init__(self, name: str, changed_ids: set[str]) -> None:
        """Start an empty result for the named step."""
        self._lock = asyncio.Lock()
        self._result = StepResult(name=name)
        self._changed: set[str] = set()
        self._changed_ids = changed_ids
        self._visited = 0

    @property
    def result(self) -> StepResult:
        """Return the accumulated result; read it once the pool has drained."""
        return self._result

    async def entry(
        self,
        lexeme_id: str,
        *,
        groups_merged: int = 0,
        senses_retired: int = 0,
        examples_moved: int = 0,
        examples_removed: int = 0,
        senses_emptied: int = 0,
        rejected: int = 0,
    ) -> None:
        """Fold one visited entry into the step result.

        Args:
            lexeme_id: The entry visited.
            groups_merged: Duplicate groups applied on this entry.
            senses_retired: Senses retired on this entry.
            examples_moved: Examples refiled on this entry.
            examples_removed: Examples taken out of a sense on this entry.
            senses_emptied: Senses left with no canonical example on this entry.
            rejected: Model answers refused for this entry.
        """
        async with self._lock:
            result = self._result
            self._visited += 1
            result.entries_scanned += 1
            result.groups_merged += groups_merged
            result.senses_retired += senses_retired
            result.examples_moved += examples_moved
            result.examples_removed += examples_removed
            result.senses_emptied += senses_emptied
            result.rejected += rejected
            if senses_retired or examples_moved or examples_removed:
                self._changed.add(lexeme_id)
                self._changed_ids.add(lexeme_id)
                result.entries_changed = len(self._changed)
            if self._visited and self._visited % PROGRESS_EVERY == 0:
                _LOG.info(
                    "sense_hygiene_progress",
                    step=result.name,
                    entries_done=self._visited,
                    entries_changed=result.entries_changed,
                    calls=result.calls,
                    cost_usd=round(result.cost_usd, 6),
                )

    async def call(self, cost_usd: float) -> None:
        """Record one completed model call and what it cost."""
        async with self._lock:
            self._result.calls += 1
            self._result.cost_usd += cost_usd

    async def note_stop(self, reason: str) -> None:
        """Record why the step stopped early, keeping the first reason given."""
        async with self._lock:
            if self._result.stopped_reason is None:
                self._result.stopped_reason = reason


async def _drive(
    items: Sequence[str],
    handler: Callable[[str], Awaitable[None]],
    tally: _Tally,
    *,
    workers: int,
    stop_event: asyncio.Event | None,
) -> None:
    """Run one step's handler over ``items`` through the bounded pool.

    ``run_pool`` already treats :class:`BudgetExceededError` as a clean stop of the whole pool
    rather than an error to propagate, so this wrapper exists only to record *why* the step
    stopped before the exception is swallowed.

    Args:
        items: The entry ids to visit.
        handler: The per-item coroutine function.
        tally: The step tally, which learns the stop reason.
        workers: Pool size.
        stop_event: Shared stop event; set by a budget stop, and honoured if the caller sets it
            from outside.
    """

    async def guarded(item: str) -> None:
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
    """Return the zero-cost provenance record a rule-applied edit is stamped with.

    Args:
        note: Free text to preserve on the record — a retirement, a removed example, a marker.

    Returns:
        A :class:`~opengloss_generator.schema.Provenance` with every cost and token field at
        zero, so a naive sum over an entry's provenance table is unaffected by this pass having
        run.
    """
    return Provenance(
        stage=StageName.HYGIENE,
        model=DETERMINISTIC_MODEL,
        prompt_version=PROMPT_VERSION,
        cost_usd=0.0,
        attempts=0,
        note=note,
    )


def _normalised(text: str) -> str:
    """Return the comparison key for a piece of stored prose.

    Case- and whitespace-insensitive, and one trailing period is ignored, so "A cat." and "a cat"
    are the same text. Mirrors ``content_hygiene._normalised``.

    Args:
        text: The prose to key.

    Returns:
        The normalised key.
    """
    collapsed = " ".join(text.split()).strip().lower()
    return collapsed[:-1] if collapsed.endswith(".") else collapsed


def _one_line(text: str) -> str:
    """Return ``text`` with every run of whitespace collapsed, for a one-item-per-line list."""
    return " ".join(text.split())


def _forms_for(entry: Lexeme, pos_entry: POSEntry) -> list[str]:
    """Return every surface form to try when locating the headword in an example.

    The union of the model-supplied :class:`~opengloss_generator.schema.Morphology` forms and
    :func:`~opengloss_generator.spans.generate_forms`' rule-based ones. Mirrors
    ``content_hygiene._forms_for``, which is module-private there.

    Args:
        entry: The entry the example belongs to.
        pos_entry: The part-of-speech entry the example is being filed under.

    Returns:
        The candidate forms, de-duplicated, morphology first.
    """
    forms = [*pos_entry.morphology.inflected_forms(), *pos_entry.morphology.derivations]
    for generated in spans.generate_forms(entry.headword):
        if generated not in forms:
            forms.append(generated)
    return forms


def _live_senses(entry: Lexeme) -> list[tuple[POSEntry, Sense, str]]:
    """Return ``(pos_entry, sense, sense_id)`` for every non-retired sense."""
    return [triple for triple in entry.iter_senses() if not triple[1].retired]


@dataclass(slots=True)
class _SenseRef:
    """One live sense, as the model is shown it and as the answer refers back to it.

    Attributes:
        pos_entry: The owning part-of-speech entry — its identity is what decides whether two
            senses may be grouped, and its morphology is what places a headword in a moved
            example.
        sense: The sense itself, mutated in place when it is retired or merged onto.
        sense_id: The derived positional id (D-1), used as the digest ref and in every note.
        gloss: Its canonical definition, shown to the model.
        example: Its first canonical example, or ``""`` where it has none.
    """

    pos_entry: POSEntry
    sense: Sense
    sense_id: str
    gloss: str
    example: str


def _first_example(sense: Sense) -> str:
    """Return a sense's first canonical example text, or ``""`` if it has none."""
    canonical = sense.examples.canonical()
    return canonical.content.text if canonical is not None else ""


def _sense_refs(entry: Lexeme) -> list[_SenseRef]:
    """Return one :class:`_SenseRef` per live sense of an entry, in document order."""
    return [
        _SenseRef(
            pos_entry=pos_entry,
            sense=sense,
            sense_id=sid,
            gloss=sense.canonical_gloss(),
            example=_first_example(sense),
        )
        for pos_entry, sense, sid in _live_senses(entry)
    ]


def _collides(renditions: Renditions[Example], candidate: Rendition[Example]) -> bool:
    """Return whether an example set already holds this exact rendition.

    :class:`~opengloss_generator.schema.Renditions` keys an example rendition on
    ``(level, register, text)`` and refuses a repeat, so a candidate matching all three has to
    be skipped rather than added.

    Args:
        renditions: The destination example set.
        candidate: The rendition about to be added.

    Returns:
        Whether adding it would raise.
    """
    return any(
        existing.key == candidate.key and existing.content.text == candidate.content.text
        for existing in renditions
    )


def _relocated(
    entry: Lexeme, rendition: Rendition[Example], forms: Sequence[str]
) -> Rendition[Example]:
    """Return a deep copy of an example rendition with its span re-found for its new home.

    A deep copy, not the rendition itself: the source sense keeps everything it had when a
    duplicate is *merged* (nothing is deleted from a retired sense), and two senses sharing one
    mutable :class:`~opengloss_generator.schema.Example` object would make an edit to either
    show up on both.

    The text does not change, so the stored span is still in bounds — but the destination sense
    may sit under a different part of speech with different inflected forms, and re-finding
    costs nothing, so the offsets that end up stored are the ones measured against the text and
    the forms that are actually there.

    Args:
        entry: The entry the example stays inside.
        rendition: The rendition to copy.
        forms: The destination part of speech's candidate surface forms.

    Returns:
        The copy, ready to be added to the destination set.
    """
    copied = rendition.model_copy(deep=True)
    copied.content.span = spans.find_span(copied.content.text, entry.headword, forms)
    return copied


# --------------------------------------------------------------------------------------
# The D-47 marker
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Marker:
    """The most recent sentinel one step left on an entry.

    Attributes:
        digest: The set hash the marker was written for — the set as it stood *after* that
            attempt's answers were applied.
        attempts: How many attempts the step has made on this entry, this one included.
    """

    digest: str
    attempts: int


def _ref_digest(refs: Iterable[str]) -> str:
    """Return a stable short hash of the set a call answered for.

    Args:
        refs: Stable identifiers of the things in question, in any order.

    Returns:
        Sixteen hex characters of SHA-256 over the sorted, newline-joined refs. Sorted so the
        digest does not depend on document order, and SHA-256 rather than :func:`hash` because
        the value is written to disk and compared across processes.
    """
    joined = "\n".join(sorted(refs))
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:16]


def _latest_marker(entry: Lexeme, prefix: str) -> _Marker | None:
    """Return the last sentinel ``prefix``'s step wrote on an entry, parsed.

    Args:
        entry: The entry to inspect.
        prefix: The step's note prefix.

    Returns:
        The most recent marker, or ``None`` if the step has never visited the entry. Provenance
        ids are assigned in insertion order and never reused, so the last matching record in the
        table is the most recently written one.
    """
    latest: _Marker | None = None
    for record in entry.provenance_in_order():
        note = record.note or ""
        if not note.startswith(f"{prefix}:"):
            continue
        digest, _, attempts = note[len(prefix) + 1 :].partition(_ATTEMPTS_SEPARATOR)
        latest = _Marker(digest, int(attempts) if attempts.isdigit() else 1)
    return latest


def _attempt_number(entry: Lexeme, prefix: str, refs: Sequence[str]) -> int | None:
    """Return which attempt is due on an entry, or ``None`` if none is.

    An entry is due an attempt when it has something to judge and either the step has never
    visited it, or the set of things to judge has changed since the step last answered — and it
    has not already had :data:`MAX_ATTEMPTS` of them (D-47).

    Args:
        entry: The entry being considered.
        prefix: The step's note prefix.
        refs: Stable identifiers of what is judgeable *now*.

    Returns:
        The 1-based attempt number, or ``None`` when the entry must be skipped — which is also
        the "do not bill this" signal for the caller.
    """
    if not refs:
        return None
    marker = _latest_marker(entry, prefix)
    if marker is None:
        return 1
    if marker.digest == _ref_digest(refs) or marker.attempts >= MAX_ATTEMPTS:
        return None
    return marker.attempts + 1


def _marker_note(prefix: str, refs: Iterable[str], attempt: int) -> str:
    """Return the sentinel to stamp for an attempt, in D-47's form.

    Args:
        prefix: The step's note prefix.
        refs: The refs the marker is written for — the set as it stands *after* the attempt's
            answers were applied (see the module docstring).
        attempt: The 1-based attempt number.

    Returns:
        ``<prefix>:<digest>;attempts=<n>``.
    """
    return f"{prefix}:{_ref_digest(refs)}{_ATTEMPTS_SEPARATOR}{attempt}"


# --------------------------------------------------------------------------------------
# Step 1 — distinctness
# --------------------------------------------------------------------------------------
#
# The instructions and the output contract live here, not in prompts.py / contracts.py: those
# files are edited concurrently on this branch, and keeping every call site in this module
# self-contained means this work never conflicts with that. Nothing outside this module depends
# on the names below.


#: Instructions for this step's one nano call per entry. Byte-stable and well over the 1,024
#: tokens a provider prompt cache needs to match on, which is also what makes two sweeps'
#: numbers comparable. The bar is WordNet's and it is stated positively — what makes two senses
#: *different* — because a model asked only "are these duplicates?" will find duplicates.
DISTINCTNESS_INSTRUCTIONS = """\
You are auditing the sense inventory of one dictionary headword at a time. Each numbered item \
below is one sense of that headword: the part of speech it is filed under, its definition, and \
one of its example sentences where it has one. Your job is to say which of these senses, if \
any, are the SAME meaning written out twice.

THE BAR. Two senses are genuinely different when a learner would need a SEPARATE DEFINITION for \
each of them -- when knowing one of the two would not let the learner understand a sentence \
that uses the other. This is the bar a printed dictionary and WordNet both use, and it is much \
higher than "the two definitions are worded differently". Ask yourself: if the dictionary \
printed only the broader of the two definitions, would a reader who met the narrower use be \
misled or left stuck? If the answer is no, the two are one sense.

WHAT IS NOT A DISTINCTION. Four differences look like distinctions and are not:

- DOMAIN COLOURING. The same meaning used in a particular field, religion, sport, trade or \
subject is not a second sense. "A solemn promise" and "a solemn religious promise made to God" \
are one meaning; the second is the first, in church. The same goes for a legal, medical, \
military or nautical shading of an ordinary meaning.
- REGISTER OR TONE. Formal, informal, slang, poetic or old-fashioned wordings of one meaning \
are one sense. How a word is said is not what it means.
- SPECIFIC VERSUS GENERIC PHRASING. One definition naming a typical instance and another \
naming the general case are one sense when the instance is simply an example of the case: "a \
tool for cutting wood" and "a tool for cutting" are not two meanings of one word unless the \
word genuinely cannot be used for the general case.
- BREADTH OF WORDING ALONE. One definition being longer, more careful, or listing more of the \
things the word covers does not make it a second sense. Neither does one of them mentioning a \
typical user, place or occasion.

WHAT IS A DISTINCTION. Two senses are different when they point at different things in the \
world (the edge of a river and a place that keeps money), when one is concrete and the other is \
an established figurative meaning that a learner would not guess from the first, when they take \
different objects or complements in a sentence, or when they would be translated by different \
words in another language. Being related is not the same as being identical: a process and its \
result, an act and the person who performs it, a material and an object made of it -- these are \
regularly separate senses and must be left alone.

NEVER GROUP ACROSS PARTS OF SPEECH. Two senses filed under different parts of speech are never \
the same sense, however similar the definitions read. A noun sense and a verb sense of the same \
word are separate entries in every dictionary and must stay separate here.

BE CONSERVATIVE. A group you report causes one of the senses to be retired from the dictionary. \
When you are not sure, do not group. A dictionary with one sense too many is a smaller problem \
than a dictionary that has lost a meaning its readers were looking for.

ANSWER FORMAT. Report groups of senses that are the same meaning, each group as the list of \
numbers those senses were listed under. A group has at least two members, and a sense belongs \
to at most one group. When three senses are all the same meaning, report one group of three \
rather than several pairs. When every sense listed is distinct -- which is the ordinary case -- \
report no groups at all.

WORKED EXAMPLE. For the headword "vow":

  1. [noun] A solemn promise or undertaking. | example: She made a vow to return.
  2. [noun] A solemn promise made to God or a saint, binding the maker to an act or way of \
life. | example: The monk took a vow of silence.
  3. [verb] To promise solemnly that one will do something. | example: He vowed to fight on.

The answer is one group: senses 1 and 2. Sense 2 is sense 1 with religious colouring on it -- a \
reader who knows "a solemn promise" understands "a vow of silence" without being told anything \
more, so the dictionary does not need a second definition. Sense 3 is not in the group: it is a \
verb, and a verb sense is never grouped with a noun sense.

For the headword "bank":

  1. [noun] The land alongside a river or lake. | example: We sat on the bank and watched.
  2. [noun] An organisation that keeps people's money and lends it out. | example: The bank \
approved the loan.

The answer is no groups at all. These two senses share nothing but their spelling: a reader who \
knows one of them cannot understand a sentence using the other, so both definitions have to be \
printed."""


class _DraftDuplicateGroups(BaseModel):
    """The groups of same-meaning senses on one entry, produced together."""

    model_config = ConfigDict(
        extra="forbid", validate_by_name=True, validate_by_alias=True, serialize_by_alias=True
    )

    duplicate_groups: list[list[Annotated[int, Field(ge=1)]]]


@dataclass(slots=True)
class _GroupCounts:
    """What one entry's ``distinctness`` call did.

    Attributes:
        answered: Whether a call actually completed for this entry. A failed call leaves no
            marker, so the entry is tried again on the next sweep.
        groups_merged: Duplicate groups applied.
        senses_retired: Senses retired across those groups.
        rejected: Groups refused — a bad ref, fewer than two usable members, or members spanning
            two parts of speech.
    """

    answered: bool = False
    groups_merged: int = 0
    senses_retired: int = 0
    rejected: int = 0


def _polysemous_under_one_pos(entry: Lexeme) -> bool:
    """Return whether some part of speech carries two or more live senses.

    The gate for this step: a headword whose senses are spread one-per-part-of-speech has
    nothing this question can be asked about, and asking anyway would be the largest single
    slice of a core sweep spent on a foregone answer.

    Args:
        entry: The entry to inspect. Never mutated.

    Returns:
        Whether any part-of-speech entry holds at least :data:`_MIN_SENSES` non-retired senses.
    """
    counts: dict[str, int] = {}
    for pos_entry, _, _ in _live_senses(entry):
        counts[pos_entry.pos.value] = counts.get(pos_entry.pos.value, 0) + 1
    return any(count >= _MIN_SENSES for count in counts.values())


def _distinctness_refs(entry: Lexeme) -> list[_SenseRef]:
    """Return the senses this step lists for an entry, or ``[]`` when it lists none.

    Every live sense is listed once the gate is passed, not only the senses of the polysemous
    part of speech: the model is being asked which of the entry's meanings coincide, and showing
    it the whole inventory is what lets it see that a sense it might otherwise group is filed
    somewhere else.

    Args:
        entry: The entry to inspect. Never mutated.

    Returns:
        One ref per live sense in document order, or an empty list when no part of speech
        carries two.
    """
    if not _polysemous_under_one_pos(entry):
        return []
    return _sense_refs(entry)


def _build_distinctness_prompt(headword: str, refs: Sequence[_SenseRef]) -> str:
    """Return the volatile half of this step's prompt.

    Args:
        headword: The lexeme's surface form.
        refs: The senses, in the order the model should answer about them — a group member in
            the reply is a 1-based position into this sequence.

    Returns:
        The per-call prompt body.
    """
    lines = [
        f"  {position}. [{ref.pos_entry.pos.value}] {_one_line(ref.gloss)} "
        f"| example: {_one_line(ref.example) or _NO_EXAMPLE}"
        for position, ref in enumerate(refs, start=1)
    ]
    listed = "\n".join(lines)
    return f"Headword: {headword}\nSenses ({len(refs)}):\n{listed}"


def _merge_examples(entry: Lexeme, survivor: _SenseRef, donor: _SenseRef) -> None:
    """Copy onto the survivor every example it does not already carry.

    Canonical examples are merged on their *text*: one the survivor already has under any
    wording that normalises the same way is not copied twice. Level renditions are merged on
    their ``(level, register)`` key: the survivor keeps its own rendition for a target it
    already has one for, and gains the donor's for a target it has none for.

    Args:
        entry: The entry both senses belong to, mutated in place.
        survivor: The sense that stays live.
        donor: The sense about to be retired. Never mutated — a retired sense keeps everything
            it had.
    """
    forms = _forms_for(entry, survivor.pos_entry)
    seen = {
        _normalised(rendition.content.text)
        for rendition in survivor.sense.examples
        if rendition.is_canonical
    }
    for rendition in donor.sense.examples:
        if rendition.is_canonical:
            key = _normalised(rendition.content.text)
            if key in seen:
                continue
            seen.add(key)
        elif survivor.sense.examples.has(*rendition.key):
            continue
        copied = _relocated(entry, rendition, forms)
        if _collides(survivor.sense.examples, copied):
            continue
        survivor.sense.examples.add(copied)


def _merge_relations(survivor: _SenseRef, donor: _SenseRef) -> None:
    """Copy onto the survivor every relation it does not already assert.

    Identity is ``(type, target lexeme)`` rather than the whole relation: two senses of one
    headword routinely name the same target with different surface wording, and the second copy
    would be a duplicate edge, which is what ``graph_hygiene`` exists to prevent.

    Args:
        survivor: The sense that stays live, mutated in place.
        donor: The sense about to be retired. Never mutated.
    """
    present = {(relation.type, relation.target.lexeme_id) for relation in survivor.sense.relations}
    for relation in donor.sense.relations:
        key = (relation.type, relation.target.lexeme_id)
        if key in present:
            continue
        present.add(key)
        survivor.sense.relations.append(relation.model_copy(deep=True))


def _merge_gloss_renditions(survivor: _SenseRef, donor: _SenseRef) -> None:
    """Copy onto the survivor every gloss rendition target it has no rendition for.

    The canonical gloss is never copied: the survivor's own definition is the one the merged
    sense is being folded into, and replacing it would be a rewrite rather than a merge.

    Args:
        survivor: The sense that stays live, mutated in place.
        donor: The sense about to be retired. Never mutated.
    """
    for rendition in donor.sense.gloss:
        if rendition.is_canonical or survivor.sense.gloss.has(*rendition.key):
            continue
        survivor.sense.gloss.add(rendition.model_copy(deep=True))


def _group_members(refs: Sequence[_SenseRef], group: Sequence[int]) -> list[_SenseRef] | None:
    """Return the senses one reported group names, or ``None`` if the group is unusable.

    A group is refused whole rather than in part: it is one claim about a set of senses, and
    acting on the half of it that parses would be acting on a claim the model did not make.

    Args:
        refs: The senses as they were listed.
        group: The 1-based positions the model grouped.

    Returns:
        The named senses in listing order, or ``None`` when a position is out of range, a named
        sense has already been retired by an earlier group in the same answer, fewer than two
        distinct senses are named, or the members span more than one part of speech.
    """
    members: list[_SenseRef] = []
    for raw in dict.fromkeys(group):
        position = raw - 1
        if not 0 <= position < len(refs):
            return None
        member = refs[position]
        if member.sense.retired:
            return None
        members.append(member)
    if len(members) < _MIN_SENSES:
        return None
    if len({id(member.pos_entry) for member in members}) != 1:
        return None
    return members


def _apply_group(entry: Lexeme, refs: Sequence[_SenseRef], group: Sequence[int]) -> int:
    """Merge one reported group onto its lowest-indexed member and retire the rest.

    Args:
        entry: The entry the senses belong to, mutated in place.
        refs: The senses as they were listed.
        group: The 1-based positions the model grouped.

    Returns:
        How many senses were retired, or ``0`` when the group was refused.
    """
    members = _group_members(refs, group)
    if members is None:
        _LOG.info(
            "sense_hygiene_group_refused",
            headword=entry.headword,
            group=list(group),
            listed=len(refs),
        )
        return 0

    survivor = min(members, key=lambda member: member.sense.index)
    retired = 0
    for member in members:
        if member is survivor:
            continue
        _merge_examples(entry, survivor, member)
        _merge_relations(survivor, member)
        _merge_gloss_renditions(survivor, member)
        member.sense.retired = True
        entry.add_provenance(
            _rule_provenance(
                RETIRED_SENSE_NOTE.format(retired=member.sense_id, survivor=survivor.sense_id)
            )
        )
        retired += 1
        _LOG.debug(
            "sense_hygiene_sense_retired",
            headword=entry.headword,
            retired=member.sense_id,
            survivor=survivor.sense_id,
        )
    return retired


async def _decide_distinctness(
    entry: Lexeme, refs: Sequence[_SenseRef], runner: StageRunner, tally: _Tally
) -> _GroupCounts:
    """Ask nano which of an entry's senses are the same meaning, and merge the ones that are.

    Args:
        entry: The entry whose senses are being judged, mutated in place.
        refs: The senses, in the order the model is shown them.
        runner: The stage runner.
        tally: The step tally, for the call and its cost.

    Returns:
        The counts for this entry.

    Raises:
        BudgetExceededError: A budget stop is a run-level condition and propagates.
    """
    counts = _GroupCounts()
    try:
        stage_result = await runner.run(
            # Reuses hygiene's model policy (nano): this is a structural verdict about two
            # definitions, not prose for an audience.
            stage=StageName.HYGIENE,
            output_type=_DraftDuplicateGroups,
            instructions=DISTINCTNESS_INSTRUCTIONS,
            prompt=_build_distinctness_prompt(entry.headword, refs),
            prompt_version=PROMPT_VERSION,
        )
    except BudgetExceededError:
        raise
    except GenerationError as exc:
        _LOG.warning("sense_hygiene_distinctness_failed", headword=entry.headword, error=str(exc))
        return counts

    await tally.call(stage_result.cost_usd)
    entry.add_provenance(stage_result.provenance)
    counts.answered = True
    for group in stage_result.output.duplicate_groups:
        retired = _apply_group(entry, refs, group)
        if retired:
            counts.groups_merged += 1
            counts.senses_retired += retired
        else:
            counts.rejected += 1
    return counts


async def _distinctness_step(
    store: LexemeStore,
    runner: StageRunner,
    ids: Sequence[str],
    *,
    workers: int,
    stop_event: asyncio.Event | None,
    changed_ids: set[str],
) -> StepResult:
    """Merge every group of senses that say the same thing, one nano call per entry.

    Args:
        store: The store to clean. Each entry is read, judged — including its one call when an
            attempt is due — and written inside one hold of its own lock.
        runner: The stage runner.
        ids: The entry ids to visit.
        workers: Pool size.
        stop_event: Shared stop event.
        changed_ids: Run-level set of entries written by any step.

    Returns:
        The step's :class:`StepResult`.
    """
    tally = _Tally(SenseHygieneStep.DISTINCTNESS, changed_ids)

    async def merge(lexeme_id: str) -> None:
        counts = _GroupCounts()
        async with store.locked(lexeme_id):
            entry = store.read(lexeme_id)
            if entry is None:
                return
            refs = _distinctness_refs(entry)
            attempt = _attempt_number(entry, _DISTINCTNESS_PREFIX, [ref.sense_id for ref in refs])
            if attempt is not None:
                counts = await _decide_distinctness(entry, refs, runner, tally)
                if counts.answered:
                    # The digest is over the sense set as the merges leave it, and it is
                    # recomputed exactly the way the next sweep will compute it — so an entry
                    # nothing changed on is free next time, and one that gains a sense is not.
                    surviving = [ref.sense_id for ref in _distinctness_refs(entry)]
                    entry.add_provenance(
                        _rule_provenance(_marker_note(_DISTINCTNESS_PREFIX, surviving, attempt))
                    )
                    # Written even when nothing merged: the marker is the only thing that call
                    # bought, and losing it re-bills the same answer.
                    store.write(entry)
        await tally.entry(
            lexeme_id,
            groups_merged=counts.groups_merged,
            senses_retired=counts.senses_retired,
            rejected=counts.rejected,
        )

    await _drive(ids, merge, tally, workers=workers, stop_event=stop_event)
    return tally.result


# --------------------------------------------------------------------------------------
# Step 2 — example_fit
# --------------------------------------------------------------------------------------


#: Instructions for this step's one nano call per entry. Byte-stable, for the reason
#: :data:`DISTINCTNESS_INSTRUCTIONS` is.
EXAMPLE_FIT_INSTRUCTIONS = """\
You are checking where a dictionary has filed its example sentences. You are given every sense \
of one headword, numbered, and every example sentence the dictionary holds for that headword, \
also numbered, each marked with the sense it is currently filed under. For each example \
sentence, say which of the listed senses it actually illustrates.

WHAT AN EXAMPLE IS FOR. An example sentence exists to show a reader the word being used in one \
particular meaning. It illustrates a sense when a reader who has just read that sense's \
definition would recognise the sentence as an instance of it, and would come away with the \
definition confirmed rather than muddled. The question is not which sense the sentence is \
loosely about, nor which sense is most common: it is which definition the sentence actually \
uses.

HOW TO DECIDE. Read the sentence, work out what the headword means in it, and match that \
against the definitions in front of you. Do not be swayed by the topic of the sentence, by \
which sense the example is currently filed under, or by any wording the sentence shares with a \
definition. A sentence about churches does not illustrate a religious sense unless the headword \
itself is used religiously in it; a sentence about a race does not illustrate a sporting sense \
unless the word means the sporting thing there.

THE PART OF SPEECH IS PART OF THE ANSWER. Each sense is labelled with its part of speech. A \
sentence that uses the headword as a noun cannot illustrate a verb sense, and a sentence that \
uses it as a verb cannot illustrate a noun sense, however well the subject matter matches. This \
is a common filing error and it is one you are being asked to catch: when the only sense the \
sentence could otherwise fit is under the wrong part of speech, and no sense under the right \
part of speech fits it, the answer is that it fits none of them.

WHEN THE ANSWER IS "NONE". Answer that an example fits no listed sense when the headword is \
used in a meaning none of the definitions covers, when the sentence uses the word under a part \
of speech no listed sense has, when the headword does not appear in the sentence at all, or \
when the sentence is not a usable example of anything -- a fragment, a definition restated, or \
a line of debris. Do not answer "none" merely because the fit is imperfect: an example that \
illustrates a sense clumsily still illustrates it, and answering "none" removes it from the \
dictionary.

THE COMMON ANSWER IS "WHERE IT IS". Most examples are filed correctly. When the sentence \
illustrates the sense it is already filed under, say so by naming that same sense. That is a \
normal answer, not a failure to find something. Only name a different sense when the sentence \
genuinely uses that other definition.

WHAT NOT TO DO. Do not rewrite a sentence, do not invent a sense for one that fits nothing, \
and do not judge how well a sentence is written: an example in a stilted or bookish register \
still illustrates whichever sense it illustrates, and a different pass repairs its wording. Do \
not move an example in order to spread the examples evenly across the senses -- one sense \
holding four good examples while another holds none is a fact about how the dictionary was \
written, not a filing error, and the empty sense is refilled elsewhere. Judge each sentence on \
its own: what you answer for one sentence must not change what you answer for another, and two \
sentences may perfectly well illustrate the same sense.

ANSWER FORMAT. Give one answer for every example sentence you were shown, identified by the \
number it was listed under, and give the sense as the number that sense was listed under, or \
null for "fits none of them". Answer nothing else.

WORKED EXAMPLE. For the headword "vow", with these senses:

  1. [noun] A solemn promise or undertaking.
  2. [noun] A solemn promise made to God, binding the maker to a religious way of life.
  3. [verb] To promise solemnly that one will do something.

and these examples:

  1. (sense 2) He broke his vow to his sister about the broken toy.
  2. (sense 2) The monk took a vow of poverty when he entered the order.
  3. (sense 1) She vowed to finish the race before sunset.
  4. (sense 1) The vow was of an ancient and solemn character in the extreme.

The answers are: 1 belongs to sense 1 -- it is an ordinary promise between two people, with \
nothing religious in it, so it is filed one sense too far. 2 belongs to sense 2, where it \
already is -- a monastic vow of poverty is exactly the religious sense. 3 belongs to sense 3 -- \
"vowed" is the verb here, and a verb use cannot illustrate a noun sense however well the \
meaning matches. 4 fits none of them -- it restates the definition rather than using the word \
in a situation, so no reader would learn anything from it."""


class _DraftPlacement(BaseModel):
    """Where one example sentence actually belongs."""

    model_config = ConfigDict(
        extra="forbid", validate_by_name=True, validate_by_alias=True, serialize_by_alias=True
    )

    example_ref: Annotated[int, Field(ge=1)]
    best_sense_ref: Annotated[int, Field(ge=1)] | None


class _DraftExamplePlacements(BaseModel):
    """Placements for every canonical example of one entry, produced together."""

    model_config = ConfigDict(
        extra="forbid", validate_by_name=True, validate_by_alias=True, serialize_by_alias=True
    )

    placements: Annotated[list[_DraftPlacement], Field(min_length=1)]


@dataclass(slots=True)
class _ExampleRef:
    """One canonical example and the level renditions that belong with it.

    Attributes:
        sense_position: The 1-based position, in the prompt's sense list, of the sense the
            example is currently filed under.
        sense_ref: That sense.
        group: The canonical rendition first, then its level renditions — the
            ``(level, register)`` siblings at the same position within their own key's members.
            They move and are removed together, because a level rendition of an example whose
            canonical has gone illustrates nothing.
        text: The canonical example's text, shown to the model and preserved in a note if the
            example is removed.
        ref_id: The digest ref — the owning sense plus the normalised text, so the same sentence
            under a different sense is a different ref and a move registers as a change.
        settled: Whether a placement has already been applied to this example. A second
            placement naming it is refused rather than applied to renditions that have moved.
    """

    sense_position: int
    sense_ref: _SenseRef
    group: list[Rendition[Example]]
    text: str
    ref_id: str
    settled: bool = False


@dataclass(slots=True)
class _FitCounts:
    """What one entry's ``example_fit`` call did.

    Attributes:
        answered: Whether a call actually completed for this entry.
        moved: Examples refiled under another sense.
        removed: Examples taken out of a sense — no room at the destination, or fitting none.
        emptied: Senses this call left with no canonical example at all.
        rejected: Placements refused — a bad example ref, a bad sense ref, or a second answer
            about an example already settled.
    """

    answered: bool = False
    moved: int = 0
    removed: int = 0
    emptied: int = 0
    rejected: int = 0


def _canonical_groups(sense: Sense) -> list[list[Rendition[Example]]]:
    """Return one group of renditions per canonical example of a sense.

    An example rendition set holds N canonical sentences and, for each other
    ``(level, register)`` target, up to N renditions of them. Nothing in the schema links a
    rendition to the canonical it renders — several examples may share one key, so there is no
    keyed id to point with (see :meth:`~opengloss_generator.schema.Lexeme.rendition_ids`) — so
    the link is **position within the key**: the k-th canonical goes with the k-th rendition of
    each other target, which is the order the renditions workflow writes them in.

    Args:
        sense: The sense to group. Never mutated.

    Returns:
        One list per canonical example, canonical first, in document order.
    """
    by_key: dict[tuple[ReadingLevel, Register], list[Rendition[Example]]] = {}
    for rendition in sense.examples:
        by_key.setdefault(rendition.key, []).append(rendition)
    groups: list[list[Rendition[Example]]] = []
    for position, canonical in enumerate(by_key.get(CANONICAL_KEY, [])):
        group = [canonical]
        for key, members in by_key.items():
            if key == CANONICAL_KEY:
                continue
            if position < len(members):
                group.append(members[position])
        groups.append(group)
    return groups


def _example_fit_refs(entry: Lexeme, senses: Sequence[_SenseRef]) -> list[_ExampleRef]:
    """Return every canonical example of an entry, in the order the model is shown them.

    Args:
        entry: The entry the examples belong to. Never mutated.
        senses: Its live senses, in listing order.

    Returns:
        One ref per canonical example, senses in listing order.
    """
    del entry  # the refs are built entirely from the senses; kept for call-site symmetry
    refs: list[_ExampleRef] = []
    for position, sense_ref in enumerate(senses, start=1):
        for group in _canonical_groups(sense_ref.sense):
            text = group[0].content.text
            refs.append(
                _ExampleRef(
                    sense_position=position,
                    sense_ref=sense_ref,
                    group=group,
                    text=text,
                    ref_id=f"{sense_ref.sense_id}|{_normalised(text)}",
                )
            )
    return refs


def _example_fit_senses(entry: Lexeme) -> list[_SenseRef]:
    """Return the senses this step lists, or ``[]`` when the entry has fewer than two.

    The gate here is looser than ``distinctness``' — any two live senses, under any parts of
    speech — because the defect this step repairs crosses parts of speech: a noun use filed
    under a verb sense is one of the shapes the judge measured, and it can only be seen when
    both are on the table.

    Args:
        entry: The entry to inspect. Never mutated.

    Returns:
        One ref per live sense in document order, or an empty list.
    """
    refs = _sense_refs(entry)
    return refs if len(refs) >= _MIN_SENSES else []


def _build_example_fit_prompt(
    headword: str, senses: Sequence[_SenseRef], examples: Sequence[_ExampleRef]
) -> str:
    """Return the volatile half of this step's prompt.

    Two lists, each numbered from one, exactly as the QA prompt does it: a sense ref and an
    example ref are different kinds of thing and share no numbering.

    Args:
        headword: The lexeme's surface form.
        senses: The senses, in the order ``best_sense_ref`` indexes.
        examples: The canonical examples, in the order ``example_ref`` indexes.

    Returns:
        The per-call prompt body.
    """
    sense_lines = "\n".join(
        f"  {position}. [{ref.pos_entry.pos.value}] {_one_line(ref.gloss)}"
        for position, ref in enumerate(senses, start=1)
    )
    example_lines = "\n".join(
        f"  {position}. (sense {ref.sense_position}) {_one_line(ref.text)}"
        for position, ref in enumerate(examples, start=1)
    )
    return (
        f"Headword: {headword}\n"
        f"Senses ({len(senses)}):\n{sense_lines}\n"
        f"Examples ({len(examples)}):\n{example_lines}"
    )


def _remove_group(entry: Lexeme, ref: _ExampleRef, note_prefix: str) -> None:
    """Take one example and its level renditions out of a sense, keeping every text in a note.

    The only deletion this pass performs, and it is not a loss: each removed text is written to
    a zero-cost ``Provenance.note`` before the rendition comes out, so the entry still carries
    what was there.

    Args:
        entry: The entry, mutated in place.
        ref: The example to remove.
        note_prefix: Which of the two removals this is — :data:`MOVED_OUT_NOTE` or
            :data:`REMOVED_EXAMPLE_NOTE`.
    """
    for rendition in ref.group:
        entry.add_provenance(_rule_provenance(f"{note_prefix}{rendition.content.text}"))
    sense = ref.sense_ref.sense
    # Selected by identity, not by value: two example renditions can hold equal content, and
    # ``list.remove``/``in`` would then take out the wrong one.
    sense.examples.root = [
        rendition
        for rendition in sense.examples
        if not any(rendition is doomed for doomed in ref.group)
    ]


def _move_group(entry: Lexeme, ref: _ExampleRef, destination: _SenseRef) -> None:
    """Refile one example and its level renditions under the sense it illustrates.

    Args:
        entry: The entry both senses belong to, mutated in place.
        ref: The example to move.
        destination: The sense it belongs under.
    """
    forms = _forms_for(entry, destination.pos_entry)
    for rendition in ref.group:
        copied = _relocated(entry, rendition, forms)
        if _collides(destination.sense.examples, copied):
            continue
        destination.sense.examples.add(copied)
    sense = ref.sense_ref.sense
    sense.examples.root = [
        rendition
        for rendition in sense.examples
        if not any(rendition is doomed for doomed in ref.group)
    ]
    _LOG.debug(
        "sense_hygiene_example_moved",
        headword=entry.headword,
        source=ref.sense_ref.sense_id,
        destination=destination.sense_id,
    )


def _apply_placement(
    entry: Lexeme,
    senses: Sequence[_SenseRef],
    examples: Sequence[_ExampleRef],
    drafted: _DraftPlacement,
    canonical_counts: dict[str, int],
) -> str:
    """Act on one placement, and return what it did.

    Args:
        entry: The entry, mutated in place.
        senses: The senses as they were listed.
        examples: The examples as they were listed.
        drafted: The model's answer for one of them.
        canonical_counts: Live canonical-example count per sense id, updated in place, so the
            :data:`MAX_CANONICAL_EXAMPLES` cap sees the moves already made by this same call.

    Returns:
        ``"moved"``, ``"removed"``, ``"kept"`` (already where it belongs) or ``"refused"``.
    """
    position = drafted.example_ref - 1
    if not 0 <= position < len(examples):
        return "refused"
    ref = examples[position]
    best = drafted.best_sense_ref
    destination = senses[best - 1] if best is not None and 0 <= best - 1 < len(senses) else None
    if ref.settled or (best is not None and destination is None):
        return "refused"
    source = ref.sense_ref.sense_id
    if destination is not None and destination.sense_id == source:
        return "kept"

    ref.settled = True
    if destination is None:
        _remove_group(entry, ref, REMOVED_EXAMPLE_NOTE)
    elif canonical_counts[destination.sense_id] >= MAX_CANONICAL_EXAMPLES:
        # The sense it belongs to already has enough examples. Piling a fourth on would be a
        # different defect, so the text is kept in a note and the wrong filing is undone.
        _remove_group(entry, ref, MOVED_OUT_NOTE)
    else:
        _move_group(entry, ref, destination)
        canonical_counts[source] -= 1
        canonical_counts[destination.sense_id] += 1
        return "moved"
    canonical_counts[source] -= 1
    return "removed"


async def _place_examples(
    entry: Lexeme,
    senses: Sequence[_SenseRef],
    examples: Sequence[_ExampleRef],
    runner: StageRunner,
    tally: _Tally,
) -> _FitCounts:
    """Ask nano which sense each example illustrates, and refile the ones that are misplaced.

    Args:
        entry: The entry whose examples are being placed, mutated in place.
        senses: Its live senses, in the order the model is shown them.
        examples: Its canonical examples, in the order the model is shown them.
        runner: The stage runner.
        tally: The step tally, for the call and its cost.

    Returns:
        The counts for this entry.

    Raises:
        BudgetExceededError: A budget stop is a run-level condition and propagates.
    """
    counts = _FitCounts()
    try:
        stage_result = await runner.run(
            # Reuses hygiene's model policy (nano): matching a sentence to a definition is a
            # structural verdict, not prose for an audience.
            stage=StageName.HYGIENE,
            output_type=_DraftExamplePlacements,
            instructions=EXAMPLE_FIT_INSTRUCTIONS,
            prompt=_build_example_fit_prompt(entry.headword, senses, examples),
            prompt_version=PROMPT_VERSION,
        )
    except BudgetExceededError:
        raise
    except GenerationError as exc:
        _LOG.warning("sense_hygiene_example_fit_failed", headword=entry.headword, error=str(exc))
        return counts

    await tally.call(stage_result.cost_usd)
    entry.add_provenance(stage_result.provenance)
    counts.answered = True

    before = {ref.sense_id: len(_canonical_groups(ref.sense)) for ref in senses}
    canonical_counts = dict(before)
    for drafted in stage_result.output.placements:
        outcome = _apply_placement(entry, senses, examples, drafted, canonical_counts)
        if outcome == "moved":
            counts.moved += 1
        elif outcome == "removed":
            counts.removed += 1
        elif outcome == "refused":
            counts.rejected += 1
    # Reported, never repaired here: ``retrofit --only repair`` step (b) writes canonical
    # examples for exactly this condition, and duplicating it would spend twice.
    counts.emptied = sum(1 for sid, count in canonical_counts.items() if count <= 0 < before[sid])
    if counts.emptied:
        _LOG.info(
            "sense_hygiene_senses_emptied",
            headword=entry.headword,
            senses=counts.emptied,
            hint="run retrofit --only repair",
        )
    return counts


async def _example_fit_step(
    store: LexemeStore,
    runner: StageRunner,
    ids: Sequence[str],
    *,
    workers: int,
    stop_event: asyncio.Event | None,
    changed_ids: set[str],
) -> StepResult:
    """Refile every example that illustrates a sense other than the one it sits under.

    Args:
        store: The store to clean. Each entry is read, placed — including its one call when an
            attempt is due — and written inside one hold of its own lock.
        runner: The stage runner.
        ids: The entry ids to visit.
        workers: Pool size.
        stop_event: Shared stop event.
        changed_ids: Run-level set of entries written by any step.

    Returns:
        The step's :class:`StepResult`.
    """
    tally = _Tally(SenseHygieneStep.EXAMPLE_FIT, changed_ids)

    async def place(lexeme_id: str) -> None:
        counts = _FitCounts()
        async with store.locked(lexeme_id):
            entry = store.read(lexeme_id)
            if entry is None:
                return
            senses = _example_fit_senses(entry)
            examples = _example_fit_refs(entry, senses)
            attempt = _attempt_number(entry, _EXAMPLE_FIT_PREFIX, [ref.ref_id for ref in examples])
            if attempt is not None:
                counts = await _place_examples(entry, senses, examples, runner, tally)
                if counts.answered:
                    # Recomputed the way the next sweep will compute it, over the example set as
                    # the moves and removals leave it: a moved example is a new ref under its
                    # new sense, so a settled entry is free and a changed one is not.
                    settled = _example_fit_senses(entry)
                    surviving = [ref.ref_id for ref in _example_fit_refs(entry, settled)]
                    entry.add_provenance(
                        _rule_provenance(_marker_note(_EXAMPLE_FIT_PREFIX, surviving, attempt))
                    )
                    # Written even when nothing moved: the marker is the only thing that call
                    # bought, and losing it re-bills the same answer.
                    store.write(entry)
        await tally.entry(
            lexeme_id,
            examples_moved=counts.moved,
            examples_removed=counts.removed,
            senses_emptied=counts.emptied,
            rejected=counts.rejected,
        )

    await _drive(ids, place, tally, workers=workers, stop_event=stop_event)
    return tally.result


# --------------------------------------------------------------------------------------
# The entry point
# --------------------------------------------------------------------------------------


#: One step, as :func:`run_sense_hygiene` calls it. Every step takes the store, the runner and
#: the id list positionally and the pool settings by keyword.
type _StepFn = Callable[..., Awaitable[StepResult]]

_STEP_FUNCTIONS: dict[str, _StepFn] = {
    SenseHygieneStep.DISTINCTNESS: _distinctness_step,
    SenseHygieneStep.EXAMPLE_FIT: _example_fit_step,
}


async def run_sense_hygiene(
    store: LexemeStore,
    runner: StageRunner,
    *,
    workers: int,
    stop_event: asyncio.Event | None = None,
    only: set[str] | None = None,
    lexeme_ids: Sequence[str] | None = None,
) -> SenseHygieneOutcome:
    """Merge near-duplicate senses and refile examples under the sense they illustrate.

    Two steps, described in full in the module docstring. ``distinctness`` retires a sense that
    says what a lower-indexed sense already says, after merging onto that survivor everything it
    lacked; ``example_fit`` moves a canonical example — with its level renditions — to the sense
    it actually illustrates, or takes it out with its text preserved in a note when it
    illustrates none of them. Sense ids are positional and are never renumbered (D-1), no sense
    is ever deleted, and every entry is read and written inside one hold of its own lock.

    A sense left with no canonical example is *reported* as ``senses_emptied``, not repaired:
    ``workflows/retrofit.py``'s ``repair`` pass step (b) already regenerates canonical examples
    for exactly that condition, so run ``retrofit --only repair`` after this workflow. This
    function does not call it.

    Args:
        store: The store to repair.
        runner: The stage runner. Both steps make one nano call per qualifying entry on the
            ``HYGIENE`` policy; an entry with a single live sense never costs anything.
        workers: Pool size for every step.
        stop_event: Shared stop event. A budget stop sets it; a caller may also set it from
            outside (the CLI passes its session's event, which ``SIGINT`` sets).
        only: Step names to run; defaults to all of :attr:`SenseHygieneStep.ALL`, in that order.
            Steps run in :attr:`SenseHygieneStep.ALL`'s order whatever order they are given in.
        lexeme_ids: Ids to visit; defaults to every id in the store, sorted.

    Returns:
        A :class:`SenseHygieneOutcome` carrying counts and cost per step. If a step stopped
        early its ``stopped_reason`` says why and the remaining steps are skipped; the outcome is
        still returned rather than raised, so a partial run reports what it managed to do.

    Raises:
        ValueError: If ``only`` names a step that does not exist.
    """
    selected = set(only) if only is not None else set(SenseHygieneStep.ALL)
    unknown = sorted(selected - set(SenseHygieneStep.ALL))
    if unknown:
        raise ValueError(f"unknown sense hygiene step(s): {unknown}")

    ids = list(lexeme_ids) if lexeme_ids is not None else sorted(store.iter_ids())
    outcome = SenseHygieneOutcome()
    changed_ids: set[str] = set()

    for name in SenseHygieneStep.ALL:
        if name not in selected:
            continue
        result = await _STEP_FUNCTIONS[name](
            store,
            runner,
            ids,
            workers=workers,
            stop_event=stop_event,
            changed_ids=changed_ids,
        )
        outcome.steps[name] = result
        if result.stopped_reason is not None:
            _LOG.warning(
                "sense_hygiene_step_stopped",
                step=name,
                reason=result.stopped_reason,
                entries_scanned=result.entries_scanned,
                skipped=[
                    s for s in SenseHygieneStep.ALL if s in selected and s not in outcome.steps
                ],
            )
            break

    outcome.entries_changed = len(changed_ids)
    _LOG.info("sense_hygiene_complete", entries=len(ids), workers=workers, **outcome.as_dict())
    return outcome
