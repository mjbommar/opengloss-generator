"""Workflow 11 — verified, sense-disambiguated example sentences, in volume (D-53).

Every other prose the pipeline writes is a definition or a passage *about* a word. This
workflow writes the one kind of text a small encoder actually trains on: a sentence that
*uses* the word in one particular meaning, tagged with which meaning that is. That makes
it the highest-value output per token in the project and, not coincidentally, the most
checkable — a definition can only be judged by another model, but a sentence can be
checked for whether it contains the headword, how long it is, how hard it reads, how many
of its words a six-year-old knows, whether it is a definition wearing a sentence's
clothes, and whether it repeats one that already exists, all for free.

The judge's standing verdict is what makes this a generation pass rather than another
repair pass (``docs/QA-DIARY.md``, iterations 1 and 4): ``examples_natural`` failed on
29.6% then 33.3% of judged senses and ``examples_fit_sense`` on 34.1% then 31.8%, and
neither moved under the repair passes aimed at them — "rewriting by pattern moves the
pattern". Iteration 4's own conclusion was that example naturalness "belongs to a future
per-sense regeneration, not a repair pass". This is that regeneration, run at volume: N
fresh sentences per sense, none of them kept unless it passes every check.

One call per *entry*
--------------------

Not per sense. The prompt lists every live sense as ``[sense_ref, pos, canonical gloss,
one existing example]`` and asks for ``config.examples.per_sense`` sentences for each of
them, so the model writes sense 2's sentences knowing what senses 1 and 3 mean. That is
the only way "this sentence must fit ONLY the sense it is filed under" can be *asked* for
at generation time rather than measured afterwards, and it is what makes the
input:output ratio the lowest of any stage here: one ~700-token prompt (against a ~2K-token
cached instruction prefix) buys eight sentences per sense.

Each sentence carries a target drawn round-robin from
:meth:`~opengloss_generator.config.ExamplesConfig.targets` — each configured reading level
at ``plain``, then ``neutral`` at each configured register — so a sense's eight sentences
span audiences instead of repeating one voice. The axes are deliberately *not* crossed the
way :class:`~opengloss_generator.workflows.enrich.RenditionRequest` crosses them: a
grade_1 technical example sentence is not a thing.

Acceptance: deterministic, per sentence, and no retries
-------------------------------------------------------

Every returned sentence is markdown-stripped and then checked, in this order, by
:func:`_judge`:

* it is one non-empty line of prose, opening on a capital letter and closing on ``.``,
  ``?`` or ``!`` — the fragment class ``content_hygiene`` had to repair on 498 stored
  examples, refused here at the door;
* its word count is inside ``[min_words, max_words]``, tightened at ``grade_1`` (10) and
  ``grade_5`` (16) to the numbers ``RENDITIONS_INSTRUCTIONS`` already states;
* :func:`~opengloss_generator.spans.find_span` places the headword or one of its forms in
  it (the sense's own :class:`~opengloss_generator.schema.Morphology`, falling back to
  :func:`~opengloss_generator.spans.generate_forms`) — D-45's defect, refused rather than
  flagged;
* it is not a definition in disguise. :func:`~opengloss_generator.hygiene.is_headword_initial`
  is deliberately *not* used: an example sentence may perfectly well begin with its
  headword ("Bank staff went on strike."), and D-39's rule is about glosses. What is
  refused is the gloss *shape*: an optional article, the headword, then ``is``/``are``/
  ``means``/``refers`` (:data:`_GLOSS_SHAPE_TEMPLATE`);
* its Flesch-Kincaid grade is within its level's upper bound plus
  ``config.readability.tolerance``. Only the upper bound: a short, natural sentence
  legitimately measures below a college band, and rejecting it for being easy would select
  for padding;
* at ``grade_1`` and ``grade_5``, its unfamiliar-word share is within
  :func:`~opengloss_generator.vocabulary.vocabulary_band` plus
  ``config.readability.vocabulary_tolerance`` (D-51);
* it is not a near-duplicate, on normalised text, of an example the sense already holds or
  of one accepted earlier in this same call;
* its first three words differ from those of every sentence already accepted in this call.
  A set of eight sentences that all open "The bank was" is worth less than one that does
  not, and this is the cheapest possible proxy for that.

A rejected sentence is **counted by reason and dropped**. There is no retry loop, and that
is a deliberate reversal of ``enrich.py``'s single-retry discipline: there, one call
produces one rendition per target and losing it loses the target, so a retry is worth its
price; here one call produces dozens of interchangeable sentences and the next entry's
call buys more of them more cheaply than a retry buys back one. The reason counters are
the feedback loop instead — they are what a later prompt change would be aimed at.

The sense-fit check
-------------------

"Fits only this sense" is the one property none of the checks above can see, and it is the
judge's largest measured example defect. So for an entry with two or more live senses,
``config.examples.sense_check`` (on by default) buys a **second, cheap call** on the
``HYGIENE`` policy — nano, the same model and shape ``sense_hygiene``'s ``example_fit``
step uses to answer this very question about stored examples — listing the accepted
sentences as ``[ref, text]`` and the senses as ``[sense_ref, gloss]``, and asking which
sense each sentence illustrates. The sentences are listed **without** saying which sense
each was written for, so the answer is a judgement rather than an agreement.

A sentence whose answer is not the sense it was written for is **dropped**, not moved. It
was written to illustrate a sense it does not fit; the sense it does fit already has its
own eight sentences written for it deliberately, and refiling would put a weaker sentence
in a place that does not need one. A ``null`` answer — fits none of them — is dropped for
the same reason.

Idempotence and cost
--------------------

A zero-cost sentinel on the generation call's own provenance record,
``examples:<digest of live sense ids>;n=<per_sense>``. A rerun over an unchanged entry
costs $0; an entry that gains or retires a sense, or a run configured for a different
``per_sense``, earns exactly one more call. Unlike D-47's markers this one carries no
attempt counter: it is not repairing a defect that may survive an attempt, it is filling a
set, and the set is described by the digest.

Concurrency and locking (D-31) mirror every other sweep in the project: the unit of work
is one entry, and the handler holding that entry's lock reads it, makes its one or two
calls, applies what survived, and writes it back inside the same lock hold. Counters go
through :class:`_Tally`, mutated only while holding an ``asyncio.Lock``, for the reason
``retrofit._Tally`` gives.

The generation contract and instructions live in ``contracts.py`` and ``prompts.py``,
where a stage's public prompt surface belongs and where the ~2K-token cached prefix is
visible beside the others. The *sense-fit* call's contract and instructions are
module-private here, following ``sense_hygiene``: it reuses the shared ``HYGIENE`` policy
rather than a stage of its own, it is an internal detail of this workflow's acceptance
rules rather than a stage anyone else calls, and keeping it here means this work does not
conflict with the several modules being edited concurrently on this branch.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Annotated

from pydantic import BaseModel, ConfigDict, Field

from opengloss_generator import prompts, spans
from opengloss_generator.contracts import DraftExampleBatch
from opengloss_generator.errors import BudgetExceededError, GenerationError
from opengloss_generator.log import get_logger
from opengloss_generator.readability import (
    flesch_kincaid_grade,
    grade_band,
    strip_markdown,
    word_count,
)
from opengloss_generator.runner import run_pool
from opengloss_generator.schema import (
    Assessment,
    Example,
    ReadingLevel,
    Register,
    Rendition,
    StageName,
)
from opengloss_generator.vocabulary import hard_word_share, vocabulary_band

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from opengloss_generator.config import ExamplesConfig, ReadabilityConfig
    from opengloss_generator.contracts import DraftSenseExample
    from opengloss_generator.schema import Lexeme, POSEntry, Provenance, Sense
    from opengloss_generator.stages import StageRunner
    from opengloss_generator.store import LexemeStore

__all__ = [
    "ExamplesOutcome",
    "ExamplesPlan",
    "RejectReason",
    "plan_examples",
    "run_examples",
]

_LOG = get_logger(__name__)

#: Prefix of this workflow's idempotence sentinel. The full note is
#: ``examples:<digest>;n=<per_sense>``: the digest describes *which* senses were written
#: for, and the count describes *how many* sentences each was asked for, so changing
#: either earns exactly one more call and changing neither costs nothing.
MARKER_PREFIX = "examples"

#: Separates the live-sense digest from the per-sense count inside a marker note.
_COUNT_SEPARATOR = ";n="

#: Word ceilings tighter than ``ExamplesConfig.max_words`` at the two levels that have
#: one. The numbers are the ones :data:`~opengloss_generator.prompts.RENDITIONS_INSTRUCTIONS`
#: already states for those levels, so the prompt and the acceptance rule cannot disagree.
_LEVEL_WORD_CAPS: dict[ReadingLevel, int] = {
    ReadingLevel.GRADE_1: 10,
    ReadingLevel.GRADE_5: 16,
}

#: The gloss *shape*, which an example sentence must not have: an optional article, the
#: headword, then a defining verb. This is not
#: :func:`~opengloss_generator.hygiene.is_headword_initial`, and deliberately so — that
#: predicate answers a question about definitions (D-39), and an example sentence is
#: entirely free to begin with its own headword ("Bank staff walked out at noon."). What
#: is refused here is only the sentence that is a definition in disguise.
_GLOSS_SHAPE_TEMPLATE = r"^\s*(?:the|a|an)?\s*{headword}s?\s+(?:is|are|means|refers)\b"

#: Terminal punctuation an accepted sentence must end on.
_TERMINALS = (".", "?", "!")

#: How many leading words must differ between two sentences accepted from one call.
_OPENING_WORDS = 3

#: Everything but letters, digits and single spaces, removed before two sentences are
#: compared for near-duplication.
_NORMALISE_RE = re.compile(r"[^a-z0-9 ]+")
_WHITESPACE_RE = re.compile(r"\s+")

#: Shown in place of the existing example of a sense that has none.
_NO_EXAMPLE = "(none)"

#: The fewest live senses that make the sense-fit question worth asking. With one sense
#: there is nothing for a sentence to be confused with, and the call would buy a verdict
#: whose only possible answer is "sense 1".
_MIN_SENSES_FOR_CHECK = 2

#: How many progress lines a long sweep emits, matching every other sweep in the project.
PROGRESS_EVERY = 500


class RejectReason(StrEnum):
    """Why one generated sentence was not kept.

    Every rejection is counted under one of these, and the counts are this workflow's
    feedback loop: there is no retry, so a reason that dominates one sweep is the thing
    the next change to :data:`~opengloss_generator.prompts.EXAMPLES_INSTRUCTIONS` has to
    address.
    """

    #: The answer named a sense, level or register that was not asked for, or repeated a
    #: (sense, target) pair already filled.
    UNWANTED = "unwanted"
    #: Empty once markdown was stripped from it.
    EMPTY = "empty"
    #: Not one plain sentence: no opening capital, or no terminal punctuation.
    NOT_A_SENTENCE = "not_a_sentence"
    #: Fewer words than ``ExamplesConfig.min_words``.
    TOO_SHORT = "too_short"
    #: More words than its level allows.
    TOO_LONG = "too_long"
    #: :func:`~opengloss_generator.spans.find_span` could not place the headword (D-45).
    HEADWORD_ABSENT = "headword_absent"
    #: A definition in disguise (see :data:`_GLOSS_SHAPE_TEMPLATE`).
    GLOSS_SHAPED = "gloss_shaped"
    #: Measured Flesch-Kincaid grade above its level's band plus tolerance.
    READABILITY = "readability"
    #: Unfamiliar-word share above its level's band plus tolerance (D-51).
    HARD_VOCABULARY = "hard_vocabulary"
    #: Normalises to a sentence the sense already holds, or to one accepted earlier in
    #: this same call.
    DUPLICATE = "duplicate"
    #: Opens on the same three words as a sentence already accepted in this call.
    REPEATED_OPENING = "repeated_opening"


@dataclass(frozen=True, slots=True)
class ExamplesPlan:
    """What one entry would cost this workflow, computed without a model call.

    Attributes:
        senses: How many live senses would be written for; ``0`` when the entry is not
            due a call at all (no live senses, or the marker already matches).
        sentences: How many sentences would be asked for — ``senses * per_sense``.
        sense_check: Whether the second, cheap sense-fit call would also be made.
    """

    senses: int = 0
    sentences: int = 0
    sense_check: bool = False

    @property
    def due(self) -> bool:
        """Return whether this entry would cost anything at all."""
        return self.senses > 0


@dataclass(slots=True)
class ExamplesOutcome:
    """What one :func:`run_examples` sweep did across the store."""

    entries_scanned: int = 0
    entries_changed: int = 0
    calls: int = 0
    sentences_generated: int = 0
    accepted: int = 0
    rejected_by_reason: dict[str, int] = field(default_factory=dict)
    refiled_dropped: int = 0
    cost_usd: float = 0.0
    stopped_reason: str | None = None

    @property
    def rejected(self) -> int:
        """Return how many generated sentences were rejected, for any reason."""
        return sum(self.rejected_by_reason.values())

    def as_dict(self) -> dict[str, object]:
        """Return a JSON-serialisable view for the CLI run summary."""
        return {
            "entries_scanned": self.entries_scanned,
            "entries_changed": self.entries_changed,
            "calls": self.calls,
            "sentences_generated": self.sentences_generated,
            "accepted": self.accepted,
            "rejected": self.rejected,
            "rejected_by_reason": dict(sorted(self.rejected_by_reason.items())),
            "refiled_dropped": self.refiled_dropped,
            "cost_usd": round(self.cost_usd, 6),
            "stopped_reason": self.stopped_reason,
        }


# --------------------------------------------------------------------------------------
# The sense-fit contract and instructions (module-private; see the module docstring)
# --------------------------------------------------------------------------------------


class _DraftSentenceFit(BaseModel):
    """Which listed sense one freshly written sentence actually illustrates."""

    model_config = ConfigDict(
        extra="forbid", validate_by_name=True, validate_by_alias=True, serialize_by_alias=True
    )

    sentence_ref: Annotated[int, Field(ge=1)]
    best_sense_ref: Annotated[
        int | None, Field(default=None, ge=1, description="Null when it fits none of them.")
    ] = None


class _DraftSentenceFits(BaseModel):
    """The sense each of one entry's freshly written sentences illustrates."""

    model_config = ConfigDict(
        extra="forbid", validate_by_name=True, validate_by_alias=True, serialize_by_alias=True
    )

    fits: Annotated[list[_DraftSentenceFit], Field(min_length=1)]


#: Instructions for the sense-fit call. Byte-stable and past the 1,024-token cache floor,
#: like every other stage's, so a sweep's second call is served from the provider's cache
#: after the first entry. Deliberately close in wording to ``sense_hygiene``'s
#: ``EXAMPLE_FIT_INSTRUCTIONS``: it is the same question, asked about a sentence that was
#: written a minute ago rather than one that has been on disk since v1.3.
SENSE_FIT_INSTRUCTIONS = """\
You are checking newly written example sentences against the sense inventory of one \
dictionary headword. You are given every sense of that headword, numbered, and a list of \
sentences, also numbered. For each sentence, say which one of the listed senses it \
actually illustrates, or that it illustrates none of them.

WHAT AN EXAMPLE IS FOR. An example sentence exists to show a reader the word being used \
in one particular meaning. It illustrates a sense when a reader who has just read that \
sense's definition would recognise the sentence as an instance of it, and would come away \
with the definition confirmed rather than muddled. The question is not which sense the \
sentence is loosely about, and not which sense is the most common one: it is which \
definition the sentence actually uses.

HOW TO DECIDE. Read the sentence. Work out what the headword means in it — what the \
speaker would have meant, what a listener would have understood. Then match that meaning \
against the definitions in front of you and pick the one it belongs to. Do not be swayed \
by the topic of the sentence, by the vocabulary around the headword, or by wording the \
sentence happens to share with a definition. A sentence about a hospital does not \
illustrate a medical sense unless the headword itself is used medically in it.

THE PART OF SPEECH IS PART OF THE ANSWER. Each sense is labelled with its part of speech. \
A sentence that uses the headword as a noun cannot illustrate a verb sense, and a \
sentence that uses it as a verb cannot illustrate a noun sense, however well the subject \
matter matches. When the only sense a sentence could otherwise fit is filed under the \
wrong part of speech, and no sense under the right part of speech fits it, the answer is \
that it fits none of them.

EXACTLY ONE ANSWER, OR NONE. Choose the single sense the sentence illustrates best. If \
two senses both fit the sentence equally well, that is a property of the sentence and not \
of the inventory: pick the one it fits more precisely. Answer null only when no listed \
sense is used in the sentence at all — because the headword carries a meaning nobody \
listed, or because the sentence does not really use the headword as a word of that part \
of speech.

BE LITERAL, NOT GENEROUS. These sentences were each written to illustrate one particular \
sense, and the point of asking you is to catch the ones that came out illustrating a \
different one. Do not reason about which sense the writer probably intended; read only \
what is on the page. A sentence that would sit equally comfortably under two senses fits \
the one its own details point at, and if its details point at nothing, it fits none.

FIXED PHRASES AND IDIOMS. When the headword appears inside a set phrase whose meaning is \
not built out of the meanings of its words -- "bank on it", "break the ice", "call it a \
day" -- the sentence illustrates a listed sense only when one of the listed senses is \
that phrase's meaning. If the inventory has a sense for the idiom, the sentence fits it. \
If the inventory has only literal senses, the sentence fits none of them, however \
familiar the phrase is: an idiom is a meaning the dictionary either carries or does not.

A SENTENCE THAT USES THE WORD TWICE. Some sentences use the headword more than once, and \
occasionally in two different meanings ("She banked the cheque at the bank on the \
corner."). Answer for the use the sentence is built around -- the one that carries the \
sentence's point -- rather than a passing second use. If the two uses are equally \
central, the sentence is not a good example of either and fits none of them.

DERIVED AND COMPOUND FORMS. An inflected form of the headword -- a plural, a past tense, \
an -ing form -- counts as the headword and is judged like any other use. A longer word \
that merely contains the headword's letters does not: "embankment" is not "bank", and a \
sentence whose only near-use is a word like that fits none of the listed senses. The same \
goes for a compound that has become its own word: "riverbank" is a use of "bank" only if \
the sentence is genuinely about the thing the listed sense defines.

DO NOT JUDGE THE SENTENCE ITSELF. You are not being asked whether the sentence is well \
written, whether it reads at the right level, whether it is interesting, or whether it is \
true. Those are settled elsewhere. Your only question is which definition it uses. A dull \
sentence that plainly uses sense 2 fits sense 2; a vivid sentence that could be about \
anything fits none.

WORKED EXAMPLE. Suppose the headword is "bank" and the senses listed are:

  1. [noun] The land alongside a river or a lake.
  2. [noun] A business that keeps people's money and lends it out.
  3. [verb] To tilt an aircraft to one side while turning.

  "We ate our lunch on the grassy bank." — fits sense 1.
  "The bank turned down her loan application." — fits sense 2.
  "The pilot banked hard to the left." — fits sense 3.
  "She banked on getting the job." — fits none of them: this is a different verb sense \
that nobody listed.
  "The bank was closed all week." — fits sense 2: a river bank does not close.
  "They walked past the embankment at dusk." — fits none: "embankment" is a different \
word, not a form of the headword.
  "Rain had washed away part of the bank." — fits sense 1: it is the land that washes \
away, not the business.

Answer for every sentence you were given, identified by the number it was listed under, \
using the sense numbers exactly as they were listed."""


def _build_sense_fit_prompt(
    headword: str,
    senses: Sequence[tuple[int, str, str]],
    sentences: Sequence[str],
) -> str:
    """Return the volatile half of the sense-fit prompt.

    Two lists numbered from one, as the QA prompt and ``sense_hygiene`` both do it. The
    sentences are listed **without** the sense each was written for: naming it would turn
    the question into a request for agreement, which is the one answer this call is being
    bought to avoid.

    Args:
        headword: The lexeme's surface form.
        senses: ``(sense_ref, part of speech, canonical gloss)`` per live sense.
        sentences: The accepted sentences, in the order the answer refers to them by.

    Returns:
        The per-call prompt body.
    """
    lines = [f"Headword: {headword}", f"Senses ({len(senses)}):"]
    lines.extend(f"  {ref}. [{pos}] {gloss}" for ref, pos, gloss in senses)
    lines.append(f"Sentences ({len(sentences)}):")
    lines.extend(f"  {i + 1}. {text}" for i, text in enumerate(sentences))
    return "\n".join(lines)


# --------------------------------------------------------------------------------------
# Planning: which senses, which targets, and whether a call is due at all
# --------------------------------------------------------------------------------------


@dataclass(slots=True)
class _SenseSlot:
    """One live sense of an entry, and everything writing sentences for it needs.

    Attributes:
        ref: The 1-based number the sense is listed under, and the number the model's
            answer refers back to. Never a stored sense id — the model cannot invent a ref
            for a sense it was not shown.
        sense_id: The derived positional id (D-1), used for the marker digest and logs.
        pos_entry: The owning part-of-speech entry, for its morphology.
        sense: The sense itself, mutated when a sentence is accepted for it.
        gloss: Its canonical definition, shown to the model.
        example: One example it already holds, shown so the model does not repeat the
            situation; :data:`_NO_EXAMPLE` when it holds none.
        forms: Surface forms tried when locating the headword in a candidate sentence.
        existing: Normalised text of every example the sense already holds, for the
            near-duplicate check.
    """

    ref: int
    sense_id: str
    pos_entry: POSEntry
    sense: Sense
    gloss: str
    example: str
    forms: list[str]
    existing: set[str]


def _forms_for(entry: Lexeme, pos_entry: POSEntry) -> list[str]:
    """Return the surface forms to try when locating the headword in a sentence.

    Mirrors ``workflows/enrich.py``'s and ``workflows/example_hygiene.py``'s own private
    ``_forms_for``: the sense's own morphology first, falling back to the cheap
    rule-based forms when the model supplied none.
    """
    forms = [*pos_entry.morphology.inflected_forms(), *pos_entry.morphology.derivations]
    return forms or list(spans.generate_forms(entry.headword))


def _normalise(text: str) -> str:
    """Return a comparison key for near-duplicate detection.

    Lowercased, stripped of everything but letters, digits and single spaces, so "We sat
    on the bank!" and "we sat on the bank." are one sentence and are not stored twice.

    Args:
        text: The sentence to normalise.

    Returns:
        The comparison key.
    """
    lowered = _NORMALISE_RE.sub(" ", text.lower())
    return _WHITESPACE_RE.sub(" ", lowered).strip()


def _opening(text: str) -> str:
    """Return the normalised first three words of a sentence, for the variety check."""
    return " ".join(_normalise(text).split()[:_OPENING_WORDS])


def _slots(entry: Lexeme) -> list[_SenseSlot]:
    """Return one slot per live sense of an entry, numbered from one in document order.

    Retired senses are skipped entirely: they are tombstones (D-52) and writing fresh
    sentences into one would be paying to fill a grave.

    Args:
        entry: The entry to inspect. Never mutated.

    Returns:
        The slots, in the order the model is shown them and refers to them by.
    """
    slots: list[_SenseSlot] = []
    for pos_entry, sense, sense_id in entry.iter_senses():
        if sense.retired:
            continue
        canonical = sense.examples.canonical()
        first = canonical.content.text if canonical is not None else None
        if first is None and len(sense.examples):
            first = sense.examples[0].content.text
        slots.append(
            _SenseSlot(
                ref=len(slots) + 1,
                sense_id=sense_id,
                pos_entry=pos_entry,
                sense=sense,
                gloss=" ".join(strip_markdown(sense.canonical_gloss()).split()),
                example=" ".join(first.split()) if first else _NO_EXAMPLE,
                forms=_forms_for(entry, pos_entry),
                existing={_normalise(r.content.text) for r in sense.examples},
            )
        )
    return slots


def _digest(sense_ids: Iterable[str]) -> str:
    """Return a stable short hash of the set of senses one call was made for.

    Args:
        sense_ids: The live sense ids, in any order.

    Returns:
        Sixteen hex characters of SHA-256 over the sorted, newline-joined ids. Sorted so
        the digest does not depend on document order, and SHA-256 rather than :func:`hash`
        because the value is written to disk and compared across processes.
    """
    joined = "\n".join(sorted(sense_ids))
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:16]


def _marker_note(slots: Sequence[_SenseSlot], per_sense: int) -> str:
    """Return the sentinel an entry carries once this workflow has written for it."""
    digest = _digest(slot.sense_id for slot in slots)
    return f"{MARKER_PREFIX}:{digest}{_COUNT_SEPARATOR}{per_sense}"


def _already_written(entry: Lexeme, note: str) -> bool:
    """Return whether this entry already carries exactly this workflow's marker."""
    return any(record.note == note for record in entry.provenance.values())


def plan_examples(entry: Lexeme, config: ExamplesConfig) -> ExamplesPlan:
    """Return what this entry would cost, without calling a model.

    The planning view ``examples --dry-run`` prices, and the same gate the sweep itself
    applies: an entry with no live senses, or one already carrying the marker for exactly
    this sense set and this ``per_sense``, is not due a call.

    Args:
        entry: The entry to inspect. Never mutated.
        config: The run's example policy.

    Returns:
        An :class:`ExamplesPlan`. ``due`` is ``False`` when the entry would cost $0.
    """
    slots = _slots(entry)
    if not slots or _already_written(entry, _marker_note(slots, config.per_sense)):
        return ExamplesPlan()
    return ExamplesPlan(
        senses=len(slots),
        sentences=len(slots) * config.per_sense,
        sense_check=config.sense_check and len(slots) >= _MIN_SENSES_FOR_CHECK,
    )


# --------------------------------------------------------------------------------------
# Acceptance
# --------------------------------------------------------------------------------------


@dataclass(slots=True)
class _Accepted:
    """One sentence that passed every deterministic check, ready to be stored."""

    slot: _SenseSlot
    level: ReadingLevel
    register: Register
    text: str
    span: tuple[int, int]
    grade: float
    hard_share: float


@dataclass(slots=True)
class _Sieve:
    """The per-call state the acceptance checks read and write.

    One instance per model call, so "already accepted in this call" is exactly what it
    says: the accepted texts and openings of every sense of one entry, pooled, because a
    sentence duplicated across two senses is as useless as one duplicated within a sense.

    Attributes:
        headword: The entry's surface form.
        wanted: The ``(sense_ref, level, register)`` triples that were actually asked for.
        gloss_shape: The compiled gloss-shape pattern for this headword.
        accepted_texts: Normalised text of every sentence accepted so far in this call.
        openings: First-three-word openings of the same.
    """

    headword: str
    wanted: set[tuple[int, ReadingLevel, Register]]
    gloss_shape: re.Pattern[str]
    accepted_texts: set[str] = field(default_factory=set)
    openings: set[str] = field(default_factory=set)


def _word_cap(level: ReadingLevel, config: ExamplesConfig) -> int:
    """Return the longest an accepted sentence may be at one reading level."""
    return min(config.max_words, _LEVEL_WORD_CAPS.get(level, config.max_words))


def _shape_reason(text: str, level: ReadingLevel, config: ExamplesConfig) -> RejectReason | None:
    """Return why a sentence is not one sentence of the right length, or ``None``.

    Args:
        text: The markdown-stripped, whitespace-collapsed candidate.
        level: The reading level it was written for, whose word cap applies.
        config: The run's example policy.

    Returns:
        The first shape defect found, or ``None`` when there is none.
    """
    if not text:
        return RejectReason.EMPTY
    if not text[0].isupper() or not text.endswith(_TERMINALS):
        return RejectReason.NOT_A_SENTENCE
    words = word_count(text)
    if words < config.min_words:
        return RejectReason.TOO_SHORT
    if words > _word_cap(level, config):
        return RejectReason.TOO_LONG
    return None


def _band_reason(
    level: ReadingLevel,
    grade: float,
    hard_share: float,
    readability: ReadabilityConfig,
) -> RejectReason | None:
    """Return why a sentence is too hard for its own reading level, or ``None``.

    Only the *upper* bound of the Flesch-Kincaid band is enforced, unlike
    ``enrich._misses_band``'s equivalent: a short, natural sentence legitimately measures
    below a college band, and rejecting it for reading easily would select for padding.
    The unfamiliar-word share is enforced only where it has a band at all — ``grade_1``
    and ``grade_5`` (D-51).

    Args:
        level: The reading level the sentence was written for.
        grade: Its measured Flesch-Kincaid grade, headword excused.
        hard_share: Its measured unfamiliar-word share, headword excused.
        readability: The run's readability policy, for the two tolerances and switches.

    Returns:
        The band defect found, or ``None``.
    """
    if readability.enabled and grade > grade_band(level)[1] + readability.tolerance:
        return RejectReason.READABILITY
    band = vocabulary_band(level)
    if (
        readability.vocabulary_check
        and band is not None
        and hard_share > band + readability.vocabulary_tolerance
    ):
        return RejectReason.HARD_VOCABULARY
    return None


def _novelty_reason(
    normalised: str, opening: str, slot: _SenseSlot, sieve: _Sieve
) -> RejectReason | None:
    """Return why a sentence repeats one the entry already has, or ``None``.

    Args:
        normalised: Its comparison key, from :func:`_normalise`.
        opening: Its first three normalised words, from :func:`_opening`.
        slot: The sense it was written for, whose stored examples it must not repeat.
        sieve: The per-call state, carrying what this call has already accepted.

    Returns:
        The novelty defect found, or ``None``.
    """
    if normalised in slot.existing or normalised in sieve.accepted_texts:
        return RejectReason.DUPLICATE
    if opening in sieve.openings:
        return RejectReason.REPEATED_OPENING
    return None


def _judge(
    draft: DraftSenseExample,
    slot: _SenseSlot,
    sieve: _Sieve,
    config: ExamplesConfig,
    readability: ReadabilityConfig,
) -> _Accepted | RejectReason:
    """Accept one drafted sentence, or say why it was not kept.

    The checks are ordered cheapest-and-most-fundamental first, and the first failure
    wins: a sentence that is both a fragment and too hard is counted once, as a fragment,
    because that is the defect a prompt change would have to fix first. A sentence that is
    accepted updates ``sieve`` on its way out, so the sentences after it in the same
    answer are compared against it.

    Args:
        draft: One sentence as the model returned it, before markdown stripping.
        slot: The sense it was written for.
        sieve: The per-call acceptance state, read and (on acceptance) updated.
        config: The run's example policy.
        readability: The run's readability policy, for the two tolerances.

    Returns:
        The accepted sentence with everything measured on it, or the reason it was not.
    """
    text = " ".join(strip_markdown(draft.text).split())
    shape = _shape_reason(text, draft.reading_level, config)
    if shape is not None:
        return shape

    span = spans.find_span(text, sieve.headword, slot.forms)
    if span is None:
        return RejectReason.HEADWORD_ABSENT
    if sieve.gloss_shape.match(text):
        return RejectReason.GLOSS_SHAPED

    grade = flesch_kincaid_grade(text, ignore=(sieve.headword,))
    hard_share = hard_word_share(text, ignore=(sieve.headword,))
    normalised = _normalise(text)
    opening = _opening(text)
    reason = _band_reason(draft.reading_level, grade, hard_share, readability) or _novelty_reason(
        normalised, opening, slot, sieve
    )
    if reason is not None:
        return reason

    sieve.accepted_texts.add(normalised)
    sieve.openings.add(opening)
    return _Accepted(
        slot=slot,
        level=draft.reading_level,
        register=draft.style,
        text=text,
        span=span,
        grade=grade,
        hard_share=hard_share,
    )


def _sift(
    drafted: Sequence[DraftSenseExample],
    slots: Sequence[_SenseSlot],
    headword: str,
    config: ExamplesConfig,
    readability: ReadabilityConfig,
) -> tuple[list[_Accepted], dict[str, int]]:
    """Run every drafted sentence past :func:`_judge`, in the order it was returned.

    Order matters and is the model's own: the duplicate and repeated-opening checks are
    against what has already been accepted, so the first of two colliding sentences is the
    one that survives. Nothing here re-orders or prefers, which keeps the result a pure
    function of the answer.

    Args:
        drafted: The sentences as returned.
        slots: The live senses, indexed by their 1-based ref.
        headword: The entry's surface form.
        config: The run's example policy.
        readability: The run's readability policy.

    Returns:
        ``(accepted sentences, rejection counts by reason)``.
    """
    by_ref = {slot.ref: slot for slot in slots}
    targets = config.targets()
    wanted = {(slot.ref, level, register) for slot in slots for level, register in targets}
    sieve = _Sieve(
        headword=headword,
        wanted=wanted,
        gloss_shape=re.compile(
            _GLOSS_SHAPE_TEMPLATE.format(headword=re.escape(headword)), re.IGNORECASE
        ),
    )
    accepted: list[_Accepted] = []
    rejected: dict[str, int] = {}
    filled: set[tuple[int, ReadingLevel, Register]] = set()

    for draft in drafted:
        key = (draft.sense_ref, draft.reading_level, draft.style)
        slot = by_ref.get(draft.sense_ref)
        if slot is None or key not in sieve.wanted or key in filled:
            rejected[RejectReason.UNWANTED.value] = rejected.get(RejectReason.UNWANTED.value, 0) + 1
            continue
        filled.add(key)
        verdict = _judge(draft, slot, sieve, config, readability)
        if isinstance(verdict, RejectReason):
            rejected[verdict.value] = rejected.get(verdict.value, 0) + 1
            continue
        accepted.append(verdict)
    return accepted, rejected


# --------------------------------------------------------------------------------------
# The two calls
# --------------------------------------------------------------------------------------


async def _write_sentences(
    entry: Lexeme,
    slots: Sequence[_SenseSlot],
    runner: StageRunner,
    config: ExamplesConfig,
) -> tuple[list[DraftSenseExample], Provenance, float] | None:
    """Make the one generation call for an entry, or return ``None`` if it failed.

    Args:
        entry: The entry being written for.
        slots: Its live senses, in listing order.
        runner: The stage runner.
        config: The run's example policy, for the target list.

    Returns:
        ``(drafted sentences, the call's provenance, its cost)``, or ``None``.

    Raises:
        BudgetExceededError: A budget stop is a run-level condition and propagates.
    """
    try:
        result = await runner.run(
            stage=StageName.EXAMPLES,
            output_type=DraftExampleBatch,
            instructions=prompts.EXAMPLES_INSTRUCTIONS,
            prompt=prompts.build_examples_prompt(
                entry.headword,
                [(slot.ref, slot.pos_entry.pos.value, slot.gloss, slot.example) for slot in slots],
                config.targets(),
            ),
            prompt_version=prompts.PROMPT_VERSION,
        )
    except BudgetExceededError:
        raise
    except GenerationError as exc:
        _LOG.warning("examples_generation_failed", headword=entry.headword, error=str(exc))
        return None
    return list(result.output.examples), result.provenance, result.cost_usd


async def _check_sense_fit(
    entry: Lexeme,
    slots: Sequence[_SenseSlot],
    accepted: Sequence[_Accepted],
    runner: StageRunner,
) -> tuple[list[_Accepted], int, Provenance | None, float]:
    """Drop the accepted sentences that illustrate a sense other than their own.

    A failed call is not a failure of the workflow: the sentences have already passed
    every deterministic check, and keeping them unchecked is strictly better than
    discarding them. Only a budget stop propagates.

    Args:
        entry: The entry being written for.
        slots: Its live senses, in listing order.
        accepted: The sentences that passed :func:`_judge`, in listing order.
        runner: The stage runner.

    Returns:
        ``(surviving sentences, how many were dropped, the call's provenance, its cost)``.
        The provenance is ``None`` when no call was made or the call failed.

    Raises:
        BudgetExceededError: A budget stop is a run-level condition and propagates.
    """
    try:
        result = await runner.run(
            # The shared HYGIENE policy (nano): this is a structural verdict about which
            # definition a sentence uses, not prose for a reader — the same call
            # ``sense_hygiene``'s ``example_fit`` step makes about stored examples.
            stage=StageName.SENSE_CHECK,
            output_type=_DraftSentenceFits,
            instructions=SENSE_FIT_INSTRUCTIONS,
            prompt=_build_sense_fit_prompt(
                entry.headword,
                [(slot.ref, slot.pos_entry.pos.value, slot.gloss) for slot in slots],
                [item.text for item in accepted],
            ),
            prompt_version=prompts.PROMPT_VERSION,
        )
    except BudgetExceededError:
        raise
    except GenerationError as exc:
        _LOG.warning("examples_sense_check_failed", headword=entry.headword, error=str(exc))
        return list(accepted), 0, None, 0.0

    verdicts = {
        fit.sentence_ref: fit.best_sense_ref
        for fit in result.output.fits
        if 1 <= fit.sentence_ref <= len(accepted)
    }
    kept: list[_Accepted] = []
    dropped = 0
    for position, item in enumerate(accepted, start=1):
        # A sentence the answer says nothing about is kept: silence is not a verdict, and
        # every one of these has already passed every deterministic check.
        if position in verdicts and verdicts[position] != item.slot.ref:
            dropped += 1
            _LOG.info(
                "example_sense_mismatch",
                headword=entry.headword,
                sense=item.slot.sense_id,
                best_sense_ref=verdicts[position],
                text=item.text,
            )
            continue
        kept.append(item)
    return kept, dropped, result.provenance, result.cost_usd


# --------------------------------------------------------------------------------------
# Applying
# --------------------------------------------------------------------------------------


def _store_sentences(
    entry: Lexeme, accepted: Sequence[_Accepted], provenance_id: str | None
) -> int:
    """Add every surviving sentence to its sense as an example rendition.

    Example renditions are keyed on ``(level, register, text)``
    (:func:`~opengloss_generator.schema._uniqueness_key`), so many sentences can share one
    target — which is the whole point of this workflow, and why it does not go through
    ``Renditions.missing`` the way ``enrich.py`` does. The duplicate check has already
    guaranteed no two accepted texts normalise the same, so the key cannot collide; the
    guard below is for the concurrent case where another pass filled the identical text
    between this entry's read and its write.

    Args:
        entry: The entry, mutated in place through its senses.
        accepted: The sentences to store.
        provenance_id: Key of the generation call's record in the entry's provenance
            table, carried by every rendition it produced.

    Returns:
        How many renditions were actually added.
    """
    added = 0
    for item in accepted:
        renditions = item.slot.sense.examples
        if any(
            r.key == (item.level, item.register) and r.content.text == item.text for r in renditions
        ):
            continue
        renditions.add(
            Rendition[Example](
                reading_level=item.level,
                style=item.register,
                content=Example(text=item.text, span=item.span),
                provenance_id=provenance_id,
                assessment=Assessment(
                    readability_grade=round(item.grade, 2),
                    hard_word_share=round(item.hard_share, 3),
                ),
            )
        )
        added += 1
    return added


async def _fill_entry(
    entry: Lexeme,
    runner: StageRunner,
    config: ExamplesConfig,
    readability: ReadabilityConfig,
    tally: _Tally,
) -> tuple[int, bool]:
    """Write, check and store one entry's example sentences, in place.

    Args:
        entry: The entry to fill, mutated in place.
        runner: The stage runner.
        config: The run's example policy.
        readability: The run's readability policy.
        tally: The sweep tally, for calls, cost and rejection counts.

    Returns:
        ``(sentences stored, whether the entry needs writing)``. The second element is not
        the first one's truth value: a call that produced nothing usable still leaves the
        idempotence marker to be persisted, or the next sweep pays for the same answer.

    Raises:
        BudgetExceededError: A budget stop is a run-level condition and propagates —
            before anything is written, so the entry is left exactly as it was found.
    """
    slots = _slots(entry)
    if not slots:
        return 0, False
    marker = _marker_note(slots, config.per_sense)
    if _already_written(entry, marker):
        return 0, False

    written = await _write_sentences(entry, slots, runner, config)
    if written is None:
        # A call that failed outright writes no marker, so the entry is retried whole on
        # the next sweep — the convention every model call in this project follows.
        return 0, False
    drafted, provenance, cost = written
    await tally.call(cost)

    accepted, rejected = _sift(drafted, slots, entry.headword, config, readability)
    await tally.sentences(len(drafted), rejected)

    dropped = 0
    check_provenance: Provenance | None = None
    if config.sense_check and len(slots) >= _MIN_SENSES_FOR_CHECK and accepted:
        accepted, dropped, check_provenance, check_cost = await _check_sense_fit(
            entry, slots, accepted, runner
        )
        if check_provenance is not None:
            await tally.call(check_cost)
        await tally.dropped(dropped)

    # The marker rides the generation call's own record, so one record is both "what this
    # cost" and "this entry has been written for", exactly as ``example_hygiene`` does it.
    provenance_id = entry.add_provenance(provenance.model_copy(update={"note": marker}))
    if check_provenance is not None:
        entry.add_provenance(check_provenance)
    stored = _store_sentences(entry, accepted, provenance_id)
    await tally.accepted(stored)

    _LOG.info(
        "examples_written",
        headword=entry.headword,
        senses=len(slots),
        generated=len(drafted),
        accepted=stored,
        rejected=sum(rejected.values()),
        refiled_dropped=dropped,
        cost_usd=round(cost, 6),
    )
    return stored, True


# --------------------------------------------------------------------------------------
# The sweep
# --------------------------------------------------------------------------------------


class _Tally:
    """One sweep's counters, mutated only while holding an ``asyncio.Lock``.

    Mirrors ``retrofit.py``'s own ``_Tally`` and every sweep that followed it: many
    handlers touch these counters around many awaits, and single-threaded asyncio only
    makes one await-free statement atomic, not a whole read-modify-write spanning one.
    """

    def __init__(self) -> None:
        """Start an empty outcome."""
        self._lock = asyncio.Lock()
        self._result = ExamplesOutcome()

    @property
    def result(self) -> ExamplesOutcome:
        """Return the accumulated outcome; read it once the pool has drained."""
        return self._result

    async def entry(self, *, changed: bool) -> None:
        """Fold one visited entry into the outcome."""
        async with self._lock:
            self._result.entries_scanned += 1
            if changed:
                self._result.entries_changed += 1
            if self._result.entries_scanned % PROGRESS_EVERY == 0:
                _LOG.info(
                    "examples_progress",
                    entries_scanned=self._result.entries_scanned,
                    accepted=self._result.accepted,
                    cost_usd=round(self._result.cost_usd, 6),
                )

    async def call(self, cost_usd: float) -> None:
        """Record one completed model call and what it cost."""
        async with self._lock:
            self._result.calls += 1
            self._result.cost_usd += cost_usd

    async def sentences(self, generated: int, rejected: dict[str, int]) -> None:
        """Record one call's generated sentences and why any of them were refused."""
        async with self._lock:
            self._result.sentences_generated += generated
            for reason, count in rejected.items():
                self._result.rejected_by_reason[reason] = (
                    self._result.rejected_by_reason.get(reason, 0) + count
                )

    async def accepted(self, count: int) -> None:
        """Record how many sentences one entry actually stored."""
        async with self._lock:
            self._result.accepted += count

    async def dropped(self, count: int) -> None:
        """Record sentences the sense-fit check dropped."""
        async with self._lock:
            self._result.refiled_dropped += count

    async def note_stop(self, reason: str) -> None:
        """Record why the sweep stopped early, keeping the first reason given."""
        async with self._lock:
            if self._result.stopped_reason is None:
                self._result.stopped_reason = reason


async def run_examples(
    store: LexemeStore,
    runner: StageRunner,
    *,
    lexeme_ids: Iterable[str] | None = None,
    workers: int,
    stop_event: asyncio.Event | None = None,
) -> ExamplesOutcome:
    """Write, verify and store fresh example sentences for every sense in the store (D-53).

    Args:
        store: The store to fill. Each entry is read, written for — including its one or
            two model calls — and written back inside one hold of its own lock, exactly
            the discipline ``workflows/retrofit.py`` documents.
        runner: The stage runner. Its configuration supplies both the example policy
            (``config.examples``) and the two acceptance tolerances
            (``config.readability``), so this function's signature stays the one every
            other sweep in the project has.
        lexeme_ids: Ids to visit; defaults to every id in the store, sorted.
        workers: Pool size.
        stop_event: Shared stop event; set by a budget stop, and honoured if the caller
            sets it from outside.

    Returns:
        An :class:`ExamplesOutcome` carrying counts, the rejection breakdown and cost. A
        sweep that stopped early still returns its outcome, with ``stopped_reason`` set,
        so a partial sweep reports what it managed to do.
    """
    ids = list(lexeme_ids) if lexeme_ids is not None else sorted(store.iter_ids())
    config = runner.config.examples
    readability = runner.config.readability
    tally = _Tally()

    async def fill(lexeme_id: str) -> None:
        async with store.locked(lexeme_id):
            entry = store.read(lexeme_id)
            if entry is None:
                return
            _, needs_write = await _fill_entry(entry, runner, config, readability, tally)
            if needs_write:
                store.write(entry)
        await tally.entry(changed=needs_write)

    async def guarded(lexeme_id: str) -> None:
        try:
            await fill(lexeme_id)
        except BudgetExceededError:
            await tally.note_stop("budget")
            raise

    await run_pool(ids, guarded, workers=workers, stop_event=stop_event)
    if stop_event is not None and stop_event.is_set():
        await tally.note_stop("stopped")

    result = tally.result
    _LOG.info("examples_complete", **result.as_dict())
    return result
