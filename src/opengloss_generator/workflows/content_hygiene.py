"""Workflow 8 — content hygiene: contradictory relations, junk examples, dead renditions.

``audit.py`` and the QA sweep measure the graph's *shape*; ``workflows/graph_hygiene.py``
repairs it. This workflow is for the defects that are not shape at all but **content**:
two relation types asserted about the same target that cannot both be true, an example
sentence that is not a sentence, a canonical example written in a register no one speaks
in, and a rendition set whose members are word-for-word the same text. Every one of them
was counted on the 10K core store, not guessed at (``docs/CORE-DIARY.md`` Iteration 8):

============================  ======  ==================================================
Defect                        Count   Shape
============================  ======  ==================================================
synonym *and* hypernym        9,873   direction genuinely mixed; 7,527 with both resolved
self-synonym                    185   a sense naming its own lexeme a synonym (v1.3)
synonym *and* antonym            63   several are reciprocals of a wrong far-side edge
stilted canonical examples    5,401   "Two researchers formed a duo to complete the …"
fragment canonical examples      498   "the mile-long bridge opened to traffic"
degenerate renditions       592 + 87  two targets sharing one text; a copy of the canonical
garbage examples                 21   ``'hypernyms(['``, ``'?'``, bare single words
============================  ======  ==================================================

Seven steps, selectable by name through ``only=``, each idempotent, each run as its own
pooled sweep over the id list. Three are free; four make one model call per entry.

``self_synonym`` (free)
    A ``synonym`` whose ``target.lexeme_id`` is the entry's own is demoted to
    ``see_also`` with the note ``demoted: self-synonym``. The relation is not dropped
    (D-1's spirit — nothing is lost), and ``see_also`` is outside every check that made
    the self-synonym a defect, which is what makes the step idempotent: a second sweep
    finds nothing.

``synonym_antonym`` (free)
    A sense that calls the same target lexeme both a synonym and an antonym has said two
    things that cannot both hold. The ``antonym`` is the one demoted (``demoted:
    contradicts synonym``): a wrong antonym is the commoner error of the two, and
    ``refrigerator:noun:0 antonym fridge`` is the measured shape. Several of these are
    reciprocals — ``workflows/graph_hygiene.py`` step 4 copies a symmetric relation to
    the far side, so a wrong assertion arrives twice — so the step has a second phase:
    for every antonym it demotes, it looks at the *other* entry under that entry's own
    lock and demotes the assertion pointing back, when that far-side relation actually
    names this sense (by resolved ``sense_id`` or by ``graph_hygiene``'s own
    ``reciprocal of <sense id>`` note). A far-side antonym that names neither is left
    alone: it may be about a different sense entirely.

``synonym_hypernym``
    The big one, and the only one whose direction is not decidable by rule. ``tahoe:noun:0``
    calls ``lake`` both a synonym and a hypernym and should assert neither — it is an
    *instance* of a lake; ``teach:verb:0`` and ``instruct`` really are synonyms;
    ``chief:noun:2`` and ``title`` really are hypernym. So the step splits:

    * **Proper nouns are decided by rule, for free.** A named entity is never a synonym
      of its category, and its hypernym is Global WordNet's ``instance_hypernym`` — our
      :attr:`~opengloss_generator.schema.RelationType.INSTANCE_OF`. The hypernym is
      *retyped* (``retyped: proper noun instance``) and the synonym demoted to
      ``see_also``. No call is made.
    * **Everything else gets one nano call per entry** on the ``HYGIENE`` policy — this
      is a structural verdict about two definitions, not prose for an audience — listing
      every offending pair as its source gloss, the target term, and the *target's own*
      canonical gloss where the relation is resolved (``(unresolved)`` where it is not),
      and asking which of the two relations is true. The loser is demoted to ``see_also``
      with the note ``demoted: nano chose <keep>``; a ``neither`` verdict demotes both.

``garbage_examples`` (free)
    An example rendition of fewer than three words, or with no ASCII letter in it at all,
    is not an example. These are the only things this workflow deletes, and the deletion
    is not a loss: the text is written to a zero-cost ``Provenance.note`` reading
    ``removed garbage example: <text>`` before the rendition comes out. A sense left with
    no canonical example at all is exactly what ``workflows/retrofit.py``'s ``repair``
    pass step (b) already regenerates, so this step does not call it and does not
    duplicate it — run ``retrofit --only repair`` after this workflow. A fragment (see
    ``fragment_examples`` below) is not this step's business either: it is a sentence
    that is missing its capital or its stop, not debris, and it is repaired in place
    rather than removed.

``stilted_examples``
    5,401 canonical examples match :data:`STILTED_RE` — the academic-register tell the
    paper's own v1.3 QA flagged. Only **canonical** ``(neutral, plain)`` examples are
    offenders: a ``college`` rendition using the same words is doing its job, and
    rewriting it would be the defect. One luna call per entry on the ``RENDITIONS``
    policy (prose for a reader, held to a register) lists each offender with its sense's
    gloss and asks for a natural everyday sentence for the same sense. A rewrite is
    adopted only when :func:`~opengloss_generator.spans.find_span` can place the headword
    in it — a sentence that dropped the word it illustrates has traded one defect for a
    worse one — and its span and Flesch-Kincaid grade are re-measured from the text that
    is actually stored.

``fragment_examples``
    498 canonical examples measured on the 10K core (``docs/QA-DIARY.md``, iteration 3's
    after-sample) are sentence fragments rather than sentences: they start with a
    lowercase letter, or carry no terminal punctuation, or both — "the mile-long bridge
    opened to traffic" has neither. Capitalising the first letter and appending a period
    is not a repair for most of these; several are genuinely incomplete thoughts, so this
    step asks luna for a complete sentence for the same sense rather than patching the
    text it has. One call per entry on the ``RENDITIONS`` policy, the ``examples``
    field rule sliced out of :data:`~opengloss_generator.prompts.RENDITIONS_INSTRUCTIONS`
    the same way ``stilted_examples`` slices it, listing each offender with its sense's
    gloss. A rewrite is adopted only when it starts with an uppercase letter, ends with
    terminal punctuation (``.``, ``!``, ``?``, ``"``, ``”`` or ``)``), and
    :func:`~opengloss_generator.spans.find_span` can still place the headword in it — the
    same non-negotiable ``stilted_examples`` holds every rewrite to. The old text is kept
    in a zero-cost note reading ``superseded fragment example: <text>``.

``degenerate_renditions``
    A rendition set exists so each ``(level, register)`` target says the same thing
    differently. Two members carrying identical text carry one member's worth of
    information. Both measured shapes are collected per sense: two non-canonical gloss
    renditions whose normalised text matches, and a non-canonical rendition that is a
    copy of the canonical gloss. One luna call per entry asks for a rendition *for that
    exact target*, with the reading-level, register and headword-initial rules sliced
    verbatim out of :data:`~opengloss_generator.prompts.RENDITIONS_INSTRUCTIONS` rather
    than restated, so a rewrite is held to the bar the original was written against. A
    rewrite is accepted only if it is markdown-free, is not headword-initial
    (:func:`~opengloss_generator.hygiene.is_headword_initial`; proper nouns exempt, D-30),
    and differs from both the canonical and every sibling — otherwise the degenerate text
    stays and the attempt is counted as rejected.

Idempotence
-----------

The three free steps are idempotent because they leave nothing behind for themselves to
find: a demoted relation is no longer a synonym/antonym/hypernym, and a removed example
is gone. The four model steps cannot be, so each writes a sentinel to the ``note`` of
its own call's provenance record, in D-47's form — ``<prefix>:<digest>;attempts=<n>``,
where the digest is a hash of the *set of things the call answered for*. An entry is
skipped only when what offends now hashes to what its last marker was written for; a set
that changed since earns one more attempt, bounded at :data:`_MAX_ATTEMPTS` per entry. A
sentinel rather than a bare :class:`~opengloss_generator.schema.StageName` because these
steps reuse the ``HYGIENE`` and ``RENDITIONS`` policies rather than adding stages of their
own, so the stage alone would collide with every other pass that does the same.

Concurrency and locking (D-31)
------------------------------

Every step drives its ids through :func:`~opengloss_generator.runner.run_pool`, and the
handler holds the entry's lock across the whole of read → deterministic work → model call
→ write, so no entry is ever read outside the lock it is written under. Two reads happen
outside a lock, both strictly read-only and both for context only: ``synonym_hypernym``
looks up a *target* entry's canonical gloss to put in its prompt, and
``synonym_antonym``'s second phase re-reads the far-side entry under *that* entry's own
lock before touching it. Counters go through :class:`_Tally`, mutated only while holding
an ``asyncio.Lock``, for the reason ``workflows/retrofit.py``'s own ``_Tally`` gives.

This module is deliberately self-contained: its contracts and its instructions are
module-private and it imports nothing from ``workflows/retrofit.py``, because
``contracts.py``, ``prompts.py`` and ``cli.py`` are being edited concurrently on this
branch and a new independent module is the only way to land this work without conflicting
with that. :func:`run_content_hygiene` is written to be callable exactly the way a future
``retrofit --only content_hygiene`` wiring will need it.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from opengloss_generator import prompts, spans
from opengloss_generator.errors import BudgetExceededError, GenerationError
from opengloss_generator.hygiene import is_headword_initial
from opengloss_generator.identity import rendition_id
from opengloss_generator.log import get_logger
from opengloss_generator.prompts import PROMPT_VERSION
from opengloss_generator.readability import flesch_kincaid_grade, strip_markdown, word_count
from opengloss_generator.runner import run_pool
from opengloss_generator.schema import (
    CANONICAL_KEY,
    Assessment,
    LexemeKind,
    Provenance,
    RelationType,
    StageName,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Sequence

    from opengloss_generator.schema import (
        Example,
        Lexeme,
        POSEntry,
        Relation,
        Rendition,
        Sense,
    )
    from opengloss_generator.stages import StageRunner
    from opengloss_generator.store import LexemeStore

__all__ = ["ContentHygieneOutcome", "ContentHygieneStep", "StepResult", "run_content_hygiene"]

_LOG = get_logger(__name__)

#: Provenance ``model`` for every free edit this workflow makes. A rule, not a model,
#: named the way ``graph_hygiene.DEMOTION_MODEL`` and ``retrofit.DETERMINISTIC_MODEL`` are.
DETERMINISTIC_MODEL = "rule:content_hygiene"

#: How often a running step logs its progress, in entries. Mirrors ``retrofit.py`` and
#: ``graph_hygiene.py`` so every sweep reads the same in a run log.
PROGRESS_EVERY = 500

#: Note text per edit. They are also the audit trail: every relation or example this
#: workflow touched can be found again by its note.
SELF_SYNONYM_NOTE = "demoted: self-synonym"
SYNONYM_ANTONYM_NOTE = "demoted: contradicts synonym"
PROPER_NOUN_RETYPE_NOTE = "retyped: proper noun instance"
PROPER_NOUN_DEMOTE_NOTE = "demoted: proper noun instance"
GARBAGE_EXAMPLE_NOTE = "removed garbage example: "
FRAGMENT_EXAMPLE_NOTE = "superseded fragment example: "

#: The academic-register tell in a canonical example. Measured on the 10K core: 5,401
#: canonical examples match it ("Two researchers formed a duo to complete the project."),
#: which is the defect the paper's own v1.3 QA flagged. Deliberately narrow — it matches
#: the framing words, not any sentence that happens to be about science.
STILTED_RE = re.compile(
    r"\b(researchers?|participants?|observers?|the study|this study|data ?set)\b",
    re.IGNORECASE,
)

#: An example rendition below this many words is not a sentence. Three is the floor a
#: real example clears trivially ("Rain fell." is two words and is not one of these; the
#: 21 measured offenders are bare single words and parser debris like ``hypernyms([``).
_MIN_EXAMPLE_WORDS = 3

#: Any ASCII letter. An example with none at all — ``'?'`` — is not text.
_ASCII_LETTER_RE = re.compile(r"[A-Za-z]")

#: Quote characters stripped off the *front* of an example before the lowercase-start
#: check, so a sentence quoted whole ("the bridge is closed.") is judged by its own first
#: letter rather than by the mark that opens the quotation. Never stripped off the back —
#: :data:`_FRAGMENT_TERMINAL_CHARS` already accepts a trailing quote as terminal in its
#: own right, the way dialogue closes ('The guide said, "Mind the gap."').
_FRAGMENT_QUOTE_CHARS = "\"'\u201c\u201d\u2018\u2019"

#: Characters that end a sentence: the three stops, a straight or curly closing quote
#: (dialogue closes on one), and a closing parenthesis (a parenthetical aside can be the
#: last thing in the sentence). Measured on the 10K core: 498 canonical examples start
#: with a lowercase letter, end in none of these, or both ("the mile-long bridge opened
#: to traffic" — ``docs/QA-DIARY.md``, iteration 3's after-sample).
_FRAGMENT_TERMINAL_CHARS = frozenset('.!?"\u201d)')

#: How many attempts a model step makes on one entry before leaving what still offends
#: alone rather than billing a third answer for it (D-47's bound, per entry).
_MAX_ATTEMPTS = 2

#: Separates the offending-set digest from the attempt count inside a marker note.
_ATTEMPTS_SEPARATOR = ";attempts="

#: Sentinel prefixes, one per model step. Each is written to the ``note`` of that step's
#: own call record. They must be distinct from every other pass's, because all of these
#: calls reuse a shared stage's policy rather than adding a stage of their own.
_SYNONYM_HYPERNYM_PREFIX = "content_hygiene:synonym_hypernym"
_STILTED_PREFIX = "content_hygiene:stilted_examples"
_FRAGMENT_PREFIX = "content_hygiene:fragment_examples"
_DEGENERATE_PREFIX = "content_hygiene:degenerate_renditions"

#: Shown in place of a target's gloss when the relation was never resolved to a sense.
UNRESOLVED_GLOSS = "(unresolved)"


class ContentHygieneStep:
    """Names of the steps :func:`run_content_hygiene` can select between."""

    SELF_SYNONYM = "self_synonym"
    SYNONYM_ANTONYM = "synonym_antonym"
    SYNONYM_HYPERNYM = "synonym_hypernym"
    GARBAGE_EXAMPLES = "garbage_examples"
    STILTED_EXAMPLES = "stilted_examples"
    FRAGMENT_EXAMPLES = "fragment_examples"
    DEGENERATE_RENDITIONS = "degenerate_renditions"

    #: The order the steps run in: the three free ones first, cheapest work before any
    #: spend, so a run stopped by its budget has already banked everything that cost
    #: nothing. Within the free three, the relation steps precede the example step only
    #: because they are the ones a later step never reads. ``fragment_examples`` follows
    #: ``stilted_examples`` because both rewrite canonical examples: it sees each entry's
    #: examples as ``stilted_examples`` left them, not as the store held them before that
    #: step ran, so a stilted rewrite that happens to still read as a fragment is caught
    #: here rather than surviving the sweep.
    ALL: tuple[str, ...] = (
        SELF_SYNONYM,
        SYNONYM_ANTONYM,
        SYNONYM_HYPERNYM,
        GARBAGE_EXAMPLES,
        STILTED_EXAMPLES,
        FRAGMENT_EXAMPLES,
        DEGENERATE_RENDITIONS,
    )


# --------------------------------------------------------------------------------------
# The outcome
# --------------------------------------------------------------------------------------


@dataclass(slots=True)
class StepResult:
    """Counts and cost for one content-hygiene step.

    Attributes:
        name: The step this result belongs to.
        entries_scanned: Entries the step visited.
        entries_changed: Entries it actually wrote.
        demoted: Relations retyped to ``see_also`` because they contradicted another.
        retyped: Relations retyped to ``instance_of`` (proper-noun rule only).
        removed: Example renditions taken out of a sense (``garbage_examples`` only).
        rewritten: Stored texts replaced by a model rewrite.
        accepted: Model answers applied — a rewrite adopted, or a relation verdict acted
            on. Equals :attr:`rewritten` for the two rewriting steps by construction; it
            is reported separately because for ``synonym_hypernym`` the applied answer is
            a demotion rather than a rewrite.
        rejected: Model answers refused — a rewrite that lost its headword, that read as
            headword-initial, or that was still identical to what it replaced; a verdict
            whose ``ref`` named nothing.
        calls: Model calls made.
        cost_usd: What they cost.
        stopped_reason: ``None`` when the step ran to completion; ``"budget"`` when the
            run's ceiling was reached mid-step; ``"stopped"`` when the caller's stop event
            was set. A stopped step still reports everything it did, and everything it
            wrote is on disk.
    """

    name: str
    entries_scanned: int = 0
    entries_changed: int = 0
    demoted: int = 0
    retyped: int = 0
    removed: int = 0
    rewritten: int = 0
    accepted: int = 0
    rejected: int = 0
    calls: int = 0
    cost_usd: float = 0.0
    stopped_reason: str | None = None

    @property
    def changed(self) -> int:
        """Return how many individual things this step changed."""
        return self.demoted + self.retyped + self.removed + self.rewritten

    def as_dict(self) -> dict[str, object]:
        """Return a JSON-able view for the run summary and the CLI."""
        return {
            "entries_scanned": self.entries_scanned,
            "entries_changed": self.entries_changed,
            "demoted": self.demoted,
            "retyped": self.retyped,
            "removed": self.removed,
            "rewritten": self.rewritten,
            "accepted": self.accepted,
            "rejected": self.rejected,
            "calls": self.calls,
            "cost_usd": round(self.cost_usd, 6),
            "stopped_reason": self.stopped_reason,
        }


@dataclass(slots=True)
class ContentHygieneOutcome:
    """What one :func:`run_content_hygiene` sweep did, per step.

    Attributes:
        steps: One :class:`StepResult` per step that ran, keyed by step name.
        entries_changed: How many *distinct* entries were written across every step —
            not the sum of the per-step figures, which would count an entry twice when
            two steps both touched it.
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

        A budget stop is reported here, not raised, so a caller's run summary can say
        "budget" rather than "completed" — the same convention ``run_retrofit`` follows.
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

    Single-threaded asyncio does make ``counter += 1`` atomic on its own — nothing else
    can run between the read and the write of an await-free statement — but these
    counters are touched by many handlers around many awaits, and that guarantee is a
    property of the interpreter rather than of this code. Every mutation therefore goes
    through this class, inside the lock, the same discipline ``retrofit._Tally`` keeps.

    Args:
        name: The step this tally belongs to.
        changed_ids: The run-level set of entry ids that have been written by any step.
            Shared so :attr:`ContentHygieneOutcome.entries_changed` counts distinct
            entries rather than entry-visits.
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
        scanned: bool = True,
        demoted: int = 0,
        retyped: int = 0,
        removed: int = 0,
        rewritten: int = 0,
        accepted: int = 0,
        rejected: int = 0,
    ) -> None:
        """Fold one visited entry into the step result.

        Args:
            lexeme_id: The entry visited.
            scanned: Whether this visit counts as a scan. ``False`` for a second-phase
                visit to an entry the step has already counted once.
            demoted: Relations demoted to ``see_also`` in this entry.
            retyped: Relations retyped to ``instance_of`` in this entry.
            removed: Example renditions removed from this entry.
            rewritten: Stored texts replaced in this entry.
            accepted: Model answers applied for this entry.
            rejected: Model answers refused for this entry.
        """
        async with self._lock:
            result = self._result
            if scanned:
                self._visited += 1
                result.entries_scanned += 1
            result.demoted += demoted
            result.retyped += retyped
            result.removed += removed
            result.rewritten += rewritten
            result.accepted += accepted
            result.rejected += rejected
            if demoted or retyped or removed or rewritten:
                self._changed.add(lexeme_id)
                self._changed_ids.add(lexeme_id)
                result.entries_changed = len(self._changed)
            if self._visited and self._visited % PROGRESS_EVERY == 0:
                _LOG.info(
                    "content_hygiene_progress",
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


async def _drive[T](
    items: Sequence[T],
    handler: Callable[[T], Awaitable[None]],
    tally: _Tally,
    *,
    workers: int,
    stop_event: asyncio.Event | None,
) -> None:
    """Run one step's handler over ``items`` through the bounded pool.

    ``run_pool`` already treats :class:`BudgetExceededError` as a clean stop of the whole
    pool rather than an error to propagate, so this wrapper exists only to record *why*
    the step stopped before the exception is swallowed.

    Args:
        items: The work items — lexeme ids, or the second-phase requests of a step that
            has two phases.
        handler: The per-item coroutine function.
        tally: The step tally, which learns the stop reason.
        workers: Pool size.
        stop_event: Shared stop event; set by a budget stop, and honoured if the caller
            sets it from outside.
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


def _forms_for(entry: Lexeme, pos_entry: POSEntry) -> list[str]:
    """Return every surface form to try when locating the headword in an example.

    The union of the model-supplied :class:`~opengloss_generator.schema.Morphology` forms
    and :func:`~opengloss_generator.spans.generate_forms`' rule-based ones, rather than
    the fallback relationship the other passes use: a rewrite is *accepted or discarded*
    on whether the finder can place it, so the widest honest candidate list is the one
    that refuses the fewest good sentences.

    Args:
        entry: The entry the example belongs to.
        pos_entry: The owning part-of-speech entry.

    Returns:
        The candidate forms, de-duplicated, morphology first.
    """
    forms = [*pos_entry.morphology.inflected_forms(), *pos_entry.morphology.derivations]
    for generated in spans.generate_forms(entry.headword):
        if generated not in forms:
            forms.append(generated)
    return forms


def _normalised(text: str) -> str:
    """Return the comparison key for a piece of stored prose.

    Case- and whitespace-insensitive, and one trailing period is ignored, so "A cat." and
    "a cat" are the same text. Mirrors ``retrofit._normalized_gloss``, which decides the
    same question for duplicate senses.

    Args:
        text: The prose to key.

    Returns:
        The normalised key.
    """
    collapsed = " ".join(text.split()).strip().lower()
    return collapsed[:-1] if collapsed.endswith(".") else collapsed


def _rule_provenance(note: str | None = None) -> Provenance:
    """Return the zero-cost provenance record a free edit is stamped with.

    Args:
        note: Free text to preserve on the record — a superseded or removed value.

    Returns:
        A :class:`~opengloss_generator.schema.Provenance` with every cost and token field
        at zero, so a naive sum over an entry's provenance table is unaffected by this
        workflow having run.
    """
    return Provenance(
        stage=StageName.HYGIENE,
        model=DETERMINISTIC_MODEL,
        prompt_version=PROMPT_VERSION,
        cost_usd=0.0,
        attempts=0,
        note=note,
    )


def _note_provenance(base: Provenance, note: str) -> Provenance:
    """Return a zero-cost copy of a call's provenance record, carrying ``note``.

    The real cost and token counts of the call are recorded once, on the entry's own call
    marker; this copy exists only so each superseded text is individually retrievable,
    without inflating a naive sum of ``cost_usd`` over the entry's provenance table.
    Mirrors ``retrofit._note_provenance`` and ``example_hygiene._note_provenance``.

    Args:
        base: The call's own provenance record.
        note: The superseded text to preserve.

    Returns:
        A copy of ``base`` with ``note`` set and cost/token fields zeroed.
    """
    return base.model_copy(
        update={
            "note": note,
            "cost_usd": 0.0,
            "input_tokens": 0,
            "cached_input_tokens": 0,
            "output_tokens": 0,
            "attempts": 0,
        }
    )


def _retype(relation: Relation, new_type: RelationType, note: str, provenance_id: str) -> None:
    """Retype one relation in place, keeping any note it already carried.

    Nothing is ever deleted (D-1's spirit): a defective relation becomes a weaker one
    that still says something true, and the reason is written where a later reader will
    find it. Mirrors ``graph_hygiene._apply_plan``'s own demotion.

    Args:
        relation: The relation to retype, mutated in place.
        new_type: What it becomes — ``see_also`` for a demotion, ``instance_of`` for the
            proper-noun retype.
        note: Why, prepended to whatever note the relation already had.
        provenance_id: The entry's record for this edit.
    """
    relation.type = new_type
    relation.note = note if relation.note is None else f"{note} | {relation.note}"
    relation.provenance_id = provenance_id


class _Marker:
    """The most recent sentinel one model step left on an entry.

    Attributes:
        digest: The offending-set hash the marker was written for.
        attempts: How many attempts the step has made on this entry, this one included.
    """

    __slots__ = ("attempts", "digest")

    def __init__(self, digest: str, attempts: int) -> None:
        """Record a parsed marker."""
        self.digest = digest
        self.attempts = attempts


def _offender_digest(refs: Sequence[str]) -> str:
    """Return a stable short hash of the things a call is about to answer for.

    Args:
        refs: Stable identifiers of everything the step found to fix on this sweep, in
            any order.

    Returns:
        Sixteen hex characters of SHA-256 over the sorted, newline-joined refs. Sorted so
        the digest does not depend on document order, and SHA-256 rather than :func:`hash`
        because the value is written to disk and compared across processes.
    """
    joined = "\n".join(sorted(refs))
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:16]


def _latest_marker(entry: Lexeme, prefix: str) -> _Marker | None:
    """Return the last sentinel ``prefix``'s step wrote on an entry, parsed.

    Args:
        entry: The entry to inspect.
        prefix: The step's note prefix.

    Returns:
        The most recent marker, or ``None`` if the step has never visited the entry.
        Provenance ids are assigned in insertion order and never reused, so the last
        matching record in the table is the most recently written one.
    """
    latest: _Marker | None = None
    for record in entry.provenance_in_order():
        note = record.note or ""
        if not note.startswith(f"{prefix}:"):
            continue
        digest, _, attempts = note[len(prefix) + 1 :].partition(_ATTEMPTS_SEPARATOR)
        latest = _Marker(digest, int(attempts) if attempts.isdigit() else 1)
    return latest


def _attempt_due(entry: Lexeme, prefix: str, refs: Sequence[str]) -> str | None:
    """Return the sentinel to stamp on this entry's next attempt, or ``None``.

    An entry is due an attempt when it has something to fix and either the step has never
    visited it, or the set of things to fix has changed since the step last answered for
    it — and it has not already had :data:`_MAX_ATTEMPTS` of them (D-47).

    Args:
        entry: The entry being considered.
        prefix: The step's note prefix.
        refs: Stable identifiers of what offends *now*, which is exactly what the attempt
            would cover.

    Returns:
        The note to write on the call's provenance record, or ``None`` when the entry
        must be skipped — which is also the "do not bill this" signal for the caller.
    """
    if not refs:
        return None
    digest = _offender_digest(refs)
    marker = _latest_marker(entry, prefix)
    if marker is None:
        return f"{prefix}:{digest}{_ATTEMPTS_SEPARATOR}1"
    if marker.digest == digest or marker.attempts >= _MAX_ATTEMPTS:
        return None
    return f"{prefix}:{digest}{_ATTEMPTS_SEPARATOR}{marker.attempts + 1}"


def _live_senses(entry: Lexeme) -> list[tuple[POSEntry, Sense, str]]:
    """Return ``(pos_entry, sense, sense_id)`` for every non-retired sense."""
    return [triple for triple in entry.iter_senses() if not triple[1].retired]


# --------------------------------------------------------------------------------------
# Step 1 — self_synonym (free)
# --------------------------------------------------------------------------------------


def _demote_self_synonyms(entry: Lexeme) -> int:
    """Demote every synonym a sense asserts toward its own lexeme, in place.

    185 senses on the 10K core name their own lexeme a synonym, an artifact of the v1.3
    import that carries no provenance note explaining itself. "X is a synonym of X" is
    not information; ``see_also`` toward oneself is at least harmless, and keeping the
    relation rather than dropping it is what D-1's spirit asks.

    Args:
        entry: The entry to clean, mutated in place.

    Returns:
        How many relations were demoted.
    """
    demoted = 0
    provenance_id: str | None = None
    for _, sense, _ in _live_senses(entry):
        for relation in sense.relations:
            if relation.type is not RelationType.SYNONYM:
                continue
            if relation.target.lexeme_id != entry.lexeme_id:
                continue
            if provenance_id is None:
                provenance_id = entry.add_provenance(_rule_provenance())
            _retype(relation, RelationType.SEE_ALSO, SELF_SYNONYM_NOTE, provenance_id)
            demoted += 1
    return demoted


async def _self_synonym_step(
    store: LexemeStore,
    runner: StageRunner,
    ids: Sequence[str],
    *,
    workers: int,
    stop_event: asyncio.Event | None,
    changed_ids: set[str],
) -> StepResult:
    """Demote every self-synonym in the store, for $0.

    Args:
        store: The store to clean; each entry read and written inside one lock hold.
        runner: Unused — this step makes no model call.
        ids: The entry ids to visit.
        workers: Pool size.
        stop_event: Shared stop event.
        changed_ids: Run-level set of entries written by any step.

    Returns:
        The step's :class:`StepResult`.
    """
    del runner  # free step
    tally = _Tally(ContentHygieneStep.SELF_SYNONYM, changed_ids)

    async def clean(lexeme_id: str) -> None:
        demoted = 0
        async with store.locked(lexeme_id):
            entry = store.read(lexeme_id)
            if entry is None:
                return
            demoted = _demote_self_synonyms(entry)
            if demoted:
                store.write(entry)
        await tally.entry(lexeme_id, demoted=demoted)

    await _drive(ids, clean, tally, workers=workers, stop_event=stop_event)
    return tally.result


# --------------------------------------------------------------------------------------
# Step 2 — synonym_antonym (free, two phases)
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _ReciprocalRequest:
    """One far-side antonym to check, after its near side was demoted.

    Attributes:
        lexeme_id: The entry to visit — the target of the demoted antonym.
        source_sense: The sense id whose antonym was demoted. A far-side relation counts
            as the reciprocal of it only when it names this sense.
        source_lexeme: That sense's lexeme, which the far-side relation must point at.
    """

    lexeme_id: str
    source_sense: str
    source_lexeme: str


def _contradicted_antonyms(sense: Sense) -> list[Relation]:
    """Return the antonyms of a sense that point where one of its synonyms also points.

    Args:
        sense: The sense to inspect. Never mutated.

    Returns:
        The offending ``antonym`` relations, in document order.
    """
    synonym_targets = {
        relation.target.lexeme_id
        for relation in sense.relations
        if relation.type is RelationType.SYNONYM
    }
    if not synonym_targets:
        return []
    return [
        relation
        for relation in sense.relations
        if relation.type is RelationType.ANTONYM and relation.target.lexeme_id in synonym_targets
    ]


def _demote_contradicted_antonyms(entry: Lexeme) -> tuple[int, list[_ReciprocalRequest]]:
    """Demote every antonym contradicted by a synonym on the same sense, in place.

    Args:
        entry: The entry to clean, mutated in place.

    Returns:
        ``(relations demoted, the far-side checks the demotions imply)``.
    """
    demoted = 0
    requests: list[_ReciprocalRequest] = []
    provenance_id: str | None = None
    for _, sense, sid in _live_senses(entry):
        for relation in _contradicted_antonyms(sense):
            if provenance_id is None:
                provenance_id = entry.add_provenance(_rule_provenance())
            target_lexeme = relation.target.lexeme_id
            _retype(relation, RelationType.SEE_ALSO, SYNONYM_ANTONYM_NOTE, provenance_id)
            demoted += 1
            if target_lexeme != entry.lexeme_id:
                requests.append(
                    _ReciprocalRequest(
                        lexeme_id=target_lexeme,
                        source_sense=sid,
                        source_lexeme=entry.lexeme_id,
                    )
                )
    return demoted, requests


def _is_reciprocal_of(relation: Relation, request: _ReciprocalRequest) -> bool:
    """Return whether a far-side relation is the copy of the antonym just demoted.

    Identity has to be positive, not merely plausible: the far-side entry may hold a
    perfectly good antonym toward this lexeme *about a different sense*, and demoting
    that would be a new defect. A relation qualifies only when it points at the right
    lexeme and either resolves to the exact sense whose assertion was demoted, or carries
    ``workflows/graph_hygiene.py``'s own ``reciprocal of <sense id>`` note naming it.

    Args:
        relation: The far-side relation under consideration.
        request: The demotion that prompted the check.

    Returns:
        Whether the relation is the reciprocal of the demoted assertion.
    """
    if relation.type is not RelationType.ANTONYM:
        return False
    if relation.target.lexeme_id != request.source_lexeme:
        return False
    if relation.target.sense_id == request.source_sense:
        return True
    return request.source_sense in (relation.note or "")


async def _synonym_antonym_step(
    store: LexemeStore,
    runner: StageRunner,
    ids: Sequence[str],
    *,
    workers: int,
    stop_event: asyncio.Event | None,
    changed_ids: set[str],
) -> StepResult:
    """Demote every antonym contradicted by a synonym, and its far-side copy, for $0.

    Two pooled phases. The first demotes what each entry contradicts itself about, under
    that entry's lock, and collects the far-side checks the demotions imply; the second
    visits each of those entries under *its* own lock, so a reciprocal copied onto the
    other side by ``graph_hygiene`` step 4 does not survive its own source.

    Args:
        store: The store to clean.
        runner: Unused — this step makes no model call.
        ids: The entry ids to visit.
        workers: Pool size.
        stop_event: Shared stop event.
        changed_ids: Run-level set of entries written by any step.

    Returns:
        The step's :class:`StepResult`.
    """
    del runner  # free step
    tally = _Tally(ContentHygieneStep.SYNONYM_ANTONYM, changed_ids)
    requests: list[_ReciprocalRequest] = []
    requests_lock = asyncio.Lock()

    async def clean(lexeme_id: str) -> None:
        demoted = 0
        found: list[_ReciprocalRequest] = []
        async with store.locked(lexeme_id):
            entry = store.read(lexeme_id)
            if entry is None:
                return
            demoted, found = _demote_contradicted_antonyms(entry)
            if demoted:
                store.write(entry)
        if found:
            async with requests_lock:
                requests.extend(found)
        await tally.entry(lexeme_id, demoted=demoted)

    await _drive(ids, clean, tally, workers=workers, stop_event=stop_event)

    async def reciprocate(request: _ReciprocalRequest) -> None:
        demoted = 0
        async with store.locked(request.lexeme_id):
            entry = store.read(request.lexeme_id)
            if entry is None:
                return
            provenance_id: str | None = None
            for _, sense, _ in _live_senses(entry):
                for relation in sense.relations:
                    if not _is_reciprocal_of(relation, request):
                        continue
                    if provenance_id is None:
                        provenance_id = entry.add_provenance(_rule_provenance())
                    note = f"{SYNONYM_ANTONYM_NOTE} (reciprocal of {request.source_sense})"
                    _retype(relation, RelationType.SEE_ALSO, note, provenance_id)
                    demoted += 1
            if demoted:
                store.write(entry)
        await tally.entry(request.lexeme_id, scanned=False, demoted=demoted)

    # Sorted so the same store produces the same second phase whatever order the first
    # phase's workers finished in.
    ordered = sorted(set(requests), key=lambda r: (r.lexeme_id, r.source_sense))
    # The far-side phase repairs writes already committed by the first phase; it buys
    # nothing and must not be skipped by a budget or SIGINT stop (D-50, second amendment:
    # a stop event set during the first phase made this phase run over zero items).
    await _drive(ordered, reciprocate, tally, workers=workers, stop_event=None)
    return tally.result


# --------------------------------------------------------------------------------------
# Step 3 — synonym_hypernym
# --------------------------------------------------------------------------------------
#
# The instructions and the output contract live here, not in prompts.py / contracts.py:
# those files are being edited concurrently on this branch, and keeping every call site
# in this module self-contained means this work never conflicts with that. Nothing
# outside this module depends on the names below.


#: Instructions for this step's one nano call per entry. Kept short and byte-stable so it
#: caches like every other stage's instructions do.
RELATION_CHOICE_INSTRUCTIONS = """\
Each numbered item below is one sense of a headword that asserts BOTH "synonym" and \
"hypernym" toward the same target term. Both cannot be true at once: a synonym means \
roughly the same thing as the sense, while a hypernym is a broader category the sense \
falls under and is not a substitute for it.

For each item, answer with the one relation that is actually true:

- "synonym" when the two terms mean roughly the same thing and either could stand in for \
the other;
- "hypernym" when the target names a broader category that the sense belongs to;
- "neither" when both are wrong -- the target is narrower than the sense, is a specific \
instance of it, or is merely related to it.

Judge from the two definitions you are given, not from the words alone. Where the sense \
names one particular person, place, work or organisation and the target names the general \
category it belongs to, the answer is "neither".

Answer every item you are given, identified by the number it was listed under."""


class _DraftRelationChoice(BaseModel):
    """One verdict on a sense that asserts both a synonym and a hypernym at one target."""

    model_config = ConfigDict(
        extra="forbid", validate_by_name=True, validate_by_alias=True, serialize_by_alias=True
    )

    ref: Annotated[int, Field(ge=1)]
    keep: Literal["synonym", "hypernym", "neither"]


class _DraftRelationChoices(BaseModel):
    """Verdicts for every contradictory pair of one entry, produced together."""

    model_config = ConfigDict(
        extra="forbid", validate_by_name=True, validate_by_alias=True, serialize_by_alias=True
    )

    choices: Annotated[list[_DraftRelationChoice], Field(min_length=1)]


@dataclass(slots=True)
class _RelationPair:
    """One sense asserting both ``synonym`` and ``hypernym`` toward the same lexeme.

    Attributes:
        synonym: The synonym relation, mutated in place if it loses.
        hypernym: The hypernym relation, mutated in place if it loses or is retyped.
        term: The target's surface form, as stored.
        source_gloss: The asserting sense's canonical gloss, shown to the model.
        target_gloss: The target sense's canonical gloss, or :data:`UNRESOLVED_GLOSS`.
        ref_id: A stable identifier for the digest — the asserting sense and the target
            lexeme, which is what makes the pair the same pair across sweeps.
    """

    synonym: Relation
    hypernym: Relation
    term: str
    source_gloss: str
    target_gloss: str
    ref_id: str


def _pair_relations(sense: Sense) -> list[tuple[Relation, Relation, str]]:
    """Return ``(synonym, hypernym, target lexeme)`` for each doubly-asserted target.

    Where a sense asserts several synonyms or several hypernyms at the same lexeme — rare,
    and always a duplicate — the first of each is paired and the rest are left alone; the
    next sweep pairs whatever is still there.

    Args:
        sense: The sense to inspect. Never mutated.

    Returns:
        One triple per target lexeme asserted as both, in the order the synonyms appear.
    """
    synonyms: dict[str, Relation] = {}
    hypernyms: dict[str, Relation] = {}
    for relation in sense.relations:
        target = relation.target.lexeme_id
        if relation.type is RelationType.SYNONYM:
            synonyms.setdefault(target, relation)
        elif relation.type is RelationType.HYPERNYM:
            hypernyms.setdefault(target, relation)
    return [
        (synonym, hypernyms[target], target)
        for target, synonym in synonyms.items()
        if target in hypernyms
    ]


def _target_gloss(store: LexemeStore, sense_id: str | None, cache: dict[str, str]) -> str:
    """Return the canonical gloss of a resolved relation target, for the prompt.

    The target entry is read *without* its lock: this is a read-only lookup for prompt
    context, never a read the workflow then writes back from, so the discipline that
    matters (no entry is written from a read taken outside its own lock) is untouched.
    ``audit.py`` and ``graph_hygiene.py``'s load pass read the store the same way.

    Args:
        store: The store to read from.
        sense_id: The resolved target sense, or ``None`` if the relation is unresolved.
        cache: Per-step memo, ``sense_id -> gloss``. Popular targets are asserted by many
            entries, so the same gloss would otherwise be read off disk hundreds of times.

    Returns:
        The target sense's canonical gloss, or :data:`UNRESOLVED_GLOSS` when the relation
        is unresolved or the target is missing from the store.
    """
    if sense_id is None:
        return UNRESOLVED_GLOSS
    cached = cache.get(sense_id)
    if cached is not None:
        return cached
    entry = store.read(sense_id.rsplit(":", 2)[0])
    gloss = UNRESOLVED_GLOSS
    if entry is not None:
        for _, sense, sid in entry.iter_senses():
            if sid == sense_id:
                gloss = sense.canonical_gloss()
                break
    cache[sense_id] = gloss
    return gloss


def _collect_pairs(entry: Lexeme, store: LexemeStore, cache: dict[str, str]) -> list[_RelationPair]:
    """Return every contradictory synonym/hypernym pair of one entry.

    Args:
        entry: The entry to inspect. Never mutated.
        store: The store, read only to look up target glosses.
        cache: The step's target-gloss memo.

    Returns:
        One :class:`_RelationPair` per offending pair, in document order — the order the
        model is shown them in and refers to them by.
    """
    pairs: list[_RelationPair] = []
    for _, sense, sid in _live_senses(entry):
        source_gloss = sense.canonical_gloss()
        for synonym, hypernym, target_lexeme in _pair_relations(sense):
            pairs.append(
                _RelationPair(
                    synonym=synonym,
                    hypernym=hypernym,
                    term=synonym.target.term,
                    source_gloss=source_gloss,
                    target_gloss=_target_gloss(store, hypernym.target.sense_id, cache),
                    ref_id=f"{sid}|{target_lexeme}",
                )
            )
    return pairs


def _resolve_proper_noun_pairs(entry: Lexeme, pairs: Sequence[_RelationPair]) -> tuple[int, int]:
    """Apply the proper-noun rule to every pair of one entry, for free.

    A named entity is never a synonym of the category it belongs to, and the relation it
    *does* have to that category is Global WordNet's ``instance_hypernym`` — our
    :attr:`~opengloss_generator.schema.RelationType.INSTANCE_OF`. ``tahoe`` is not a
    synonym of ``lake`` and is not a kind of lake either; it is an instance of one. No
    model is needed to know that, so no call is made.

    Args:
        entry: The entry, mutated in place.
        pairs: Its contradictory pairs.

    Returns:
        ``(relations demoted, relations retyped)``.
    """
    if not pairs:
        return 0, 0
    provenance_id = entry.add_provenance(_rule_provenance())
    for pair in pairs:
        _retype(pair.hypernym, RelationType.INSTANCE_OF, PROPER_NOUN_RETYPE_NOTE, provenance_id)
        _retype(pair.synonym, RelationType.SEE_ALSO, PROPER_NOUN_DEMOTE_NOTE, provenance_id)
    return len(pairs), len(pairs)


def _build_relation_choice_prompt(headword: str, pairs: Sequence[_RelationPair]) -> str:
    """Return the volatile half of this step's verdict prompt.

    Args:
        headword: The lexeme's surface form.
        pairs: The offending pairs, in the order the model should answer them — ``ref``
            in the reply is a 1-based position into this sequence.

    Returns:
        The per-call prompt body.
    """
    lines = [
        f'  {i + 1}. target="{pair.term}" | sense: {pair.source_gloss} '
        f"| target sense: {pair.target_gloss}"
        for i, pair in enumerate(pairs)
    ]
    listed = "\n".join(lines)
    return f"Headword: {headword}\nPairs ({len(pairs)}):\n{listed}"


def _apply_choice(entry: Lexeme, pair: _RelationPair, keep: str, provenance_id: str) -> int:
    """Demote whichever of a pair's two relations the model did not keep.

    Args:
        entry: The entry, used only for the log line.
        pair: The pair the verdict is about, mutated in place.
        keep: ``"synonym"``, ``"hypernym"`` or ``"neither"``.
        provenance_id: The entry's record for this call's edits.

    Returns:
        How many relations were demoted — one, or two for ``"neither"``.
    """
    note = f"demoted: nano chose {keep}"
    demoted = 0
    if keep != "synonym" and pair.synonym.type is RelationType.SYNONYM:
        _retype(pair.synonym, RelationType.SEE_ALSO, note, provenance_id)
        demoted += 1
    if keep != "hypernym" and pair.hypernym.type is RelationType.HYPERNYM:
        _retype(pair.hypernym, RelationType.SEE_ALSO, note, provenance_id)
        demoted += 1
    _LOG.debug(
        "content_hygiene_relation_choice",
        headword=entry.headword,
        target=pair.term,
        keep=keep,
        demoted=demoted,
    )
    return demoted


async def _choose_relations(
    entry: Lexeme,
    pairs: Sequence[_RelationPair],
    runner: StageRunner,
    tally: _Tally,
    marker_note: str,
) -> tuple[int, int, int]:
    """Ask nano which of each pair's two relations is true, and apply the answer.

    Args:
        entry: The entry whose pairs need deciding, mutated in place.
        pairs: The pairs, in the order the model was shown them.
        runner: The stage runner.
        tally: The step tally, for the call and its cost.
        marker_note: The offending-set sentinel to stamp on the call's record.

    Returns:
        ``(relations demoted, verdicts applied, verdicts refused)``.

    Raises:
        BudgetExceededError: A budget stop is a run-level condition and propagates.
    """
    try:
        stage_result = await runner.run(
            # Reuses hygiene's model policy (nano, low effort): this is a structural
            # verdict about two definitions, not prose for an audience.
            stage=StageName.HYGIENE,
            output_type=_DraftRelationChoices,
            instructions=RELATION_CHOICE_INSTRUCTIONS,
            prompt=_build_relation_choice_prompt(entry.headword, pairs),
            prompt_version=PROMPT_VERSION,
        )
    except BudgetExceededError:
        raise
    except GenerationError as exc:
        _LOG.warning("content_hygiene_choice_failed", headword=entry.headword, error=str(exc))
        return 0, 0, 0

    await tally.call(stage_result.cost_usd)
    # Written unconditionally once the call itself succeeded, so a pair the model did not
    # usefully answer for is not re-billed for the same offending set on the next sweep —
    # the convention every note-stamped pass in this project follows.
    provenance_id = entry.add_provenance(
        stage_result.provenance.model_copy(update={"note": marker_note})
    )
    demoted = accepted = rejected = 0
    for drafted in stage_result.output.choices:
        position = drafted.ref - 1
        if not 0 <= position < len(pairs):
            rejected += 1
            continue
        demoted += _apply_choice(entry, pairs[position], drafted.keep, provenance_id)
        accepted += 1
    return demoted, accepted, rejected


async def _synonym_hypernym_step(
    store: LexemeStore,
    runner: StageRunner,
    ids: Sequence[str],
    *,
    workers: int,
    stop_event: asyncio.Event | None,
    changed_ids: set[str],
) -> StepResult:
    """Settle every sense that calls one target both a synonym and a hypernym.

    Proper nouns are settled by rule for free; everything else costs one nano call per
    entry, whatever the number of pairs on it.

    Args:
        store: The store to clean. Each entry is read, settled — including its one call
            when one is due — and written inside one hold of its own lock.
        runner: The stage runner.
        ids: The entry ids to visit.
        workers: Pool size.
        stop_event: Shared stop event.
        changed_ids: Run-level set of entries written by any step.

    Returns:
        The step's :class:`StepResult`.
    """
    tally = _Tally(ContentHygieneStep.SYNONYM_HYPERNYM, changed_ids)
    gloss_cache: dict[str, str] = {}

    async def settle(lexeme_id: str) -> None:
        demoted = retyped = accepted = rejected = 0
        async with store.locked(lexeme_id):
            entry = store.read(lexeme_id)
            if entry is None:
                return
            pairs = _collect_pairs(entry, store, gloss_cache)
            needs_write = False
            if entry.kind is LexemeKind.PROPER_NOUN:
                demoted, retyped = _resolve_proper_noun_pairs(entry, pairs)
                needs_write = bool(demoted)
            else:
                marker = _attempt_due(
                    entry, _SYNONYM_HYPERNYM_PREFIX, [pair.ref_id for pair in pairs]
                )
                if marker is not None:
                    demoted, accepted, rejected = await _choose_relations(
                        entry, pairs, runner, tally, marker
                    )
                    # Written even when nothing was demoted: the sentinel is the only
                    # thing that call bought, and losing it re-bills the same answer.
                    needs_write = True
            if needs_write:
                store.write(entry)
        await tally.entry(
            lexeme_id, demoted=demoted, retyped=retyped, accepted=accepted, rejected=rejected
        )

    await _drive(ids, settle, tally, workers=workers, stop_event=stop_event)
    return tally.result


# --------------------------------------------------------------------------------------
# Step 4 — garbage_examples (free)
# --------------------------------------------------------------------------------------


def _is_garbage_example(text: str) -> bool:
    """Return whether an example's text is not a sentence at all.

    Args:
        text: The example text, exactly as stored.

    Returns:
        Whether it has fewer than :data:`_MIN_EXAMPLE_WORDS` words or contains no ASCII
        letter — the two shapes the 21 measured offenders take (``hypernyms([``, ``?``,
        and bare single words).
    """
    return word_count(text) < _MIN_EXAMPLE_WORDS or _ASCII_LETTER_RE.search(text) is None


def _remove_garbage_examples(entry: Lexeme) -> int:
    """Remove every example rendition that is not a sentence, in place.

    The one deletion this workflow performs, and the text is preserved before it happens:
    each removed example's text goes into a zero-cost ``Provenance.note`` reading
    ``removed garbage example: <text>``, so the entry still carries what was there.

    A sense left with no canonical example at all is not repaired here.
    ``workflows/retrofit.py``'s ``repair`` pass step (b) already writes canonical examples
    for exactly that condition, so this step does not call it and does not duplicate it —
    run ``retrofit --only repair`` after this workflow.

    Args:
        entry: The entry to clean, mutated in place.

    Returns:
        How many example renditions were removed.
    """
    removed = 0
    for _, sense, _ in _live_senses(entry):
        # Selected by position, not by value: two example renditions can hold equal
        # content, and ``list.remove``/``in`` would then take out the wrong one.
        doomed = {
            index
            for index, rendition in enumerate(sense.examples)
            if _is_garbage_example(rendition.content.text)
        }
        if not doomed:
            continue
        for index in sorted(doomed):
            entry.add_provenance(
                _rule_provenance(f"{GARBAGE_EXAMPLE_NOTE}{sense.examples[index].content.text}")
            )
        sense.examples.root = [
            rendition for index, rendition in enumerate(sense.examples) if index not in doomed
        ]
        removed += len(doomed)
    return removed


async def _garbage_examples_step(
    store: LexemeStore,
    runner: StageRunner,
    ids: Sequence[str],
    *,
    workers: int,
    stop_event: asyncio.Event | None,
    changed_ids: set[str],
) -> StepResult:
    """Remove every example that is not a sentence, keeping its text in a note, for $0.

    Args:
        store: The store to clean.
        runner: Unused — this step makes no model call.
        ids: The entry ids to visit.
        workers: Pool size.
        stop_event: Shared stop event.
        changed_ids: Run-level set of entries written by any step.

    Returns:
        The step's :class:`StepResult`.
    """
    del runner  # free step
    tally = _Tally(ContentHygieneStep.GARBAGE_EXAMPLES, changed_ids)

    async def clean(lexeme_id: str) -> None:
        removed = 0
        async with store.locked(lexeme_id):
            entry = store.read(lexeme_id)
            if entry is None:
                return
            removed = _remove_garbage_examples(entry)
            if removed:
                store.write(entry)
        await tally.entry(lexeme_id, removed=removed)

    await _drive(ids, clean, tally, workers=workers, stop_event=stop_event)
    return tally.result


# --------------------------------------------------------------------------------------
# Step 5 — stilted_examples
# --------------------------------------------------------------------------------------
#
# Like every other call site in this module, the instructions and the output contract are
# module-private. What they are *not* is newly written: the rule an example sentence has
# to satisfy is sliced verbatim out of RENDITIONS_INSTRUCTIONS at import time, so a
# rewrite is held to the bar the original generation was held to and the two cannot drift.


def _extract_instructions_block(source: str, start_marker: str, end_marker: str) -> str:
    """Return the substring of ``source`` between two markers, trimmed.

    Used only at import time, to lift a section of
    :data:`~opengloss_generator.prompts.RENDITIONS_INSTRUCTIONS` verbatim into this
    module's own instructions rather than retyping it.

    Args:
        source: The text to slice.
        start_marker: The literal text the wanted section begins with.
        end_marker: The literal text that follows the wanted section.

    Returns:
        The text from ``start_marker`` up to (not including) ``end_marker``, stripped.

    Raises:
        ValueError: If either marker is absent — a signal that
            ``RENDITIONS_INSTRUCTIONS`` changed shape and these instructions need
            re-slicing, not that the entries being processed are at fault.
    """
    start = source.index(start_marker)
    end = source.index(end_marker, start)
    return source[start:end].strip()


#: The ``examples`` paragraph of ``RENDITIONS_INSTRUCTIONS``' field-meaning block,
#: verbatim: what an example sentence is for, including its own "do not open with
#: Researchers" clause — which is the defect this step exists to repair.
_EXAMPLE_FIELD_RULE = _extract_instructions_block(
    prompts.RENDITIONS_INSTRUCTIONS, "examples - one fresh", "\n\nencyclopedia -"
)

#: The "READING LEVELS." section of ``RENDITIONS_INSTRUCTIONS``, verbatim.
_LEVEL_RULES = _extract_instructions_block(
    prompts.RENDITIONS_INSTRUCTIONS, "READING LEVELS.", "\n\nREGISTERS."
)

#: The "REGISTERS." section of ``RENDITIONS_INSTRUCTIONS``, verbatim.
_REGISTER_RULES = _extract_instructions_block(
    prompts.RENDITIONS_INSTRUCTIONS, "REGISTERS.", "\n\nWHAT THE FIELD MEANS FOR YOUR OUTPUT."
)

#: The one-sentence headword-initial rule of ``RENDITIONS_INSTRUCTIONS``, verbatim.
_HEADWORD_RULE = _extract_instructions_block(
    prompts.RENDITIONS_INSTRUCTIONS, "Never begin a definition rendition", "\n\nFormatting,"
)

#: Instructions for this step's one luna call per entry. The example rule is sliced, not
#: restated (see the section comment above); only the framing and the answer format are
#: new.
STILTED_EXAMPLES_INSTRUCTIONS = f"""\
Rewrite each numbered example sentence below. Each is a dictionary's own example for the \
sense printed beside it, and each is written in a stilted academic register -- "Two \
researchers formed a duo to complete the project." -- which is not how anyone uses the \
word.

Write one natural everyday sentence for the same sense: the kind of thing a person would \
actually write or say, in a kitchen, an office or a street rather than a laboratory. The \
sentence must contain the headword itself or a natural inflected form of it, or it will \
be discarded. At most 20 words.

{_EXAMPLE_FIELD_RULE}

Formatting: plain prose, no markdown. Write one sentence and nothing else.

Answer every sentence you are given, identified by the number it was listed under."""


class _DraftStiltedRewrite(BaseModel):
    """One replacement sentence for a stilted canonical example."""

    model_config = ConfigDict(
        extra="forbid", validate_by_name=True, validate_by_alias=True, serialize_by_alias=True
    )

    ref: Annotated[int, Field(ge=1)]
    text: Annotated[str, Field(min_length=3, max_length=1000)]


class _DraftStiltedRewrites(BaseModel):
    """Replacements for every stilted canonical example of one entry, produced together."""

    model_config = ConfigDict(
        extra="forbid", validate_by_name=True, validate_by_alias=True, serialize_by_alias=True
    )

    rewrites: Annotated[list[_DraftStiltedRewrite], Field(min_length=1)]


@dataclass(slots=True)
class _StiltedExample:
    """One canonical example written in an academic register.

    Attributes:
        rendition: The offending rendition, mutated in place once a rewrite is adopted.
        sense: The owning sense, needed to check a rewrite against its sibling examples.
        pos_entry: The owning part-of-speech entry, for the headword's inflected forms.
        gloss: The sense's canonical gloss, shown to the model so the replacement fits
            this sense and no other.
        ref_id: A stable identifier for the digest. Example renditions have no unique
            keyed id — several may share one ``(level, register)`` — so theirs carries
            the offender's position in its sense's example list as well.
    """

    rendition: Rendition[Example]
    sense: Sense
    pos_entry: POSEntry
    gloss: str
    ref_id: str


def _stilted_examples(entry: Lexeme) -> list[_StiltedExample]:
    """Return every canonical example of an entry written in an academic register.

    Only the canonical ``(neutral, plain)`` examples are offenders. A ``college``
    rendition using the same words is doing exactly what its target asks of it, and
    rewriting it would be the defect rather than the repair.

    Args:
        entry: The entry to inspect. Never mutated.

    Returns:
        One :class:`_StiltedExample` per offender, in document order — the order the model
        is shown them in and refers to them by.
    """
    offenders: list[_StiltedExample] = []
    for pos_entry, sense, sid in _live_senses(entry):
        gloss = sense.canonical_gloss()
        for index, rendition in enumerate(sense.examples):
            if rendition.key != CANONICAL_KEY:
                continue
            if not STILTED_RE.search(rendition.content.text):
                continue
            offenders.append(
                _StiltedExample(
                    rendition=rendition,
                    sense=sense,
                    pos_entry=pos_entry,
                    gloss=gloss,
                    ref_id=rendition_id(sid, rendition.reading_level.value, rendition.style.value)
                    + f"[{index}]",
                )
            )
    return offenders


def _build_stilted_prompt(headword: str, offenders: Sequence[_StiltedExample]) -> str:
    """Return the volatile half of this step's rewrite prompt.

    Args:
        headword: The lexeme's surface form.
        offenders: The examples to rewrite, in the order the model should answer them —
            ``ref`` in the reply is a 1-based position into this sequence.

    Returns:
        The per-call prompt body.
    """
    lines = [
        # Collapsed to one line: the listing format below is one item per line.
        f"  {i + 1}. [{offender.gloss}] {' '.join(offender.rendition.content.text.split())}"
        for i, offender in enumerate(offenders)
    ]
    listed = "\n".join(lines)
    return f"Headword: {headword}\nExamples ({len(offenders)}):\n{listed}"


def _apply_stilted_rewrite(
    entry: Lexeme,
    offender: _StiltedExample,
    drafted_text: str,
    base_provenance: Provenance,
) -> bool:
    """Adopt one replacement sentence, if it is actually usable.

    Three conditions, all cheap and all deterministic: the markdown-stripped rewrite must
    be new, :func:`~opengloss_generator.spans.find_span` must be able to place the
    headword in it (a sentence that dropped the word it illustrates has traded one defect
    for the worse one ``workflows/example_hygiene.py`` exists to repair), and it must not
    collide with another example of the same sense at the same key, which
    :class:`~opengloss_generator.schema.Renditions` forbids. An adopted rewrite gets its
    span from the same find and its Flesch-Kincaid grade re-measured with the headword
    scored as one syllable, matching how every other rendition in the project is measured.

    Args:
        entry: The entry the example belongs to, mutated in place.
        offender: The offending example and the context needed to rewrite it.
        drafted_text: The model's proposed replacement, before markdown stripping.
        base_provenance: The call's own record, used to build the zero-cost note record
            that keeps the superseded text.

    Returns:
        Whether the rewrite was adopted.
    """
    example = offender.rendition.content
    new_text = strip_markdown(drafted_text)
    if not new_text or new_text == example.text:
        return False
    span = spans.find_span(new_text, entry.headword, _forms_for(entry, offender.pos_entry))
    if span is None:
        _LOG.info(
            "content_hygiene_stilted_rejected_no_span",
            headword=entry.headword,
            rendition=offender.ref_id,
        )
        return False
    collides = any(
        other is not offender.rendition
        and other.key == offender.rendition.key
        and other.content.text == new_text
        for other in offender.sense.examples
    )
    if collides:
        return False

    old_text = example.text
    example.text = new_text
    example.span = span
    offender.rendition.provenance_id = entry.add_provenance(
        _note_provenance(base_provenance, old_text)
    )
    assessment = offender.rendition.assessment or Assessment()
    assessment.readability_grade = round(
        flesch_kincaid_grade(new_text, ignore=(entry.headword,)), 2
    )
    offender.rendition.assessment = assessment
    return True


async def _rewrite_stilted(
    entry: Lexeme,
    offenders: Sequence[_StiltedExample],
    runner: StageRunner,
    tally: _Tally,
    marker_note: str,
) -> tuple[int, int]:
    """Ask luna for everyday replacements and adopt the ones that keep the headword.

    Args:
        entry: The entry whose examples need rewriting, mutated in place.
        offenders: The examples to rewrite, in the order the model was shown them.
        runner: The stage runner.
        tally: The step tally, for the call and its cost.
        marker_note: The offending-set sentinel to stamp on the call's record.

    Returns:
        ``(rewrites adopted, rewrites refused)``.

    Raises:
        BudgetExceededError: A budget stop is a run-level condition and propagates.
    """
    try:
        stage_result = await runner.run(
            # Reuses the RENDITIONS policy (luna): this is prose for a reader, held to a
            # register, not a structural verdict.
            stage=StageName.RENDITIONS,
            output_type=_DraftStiltedRewrites,
            instructions=STILTED_EXAMPLES_INSTRUCTIONS,
            prompt=_build_stilted_prompt(entry.headword, offenders),
            prompt_version=PROMPT_VERSION,
        )
    except BudgetExceededError:
        raise
    except GenerationError as exc:
        _LOG.warning("content_hygiene_stilted_failed", headword=entry.headword, error=str(exc))
        return 0, 0

    await tally.call(stage_result.cost_usd)
    entry.add_provenance(stage_result.provenance.model_copy(update={"note": marker_note}))

    accepted = rejected = 0
    for drafted in stage_result.output.rewrites:
        position = drafted.ref - 1
        if not 0 <= position < len(offenders):
            rejected += 1
            continue
        if _apply_stilted_rewrite(
            entry, offenders[position], drafted.text, stage_result.provenance
        ):
            accepted += 1
        else:
            rejected += 1
    return accepted, rejected


async def _stilted_examples_step(
    store: LexemeStore,
    runner: StageRunner,
    ids: Sequence[str],
    *,
    workers: int,
    stop_event: asyncio.Event | None,
    changed_ids: set[str],
) -> StepResult:
    """Rewrite every canonical example written in a stilted academic register.

    Args:
        store: The store to clean. Each entry is read, cleaned — including its one call
            when one is due — and written inside one hold of its own lock.
        runner: The stage runner.
        ids: The entry ids to visit.
        workers: Pool size.
        stop_event: Shared stop event.
        changed_ids: Run-level set of entries written by any step.

    Returns:
        The step's :class:`StepResult`.
    """
    tally = _Tally(ContentHygieneStep.STILTED_EXAMPLES, changed_ids)

    async def clean(lexeme_id: str) -> None:
        accepted = rejected = 0
        async with store.locked(lexeme_id):
            entry = store.read(lexeme_id)
            if entry is None:
                return
            offenders = _stilted_examples(entry)
            marker = _attempt_due(entry, _STILTED_PREFIX, [o.ref_id for o in offenders])
            if marker is not None:
                accepted, rejected = await _rewrite_stilted(entry, offenders, runner, tally, marker)
                # Written even when nothing was adopted: the sentinel is the only thing
                # that call bought, and losing it re-bills the same answer.
                store.write(entry)
        await tally.entry(lexeme_id, rewritten=accepted, accepted=accepted, rejected=rejected)

    await _drive(ids, clean, tally, workers=workers, stop_event=stop_event)
    return tally.result


# --------------------------------------------------------------------------------------
# Step 6 — fragment_examples
# --------------------------------------------------------------------------------------
#
# Same technique as step 5: the instructions and contract are module-private, and the
# ``examples`` field rule is the same :data:`_EXAMPLE_FIELD_RULE` slice step 5 already
# lifted out of ``RENDITIONS_INSTRUCTIONS`` at import time — one slice, reused, rather
# than a second one of the same text.


#: Instructions for this step's one luna call per entry. The example rule is the same
#: slice :data:`STILTED_EXAMPLES_INSTRUCTIONS` uses; only the framing is new.
FRAGMENT_EXAMPLES_INSTRUCTIONS = f"""\
Rewrite each numbered example sentence below. Each is a dictionary's own example for the \
sense printed beside it, and each is a sentence fragment rather than a complete sentence \
-- it starts with a lowercase letter, has no closing punctuation, or both, the way "the \
mile-long bridge opened to traffic" is missing both the capital and the period a sentence \
needs.

Write one complete, natural sentence for the same sense: it must begin with a capital \
letter and end with terminal punctuation, and it must contain the headword itself or a \
natural inflected form of it, or it will be discarded. At most 20 words.

{_EXAMPLE_FIELD_RULE}

Formatting: plain prose, no markdown. Write one sentence and nothing else.

Answer every sentence you are given, identified by the number it was listed under."""


class _DraftFragmentRewrite(BaseModel):
    """One replacement sentence for a fragment canonical example."""

    model_config = ConfigDict(
        extra="forbid", validate_by_name=True, validate_by_alias=True, serialize_by_alias=True
    )

    ref: Annotated[int, Field(ge=1)]
    text: Annotated[str, Field(min_length=3, max_length=1000)]


class _DraftFragmentRewrites(BaseModel):
    """Replacements for every fragment canonical example of one entry, produced together."""

    model_config = ConfigDict(
        extra="forbid", validate_by_name=True, validate_by_alias=True, serialize_by_alias=True
    )

    rewrites: Annotated[list[_DraftFragmentRewrite], Field(min_length=1)]


@dataclass(slots=True)
class _FragmentExample:
    """One canonical example that is a sentence fragment rather than a sentence.

    Attributes:
        rendition: The offending rendition, mutated in place once a rewrite is adopted.
        sense: The owning sense, needed to check a rewrite against its sibling examples.
        pos_entry: The owning part-of-speech entry, for the headword's inflected forms.
        gloss: The sense's canonical gloss, shown to the model so the replacement fits
            this sense and no other.
        ref_id: A stable identifier for the digest — the offender's position in its
            sense's example list, the same shape :class:`_StiltedExample` uses and for
            the same reason: example renditions carry no unique keyed id of their own.
    """

    rendition: Rendition[Example]
    sense: Sense
    pos_entry: POSEntry
    gloss: str
    ref_id: str


def _is_fragment_example(text: str) -> bool:
    """Return whether an example is a sentence fragment rather than a complete sentence.

    Measured on the 10K core: 498 canonical examples start with a lowercase letter, carry
    no terminal punctuation, or both (``docs/QA-DIARY.md``, iteration 3's after-sample) —
    "the mile-long bridge opened to traffic" has neither. A leading quote mark is stripped
    before the lowercase check, since a sentence quoted whole still opens on its own
    capital one character in; nothing is stripped from the end, because a trailing quote
    or closing parenthesis is already in :data:`_FRAGMENT_TERMINAL_CHARS` and counts as
    terminal in its own right.

    Args:
        text: The example text, exactly as stored.

    Returns:
        Whether the text starts lowercase, ends without terminal punctuation, or both.
    """
    stripped = text.strip()
    if not stripped:
        return False
    start = stripped.lstrip(_FRAGMENT_QUOTE_CHARS) or stripped
    starts_lower = start[0].isalpha() and start[0].islower()
    ends_terminal = stripped[-1] in _FRAGMENT_TERMINAL_CHARS
    return starts_lower or not ends_terminal


def _fragment_examples(entry: Lexeme) -> list[_FragmentExample]:
    """Return every canonical example of an entry that is a sentence fragment.

    Only the canonical ``(neutral, plain)`` examples are offenders, mirroring
    :func:`_stilted_examples`: a ``college`` rendition that happens to share the fragment's
    words is doing its own job, not this step's.

    Args:
        entry: The entry to inspect. Never mutated.

    Returns:
        One :class:`_FragmentExample` per offender, in document order — the order the
        model is shown them in and refers to them by.
    """
    offenders: list[_FragmentExample] = []
    for pos_entry, sense, sid in _live_senses(entry):
        gloss = sense.canonical_gloss()
        for index, rendition in enumerate(sense.examples):
            if rendition.key != CANONICAL_KEY:
                continue
            if not _is_fragment_example(rendition.content.text):
                continue
            offenders.append(
                _FragmentExample(
                    rendition=rendition,
                    sense=sense,
                    pos_entry=pos_entry,
                    gloss=gloss,
                    ref_id=rendition_id(sid, rendition.reading_level.value, rendition.style.value)
                    + f"[{index}]",
                )
            )
    return offenders


def _build_fragment_prompt(headword: str, offenders: Sequence[_FragmentExample]) -> str:
    """Return the volatile half of this step's rewrite prompt.

    Args:
        headword: The lexeme's surface form.
        offenders: The examples to rewrite, in the order the model should answer them —
            ``ref`` in the reply is a 1-based position into this sequence.

    Returns:
        The per-call prompt body.
    """
    lines = [
        # Collapsed to one line: the listing format below is one item per line.
        f"  {i + 1}. [{offender.gloss}] {' '.join(offender.rendition.content.text.split())}"
        for i, offender in enumerate(offenders)
    ]
    listed = "\n".join(lines)
    return f"Headword: {headword}\nExamples ({len(offenders)}):\n{listed}"


def _apply_fragment_rewrite(
    entry: Lexeme,
    offender: _FragmentExample,
    drafted_text: str,
    base_provenance: Provenance,
) -> bool:
    """Adopt one replacement sentence, if it is actually a complete sentence.

    Four conditions, all cheap and all deterministic: the markdown-stripped rewrite must
    be new; it must start with an uppercase letter and end in one of
    :data:`_FRAGMENT_TERMINAL_CHARS` — the fragment defect actually repaired, not merely
    reworded into another fragment; :func:`~opengloss_generator.spans.find_span` must be
    able to place the headword in it, the same non-negotiable
    :func:`_apply_stilted_rewrite` holds every rewrite to; and it must not collide with
    another example of the same sense at the same key. An adopted rewrite gets its span
    from the same find and its Flesch-Kincaid grade re-measured with the headword scored
    as one syllable, matching how every other rendition in the project is measured.

    Args:
        entry: The entry the example belongs to, mutated in place.
        offender: The offending example and the context needed to rewrite it.
        drafted_text: The model's proposed replacement, before markdown stripping.
        base_provenance: The call's own record, used to build the zero-cost note record
            that keeps the superseded text.

    Returns:
        Whether the rewrite was adopted.
    """
    example = offender.rendition.content
    new_text = strip_markdown(drafted_text)
    if not new_text or new_text == example.text:
        return False
    if not new_text[0].isupper() or new_text[-1] not in _FRAGMENT_TERMINAL_CHARS:
        _LOG.info(
            "content_hygiene_fragment_rejected_still_a_fragment",
            headword=entry.headword,
            rendition=offender.ref_id,
        )
        return False
    span = spans.find_span(new_text, entry.headword, _forms_for(entry, offender.pos_entry))
    if span is None:
        _LOG.info(
            "content_hygiene_fragment_rejected_no_span",
            headword=entry.headword,
            rendition=offender.ref_id,
        )
        return False
    collides = any(
        other is not offender.rendition
        and other.key == offender.rendition.key
        and other.content.text == new_text
        for other in offender.sense.examples
    )
    if collides:
        return False

    old_text = example.text
    example.text = new_text
    example.span = span
    offender.rendition.provenance_id = entry.add_provenance(
        _note_provenance(base_provenance, f"{FRAGMENT_EXAMPLE_NOTE}{old_text}")
    )
    assessment = offender.rendition.assessment or Assessment()
    assessment.readability_grade = round(
        flesch_kincaid_grade(new_text, ignore=(entry.headword,)), 2
    )
    offender.rendition.assessment = assessment
    return True


async def _rewrite_fragments(
    entry: Lexeme,
    offenders: Sequence[_FragmentExample],
    runner: StageRunner,
    tally: _Tally,
    marker_note: str,
) -> tuple[int, int]:
    """Ask luna for complete sentences and adopt the ones that are actually complete.

    Args:
        entry: The entry whose examples need rewriting, mutated in place.
        offenders: The examples to rewrite, in the order the model was shown them.
        runner: The stage runner.
        tally: The step tally, for the call and its cost.
        marker_note: The offending-set sentinel to stamp on the call's record.

    Returns:
        ``(rewrites adopted, rewrites refused)``.

    Raises:
        BudgetExceededError: A budget stop is a run-level condition and propagates.
    """
    try:
        stage_result = await runner.run(
            # Reuses the RENDITIONS policy (luna): this is prose for a reader, not a
            # structural verdict.
            stage=StageName.RENDITIONS,
            output_type=_DraftFragmentRewrites,
            instructions=FRAGMENT_EXAMPLES_INSTRUCTIONS,
            prompt=_build_fragment_prompt(entry.headword, offenders),
            prompt_version=PROMPT_VERSION,
        )
    except BudgetExceededError:
        raise
    except GenerationError as exc:
        _LOG.warning("content_hygiene_fragment_failed", headword=entry.headword, error=str(exc))
        return 0, 0

    await tally.call(stage_result.cost_usd)
    entry.add_provenance(stage_result.provenance.model_copy(update={"note": marker_note}))

    accepted = rejected = 0
    for drafted in stage_result.output.rewrites:
        position = drafted.ref - 1
        if not 0 <= position < len(offenders):
            rejected += 1
            continue
        if _apply_fragment_rewrite(
            entry, offenders[position], drafted.text, stage_result.provenance
        ):
            accepted += 1
        else:
            rejected += 1
    return accepted, rejected


async def _fragment_examples_step(
    store: LexemeStore,
    runner: StageRunner,
    ids: Sequence[str],
    *,
    workers: int,
    stop_event: asyncio.Event | None,
    changed_ids: set[str],
) -> StepResult:
    """Rewrite every canonical example that is a sentence fragment.

    Args:
        store: The store to clean. Each entry is read, cleaned — including its one call
            when one is due — and written inside one hold of its own lock.
        runner: The stage runner.
        ids: The entry ids to visit.
        workers: Pool size.
        stop_event: Shared stop event.
        changed_ids: Run-level set of entries written by any step.

    Returns:
        The step's :class:`StepResult`.
    """
    tally = _Tally(ContentHygieneStep.FRAGMENT_EXAMPLES, changed_ids)

    async def clean(lexeme_id: str) -> None:
        accepted = rejected = 0
        async with store.locked(lexeme_id):
            entry = store.read(lexeme_id)
            if entry is None:
                return
            offenders = _fragment_examples(entry)
            marker = _attempt_due(entry, _FRAGMENT_PREFIX, [o.ref_id for o in offenders])
            if marker is not None:
                accepted, rejected = await _rewrite_fragments(
                    entry, offenders, runner, tally, marker
                )
                # Written even when nothing was adopted: the sentinel is the only thing
                # that call bought, and losing it re-bills the same answer.
                store.write(entry)
        await tally.entry(lexeme_id, rewritten=accepted, accepted=accepted, rejected=rejected)

    await _drive(ids, clean, tally, workers=workers, stop_event=stop_event)
    return tally.result


# --------------------------------------------------------------------------------------
# Step 7 — degenerate_renditions
# --------------------------------------------------------------------------------------


#: Instructions for this step's one luna call per entry. The headword-initial rule, the
#: reading-level constraints and the register scale are sliced out of
#: ``RENDITIONS_INSTRUCTIONS`` verbatim rather than restated, so a rewrite is held to the
#: exact bar the original rendition was written against.
DEGENERATE_RENDITIONS_INSTRUCTIONS = f"""\
Each numbered item below is one reading-level and register rendition of a dictionary \
definition, and each one is degenerate: it repeats the sense's canonical definition \
word for word, or it repeats another rendition written for a different target. A \
rendition set exists so that each target says the same thing differently; two identical \
texts carry one text's worth of information.

Rewrite each item for the exact reading level and register it is labelled with. The \
rewrite must mean what the canonical definition means, must not reuse its wording, and \
must differ from every other rendition of the same definition. It is not a paraphrase: \
read the canonical definition, work out what it means, then write what a reader at that \
level and in that register would understand on first reading.

{_HEADWORD_RULE}

{_LEVEL_RULES}

{_REGISTER_RULES}

Formatting: plain prose, no markdown. No bold, no italics, no backticks, no bullets, no \
headings, no numbered lists, and no asterisks or underscores used for emphasis.

Answer every item you are given, identified by the number it was listed under."""


class _DraftDegenerateRewrite(BaseModel):
    """One replacement text for a degenerate gloss rendition."""

    model_config = ConfigDict(
        extra="forbid", validate_by_name=True, validate_by_alias=True, serialize_by_alias=True
    )

    ref: Annotated[int, Field(ge=1)]
    text: Annotated[str, Field(min_length=3, max_length=2000)]


class _DraftDegenerateRewrites(BaseModel):
    """Replacements for every degenerate gloss rendition of one entry, produced together."""

    model_config = ConfigDict(
        extra="forbid", validate_by_name=True, validate_by_alias=True, serialize_by_alias=True
    )

    rewrites: Annotated[list[_DraftDegenerateRewrite], Field(min_length=1)]


@dataclass(slots=True)
class _DegenerateRendition:
    """One gloss rendition carrying no information its set does not already carry.

    Attributes:
        rendition: The offending rendition, mutated in place once a rewrite is adopted.
        sense: The owning sense, whose whole gloss set a rewrite must differ from.
        canonical: The sense's canonical gloss.
        label: ``"level/register"``, which is what tells the model the audience to hold.
        reason: ``"canonical"`` when the text copies the canonical gloss, ``"sibling"``
            when it copies another rendition. Logged, not shown to the model — the
            instruction is the same either way.
        ref_id: The rendition's derived identifier, which is what the digest is taken
            over: stable across sweeps, unlike its text or its position.
    """

    rendition: Rendition[str]
    sense: Sense
    canonical: str
    label: str
    reason: str
    ref_id: str


def _degenerate_renditions(entry: Lexeme) -> list[_DegenerateRendition]:
    """Return every gloss rendition of an entry that duplicates another.

    Two shapes, both measured: a non-canonical rendition whose normalised text matches the
    canonical gloss, and the second and later members of any group of non-canonical
    renditions that share one normalised text. The *first* member of such a group is not
    an offender — one of the two has to survive, and rewriting the earlier target would
    be an arbitrary choice with the same result.

    Args:
        entry: The entry to inspect. Never mutated.

    Returns:
        One :class:`_DegenerateRendition` per offender, in document order — the order the
        model is shown them in and refers to them by.
    """
    offenders: list[_DegenerateRendition] = []
    for _, sense, sid in _live_senses(entry):
        canonical = sense.canonical_gloss()
        canonical_key = _normalised(canonical)
        seen: set[str] = set()
        for rendition in sense.gloss:
            if rendition.is_canonical:
                continue
            key = _normalised(rendition.content)
            if key == canonical_key:
                reason = "canonical"
            elif key in seen:
                reason = "sibling"
            else:
                seen.add(key)
                continue
            offenders.append(
                _DegenerateRendition(
                    rendition=rendition,
                    sense=sense,
                    canonical=canonical,
                    label=f"{rendition.reading_level.value}/{rendition.style.value}",
                    reason=reason,
                    ref_id=rendition_id(sid, rendition.reading_level.value, rendition.style.value),
                )
            )
    return offenders


def _build_degenerate_prompt(headword: str, offenders: Sequence[_DegenerateRendition]) -> str:
    """Return the volatile half of this step's rewrite prompt.

    Args:
        headword: The lexeme's surface form.
        offenders: The renditions to rewrite, in the order the model should answer them —
            ``ref`` in the reply is a 1-based position into this sequence.

    Returns:
        The per-call prompt body.
    """
    lines = [
        f"  {i + 1}. [{offender.label}] canonical: {' '.join(offender.canonical.split())} "
        f"| current: {' '.join(offender.rendition.content.split())}"
        for i, offender in enumerate(offenders)
    ]
    listed = "\n".join(lines)
    return f"Headword: {headword}\nRenditions ({len(offenders)}):\n{listed}"


def _degenerate_rewrite_is_usable(
    entry: Lexeme, offender: _DegenerateRendition, new_text: str
) -> bool:
    """Return whether a proposed rendition rewrite may be adopted.

    Three conditions. It must not begin by naming its own headword — the easiest way to
    write a short definition is the way a dictionary must not, and this step's whole
    output is short definitions (proper nouns are exempt, D-30). It must not equal the
    canonical gloss, which is the defect for half these offenders. And it must not equal
    any other rendition in the same set, which is the defect for the other half; checking
    against the set as it stands *now* is what lets several offenders on one sense be
    applied one after another without the second recreating the first's duplicate.

    Args:
        entry: The entry the rendition belongs to.
        offender: The offending rendition.
        new_text: The markdown-stripped rewrite under consideration.

    Returns:
        Whether the rewrite may be adopted. A refusal is logged, since it is the outcome
        a prompt change could reduce.
    """
    if entry.kind is not LexemeKind.PROPER_NOUN and is_headword_initial(new_text, entry.headword):
        _LOG.info(
            "content_hygiene_degenerate_rejected_headword_initial",
            headword=entry.headword,
            rendition=offender.ref_id,
        )
        return False
    key = _normalised(new_text)
    if key == _normalised(offender.canonical):
        _LOG.info(
            "content_hygiene_degenerate_rejected_identical",
            headword=entry.headword,
            rendition=offender.ref_id,
            against="canonical",
        )
        return False
    for other in offender.sense.gloss:
        if other is offender.rendition:
            continue
        if _normalised(other.content) == key:
            _LOG.info(
                "content_hygiene_degenerate_rejected_identical",
                headword=entry.headword,
                rendition=offender.ref_id,
                against="sibling",
            )
            return False
    return True


def _apply_degenerate_rewrite(
    entry: Lexeme,
    offender: _DegenerateRendition,
    drafted_text: str,
    base_provenance: Provenance,
) -> bool:
    """Adopt one rendition rewrite, if it is usable, and re-measure what is stored.

    Args:
        entry: The entry the rendition belongs to, mutated in place.
        offender: The offending rendition.
        drafted_text: The model's proposed replacement, before markdown stripping.
        base_provenance: The call's own record, used to build the zero-cost note record
            that keeps the superseded text.

    Returns:
        Whether the rewrite was adopted.
    """
    new_text = strip_markdown(drafted_text)
    if not new_text or not _degenerate_rewrite_is_usable(entry, offender, new_text):
        return False

    old_text = offender.rendition.content
    offender.rendition.content = new_text
    offender.rendition.provenance_id = entry.add_provenance(
        _note_provenance(base_provenance, old_text)
    )
    assessment = offender.rendition.assessment or Assessment()
    assessment.readability_grade = round(
        flesch_kincaid_grade(new_text, ignore=(entry.headword,)), 2
    )
    offender.rendition.assessment = assessment
    return True


async def _rewrite_degenerate(
    entry: Lexeme,
    offenders: Sequence[_DegenerateRendition],
    runner: StageRunner,
    tally: _Tally,
    marker_note: str,
) -> tuple[int, int]:
    """Ask luna for distinct renditions and adopt the ones that are actually distinct.

    Args:
        entry: The entry whose renditions need rewriting, mutated in place.
        offenders: The renditions to rewrite, in the order the model was shown them.
        runner: The stage runner.
        tally: The step tally, for the call and its cost.
        marker_note: The offending-set sentinel to stamp on the call's record.

    Returns:
        ``(rewrites adopted, rewrites refused)``.

    Raises:
        BudgetExceededError: A budget stop is a run-level condition and propagates.
    """
    try:
        stage_result = await runner.run(
            # Reuses the RENDITIONS policy (luna): this is prose for an audience, held to
            # a reading level and a register, which is exactly that stage's own job.
            stage=StageName.RENDITIONS,
            output_type=_DraftDegenerateRewrites,
            instructions=DEGENERATE_RENDITIONS_INSTRUCTIONS,
            prompt=_build_degenerate_prompt(entry.headword, offenders),
            prompt_version=PROMPT_VERSION,
        )
    except BudgetExceededError:
        raise
    except GenerationError as exc:
        _LOG.warning("content_hygiene_degenerate_failed", headword=entry.headword, error=str(exc))
        return 0, 0

    await tally.call(stage_result.cost_usd)
    entry.add_provenance(stage_result.provenance.model_copy(update={"note": marker_note}))

    accepted = rejected = 0
    for drafted in stage_result.output.rewrites:
        position = drafted.ref - 1
        if not 0 <= position < len(offenders):
            rejected += 1
            continue
        if _apply_degenerate_rewrite(
            entry, offenders[position], drafted.text, stage_result.provenance
        ):
            accepted += 1
        else:
            rejected += 1
    return accepted, rejected


async def _degenerate_renditions_step(
    store: LexemeStore,
    runner: StageRunner,
    ids: Sequence[str],
    *,
    workers: int,
    stop_event: asyncio.Event | None,
    changed_ids: set[str],
) -> StepResult:
    """Rewrite every gloss rendition that duplicates its canonical or a sibling.

    Args:
        store: The store to clean. Each entry is read, cleaned — including its one call
            when one is due — and written inside one hold of its own lock.
        runner: The stage runner.
        ids: The entry ids to visit.
        workers: Pool size.
        stop_event: Shared stop event.
        changed_ids: Run-level set of entries written by any step.

    Returns:
        The step's :class:`StepResult`.
    """
    tally = _Tally(ContentHygieneStep.DEGENERATE_RENDITIONS, changed_ids)

    async def clean(lexeme_id: str) -> None:
        accepted = rejected = 0
        async with store.locked(lexeme_id):
            entry = store.read(lexeme_id)
            if entry is None:
                return
            offenders = _degenerate_renditions(entry)
            marker = _attempt_due(entry, _DEGENERATE_PREFIX, [o.ref_id for o in offenders])
            if marker is not None:
                accepted, rejected = await _rewrite_degenerate(
                    entry, offenders, runner, tally, marker
                )
                # Written even when nothing was adopted: the sentinel is the only thing
                # that call bought, and losing it re-bills the same answer.
                store.write(entry)
        await tally.entry(lexeme_id, rewritten=accepted, accepted=accepted, rejected=rejected)

    await _drive(ids, clean, tally, workers=workers, stop_event=stop_event)
    return tally.result


# --------------------------------------------------------------------------------------
# The entry point
# --------------------------------------------------------------------------------------


#: One step, as :func:`run_content_hygiene` calls it. Every step takes the store, the
#: runner and the id list positionally and the pool settings by keyword.
type _StepFn = Callable[..., Awaitable[StepResult]]

_STEP_FUNCTIONS: dict[str, _StepFn] = {
    ContentHygieneStep.SELF_SYNONYM: _self_synonym_step,
    ContentHygieneStep.SYNONYM_ANTONYM: _synonym_antonym_step,
    ContentHygieneStep.SYNONYM_HYPERNYM: _synonym_hypernym_step,
    ContentHygieneStep.GARBAGE_EXAMPLES: _garbage_examples_step,
    ContentHygieneStep.STILTED_EXAMPLES: _stilted_examples_step,
    ContentHygieneStep.FRAGMENT_EXAMPLES: _fragment_examples_step,
    ContentHygieneStep.DEGENERATE_RENDITIONS: _degenerate_renditions_step,
}


async def run_content_hygiene(
    store: LexemeStore,
    runner: StageRunner,
    *,
    workers: int,
    stop_event: asyncio.Event | None = None,
    only: set[str] | None = None,
    lexeme_ids: Sequence[str] | None = None,
) -> ContentHygieneOutcome:
    """Repair the content defects measured on the 10K core store.

    Seven steps, described in full in the module docstring: three free ones that demote
    relations no sense can consistently assert and remove examples that are not
    sentences, and four that ask a model the questions no rule can answer. Every step is
    idempotent, every entry is read and written inside one hold of its own lock, and
    nothing is deleted except an unusable example whose text is preserved in a note
    first.

    Args:
        store: The store to repair.
        runner: The stage runner. Used by ``synonym_hypernym`` (nano, ``HYGIENE`` policy)
            and by ``stilted_examples``, ``fragment_examples`` and
            ``degenerate_renditions`` (luna, ``RENDITIONS`` policy); the three free steps
            never touch it.
        workers: Pool size for every step.
        stop_event: Shared stop event. A budget stop sets it; a caller may also set it
            from outside (the CLI passes its session's event, which ``SIGINT`` sets).
        only: Step names to run; defaults to all of :attr:`ContentHygieneStep.ALL`, in
            that order. Steps run in :attr:`ContentHygieneStep.ALL`'s order whatever
            order they are given in.
        lexeme_ids: Ids to visit; defaults to every id in the store, sorted.

    Returns:
        A :class:`ContentHygieneOutcome` carrying counts and cost per step. If a step
        stopped early its ``stopped_reason`` says why and the remaining steps are skipped;
        the outcome is still returned rather than raised, so a partial run reports what it
        managed to do.

    Raises:
        ValueError: If ``only`` names a step that does not exist.
    """
    selected = set(only) if only is not None else set(ContentHygieneStep.ALL)
    unknown = sorted(selected - set(ContentHygieneStep.ALL))
    if unknown:
        raise ValueError(f"unknown content hygiene step(s): {unknown}")

    ids = list(lexeme_ids) if lexeme_ids is not None else sorted(store.iter_ids())
    outcome = ContentHygieneOutcome()
    changed_ids: set[str] = set()

    for name in ContentHygieneStep.ALL:
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
                "content_hygiene_step_stopped",
                step=name,
                reason=result.stopped_reason,
                entries_scanned=result.entries_scanned,
                skipped=[
                    s for s in ContentHygieneStep.ALL if s in selected and s not in outcome.steps
                ],
            )
            break

    outcome.entries_changed = len(changed_ids)
    _LOG.info("content_hygiene_complete", entries=len(ids), workers=workers, **outcome.as_dict())
    return outcome
