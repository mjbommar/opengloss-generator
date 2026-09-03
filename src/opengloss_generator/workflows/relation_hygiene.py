"""Workflow 9 — relation validity: is the edge *true*, not merely well-shaped.

``workflows/graph_hygiene.py`` makes the relation graph **consistent** (no self-loops, no
cycles, symmetric types reciprocated) and ``workflows/content_hygiene.py`` settles the
pairs of types that **contradict each other** on one target. Neither of them ever asks
the only question that matters to a reader: is the thing on the far end of this edge
actually related to this sense in the way the edge claims?

The QA judge (``docs/QA-DIARY.md``, Iteration 1; ``claude-opus-5`` over a stratified
sample of 58 core entries and 179 senses) answered that, and the answer is the worst
number in the resource: **``relations_valid`` false on 92.7% of judged senses**, and
**44.9% of the 1,734 individual relations named invalid** — antonym 51%, hyponym 51%,
synonym 40%, hypernym 35%. The graph is consistent and untrue.

The invalid targets the judge listed fall into four shapes, and three of them are
decidable by rule for $0::

    banner   synonym  "banners"           an inflection of a sibling target
    ad       synonym  "advertisements"    an inflection of a sibling target
    stay     synonym  "stays"             an inflection of the headword itself
    benjamin hyponym  "crisp benjamin"    a modifier phrase built on the headword
    benjamin hyponym  "folded benjamin"   a modifier phrase built on the headword
    benjamin antonym  "one-dollar bill"   an enumeration mis-typed as an opposite
    benjamin see_also "slang term"        a meta-label, not a lexical unit
    julian   see_also "popular given name" a meta-label, not a lexical unit
    ivy      hyponym  "indoor plant"      a descriptive phrase, not a lexical unit

Four steps, selectable by name through ``only=``, each idempotent, each its own pooled
sweep over the id list. Three are free and run first, so the fourth is never billed for
anything a rule could have settled. **Nothing is ever deleted** (D-1's spirit): a
relation that fails a check is *demoted* to ``see_also``, which still says something
true — these two terms are related — and the reason is written to ``Relation.note``, so
every edge this pass touched can be found again by its note.

``inflections`` (free)
    Two shapes, in this order. First, a target that is an inflected form of the entry's
    own headword — the union of the model-supplied
    :meth:`~opengloss_generator.schema.Morphology.inflected_forms` and
    :func:`~opengloss_generator.spans.generate_forms`' rule-based ones, which is the same
    candidate union ``content_hygiene._forms_for`` builds for span finding. "stays" is
    not a synonym of *stay*; it is *stay*. Second, a target that is an inflected form of
    **another target of the same type on the same sense**: ``generate_forms`` is run over
    each sibling, so "banners" beside "banner", or "advertisements" beside
    "advertisement", is recognised as the same lexical unit listed twice. The shorter
    (base) form is the one kept; only the inflected one is demoted, with a note naming
    the sibling it duplicates.

    ``derivation`` relations are exempt from both shapes. Morphology is precisely that
    type's subject, and a pass that demoted a derivation for naming a morphological
    relative would be destroying the information the type exists to carry. The defect the
    judge measured is a plural asserted as a **synonym**, not a derivation doing its job.

``headword_phrases`` (free)
    A multi-word target that contains the entry's headword as a whole word — "crisp
    benjamin", "counterfeit benjamin", "folded benjamin" as hyponyms of *benjamin* — is
    not a lexical relation. It is a description of the headword with a modifier stuck on
    the front, and there is no second lexical unit for the edge to point at. The one
    exception is decided by the store rather than by a rule about
    :class:`~opengloss_generator.schema.LexemeKind`: if the target **is itself an entry
    in the store** (``store.exists``), it is a real compound that happens to contain the
    headword ("ice axe" under *ice*) and it is kept. Asking the store is both simpler and
    more accurate than asking whether the source entry is a ``compound``/``idiom``/
    ``phrasal_verb``, because it is a fact about the *target*, which is what the question
    is about.

    ``collocation`` and ``used_with`` are exempt, and the exemption is not a hedge: "a
    solemn vow" *is* the collocation of *vow*, and demoting it would delete the only
    correct instance of the shape this step otherwise rejects.

``meta_labels`` (free)
    A target that is a label about words rather than a word — "slang term", "modifier",
    "biblical name", "popular given name", "variant spelling", "plural form". These are
    the same artifact class ``filters.py`` rejects at the frontier, one step later in the
    pipeline: the frontier filter stops a meta-label becoming an *entry*, and nothing
    stopped one becoming a relation *target*. :data:`META_LABELS` is a superset of
    ``filters``' own list (that one is module-private, so it is mirrored rather than
    imported) plus the lexicographic labels the judge found; :data:`META_QUALIFIERS`
    catches the open-ended ``<qualifier> term|form|name`` shape, deliberately as a
    *qualifier* list rather than a bare suffix rule, so "life form", "code name" and
    "art form" — which are real lexical units — survive.

``validity`` (nano, ``HYGIENE`` policy)
    What no rule can decide. One call per entry (chunked at :data:`MAX_REFS_PER_CALL`
    refs) listing every relation still standing after the three free steps as its type,
    its sense's gloss (printed once per sense, not once per relation), the target term
    and the target's own canonical gloss where the relation is resolved, and asking for a
    ``valid`` boolean per ref plus an optional ``better_type``. The relation-type
    definitions live in :data:`RELATION_VALIDITY_INSTRUCTIONS`, byte-stable so the
    provider's prompt cache can match on them and so two sweeps' numbers are comparable.

    A verdict is applied one of three ways: ``valid`` keeps the relation untouched; not
    ``valid`` with a ``better_type`` that parses to a different
    :class:`~opengloss_generator.schema.RelationType` **retypes** it (``retyped: nano
    <old>→<new>``), which is the answer to the "one-dollar bill as an antonym of
    benjamin" shape — the claim is real, the type is wrong; not ``valid`` with no usable
    ``better_type`` demotes it to ``see_also`` (``demoted: nano invalid``). A
    ``better_type`` on a relation the model called *valid* is ignored: a retype is a
    repair, and there is nothing to repair.

    ``see_also`` relations are **not** listed. ``see_also`` is already this pass's floor,
    the weakest thing an edge can say, and the only verdict that could change one is a
    promotion — which is exactly the judgement a nano model should not be trusted to make
    unprompted, and which would spend money on the largest and least consequential slice
    of the graph.

Idempotence
-----------

The three free steps are idempotent because they leave nothing behind for themselves to
find: a demoted relation is a ``see_also``, and every free check skips ``see_also``.

The model step carries D-47's sentinel — ``relation_hygiene:validity:<digest>;attempts=<n>``
on a zero-cost provenance record — with one deliberate difference from
``content_hygiene``'s: the digest is taken over the ref set **as it stands after the
verdicts have been applied**, not before. Taken before, a sweep that demoted anything
would leave a marker for a set that no longer exists, and the very next sweep would find
a different digest and buy a second opinion about the relations the first sweep had
already passed. Taken after, the marker reads "I have judged exactly this set", a second
sweep over an unchanged entry is free, and an entry that later *gains* a relation earns
one more attempt, bounded at :data:`MAX_ATTEMPTS` per entry.

Run order, and ``graph_hygiene``'s reciprocity step
---------------------------------------------------

Run this pass **after** ``graph_hygiene``, not before, and re-run it if ``graph_hygiene``
is re-run. ``graph_hygiene`` step 4 adds the implied reverse of a *symmetric* relation
(``synonym``, ``antonym``, ``confusable_with`` — ``see_also`` is not among them), so it
never infers anything from a relation this pass has demoted. But it keys its
"already asserted" set on the exact triple ``(lexeme, type, target)``, so a demoted
``synonym`` A→B leaves ``(A, synonym, B)`` unasserted while a *resolved* ``synonym`` B→A
may still stand on the far side — and step 4 would then write the forward edge back onto
A as a fresh reciprocal.

**Amendment (D-50's follow-up sweep).** A measured sweep did show it mattered: reciprocity
on the 10K core fell from 99.96% to 78.7% for ``synonym`` and 99.99% to 84.7% for
``antonym`` after this pass ran, because every step here — not only ``validity`` — demotes
plenty of *resolved* symmetric relations, not only the unresolvable "banners"/"crisp
benjamin"/"slang term" shapes the original text above expected to dominate. Every step
now carries the same second phase ``content_hygiene``'s ``synonym_antonym`` step does: a
demotion of a relation whose type is ``synonym``, ``antonym`` or ``confusable_with``
(:data:`_SYMMETRIC_RELATION_TYPES`, mirrored from
:data:`~opengloss_generator.workflows.graph_hygiene.SYMMETRIC_RELATION_TYPES`) toward a
*different* lexeme queues a :class:`_FarSideRequest`; once the step's main pooled sweep
has fully drained and every lock from it released, a second pooled sweep visits each
distinct target lexeme under its own lock and demotes every relation there of the same
type that points back at the source lexeme and either is unresolved or resolves to the
exact sense that was demoted (:func:`_is_far_side_of` — a relation resolved to a
*different* sense of the source is left alone, since it may be a perfectly good assertion
about that other sense). The far-side note is ``demoted: far side of <sense id>
(<reason>)``, which both starts with ``"demoted:"`` — so
:func:`~opengloss_generator.workflows.graph_hygiene._asserted_pairs` blocks
``graph_hygiene`` step 4 from writing the reciprocal straight back — and names the
near-side reason in full, so either side of a stale pair can be traced from its note
alone. Counted separately from the ordinary ``demoted`` figure as
:attr:`_RelationStepResult.far_side_demoted` (folded into ``demoted`` too, so that total
still answers "how many relations did this step demote") and surfaced on
:attr:`RelationHygieneOutcome.far_side_demoted`. Idempotent by construction: a relation
already demoted no longer matches its pre-demotion type, so a second sweep that finds no
new near-side demotions queues no new far-side requests.

**Second finding (the far side and a stop event).** The far-side phase is the only pooled
sweep here that is *not* given the run's stop event, and that is load-bearing rather than
an oversight — see :func:`_demote_far_side_all`. It repairs demotions already written; a
stop event means "spend no more", and this phase spends nothing. Given the event it would
be skipped entirely, because ``run_pool``'s workers return before pulling their first item
once it is set and a budget stop sets it. A real 10K ``validity`` sweep stopped on its cap
and reported ``demoted=12321, far_side_demoted=0``: 12,321 near-side demotions banked and
every reciprocal they implied left standing.

Concurrency and locking (D-31)
------------------------------

Every step drives its ids through :func:`~opengloss_generator.runner.run_pool`, and the
handler holds the entry's lock across the whole of read → deterministic work → model
call → write, so no entry is ever read outside the lock it is written under. Two reads
happen outside a lock, both strictly read-only and both for context only:
``headword_phrases`` asks ``store.exists`` about a *target*, and ``validity`` looks up a
target entry's canonical gloss for its prompt. Counters go through :class:`_Tally`,
mutated only while holding an ``asyncio.Lock``, for the reason ``retrofit._Tally`` gives.

:class:`~opengloss_generator.workflows.content_hygiene.StepResult` and
:data:`~opengloss_generator.workflows.content_hygiene.UNRESOLVED_GLOSS` are imported from
``content_hygiene`` rather than restated: they are that module's public surface and this
pass reports the same shape. Everything else it needs there — the tally, the pool driver,
the provenance helpers, the D-47 marker — is module-private, so it is mirrored here, and
this module's contract and instructions are module-private for the same reason
``content_hygiene``'s are: ``contracts.py``, ``prompts.py`` and ``readability.py`` are
being edited concurrently on this branch.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Annotated

from pydantic import BaseModel, ConfigDict, Field

from opengloss_generator import spans
from opengloss_generator.errors import BudgetExceededError, GenerationError
from opengloss_generator.filters import normalise_candidate
from opengloss_generator.log import get_logger
from opengloss_generator.prompts import PROMPT_VERSION
from opengloss_generator.runner import run_pool
from opengloss_generator.schema import Provenance, RelationType, StageName
from opengloss_generator.workflows.content_hygiene import (
    PROGRESS_EVERY,
    UNRESOLVED_GLOSS,
    StepResult,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Iterable, Sequence

    from opengloss_generator.schema import Lexeme, POSEntry, Relation, Sense
    from opengloss_generator.stages import StageRunner
    from opengloss_generator.store import LexemeStore

__all__ = [
    "FAR_SIDE_NOTE_PREFIX",
    "HEADWORD_INFLECTION_NOTE",
    "HEADWORD_PHRASE_NOTE",
    "MAX_ATTEMPTS",
    "MAX_REFS_PER_CALL",
    "META_LABELS",
    "META_LABEL_NOTE",
    "META_QUALIFIERS",
    "NANO_INVALID_NOTE",
    "NANO_RETYPE_NOTE",
    "RELATION_VALIDITY_INSTRUCTIONS",
    "SIBLING_INFLECTION_NOTE",
    "RelationHygieneOutcome",
    "RelationHygieneStep",
    "is_meta_label",
    "run_relation_hygiene",
]

_LOG = get_logger(__name__)

#: Provenance ``model`` for every free edit this pass makes. A rule, not a model, named
#: the way ``content_hygiene.DETERMINISTIC_MODEL`` and ``graph_hygiene.DEMOTION_MODEL``
#: are.
DETERMINISTIC_MODEL = "rule:relation_hygiene"

#: Note text per edit, and the audit trail: every relation this pass touched can be found
#: again by its note. :data:`SIBLING_INFLECTION_NOTE` and :data:`NANO_RETYPE_NOTE` are
#: prefixes completed with the sibling term and the type change respectively.
HEADWORD_INFLECTION_NOTE = "demoted: inflection of headword"
SIBLING_INFLECTION_NOTE = "demoted: inflection of sibling "
HEADWORD_PHRASE_NOTE = "demoted: modifier phrase on headword"
META_LABEL_NOTE = "demoted: meta-label"
NANO_INVALID_NOTE = "demoted: nano invalid"
NANO_RETYPE_NOTE = "retyped: nano "

#: Prefix for the far-side note a demotion of a symmetric relation queues (D-50's
#: amendment). Completed with the demoted sense id and, in parentheses, the near-side
#: reason. Starts with ``"demoted:"`` like every other note this pass writes, which is
#: what stops :func:`~opengloss_generator.workflows.graph_hygiene._asserted_pairs` from
#: writing the pair straight back as a fresh reciprocal.
FAR_SIDE_NOTE_PREFIX = "demoted: far side of "

#: Relation types that hold in both directions by definition, mirrored from
#: :data:`~opengloss_generator.workflows.graph_hygiene.SYMMETRIC_RELATION_TYPES` (not
#: part of that module's ``__all__``, so mirrored rather than imported, the same reason
#: :data:`META_LABELS` mirrors ``filters.py``'s own list). Only a demotion of one of these
#: has a reciprocal that can go stale — the far-side amendment applies to exactly this set.
_SYMMETRIC_RELATION_TYPES: frozenset[RelationType] = frozenset(
    {RelationType.SYNONYM, RelationType.ANTONYM, RelationType.CONFUSABLE_WITH}
)

#: How many refs one ``validity`` call answers for. An entry with more is chunked, one
#: call per chunk, because the answer is one short record per ref and 60 of them is
#: already most of the ``HYGIENE`` policy's output ceiling — the QA sweep lost two entries
#: to exactly that truncation (``docs/QA-DIARY.md``, Iteration 1).
MAX_REFS_PER_CALL = 60

#: How many attempts the model step makes on one entry before leaving what still offends
#: alone rather than billing a third answer for it (D-47's bound, per entry).
MAX_ATTEMPTS = 2

#: Separates the ref-set digest from the attempt count inside a marker note.
_ATTEMPTS_SEPARATOR = ";attempts="

#: The model step's sentinel prefix. Distinct from every other pass's, because the call
#: reuses the shared ``HYGIENE`` stage rather than adding a stage of its own, so the stage
#: alone would collide with ``content_hygiene``, ``retrofit`` and every other pass that
#: does the same.
_VALIDITY_PREFIX = "relation_hygiene:validity"

#: How often a running step logs its progress, in entries. Imported from
#: ``content_hygiene`` so every sweep in the project reads the same in a run log.
_PROGRESS_EVERY = PROGRESS_EVERY

#: Relation types the ``inflections`` step leaves alone. ``see_also`` is the pass's own
#: floor and re-checking it is what would make the step non-idempotent; ``derivation`` is
#: about morphology by definition, and demoting one for naming a morphological relative
#: would delete the information the type exists to carry.
_INFLECTION_EXEMPT: frozenset[RelationType] = frozenset(
    {RelationType.SEE_ALSO, RelationType.DERIVATION}
)

#: Relation types the ``headword_phrases`` step leaves alone. A collocation or a
#: ``used_with`` target that contains the headword is not the defect — it is the whole
#: point of those two types ("a solemn vow", "vow of silence").
_PHRASE_EXEMPT: frozenset[RelationType] = frozenset(
    {RelationType.SEE_ALSO, RelationType.COLLOCATION, RelationType.USED_WITH}
)

#: Targets that are labels about words rather than words. The first block mirrors
#: ``filters._META_LABELS`` (module-private there, so it cannot be imported); the rest are
#: the lexicographic labels the QA judge found standing as relation targets on the core
#: store. Compared against :func:`~opengloss_generator.filters.normalise_candidate`'s
#: output, so matching is case- and surrounding-punctuation-insensitive.
META_LABELS: frozenset[str] = frozenset(
    {
        # mirrored from filters.py
        "see also",
        "see",
        "cf",
        "compare",
        "figurative",
        "literal",
        "archaic",
        "obsolete",
        "colloquial",
        "slang",
        "informal",
        "formal",
        "none",
        "n/a",
        "na",
        "unknown",
        "various",
        "etc",
        "other",
        "general",
        # measured on the judged sample
        "slang term",
        "informal term",
        "formal term",
        "technical term",
        "general term",
        "generic term",
        "umbrella term",
        "archaic term",
        "obsolete term",
        "modifier",
        "qualifier",
        "intensifier",
        "determiner",
        "biblical name",
        "given name",
        "popular given name",
        "first name",
        "surname",
        "family name",
        "proper noun",
        "common noun",
        "collective noun",
        "mass noun",
        "count noun",
        "abbreviation",
        "acronym",
        "initialism",
        "contraction",
        "variant",
        "variant spelling",
        "alternative spelling",
        "alternate spelling",
        "alternative form",
        "spelling variant",
        "plural",
        "plural form",
        "singular",
        "singular form",
        "figurative sense",
        "literal sense",
        "figurative use",
        "literal use",
        "part of speech",
        "prefix",
        "suffix",
        "root word",
    }
)

#: The qualifier half of the open-ended ``<qualifier> term|form|name`` shape. A bare
#: suffix rule would take "life form", "art form" and "code name" with it, which are real
#: lexical units; requiring a *lexicographic qualifier* in front keeps them.
META_QUALIFIERS: frozenset[str] = frozenset(
    {
        "slang",
        "informal",
        "formal",
        "colloquial",
        "archaic",
        "obsolete",
        "figurative",
        "literal",
        "technical",
        "scientific",
        "medical",
        "legal",
        "general",
        "generic",
        "umbrella",
        "blanket",
        "collective",
        "proper",
        "common",
        "plural",
        "singular",
        "variant",
        "alternative",
        "alternate",
        "given",
        "popular",
        "biblical",
        "dialect",
        "dialectal",
        "regional",
        "poetic",
        "derogatory",
        "offensive",
        "familiar",
        "polite",
        "vulgar",
    }
)

#: The head nouns :data:`META_QUALIFIERS` may sit in front of.
_META_HEADS: frozenset[str] = frozenset({"term", "form", "name", "word", "usage", "sense"})

#: Every relation type spelled the way the model is asked to spell it, for parsing a
#: ``better_type`` back. Built from the enum so a new member cannot be forgotten here.
_RELATION_TYPE_BY_VALUE: dict[str, RelationType] = {member.value: member for member in RelationType}


class RelationHygieneStep:
    """Names of the steps :func:`run_relation_hygiene` can select between."""

    INFLECTIONS = "inflections"
    HEADWORD_PHRASES = "headword_phrases"
    META_LABELS = "meta_labels"
    VALIDITY = "validity"

    #: The order the steps run in: the three free ones first, so a run stopped by its
    #: budget has already banked everything that cost nothing, and so the one step that
    #: spends money is never billed for a relation a rule had already settled.
    ALL: tuple[str, ...] = (INFLECTIONS, HEADWORD_PHRASES, META_LABELS, VALIDITY)


# --------------------------------------------------------------------------------------
# The outcome
# --------------------------------------------------------------------------------------


@dataclass(slots=True)
class _RelationStepResult(StepResult):
    """``StepResult`` plus how many of a step's demotions were the far side of another.

    A demotion of a symmetric relation (synonym, antonym, confusable_with) toward a
    different lexeme queues a check of that lexeme's own relations for the stale
    reciprocal it leaves behind (D-50's amendment; see the module docstring's "Run
    order" section). :attr:`far_side_demoted` is how many of those a step actually acted
    on; every one of them is also folded into the inherited :attr:`~StepResult.demoted`,
    so that counter keeps meaning "how many relations did this step demote" without a
    caller having to add the two together.

    Attributes:
        far_side_demoted: Relations demoted on the far side of one of this step's own
            near-side demotions.
    """

    far_side_demoted: int = 0

    def as_dict(self) -> dict[str, object]:
        """Return :meth:`StepResult.as_dict`'s view plus :attr:`far_side_demoted`."""
        data = super().as_dict()
        data["far_side_demoted"] = self.far_side_demoted
        return data


@dataclass(slots=True)
class RelationHygieneOutcome:
    """What one :func:`run_relation_hygiene` sweep did, per step.

    Attributes:
        steps: One :class:`~opengloss_generator.workflows.content_hygiene.StepResult` per
            step that ran, keyed by step name — in practice always a
            :class:`_RelationStepResult`, since every step here builds its own through
            :class:`_Tally`. The ``removed`` and ``rewritten`` counters of that shared
            shape are always zero here: this pass retypes relations and never touches
            stored prose.
        entries_changed: How many *distinct* entries were written across every step — not
            the sum of the per-step figures, which would count an entry twice when two
            steps both touched it.
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
    def demoted(self) -> int:
        """Return how many relations were demoted to ``see_also`` across every step."""
        return sum(result.demoted for result in self.steps.values())

    @property
    def retyped(self) -> int:
        """Return how many relations the model step retyped rather than demoted."""
        return sum(result.retyped for result in self.steps.values())

    @property
    def far_side_demoted(self) -> int:
        """Return how many far-side reciprocals were demoted across every step (D-50)."""
        return sum(
            result.far_side_demoted
            for result in self.steps.values()
            if isinstance(result, _RelationStepResult)
        )

    @property
    def stopped_reason(self) -> str | None:
        """Return why the run stopped early, or ``None`` if every selected step ran.

        A budget stop is reported here, not raised, so a caller's run summary can say
        "budget" rather than "completed" — the convention ``run_content_hygiene`` and
        ``run_retrofit`` both follow.
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
            "demoted": self.demoted,
            "retyped": self.retyped,
            "far_side_demoted": self.far_side_demoted,
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

    Mirrors ``content_hygiene._Tally``, which is module-private there. Single-threaded
    asyncio does make ``counter += 1`` atomic on its own, but these counters are touched
    by many handlers around many awaits and that guarantee is a property of the
    interpreter rather than of this code.

    Args:
        name: The step this tally belongs to.
        changed_ids: The run-level set of entry ids written by any step, shared so
            :attr:`RelationHygieneOutcome.entries_changed` counts distinct entries rather
            than entry-visits.
    """

    def __init__(self, name: str, changed_ids: set[str]) -> None:
        """Start an empty result for the named step."""
        self._lock = asyncio.Lock()
        self._result = _RelationStepResult(name=name)
        self._changed: set[str] = set()
        self._changed_ids = changed_ids
        self._visited = 0

    @property
    def result(self) -> _RelationStepResult:
        """Return the accumulated result; read it once the pool has drained."""
        return self._result

    async def entry(
        self,
        lexeme_id: str,
        *,
        scanned: bool = True,
        demoted: int = 0,
        retyped: int = 0,
        accepted: int = 0,
        rejected: int = 0,
        far_side_demoted: int = 0,
    ) -> None:
        """Fold one visited entry into the step result.

        Args:
            lexeme_id: The entry visited.
            scanned: Whether this visit counts as a scan of the step's own id list.
                ``False`` for a far-side phase visit (D-50's amendment): the target lexeme
                a demotion's reciprocal check visits is not one of the ids the step was
                driven over, and counting it as a scan would inflate ``entries_scanned``
                past the length of the id list the step was actually given.
            demoted: Relations demoted to ``see_also`` in this entry, far-side demotions
                included.
            retyped: Relations retyped to a different relation type in this entry.
            accepted: Model verdicts applied for this entry.
            rejected: Model verdicts refused for this entry.
            far_side_demoted: Of ``demoted``, how many were the far side of another
                step's-own demotion rather than a demotion this entry earned directly.
        """
        async with self._lock:
            result = self._result
            if scanned:
                self._visited += 1
                result.entries_scanned += 1
            result.demoted += demoted
            result.retyped += retyped
            result.accepted += accepted
            result.rejected += rejected
            result.far_side_demoted += far_side_demoted
            if demoted or retyped:
                self._changed.add(lexeme_id)
                self._changed_ids.add(lexeme_id)
                result.entries_changed = len(self._changed)
            if self._visited and self._visited % _PROGRESS_EVERY == 0:
                _LOG.info(
                    "relation_hygiene_progress",
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

    Generic, like ``content_hygiene``'s own ``_drive``: a step with a far-side phase
    (D-50's amendment) drives lexeme ids in its main phase and :class:`_FarSideRequest`
    objects in its second one, through this same wrapper.

    ``run_pool`` already treats :class:`BudgetExceededError` as a clean stop of the whole
    pool rather than an error to propagate, so this wrapper exists only to record *why*
    the step stopped before the exception is swallowed.

    Args:
        items: The work items — lexeme ids, or a step's far-side requests.
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


def _key(term: str) -> str:
    """Return the comparison key for a surface form.

    :func:`~opengloss_generator.filters.normalise_candidate` is the project's answer to
    this question already — lowercase, collapsed whitespace, surrounding punctuation
    stripped, internal hyphens and apostrophes kept because they are part of real
    headwords — so this pass compares targets the same way the frontier filter does.

    Args:
        term: The surface form to key.

    Returns:
        The normalised key, possibly empty.
    """
    return normalise_candidate(term)


def _rule_provenance(note: str | None = None) -> Provenance:
    """Return the zero-cost provenance record a free edit is stamped with.

    Args:
        note: Free text to preserve on the record.

    Returns:
        A :class:`~opengloss_generator.schema.Provenance` with every cost and token field
        at zero, so a naive sum over an entry's provenance table is unaffected by this
        pass having run.
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

    Nothing is ever deleted (D-1's spirit): a defective relation becomes a weaker — or a
    better-typed — one that still says something true, and the reason is written where a
    later reader will find it. Mirrors ``content_hygiene._retype``.

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
    """Return ``(pos_entry, sense, sense_id)`` for every non-retired sense."""
    return [triple for triple in entry.iter_senses() if not triple[1].retired]


class _Editor:
    """Adds one provenance record per entry, lazily, and hands out its id.

    A free step that finds nothing must not write a provenance record, or every entry in
    the store would grow one on every sweep; a free step that finds three things wants
    one record, not three. Both fall out of asking this for the id only when there is
    something to stamp.

    Args:
        entry: The entry being edited.
    """

    __slots__ = ("_entry", "_provenance_id")

    def __init__(self, entry: Lexeme) -> None:
        """Note the entry; no record is created until one is asked for."""
        self._entry = entry
        self._provenance_id: str | None = None

    def provenance_id(self) -> str:
        """Return this entry's record for the current step, creating it on first use."""
        if self._provenance_id is None:
            self._provenance_id = self._entry.add_provenance(_rule_provenance())
        return self._provenance_id


# --------------------------------------------------------------------------------------
# The D-47 marker
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Marker:
    """The most recent sentinel the model step left on an entry.

    Attributes:
        digest: The ref-set hash the marker was written for — the set as it stood *after*
            that attempt's verdicts were applied.
        attempts: How many attempts the step has made on this entry, this one included.
    """

    digest: str
    attempts: int


def _ref_digest(refs: Iterable[str]) -> str:
    """Return a stable short hash of a set of relation refs.

    Args:
        refs: Stable identifiers of the relations in question, in any order.

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


def _attempt_number(entry: Lexeme, prefix: str, refs: Sequence[str]) -> int | None:
    """Return which attempt is due on an entry, or ``None`` if none is.

    An entry is due an attempt when it has something to judge and either the step has
    never visited it, or the set of things to judge has changed since the step last
    answered — and it has not already had :data:`MAX_ATTEMPTS` of them (D-47).

    Args:
        entry: The entry being considered.
        prefix: The step's note prefix.
        refs: Stable identifiers of what is judgeable *now*.

    Returns:
        The 1-based attempt number, or ``None`` when the entry must be skipped — which is
        also the "do not bill this" signal for the caller.
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
        refs: The refs the marker is being written for — for this pass, the set as it
            stands *after* the attempt's verdicts were applied (see the module docstring).
        attempt: The 1-based attempt number.

    Returns:
        ``<prefix>:<digest>;attempts=<n>``.
    """
    return f"{prefix}:{_ref_digest(refs)}{_ATTEMPTS_SEPARATOR}{attempt}"


# --------------------------------------------------------------------------------------
# Far-side reciprocity (D-50's amendment)
# --------------------------------------------------------------------------------------
#
# Shared by every step below that can demote a symmetric relation: ``inflections`` and
# ``headword_phrases`` and ``meta_labels`` whenever the offending relation happens to be
# typed ``synonym``, ``antonym`` or ``confusable_with`` rather than one of the asymmetric
# types they otherwise mostly see, and ``validity``'s "demoted: nano invalid" path (its
# "retyped: nano ..." path is a repair, not a demotion, and has no far side to chase).
# See the module docstring's amendment under "Run order" for why this exists.


@dataclass(frozen=True, slots=True)
class _FarSideRequest:
    """One far-side reciprocal to check, after a symmetric-relation demotion queued it.

    Attributes:
        lexeme_id: The entry to visit — the demoted relation's target lexeme (B).
        source_lexeme: The entry the demotion happened on (A).
        source_sense: The sense id on A whose relation was demoted (S).
        relation_type: The type the near-side relation had *before* its demotion (T).
            Only a far-side relation still carrying this same type is a candidate — one
            already ``see_also`` cannot be the stale reciprocal, which is what makes a
            second sweep over this a no-op.
        reason: The near-side note's reason, without its own ``"demoted: "`` prefix,
            folded into the far-side note so both sides of a stale pair name the same
            cause.
    """

    lexeme_id: str
    source_lexeme: str
    source_sense: str
    relation_type: RelationType
    reason: str


def _far_side_reason(note: str) -> str:
    """Return a near-side demotion note's reason, without its own ``"demoted: "`` prefix.

    Args:
        note: The note the near-side relation was just given.

    Returns:
        The reason half, folded into the far-side note this pass writes for the
        reciprocal it implies.
    """
    return note.removeprefix("demoted: ")


def _far_side_request(
    entry: Lexeme, sense_id: str, relation: Relation, reason: str
) -> _FarSideRequest | None:
    """Return the far-side check one demotion implies, or ``None`` if it implies none.

    Must be called *before* ``relation`` is retyped: it reads ``relation.type`` to decide
    whether the type being left behind was symmetric, which is exactly the type that no
    longer holds once the retype has happened.

    Args:
        entry: The entry the demotion is happening on (A).
        sense_id: The sense whose relation is being demoted (S).
        relation: The relation about to be demoted, inspected in its pre-demotion state.
        reason: The near-side note's reason, already stripped of its ``"demoted: "``
            prefix (see :func:`_far_side_reason`).

    Returns:
        The request, or ``None`` when the relation's type is not one of
        :data:`_SYMMETRIC_RELATION_TYPES` or its target is this same entry — a self-loop
        has no far side to reciprocate from.
    """
    if relation.type not in _SYMMETRIC_RELATION_TYPES:
        return None
    target_lexeme = relation.target.lexeme_id
    if target_lexeme == entry.lexeme_id:
        return None
    return _FarSideRequest(
        lexeme_id=target_lexeme,
        source_lexeme=entry.lexeme_id,
        source_sense=sense_id,
        relation_type=relation.type,
        reason=reason,
    )


def _is_far_side_of(relation: Relation, request: _FarSideRequest) -> bool:
    """Return whether a far-side relation is the reciprocal a demotion implies.

    Identity has to be positive, not merely plausible (mirrors
    ``content_hygiene._is_reciprocal_of``): the far-side entry may hold a perfectly good
    relation of the same type toward the same lexeme *about a different sense*, and
    demoting that would be a new defect rather than a repair.

    Args:
        relation: The far-side relation under consideration.
        request: The demotion that prompted the check.

    Returns:
        Whether ``relation`` still carries the pre-demotion type, points back at the
        source lexeme, and either has never been resolved to a sense or resolves to
        exactly the sense whose relation was demoted.
    """
    return (
        relation.type is request.relation_type
        and relation.target.lexeme_id == request.source_lexeme
        and relation.target.sense_id in (None, request.source_sense)
    )


async def _demote_far_side(request: _FarSideRequest, store: LexemeStore, tally: _Tally) -> None:
    """Demote the far-side reciprocal one symmetric-relation demotion implies, for $0.

    Visits ``request.lexeme_id`` (B) under its own lock, and only ever after the phase
    that queued the request has fully drained and released A's lock, so no two entry
    locks are ever held at once (D-31). Idempotent: a relation already demoted to
    ``see_also`` no longer carries ``request.relation_type`` and :func:`_is_far_side_of`
    skips it.

    Args:
        request: The far-side check to perform.
        store: The store.
        tally: The step tally the caller is accumulating into; the visit is folded into
            the same step's counts, marked as a second-phase visit rather than a scan.
    """
    demoted = 0
    async with store.locked(request.lexeme_id):
        entry = store.read(request.lexeme_id)
        if entry is None:
            return
        editor = _Editor(entry)
        for _, sense, _ in _live_senses(entry):
            for relation in sense.relations:
                if not _is_far_side_of(relation, request):
                    continue
                note = f"{FAR_SIDE_NOTE_PREFIX}{request.source_sense} ({request.reason})"
                _retype(relation, RelationType.SEE_ALSO, note, editor.provenance_id())
                demoted += 1
        if demoted:
            store.write(entry)
    await tally.entry(request.lexeme_id, scanned=False, demoted=demoted, far_side_demoted=demoted)


async def _demote_far_side_all(
    requests: Sequence[_FarSideRequest],
    store: LexemeStore,
    tally: _Tally,
    *,
    workers: int,
) -> None:
    """Run a step's far-side phase over every request its near-side phase queued.

    A second pooled sweep, run only once the first has fully drained: every lock the
    near-side phase held has been released before this one acquires any (D-31).

    **Takes no stop event, deliberately** (D-50's second finding). Every other pooled
    sweep in this pass buys something, so a stop event is a "spend no more" instruction it
    must honour. This one buys nothing: it is the *repair* of near-side demotions already
    written to disk, one local read-modify-write per target entry and not a single model
    call. Honouring the stop here would abandon the repair for work the run has already
    paid for and committed, leaving the store asserting exactly one half of a pair this
    pass has just judged untrue — which is what a real 10K sweep did, silently, because
    ``run_pool``'s workers return before pulling their first item once the event is set
    and a budget stop sets it (see :func:`~opengloss_generator.runner.run_pool`). The
    phase is bounded by the near-side demotions the run managed to make, so draining it
    cannot run away: no stop, however urgent, is worth a knowingly inconsistent store.

    Args:
        requests: Every far-side check the near-side phase queued, in any order and with
            duplicates — a request naming the same ``(lexeme, source, sense, type)`` more
            than once is deduplicated here, and processed in a stable order so the same
            store produces the same result whatever order the near-side phase's workers
            finished in.
        store: The store.
        tally: The step tally to accumulate into. Its ``stopped_reason`` is already set by
            the near-side phase when that one stopped, and this phase never clears it: the
            step still reports that it stopped early, having repaired what it did do.
        workers: Pool size.
    """
    ordered = sorted(
        set(requests),
        key=lambda r: (r.lexeme_id, r.source_lexeme, r.source_sense, r.relation_type.value),
    )

    async def visit(request: _FarSideRequest) -> None:
        await _demote_far_side(request, store, tally)

    await _drive(ordered, visit, tally, workers=workers, stop_event=None)


# --------------------------------------------------------------------------------------
# Step 1 — inflections (free)
# --------------------------------------------------------------------------------------


def _headword_forms(entry: Lexeme, pos_entry: POSEntry) -> set[str]:
    """Return the normalised inflected forms of an entry's headword.

    The union of the model-supplied
    :meth:`~opengloss_generator.schema.Morphology.inflected_forms` and
    :func:`~opengloss_generator.spans.generate_forms`' rule-based ones — the same union
    ``content_hygiene._forms_for`` builds for span finding, minus the derivations, which
    are a different claim and are the legitimate subject of a ``derivation`` relation.
    The headword itself is excluded: a relation toward one's own lexeme is a self-synonym,
    which ``content_hygiene`` already owns.

    Args:
        entry: The entry.
        pos_entry: The owning part-of-speech entry, for its morphology block.

    Returns:
        The normalised forms, without the headword and without empties.
    """
    forms = {_key(form) for form in pos_entry.morphology.inflected_forms()}
    forms |= {_key(form) for form in spans.generate_forms(entry.headword)}
    forms.discard(_key(entry.headword))
    forms.discard("")
    return forms


def _sibling_base(relation: Relation, siblings: Sequence[Relation]) -> str | None:
    """Return the sibling target ``relation``'s target is an inflection of, if any.

    Args:
        relation: The relation under consideration.
        siblings: The other relations of the same type on the same sense.

    Returns:
        The base term to keep, or ``None`` if no sibling generates this target. Where
        several do, the shortest wins (ties broken alphabetically), which is what "prefer
        the base form" means when the candidates are all shorter than the inflection.
    """
    target = _key(relation.target.term)
    if not target:
        return None
    bases = [
        sibling.target.term
        for sibling in siblings
        if sibling is not relation
        and target in {_key(form) for form in spans.generate_forms(sibling.target.term)}
    ]
    if not bases:
        return None
    best = min(bases, key=lambda term: (len(_key(term)), _key(term)))
    # Every form ``generate_forms`` produces is longer than the term it came from, so a
    # base that is not shorter than its own inflection is a normalisation collision
    # ("Banner" vs "banner") rather than a real pair, and demoting either is arbitrary.
    return best if len(_key(best)) < len(target) else None


def _demote_inflections(entry: Lexeme) -> tuple[int, list[_FarSideRequest]]:
    """Demote every relation target that is an inflection of the headword or a sibling.

    Two passes per sense, in this order, because the first can remove a candidate the
    second would otherwise have kept as a base: the headword's own inflections go first,
    then the surviving relations are grouped by type and each is checked against its
    siblings.

    Args:
        entry: The entry to clean, mutated in place.

    Returns:
        ``(relations demoted, the far-side checks the demotions imply)`` — D-50's
        amendment: a demoted relation of a symmetric type (synonym, antonym,
        confusable_with) toward a different lexeme queues a check of that lexeme's own
        relations for the stale reciprocal it leaves behind.
    """
    demoted = 0
    requests: list[_FarSideRequest] = []
    editor = _Editor(entry)
    for pos_entry, sense, sid in _live_senses(entry):
        forms = _headword_forms(entry, pos_entry)
        for relation in sense.relations:
            if relation.type in _INFLECTION_EXEMPT:
                continue
            if _key(relation.target.term) in forms:
                far_side = _far_side_request(
                    entry, sid, relation, _far_side_reason(HEADWORD_INFLECTION_NOTE)
                )
                _retype(
                    relation,
                    RelationType.SEE_ALSO,
                    HEADWORD_INFLECTION_NOTE,
                    editor.provenance_id(),
                )
                demoted += 1
                if far_side is not None:
                    requests.append(far_side)

        by_type: dict[RelationType, list[Relation]] = {}
        for relation in sense.relations:
            if relation.type in _INFLECTION_EXEMPT:
                continue
            by_type.setdefault(relation.type, []).append(relation)
        for siblings in by_type.values():
            for relation in list(siblings):
                base = _sibling_base(relation, siblings)
                if base is None:
                    continue
                note = f"{SIBLING_INFLECTION_NOTE}{base}"
                far_side = _far_side_request(entry, sid, relation, _far_side_reason(note))
                _retype(relation, RelationType.SEE_ALSO, note, editor.provenance_id())
                demoted += 1
                if far_side is not None:
                    requests.append(far_side)
    return demoted, requests


# --------------------------------------------------------------------------------------
# Step 2 — headword_phrases (free)
# --------------------------------------------------------------------------------------


def _contains_headword(target: str, headword_pattern: re.Pattern[str] | None) -> bool:
    """Return whether a multi-word target contains the headword as a whole word.

    Args:
        target: The normalised target term.
        headword_pattern: The whole-word pattern for the headword, or ``None`` if the
            headword has no matchable content.

    Returns:
        Whether the target is multi-word and the headword appears in it as a whole word.
        A single-word target is never a modifier phrase, whatever it contains.
    """
    if headword_pattern is None or len(target.split()) < 2:  # noqa: PLR2004 - "multi-word"
        return False
    return headword_pattern.search(target) is not None


def _demote_headword_phrases(
    entry: Lexeme, store: LexemeStore, known: dict[str, bool]
) -> tuple[int, int, list[_FarSideRequest]]:
    """Demote every multi-word target built on the headword, unless it is a real entry.

    Args:
        entry: The entry to clean, mutated in place.
        store: The store, read only to ask whether a target is itself an entry.
        known: Per-step memo, ``target key -> exists``. A popular headword's phrases are
            asked about by many entries, and ``store.exists`` is a filesystem stat.

    Returns:
        ``(relations demoted, relations kept because the target is a stored entry, the
        far-side checks the demotions imply)`` — D-50's amendment; see
        :func:`_demote_inflections` for what that last part means.
    """
    pattern = _whole_word_pattern(_key(entry.headword))
    demoted = kept = 0
    requests: list[_FarSideRequest] = []
    editor = _Editor(entry)
    for _, sense, sid in _live_senses(entry):
        for relation in sense.relations:
            if relation.type in _PHRASE_EXEMPT:
                continue
            target = _key(relation.target.term)
            if not _contains_headword(target, pattern):
                continue
            exists = known.get(target)
            if exists is None:
                exists = store.exists(relation.target.term)
                known[target] = exists
            if exists:
                kept += 1
                continue
            reason = _far_side_reason(HEADWORD_PHRASE_NOTE)
            far_side = _far_side_request(entry, sid, relation, reason)
            _retype(relation, RelationType.SEE_ALSO, HEADWORD_PHRASE_NOTE, editor.provenance_id())
            demoted += 1
            if far_side is not None:
                requests.append(far_side)
    return demoted, kept, requests


def _whole_word_pattern(term: str) -> re.Pattern[str] | None:
    r"""Return a compiled whole-word pattern for a term, or ``None`` if it has none.

    Args:
        term: The normalised term to match.

    Returns:
        The pattern, or ``None`` when the term is empty or has no word characters for a
        ``\\b`` assertion to anchor against.
    """
    if not term or not re.search(r"\w", term):
        return None
    return re.compile(rf"\b{re.escape(term)}\b")


# --------------------------------------------------------------------------------------
# Step 3 — meta_labels (free)
# --------------------------------------------------------------------------------------


def is_meta_label(term: str) -> bool:
    """Return whether a relation target is a label about words rather than a word.

    Args:
        term: The target term, in any casing; it is normalised here.

    Returns:
        Whether it is in :data:`META_LABELS`, or has the shape ``<qualifier> term`` /
        ``<qualifier> form`` / ``<qualifier> name`` for a qualifier in
        :data:`META_QUALIFIERS`. The qualifier list is what keeps "life form" and "code
        name" — real lexical units — out of the net.
    """
    normalised = _key(term)
    if normalised in META_LABELS:
        return True
    words = normalised.split()
    return (
        len(words) >= 2  # noqa: PLR2004 - a qualifier and a head noun
        and words[-1] in _META_HEADS
        and words[-2] in META_QUALIFIERS
    )


def _demote_meta_labels(entry: Lexeme) -> tuple[int, list[_FarSideRequest]]:
    """Demote every relation whose target is a meta-label, in place.

    Args:
        entry: The entry to clean, mutated in place.

    Returns:
        ``(relations demoted, the far-side checks the demotions imply)`` — D-50's
        amendment; see :func:`_demote_inflections` for what that means.
    """
    demoted = 0
    requests: list[_FarSideRequest] = []
    editor = _Editor(entry)
    for _, sense, sid in _live_senses(entry):
        for relation in sense.relations:
            if relation.type is RelationType.SEE_ALSO:
                continue
            if not is_meta_label(relation.target.term):
                continue
            far_side = _far_side_request(entry, sid, relation, _far_side_reason(META_LABEL_NOTE))
            _retype(relation, RelationType.SEE_ALSO, META_LABEL_NOTE, editor.provenance_id())
            demoted += 1
            if far_side is not None:
                requests.append(far_side)
    return demoted, requests


# --------------------------------------------------------------------------------------
# The three free steps, as pooled sweeps
# --------------------------------------------------------------------------------------


async def _inflections_step(
    store: LexemeStore,
    runner: StageRunner,
    ids: Sequence[str],
    *,
    workers: int,
    stop_event: asyncio.Event | None,
    changed_ids: set[str],
) -> StepResult:
    """Demote every inflected duplicate of the headword or of a sibling target, for $0.

    A demotion of a symmetric relation type queues a far-side check of its target
    lexeme, run as a second pooled phase once this one has fully drained (D-50's
    amendment; see :func:`_demote_far_side_all`).

    Args:
        store: The store to clean; each entry read and written inside one lock hold.
        runner: Unused — this step makes no model call.
        ids: The entry ids to visit.
        workers: Pool size.
        stop_event: Shared stop event.
        changed_ids: Run-level set of entries written by any step.

    Returns:
        The step's :class:`~opengloss_generator.workflows.content_hygiene.StepResult`.
    """
    del runner  # free step
    tally = _Tally(RelationHygieneStep.INFLECTIONS, changed_ids)
    requests: list[_FarSideRequest] = []
    requests_lock = asyncio.Lock()

    async def clean(lexeme_id: str) -> None:
        found: list[_FarSideRequest] = []
        async with store.locked(lexeme_id):
            entry = store.read(lexeme_id)
            if entry is None:
                return
            demoted, found = _demote_inflections(entry)
            if demoted:
                store.write(entry)
        if found:
            async with requests_lock:
                requests.extend(found)
        await tally.entry(lexeme_id, demoted=demoted)

    await _drive(ids, clean, tally, workers=workers, stop_event=stop_event)
    await _demote_far_side_all(requests, store, tally, workers=workers)
    return tally.result


async def _headword_phrases_step(
    store: LexemeStore,
    runner: StageRunner,
    ids: Sequence[str],
    *,
    workers: int,
    stop_event: asyncio.Event | None,
    changed_ids: set[str],
) -> StepResult:
    """Demote every modifier phrase built on the headword, for $0.

    A demotion of a symmetric relation type queues a far-side check of its target
    lexeme, run as a second pooled phase once this one has fully drained (D-50's
    amendment; see :func:`_demote_far_side_all`).

    Args:
        store: The store to clean, and the authority on whether a target is a real entry.
        runner: Unused — this step makes no model call.
        ids: The entry ids to visit.
        workers: Pool size.
        stop_event: Shared stop event.
        changed_ids: Run-level set of entries written by any step.

    Returns:
        The step's :class:`~opengloss_generator.workflows.content_hygiene.StepResult`.
    """
    del runner  # free step
    tally = _Tally(RelationHygieneStep.HEADWORD_PHRASES, changed_ids)
    known: dict[str, bool] = {}
    requests: list[_FarSideRequest] = []
    requests_lock = asyncio.Lock()

    async def clean(lexeme_id: str) -> None:
        found: list[_FarSideRequest] = []
        async with store.locked(lexeme_id):
            entry = store.read(lexeme_id)
            if entry is None:
                return
            demoted, kept, found = _demote_headword_phrases(entry, store, known)
            if demoted:
                store.write(entry)
        if found:
            async with requests_lock:
                requests.extend(found)
        # A phrase kept because the store holds it is a refused edit, not a silent pass:
        # counting it is what makes the exception visible in a run summary.
        await tally.entry(lexeme_id, demoted=demoted, rejected=kept)

    await _drive(ids, clean, tally, workers=workers, stop_event=stop_event)
    await _demote_far_side_all(requests, store, tally, workers=workers)
    return tally.result


async def _meta_labels_step(
    store: LexemeStore,
    runner: StageRunner,
    ids: Sequence[str],
    *,
    workers: int,
    stop_event: asyncio.Event | None,
    changed_ids: set[str],
) -> StepResult:
    """Demote every relation pointing at a label about words, for $0.

    A demotion of a symmetric relation type queues a far-side check of its target
    lexeme, run as a second pooled phase once this one has fully drained (D-50's
    amendment; see :func:`_demote_far_side_all`).

    Args:
        store: The store to clean.
        runner: Unused — this step makes no model call.
        ids: The entry ids to visit.
        workers: Pool size.
        stop_event: Shared stop event.
        changed_ids: Run-level set of entries written by any step.

    Returns:
        The step's :class:`~opengloss_generator.workflows.content_hygiene.StepResult`.
    """
    del runner  # free step
    tally = _Tally(RelationHygieneStep.META_LABELS, changed_ids)
    requests: list[_FarSideRequest] = []
    requests_lock = asyncio.Lock()

    async def clean(lexeme_id: str) -> None:
        found: list[_FarSideRequest] = []
        async with store.locked(lexeme_id):
            entry = store.read(lexeme_id)
            if entry is None:
                return
            demoted, found = _demote_meta_labels(entry)
            if demoted:
                store.write(entry)
        if found:
            async with requests_lock:
                requests.extend(found)
        await tally.entry(lexeme_id, demoted=demoted)

    await _drive(ids, clean, tally, workers=workers, stop_event=stop_event)
    await _demote_far_side_all(requests, store, tally, workers=workers)
    return tally.result


# --------------------------------------------------------------------------------------
# Step 4 — validity (nano, HYGIENE policy)
# --------------------------------------------------------------------------------------
#
# The instructions and the output contract live here, not in prompts.py / contracts.py:
# those files are being edited concurrently on this branch, and keeping every call site
# in this module self-contained means this work never conflicts with that. Nothing
# outside this module depends on the names below.


#: Instructions for the ``validity`` call. Byte-stable and ~1.9K tokens by
#: ``router.estimate_tokens``' measure, so it is well past the 1,024-token minimum a
#: provider prompt cache needs and two sweeps' numbers are comparable. Every relation type
#: in the schema is defined here, because the measured defect is very often a *true claim
#: under the wrong type* ("one-dollar bill" listed as an antonym of "benjamin"), and a
#: model cannot propose a better type it was never told the meaning of.
RELATION_VALIDITY_INSTRUCTIONS = """\
You are checking a dictionary's semantic relations one at a time. Each numbered item is \
one relation asserted by one sense of one headword: a relation type, the definition of \
the sense making the assertion, the target term the relation points at, and -- when the \
dictionary managed to resolve the target -- that target's own definition. Where the \
target was never resolved you are shown "(unresolved)" and must judge from the term \
alone.

Answer, for each item, whether the assertion is true *as typed*, and if it is not, \
whether a different relation type would have made it true.

These are the relation types and exactly what each one claims:

- "synonym": the target means roughly the same thing as this sense, closely enough that \
either could stand in for the other in an ordinary sentence without changing what is \
meant. Two words that merely belong to the same subject are not synonyms.
- "antonym": the target is the opposite of this sense along one clear axis -- hot/cold, \
buy/sell, always/never. A different member of the same set is NOT an antonym: a \
one-dollar bill is not the opposite of a hundred-dollar bill, it is another banknote. \
Neither is a term that simply lacks the quality.
- "hypernym": the target names a broader category that this sense belongs to. "This \
sense IS A KIND OF the target" must read as true.
- "hyponym": the reverse. "The target IS A KIND OF this sense" must read as true. A \
description of this sense with an adjective in front of it -- "crisp banknote" under \
"banknote" -- is not a hyponym; it is the same thing described, and there is no second \
word there.
- "meronym": the target is a part, member or substance of this sense -- "wheel" under \
"car".
- "holonym": the reverse -- the target is the whole this sense is a part of.
- "instance_of": this sense names one particular individual -- a named person, place, \
work, organisation or event -- and the target names the class it is an individual of. \
Use this rather than "hypernym" for named entities.
- "derivation": the target is morphologically derived from this sense's word, or the \
word from it -- "happiness" and "happy". A mere inflection of the same word (a plural, a \
past tense, a third-person form) is NOT a derivation and is not a relation at all: it is \
the same word.
- "collocation": the target is a word this sense habitually appears next to, more often \
than chance would explain -- "solemn" with "vow", "torrential" with "rain".
- "used_with": the target is a word this sense is grammatically or idiomatically \
constructed with -- a preposition, a particle, a fixed partner.
- "confusable_with": the target is a different word that writers commonly mistake for \
this one -- "affect" and "effect".
- "causes": this sense brings the target about.
- "entails": if this sense is true or happens, the target must also be true or happen -- \
"snore" entails "sleep".
- "see_also": the two are related in some way none of the above captures. This is the \
weakest claim available and is almost always defensible.

Mark an item invalid when the target is not a lexical unit at all. Three shapes recur: \
an inflected form of a word already present (a plural, a past tense); a phrase that is \
just the headword with a modifier attached; and a label about language rather than a \
word -- "slang term", "given name", "plural form", "modifier". A descriptive phrase that \
no dictionary would carry as an entry ("indoor plant", "secondary account") is also \
invalid, even when it describes the sense accurately.

When the claim itself is sound but filed under the wrong type, mark the item invalid and \
put the type that WOULD be true in "better_type", spelled exactly as one of the type \
names listed above. When nothing would make it true, leave "better_type" as null. When \
the item is fine as it stands, mark it valid and leave "better_type" null.

Judge from the definitions you are given, not from the words alone; a term with several \
meanings is being asserted about the one definition shown. Do not be strict about \
register or regional variation, and do not mark an item invalid merely because you would \
have chosen a different word. Answer every item you are given, identified by the number \
it was listed under, and answer nothing else.

Worked example. For the headword "banknote", sense "A piece of paper money issued by a \
bank.":

  1. type=synonym | target="banknotes" | target sense: (unresolved)
  2. type=hyponym | target="crisp banknote" | target sense: (unresolved)
  3. type=antonym | target="coin" | target sense: A small flat piece of metal money.
  4. type=synonym | target="bill" | target sense: A piece of paper money.
  5. type=hypernym | target="money" | target sense: A medium of exchange.

The answers are: 1 invalid, better_type null -- "banknotes" is the plural of the \
headword, so there is no second word for the relation to reach. 2 invalid, better_type \
null -- "crisp banknote" is the headword with an adjective in front of it, not a kind of \
banknote anyone would look up. 3 invalid, better_type "see_also" -- a coin is not the \
opposite of a banknote along any axis; both are money, and "see_also" is the strongest \
true thing left to say. 4 valid, better_type null -- the two definitions are \
interchangeable. 5 valid, better_type null -- a banknote is a kind of money, so the \
"IS A KIND OF" reading holds."""


class _DraftRelationVerdict(BaseModel):
    """One verdict on one relation the pass listed."""

    model_config = ConfigDict(
        extra="forbid", validate_by_name=True, validate_by_alias=True, serialize_by_alias=True
    )

    ref: Annotated[int, Field(ge=1)]
    valid: bool
    better_type: str | None


class _DraftRelationVerdicts(BaseModel):
    """Verdicts for one chunk of one entry's relations, produced together."""

    model_config = ConfigDict(
        extra="forbid", validate_by_name=True, validate_by_alias=True, serialize_by_alias=True
    )

    verdicts: Annotated[list[_DraftRelationVerdict], Field(min_length=1)]


@dataclass(slots=True)
class _RelationRef:
    """One relation put to the model, and everything the prompt says about it.

    Attributes:
        relation: The relation itself, mutated in place when the verdict says so.
        sense_id: The asserting sense, half of :attr:`ref_id`.
        sense_key: The sense's ``pos index`` label, used to group rows under one gloss.
        source_gloss: The asserting sense's canonical gloss.
        target_gloss: The target sense's canonical gloss, or
            :data:`~opengloss_generator.workflows.content_hygiene.UNRESOLVED_GLOSS`.
    """

    relation: Relation
    sense_id: str
    sense_key: str
    source_gloss: str
    target_gloss: str

    @property
    def ref_id(self) -> str:
        """Return the identifier the digest keys this relation by.

        The asserting sense, the relation's *current* type and the target lexeme — read
        live off the relation rather than frozen at collection time, so that the marker
        written after a retype describes the relation as it now stands and the next sweep
        recognises it instead of buying a second opinion about it.
        """
        return f"{self.sense_id}|{self.relation.type.value}|{self.relation.target.lexeme_id}"


def _target_gloss(store: LexemeStore, sense_id: str | None, cache: dict[str, str]) -> str:
    """Return the canonical gloss of a resolved relation target, for the prompt.

    The target entry is read *without* its lock: this is a read-only lookup for prompt
    context, never a read the pass then writes back from, so the discipline that matters
    (no entry is written from a read taken outside its own lock) is untouched. Mirrors
    ``content_hygiene._target_gloss``.

    Args:
        store: The store to read from.
        sense_id: The resolved target sense, or ``None`` if the relation is unresolved.
        cache: Per-step memo, ``sense_id -> gloss``.

    Returns:
        The target sense's canonical gloss, or
        :data:`~opengloss_generator.workflows.content_hygiene.UNRESOLVED_GLOSS` when the
        relation is unresolved or the target is missing from the store.
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


def _collect_refs(entry: Lexeme, store: LexemeStore, cache: dict[str, str]) -> list[_RelationRef]:
    """Return every relation of one entry the model should judge.

    ``see_also`` relations are excluded: that type is this pass's demotion floor, and the
    only verdict that could change one is a promotion (see the module docstring).

    Args:
        entry: The entry to inspect. Never mutated.
        store: The store, read only to look up target glosses.
        cache: The step's target-gloss memo.

    Returns:
        The refs in document order — the order the model is shown them in and refers to
        them by.
    """
    refs: list[_RelationRef] = []
    for pos_entry, sense, sid in _live_senses(entry):
        source_gloss = sense.canonical_gloss()
        sense_key = f"{pos_entry.pos.value} {sense.index}"
        for relation in sense.relations:
            if relation.type is RelationType.SEE_ALSO:
                continue
            refs.append(
                _RelationRef(
                    relation=relation,
                    sense_id=sid,
                    sense_key=sense_key,
                    source_gloss=source_gloss,
                    target_gloss=_target_gloss(store, relation.target.sense_id, cache),
                )
            )
    return refs


def _chunks(refs: Sequence[_RelationRef]) -> list[Sequence[_RelationRef]]:
    """Split an entry's refs into calls of at most :data:`MAX_REFS_PER_CALL` each."""
    return [
        refs[start : start + MAX_REFS_PER_CALL] for start in range(0, len(refs), MAX_REFS_PER_CALL)
    ]


def _build_validity_prompt(headword: str, refs: Sequence[_RelationRef]) -> str:
    """Return the volatile half of the ``validity`` prompt for one chunk.

    The asserting sense's gloss is printed once, as a heading over the relations that
    belong to it, rather than repeated on every row: an entry with twelve relations on one
    sense would otherwise pay for its own definition twelve times.

    Args:
        headword: The lexeme's surface form.
        refs: The chunk's refs, in the order the model should answer them — ``ref`` in the
            reply is a 1-based position into this sequence.

    Returns:
        The per-call prompt body.
    """
    lines: list[str] = [f"Headword: {headword}", f"Relations ({len(refs)}):"]
    current: str | None = None
    for position, ref in enumerate(refs, start=1):
        if ref.sense_key != current:
            current = ref.sense_key
            lines.append(f"sense {ref.sense_key}: {ref.source_gloss}")
        lines.append(
            f'  {position}. type={ref.relation.type.value} | target="{ref.relation.target.term}"'
            f" | target sense: {ref.target_gloss}"
        )
    return "\n".join(lines)


def _better_type(drafted: _DraftRelationVerdict, current: RelationType) -> RelationType | None:
    """Return the retype a verdict asks for, or ``None`` if it asks for none.

    Args:
        drafted: The verdict as returned.
        current: The relation's present type.

    Returns:
        The new type when ``better_type`` names a real
        :class:`~opengloss_generator.schema.RelationType` other than ``see_also`` and
        other than the type the relation already has; ``None`` otherwise — including for
        a verdict the model called valid, where there is nothing to repair, and for
        ``see_also``, which is the demotion path rather than a retype.
    """
    if drafted.valid or drafted.better_type is None:
        return None
    proposed = _RELATION_TYPE_BY_VALUE.get(drafted.better_type.strip().lower())
    if proposed is None or proposed is current or proposed is RelationType.SEE_ALSO:
        return None
    return proposed


def _apply_verdict(
    entry: Lexeme, ref: _RelationRef, drafted: _DraftRelationVerdict, provenance_id: str
) -> tuple[int, int, _FarSideRequest | None]:
    """Apply one verdict to one relation, in place.

    Args:
        entry: The entry the relation belongs to (A) — read only for its lexeme id, for
            the far-side check a demotion of a symmetric relation implies (D-50).
        ref: The relation the verdict is about.
        drafted: The verdict.
        provenance_id: The entry's record for this call's edits.

    Returns:
        ``(demoted, retyped, far_side)`` — at most one of ``demoted``/``retyped`` is ever
        1, and ``far_side`` is the far-side check the demotion implies, or ``None`` when
        there is nothing to retype or demote, or the demotion's type was not symmetric.
    """
    if drafted.valid:
        return 0, 0, None
    current = ref.relation.type
    proposed = _better_type(drafted, current)
    if proposed is not None:
        note = f"{NANO_RETYPE_NOTE}{current.value}→{proposed.value}"
        _retype(ref.relation, proposed, note, provenance_id)
        return 0, 1, None
    reason = _far_side_reason(NANO_INVALID_NOTE)
    far_side = _far_side_request(entry, ref.sense_id, ref.relation, reason)
    _retype(ref.relation, RelationType.SEE_ALSO, NANO_INVALID_NOTE, provenance_id)
    return 1, 0, far_side


@dataclass(slots=True)
class _CallCounts:
    """What one entry's ``validity`` calls did.

    Attributes:
        demoted: Relations demoted to ``see_also``.
        retyped: Relations given a different type.
        accepted: Verdicts applied.
        rejected: Verdicts whose ``ref`` named nothing in the chunk they answered.
        answered: Whether at least one call succeeded, which is what earns a marker.
        far_side_requests: The far-side checks (D-50) this entry's demotions imply.
    """

    demoted: int = 0
    retyped: int = 0
    accepted: int = 0
    rejected: int = 0
    answered: bool = False
    far_side_requests: list[_FarSideRequest] = field(default_factory=list)


async def _judge_relations(
    entry: Lexeme,
    refs: Sequence[_RelationRef],
    runner: StageRunner,
    tally: _Tally,
) -> _CallCounts:
    """Ask nano whether each of an entry's relations is true, and apply the answers.

    One call per chunk of :data:`MAX_REFS_PER_CALL` refs. Each call's own priced
    provenance record is added to the entry as it returns; the D-47 marker is written
    separately by the caller, once, after every chunk has been applied, because its digest
    is over the ref set as the verdicts leave it.

    Args:
        entry: The entry whose relations are being judged, mutated in place.
        refs: Every ref, in the order the prompt builder will chunk them.
        runner: The stage runner.
        tally: The step tally, for each call and its cost.

    Returns:
        The counts for this entry.

    Raises:
        BudgetExceededError: A budget stop is a run-level condition and propagates.
    """
    counts = _CallCounts()
    for chunk in _chunks(refs):
        try:
            stage_result = await runner.run(
                # Reuses hygiene's model policy (nano): this is a structural verdict about
                # two definitions, not prose for an audience.
                stage=StageName.HYGIENE,
                output_type=_DraftRelationVerdicts,
                instructions=RELATION_VALIDITY_INSTRUCTIONS,
                prompt=_build_validity_prompt(entry.headword, chunk),
                prompt_version=PROMPT_VERSION,
            )
        except BudgetExceededError:
            raise
        except GenerationError as exc:
            _LOG.warning(
                "relation_hygiene_validity_failed", headword=entry.headword, error=str(exc)
            )
            continue

        await tally.call(stage_result.cost_usd)
        provenance_id = entry.add_provenance(stage_result.provenance)
        counts.answered = True
        for drafted in stage_result.output.verdicts:
            position = drafted.ref - 1
            if not 0 <= position < len(chunk):
                counts.rejected += 1
                continue
            demoted, retyped, far_side = _apply_verdict(
                entry, chunk[position], drafted, provenance_id
            )
            counts.demoted += demoted
            counts.retyped += retyped
            counts.accepted += 1
            if far_side is not None:
                counts.far_side_requests.append(far_side)
    return counts


async def _validity_step(
    store: LexemeStore,
    runner: StageRunner,
    ids: Sequence[str],
    *,
    workers: int,
    stop_event: asyncio.Event | None,
    changed_ids: set[str],
) -> StepResult:
    """Judge every relation the free steps left standing, one nano call per 60 of them.

    A "demoted: nano invalid" verdict on a symmetric relation type queues a far-side
    check of its target lexeme, run as a second pooled phase once this one has fully
    drained (D-50's amendment; see :func:`_demote_far_side_all`). A "retyped: nano ..."
    verdict never does: a retype is a repair, not a demotion, and has no far side to
    chase.

    Args:
        store: The store to clean. Each entry is read, judged — including its calls when
            an attempt is due — and written inside one hold of its own lock.
        runner: The stage runner.
        ids: The entry ids to visit.
        workers: Pool size.
        stop_event: Shared stop event.
        changed_ids: Run-level set of entries written by any step.

    Returns:
        The step's :class:`~opengloss_generator.workflows.content_hygiene.StepResult`.
    """
    tally = _Tally(RelationHygieneStep.VALIDITY, changed_ids)
    gloss_cache: dict[str, str] = {}
    requests: list[_FarSideRequest] = []
    requests_lock = asyncio.Lock()

    async def judge(lexeme_id: str) -> None:
        counts = _CallCounts()
        async with store.locked(lexeme_id):
            entry = store.read(lexeme_id)
            if entry is None:
                return
            refs = _collect_refs(entry, store, gloss_cache)
            attempt = _attempt_number(entry, _VALIDITY_PREFIX, [ref.ref_id for ref in refs])
            if attempt is not None:
                counts = await _judge_relations(entry, refs, runner, tally)
                if counts.answered:
                    # The digest is over what survives the verdicts, not over what was
                    # asked about: a marker for a set the call itself dissolved would buy
                    # a second opinion on the next sweep about relations already passed.
                    surviving = [
                        ref.ref_id for ref in refs if ref.relation.type is not RelationType.SEE_ALSO
                    ]
                    entry.add_provenance(
                        _rule_provenance(_marker_note(_VALIDITY_PREFIX, surviving, attempt))
                    )
                    # Written even when nothing changed: the marker is the only thing the
                    # call bought, and losing it re-bills the same answer.
                    store.write(entry)
        if counts.far_side_requests:
            async with requests_lock:
                requests.extend(counts.far_side_requests)
        await tally.entry(
            lexeme_id,
            demoted=counts.demoted,
            retyped=counts.retyped,
            accepted=counts.accepted,
            rejected=counts.rejected,
        )

    await _drive(ids, judge, tally, workers=workers, stop_event=stop_event)
    await _demote_far_side_all(requests, store, tally, workers=workers)
    return tally.result


# --------------------------------------------------------------------------------------
# The entry point
# --------------------------------------------------------------------------------------


#: One step, as :func:`run_relation_hygiene` calls it. Every step takes the store, the
#: runner and the id list positionally and the pool settings by keyword.
type _StepFn = Callable[..., Awaitable[StepResult]]

_STEP_FUNCTIONS: dict[str, _StepFn] = {
    RelationHygieneStep.INFLECTIONS: _inflections_step,
    RelationHygieneStep.HEADWORD_PHRASES: _headword_phrases_step,
    RelationHygieneStep.META_LABELS: _meta_labels_step,
    RelationHygieneStep.VALIDITY: _validity_step,
}


async def run_relation_hygiene(
    store: LexemeStore,
    runner: StageRunner,
    *,
    workers: int,
    stop_event: asyncio.Event | None = None,
    only: set[str] | None = None,
    lexeme_ids: Sequence[str] | None = None,
) -> RelationHygieneOutcome:
    """Demote or retype the relations the QA judge found untrue (D-50).

    Four steps, described in full in the module docstring: three free ones that settle
    the artifact classes a rule can recognise — inflected duplicates, modifier phrases
    built on the headword, and meta-labels — and one nano call per entry for the rest.
    Every step is idempotent, every entry is read and written inside one hold of its own
    lock, and nothing is ever deleted: a relation that fails a check becomes a
    ``see_also``, or is retyped to the type that would have been true.

    Args:
        store: The store to repair.
        runner: The stage runner. Used by ``validity`` (nano, ``HYGIENE`` policy); the
            three free steps never touch it.
        workers: Pool size for every step.
        stop_event: Shared stop event. A budget stop sets it; a caller may also set it
            from outside (the CLI passes its session's event, which ``SIGINT`` sets).
        only: Step names to run; defaults to all of :attr:`RelationHygieneStep.ALL`.
            Steps run in that attribute's order whatever order they are given in.
        lexeme_ids: Ids to visit; defaults to every id in the store, sorted.

    Returns:
        A :class:`RelationHygieneOutcome` carrying counts and cost per step. If a step
        stopped early its ``stopped_reason`` says why and the remaining steps are skipped;
        the outcome is still returned rather than raised, so a partial run reports what it
        managed to do.

    Raises:
        ValueError: If ``only`` names a step that does not exist.
    """
    selected = set(only) if only is not None else set(RelationHygieneStep.ALL)
    unknown = sorted(selected - set(RelationHygieneStep.ALL))
    if unknown:
        raise ValueError(f"unknown relation hygiene step(s): {unknown}")

    ids = list(lexeme_ids) if lexeme_ids is not None else sorted(store.iter_ids())
    outcome = RelationHygieneOutcome()
    changed_ids: set[str] = set()

    for name in RelationHygieneStep.ALL:
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
                "relation_hygiene_step_stopped",
                step=name,
                reason=result.stopped_reason,
                entries_scanned=result.entries_scanned,
                skipped=[
                    s for s in RelationHygieneStep.ALL if s in selected and s not in outcome.steps
                ],
            )
            break

    outcome.entries_changed = len(changed_ids)
    _LOG.info("relation_hygiene_complete", entries=len(ids), workers=workers, **outcome.as_dict())
    return outcome
