"""Workflow 12 — doc2query: the queries a real user would type to reach one sense (D-55).

Every other text this project writes is an *answer*: a definition, an example, an
encyclopedia passage. An encoder trained for retrieval needs the other half of the pair —
the question someone actually typed — and that half does not exist anywhere in a
dictionary. Doc2query is the standard way to manufacture it: show a model a document and
ask what queries it answers, then train the retriever on (query, document) pairs. Here the
"document" is one sense, and the store already knows the two things that make the
manufactured queries worth having.

First, **the entry knows its own ambiguity**. A sense is never shown alone: the prompt
carries the other live senses' glosses and says, in as many words, that a query which
would equally well retrieve one of those has failed. That is a discrimination signal a
plain doc2query run over a text corpus cannot ask for, and it is exactly what a retriever
has to learn — "bank" the riverside is not "bank" the business, and the query that
separates them is the training pair worth paying for.

Second, **a lexical query teaches a retriever nothing**. If every query contains the
headword, a BM25 baseline solves the task and the encoder learns to match a string. So the
instructions require at least half of a sense's queries to describe the meaning *without
naming the word*, and :func:`_sift` measures the achieved share for free on every sweep
rather than trusting the instruction. That measurement is the number this stage is judged
on.

Eight styles, one call per sense
--------------------------------

:class:`~opengloss_generator.schema.QueryStyle` names eight ways a person reaches for a
meaning — a bare keyword, a full question, a chatty aside, a stated constraint, a stated
role, a request for an example, a how-to, a bare imperative — and every sense is asked for
at least one of each. Style is what buys variance: twelve paraphrases of "what does X
mean" would be twelve near-identical rows, and a retriever trained on them learns one
surface form.

The call is per *sense*, not per entry as ``examples.py`` does it. The two stages differ
in what the model has to hold at once: an example sentence must fit its own sense and no
other, so writing sense 2's sentences while looking at sense 1 is the whole trick, whereas
a query is a short, self-contained string and asking for twelve of them for six senses in
one answer is the shape that gets four thrown-away queries per sense. Sense 2's siblings
are still in the prompt — they are what the queries have to discriminate against — they
are just there as context rather than as more work.

Acceptance: free, deterministic, and counted
--------------------------------------------

Nothing about a query can be checked by a model that cannot be checked for nothing:

* it is stripped of markdown and collapsed to one line; empty is refused;
* it is refused above :data:`QUERY_MAX_CHARS` characters — the storage schema's own
  ceiling, so an over-long query is dropped here rather than failing the entry's write;
* it is refused when it normalises (:func:`~opengloss_generator.schema.normalise_query_text`)
  to a query already accepted in this call or already stored on the sense — the schema
  forbids two queries with equal normalised text on one sense, so this check is what keeps
  a rerun from making an entry unwritable;
* everything past ``per_sense`` is refused as surplus, so the count a run asked for is the
  count it stores.

Containing the headword is **counted, never refused**. A keyword query for a rare technical
sense reasonably names the word, and refusing those individually would only teach the model
to smuggle the headword in as a paraphrase. What matters is the share across the sweep, and
that is what :class:`QueriesOutcome` reports.

Idempotence and cost
--------------------

A D-47 marker per sense, on the answering call's own provenance record:
``queries:<sense_id>:<digest>;attempts=<n>``, where the digest covers the sense's canonical
gloss text and the ``per_sense`` count. A rerun over an unchanged sense costs $0; a sense
whose gloss was rewritten, or a run configured for a different ``per_sense``, earns exactly
one more call, and D-47's bound of :data:`MAX_ATTEMPTS` stops there — a sense that has been
answered twice is left alone rather than re-billed by every future sweep.

Concurrency and locking (D-31) mirror every other sweep: the unit of work is one entry, and
the handler holding that entry's lock reads it, makes one call per due sense, applies what
survived, and writes it back inside the same lock hold. A budget stop mid-entry keeps the
senses already paid for — it is recorded on the entry and written before the stop
propagates, because throwing away an answer that has already been billed is the one thing a
budget guard must not cause.

The contract and the instructions are module-private rather than living in ``contracts.py``
and ``prompts.py``, following ``sense_hygiene`` and ``relation_hygiene``: several sibling
retrieval-data features are being built concurrently against the same shared files, and a
stage whose whole prompt surface is in one module cannot conflict with any of them.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Annotated

from pydantic import BaseModel, ConfigDict, Field

from opengloss_generator import spans
from opengloss_generator.errors import BudgetExceededError, GenerationError
from opengloss_generator.log import get_logger
from opengloss_generator.prompts import PROMPT_VERSION
from opengloss_generator.readability import strip_markdown
from opengloss_generator.runner import run_pool
from opengloss_generator.schema import (
    Query,
    QueryStyle,
    StageName,
    normalise_query_text,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Iterable, Sequence

    from opengloss_generator.schema import Lexeme, POSEntry, Provenance, Sense
    from opengloss_generator.stages import StageRunner
    from opengloss_generator.store import LexemeStore

__all__ = [
    "DEFAULT_PER_SENSE",
    "MARKER_PREFIX",
    "MAX_ATTEMPTS",
    "MAX_PER_SENSE",
    "MIN_PER_SENSE",
    "QUERY_MAX_CHARS",
    "QueriesOutcome",
    "QueriesPlan",
    "RejectReason",
    "SenseReport",
    "plan_queries",
    "run_queries",
]

_LOG = get_logger(__name__)

#: Prefix of this stage's D-47 marker. The full note is
#: ``queries:<sense_id>:<digest>;attempts=<n>``.
MARKER_PREFIX = "queries"

#: Separates the digest from the attempt counter inside a marker note (D-47's form).
_ATTEMPTS_SEPARATOR = ";attempts="

#: How many times one sense may be answered before it is left alone rather than re-billed
#: (D-47's bound). Two: the second attempt exists for a sense whose gloss was rewritten
#: under it, not as a retry loop — the stage has no defect it retries for.
MAX_ATTEMPTS = 2

#: Queries asked for per sense. Twelve against eight styles leaves four slots of slack, so
#: the model can spend them where the sense actually has more than one way in rather than
#: padding a style that has one.
DEFAULT_PER_SENSE = 12

#: One per style is the floor: below it the style axis stops being a coverage guarantee.
MIN_PER_SENSE = len(QueryStyle)

#: Ceiling, so a mistyped ``--per-sense`` fails at the door instead of truncating an answer
#: against the stage's ``max_tokens``.
MAX_PER_SENSE = 24

#: The storage schema's own ceiling on :class:`~opengloss_generator.schema.Query.text`.
#: Enforced here as a *post-check* rather than in the contract below, so an over-long query
#: is dropped and counted instead of failing the whole call's validation.
QUERY_MAX_CHARS = 200

#: What the contract allows, comfortably above :data:`QUERY_MAX_CHARS` for that reason.
_DRAFT_MAX_CHARS = 400

#: What the contract allows in one answer, comfortably above :data:`MAX_PER_SENSE` so a
#: model that returns a couple of extras is sifted rather than failed.
_DRAFT_MAX_QUERIES = 40

#: Shown in place of the example of a sense that has none, and of an absent domain.
_NONE = "(none)"

#: Everything but letters, digits and single spaces, removed before two texts are compared.
_WHITESPACE_RE = re.compile(r"\s+")

#: How many progress lines a long sweep emits, matching every other sweep in the project.
PROGRESS_EVERY = 500


class RejectReason(StrEnum):
    """Why one drafted query was not stored.

    There is no retry, so these counts are the stage's feedback loop: a reason that
    dominates a sweep is what the next change to :data:`QUERIES_INSTRUCTIONS` has to be
    aimed at.
    """

    #: Nothing left once markdown was stripped and whitespace collapsed.
    EMPTY = "empty"
    #: Longer than :data:`QUERY_MAX_CHARS`, which the storage schema refuses.
    TOO_LONG = "too_long"
    #: Normalises to a query the sense already holds, or to one accepted earlier in this
    #: same answer.
    DUPLICATE = "duplicate"
    #: Returned past the ``per_sense`` count the call asked for.
    SURPLUS = "surplus"


@dataclass(frozen=True, slots=True)
class QueriesPlan:
    """What one entry would cost this stage, computed without a model call.

    Attributes:
        senses: How many senses are due a call; ``0`` when the entry would cost nothing.
        queries: How many queries would be asked for — ``senses * per_sense``.
    """

    senses: int = 0
    queries: int = 0

    @property
    def due(self) -> bool:
        """Return whether this entry would cost anything at all."""
        return self.senses > 0


@dataclass(frozen=True, slots=True)
class SenseReport:
    """What one sense's call produced, for the run ledger.

    One record per *call*, which is one per sense, so the ledger a pilot reads back carries
    the per-sense cost and output-token count that D-41 wants
    ``ModelPolicy.expected_output_tokens`` set from.

    Attributes:
        sense_id: The derived sense id the call was made for.
        outcome: ``"stored"``, ``"empty"`` (the call succeeded but nothing survived the
            sieve) or ``"failed"``.
        stored: How many queries were written to the sense.
        rejected: How many drafted queries were refused, for any reason.
        with_headword: How many stored queries name the headword or a form of it.
        cost_usd: What the call cost.
        input_tokens: Reported prompt tokens.
        cached_input_tokens: The cached part of them.
        output_tokens: Reported completion tokens, reasoning included.
    """

    sense_id: str
    outcome: str
    stored: int = 0
    rejected: int = 0
    with_headword: int = 0
    cost_usd: float = 0.0
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0


@dataclass(slots=True)
class QueriesOutcome:
    """What one :func:`run_queries` sweep did across the store."""

    entries_scanned: int = 0
    entries_changed: int = 0
    senses_scanned: int = 0
    senses_answered: int = 0
    calls: int = 0
    queries_generated: int = 0
    stored: int = 0
    rejected_by_reason: dict[str, int] = field(default_factory=dict)
    stored_by_style: dict[str, int] = field(default_factory=dict)
    with_headword: int = 0
    senses_with_full_style_coverage: int = 0
    senses_below_headword_free_target: int = 0
    cost_usd: float = 0.0
    stopped_reason: str | None = None

    @property
    def rejected(self) -> int:
        """Return how many drafted queries were refused, for any reason."""
        return sum(self.rejected_by_reason.values())

    @property
    def headword_free_share(self) -> float:
        """Return the share of stored queries that do **not** name the headword.

        This is the number the stage is judged on: a corpus of queries that all contain
        their own answer's headword trains a retriever to do string matching.
        """
        if not self.stored:
            return 0.0
        return (self.stored - self.with_headword) / self.stored

    def as_dict(self) -> dict[str, object]:
        """Return a JSON-serialisable view for the CLI run summary."""
        return {
            "entries_scanned": self.entries_scanned,
            "entries_changed": self.entries_changed,
            "senses_scanned": self.senses_scanned,
            "senses_answered": self.senses_answered,
            "calls": self.calls,
            "queries_generated": self.queries_generated,
            "stored": self.stored,
            "rejected": self.rejected,
            "rejected_by_reason": dict(sorted(self.rejected_by_reason.items())),
            "stored_by_style": dict(sorted(self.stored_by_style.items())),
            "with_headword": self.with_headword,
            "headword_free_share": round(self.headword_free_share, 4),
            "senses_with_full_style_coverage": self.senses_with_full_style_coverage,
            "senses_below_headword_free_target": self.senses_below_headword_free_target,
            "cost_usd": round(self.cost_usd, 6),
            "stopped_reason": self.stopped_reason,
        }


# --------------------------------------------------------------------------------------
# The contract and the instructions (module-private; see the module docstring)
# --------------------------------------------------------------------------------------


class _DraftQuery(BaseModel):
    """One synthetic query, as the model returns it.

    No id and no provenance: both are derived
    (:func:`~opengloss_generator.identity.query_id` from the query's position, the
    provenance id from the call that wrote it), which is ``contracts.py``'s standing rule
    about never asking a model for something derivable.
    """

    model_config = ConfigDict(
        extra="forbid", validate_by_name=True, validate_by_alias=True, serialize_by_alias=True
    )

    text: Annotated[str, Field(min_length=1, max_length=_DRAFT_MAX_CHARS)]
    style: QueryStyle


class _DraftQuerySet(BaseModel):
    """Every query written for one sense in a single call."""

    model_config = ConfigDict(
        extra="forbid", validate_by_name=True, validate_by_alias=True, serialize_by_alias=True
    )

    queries: Annotated[list[_DraftQuery], Field(min_length=1, max_length=_DRAFT_MAX_QUERIES)]


#: Instructions for the one call this stage makes. Byte-stable and well past the 1,024-token
#: floor a provider prompt cache matches on, so every call after the first in a sweep is
#: served the prefix at the cached rate — the same discipline every stage in ``prompts.py``
#: follows, and the reason two sweeps' costs are comparable at all.
QUERIES_INSTRUCTIONS = """\
You are building the query side of a search-training corpus for a dictionary. You are \
given one sense of one headword -- its part of speech, its definition, one example \
sentence, its subject domain -- and the definitions of the headword's other senses. Your \
job is to write the search queries that this sense's definition would be the right answer \
to.

WHAT A QUERY IS HERE. Not a paraphrase of the definition, and not a question about the \
word. A query is what a real person typed into a search box, said to an assistant, or \
asked a colleague, at the moment they needed exactly this meaning and did not have it. \
Picture that person: they have the concept and they are missing the word, or they have \
half the word and want the rest, or they hit the thing in the wild and want to know what \
it was. Write what they typed. Real queries are short, uneven, sometimes ungrammatical, \
and almost never begin "What is the definition of".

THE ONE RULE THAT MATTERS MOST: AT LEAST HALF OF YOUR QUERIES MUST NOT CONTAIN THE \
HEADWORD, IN ANY FORM. A query that names the word is a query a plain keyword index \
already answers, and it teaches a search engine nothing except to match a string. The \
valuable query is the one that describes the meaning from the outside -- the situation, \
the effect, the thing it is used for, what it feels like, what it is not -- so that only \
something that understands the meaning could connect the two. Do not smuggle the headword \
back in as an obvious one-word synonym either. Count them before you answer: if more than \
half of your queries contain the headword or an inflected form of it, rewrite the excess \
ones from the outside.

THE SECOND RULE: EVERY QUERY MUST POINT AT THIS SENSE AND NOT AT ITS SIBLINGS. You are \
shown the headword's other senses precisely so you can avoid them. Before you keep a \
query, ask whether one of the other definitions on the page would answer it just as well. \
If it would, the query is worthless for training -- it is exactly the case the search \
engine is supposed to get right, handed to it with no signal. Put the discriminating \
detail into the query: the setting, the field, the material, who does it, what it is done \
to, what comes next. When the headword has only one sense there is nothing to \
discriminate against, and the queries should simply be as varied as the styles below \
allow.

THE EIGHT STYLES. Write at least one query in each of these, and label every query with \
the style it is written in. The styles are how people actually reach for a meaning, and a \
set that is all one style is worth a fraction of a set that is not.

keyword -- bare search terms, no sentence, usually two to six words, no punctuation and \
no politeness. The way a search box is really used. "rope descent cliff technique", \
"metal thing that holds a door open".

question -- a complete question, ending in a question mark. Who, what, when, where, why, \
how, which, or a yes/no question. "What do climbers call going down a rope face first?", \
"Is it still theft if you meant to give it back?".

conversational -- what someone would type to an assistant or say to a person, in a \
sentence or two, often in the first person, often with the context of why they are \
asking. Contractions are welcome. "I'm trying to remember the word for when you slide \
down a rope off a cliff, it's driving me mad".

constraint -- a request with a stated requirement, limit, exclusion or filter in it. The \
constraint is the point: "without any special gear", "that works in under an hour", "for \
under fifty pounds", "that doesn't need electricity", "not the legal meaning".

role -- a query that says who is asking or who it is for, so the answer has to be pitched \
at them. "as a complete beginner", "for a nurse explaining this to a patient", "I teach \
year 4 and need", "from a lawyer's point of view".

example_based -- a request for an instance rather than an explanation: a sentence using \
it, a case of it happening, a picture of what it looks like. "sentence using this in the \
climbing sense", "real examples of this happening at work", "what does one of these \
actually look like".

step_by_step -- a request for a procedure or a sequence, explicitly or by shape. "how do \
I do this safely, step by step", "what happens first and what happens after", "walk me \
through the process".

directive -- a bare imperative given to an assistant, with no question mark and no \
please. "explain the difference between the two meanings", "list the tools you need", \
"compare this with the everyday sense", "summarise how this works".

LENGTH AND FORM. Every query is a single line of plain text, at most 200 characters, and \
usually far shorter. No markdown, no bullets, no numbering, no surrounding quotation \
marks, no explanation of what you were going for. Do not repeat a query you have already \
written, and do not write two that differ only in a word: two ways of asking is the point, \
two spellings of one way is not. Vary the length across the set -- a three-word keyword \
next to a two-sentence conversational aside is exactly right.

DO NOT WRITE THESE. They look like queries and are worth nothing:

- "What does X mean?" and every variant of it. It is the one query a dictionary already \
answers, it names the headword, and it does not distinguish one sense from another.
- Anything that quotes the definition back. If your query and the definition share a whole \
phrase, you have written the answer, not the question.
- Queries that would fit any sense of the word, or any word at all: "tell me about this", \
"more information".
- Queries about the word as a word -- its spelling, its etymology, how many letters it has \
-- unless the sense itself is about language.
- Anything a person would not type: no "Please provide a comprehensive overview of", no \
"In the context of the aforementioned".

WORKED EXAMPLE. Suppose the sense is:

  Headword: bank [noun], domain business.finance
  Definition: A business that keeps people's money safe and lends it out at interest.
  Other senses: (1) [noun] The land alongside a river or a lake. (2) [verb] To tilt an \
aircraft to one side while turning.

Good queries, one per style, with six of the eight not naming the headword:

  keyword: "place that holds your money and lends it"
  question: "Where should I put my savings so they're insured?"
  conversational: "I got paid but the money isn't showing up yet, who do I even ask about \
that"
  constraint: "somewhere to keep money that doesn't charge a monthly fee"
  role: "as a first-time saver, who actually looks after my money"
  example_based: "sentence using bank in the money sense not the river one"
  step_by_step: "how do I open an account and get a card, step by step"
  directive: "explain how lending and interest work at these institutions"

Notice what each does. Only two of the eight name the headword, and one of those needs to \
-- it is a request for a usage example of the word itself. Not one of them would be \
answered by the riverbank sense or the aircraft sense: money, savings, fees, interest and \
accounts are in the queries, doing the discriminating work. And each sounds like a person \
in a moment of actually needing to know.

Bad queries for the same sense, and why:

  "What is a bank?" -- names the headword, fits two of the three senses, and is the query \
a dictionary already answers.
  "A business that keeps people's money safe." -- the definition typed back as a search \
box entry. Nobody types this.
  "bank" -- one word, fits every sense.
  "tell me about banks" -- no content; nothing to match on.
  "grassy slope beside the water" -- a perfectly good query, for the wrong sense.

Answer with exactly the number of queries you are asked for, each labelled with one of \
the eight styles, and with every style used at least once."""


def _build_prompt(
    headword: str,
    slot: _SenseSlot,
    siblings: Sequence[tuple[str, str]],
    per_sense: int,
) -> str:
    """Return the volatile half of the prompt for one sense.

    The sense being written for comes first and is named as such; the siblings follow as
    context, labelled with what they are for, so nothing in the prompt has to be inferred
    from position. The count is here rather than in the instructions because it is the one
    part of the ask that varies per run, and a variable inside the instructions would
    destroy the prefix cache for every call in the sweep.

    Args:
        headword: The lexeme's surface form.
        slot: The sense being written for, supplying its part of speech, domain, canonical
            definition and one existing example.
        siblings: ``(part of speech, canonical gloss)`` for every *other* live sense of the
            headword, in document order.
        per_sense: How many queries to ask for.

    Returns:
        The per-call prompt body.
    """
    lines = [
        f"Headword: {headword}",
        f"Part of speech: {slot.pos_entry.pos.value}",
        f"Domain: {slot.domain}",
        f"Definition: {slot.gloss}",
        f"Example: {slot.example}",
    ]
    if siblings:
        lines.append(
            f"Other senses of this headword ({len(siblings)}) — your queries must NOT fit these:"
        )
        lines.extend(f"  {i + 1}. [{p}] {g}" for i, (p, g) in enumerate(siblings))
    else:
        lines.append("Other senses of this headword: none; this headword has one live sense.")
    lines.append(
        f"Write exactly {per_sense} queries for the sense defined above, "
        "using each of the eight styles at least once, "
        f"and with no more than {per_sense // 2} of them containing the headword."
    )
    return "\n".join(lines)


# --------------------------------------------------------------------------------------
# Planning: which senses are due, and the D-47 marker
# --------------------------------------------------------------------------------------


@dataclass(slots=True)
class _SenseSlot:
    """One live sense of an entry, and everything writing queries for it needs.

    Attributes:
        sense_id: The derived positional id (D-1), which the marker is keyed on.
        pos_entry: The owning part-of-speech entry, for its morphology.
        sense: The sense itself, mutated when a query is accepted for it.
        gloss: Its canonical definition, whitespace-collapsed, shown to the model and
            hashed into the marker digest.
        example: One example it already holds; :data:`_NONE` when it holds none.
        domain: Its domain tag; :data:`_NONE` when it has none.
        forms: Surface forms tried when looking for the headword inside a query.
        existing: Normalised text of every query the sense already holds.
    """

    sense_id: str
    pos_entry: POSEntry
    sense: Sense
    gloss: str
    example: str
    domain: str
    forms: list[str]
    existing: set[str]


def _collapse(text: str) -> str:
    """Return ``text`` stripped of markdown and collapsed onto one line."""
    return _WHITESPACE_RE.sub(" ", strip_markdown(text)).strip()


def _forms_for(entry: Lexeme, pos_entry: POSEntry) -> list[str]:
    """Return the surface forms to look for when deciding whether a query is lexical.

    Mirrors ``workflows/examples.py``'s own private ``_forms_for``: the sense's stored
    morphology first, falling back to the cheap rule-based forms when the model supplied
    none. Inflected forms count as the headword here — a query saying "abseiling" is just
    as lexical as one saying "abseil", and the share this stage reports would flatter
    itself if it pretended otherwise.
    """
    forms = [*pos_entry.morphology.inflected_forms(), *pos_entry.morphology.derivations]
    return forms or list(spans.generate_forms(entry.headword))


def _slots(entry: Lexeme) -> list[_SenseSlot]:
    """Return one slot per live sense of an entry, in document order.

    Retired senses are skipped: they are tombstones (D-52), and a query that retrieves one
    is a query pointing at a meaning the dictionary has withdrawn.

    Args:
        entry: The entry to inspect. Never mutated.

    Returns:
        The slots, in document order.
    """
    slots: list[_SenseSlot] = []
    for pos_entry, sense, ident in entry.iter_senses():
        if sense.retired:
            continue
        canonical = sense.examples.canonical()
        first = canonical.content.text if canonical is not None else None
        if first is None and len(sense.examples):
            first = sense.examples[0].content.text
        slots.append(
            _SenseSlot(
                sense_id=ident,
                pos_entry=pos_entry,
                sense=sense,
                gloss=_collapse(sense.canonical_gloss()),
                example=_collapse(first) if first else _NONE,
                domain=sense.domain.value if sense.domain is not None else _NONE,
                forms=_forms_for(entry, pos_entry),
                existing={normalise_query_text(query.text) for query in sense.queries},
            )
        )
    return slots


def _digest(gloss: str, per_sense: int) -> str:
    """Return the marker digest for one sense.

    Args:
        gloss: The sense's canonical definition, as the model would be shown it.
        per_sense: How many queries the run asks for.

    Returns:
        Sixteen hex characters of SHA-256 over both. The count is in the digest for the
        reason ``examples.py`` puts it in its marker: a run configured for more queries is
        asking a different question and should earn one more call, and a run configured for
        the same number should not.
    """
    joined = f"{gloss}\n{per_sense}"
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True, slots=True)
class _Marker:
    """The most recent sentinel this stage left for one sense.

    Attributes:
        digest: The gloss/count hash the marker was written for.
        attempts: How many calls the stage has made for that sense, this one included.
    """

    digest: str
    attempts: int


def _latest_marker(entry: Lexeme, sense_id: str) -> _Marker | None:
    """Return the last sentinel this stage wrote for one sense, parsed.

    Args:
        entry: The entry to inspect.
        sense_id: The sense whose marker is wanted.

    Returns:
        The most recent marker, or ``None`` when the sense has never been answered.
        Provenance ids are handed out in insertion order and never reused, so the last
        matching record in the table is the most recently written one.
    """
    prefix = f"{MARKER_PREFIX}:{sense_id}:"
    latest: _Marker | None = None
    for record in entry.provenance.values():
        note = record.note or ""
        if not note.startswith(prefix):
            continue
        digest, _, attempts = note[len(prefix) :].partition(_ATTEMPTS_SEPARATOR)
        latest = _Marker(digest, int(attempts) if attempts.isdigit() else 1)
    return latest


def _attempt_number(entry: Lexeme, slot: _SenseSlot, per_sense: int) -> int | None:
    """Return which attempt is due for one sense, or ``None`` if none is (D-47).

    A sense is due a call when the stage has never answered for it, or when its canonical
    gloss (or the run's ``per_sense``) has changed since it last did — and when it has not
    already had :data:`MAX_ATTEMPTS` of them.

    Args:
        entry: The entry the sense belongs to.
        slot: The sense in question.
        per_sense: How many queries this run asks for.

    Returns:
        The 1-based attempt number, or ``None`` — which is also the "do not bill this"
        signal for the caller.
    """
    marker = _latest_marker(entry, slot.sense_id)
    if marker is None:
        return 1
    if marker.digest == _digest(slot.gloss, per_sense) or marker.attempts >= MAX_ATTEMPTS:
        return None
    return marker.attempts + 1


def _marker_note(slot: _SenseSlot, per_sense: int, attempt: int) -> str:
    """Return the sentinel to stamp for one attempt, in D-47's form."""
    digest = _digest(slot.gloss, per_sense)
    return f"{MARKER_PREFIX}:{slot.sense_id}:{digest}{_ATTEMPTS_SEPARATOR}{attempt}"


def plan_queries(entry: Lexeme, per_sense: int = DEFAULT_PER_SENSE) -> QueriesPlan:
    """Return what this entry would cost, without calling a model.

    The planning view ``queries --dry-run`` prices, and the same gate the sweep itself
    applies: a sense already carrying this stage's marker for its current gloss, or one that
    has had D-47's two attempts, is not due a call.

    Args:
        entry: The entry to inspect. Never mutated.
        per_sense: How many queries a due sense would be asked for.

    Returns:
        A :class:`QueriesPlan`. ``due`` is ``False`` when the entry would cost $0.
    """
    due = sum(1 for slot in _slots(entry) if _attempt_number(entry, slot, per_sense) is not None)
    return QueriesPlan(senses=due, queries=due * per_sense)


# --------------------------------------------------------------------------------------
# Acceptance
# --------------------------------------------------------------------------------------


@dataclass(slots=True)
class _Accepted:
    """One query that passed every free check, ready to be stored."""

    text: str
    style: QueryStyle
    has_headword: bool


def _has_headword(text: str, headword: str, forms: Sequence[str]) -> bool:
    """Return whether a query names the headword or one of its forms.

    Uses :func:`~opengloss_generator.spans.find_span`, which matches whole words
    case-insensitively, so "banks" counts for "bank" but "embankment" does not — the same
    predicate the example workflow uses to decide whether a sentence uses its word at all.
    """
    return spans.find_span(text, headword, forms) is not None


def _sift(
    drafted: Sequence[_DraftQuery],
    slot: _SenseSlot,
    headword: str,
    per_sense: int,
) -> tuple[list[_Accepted], dict[str, int]]:
    """Run every drafted query past the free checks, in the order it was returned.

    Order is the model's own and nothing here re-orders or prefers, so the result is a pure
    function of the answer: the first of two colliding queries survives, and the surplus is
    whatever came back after the count was filled.

    Args:
        drafted: The queries as returned.
        slot: The sense they were written for, whose stored queries they must not repeat.
        headword: The entry's surface form, for the lexical measurement.
        per_sense: How many to keep.

    Returns:
        ``(accepted queries, rejection counts by reason)``.
    """
    accepted: list[_Accepted] = []
    rejected: dict[str, int] = {}
    seen = set(slot.existing)

    def refuse(reason: RejectReason) -> None:
        rejected[reason.value] = rejected.get(reason.value, 0) + 1

    for draft in drafted:
        if len(accepted) >= per_sense:
            refuse(RejectReason.SURPLUS)
            continue
        text = _collapse(draft.text)
        if not text:
            refuse(RejectReason.EMPTY)
            continue
        if len(text) > QUERY_MAX_CHARS:
            refuse(RejectReason.TOO_LONG)
            continue
        key = normalise_query_text(text)
        if not key:
            # Punctuation only: non-empty as text, empty as a comparison key, and the
            # schema keys uniqueness on that key, so two of them could not both be stored.
            refuse(RejectReason.EMPTY)
            continue
        if key in seen:
            refuse(RejectReason.DUPLICATE)
            continue
        seen.add(key)
        accepted.append(
            _Accepted(
                text=text,
                style=draft.style,
                has_headword=_has_headword(text, headword, slot.forms),
            )
        )
    return accepted, rejected


# --------------------------------------------------------------------------------------
# The call
# --------------------------------------------------------------------------------------


async def _write_queries(
    entry: Lexeme,
    slot: _SenseSlot,
    siblings: Sequence[tuple[str, str]],
    runner: StageRunner,
    per_sense: int,
) -> tuple[list[_DraftQuery], Provenance, float, tuple[int, int, int]] | None:
    """Make the one call for one sense, or return ``None`` if it failed.

    Args:
        entry: The entry being written for.
        slot: The sense being written for.
        siblings: ``(pos, gloss)`` for the entry's other live senses.
        runner: The stage runner.
        per_sense: How many queries to ask for.

    Returns:
        ``(drafted queries, the call's provenance, its cost, its token counts)``, or
        ``None`` when the call failed outright — in which case no marker is written and the
        sense is retried whole on the next sweep, the convention every model call in this
        project follows.

    Raises:
        BudgetExceededError: A budget stop is a run-level condition and propagates.
    """
    try:
        result = await runner.run(
            stage=StageName.QUERIES,
            output_type=_DraftQuerySet,
            instructions=QUERIES_INSTRUCTIONS,
            prompt=_build_prompt(entry.headword, slot, siblings, per_sense),
            prompt_version=PROMPT_VERSION,
        )
    except BudgetExceededError:
        raise
    except GenerationError as exc:
        _LOG.warning(
            "queries_generation_failed",
            headword=entry.headword,
            sense=slot.sense_id,
            error=str(exc),
        )
        return None
    tokens = (result.input_tokens, result.cached_input_tokens, result.output_tokens)
    return list(result.output.queries), result.provenance, result.cost_usd, tokens


# --------------------------------------------------------------------------------------
# Applying
# --------------------------------------------------------------------------------------


def _store_queries(slot: _SenseSlot, accepted: Sequence[_Accepted], provenance_id: str) -> int:
    """Append every surviving query to its sense.

    Appends rather than inserts, because query ids are positional
    (:func:`~opengloss_generator.identity.query_id`) and inserting would rename every query
    after the insertion point. The sieve has already guaranteed no two accepted texts
    normalise the same as each other or as one the sense holds, so the schema's uniqueness
    validator cannot fire on the write.

    Args:
        slot: The sense to append to, mutated in place.
        accepted: The queries to store, in answer order.
        provenance_id: Key of this call's record in the entry's provenance table.

    Returns:
        How many queries were added.
    """
    for item in accepted:
        slot.sense.queries.append(
            Query(text=item.text, style=item.style, provenance_id=provenance_id)
        )
        slot.existing.add(normalise_query_text(item.text))
    return len(accepted)


async def _fill_sense(
    entry: Lexeme,
    slot: _SenseSlot,
    siblings: Sequence[tuple[str, str]],
    runner: StageRunner,
    *,
    per_sense: int,
    attempt: int,
    tally: _Tally,
) -> bool:
    """Write, sift and store one sense's queries, in place.

    Args:
        entry: The entry the sense belongs to, mutated through its provenance table.
        slot: The sense to fill, mutated in place.
        siblings: ``(pos, gloss)`` for the entry's other live senses.
        runner: The stage runner.
        per_sense: How many queries to ask for.
        attempt: The 1-based attempt number this call is (D-47).
        tally: The sweep tally.

    Returns:
        Whether the entry needs writing. Not the same as "queries were stored": a call that
        produced nothing usable still leaves its marker to be persisted, or the next sweep
        pays for the same answer.

    Raises:
        BudgetExceededError: A budget stop is a run-level condition and propagates, before
            anything is written for this sense.
    """
    written = await _write_queries(entry, slot, siblings, runner, per_sense)
    if written is None:
        await tally.sense(SenseReport(sense_id=slot.sense_id, outcome="failed"))
        return False
    drafted, provenance, cost, tokens = written

    accepted, rejected = _sift(drafted, slot, entry.headword, per_sense)
    provenance_id = entry.add_provenance(
        provenance.model_copy(update={"note": _marker_note(slot, per_sense, attempt)})
    )
    stored = _store_queries(slot, accepted, provenance_id)
    with_headword = sum(1 for item in accepted if item.has_headword)

    await tally.answered(
        generated=len(drafted),
        accepted=accepted,
        rejected=rejected,
        cost_usd=cost,
    )
    await tally.sense(
        SenseReport(
            sense_id=slot.sense_id,
            outcome="stored" if stored else "empty",
            stored=stored,
            rejected=sum(rejected.values()),
            with_headword=with_headword,
            cost_usd=cost,
            input_tokens=tokens[0],
            cached_input_tokens=tokens[1],
            output_tokens=tokens[2],
        )
    )
    _LOG.info(
        "queries_written",
        headword=entry.headword,
        sense=slot.sense_id,
        attempt=attempt,
        generated=len(drafted),
        stored=stored,
        rejected=sum(rejected.values()),
        with_headword=with_headword,
        cost_usd=round(cost, 6),
    )
    return True


async def _fill_entry(
    entry: Lexeme,
    runner: StageRunner,
    per_sense: int,
    tally: _Tally,
) -> tuple[bool, BudgetExceededError | None]:
    """Write queries for every due sense of one entry, in place.

    A budget stop is caught rather than propagated so the caller can still persist the
    senses already answered: those calls have been billed, and throwing their answers away
    is the one thing a budget guard must not cause. The error is handed back for the caller
    to re-raise once the entry is on disk.

    Args:
        entry: The entry to fill, mutated in place.
        runner: The stage runner.
        per_sense: How many queries each due sense is asked for.
        tally: The sweep tally.

    Returns:
        ``(whether the entry needs writing, the budget stop that ended it early or None)``.
    """
    slots = _slots(entry)
    await tally.senses_scanned(len(slots))
    needs_write = False
    for position, slot in enumerate(slots):
        attempt = _attempt_number(entry, slot, per_sense)
        if attempt is None:
            continue
        siblings = [(o.pos_entry.pos.value, o.gloss) for i, o in enumerate(slots) if i != position]
        try:
            changed = await _fill_sense(
                entry, slot, siblings, runner, per_sense=per_sense, attempt=attempt, tally=tally
            )
        except BudgetExceededError as exc:
            return needs_write, exc
        needs_write = needs_write or changed
    return needs_write, None


# --------------------------------------------------------------------------------------
# The sweep
# --------------------------------------------------------------------------------------


class _Tally:
    """One sweep's counters, mutated only while holding an ``asyncio.Lock``.

    Mirrors ``retrofit.py``'s own ``_Tally`` and every sweep that followed it: many handlers
    touch these counters around many awaits, and single-threaded asyncio only makes one
    await-free statement atomic, not a read-modify-write spanning one.
    """

    def __init__(self, on_sense: Callable[[SenseReport], Awaitable[None]] | None = None) -> None:
        """Start an empty outcome, optionally reporting each call to the run ledger."""
        self._lock = asyncio.Lock()
        self._result = QueriesOutcome()
        self._on_sense = on_sense

    @property
    def result(self) -> QueriesOutcome:
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
                    "queries_progress",
                    entries_scanned=self._result.entries_scanned,
                    stored=self._result.stored,
                    cost_usd=round(self._result.cost_usd, 6),
                )

    async def senses_scanned(self, count: int) -> None:
        """Record how many live senses one entry offered."""
        async with self._lock:
            self._result.senses_scanned += count

    async def answered(
        self,
        *,
        generated: int,
        accepted: Sequence[_Accepted],
        rejected: dict[str, int],
        cost_usd: float,
    ) -> None:
        """Record one completed call: its cost, what it produced and what was refused."""
        async with self._lock:
            result = self._result
            result.calls += 1
            result.senses_answered += 1
            result.cost_usd += cost_usd
            result.queries_generated += generated
            result.stored += len(accepted)
            for reason, count in rejected.items():
                result.rejected_by_reason[reason] = result.rejected_by_reason.get(reason, 0) + count
            styles = set()
            with_headword = 0
            for item in accepted:
                result.stored_by_style[item.style.value] = (
                    result.stored_by_style.get(item.style.value, 0) + 1
                )
                styles.add(item.style)
                with_headword += int(item.has_headword)
            result.with_headword += with_headword
            if len(styles) == len(QueryStyle):
                result.senses_with_full_style_coverage += 1
            # The instruction is "no more than half", so the target is missed when strictly
            # more than half of what was stored names the word.
            if accepted and with_headword * 2 > len(accepted):
                result.senses_below_headword_free_target += 1

    async def sense(self, report: SenseReport) -> None:
        """Hand one call's per-sense report to the ledger, if the caller wanted one."""
        if self._on_sense is not None:
            await self._on_sense(report)

    async def note_stop(self, reason: str) -> None:
        """Record why the sweep stopped early, keeping the first reason given."""
        async with self._lock:
            if self._result.stopped_reason is None:
                self._result.stopped_reason = reason


async def run_queries(
    store: LexemeStore,
    runner: StageRunner,
    *,
    lexeme_ids: Iterable[str] | None = None,
    per_sense: int = DEFAULT_PER_SENSE,
    workers: int,
    stop_event: asyncio.Event | None = None,
    on_sense: Callable[[SenseReport], Awaitable[None]] | None = None,
) -> QueriesOutcome:
    """Write synthetic retrieval queries for every live sense in the store (D-55).

    Args:
        store: The store to fill. Each entry is read, written for — including one model
            call per due sense — and written back inside one hold of its own lock, exactly
            the discipline ``workflows/retrofit.py`` documents (D-31).
        runner: The stage runner.
        lexeme_ids: Ids to visit; defaults to every id in the store, sorted.
        per_sense: How many queries each due sense is asked for.
        workers: Pool size.
        stop_event: Shared stop event; set by a budget stop, and honoured if the caller sets
            it from outside.
        on_sense: Called once per model call with a :class:`SenseReport`, so the CLI can put
            one ledger record per sense on the run ledger. The pool's own contract is that
            the handler owns its ledger emission (``runner.run_pool``).

    Returns:
        A :class:`QueriesOutcome` carrying counts, the style histogram, the headword-free
        share and cost. A sweep that stopped early still returns its outcome, with
        ``stopped_reason`` set.

    Raises:
        ValueError: If ``per_sense`` is outside ``[MIN_PER_SENSE, MAX_PER_SENSE]``.
    """
    if not MIN_PER_SENSE <= per_sense <= MAX_PER_SENSE:
        message = (
            f"per_sense must be between {MIN_PER_SENSE} (one per style) and "
            f"{MAX_PER_SENSE}, got {per_sense}"
        )
        raise ValueError(message)
    ids = list(lexeme_ids) if lexeme_ids is not None else sorted(store.iter_ids())
    tally = _Tally(on_sense)

    async def fill(lexeme_id: str) -> None:
        async with store.locked(lexeme_id):
            entry = store.read(lexeme_id)
            if entry is None:
                return
            needs_write, halted = await _fill_entry(entry, runner, per_sense, tally)
            if needs_write:
                store.write(entry)
            if halted is not None:
                await tally.entry(changed=needs_write)
                raise halted
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
    _LOG.info("queries_complete", **result.as_dict())
    return result
