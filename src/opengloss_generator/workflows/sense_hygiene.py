"""Workflow 10 — sense hygiene: phantom parts of speech, duplicate senses, misfiled examples.

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

A third question came out of the tier-4 sample (``docs/QA-DIARY.md``, Iteration 18 — 40 entries,
mean score 62.2, the weakest tier), and it is about the *part-of-speech inventory* rather than
the sense inventory. 63% of tier 4 is multiword, inherited from OpenGloss v1.3, whose generator
wrote a sense under every part of speech it guessed at. So compounds carry **phantom
part-of-speech entries**: ``blank cell`` has an adjective entry whose glosses define the
adjective *blank* — "no such adjective exists", the judge wrote — and whose morphology inflects
*blank* ("blanker", "blankest") rather than the compound. That one defect scores against three
criteria at once: the gloss is inaccurate for the headword, the phantom sense is not distinct
from the real one, and its relations (nominal hypernyms under an adjective) are wrong. Neither
step below can see it — ``distinctness`` refuses to group across parts of speech by design, and
the phantom gloss *is* distinct text.

Three steps, selectable by name through ``only=``, each idempotent, each its own pooled sweep
over the id list.

``phantom_pos`` (nano, ``HYGIENE`` policy)
    Entries with **two or more live part-of-speech entries**, plus any compound, idiom or
    phrasal verb carrying a part of speech outside its kind's natural set
    (:data:`NATURAL_POS`), get one call listing every live part-of-speech entry as
    ``[ref, pos, canonical glosses]`` beside the headword and its ``kind``, and the answer is
    ``{verdicts: [{pos_ref, verdict}]}`` — one strict-enum verdict per block:

    ``genuine``
        The definitions really are definitions of this headword used as this part of speech.
        The ordinary answer.
    ``phantom_component``
        They define a different lexeme — one component word of a multi-word headword, or a word
        derived from it — rather than the headword itself. ``blank cell``'s adjective block.
    ``phantom_duplicate``
        They restate another listed block's senses under a part of speech the headword does not
        have in that use: a noun-shaped definition filed under an adjective, or the reverse.

    A phantom block is retired **whole**: every live sense under it is marked
    :attr:`~opengloss_generator.schema.Sense.retired` — never deleted, never renumbered — with
    ``retired sense <sid>: phantom_pos: <reason>`` on the entry's provenance table, and every
    relation on those senses is *demoted* to ``see_also`` rather than dropped
    (:data:`PHANTOM_RELATION_NOTE`), following ``relation_hygiene``'s convention that a
    defective edge becomes a weaker one that still says something true. The lexeme's **last
    live part of speech is never retired**: when the model calls every listed block phantom,
    the one whose part of speech is natural for the kind (failing that, the first listed) is
    kept and counted as ``skipped_last_pos``, because an entry with no live sense is not a
    smaller dictionary but a broken one.

    A free signal prioritises the sweep and is reported rather than acted on
    (:func:`_defines_a_component`): a multi-word headword's block whose every gloss names none
    of the headword's content words, and whose morphology inflects exactly one of them as a
    standalone word, looks exactly like a definition of that component. It is precise and
    incomplete, so the verdict is still bought for every listed block, and it is deliberately
    kept out of the prompt — a hint of the answer is not an independent judgement of it.

``distinctness`` (nano, ``HYGIENE`` policy)
    Runs after ``phantom_pos`` so a phantom sense is never merged with a real one. Entries with
    **two or more non-retired senses under one part of speech** get one call
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

No step is idempotent by construction, so each carries D-47's sentinel on a zero-cost
provenance record — ``<prefix>:<digest>;attempts=<n>``, bounded at :data:`MAX_ATTEMPTS` attempts
per entry — over the *part-of-speech gloss sets* for ``phantom_pos``, the *sense set* for
``distinctness`` and the *canonical example set* for ``example_fit``. ``phantom_pos`` keys on
the glosses rather than on the sense ids because the question is about what the definitions
say: the same three sense ids can hold three rewritten definitions, and that is a different
question. Following ``relation_hygiene`` rather than ``content_hygiene``, the digest is
taken over the set **as the answers leave it**, not as they found it: taken the other way, a
sweep that merged a duplicate or moved an example would leave a marker describing a set that no
longer exists, and the very next sweep would buy a second opinion about senses it had already
passed. Taken this way the marker reads "I have judged exactly this set", a second sweep over an
unchanged entry is free, and an entry that later *gains* a sense or an example still earns one
further attempt. A sentinel rather than a bare :class:`~opengloss_generator.schema.StageName`
because both calls reuse the shared ``HYGIENE`` policy rather than adding a stage of their own.

An entry with one live sense is never listed by either sense-level step, never called for, and
costs $0 on every sweep; a simplex with one live part of speech is likewise free in
``phantom_pos``, whose last-live-part-of-speech guard makes its answer foregone.

Run order
---------

``phantom_pos`` runs first, then ``distinctness``, then ``example_fit`` (see
:attr:`SenseHygieneStep.ALL`). Run this pass **before** ``retrofit --only repair`` (which
refills the senses ``example_fit`` empties) and after ``content_hygiene``'s
``garbage_examples`` (there is no point paying a model to decide where ``'hypernyms(['``
belongs). Retiring a sense does not renumber anything, so no
downstream sense id moves and no edge is re-pointed; ``Lexeme.edges`` already skips retired
senses, so a retired duplicate leaves the projected graph on its own.

Concurrency and locking (D-31)
------------------------------

Every step drives its ids through :func:`~opengloss_generator.runner.run_pool`, and the handler
holds the entry's lock across the whole of read → collect → model call → apply → write, so no
entry is ever read outside the lock it is written under. No step reads any *other* entry at
all: all three questions are answered entirely from within one entry. ``phantom_pos``'s free
prioritisation pass (:func:`_signalled_first`) does read every entry before the pool starts,
outside any lock, and is the one exception that proves the rule: it decides *visit order* and
nothing else, so a stale read costs an ordering rather than a verdict. Counters go through
:class:`_Tally`, mutated only while holding an ``asyncio.Lock``, for the reason
``retrofit._Tally`` gives.

:data:`~opengloss_generator.workflows.content_hygiene.PROGRESS_EVERY` is imported from
``content_hygiene`` so every sweep in the project reads the same in a run log. This pass keeps
its own :class:`StepResult` rather than importing that module's, because its counters are its
own — nothing here is retyped or rewritten, and the one demotion it does make
(``phantom_pos``'s, onto a sense it has just retired) is counted separately. Its contract and
its instructions are module-private for D-49's and D-50's reason: ``contracts.py`` and
``prompts.py`` are edited concurrently on this branch, and a self-contained module is what
lets this work land without conflicting with that.
"""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from opengloss_generator import spans
from opengloss_generator.errors import BudgetExceededError, GenerationError
from opengloss_generator.hygiene import content_words
from opengloss_generator.log import get_logger
from opengloss_generator.prompts import PROMPT_VERSION
from opengloss_generator.runner import run_pool
from opengloss_generator.schema import (
    CANONICAL_KEY,
    LexemeKind,
    PartOfSpeech,
    Provenance,
    RelationType,
    StageName,
)
from opengloss_generator.workflows.content_hygiene import PROGRESS_EVERY

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Iterable, Sequence

    from opengloss_generator.schema import (
        Example,
        Lexeme,
        POSEntry,
        ReadingLevel,
        Register,
        Relation,
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
    "NATURAL_POS",
    "PHANTOM_POS_INSTRUCTIONS",
    "PHANTOM_RELATION_NOTE",
    "PHANTOM_VERDICTS",
    "REMOVED_EXAMPLE_NOTE",
    "RETIRED_PHANTOM_NOTE",
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

#: The note a ``phantom_pos`` retirement writes, one per retired sense. Shares
#: :data:`RETIRED_SENSE_NOTE`'s ``retired sense <sid>:`` opening so one grep finds every
#: retirement this pass makes, and carries the model's own verdict word as the reason.
RETIRED_PHANTOM_NOTE = "retired sense {retired}: phantom_pos: {reason}"

#: What a relation on a sense ``phantom_pos`` retires is demoted *to* a ``see_also`` with. The
#: relation is not deleted — a phantom adjective entry's nominal hypernyms are wrong about the
#: headword but they are evidence of what the generator was thinking, and ``relation_hygiene``
#: writes its own demotions the same way (``demoted: …``), so one convention covers both.
PHANTOM_RELATION_NOTE = "demoted: phantom_pos sense"

#: The two ways a part-of-speech entry can be phantom, as the model names them and as the
#: retirement note records them. ``genuine`` is the third answer and is not here: it is the
#: absence of a defect rather than one of its kinds.
PHANTOM_COMPONENT = "phantom_component"
PHANTOM_DUPLICATE = "phantom_duplicate"
PHANTOM_GENUINE = "genuine"
PHANTOM_VERDICTS: tuple[str, ...] = (PHANTOM_GENUINE, PHANTOM_COMPONENT, PHANTOM_DUPLICATE)

#: The parts of speech a multi-word lexeme of each kind *naturally* has. Used only as a gate:
#: an entry carrying a part of speech outside its kind's set is asked about even when it has
#: only one, because that is exactly the shape ``docs/QA-DIARY.md`` iteration 18 found (an
#: adjective entry on a nominal compound). It is not evidence on its own — plenty of compounds
#: really are adjectives ("well known") or verbs ("carry out"), which is why the answer is
#: still bought from the model rather than inferred here. A kind absent from this map (a
#: simplex, a proper noun) has no natural part of speech and is gated on its POS-entry count
#: alone.
NATURAL_POS: dict[LexemeKind, frozenset[PartOfSpeech]] = {
    LexemeKind.COMPOUND: frozenset({PartOfSpeech.NOUN}),
    LexemeKind.PHRASAL_VERB: frozenset({PartOfSpeech.VERB}),
    LexemeKind.IDIOM: frozenset(
        {
            PartOfSpeech.NOUN,
            PartOfSpeech.VERB,
            PartOfSpeech.ADJECTIVE,
            PartOfSpeech.ADVERB,
        }
    ),
}

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
_PHANTOM_POS_PREFIX = "sense_hygiene:phantom_pos"
_DISTINCTNESS_PREFIX = "sense_hygiene:distinctness"
_EXAMPLE_FIT_PREFIX = "sense_hygiene:example_fit"

#: Shown in place of an example that a sense does not have.
_NO_EXAMPLE = "(none)"

#: The fewest live senses that make either question worth asking.
_MIN_SENSES = 2

#: The fewest content words a headword needs before ``phantom_pos``'s free signal can ask which
#: *component* a block defines. A one-word headword has no components to confuse it with.
_MIN_COMPONENT_WORDS = 2

#: The fewest live part-of-speech entries that make ``phantom_pos`` worth asking about on a
#: lexeme whose kind has no natural part of speech. One is not enough on its own: the step
#: never retires a lexeme's last live part of speech, so a single-POS simplex has a foregone
#: answer.
_MIN_POS_ENTRIES = 2


class SenseHygieneStep:
    """Names of the steps :func:`run_sense_hygiene` can select between."""

    PHANTOM_POS = "phantom_pos"
    DISTINCTNESS = "distinctness"
    EXAMPLE_FIT = "example_fit"

    #: The order the steps run in. ``phantom_pos`` first, deliberately: it retires whole
    #: part-of-speech entries that were never this headword's to begin with, and a phantom sense
    #: must not be merged with a real one — ``distinctness`` refuses to group across parts of
    #: speech, but it would happily merge a phantom adjective sense with a second phantom
    #: adjective sense and leave the survivor looking like a settled meaning. Running it first
    #: also means neither later step is billed to judge senses that are about to disappear.
    #: ``distinctness`` before ``example_fit`` for its own reason: it retires senses and merges
    #: their examples onto a survivor, so running it first means ``example_fit`` is never billed
    #: to decide where an example belongs among senses that were about to be merged, and never
    #: files an example under a sense that is retired a moment later.
    ALL: tuple[str, ...] = (PHANTOM_POS, DISTINCTNESS, EXAMPLE_FIT)


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
        pos_entries_judged: Part-of-speech entries a verdict was bought for (``phantom_pos``
            only). Several per call: one call covers a whole entry.
        pos_entries_retired: Part-of-speech entries found phantom and retired whole.
        retired_component: Of those, the ones whose definitions defined a component word or a
            different lexeme (``phantom_component``).
        retired_duplicate: Of those, the ones restating another part-of-speech entry's senses
            under a part of speech the headword does not have (``phantom_duplicate``).
        skipped_last_pos: Part-of-speech entries called phantom and left alone anyway, because
            retiring them would have left the lexeme with no live part of speech at all. A
            *report*: the entry needs a rewritten sense, which is not this step's job.
        relations_demoted: Relations on a retired phantom sense turned into ``see_also`` rather
            than deleted. A consequence of a retirement already counted, so it is deliberately
            absent from :attr:`changed`.
        signalled: Part-of-speech entries the free component-definition signal fired on
            (:func:`_defines_a_component`). Never acted on — it orders the sweep and is
            reported so the signal's precision against the model can be measured.
        signalled_phantom: Of those, the ones the model then called phantom.
        senses_retired: Senses marked retired — as duplicates of a survivor by
            ``distinctness``, or as part of a phantom part-of-speech entry by ``phantom_pos``.
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
    pos_entries_judged: int = 0
    pos_entries_retired: int = 0
    retired_component: int = 0
    retired_duplicate: int = 0
    skipped_last_pos: int = 0
    relations_demoted: int = 0
    signalled: int = 0
    signalled_phantom: int = 0
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
        ``senses_emptied`` is absent because it is a report about an edit already counted, and
        so are ``pos_entries_retired`` and ``relations_demoted``: every sense a phantom
        part-of-speech entry loses is already in ``senses_retired``, and every relation demoted
        sits on one of those senses.
        """
        return self.senses_retired + self.examples_moved + self.examples_removed

    def as_dict(self) -> dict[str, object]:
        """Return a JSON-able view for the run summary and the CLI."""
        return {
            "entries_scanned": self.entries_scanned,
            "entries_changed": self.entries_changed,
            "groups_merged": self.groups_merged,
            "pos_entries_judged": self.pos_entries_judged,
            "pos_entries_retired": self.pos_entries_retired,
            "retired_component": self.retired_component,
            "retired_duplicate": self.retired_duplicate,
            "skipped_last_pos": self.skipped_last_pos,
            "relations_demoted": self.relations_demoted,
            "signalled": self.signalled,
            "signalled_phantom": self.signalled_phantom,
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
        pos_entries_judged: int = 0,
        pos_entries_retired: int = 0,
        retired_component: int = 0,
        retired_duplicate: int = 0,
        skipped_last_pos: int = 0,
        relations_demoted: int = 0,
        signalled: int = 0,
        signalled_phantom: int = 0,
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
            pos_entries_judged: Part-of-speech entries judged on this entry.
            pos_entries_retired: Part-of-speech entries retired whole on this entry.
            retired_component: Of those, the ``phantom_component`` ones.
            retired_duplicate: Of those, the ``phantom_duplicate`` ones.
            skipped_last_pos: Phantom verdicts left unapplied to keep one live part of speech.
            relations_demoted: Relations on this entry's retired phantom senses demoted.
            signalled: Part-of-speech entries the free signal fired on.
            signalled_phantom: Of those, the ones the model called phantom.
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
            result.pos_entries_judged += pos_entries_judged
            result.pos_entries_retired += pos_entries_retired
            result.retired_component += retired_component
            result.retired_duplicate += retired_duplicate
            result.skipped_last_pos += skipped_last_pos
            result.relations_demoted += relations_demoted
            result.signalled += signalled
            result.signalled_phantom += signalled_phantom
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
# Step 1 — phantom_pos
# --------------------------------------------------------------------------------------
#
# The instructions and the output contract live here, not in prompts.py / contracts.py, for the
# reason the two later steps give: a self-contained call site cannot conflict with the
# concurrent edits those files are under. Nothing outside this module depends on the names
# below.


#: Instructions for this step's one nano call per entry. Byte-stable, for the reason
#: :data:`DISTINCTNESS_INSTRUCTIONS` is. Stated around what a *genuine* part of speech looks
#: like rather than around the defect, because a model asked only "which of these is fake?"
#: will find fakes, and a wrong answer here retires a real meaning.
PHANTOM_POS_INSTRUCTIONS = """\
You are auditing which PARTS OF SPEECH a dictionary has given one headword. You are shown the \
headword, what kind of lexical item it is, and every part-of-speech entry the dictionary \
currently holds for it, numbered, each with the definitions filed under it. For each numbered \
part-of-speech entry, say whether it genuinely belongs to this headword.

WHY THIS IS ASKED. The dictionary these entries come from wrote a definition under every part \
of speech its generator guessed at. For a single word that is usually harmless. For a \
MULTI-WORD headword it is not. Asked for the adjective of "blank cell", the generator defined \
the adjective "blank" -- one component of the headword, a different word entirely -- and filed \
it under "blank cell" as though the whole compound were an adjective. There is no adjective \
"blank cell". The same thing happens with a verb entry that defines only the verb component of \
a compound, and with a part-of-speech entry that restates the headword's real definition under \
a part of speech the headword is never actually used in.

THE THREE ANSWERS.

- genuine. The definitions under this part of speech really are definitions of THIS headword, \
used as THIS part of speech. Someone could use the whole headword, unchanged, in a sentence in \
that role, meaning what these definitions say. This is the ordinary answer and most \
part-of-speech entries deserve it.
- phantom_component. The definitions define a DIFFERENT lexeme: one component word of a \
multi-word headword, or a word derived from it, rather than the headword itself. The clearest \
sign is that the definition would be exactly right printed under that component word on its \
own, and says nothing at all about what the whole headword means. A second sign is inflected \
forms belonging to the component rather than to the headword.
- phantom_duplicate. The definitions say what another listed part-of-speech entry already \
says, restated under a part of speech the headword does not have in that use. The commonest \
shapes are a noun-shaped definition ("a place where...", "the act of...", "a person who...") \
filed under an adjective, and an adjective-shaped one ("having...", "relating to...", \
"describing...") filed under a noun.

HOW TO DECIDE. Read the definitions filed under the numbered part of speech and ask one \
question: could a speaker use the WHOLE headword, unchanged and without adding words to it, in \
a sentence in that role, meaning this? "The cell was blank" uses the adjective "blank", not the \
compound "blank cell", so an adjective entry defining "not filled in" does not survive the \
question. "It was a blank cell" uses the noun, so the noun entry does.

WHAT IS NOT EVIDENCE. That the headword is several words is not evidence: plenty of multi-word \
headwords genuinely are adjectives ("well known", "state of the art"), adverbs ("part time") \
or verbs ("carry out"), and a phrasal verb genuinely has a noun form as often as not. That a \
definition is short, badly worded, or narrower than the others is not evidence either -- this \
question is about whether the part of speech exists at all, not about how well it is written. \
That two parts of speech are RELATED in the ordinary way a noun and its verb are related is \
not evidence: "an act of running" under the noun and "to move quickly on foot" under the verb \
are two genuine entries, not a duplicate.

BE CONSERVATIVE. A phantom verdict retires every definition filed under that part of speech \
from the dictionary. A wrong one hides a meaning a reader was looking for, and there is nothing \
downstream to catch it. When the definitions are a plausible reading of the whole headword in \
that role, or when you are simply not sure, answer genuine.

ANSWER FORMAT. Give exactly one verdict for every numbered part-of-speech entry you were \
shown, referring to each by the number it was listed under, and nothing for numbers you were \
not shown.

WORKED EXAMPLES.

Headword: "blank cell" (compound)

  1. [noun] An empty cell in a table, spreadsheet, form, or grid that currently contains no \
data.
  2. [adjective] Not filled in or completed; having no marks, data, or content yet present. | \
Lacking expression or emotion; not showing understanding or reaction.

Verdicts: 1 genuine, 2 phantom_component. Both adjective definitions define the ordinary \
adjective "blank" -- the second one ("a blank look") is not even about cells. Nobody says "the \
table was blank cell".

Headword: "field trip" (compound)

  1. [noun] A visit made by students to a place away from their usual classroom, for study.
  2. [adjective] Relating to or involving a visit made by students away from the classroom.

Verdicts: 1 genuine, 2 phantom_duplicate. The adjective entry says exactly what the noun entry \
says, turned into an adjective definition; "field trip" is a noun that can sit in front of \
another noun ("field trip permission"), which every noun can do, and that is not an adjective \
sense.

Headword: "part time" (compound)

  1. [adjective] Working or done for less than the usual number of hours.
  2. [adverb] For less than the full number of usual working hours.

Verdicts: 1 genuine, 2 genuine. The whole headword really is used both ways -- "a part time \
job" and "she works part time" -- and neither definition is a definition of "part" or of \
"time". Two closely related definitions under two parts of speech that the headword genuinely \
has are two genuine entries."""


class _DraftPOSVerdict(BaseModel):
    """One part-of-speech entry's verdict."""

    model_config = ConfigDict(extra="forbid")

    pos_ref: Annotated[int, Field(ge=1)]
    verdict: Literal["genuine", "phantom_component", "phantom_duplicate"]


class _DraftPOSVerdicts(BaseModel):
    """Every part-of-speech verdict for one entry, produced together.

    One call per entry rather than per part-of-speech entry: ``phantom_duplicate`` is a claim
    about *two* of them at once, so a per-entry question could not be asked at all.
    """

    model_config = ConfigDict(extra="forbid")

    verdicts: list[_DraftPOSVerdict]


@dataclass(slots=True)
class _POSRef:
    """One live part-of-speech entry, as the model is shown it and as the answer refers back.

    Attributes:
        pos_entry: The part-of-speech entry itself, mutated in place when it is retired.
        senses: Its live senses, paired with their derived positional ids (D-1).
        glosses: Their canonical definitions, shown to the model and hashed into the marker.
        signalled: Whether the free component-definition signal fired on it.
    """

    pos_entry: POSEntry
    senses: list[tuple[Sense, str]]
    glosses: list[str]
    signalled: bool


@dataclass(slots=True)
class _PhantomCounts:
    """What one entry's ``phantom_pos`` call did.

    Attributes:
        answered: Whether a call actually completed. A failed call leaves no marker, so the
            entry is tried again on the next sweep.
        judged: Part-of-speech entries a verdict was returned for.
        retired: Part-of-speech entries retired whole.
        component: Of those, the ``phantom_component`` ones.
        duplicate: Of those, the ``phantom_duplicate`` ones.
        skipped_last: Phantom verdicts deliberately not applied, to leave one live part of
            speech on the lexeme.
        senses_retired: Senses retired across every retired part-of-speech entry.
        relations_demoted: Relations on those senses demoted to ``see_also``.
        signalled: Part-of-speech entries the free signal fired on.
        signalled_phantom: Of those, the ones the model called phantom.
        rejected: Verdicts refused — a ref that was not listed, or a second verdict for a ref
            already answered.
    """

    answered: bool = False
    judged: int = 0
    retired: int = 0
    component: int = 0
    duplicate: int = 0
    skipped_last: int = 0
    senses_retired: int = 0
    relations_demoted: int = 0
    signalled: int = 0
    signalled_phantom: int = 0
    rejected: int = 0


def _live_pos_entries(entry: Lexeme) -> list[POSEntry]:
    """Return every part-of-speech entry that still carries at least one live sense.

    A part-of-speech entry whose senses were all retired by some earlier pass is already gone
    from the dictionary a reader sees, so it is neither listed for the model nor counted
    towards the last-live-part-of-speech guard.

    Args:
        entry: The entry to inspect. Never mutated.

    Returns:
        The live part-of-speech entries, in document order.
    """
    return [
        pos_entry
        for pos_entry in entry.pos_entries
        if any(not sense.retired for sense in pos_entry.senses)
    ]


def _has_unnatural_pos(entry: Lexeme, live: Sequence[POSEntry]) -> bool:
    """Return whether a multi-word kind carries a part of speech outside its natural set.

    The second half of this step's gate (:data:`NATURAL_POS`). It is a *question* gate, not
    evidence: a compound really can be an adjective, and the verdict is still bought from the
    model. A kind with no entry in :data:`NATURAL_POS` — every simplex, every proper noun —
    never qualifies this way.

    Args:
        entry: The entry to inspect. Never mutated.
        live: Its live part-of-speech entries.

    Returns:
        Whether any of them sits outside the kind's natural set.
    """
    natural = NATURAL_POS.get(entry.kind)
    if natural is None:
        return False
    return any(pos_entry.pos not in natural for pos_entry in live)


def _defines_a_component(entry: Lexeme, pos_entry: POSEntry) -> bool:
    """Return whether a part-of-speech entry looks like a definition of one component word.

    The free signal, computed from the entry alone and costing nothing. It fires when all three
    of these hold of a **multi-word** headword's part-of-speech entry:

    1. every live canonical gloss under it names **none** of the headword's content words — a
       compound's own definition almost always repeats its head noun ("an empty *cell* in a
       table"), and one that repeats nothing is not talking about the compound;
    2. the part-of-speech entry's morphology names **exactly one** of those content words as
       the standalone word it inflects ("blanker", "blankest", "blankly" for *blank cell*);
    3. and none of its forms is a form of the whole headword ("blank cells"), which would say
       the block is about the compound after all.

    Measured on the D-76 pilot sample (400 tier-4 entries, 408 blocks judged): it fires on 41
    blocks, 22 of which the model then called phantom — 54% precision against a 23% base rate,
    but only 23% recall of the 95 blocks actually retired. Better than chance and nowhere near
    a substitute, which is why it *prioritises* the sweep (:func:`_signalled_first`) and is
    reported for its precision, and why the verdict is still bought for every listed
    part-of-speech entry. It is deliberately **not** shown to the model: a hint of the answer
    inside the prompt is not an independent judgement of it.

    Args:
        entry: The entry the part-of-speech block belongs to. Never mutated.
        pos_entry: The block to test. Never mutated.

    Returns:
        Whether the block looks like a definition of one of the headword's component words.
    """
    parts = content_words(entry.headword)
    if len(parts) < _MIN_COMPONENT_WORDS:
        return False
    glosses = [sense.canonical_gloss() for sense in pos_entry.senses if not sense.retired]
    if not glosses:
        return False
    if any(parts & content_words(gloss) for gloss in glosses):
        return False
    inflected: set[str] = set()
    for form in (*pos_entry.morphology.inflected_forms(), *pos_entry.morphology.derivations):
        tokens = content_words(form)
        named = {part for part in parts if any(token.startswith(part) for token in tokens)}
        if len(named) > 1:
            return False
        inflected |= named
    return len(inflected) == 1


def _phantom_refs(entry: Lexeme) -> list[_POSRef]:
    """Return the part-of-speech entries this step lists, or ``[]`` when it lists none.

    Every live part-of-speech entry is listed once the gate is passed, not only the suspicious
    one: ``phantom_duplicate`` is a claim that one block restates another, which cannot be
    judged without both of them in front of the model.

    The gate is two clauses. An entry with two or more live parts of speech qualifies, because
    one of them can be retired. A compound, idiom or phrasal verb carrying a part of speech
    outside its kind's natural set qualifies even with one, because that is the measured shape
    of the defect — the verdict there can only ever be a *report* (the last live part of speech
    is never retired), and it is bought anyway because a one-block compound whose only block is
    a component definition is an entry with no correct definition at all, which is worth
    knowing and is not worth guessing at.

    Args:
        entry: The entry to inspect. Never mutated.

    Returns:
        One ref per live part-of-speech entry in document order, or an empty list.
    """
    live = _live_pos_entries(entry)
    if len(live) < _MIN_POS_ENTRIES and not _has_unnatural_pos(entry, live):
        return []
    grouped: dict[int, list[tuple[Sense, str]]] = {id(pos_entry): [] for pos_entry in live}
    for pos_entry, sense, sense_id in _live_senses(entry):
        bucket = grouped.get(id(pos_entry))
        if bucket is not None:
            bucket.append((sense, sense_id))
    return [
        _POSRef(
            pos_entry=pos_entry,
            senses=grouped[id(pos_entry)],
            glosses=[sense.canonical_gloss() for sense, _ in grouped[id(pos_entry)]],
            signalled=_defines_a_component(entry, pos_entry),
        )
        for pos_entry in live
    ]


def _pos_ref_id(ref: _POSRef) -> str:
    """Return the marker ref for one part-of-speech entry: its part of speech and gloss set.

    Keyed on the glosses rather than on the sense ids so that a block whose definitions were
    *rewritten* since the last sweep earns a fresh verdict, which is the case the whole
    question turns on — the same three sense ids can hold three different definitions.

    Args:
        ref: The listed part-of-speech entry.

    Returns:
        ``<pos>:<digest of its canonical glosses>``.
    """
    return f"{ref.pos_entry.pos.value}:{_ref_digest(ref.glosses)}"


def _build_phantom_pos_prompt(entry: Lexeme, refs: Sequence[_POSRef]) -> str:
    """Return the volatile half of this step's prompt.

    Args:
        entry: The entry being judged, for its headword and kind.
        refs: The part-of-speech entries, in the order the model should answer about them — a
            ``pos_ref`` in the reply is a 1-based position into this sequence.

    Returns:
        The per-call prompt body.
    """
    lines = [
        f"  {position}. [{ref.pos_entry.pos.value}] "
        + " | ".join(_one_line(gloss) for gloss in ref.glosses)
        for position, ref in enumerate(refs, start=1)
    ]
    listed = "\n".join(lines)
    return (
        f"Headword: {entry.headword}\n"
        f"Kind: {entry.kind.value}\n"
        f"Parts of speech ({len(refs)}):\n{listed}"
    )


def _demote(relation: Relation, note: str, provenance_id: str) -> None:
    """Demote one relation to ``see_also`` in place, keeping any note it already carried.

    Mirrors ``relation_hygiene._retype``, which is module-private there. Nothing is deleted:
    a phantom adjective entry's nominal hypernyms are wrong about the headword, but a
    ``see_also`` still says the two terms have something to do with each other, and the reason
    is written where a later reader will find it.

    Args:
        relation: The relation to demote, mutated in place.
        note: Why, prepended to whatever note the relation already had.
        provenance_id: The entry's record for this edit.
    """
    relation.type = RelationType.SEE_ALSO
    relation.note = note if relation.note is None else f"{note} | {relation.note}"
    relation.provenance_id = provenance_id


def _kept_position(entry: Lexeme, refs: Sequence[_POSRef], phantoms: dict[int, str]) -> int | None:
    """Return which phantom verdict must go unapplied, or ``None`` when none must.

    A lexeme never loses its last live part of speech: an entry with no live sense at all is not
    a smaller dictionary, it is a broken one, and every consumer of this store — the exports,
    the graph projection, the QA judge — reads the live senses. When the model calls *every*
    listed block phantom, one is kept: the one whose part of speech is natural for the lexeme's
    kind, and failing that the first listed, which is document order and therefore stable.

    Args:
        entry: The entry being judged.
        refs: The part-of-speech entries as they were listed.
        phantoms: Position to verdict word, for the blocks called phantom.

    Returns:
        The position to leave alone, or ``None`` when at least one block survives anyway.
    """
    if len(phantoms) < len(refs):
        return None
    natural = NATURAL_POS.get(entry.kind)
    if natural is not None:
        for position, ref in enumerate(refs):
            if ref.pos_entry.pos in natural:
                return position
    return 0


def _retire_pos_entry(entry: Lexeme, ref: _POSRef, reason: str) -> tuple[int, int]:
    """Retire every live sense of one phantom part-of-speech entry, demoting its relations.

    Nothing is deleted and nothing is renumbered (D-1): each sense is marked
    :attr:`~opengloss_generator.schema.Sense.retired` and keeps everything it had, and each of
    its relations becomes a ``see_also`` carrying :data:`PHANTOM_RELATION_NOTE` — a relation
    asserted by a sense that should never have existed must not keep claiming to be a hypernym,
    and ``Lexeme.edges`` skips retired senses anyway, so the demotion is what a reader of the
    stored list sees rather than a change to the projected graph.

    Args:
        entry: The entry the block belongs to, mutated in place.
        ref: The block to retire.
        reason: The model's verdict word, written into the note.

    Returns:
        ``(senses retired, relations demoted)``.
    """
    retired = 0
    demoted = 0
    for sense, sense_id in ref.senses:
        provenance_id = entry.add_provenance(
            _rule_provenance(RETIRED_PHANTOM_NOTE.format(retired=sense_id, reason=reason))
        )
        for relation in sense.relations:
            if relation.type is RelationType.SEE_ALSO:
                continue
            _demote(relation, f"{PHANTOM_RELATION_NOTE} {sense_id}", provenance_id)
            demoted += 1
        sense.retired = True
        retired += 1
    _LOG.debug(
        "sense_hygiene_phantom_pos_retired",
        headword=entry.headword,
        pos=ref.pos_entry.pos.value,
        reason=reason,
        senses=retired,
        relations_demoted=demoted,
    )
    return retired, demoted


def _collect_phantoms(
    entry: Lexeme, refs: Sequence[_POSRef], verdicts: Sequence[_DraftPOSVerdict]
) -> tuple[dict[int, str], int, int]:
    """Return the positions the model called phantom, and how many verdicts were refused.

    A verdict is refused when it names a position that was not listed or repeats one already
    answered; the rest of the answer stands, because each verdict is its own claim about its own
    block rather than one claim about a set (which is what makes ``distinctness`` refuse a group
    whole).

    Args:
        entry: The entry being judged, for the log line.
        refs: The blocks as they were listed.
        verdicts: The answer.

    Returns:
        ``({position: verdict word}, blocks answered for, refused count)``.
    """
    phantoms: dict[int, str] = {}
    answered: set[int] = set()
    rejected = 0
    for verdict in verdicts:
        position = verdict.pos_ref - 1
        if not 0 <= position < len(refs) or position in answered:
            rejected += 1
            _LOG.info(
                "sense_hygiene_phantom_verdict_refused",
                headword=entry.headword,
                pos_ref=verdict.pos_ref,
                listed=len(refs),
            )
            continue
        answered.add(position)
        if verdict.verdict != PHANTOM_GENUINE:
            phantoms[position] = verdict.verdict
    return phantoms, len(answered), rejected


def _apply_phantom_verdicts(
    entry: Lexeme, refs: Sequence[_POSRef], verdicts: Sequence[_DraftPOSVerdict]
) -> _PhantomCounts:
    """Retire every part-of-speech entry the model called phantom, bar the last live one.

    Args:
        entry: The entry being judged, mutated in place.
        refs: The blocks as they were listed.
        verdicts: The answer.

    Returns:
        The counts for this entry, with :attr:`_PhantomCounts.answered` left to the caller.
    """
    counts = _PhantomCounts()
    counts.signalled = sum(1 for ref in refs if ref.signalled)
    phantoms, counts.judged, counts.rejected = _collect_phantoms(entry, refs, verdicts)
    counts.signalled_phantom = sum(1 for position in phantoms if refs[position].signalled)
    kept = _kept_position(entry, refs, phantoms)
    for position, reason in sorted(phantoms.items()):
        if position == kept:
            counts.skipped_last += 1
            _LOG.info(
                "sense_hygiene_phantom_pos_kept_last",
                headword=entry.headword,
                pos=refs[position].pos_entry.pos.value,
                reason=reason,
            )
            continue
        retired, demoted = _retire_pos_entry(entry, refs[position], reason)
        counts.retired += 1
        counts.senses_retired += retired
        counts.relations_demoted += demoted
        if reason == PHANTOM_COMPONENT:
            counts.component += 1
        else:
            counts.duplicate += 1
    return counts


async def _decide_phantom_pos(
    entry: Lexeme, refs: Sequence[_POSRef], runner: StageRunner, tally: _Tally
) -> _PhantomCounts:
    """Ask nano which of an entry's parts of speech are not this headword's, and retire them.

    Args:
        entry: The entry being judged, mutated in place.
        refs: The blocks, in the order the model is shown them.
        runner: The stage runner.
        tally: The step tally, for the call and its cost.

    Returns:
        The counts for this entry.

    Raises:
        BudgetExceededError: A budget stop is a run-level condition and propagates.
    """
    try:
        stage_result = await runner.run(
            # Reuses hygiene's model policy (nano), like the other two steps: a strict-enum
            # verdict about a definition's subject is a structural question, not prose.
            stage=StageName.HYGIENE,
            output_type=_DraftPOSVerdicts,
            instructions=PHANTOM_POS_INSTRUCTIONS,
            prompt=_build_phantom_pos_prompt(entry, refs),
            prompt_version=PROMPT_VERSION,
        )
    except BudgetExceededError:
        raise
    except GenerationError as exc:
        _LOG.warning("sense_hygiene_phantom_pos_failed", headword=entry.headword, error=str(exc))
        return _PhantomCounts(signalled=sum(1 for ref in refs if ref.signalled))

    await tally.call(stage_result.cost_usd)
    entry.add_provenance(stage_result.provenance)
    counts = _apply_phantom_verdicts(entry, refs, stage_result.output.verdicts)
    counts.answered = True
    return counts


def _signalled_first(store: LexemeStore, ids: Sequence[str]) -> list[str]:
    """Return ``ids`` reordered so the entries the free signal fires on are visited first.

    One free read per id before the pool starts, and the result affects **ordering only** — no
    verdict, no edit and no marker depends on it, so nothing here is read under a lock that it
    is later written under (D-31). What it buys is that a sweep stopped by its budget has spent
    that budget on the entries most likely to carry the defect rather than on an alphabetical
    prefix of the store.

    Args:
        store: The store being swept.
        ids: The entry ids to visit, in the caller's order.

    Returns:
        The same ids: the signalled ones first, each group otherwise in the caller's order.
    """
    signalled: list[str] = []
    rest: list[str] = []
    for lexeme_id in ids:
        entry = store.read(lexeme_id)
        hit = entry is not None and any(
            _defines_a_component(entry, pos_entry) for pos_entry in _live_pos_entries(entry)
        )
        (signalled if hit else rest).append(lexeme_id)
    _LOG.info("sense_hygiene_phantom_pos_prioritised", signalled=len(signalled), other=len(rest))
    return signalled + rest


async def _phantom_pos_step(
    store: LexemeStore,
    runner: StageRunner,
    ids: Sequence[str],
    *,
    workers: int,
    stop_event: asyncio.Event | None,
    changed_ids: set[str],
) -> StepResult:
    """Retire every part-of-speech entry that is not this headword's, one nano call per entry.

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
    tally = _Tally(SenseHygieneStep.PHANTOM_POS, changed_ids)

    async def judge(lexeme_id: str) -> None:
        counts = _PhantomCounts()
        async with store.locked(lexeme_id):
            entry = store.read(lexeme_id)
            if entry is None:
                return
            refs = _phantom_refs(entry)
            attempt = _attempt_number(entry, _PHANTOM_POS_PREFIX, [_pos_ref_id(r) for r in refs])
            if attempt is not None:
                counts = await _decide_phantom_pos(entry, refs, runner, tally)
                if counts.answered:
                    # The digest is over the part-of-speech set as the retirements leave it, and
                    # it is recomputed exactly the way the next sweep will compute it — so an
                    # entry nothing changed on is free next time, and one whose definitions are
                    # later rewritten still earns its second attempt.
                    surviving = [_pos_ref_id(ref) for ref in _phantom_refs(entry)]
                    entry.add_provenance(
                        _rule_provenance(_marker_note(_PHANTOM_POS_PREFIX, surviving, attempt))
                    )
                    # Written even when nothing was retired: the marker is the only thing that
                    # call bought, and losing it re-bills the same answer.
                    store.write(entry)
        await tally.entry(
            lexeme_id,
            pos_entries_judged=counts.judged,
            pos_entries_retired=counts.retired,
            retired_component=counts.component,
            retired_duplicate=counts.duplicate,
            skipped_last_pos=counts.skipped_last,
            relations_demoted=counts.relations_demoted,
            signalled=counts.signalled,
            signalled_phantom=counts.signalled_phantom,
            senses_retired=counts.senses_retired,
            rejected=counts.rejected,
        )

    ordered = _signalled_first(store, ids)
    await _drive(ordered, judge, tally, workers=workers, stop_event=stop_event)
    return tally.result


# --------------------------------------------------------------------------------------
# Step 2 — distinctness
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
# Step 3 — example_fit
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
    SenseHygieneStep.PHANTOM_POS: _phantom_pos_step,
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
    """Retire phantom parts of speech, merge near-duplicate senses, refile misplaced examples.

    Three steps, described in full in the module docstring. ``phantom_pos`` retires whole
    part-of-speech entries whose definitions are not this headword's — a compound carrying an
    adjective entry that defines one of its component words — demoting their relations rather
    than dropping them, and never taking a lexeme's last live part of speech. ``distinctness``
    retires a sense that
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
        runner: The stage runner. Every step makes one nano call per qualifying entry on the
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
