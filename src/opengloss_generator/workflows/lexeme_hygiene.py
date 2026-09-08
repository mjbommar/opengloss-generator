"""Workflow 11 — lexeme hygiene: entries that are not lexemes (D-79).

Every hygiene pass before this one asks a question *inside* one entry. ``content_hygiene``
repairs what a rule can see in its prose, ``relation_hygiene`` judges the far end of one of its
edges, ``sense_hygiene`` audits its sense and part-of-speech inventory — and ``sense_hygiene``
says so explicitly: "No step reads any *other* entry at all: all three questions are answered
entirely from within one entry."

The two defects here cannot be seen that way. Both are the question *should this headword have
an entry at all*, and the first of them can only be answered by looking at a **different**
entry. So they are a pass of their own rather than two more ``sense_hygiene`` steps: the
cross-entry index below is the whole mechanism, and dropping it into a module whose stated
contract is that no step reads another entry would make that contract false for every reader of
it. Both steps end in the same place ``phantom_pos`` does — the senses are tombstoned
(:attr:`~opengloss_generator.schema.Sense.retired`), never deleted, never renumbered (D-1);
their relations are *demoted* to ``see_also`` rather than dropped; and the reason is written to
the entry's provenance table.

The measured problem
--------------------

Both counts are from the v2.1 release, measured against NLTK's WordNet 3.0
(``opengloss-paper/docs/v2.0/review/F-wordnet-diff.md``, 2026-09-07):

``inflection_fold``
    **13,139 of 109,633 headwords are a plural, past tense, participle or comparative of
    another lexeme in the same store** — ``databases``, ``lunches``, ``capillaries``,
    ``staged``, ``weirdest``, ``monopolists``, ``hand signals``. D-75's inflection fold was
    applied when *selecting* the tier-3 and tier-4 word lists, so those two tiers are clean; the
    core and tier 2 were seeded from OpenGloss v1.3, which stored inflected forms as entries of
    their own, and no fold was ever run over them. The store already knows the answer: D-75's
    ``inflections`` repo is built from each ``POSEntry.morphology``, so "databases" is already a
    ``plural`` row pointing at *database*, and a reader who folds the entry loses nothing —
    the string still resolves, to the lemma, through the dataset that exists for exactly that.

``fragments``
    **306 multiword headwords begin or end with a function or auxiliary word** — ``is not``,
    ``some sugar``, ``produce energy``, ``machine based``, ``on top of``. These are not lexical
    units; they are fragments of sentences that the tier-4 candidate filter let through.

Rejecting an entry is the most destructive thing this project does, so both steps are built
around the *keeps* rather than around the retirements.

``inflection_fold`` (nano, ``HYGIENE`` policy)
---------------------------------------------

A **candidate** is a live entry whose headword, lower-cased, is recorded as a non-``lemma``,
non-``derivation`` inflection on a *different* live entry's morphology — the same relation
``export/hf_rows.RowBuilder._inflection_rows`` emits (``plural``, ``past_tense``,
``past_participle``, ``present_participle``, ``third_person_singular``, ``comparative``,
``superlative``). :class:`InflectionIndex` builds that map in one free read pass over the whole
store before the pool starts.

Four free guards refuse a candidate outright, each counted separately, because each is a way
the fold could destroy something:

``skipped_is_lemma``
    The candidate is itself the lemma of another live entry — some other headword in the store
    is one of *its* recorded inflections. "copies" is the plural of *copy* and the lemma that
    records "copied"; folding the middle of a chain would leave the far end resolving to a
    tombstone. 4,943 of the 13,149 production candidates are refused here.
``skipped_pos_mismatch``
    Some live part of speech of the candidate is not a part of speech the form is recorded
    under. A noun plural folds onto a noun lemma; a candidate that is *also* a verb is not
    wholly accounted for by the noun lemma's morphology, and folding it would retire a verb
    nothing points at.
``skipped_lemma_absent``
    The lemma is gone from the store or has no live sense of its own. There is nothing to fold
    onto.
``skipped_form_missing``
    The lemma's morphology does not, after all, carry the form under the matched part of
    speech. True by construction — the index was built from that morphology — so this is an
    assertion that the store did not change under the sweep, and it is counted rather than
    raised.

What survives is asked two questions, free one first (D-8):

1. **Is the form a WordNet lemma in its own right?** Not "does WordNet know the string" —
   NLTK's ``wn.synsets()`` lemmatises internally, so it answers *yes* for "databases" too.
   :func:`~opengloss_generator.wordnet.distinct_from_lemma` asks whether WordNet lists the form
   as a **lemma** of at least one synset that it does not list the base word under. "arms" is a
   lemma of ``weaponry.n.01`` and ``coat_of_arms.n.01`` and "arm" is a lemma of neither, so
   "arms" is kept and costs nothing (``kept_wordnet``). "databases" is a lemma of nothing at all
   and falls through. WordNet is **optional**: with ``nltk`` or its corpus missing the check is
   skipped, every candidate falls through to the verdict, and the count is reported as
   ``wordnet_unavailable`` rather than silently changing what the sweep does.
2. **Do its definitions say something the lemma's do not?** One nano call per surviving
   candidate — the lemma's canonical glosses, the form's canonical glosses, which inflection
   the store claims the form is, and the WordNet evidence as a plain statement of fact — and
   a strict two-value answer: ``pure_inflection`` (fold) or ``distinct_lexeme`` (keep).
   :data:`INFLECTION_FOLD_INSTRUCTIONS` is written around the failure to avoid, and names it:
   "glasses", "arms", "customs", "goods", "manners" are plurals whose entries carry a meaning
   the singular does not have, and retiring one of those hides a meaning a reader was looking
   for. WordNet keeps the first two for free; the last three are exactly why the call is bought
   for the rest.

A **fold** retires every live sense of the form's entry with
``retired sense <sid>: inflection_fold: <lemma_id>`` on the provenance table, demotes each of
their relations to ``see_also`` with :data:`FOLD_RELATION_NOTE` (``phantom_pos``'s convention:
a relation asserted by a sense that should not have had an entry must not keep claiming to be a
hypernym, and a ``see_also`` still says the two terms have something to do with each other),
and leaves the lemma untouched — it already carries the form, which is what makes the
tombstoned string resolve through the ``inflections`` dataset.

``fragments`` (nano, ``HYGIENE`` policy)
----------------------------------------

A candidate is a live **multiword** entry whose first token is an article, determiner,
auxiliary or preposition, or whose last token is a preposition, article or auxiliary
(:data:`LEADING_FUNCTION_WORDS`, :data:`TRAILING_FUNCTION_WORDS`; the reason recorded is the
class that matched, e.g. ``leading_determiner``). 602 entries in the production store qualify,
and most of them must be kept, so two free keeps run before anything is bought:

* a **phrasal verb or an idiom** is skipped whole (``skipped_kind``): "break down" and "out of
  stock" end in a preposition because that is what a phrasal verb and an idiom look like. 256
  of the 602 go out here;
* a phrase **WordNet holds as a lemma** is kept (``kept_wordnet``): "of course", "out of
  stock", "by chance", "in effect", "out loud" and "inside out" are all WordNet lemmas. Free,
  and skipped with the rest of the WordNet checks when the corpus is unavailable. WordNet's
  coverage of multiword expressions is *partial* and this keep is correspondingly partial:
  "on top of", "in front of" and "as well as" are not WordNet lemmas at all, and are kept by
  the verdict below rather than by this check. That asymmetry is why the residue is bought
  rather than retired on the free evidence.

The residue — "a project", "be important", "no wiring", "am i" — is genuinely ambiguous and
buys one nano call, answering ``fragment`` or ``lexical_unit``. A ``fragment`` retires every
live sense with ``retired sense <sid>: fragment: <reason>``, demoting relations the same way.

Unlike ``phantom_pos`` there is no last-live-part-of-speech guard in either step, and that is
the point rather than an omission: ``phantom_pos`` removes one part of speech from a lexeme
that still exists, while both steps here conclude that the *headword* should not have had an
entry. Leaving one sense alive to keep the entry non-empty would leave exactly the defect the
step was run to remove.

``aliases`` (nano, ``HYGIENE`` policy, D-81)
-------------------------------------------

The third step is the only one here that *adds* something rather than retiring it, and it
is in this module for the same reason the other two are: its question is about the
**headword** and it cannot be answered from inside one entry. Tier 6 brings 12,718 names
into a store that already holds the last token of 9,475 of them as an entry of its own —
it knows *Lincoln*, *Washington*, *Einstein* — and the candidate list records the
opportunity as a note (``alias_of candidate: store has 'lincoln'``) without deciding it,
because whether the single-word entry is the *same referent* or a homonym is a per-entry
judgement. "Lincoln" is Abraham Lincoln, and also a city in England and a make of car.

For each candidate the step answers one three-valued question and writes at most one edge
on the **candidate's** first live sense, never on the far side:

* ``alias_of`` — the two name one referent, so the candidate gets a
  :attr:`~opengloss_generator.schema.RelationType.ALIAS_OF` edge toward the single-word
  entry. An alias points one way (D-81): no reciprocal is written, inferred or repaired,
  and :data:`~opengloss_generator.schema.PROTECTED_RELATION_TYPES` keeps every later
  reconcile, hygiene and graph pass from demoting, pruning, capping or re-judging it.
* ``see_also`` — related but not the same referent, which is the ordinary answer for a
  surname that many people share. An **authored** ``see_also``, carrying no demotion note,
  so ``relation-reconcile``'s tombstone step leaves it alone.
* ``none`` — the single-word entry is a different word entirely (*Lincoln* the car for
  *Abraham Lincoln*'s note would be), and nothing is written.

One free keep runs first (D-8): if a live sense of the **single-word** entry already names
the candidate's full headword verbatim in its canonical gloss — WordNet's *Lincoln* gloss
is "16th President of the United States… Abraham Lincoln" — then the store has already
said they are the same referent and the verdict is ``alias_of`` for nothing. An entry
already carrying an edge toward the target is skipped for free as well, which is what
makes a second sweep a no-op even before the marker is read.

Idempotence (D-47)
------------------

Neither step is idempotent by construction, so each carries D-47's sentinel on a zero-cost
provenance record — ``<prefix>:<digest>;attempts=<n>``, bounded at :data:`MAX_ATTEMPTS`
attempts per entry. ``inflection_fold`` keys on the *form's live canonical gloss digests plus
the lemma id*: the question is what these definitions say and which lemma they were compared
against, so rewriting the glosses or re-pointing the candidate at a different lemma earns a
fresh verdict, and nothing else does. ``fragments`` keys on the gloss digests plus the matched
reason, and ``aliases`` on the gloss digests plus the target id — a candidate re-pointed
at a different single-word entry is a different question, and a rewritten gloss is too.
Following ``relation_hygiene`` and ``sense_hygiene``, the digest is taken over the set
**as the answers leave it**: a folded entry has no live gloss left, so it is never revisited at
any price, and a kept one is free on every later sweep.

Concurrency and locking (D-31)
------------------------------

Each step drives its ids through :func:`~opengloss_generator.runner.run_pool` and holds the
candidate entry's lock across read → decide → call → apply → write. The **lemma** entry is read
*without* a lock and before the candidate's is taken, which is this pass's one deliberate
departure from D-31 and the reason it is worth stating: the lemma is never mutated by this
pass, only read for its liveness, its morphology and its glosses, and taking two entry locks at
once is how a pass whose two entries can each be the other's lemma deadlocks. A stale lemma
read can therefore cost a verdict, where ``sense_hygiene``'s equivalent (``_signalled_first``)
could only cost an ordering; that is acceptable because the store is not written by anything
else while a hygiene pass runs, and it is recorded here rather than left to be discovered.
:class:`InflectionIndex` likewise reads every entry once before the pool starts, outside any
lock.
"""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Final, Literal

from pydantic import BaseModel, ConfigDict

from opengloss_generator import wordnet
from opengloss_generator.errors import BudgetExceededError, GenerationError
from opengloss_generator.log import get_logger
from opengloss_generator.prompts import PROMPT_VERSION
from opengloss_generator.runner import run_pool
from opengloss_generator.schema import (
    LexemeKind,
    PartOfSpeech,
    Provenance,
    Relation,
    RelationTarget,
    RelationType,
    StageName,
)
from opengloss_generator.wordnet import Availability
from opengloss_generator.wordnet_import import DEFAULT_CANDIDATES_PATH, read_candidate_rows
from opengloss_generator.workflows.content_hygiene import PROGRESS_EVERY

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Iterable, Sequence
    from pathlib import Path

    from opengloss_generator.schema import Lexeme, POSEntry, Sense
    from opengloss_generator.stages import StageRunner
    from opengloss_generator.store import LexemeStore

__all__ = [
    "ALIASES_INSTRUCTIONS",
    "ALIAS_NOTE",
    "FOLD_RELATION_NOTE",
    "FRAGMENTS_INSTRUCTIONS",
    "INFLECTION_FOLD_INSTRUCTIONS",
    "INFLECTION_RELATIONS",
    "LEADING_FUNCTION_WORDS",
    "MAX_ATTEMPTS",
    "RETIRED_FOLD_NOTE",
    "RETIRED_FRAGMENT_NOTE",
    "SEE_ALSO_NOTE",
    "TRAILING_FUNCTION_WORDS",
    "AliasIndex",
    "FoldPlan",
    "InflectionIndex",
    "LexemeHygieneOutcome",
    "LexemeHygieneStep",
    "StepResult",
    "plan_lexeme_hygiene",
    "run_lexeme_hygiene",
]

_LOG = get_logger(__name__)

#: Provenance ``model`` for every free edit this pass makes. Named the way
#: ``content_hygiene.DETERMINISTIC_MODEL`` and ``sense_hygiene.DETERMINISTIC_MODEL`` are: the
#: retirements are applications of a model *answer* by a rule, and the priced record for the
#: answer itself is added separately.
DETERMINISTIC_MODEL = "rule:lexeme_hygiene"

#: The note an ``inflection_fold`` retirement writes, one per retired sense. Shares
#: ``sense_hygiene.RETIRED_SENSE_NOTE``'s ``retired sense <sid>:`` opening so one grep finds
#: every sense retirement the project makes, and names the lemma the form now resolves to.
RETIRED_FOLD_NOTE = "retired sense {retired}: inflection_fold: {lemma_id}"

#: The note a ``fragments`` retirement writes, one per retired sense, carrying which
#: function-word rule matched.
RETIRED_FRAGMENT_NOTE = "retired sense {retired}: fragment: {reason}"

#: What a relation on a sense either step retires is demoted *to* a ``see_also`` with. Mirrors
#: ``sense_hygiene.PHANTOM_RELATION_NOTE``: nothing is deleted, and a reader of the stored list
#: is told why the edge weakened.
FOLD_RELATION_NOTE = "demoted: inflection_fold"

#: The same for ``fragments``.
FRAGMENT_RELATION_NOTE = "demoted: fragment"

#: The :class:`~opengloss_generator.schema.Morphology` fields that make a form an *inflection*
#: of a lemma. Deliberately the same tuple ``export/hf_rows`` reports as ``relation`` values,
#: minus ``lemma`` (the headword itself) and ``derivation``: a derivation is a different word
#: ("validly", "shoelace") and must never be folded, which is the single most important
#: exclusion in this pass.
INFLECTION_RELATIONS: Final[tuple[str, ...]] = (
    "plural",
    "past_tense",
    "past_participle",
    "present_participle",
    "third_person_singular",
    "comparative",
    "superlative",
)


def _closed_class(words: str) -> frozenset[str]:
    """Return one closed-class word set, written as a space-separated string.

    A string rather than a list literal purely so a forty-word set reads as a set of words in
    the source instead of forty quoted lines; nothing depends on the spelling.

    Args:
        words: The class's members, separated by spaces.

    Returns:
        The set.
    """
    return frozenset(words.split())


#: A first token that makes a multiword headword a fragment candidate, by class. The classes
#: are kept apart rather than unioned so the reason recorded on the retirement says which rule
#: matched, and so a class can be tightened without disturbing the others. Conservative by
#: design: every one of these is a closed-class word that cannot begin a lexical unit on its
#: own, and a phrase that legitimately begins with one ("a lot of", "out of stock") is caught
#: by the kind skip or by WordNet before anything is retired.
LEADING_FUNCTION_WORDS: Final[dict[str, frozenset[str]]] = {
    "article": _closed_class("a an the"),
    "determiner": _closed_class(
        "some any this that these those each every no all both much many several such"
        " another other my your his her its our their"
    ),
    "auxiliary": _closed_class(
        "am is are was were be been being do does did have has had"
        " will would shall should can could may might must"
    ),
    "preposition": _closed_class(
        "of in on at to for with by from into onto upon about over under between through"
        " during against among across behind beyond within without toward towards near off"
        " out up down after before since until per via than around along above below"
    ),
}

#: A last token that makes a multiword headword a fragment candidate, by class. Determiners are
#: deliberately absent: "the more effort" is a fragment because of its *first* token, while a
#: headword ending in a determiner ("all of that") is rare enough in the store that a rule for
#: it would be untested. Prepositions dominate this class and most of them are phrasal verbs,
#: which the kind skip takes out before anything is bought.
TRAILING_FUNCTION_WORDS: Final[dict[str, frozenset[str]]] = {
    "preposition": LEADING_FUNCTION_WORDS["preposition"],
    "article": LEADING_FUNCTION_WORDS["article"],
    "auxiliary": LEADING_FUNCTION_WORDS["auxiliary"],
}

#: Kinds ``fragments`` never asks about. A phrasal verb ends in a preposition because that is
#: what a phrasal verb is, and an idiom is a lexical unit whatever its tokens look like; the
#: classifier that set these kinds already made the judgement this step would be buying again.
FRAGMENT_EXEMPT_KINDS: Final[frozenset[LexemeKind]] = frozenset(
    {LexemeKind.PHRASAL_VERB, LexemeKind.IDIOM}
)

#: How many attempts a step makes on one entry before leaving it alone rather than billing a
#: third answer for it (D-47's bound, per entry). Same value and same reason as
#: ``sense_hygiene.MAX_ATTEMPTS``.
MAX_ATTEMPTS: Final = 2

#: Separates the set digest from the attempt count inside a marker note.
_ATTEMPTS_SEPARATOR: Final = ";attempts="

#: Sentinel prefixes, one per step. Both calls reuse the shared ``HYGIENE`` policy rather than
#: adding a stage of their own, so the stage alone would collide with every other pass that
#: does the same.
_FOLD_PREFIX: Final = "lexeme_hygiene:inflection_fold"
_FRAGMENTS_PREFIX: Final = "lexeme_hygiene:fragments"

#: The fewest whitespace-separated tokens a headword needs before ``fragments`` looks at it.
_MIN_TOKENS: Final = 2


class LexemeHygieneStep:
    """Names of the steps :func:`run_lexeme_hygiene` can select between."""

    INFLECTION_FOLD = "inflection_fold"
    FRAGMENTS = "fragments"
    ALIASES = "aliases"

    #: The order the steps run in. ``inflection_fold`` first: it is the larger population by
    #: two orders of magnitude, and a multiword plural that is also a fragment ("hand signals")
    #: should be recorded as resolving to its lemma rather than as a fragment resolving to
    #: nothing. Neither step can create work for the other — a retired entry is a candidate for
    #: neither — so the order is a preference about which reason gets written, not a
    #: dependency.
    #: ``aliases`` runs last because it is the only step that *adds* an edge, and adding
    #: one to an entry another step is about to tombstone would be work thrown away: a
    #: retired entry has no live sense to hang a relation on, so it is not a candidate here
    #: at all once the first two steps have run.
    ALL: tuple[str, ...] = (INFLECTION_FOLD, FRAGMENTS, ALIASES)


# --------------------------------------------------------------------------------------
# The outcome
# --------------------------------------------------------------------------------------


@dataclass(slots=True)
class StepResult:
    """Counts and cost for one lexeme-hygiene step.

    Attributes:
        name: The step this result belongs to.
        entries_scanned: Entries the step visited.
        entries_changed: Entries it actually wrote a retirement to.
        candidates: Entries that matched the step's free candidate rule — a headword recorded
            as another lexeme's inflection, or a multiword headword bounded by a function word.
        skipped_kind: ``fragments``: candidates exempt by kind (phrasal verb, idiom).
        skipped_is_lemma: ``inflection_fold``: candidates that are themselves the lemma of
            another live entry, so folding would break a resolution chain.
        skipped_pos_mismatch: ``inflection_fold``: candidates with a live part of speech the
            lemma does not record the form under.
        skipped_lemma_absent: ``inflection_fold``: candidates whose lemma is missing from the
            store or has no live sense.
        skipped_form_missing: ``inflection_fold``: candidates whose lemma turned out not to
            carry the form after all. True by construction; a non-zero count means the store
            changed under the sweep.
        kept_wordnet: Candidates kept for free on WordNet evidence — the form is a lemma of a
            synset the base word is not (``inflection_fold``), or the phrase is a WordNet lemma
            (``fragments``).
        kept_verdict: Candidates the model called ``distinct_lexeme`` / ``lexical_unit``.
        retired: Candidates the model called ``pure_inflection`` / ``fragment`` and that were
            therefore tombstoned whole.
        retired_by_reason: ``fragments``: retirements keyed by which function-word rule matched.
        senses_retired: Senses tombstoned across every retirement.
        relations_demoted: Relations on those senses turned into ``see_also`` rather than
            dropped. A consequence of a retirement already counted, so it is deliberately
            absent from :attr:`changed`.
        wordnet_unavailable: Candidates whose WordNet check could not be made because ``nltk``
            or its corpus is missing. Reported rather than silently answered ``False``: every
            one of these went to the model that WordNet might have settled for free.
        skipped_target_missing: ``aliases``: candidates whose single-word target is not in
            the store, or has no live sense to be the same referent as.
        skipped_already_linked: ``aliases``: candidates that already assert an edge toward
            the target, which is what makes a re-run free before the marker is even read.
        alias_free: ``aliases``: candidates the target's own gloss settled for nothing —
            it names the full headword verbatim.
        alias_written: ``aliases``: ``alias_of`` edges written (free keeps included).
        see_also_written: ``aliases``: authored ``see_also`` edges written.
        alias_none: ``aliases``: verdicts of ``none``, which write nothing.
        attempts_exhausted: Candidates skipped because D-47's per-entry attempt bound was
            already reached.
        calls: Model calls made.
        cost_usd: What they cost.
        stopped_reason: ``None`` when the step ran to completion; ``"budget"`` when the run's
            ceiling was reached mid-step; ``"stopped"`` when the caller's stop event was set.
    """

    name: str
    entries_scanned: int = 0
    entries_changed: int = 0
    candidates: int = 0
    skipped_kind: int = 0
    skipped_is_lemma: int = 0
    skipped_pos_mismatch: int = 0
    skipped_lemma_absent: int = 0
    skipped_form_missing: int = 0
    kept_wordnet: int = 0
    kept_verdict: int = 0
    retired: int = 0
    retired_by_reason: dict[str, int] = field(default_factory=dict)
    senses_retired: int = 0
    relations_demoted: int = 0
    wordnet_unavailable: int = 0
    skipped_target_missing: int = 0
    skipped_already_linked: int = 0
    alias_free: int = 0
    alias_written: int = 0
    see_also_written: int = 0
    alias_none: int = 0
    attempts_exhausted: int = 0
    calls: int = 0
    cost_usd: float = 0.0
    stopped_reason: str | None = None

    @property
    def changed(self) -> int:
        """Return how many individual things this step changed.

        ``retired`` is deliberately absent: it counts the same edits ``senses_retired`` does,
        one entry at a time rather than one sense at a time. ``relations_demoted`` is absent
        because every demoted relation sits on a sense already counted. ``aliases`` changes
        nothing by retiring, so its two edge counters stand in.
        """
        return self.senses_retired + self.alias_written + self.see_also_written

    @property
    def cost_per_candidate_usd(self) -> float:
        """Return the mean cost of one candidate, free keeps included."""
        return self.cost_usd / self.candidates if self.candidates else 0.0

    def as_dict(self) -> dict[str, object]:
        """Return a JSON-able view for the run summary and the CLI."""
        return {
            "entries_scanned": self.entries_scanned,
            "entries_changed": self.entries_changed,
            "candidates": self.candidates,
            "skipped_kind": self.skipped_kind,
            "skipped_is_lemma": self.skipped_is_lemma,
            "skipped_pos_mismatch": self.skipped_pos_mismatch,
            "skipped_lemma_absent": self.skipped_lemma_absent,
            "skipped_form_missing": self.skipped_form_missing,
            "kept_wordnet": self.kept_wordnet,
            "kept_verdict": self.kept_verdict,
            "retired": self.retired,
            "retired_by_reason": dict(sorted(self.retired_by_reason.items())),
            "senses_retired": self.senses_retired,
            "relations_demoted": self.relations_demoted,
            "wordnet_unavailable": self.wordnet_unavailable,
            "skipped_target_missing": self.skipped_target_missing,
            "skipped_already_linked": self.skipped_already_linked,
            "alias_free": self.alias_free,
            "alias_written": self.alias_written,
            "see_also_written": self.see_also_written,
            "alias_none": self.alias_none,
            "attempts_exhausted": self.attempts_exhausted,
            "calls": self.calls,
            "cost_usd": round(self.cost_usd, 6),
            "cost_per_candidate_usd": round(self.cost_per_candidate_usd, 8),
            "stopped_reason": self.stopped_reason,
        }


@dataclass(slots=True)
class LexemeHygieneOutcome:
    """What one :func:`run_lexeme_hygiene` sweep did, per step.

    Attributes:
        steps: One :class:`StepResult` per step that ran, keyed by step name.
        entries_changed: How many *distinct* entries were written across every step.
        wordnet: Whether WordNet was consulted at all, and why not when it was not.
    """

    steps: dict[str, StepResult] = field(default_factory=dict)
    entries_changed: int = 0
    wordnet: Availability = field(
        default_factory=lambda: Availability(usable=False, reason="not consulted")
    )

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
        """Return why the run stopped early, or ``None`` if every selected step ran."""
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
            "wordnet": self.wordnet.reason or "available",
            "stopped_reason": self.stopped_reason,
            "steps": {name: result.as_dict() for name, result in self.steps.items()},
        }


# --------------------------------------------------------------------------------------
# Tally and pool driver
# --------------------------------------------------------------------------------------


class _Tally:
    """One step's counters, mutated only while holding an ``asyncio.Lock``.

    Mirrors ``sense_hygiene._Tally`` for the reason ``retrofit._Tally`` gives: single-threaded
    asyncio does make ``counter += 1`` atomic on its own, but these counters are touched by many
    handlers around many awaits and that guarantee is a property of the interpreter rather than
    of this code.

    Args:
        name: The step this tally belongs to.
        changed_ids: The run-level set of entry ids written by any step.
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

    async def entry(self, lexeme_id: str, counts: _Counts) -> None:
        """Fold one visited entry's counts into the step result.

        Args:
            lexeme_id: The entry visited.
            counts: What happened to it.
        """
        async with self._lock:
            result = self._result
            self._visited += 1
            result.entries_scanned += 1
            result.candidates += counts.candidate
            result.skipped_kind += counts.skipped_kind
            result.skipped_is_lemma += counts.skipped_is_lemma
            result.skipped_pos_mismatch += counts.skipped_pos_mismatch
            result.skipped_lemma_absent += counts.skipped_lemma_absent
            result.skipped_form_missing += counts.skipped_form_missing
            result.kept_wordnet += counts.kept_wordnet
            result.kept_verdict += counts.kept_verdict
            result.retired += counts.retired
            result.senses_retired += counts.senses_retired
            result.relations_demoted += counts.relations_demoted
            result.wordnet_unavailable += counts.wordnet_unavailable
            result.skipped_target_missing += counts.skipped_target_missing
            result.skipped_already_linked += counts.skipped_already_linked
            result.alias_free += counts.alias_free
            result.alias_written += counts.alias_written
            result.see_also_written += counts.see_also_written
            result.alias_none += counts.alias_none
            result.attempts_exhausted += counts.attempts_exhausted
            if counts.retired and counts.reason:
                result.retired_by_reason[counts.reason] = (
                    result.retired_by_reason.get(counts.reason, 0) + 1
                )
            if counts.senses_retired or counts.alias_written or counts.see_also_written:
                self._changed.add(lexeme_id)
                self._changed_ids.add(lexeme_id)
                result.entries_changed = len(self._changed)
            if self._visited and self._visited % PROGRESS_EVERY == 0:
                _LOG.info(
                    "lexeme_hygiene_progress",
                    step=result.name,
                    entries_done=self._visited,
                    candidates=result.candidates,
                    retired=result.retired,
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


@dataclass(slots=True)
class _Counts:
    """What one entry's visit did, in one step.

    Attributes:
        candidate: 1 when the entry matched the step's free candidate rule.
        skipped_kind: 1 when it was exempt by kind.
        skipped_is_lemma: 1 when it is itself the lemma of another live entry.
        skipped_pos_mismatch: 1 when a live part of speech is not covered by the lemma.
        skipped_lemma_absent: 1 when the lemma is missing or retired.
        skipped_form_missing: 1 when the lemma does not carry the form after all.
        kept_wordnet: 1 when WordNet settled it for free.
        kept_verdict: 1 when the model said keep.
        retired: 1 when the entry was tombstoned.
        reason: The reason recorded on a ``fragments`` retirement.
        senses_retired: Senses tombstoned on this entry.
        relations_demoted: Relations on those senses demoted.
        wordnet_unavailable: 1 when the WordNet check could not be made.
        skipped_target_missing: 1 when an alias candidate's target is absent or retired.
        skipped_already_linked: 1 when it already asserts an edge toward the target.
        alias_free: 1 when the target's own gloss settled the alias verdict for nothing.
        alias_written: 1 when an ``alias_of`` edge was written.
        see_also_written: 1 when an authored ``see_also`` edge was written.
        alias_none: 1 when the verdict was ``none`` and nothing was written.
        attempts_exhausted: 1 when D-47's bound skipped the entry.
        answered: Whether a model call actually completed, which is what earns a marker.
    """

    candidate: int = 0
    skipped_kind: int = 0
    skipped_is_lemma: int = 0
    skipped_pos_mismatch: int = 0
    skipped_lemma_absent: int = 0
    skipped_form_missing: int = 0
    kept_wordnet: int = 0
    kept_verdict: int = 0
    retired: int = 0
    reason: str | None = None
    senses_retired: int = 0
    relations_demoted: int = 0
    wordnet_unavailable: int = 0
    skipped_target_missing: int = 0
    skipped_already_linked: int = 0
    alias_free: int = 0
    alias_written: int = 0
    see_also_written: int = 0
    alias_none: int = 0
    attempts_exhausted: int = 0
    answered: bool = False


#: How :func:`_fold_plan`'s four free guards map onto :class:`_Counts`. A table rather than
#: ``setattr`` so that adding a guard without a counter for it is a type error rather than a
#: silently dropped count.
_SKIP_COUNTERS: Final[frozenset[str]] = frozenset(
    {
        "skipped_is_lemma",
        "skipped_pos_mismatch",
        "skipped_lemma_absent",
        "skipped_form_missing",
    }
)


def _skip_counts(skip: str) -> _Counts:
    """Return the counts for a candidate one free guard refused.

    Args:
        skip: The guard's counter name, one of :data:`_SKIP_COUNTERS`.

    Returns:
        A :class:`_Counts` marking the entry as a candidate and the guard as having fired.

    Raises:
        ValueError: If ``skip`` names no counter, which can only be a code change that added a
            guard and forgot its counter.
    """
    if skip not in _SKIP_COUNTERS:
        raise ValueError(f"unknown fold guard {skip!r}")
    return _Counts(
        candidate=1,
        skipped_is_lemma=int(skip == "skipped_is_lemma"),
        skipped_pos_mismatch=int(skip == "skipped_pos_mismatch"),
        skipped_lemma_absent=int(skip == "skipped_lemma_absent"),
        skipped_form_missing=int(skip == "skipped_form_missing"),
    )


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
    stopped before the exception is swallowed. Mirrors ``sense_hygiene._drive``.

    Args:
        items: The entry ids to visit.
        handler: The per-item coroutine function.
        tally: The step tally, which learns the stop reason.
        workers: Pool size.
        stop_event: Shared stop event.
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
        note: Free text to preserve on the record — a retirement or a marker.

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


def _one_line(text: str) -> str:
    """Return ``text`` with every run of whitespace collapsed, for a one-item-per-line list."""
    return " ".join(text.split())


def _form_key(headword: str) -> str:
    """Return the comparison key for a surface form.

    Lower-cased with runs of whitespace collapsed — the same normalisation
    ``export/hf_rows`` writes into the ``inflections`` repo's ``form_normalized`` column, so
    the candidate rule here and the dataset a reader resolves through agree by construction.

    Args:
        headword: A headword or a recorded inflected form.

    Returns:
        The key.
    """
    return " ".join(headword.lower().split())


def _live_senses(entry: Lexeme) -> list[tuple[POSEntry, Sense, str]]:
    """Return ``(pos_entry, sense, sense_id)`` for every non-retired sense."""
    return [triple for triple in entry.iter_senses() if not triple[1].retired]


def _live_pos(entry: Lexeme) -> set[PartOfSpeech]:
    """Return every part of speech the entry still carries a live sense under."""
    return {pos_entry.pos for pos_entry, _, _ in _live_senses(entry)}


def _live_glosses(entry: Lexeme) -> list[tuple[PartOfSpeech, str]]:
    """Return ``(pos, canonical gloss)`` for every live sense, in document order."""
    return [(pos_entry.pos, sense.canonical_gloss()) for pos_entry, sense, _ in _live_senses(entry)]


def _demote(relation: Relation, note: str, provenance_id: str) -> None:
    """Demote one relation to ``see_also`` in place, keeping any note it already carried.

    Mirrors ``sense_hygiene._demote``. Nothing is deleted: an edge asserted by a sense that
    should not have had an entry is wrong about the headword, but a ``see_also`` still says the
    two terms have something to do with each other, and the reason is written where a later
    reader will find it.

    Args:
        relation: The relation to demote, mutated in place.
        note: Why, prepended to whatever note the relation already had.
        provenance_id: The entry's record for this edit.
    """
    relation.type = RelationType.SEE_ALSO
    relation.note = note if relation.note is None else f"{note} | {relation.note}"
    relation.provenance_id = provenance_id


def _retire_entry(entry: Lexeme, note_template: str, relation_note: str, **fields: str) -> _Counts:
    """Tombstone every live sense of an entry, demoting the relations they assert.

    Nothing is deleted and nothing is renumbered (D-1). This is ``phantom_pos``'s
    ``_retire_pos_entry`` applied to the whole entry rather than to one part-of-speech block,
    and it deliberately has no last-live-part-of-speech guard: both steps here conclude the
    *headword* should not have had an entry, so leaving one sense alive would leave exactly the
    defect the step was run to remove.

    Args:
        entry: The entry to tombstone, mutated in place.
        note_template: :data:`RETIRED_FOLD_NOTE` or :data:`RETIRED_FRAGMENT_NOTE`.
        relation_note: What a demoted relation records.
        **fields: The template's remaining fields (``lemma_id`` or ``reason``).

    Returns:
        Counts carrying the senses retired and the relations demoted.
    """
    counts = _Counts()
    for _, sense, sense_id in _live_senses(entry):
        provenance_id = entry.add_provenance(
            _rule_provenance(note_template.format(retired=sense_id, **fields))
        )
        for relation in sense.relations:
            if relation.type is RelationType.SEE_ALSO:
                continue
            _demote(relation, f"{relation_note} {sense_id}", provenance_id)
            counts.relations_demoted += 1
        sense.retired = True
        counts.senses_retired += 1
    _LOG.info(
        "lexeme_hygiene_retired",
        headword=entry.headword,
        senses=counts.senses_retired,
        relations_demoted=counts.relations_demoted,
        **fields,
    )
    return counts


# --------------------------------------------------------------------------------------
# The D-47 marker
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Marker:
    """The most recent sentinel one step left on an entry.

    Attributes:
        digest: The set hash the marker was written for — the set as it stood *after* that
            attempt's answer was applied.
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
        The most recent marker, or ``None`` if the step has never visited the entry.
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
    visited it, or what there is to judge has changed since the step last answered — and it has
    not already had :data:`MAX_ATTEMPTS` of them (D-47).

    Args:
        entry: The entry being considered.
        prefix: The step's note prefix.
        refs: Stable identifiers of what is judgeable *now*.

    Returns:
        The 1-based attempt number, or ``None`` when the entry must be skipped.
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
            answer was applied (see the module docstring).
        attempt: The 1-based attempt number.

    Returns:
        ``<prefix>:<digest>;attempts=<n>``.
    """
    return f"{prefix}:{_ref_digest(refs)}{_ATTEMPTS_SEPARATOR}{attempt}"


# --------------------------------------------------------------------------------------
# The cross-entry index
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _LemmaRecord:
    """One lemma that records a given surface form as one of its inflections.

    Attributes:
        lexeme_id: The lemma entry's id.
        headword: Its headword, as stored.
        pos: The part of speech whose morphology carries the form.
        relation: Which :data:`INFLECTION_RELATIONS` field carries it.
    """

    lexeme_id: str
    headword: str
    pos: PartOfSpeech
    relation: str


@dataclass(frozen=True, slots=True)
class InflectionIndex:
    """Which live entries record which surface forms as inflections of themselves.

    Built once per sweep by :meth:`build`, in one free read pass over the **whole** store — not
    over the ids being visited, because a ``--from-list`` naming only the suspected forms would
    otherwise find no lemma for any of them.

    Attributes:
        forms: Normalised surface form to the live entries that record it as an inflection.
            Only :data:`INFLECTION_RELATIONS` are indexed; the ``lemma`` row (the headword
            itself) and every ``derivation`` are deliberately absent — a derivation is a
            different word and must never be folded.
        lemma_ids: The ids of entries that record at least one form which is itself a live
            entry's headword. Folding one of these would break a resolution chain, so they are
            refused.
    """

    forms: dict[str, tuple[_LemmaRecord, ...]]
    lemma_ids: frozenset[str]

    @classmethod
    def build(cls, store: LexemeStore) -> InflectionIndex:
        """Read every entry once and return the index.

        Outside any lock, before the pool starts, and never mutating anything (D-31): the index
        is a read-only view of the store's own morphology, the same view
        ``export/hf_rows._inflection_rows`` writes into the ``inflections`` dataset.

        Args:
            store: The store to index.

        Returns:
            The built index.
        """
        raw: dict[str, list[_LemmaRecord]] = {}
        live_headwords: set[str] = set()
        own_forms: dict[str, set[str]] = {}
        for entry in store.iter_entries():
            live = _live_pos(entry)
            if not live:
                continue
            live_headwords.add(_form_key(entry.headword))
            mine: set[str] = set()
            for pos_entry in entry.pos_entries:
                if pos_entry.pos not in live:
                    continue
                for relation in INFLECTION_RELATIONS:
                    form = getattr(pos_entry.morphology, relation, None)
                    if not form:
                        continue
                    key = _form_key(form)
                    if key == _form_key(entry.headword):
                        continue
                    mine.add(key)
                    raw.setdefault(key, []).append(
                        _LemmaRecord(
                            lexeme_id=entry.lexeme_id,
                            headword=entry.headword,
                            pos=pos_entry.pos,
                            relation=relation,
                        )
                    )
            own_forms[entry.lexeme_id] = mine
        lemma_ids = frozenset(
            lexeme_id for lexeme_id, mine in own_forms.items() if mine & live_headwords
        )
        index = cls(
            forms={key: tuple(records) for key, records in raw.items()},
            lemma_ids=lemma_ids,
        )
        _LOG.info(
            "lexeme_hygiene_index_built",
            forms=len(index.forms),
            live_entries=len(live_headwords),
            lemma_of_live=len(lemma_ids),
        )
        return index

    def records_for(self, entry: Lexeme) -> tuple[_LemmaRecord, ...]:
        """Return the lemmas that record this entry's headword as an inflection of themselves.

        Args:
            entry: The entry being considered.

        Returns:
            The records, self-references removed, in index order.
        """
        return tuple(
            record
            for record in self.forms.get(_form_key(entry.headword), ())
            if record.lexeme_id != entry.lexeme_id
        )


# --------------------------------------------------------------------------------------
# Step 1 — inflection_fold
# --------------------------------------------------------------------------------------
#
# The instructions and the output contract live here, not in prompts.py / contracts.py,
# following ``sense_hygiene``: a self-contained call site is what lets this pass land without
# conflicting with concurrent edits to those two files. Nothing outside this module depends on
# the names below.


#: Instructions for this step's one nano call per surviving candidate. Byte-stable and well
#: over the 1,024 tokens a provider prompt cache needs to match on, which is also what makes
#: two sweeps' numbers comparable. Stated around the *keep* and around the five words that are
#: the failure to avoid, because a model asked only "is this a plural?" will answer yes.
INFLECTION_FOLD_INSTRUCTIONS = """\
You are auditing a dictionary that accidentally gave some inflected forms their own entries. \
You are shown one such form, the base word whose entry already records this form as an \
inflection of it, the definitions filed under each of the two, and what WordNet holds about \
the form. Decide whether the form's entry should stay.

WHY THIS IS ASKED. The dictionary's earlier version stored surface strings rather than lemmas, \
so "databases" got a full entry of its own beside "database", "lunches" beside "lunch", \
"weirdest" beside "weird". Those entries say nothing the base word's entry does not say, and \
the dictionary already records the form on the base word, so a reader who looks up "databases" \
still reaches "database". Retiring them makes the dictionary honest about how many words it \
has. But some plurals and participles are NOT that: they carry a meaning of their own that the \
base word does not have, and retiring one of those loses a meaning a reader was looking for.

THE TWO ANSWERS.

- pure_inflection. The form's entry is the base word's entry, inflected. Its definitions say \
what the base word's definitions say, differing only by number, tense or degree ("more than \
one X", "past tense of X", "the most X"), or by being a slightly reworded version of the same \
meaning. Nothing in it would be lost by sending a reader to the base word. This is the \
ordinary answer.
- distinct_lexeme. The form's entry carries at least one meaning the base word's entry does \
not, and could not be reached from it. Someone who knows only the base word's definitions \
would misunderstand a sentence that uses the form in that meaning.

THE FAILURE TO AVOID. These are all plurals of a word the dictionary also holds, and every one \
of them is a distinct_lexeme:

- "glasses" -- spectacles. "glass" is a material and a drinking vessel; neither gets you to a \
thing you wear on your face.
- "arms" -- weapons, and a coat of arms. "arm" is a limb.
- "customs" -- the government service that inspects imports, and the duty it collects. \
"custom" is a habitual practice.
- "goods" -- merchandise, things for sale. "good" is what is morally right, or a benefit.
- "manners" -- polite social behaviour. "manner" is a way of doing something.

The same happens with participles that have become adjectives in their own right ("staged" \
scenery, a "swinging" style, "wanted" by the police, "gifted" children) and with plurals that \
name a thing the singular does not ("savings", "papers" meaning identity documents, "letters" \
meaning literature). Whenever the form's definitions reach somewhere the base word's do not, \
answer distinct_lexeme.

HOW TO DECIDE. Read the two sets of definitions side by side and ask: is there anything in the \
form's definitions that a reader could not get from the base word's, once they know the form \
is its plural / past tense / participle / comparative? If the answer is no, it is a \
pure_inflection. If any one of the form's definitions names a different thing in the world, \
takes different objects in a sentence, or would be translated by a different word in another \
language, it is a distinct_lexeme.

WHAT IS NOT EVIDENCE. That the form's definitions are longer, better written, more numerous or \
more specific than the base word's is not evidence of a distinct meaning -- the two entries \
were written independently and their wording varies for that reason alone. Neither is a \
definition that simply spells out the inflection ("the plural of X", "having been X-ed"): that \
is the clearest possible pure_inflection. That the form is common, or that the dictionary has \
a lot of material filed under it, is not evidence either.

WHAT WORDNET IS AND IS NOT. The WordNet line states a fact about an outside lexicon and is not \
a recommendation. WordNet listing the form as a lemma of a synset the base word is not in is \
strong support for distinct_lexeme. WordNet not listing the form at all is weak: WordNet is \
incomplete and does not cover every compound or modern term, so a form absent from it may \
still be a distinct lexeme, and the definitions in front of you decide.

BE CONSERVATIVE. A pure_inflection verdict retires every definition of the form from the \
dictionary. When the form's definitions plausibly carry a meaning of their own, or when you \
are simply not sure, answer distinct_lexeme: an extra entry is a smaller problem than a lost \
meaning.

WORKED EXAMPLES.

Form: "databases" (recorded as the plural of "database", noun)
  Base definitions: A structured collection of data held in a computer, organised so that it \
can be searched and updated.
  Form definitions: Structured collections of data stored electronically and organised for \
retrieval. | More than one database.
  WordNet: 'databases' is not a lemma in WordNet.
Answer: pure_inflection. Both definitions are "database", pluralised; the second says so \
outright.

Form: "goods" (recorded as the plural of "good", noun)
  Base definitions: That which is morally right or beneficial. | A benefit or advantage to \
someone.
  Form definitions: Merchandise or possessions; items that are produced, transported and sold.
  WordNet: 'goods' is not a lemma in WordNet.
Answer: distinct_lexeme. Merchandise is nowhere in "good"'s definitions and cannot be got to \
from them; a reader sent to "good" would be misled. WordNet's silence does not settle it -- the \
definitions do.

Form: "weirdest" (recorded as the superlative of "weird", adjective)
  Base definitions: Strange or unusual in a way that is unsettling.
  Form definitions: Most strange or unusual; the highest degree of weirdness.
  WordNet: 'weirdest' is not a lemma in WordNet.
Answer: pure_inflection. The form's definition is the base definition with "most" in front of \
it, which is what a superlative is."""


#: Instructions for ``fragments``' one nano call per ambiguous candidate. Byte-stable for the
#: same reason. Written around what a multi-word *entry* is, rather than around the defect,
#: because a model asked "is this a fragment?" will find fragments.
FRAGMENTS_INSTRUCTIONS = """\
You are auditing a dictionary's multi-word headwords. You are shown one headword, what kind of \
lexical item the dictionary thinks it is, and the definitions filed under it. Decide whether \
this string is a lexical unit that deserves a dictionary entry, or a fragment of a sentence \
that a generator produced by mistake.

WHY THIS IS ASKED. The word list this dictionary was built from was mined from running text, \
and the filter that produced it let through strings that are not words: "is not", "some \
sugar", "produce energy", "a project", "no wiring", "be important". Each of those is a piece \
of a sentence -- a verb and its object, a determiner and a noun, an auxiliary and a complement \
-- and none of them is a thing a dictionary has an entry for. They are shown to you because \
they begin or end with a function word, which is where the mistake shows.

THE TWO ANSWERS.

- lexical_unit. The string is a genuine multi-word expression that a dictionary would list: a \
compound noun, a phrasal verb, an idiom, a fixed phrase, a multi-word preposition or \
conjunction, or a named entity. Its meaning is a property of the whole phrase, not something \
you get by reading its words one after another. "on top of", "in front of", "as well as", "of \
course", "a lot of", "out of stock", "human being", "state of the art", "break down", "point \
of view" are all lexical units.
- fragment. The string is a piece of a sentence with no meaning of its own beyond its words \
read in order. Nothing is lost by deleting it, because a reader who wants it can look up the \
words it is made of. "some sugar" is "some" and "sugar"; "produce energy" is "produce" and \
"energy"; "is not" is "is" and "not".

HOW TO DECIDE. Ask whether the phrase means more than the sum of its words, or is a fixed \
form a speaker has to learn as a unit. Two tests help. First, could you translate it word by \
word into another language and get the right result? If yes, it is probably a fragment; a \
lexical unit usually cannot be translated that way ("of course" is not "of" plus "course"). \
Second, would a learner's dictionary print it as an entry, or would printing it look like \
padding? Also read the definitions: an entry whose definition is just its own words strung \
together ("sugar that is some in quantity", "to make energy") is a fragment, while one whose \
definition names a single idea ("directly above and in contact with", "in a state of being \
sold out") is a unit.

WHAT IS NOT EVIDENCE. That the phrase begins or ends with a function word is not evidence \
either way -- that is only why you are being shown it, and most multi-word prepositions and \
phrasal verbs do exactly that. That it is short, or common, or reads awkwardly is not \
evidence. That the dictionary has written several senses for it is not evidence that it is a \
unit: the generator wrote those senses on the same mistaken premise you are checking.

BE CONSERVATIVE. A fragment verdict retires the entry from the dictionary. When the phrase is \
plausibly a fixed expression, or when you are not sure, answer lexical_unit.

WORKED EXAMPLES.

Headword: "on top of" (compound)
  Definitions: Resting directly above and in contact with something. | In addition to \
something already present.
Answer: lexical_unit. A multi-word preposition with a fixed meaning; the second sense is \
plainly idiomatic and is not reachable from "on", "top" and "of" read in order.

Headword: "some sugar" (compound)
  Definitions: An unspecified quantity of sugar.
Answer: fragment. A determiner and a noun. The definition is the two words restated, and any \
noun in the language would make the same "entry" with "some" in front of it.

Headword: "be important" (function_word)
  Definitions: To have significance or value.
Answer: fragment. A copula and a predicate adjective. The dictionary already has "important"; \
"be important" is a sentence pattern, not a word."""


class _DraftFoldVerdict(BaseModel):
    """Whether one inflected-form entry carries a meaning of its own."""

    model_config = ConfigDict(extra="forbid")

    verdict: Literal["pure_inflection", "distinct_lexeme"]


class _DraftFragmentVerdict(BaseModel):
    """Whether one multi-word headword is a lexical unit."""

    model_config = ConfigDict(extra="forbid")

    verdict: Literal["fragment", "lexical_unit"]


@dataclass(frozen=True, slots=True)
class FoldPlan:
    """What ``inflection_fold`` decided about one candidate before any call was made.

    Attributes:
        lemma: The lemma record the form would fold onto, or ``None`` when a free guard
            refused the candidate or the entry is not a candidate at all.
        lemma_entry: That lemma's entry, read once here and reused for the prompt, so the
            unlocked cross-entry read this pass makes happens exactly once per candidate.
        records: Every lemma record for the form, before the choice was made.
        skip: The counter name of the guard that refused it, or ``None``.
    """

    lemma: _LemmaRecord | None
    lemma_entry: Lexeme | None
    records: tuple[_LemmaRecord, ...]
    skip: str | None


def _choose_lemma(
    entry: Lexeme, records: Sequence[_LemmaRecord], live: set[PartOfSpeech]
) -> _LemmaRecord | None:
    """Return the one lemma whose parts of speech cover every live part of speech of the form.

    Several lemmas can record the same form ("spying" is the present participle of both *spy*
    and *spies*), so the choice has to be deterministic and it has to be a *covering* one: a
    candidate that is live as a noun and a verb is only wholly accounted for by a lemma that
    records the form under both. Ties are broken by lexeme id, which is derived from the
    headword (D-1) and is therefore stable across runs and machines.

    Args:
        entry: The candidate entry.
        records: Every lemma record for its headword.
        live: The parts of speech the candidate still has a live sense under.

    Returns:
        The chosen record, or ``None`` when no single lemma covers the candidate.
    """
    by_lemma: dict[str, list[_LemmaRecord]] = {}
    for record in records:
        by_lemma.setdefault(record.lexeme_id, []).append(record)
    covering = [
        sorted(group, key=lambda record: record.pos.value)[0]
        for _, group in sorted(by_lemma.items())
        if live <= {record.pos for record in group}
    ]
    if not covering:
        _LOG.debug(
            "lexeme_hygiene_pos_mismatch",
            headword=entry.headword,
            live=sorted(pos.value for pos in live),
            offered=sorted({record.pos.value for record in records}),
        )
        return None
    return covering[0]


def _fold_plan(entry: Lexeme, index: InflectionIndex, store: LexemeStore) -> FoldPlan:
    """Return which lemma an entry would fold onto, or which free guard refused it.

    Every guard here is free (D-8) and each is counted separately, because each is a distinct
    way the fold could destroy something; see the module docstring.

    Args:
        entry: The candidate entry. Never mutated.
        index: The cross-entry index.
        store: The store, read for the lemma entry only — without a lock, deliberately (D-31,
            see the module docstring).

    Returns:
        A :class:`FoldPlan`. ``lemma is None and skip is None`` means the entry is not a
        candidate at all.
    """
    records = index.records_for(entry)
    live = _live_pos(entry)
    if not records or not live:
        return FoldPlan(lemma=None, lemma_entry=None, records=records, skip=None)
    refused = FoldPlan(lemma=None, lemma_entry=None, records=records, skip="skipped_is_lemma")
    if entry.lexeme_id in index.lemma_ids:
        return refused
    chosen = _choose_lemma(entry, records, live)
    if chosen is None:
        return replace(refused, skip="skipped_pos_mismatch")
    lemma_entry = store.read(chosen.lexeme_id)
    if lemma_entry is None or not _live_pos(lemma_entry):
        return replace(refused, skip="skipped_lemma_absent")
    if not _lemma_carries_form(lemma_entry, entry.headword, chosen.pos):
        # True by construction: the index was built from this very morphology. Counted rather
        # than raised, because the only way to reach it is a store that changed under the
        # sweep, and a hygiene pass must not die of that.
        _LOG.warning(
            "lexeme_hygiene_form_missing",
            headword=entry.headword,
            lemma=chosen.lexeme_id,
            pos=chosen.pos.value,
        )
        return replace(refused, skip="skipped_form_missing")
    return FoldPlan(lemma=chosen, lemma_entry=lemma_entry, records=records, skip=None)


def _lemma_carries_form(lemma_entry: Lexeme, form: str, pos: PartOfSpeech) -> bool:
    """Return whether the lemma's morphology really records ``form`` under ``pos``.

    The assertion that makes a fold safe: the tombstoned string only stays resolvable because
    D-75's ``inflections`` dataset is built from this field, so a fold whose lemma does not
    carry the form would delete the string from the release rather than redirect it.

    Args:
        lemma_entry: The lemma entry, as read from the store. Never mutated.
        form: The candidate's headword.
        pos: The part of speech the fold was matched under.

    Returns:
        Whether the form is one of that part of speech's recorded inflections.
    """
    key = _form_key(form)
    for pos_entry in lemma_entry.pos_entries:
        if pos_entry.pos is not pos:
            continue
        for relation in INFLECTION_RELATIONS:
            recorded = getattr(pos_entry.morphology, relation, None)
            if recorded and _form_key(recorded) == key:
                return True
    return False


def _fold_refs(entry: Lexeme, lemma_id: str) -> list[str]:
    """Return the marker refs for one fold decision: the lemma, plus each live gloss.

    Keyed on the glosses rather than on the sense ids because the question is what the
    definitions say — the same sense ids can hold rewritten definitions, and that is a
    different question — and on the lemma id because a candidate re-pointed at a different
    lemma is also a different question. A folded entry has no live gloss left, so its refs are
    empty and :func:`_attempt_number` never bills it again.

    Args:
        entry: The candidate entry.
        lemma_id: The lemma it was compared against.

    Returns:
        The refs, or ``[]`` when the entry has no live sense.
    """
    glosses = _live_glosses(entry)
    if not glosses:
        return []
    return [f"lemma:{lemma_id}", *(f"{pos.value}:{_ref_digest([gloss])}" for pos, gloss in glosses)]


def _build_fold_prompt(entry: Lexeme, lemma_entry: Lexeme, plan: FoldPlan) -> str:
    """Return the volatile half of this step's prompt.

    Args:
        entry: The candidate — the inflected form with its own entry.
        lemma_entry: The base word's entry, for its definitions.
        plan: The chosen lemma record, for the relation and part of speech claimed.

    Returns:
        The per-call prompt body.
    """
    chosen = plan.lemma
    if chosen is None:  # pragma: no cover - a prompt is only built for a chosen lemma
        raise ValueError(f"no lemma chosen for {entry.headword!r}")
    relations = sorted(
        {record.relation for record in plan.records if record.lexeme_id == chosen.lexeme_id}
    )
    base = " | ".join(_one_line(gloss) for _, gloss in _live_glosses(lemma_entry)) or "(none)"
    form = " | ".join(_one_line(gloss) for _, gloss in _live_glosses(entry)) or "(none)"
    return (
        f"Form: {entry.headword}\n"
        f"Recorded as: {', '.join(relations)} of {lemma_entry.headword} "
        f"({chosen.pos.value})\n"
        f"Base definitions: {base}\n"
        f"Form definitions: {form}\n"
        f"{wordnet.evidence_line(entry.headword, lemma_entry.headword)}"
    )


async def _decide_fold(
    entry: Lexeme,
    lemma_entry: Lexeme,
    plan: FoldPlan,
    runner: StageRunner,
    tally: _Tally,
) -> tuple[bool, bool]:
    """Ask nano whether an inflected-form entry carries a meaning of its own.

    Args:
        entry: The candidate. Never mutated here.
        lemma_entry: The base word's entry.
        plan: The chosen lemma record.
        runner: The stage runner.
        tally: The step tally, for the call and its cost.

    Returns:
        ``(answered, fold)`` — whether a call completed, and whether the answer was to fold.

    Raises:
        BudgetExceededError: A budget stop is a run-level condition and propagates.
    """
    try:
        stage_result = await runner.run(
            stage=StageName.HYGIENE,
            output_type=_DraftFoldVerdict,
            instructions=INFLECTION_FOLD_INSTRUCTIONS,
            prompt=_build_fold_prompt(entry, lemma_entry, plan),
            prompt_version=PROMPT_VERSION,
        )
    except BudgetExceededError:
        raise
    except GenerationError as exc:
        _LOG.warning("lexeme_hygiene_fold_failed", headword=entry.headword, error=str(exc))
        return False, False

    await tally.call(stage_result.cost_usd)
    entry.add_provenance(stage_result.provenance)
    return True, stage_result.output.verdict == "pure_inflection"


async def _inflection_fold_step(
    store: LexemeStore,
    runner: StageRunner,
    ids: Sequence[str],
    *,
    index: InflectionIndex,
    workers: int,
    stop_event: asyncio.Event | None,
    changed_ids: set[str],
) -> StepResult:
    """Tombstone every entry that is a pure inflection of another entry in the store.

    Args:
        store: The store to clean. The candidate is read, decided and written inside one hold
            of its own lock; the lemma is read outside it (D-31, see the module docstring).
        runner: The stage runner.
        ids: The entry ids to visit.
        index: The cross-entry index, built once by the caller.
        workers: Pool size.
        stop_event: Shared stop event.
        changed_ids: Run-level set of entries written by any step.

    Returns:
        The step's :class:`StepResult`.
    """
    tally = _Tally(LexemeHygieneStep.INFLECTION_FOLD, changed_ids)

    async def decide(entry: Lexeme, plan: FoldPlan) -> _Counts:
        """Return what happens to one candidate: a free keep, a verdict, or a fold."""
        counts = _Counts(candidate=1)
        chosen, lemma_entry = plan.lemma, plan.lemma_entry
        if chosen is None or lemma_entry is None:  # pragma: no cover - guarded by the caller
            return counts
        lemma_id = chosen.lexeme_id
        attempt = _attempt_number(entry, _FOLD_PREFIX, _fold_refs(entry, lemma_id))
        if attempt is None:
            counts.attempts_exhausted = 1
            return counts
        # Free first (D-8): WordNet settles the keeps it can, and the call is bought only for
        # what it cannot. `None` is "not consulted" and must not be read as "not a lemma".
        distinct = wordnet.distinct_from_lemma(entry.headword, lemma_entry.headword)
        if distinct is None:
            counts.wordnet_unavailable = 1
        elif distinct:
            counts.kept_wordnet = 1
        if not counts.kept_wordnet:
            counts.answered, fold = await _decide_fold(entry, lemma_entry, plan, runner, tally)
            if not counts.answered:
                return counts
            if fold:
                retired = _retire_entry(
                    entry, RETIRED_FOLD_NOTE, FOLD_RELATION_NOTE, lemma_id=lemma_id
                )
                counts.retired = 1
                counts.reason = chosen.relation
                counts.senses_retired = retired.senses_retired
                counts.relations_demoted = retired.relations_demoted
            else:
                counts.kept_verdict = 1
        # The digest is over the entry as the answer leaves it, recomputed exactly the way the
        # next sweep will compute it: a kept entry is free next time, and a folded one — which
        # now has no live gloss at all — is never billed again.
        entry.add_provenance(
            _rule_provenance(_marker_note(_FOLD_PREFIX, _fold_refs(entry, lemma_id), attempt))
        )
        return counts

    async def judge(lexeme_id: str) -> None:
        counts = _Counts()
        async with store.locked(lexeme_id):
            entry = store.read(lexeme_id)
            if entry is None:
                return
            plan = _fold_plan(entry, index, store)
            if plan.skip is not None:
                counts = _skip_counts(plan.skip)
            elif plan.lemma is not None:
                counts = await decide(entry, plan)
                if counts.answered or counts.kept_wordnet:
                    store.write(entry)
        await tally.entry(lexeme_id, counts)

    await _drive(ids, judge, tally, workers=workers, stop_event=stop_event)
    return tally.result


# --------------------------------------------------------------------------------------
# Step 2 — fragments
# --------------------------------------------------------------------------------------


def _fragment_reason(headword: str) -> str | None:
    """Return which function-word rule a multiword headword trips, or ``None``.

    Leading classes are checked before trailing ones, and within each the classes are checked
    in :data:`LEADING_FUNCTION_WORDS`' declaration order, so a headword bounded at both ends
    ("on top of") records one stable reason rather than a set.

    Args:
        headword: The headword to test.

    Returns:
        ``leading_<class>`` / ``trailing_<class>``, or ``None`` when it is not a candidate.
    """
    tokens = headword.lower().split()
    if len(tokens) < _MIN_TOKENS:
        return None
    for name, words in LEADING_FUNCTION_WORDS.items():
        if tokens[0] in words:
            return f"leading_{name}"
    for name, words in TRAILING_FUNCTION_WORDS.items():
        if tokens[-1] in words:
            return f"trailing_{name}"
    return None


def _fragment_refs(entry: Lexeme, reason: str) -> list[str]:
    """Return the marker refs for one fragment decision: the reason, plus each live gloss.

    Args:
        entry: The candidate entry.
        reason: The function-word rule that matched.

    Returns:
        The refs, or ``[]`` when the entry has no live sense.
    """
    glosses = _live_glosses(entry)
    if not glosses:
        return []
    return [f"reason:{reason}", *(f"{pos.value}:{_ref_digest([gloss])}" for pos, gloss in glosses)]


def _build_fragment_prompt(entry: Lexeme) -> str:
    """Return the volatile half of this step's prompt.

    Args:
        entry: The candidate multiword entry.

    Returns:
        The per-call prompt body.
    """
    definitions = " | ".join(_one_line(gloss) for _, gloss in _live_glosses(entry)) or "(none)"
    return f"Headword: {entry.headword}\nKind: {entry.kind.value}\nDefinitions: {definitions}"


async def _decide_fragment(entry: Lexeme, runner: StageRunner, tally: _Tally) -> tuple[bool, bool]:
    """Ask nano whether a multiword headword is a lexical unit or a sentence fragment.

    Args:
        entry: The candidate. Never mutated here.
        runner: The stage runner.
        tally: The step tally, for the call and its cost.

    Returns:
        ``(answered, retire)``.

    Raises:
        BudgetExceededError: A budget stop is a run-level condition and propagates.
    """
    try:
        stage_result = await runner.run(
            stage=StageName.HYGIENE,
            output_type=_DraftFragmentVerdict,
            instructions=FRAGMENTS_INSTRUCTIONS,
            prompt=_build_fragment_prompt(entry),
            prompt_version=PROMPT_VERSION,
        )
    except BudgetExceededError:
        raise
    except GenerationError as exc:
        _LOG.warning("lexeme_hygiene_fragment_failed", headword=entry.headword, error=str(exc))
        return False, False

    await tally.call(stage_result.cost_usd)
    entry.add_provenance(stage_result.provenance)
    return True, stage_result.output.verdict == "fragment"


async def _fragments_step(
    store: LexemeStore,
    runner: StageRunner,
    ids: Sequence[str],
    *,
    workers: int,
    stop_event: asyncio.Event | None,
    changed_ids: set[str],
) -> StepResult:
    """Tombstone every multiword headword that is a sentence fragment rather than a lexeme.

    Args:
        store: The store to clean. Each entry is read, decided and written inside one hold of
            its own lock; no other entry is read at all.
        runner: The stage runner.
        ids: The entry ids to visit.
        workers: Pool size.
        stop_event: Shared stop event.
        changed_ids: Run-level set of entries written by any step.

    Returns:
        The step's :class:`StepResult`.
    """
    tally = _Tally(LexemeHygieneStep.FRAGMENTS, changed_ids)

    async def decide(entry: Lexeme, reason: str) -> _Counts:
        """Return what happens to one candidate: a kind skip, a free keep, or a verdict."""
        counts = _Counts(candidate=1)
        if entry.kind in FRAGMENT_EXEMPT_KINDS:
            counts.skipped_kind = 1
            return counts
        attempt = _attempt_number(entry, _FRAGMENTS_PREFIX, _fragment_refs(entry, reason))
        if attempt is None:
            counts.attempts_exhausted = 1
            return counts
        known = wordnet.as_lemma(entry.headword)
        if known is None:
            counts.wordnet_unavailable = 1
        elif known:
            counts.kept_wordnet = 1
        if not counts.kept_wordnet:
            counts.answered, retire = await _decide_fragment(entry, runner, tally)
            if not counts.answered:
                return counts
            if retire:
                retired = _retire_entry(
                    entry, RETIRED_FRAGMENT_NOTE, FRAGMENT_RELATION_NOTE, reason=reason
                )
                counts.retired = 1
                counts.reason = reason
                counts.senses_retired = retired.senses_retired
                counts.relations_demoted = retired.relations_demoted
            else:
                counts.kept_verdict = 1
        entry.add_provenance(
            _rule_provenance(
                _marker_note(_FRAGMENTS_PREFIX, _fragment_refs(entry, reason), attempt)
            )
        )
        return counts

    async def judge(lexeme_id: str) -> None:
        counts = _Counts()
        async with store.locked(lexeme_id):
            entry = store.read(lexeme_id)
            if entry is None:
                return
            reason = _fragment_reason(entry.headword)
            if reason is not None and _live_pos(entry):
                counts = await decide(entry, reason)
                if counts.answered or counts.kept_wordnet:
                    store.write(entry)
        await tally.entry(lexeme_id, counts)

    await _drive(ids, judge, tally, workers=workers, stop_event=stop_event)
    return tally.result


# --------------------------------------------------------------------------------------
# Step 3 — aliases
# --------------------------------------------------------------------------------------
#
# The instructions and the output contract live here for the reason the two steps above
# give: a self-contained call site has no other dependents and never conflicts with
# concurrent edits to prompts.py / contracts.py.


#: The note an ``alias_of`` edge carries, naming what wrote it so a reader of the stored
#: relation can tell an alias established by this pass from one a generator proposed.
ALIAS_NOTE = "alias: lexeme_hygiene"

#: The same for an authored ``see_also``. Deliberately **not** a ``demoted:`` note: this
#: edge was authored at this type, not weakened to it, and ``relation-reconcile``'s
#: tombstone step removes only ``see_also`` edges carrying a demotion note (D-65).
SEE_ALSO_NOTE = "see_also: lexeme_hygiene"

#: Sentinel prefix for this step's D-47 marker.
_ALIASES_PREFIX: Final = "lexeme_hygiene:aliases"

ALIASES_INSTRUCTIONS = """\
You are linking a dictionary's long name entries to the short ones it already has. You \
are shown two headwords from the same dictionary and the definitions filed under each. \
Decide what the relationship between them is.

WHY THIS IS ASKED. The dictionary is adding full names -- "Abraham Lincoln", "New York \
City", "Supreme Court of the United States" -- to a lexicon that already holds single \
words: "lincoln", "city", "court". Sometimes the short entry is the *same thing* under a \
shorter name, and a reader who looks up the short one should be told about the long one. \
Sometimes it is a different thing that merely shares a word, and saying they are the same \
would be false. Sometimes there is no useful connection at all.

THE THREE ANSWERS.

- alias_of. The two headwords name the SAME REFERENT under a different surface form: a \
short name and a full name for one specific person, place, or body ("Abraham Lincoln" / \
"Lincoln", when the short entry is about that president); an initialism and its expansion \
("United Nations" / "UN", "Franklin D. Roosevelt" / "FDR"); a leading-article variant \
("the Netherlands" / "Netherlands"); a transliteration or diacritic variant ("Curacao" / \
"Curaçao"); or a nickname of that same specific entity. Nothing here is a class, a kind, \
or a condition the long thing merely belongs to or resembles -- see THE HEAD-NOUN TRAP.
- see_also. The two are genuinely related but are NOT the same referent: the short entry \
is the common noun or class the long name is built from, a different bearer of the same \
surname, a different thing the name has been confused with, or another connection short \
of identity. "New York City" and "city": a city is what New York City is, not another \
name for it.
- none. There is no useful link. The two share a string and nothing else -- the short \
entry is an unrelated common word, or its definitions have nothing to do with the name.

THE HEAD-NOUN TRAP -- READ THIS BEFORE ANSWERING alias_of. Most of the pairs you will be \
shown are a compound whose LAST WORD is the short headword: "Albers-Schonberg disease" / \
"disease", "Alpine scurvy" / "scurvy", "Golden Horde" / "horde" -- the general pattern is \
"X disease" / "disease". In every one of these, the short entry names the CLASS the long \
thing belongs to (a disease is a class; a horde is a class) or a different thing it has \
been confused with or named after (scurvy is a different, specific disease from Alpine \
scurvy) -- never the long thing itself. A compound is not an alias of its own head noun \
any more than "New York City" is an alias of "city" or "World War II" is an alias of \
"war": that shape is ALWAYS see_also or none, never alias_of, no matter how specific or \
technical the compound sounds. That the short headword is the tail end of the long one is \
why you are being asked and settles nothing on its own -- alias_of requires the short \
entry's OWN definitions to already single out the specific person, place, or thing the \
long headword names, not the kind of thing it is.

HOW TO DECIDE. First check THE HEAD-NOUN TRAP: if the short headword is the class, \
condition, or thing the long compound is built from or resembles, stop and answer \
see_also (a real connection) or none (a coincidental one). Otherwise, read the short \
entry's definitions and ask: do any of them describe THE THING the long headword names? \
If yes -- if one of them is about that specific person, place, or body -- answer \
alias_of. If the short entry describes a different individual with the same name, answer \
see_also. If neither, answer none.

BE CONSERVATIVE ABOUT alias_of. Answering alias_of asserts that two headwords are two \
names for one thing, and that assertion is published and is not reviewed again. A surname \
that many people share is see_also unless the short entry's own definitions single out \
this bearer. A common noun or class is never an alias of a name built from it: "city" is \
not another name for New York City, "court" is not another name for the Supreme Court, \
"disease" is not another name for any disease named after someone.

WHAT IS NOT EVIDENCE. That the short headword is the last word of the long one is why you \
are being shown the pair and settles nothing -- see THE HEAD-NOUN TRAP. That one entry is \
longer or better written than the other is not evidence. That the two are both proper \
nouns is not evidence. That the compound sounds technical, medical, or historical rather \
than ordinary is not evidence either -- "Albers-Schonberg disease" is exactly as much a \
hyponym of "disease" as "New York City" is of "city".

WORKED EXAMPLES.

Long: "Abraham Lincoln"
  Definitions: 16th President of the United States; he issued the Emancipation \
Proclamation and was assassinated in 1865.
Short: "Lincoln"
  Definitions: 16th President of the United States (1809-1865). | A city in eastern \
England, the county town of Lincolnshire.
Answer: alias_of. The short entry's first definition is the same man.

Long: "Franklin D. Roosevelt"
  Definitions: 32nd President of the United States, in office 1933-1945, who led the \
country through the Great Depression and most of World War II.
Short: "FDR"
  Definitions: An initialism for the 32nd U.S. president, known for the New Deal and his \
wartime leadership.
Answer: alias_of. Both name the same president; "FDR" is his initialism, not a class or \
condition he belongs to.

Long: "Albers-Schonberg disease"
  Definitions: A rare inherited disorder causing abnormally dense bone, also called \
osteopetrosis.
Short: "disease"
  Definitions: An abnormal condition impairing the function of an organism. | A specific \
illness with recognizable signs.
Answer: see_also. "Disease" is the class Albers-Schonberg disease belongs to, not another \
name for this specific disorder -- the head-noun trap.

Long: "Alpine scurvy"
  Definitions: An archaic name for pellagra, a niacin-deficiency disease once observed \
among alpine populations and confused with scurvy.
Short: "scurvy"
  Definitions: A disease caused by vitamin C deficiency, marked by weakness and bleeding \
gums.
Answer: see_also. "Alpine scurvy" is a historical name for a DIFFERENT disease that was \
merely confused with scurvy -- the short entry names a distinct condition, not this one.

Long: "World War II"
  Definitions: The global war of 1939 to 1945 between the Allies and the Axis powers.
Short: "ii"
  Definitions: The Roman numeral for two.
Answer: none. The two share a token and nothing else."""


class _DraftAliasVerdict(BaseModel):
    """What the relationship between a long name and a short entry is."""

    model_config = ConfigDict(extra="forbid")

    verdict: Literal["alias_of", "see_also", "none"]


@dataclass(frozen=True, slots=True)
class AliasIndex:
    """Which entries a candidate list proposes as alias links, by lexeme id (D-81).

    Built from the tier-6 candidate TSV rather than from the store, because the pairing is
    a fact the *list* recorded (``alias_of candidate: store has 'lincoln'``) and nothing in
    a store can rediscover it: "Lincoln" is the last token of "Abraham Lincoln" and also of
    nothing else the store holds, and a rule that paired headwords by their last token
    would pair far more than the list ever proposed.

    Attributes:
        targets: ``candidate lexeme id -> the single-word entry's lexeme id``.
    """

    targets: dict[str, str]

    @classmethod
    def from_path(cls, path: Path | None) -> AliasIndex:
        """Read a candidate TSV into the index.

        Args:
            path: The candidate list, or ``None`` for
                :data:`~opengloss_generator.wordnet_import.DEFAULT_CANDIDATES_PATH`. An
                absent file yields an empty index — the tier lists are gitignored (D-75),
                so their absence is an ordinary state of the tree — and an empty index
                makes the step a no-op rather than an error.

        Returns:
            The index.
        """
        rows = read_candidate_rows(path if path is not None else DEFAULT_CANDIDATES_PATH)
        index = cls(
            targets={
                row.lexeme_id: row.alias_target for row in rows if row.alias_target is not None
            }
        )
        _LOG.info("lexeme_hygiene_alias_index_built", rows=len(rows), pairs=len(index.targets))
        return index

    def target_for(self, entry: Lexeme) -> str | None:
        """Return the single-word entry this candidate would link to, or ``None``.

        Args:
            entry: The candidate entry.

        Returns:
            The target lexeme id, or ``None`` when the list names no pairing for it or
            names the entry itself.
        """
        target = self.targets.get(entry.lexeme_id)
        return target if target != entry.lexeme_id else None


def _alias_refs(entry: Lexeme, target_id: str) -> list[str]:
    """Return the marker refs for one alias decision: the target, plus each live gloss.

    Args:
        entry: The candidate entry.
        target_id: The single-word entry it was compared against.

    Returns:
        The refs, or ``[]`` when the entry has no live sense to carry an edge.
    """
    glosses = _live_glosses(entry)
    if not glosses:
        return []
    return [
        f"target:{target_id}",
        *(f"{pos.value}:{_ref_digest([gloss])}" for pos, gloss in glosses),
    ]


def _links_to(entry: Lexeme, target_id: str) -> bool:
    """Return whether any live sense already points at ``target_id``.

    The free idempotence check, ahead of the marker: an entry linked by an earlier sweep,
    by the generator, or by ``import-wordnet``'s own pointers has nothing left to buy.

    Args:
        entry: The candidate entry.
        target_id: The target lexeme id.

    Returns:
        Whether an edge of any type already reaches it.
    """
    return any(
        relation.target.lexeme_id == target_id
        for _, sense, _ in _live_senses(entry)
        for relation in sense.relations
    )


def _target_names_headword(target_entry: Lexeme, headword: str) -> bool:
    """Return whether the short entry's own definitions name the long headword verbatim.

    The free ``alias_of`` (D-8). WordNet's *Lincoln* gloss reads "16th President of the
    United States (1809-1865); Abraham Lincoln", so the store has already said the two are
    one referent and a verdict would be buying an answer it holds. Whole-string,
    case-insensitive: a substring test on the *full* headword cannot fire on a shared
    token, because the headword contains the target's own word by construction.

    Args:
        target_entry: The single-word entry.
        headword: The candidate's full headword.

    Returns:
        Whether any live canonical gloss contains it.
    """
    needle = _one_line(headword).casefold()
    return any(needle in _one_line(gloss).casefold() for _, gloss in _live_glosses(target_entry))


def _build_alias_prompt(entry: Lexeme, target_entry: Lexeme) -> str:
    """Return the volatile half of this step's prompt.

    Args:
        entry: The long-name candidate.
        target_entry: The short single-word entry.

    Returns:
        The per-call prompt body: both headwords and both entries' canonical glosses.
    """
    long_glosses = " | ".join(_one_line(gloss) for _, gloss in _live_glosses(entry)) or "(none)"
    short_glosses = (
        " | ".join(_one_line(gloss) for _, gloss in _live_glosses(target_entry)) or "(none)"
    )
    return (
        f"Long: {entry.headword}\n"
        f"  Definitions: {long_glosses}\n"
        f"Short: {target_entry.headword}\n"
        f"  Definitions: {short_glosses}"
    )


async def _decide_alias(
    entry: Lexeme, target_entry: Lexeme, runner: StageRunner, tally: _Tally
) -> tuple[bool, str]:
    """Ask nano whether a long name and a short entry are one referent.

    Args:
        entry: The candidate. Never mutated here.
        target_entry: The single-word entry.
        runner: The stage runner.
        tally: The step tally, for the call and its cost.

    Returns:
        ``(answered, verdict)`` — whether a call completed, and what it said.

    Raises:
        BudgetExceededError: A budget stop is a run-level condition and propagates.
    """
    try:
        stage_result = await runner.run(
            stage=StageName.HYGIENE,
            output_type=_DraftAliasVerdict,
            instructions=ALIASES_INSTRUCTIONS,
            prompt=_build_alias_prompt(entry, target_entry),
            prompt_version=PROMPT_VERSION,
        )
    except BudgetExceededError:
        raise
    except GenerationError as exc:
        _LOG.warning("lexeme_hygiene_alias_failed", headword=entry.headword, error=str(exc))
        return False, "none"

    await tally.call(stage_result.cost_usd)
    entry.add_provenance(stage_result.provenance)
    return True, stage_result.output.verdict


def _write_alias_edge(entry: Lexeme, target_entry: Lexeme, verdict: str) -> _Counts:
    """Write the one edge a verdict calls for, on the candidate's first live sense.

    **No far side.** An alias points one way (D-81): the single-word entry is not opened,
    not locked and not written, which is also what keeps this step inside the one-lock
    discipline every pass in this module follows.

    Args:
        entry: The candidate, mutated in place.
        target_entry: The single-word entry, read only for its headword.
        verdict: ``alias_of``, ``see_also`` or ``none``.

    Returns:
        Counts carrying whichever edge was written.
    """
    counts = _Counts()
    if verdict == "none":
        counts.alias_none = 1
        return counts
    live = _live_senses(entry)
    if not live:  # pragma: no cover - guarded by the caller
        return counts
    _, sense, sense_id = live[0]
    relation_type = RelationType.ALIAS_OF if verdict == "alias_of" else RelationType.SEE_ALSO
    note = ALIAS_NOTE if verdict == "alias_of" else SEE_ALSO_NOTE
    provenance_id = entry.add_provenance(
        _rule_provenance(f"{verdict}: {sense_id} -> {target_entry.lexeme_id}")
    )
    sense.relations.append(
        Relation(
            type=relation_type,
            target=RelationTarget(term=target_entry.headword),
            note=note,
            provenance_id=provenance_id,
        )
    )
    if relation_type is RelationType.ALIAS_OF:
        counts.alias_written = 1
    else:
        counts.see_also_written = 1
    _LOG.info(
        "lexeme_hygiene_alias_written",
        headword=entry.headword,
        target=target_entry.lexeme_id,
        verdict=verdict,
    )
    return counts


async def _aliases_step(
    store: LexemeStore,
    runner: StageRunner,
    ids: Sequence[str],
    *,
    index: AliasIndex,
    workers: int,
    stop_event: asyncio.Event | None,
    changed_ids: set[str],
) -> StepResult:
    """Link every long-name candidate to the short entry the store already holds.

    Args:
        store: The store to edit. The candidate is read, decided and written inside one
            hold of its own lock; the target is read outside it, never written (D-31, the
            same deliberate departure ``inflection_fold`` makes and for the same reason).
        runner: The stage runner.
        ids: The entry ids to visit.
        index: The candidate-list pairings, built once by the caller.
        workers: Pool size.
        stop_event: Shared stop event.
        changed_ids: Run-level set of entries written by any step.

    Returns:
        The step's :class:`StepResult`.
    """
    tally = _Tally(LexemeHygieneStep.ALIASES, changed_ids)

    async def decide(entry: Lexeme, target_entry: Lexeme) -> _Counts:
        """Return what happens to one candidate: a free skip, a free alias, or a verdict."""
        counts = _Counts(candidate=1)
        target_id = target_entry.lexeme_id
        if _links_to(entry, target_id):
            counts.skipped_already_linked = 1
            return counts
        attempt = _attempt_number(entry, _ALIASES_PREFIX, _alias_refs(entry, target_id))
        if attempt is None:
            counts.attempts_exhausted = 1
            return counts
        if _target_names_headword(target_entry, entry.headword):
            counts.alias_free = 1
            written = _write_alias_edge(entry, target_entry, "alias_of")
        else:
            counts.answered, verdict = await _decide_alias(entry, target_entry, runner, tally)
            if not counts.answered:
                return counts
            written = _write_alias_edge(entry, target_entry, verdict)
        counts.alias_written = written.alias_written
        counts.see_also_written = written.see_also_written
        counts.alias_none = written.alias_none
        entry.add_provenance(
            _rule_provenance(_marker_note(_ALIASES_PREFIX, _alias_refs(entry, target_id), attempt))
        )
        return counts

    async def judge(lexeme_id: str) -> None:
        counts = _Counts()
        target_entry: Lexeme | None = None
        target_id: str | None = None
        async with store.locked(lexeme_id):
            entry = store.read(lexeme_id)
            if entry is None:
                return
            target_id = index.target_for(entry)
            if target_id is None or not _live_senses(entry):
                await tally.entry(lexeme_id, counts)
                return
            # Read outside the candidate's write, but under its lock, exactly as
            # `_fold_plan` reads the lemma: the target is never mutated by this step.
            target_entry = store.read(target_id)
            if target_entry is None or not _live_senses(target_entry):
                counts = _Counts(candidate=1, skipped_target_missing=1)
            else:
                counts = await decide(entry, target_entry)
                if counts.answered or counts.alias_free:
                    store.write(entry)
        await tally.entry(lexeme_id, counts)

    await _drive(ids, judge, tally, workers=workers, stop_event=stop_event)
    return tally.result


# --------------------------------------------------------------------------------------
# The dry-run plan
# --------------------------------------------------------------------------------------


@dataclass(slots=True)
class _PlanCounts:
    """One step's dry-run counts.

    Attributes:
        candidates: Entries matching the step's free candidate rule.
        free_skips: Candidates a free guard or the kind exemption refuses.
        kept_wordnet: Candidates WordNet keeps for free.
        calls_due: Candidates that would buy a verdict.
    """

    candidates: int = 0
    free_skips: int = 0
    kept_wordnet: int = 0
    calls_due: int = 0

    def as_dict(self) -> dict[str, int]:
        """Return a JSON-able view."""
        return {
            "candidates": self.candidates,
            "free_skips": self.free_skips,
            "kept_wordnet": self.kept_wordnet,
            "calls_due": self.calls_due,
        }


def plan_lexeme_hygiene(
    store: LexemeStore,
    ids: Sequence[str],
    *,
    only: set[str] | None = None,
    alias_list: Path | None = None,
) -> dict[str, object]:
    """Return what a sweep would do, without a single model call.

    Every free filter runs for real — the index, the four guards, the kind exemption and both
    WordNet checks — so ``calls_due`` is the number of calls the sweep would actually buy
    rather than the number of candidates it would find. The caller prices them.

    Args:
        store: The store to inspect. Never written.
        ids: The entry ids the sweep would visit.
        only: Step names to plan for; defaults to all of them.
        alias_list: The candidate TSV ``aliases`` would read, or ``None`` for the default
            path. The pairings, the target lookups and the free gloss keep all run for
            real here, so ``calls_due`` is what the sweep would buy.

    Returns:
        A JSON-able plan, keyed by step, plus the WordNet availability the plan assumed.
    """
    selected = set(only) if only is not None else set(LexemeHygieneStep.ALL)
    plans = {name: _PlanCounts() for name in LexemeHygieneStep.ALL if name in selected}
    index = (
        InflectionIndex.build(store)
        if LexemeHygieneStep.INFLECTION_FOLD in selected
        else InflectionIndex(forms={}, lemma_ids=frozenset())
    )
    aliases = (
        AliasIndex.from_path(alias_list)
        if LexemeHygieneStep.ALIASES in selected
        else AliasIndex(targets={})
    )
    scanned = 0
    for lexeme_id in ids:
        entry = store.read(lexeme_id)
        if entry is None:
            continue
        scanned += 1
        fold = plans.get(LexemeHygieneStep.INFLECTION_FOLD)
        if fold is not None:
            _plan_fold(entry, index, store, fold)
        fragments = plans.get(LexemeHygieneStep.FRAGMENTS)
        if fragments is not None:
            _plan_fragment(entry, fragments)
        alias = plans.get(LexemeHygieneStep.ALIASES)
        if alias is not None:
            _plan_alias(entry, aliases, store, alias)
    return {
        "entries_scanned": scanned,
        "wordnet": wordnet.availability().reason or "available",
        "steps": {name: plan.as_dict() for name, plan in plans.items()},
        "estimated_calls": sum(plan.calls_due for plan in plans.values()),
    }


def _plan_fold(
    entry: Lexeme, index: InflectionIndex, store: LexemeStore, plan: _PlanCounts
) -> None:
    """Fold one entry's ``inflection_fold`` outlook into the dry-run plan."""
    decision = _fold_plan(entry, index, store)
    if decision.skip is not None:
        plan.candidates += 1
        plan.free_skips += 1
        return
    if decision.lemma is None:
        return
    plan.candidates += 1
    if _attempt_number(entry, _FOLD_PREFIX, _fold_refs(entry, decision.lemma.lexeme_id)) is None:
        plan.free_skips += 1
        return
    if wordnet.distinct_from_lemma(entry.headword, decision.lemma.headword):
        plan.kept_wordnet += 1
        return
    plan.calls_due += 1


def _plan_alias(entry: Lexeme, index: AliasIndex, store: LexemeStore, plan: _PlanCounts) -> None:
    """Fold one entry's ``aliases`` outlook into the dry-run plan.

    Every free filter the step applies runs here too — the pairing, the already-linked
    check, the marker and the target's own gloss — so a candidate counted in ``calls_due``
    is one the sweep would actually pay for. ``kept_wordnet`` carries the free
    ``alias_of`` keeps, since this step consults no WordNet and the field is the plan's
    "settled for nothing" column.
    """
    target_id = index.target_for(entry)
    if target_id is None or not _live_senses(entry):
        return
    plan.candidates += 1
    target_entry = store.read(target_id)
    if target_entry is None or not _live_senses(target_entry):
        plan.free_skips += 1
        return
    if _links_to(entry, target_id):
        plan.free_skips += 1
        return
    if _attempt_number(entry, _ALIASES_PREFIX, _alias_refs(entry, target_id)) is None:
        plan.free_skips += 1
        return
    if _target_names_headword(target_entry, entry.headword):
        plan.kept_wordnet += 1
        return
    plan.calls_due += 1


def _plan_fragment(entry: Lexeme, plan: _PlanCounts) -> None:
    """Fold one entry's ``fragments`` outlook into the dry-run plan."""
    reason = _fragment_reason(entry.headword)
    if reason is None or not _live_pos(entry):
        return
    plan.candidates += 1
    if entry.kind in FRAGMENT_EXEMPT_KINDS:
        plan.free_skips += 1
        return
    if _attempt_number(entry, _FRAGMENTS_PREFIX, _fragment_refs(entry, reason)) is None:
        plan.free_skips += 1
        return
    if wordnet.as_lemma(entry.headword):
        plan.kept_wordnet += 1
        return
    plan.calls_due += 1


# --------------------------------------------------------------------------------------
# The entry point
# --------------------------------------------------------------------------------------


async def run_lexeme_hygiene(
    store: LexemeStore,
    runner: StageRunner,
    *,
    workers: int,
    stop_event: asyncio.Event | None = None,
    only: set[str] | None = None,
    lexeme_ids: Sequence[str] | None = None,
    alias_list: Path | None = None,
) -> LexemeHygieneOutcome:
    """Tombstone entries that are not lexemes, and link long names to short entries.

    Three steps, described in full in the module docstring. ``inflection_fold`` retires an entry
    whose headword the store already records as a plural, past tense, participle or comparative
    of another live lexeme — unless WordNet lists it as a lemma of its own, unless a nano
    verdict says its definitions carry a meaning the base word's do not, and never when it is
    itself the lemma of another entry, when a live part of speech is unaccounted for, or when
    the lemma is absent. ``fragments`` retires a multiword headword bounded by a function word
    — unless it is a phrasal verb or an idiom, unless WordNet holds the phrase as a lemma, and
    unless a nano verdict calls it a lexical unit. Nothing is deleted, no sense is renumbered
    (D-1), and every retired sense's relations are demoted to ``see_also`` rather than dropped.
    ``aliases`` (D-81) links a long-name entry the candidate list pairs with a single-word
    entry the store already holds — ``alias_of`` when they name one referent, an authored
    ``see_also`` when they are merely related, nothing when they are not — one nano verdict
    each, free when the short entry's own gloss already names the long headword, and never a
    far side.

    Args:
        store: The store to repair.
        runner: The stage runner. Each step makes at most one nano call per surviving candidate
            on the ``HYGIENE`` policy; an entry that is not a candidate costs nothing.
        workers: Pool size for every step.
        stop_event: Shared stop event. A budget stop sets it; a caller may also set it from
            outside.
        only: Step names to run; defaults to all of :attr:`LexemeHygieneStep.ALL`, in that
            order.
        lexeme_ids: Ids to visit; defaults to every id in the store, sorted. The
            :class:`InflectionIndex` is always built over the *whole* store whatever this says,
            because a list naming only the suspected forms would find no lemma for any of them.
        alias_list: The candidate TSV ``aliases`` reads its pairings from, or ``None`` for
            :data:`~opengloss_generator.wordnet_import.DEFAULT_CANDIDATES_PATH`. An absent
            file makes the step a no-op rather than an error (D-75: the tier lists are
            gitignored).

    Returns:
        A :class:`LexemeHygieneOutcome` carrying counts and cost per step. If a step stopped
        early its ``stopped_reason`` says why and the remaining steps are skipped; the outcome
        is still returned rather than raised.

    Raises:
        ValueError: If ``only`` names a step that does not exist.
    """
    selected = set(only) if only is not None else set(LexemeHygieneStep.ALL)
    unknown = sorted(selected - set(LexemeHygieneStep.ALL))
    if unknown:
        raise ValueError(f"unknown lexeme hygiene step(s): {unknown}")

    ids = list(lexeme_ids) if lexeme_ids is not None else sorted(store.iter_ids())
    availability = wordnet.availability()
    if not availability.usable:
        _LOG.warning("lexeme_hygiene_wordnet_skipped", reason=availability.reason)
    outcome = LexemeHygieneOutcome(wordnet=availability)
    changed_ids: set[str] = set()

    for name in LexemeHygieneStep.ALL:
        if name not in selected:
            continue
        if name == LexemeHygieneStep.INFLECTION_FOLD:
            result = await _inflection_fold_step(
                store,
                runner,
                ids,
                index=InflectionIndex.build(store),
                workers=workers,
                stop_event=stop_event,
                changed_ids=changed_ids,
            )
        elif name == LexemeHygieneStep.FRAGMENTS:
            result = await _fragments_step(
                store,
                runner,
                ids,
                workers=workers,
                stop_event=stop_event,
                changed_ids=changed_ids,
            )
        else:
            result = await _aliases_step(
                store,
                runner,
                ids,
                index=AliasIndex.from_path(alias_list),
                workers=workers,
                stop_event=stop_event,
                changed_ids=changed_ids,
            )
        outcome.steps[name] = result
        if result.stopped_reason is not None:
            _LOG.warning(
                "lexeme_hygiene_step_stopped",
                step=name,
                reason=result.stopped_reason,
                entries_scanned=result.entries_scanned,
                skipped=[
                    s for s in LexemeHygieneStep.ALL if s in selected and s not in outcome.steps
                ],
            )
            break

    outcome.entries_changed = len(changed_ids)
    _LOG.info("lexeme_hygiene_complete", entries=len(ids), workers=workers, **outcome.as_dict())
    return outcome
