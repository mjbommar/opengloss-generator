"""Workflow 12 — "X vs Y": the paragraph that says how two linked terms actually differ (D-57).

The store asserts 8.5M typed edges and explains none of them. A ``synonym`` edge from
*abseil* to *rappel* says the two words mean the same thing, which is the one claim a
reader never needs help with; what a reader wants — and what a retrieval model has no way
to learn from an edge list — is the sentence that says *when you would write one and not
the other*. That sentence exists nowhere in the resource. Every other prose the pipeline
writes is about one sense in isolation: a gloss, an example, an encyclopedia passage. This
is the only text in the project whose subject is a **pair**.

That makes it two useful things at once, which is why it is worth paying for:

* **pretraining text with an unusual shape.** A contrast paragraph is discriminative prose
  — "*rappel* is the word an American climber uses and *abseil* the word a British one
  does; the technique is identical" — and there is very little of it per token anywhere.
* **an explanation of a hard negative.** ``export-triples`` (F3) mines exactly these edges
  for hard negatives: the sense a retriever is most likely to confuse with the right one
  is its synonym or its confusable. The contrast paragraph is the human-readable statement
  of *why* that negative is a negative, which is what makes a mined triple auditable.

One call per entry, not per edge
--------------------------------

The prompt lists up to :data:`MAX_EDGES_PER_CALL` of the entry's eligible pairs, each as
``[relation, A term, A gloss, A example, B term, B gloss, B example]``, and asks for one
paragraph per pair. Eight paragraphs against one ~2K-token cached instruction prefix costs
a fraction of eight calls that each re-send it, and the model writing pair 3 can see that
pair 1 was about the same headword — which is what stops eight paragraphs about *vow* all
opening the same way. Edges past the cap are not lost; they are simply not in this sweep's
digest, and the next sweep buys them (see *Idempotence* below).

One contrast per undirected pair
--------------------------------

``graph_hygiene`` reciprocates symmetric relations, so a synonym pair that is fully
resolved and fully in the store shows up **twice**: once on each end. Writing a paragraph
on both ends would double the bill for one fact and leave two paragraphs to disagree with
each other. So an edge is *owned* by exactly one end, and the rule is lexicographic: the
end whose **sense id sorts smaller** owns the pair (sense ids begin with the lexeme id, so
this is "the lexicographically smaller lexeme id" with a deterministic tie-break for two
senses of one entry). The far end skips it and counts it as deferred.

The ownership test is deliberately conditional on the far side actually carrying the
reciprocal. An edge whose far side does *not* point back is owned by whichever end asserts
it, however its id sorts — otherwise a one-directional edge would be deferred to an end
that will never look at it, and the pair would never be explained at all.

Reading the far side
--------------------

The far sense's gloss and one example come from a **separate, short, lock-free read** of
the target entry, memoised per sweep — the same discipline ``relation_hygiene._target_gloss``
and ``content_hygiene`` follow. It is a read for prompt context and is never written back
from, so the rule that matters (D-31: no entry is written from a read taken outside its own
lock) is untouched, and no handler ever holds two entry locks at once.

An edge whose target is unresolved (``target.sense_id is None``), or whose target lexeme is
not in this store, or whose target sense has been retired, is skipped and counted. There is
nothing to contrast against but a surface form, and a paragraph written from a bare term is
a guess.

Verdicts are recorded, never acted on
-------------------------------------

Writing the paragraph forces the model to look hard at whether the two senses are related
the way the edge claims, so the answer carries a
:class:`~opengloss_generator.schema.ContrastVerdict` alongside the prose for free. It is
**stored and counted, and nothing else**: D-50 gives relation edits to ``relation_hygiene``,
and a stage whose job is prose has no business deleting a relation on the strength of a
by-product. ``related_differently`` and ``unrelated`` appear in the sweep summary so a
human — or a later relation-hygiene run — can go and look.

Acceptance
----------

Deterministic, per paragraph, first failure wins, and no retries: a rejected paragraph is
counted by reason and dropped, exactly as ``workflows/examples.py`` argues for a stage that
buys many interchangeable outputs per call. The checks are

* one non-empty block of prose once markdown is stripped;
* between :data:`WORD_FLOOR` and :data:`WORD_CEILING` words — the 60-120 the instructions
  ask for, with slack, because a 20-word answer is a label and a 300-word one is an essay;
* it **names both terms** (:func:`~opengloss_generator.spans.find_span` over each term and
  its rule-based forms). A paragraph about two words that never mentions one of them is not
  a contrast, and this is the cheapest possible proxy for the failure this stage exists to
  avoid;
* it does not **quote either gloss verbatim** (normalised containment, glosses of at least
  :data:`_MIN_QUOTABLE_GLOSS_WORDS` words only). Restating the two definitions side by side
  is precisely the non-answer here, and a paragraph that swallows a gloss whole is the
  detectable end of it.

Idempotence (D-47, with one deliberate difference)
--------------------------------------------------

The sentinel is ``contrasts:<digest>;attempts=<n>`` on a zero-cost provenance record, where
the digest is taken over the sorted keys of the edges that are **still outstanding** — each
key being the edge id plus the digest of *both* glosses, so a rewritten gloss on either end
changes it.

The difference from ``relation_hygiene``'s marker is that the attempt counter **resets when
the digest changes** instead of accumulating. There, a changed digest means "the set I
judged has changed, buy one more opinion", and accumulating is what bounds the entry's
total spend. Here a changed digest means *progress*: the outstanding set shrank because
paragraphs were stored, or grew because an edge resolved. Accumulating would make the
2-attempt bound a cap on how many sweeps an entry with many edges ever gets, and an entry
with 30 pairs would silently stop at 16. Resetting leaves the bound doing the job D-47
gives it: an entry whose outstanding set is *unchanged* after a call — every paragraph
rejected — gets exactly one more attempt and is then left alone.

The consequence, stated plainly: a contrast is written once per edge and is not refreshed
when a gloss is later rewritten. ``Lexeme.contrast_for`` keys on the edge id alone, one
contrast per edge is the schema's uniqueness rule (D-62), and a refresh pass is not what
this plan asked for.
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
from opengloss_generator.errors import BudgetExceededError, GenerationError
from opengloss_generator.log import get_logger
from opengloss_generator.readability import strip_markdown, word_count
from opengloss_generator.runner import run_pool
from opengloss_generator.schema import (
    Contrast,
    ContrastVerdict,
    RelationType,
    Renditions,
    StageName,
    canonical_rendition,
)

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from opengloss_generator.schema import Edge, Example, Lexeme, Provenance
    from opengloss_generator.stages import StageRunner
    from opengloss_generator.store import LexemeStore

__all__ = [
    "CONTRAST_INSTRUCTIONS",
    "DEFAULT_KINDS",
    "MARKER_PREFIX",
    "MAX_ATTEMPTS",
    "MAX_EDGES_PER_CALL",
    "ContrastsOutcome",
    "ContrastsPlan",
    "RejectReason",
    "plan_contrasts",
    "run_contrasts",
]

_LOG = get_logger(__name__)

#: Prefix of this workflow's D-47 sentinel. The full note is
#: ``contrasts:<digest>;attempts=<n>`` — see the module docstring for what the digest is
#: taken over and why the attempt counter resets rather than accumulates.
MARKER_PREFIX = "contrasts"

#: Separates the outstanding-edge digest from the attempt count inside a marker note.
_ATTEMPTS_SEPARATOR = ";attempts="

#: How many calls one entry gets on an *unchanged* outstanding set before it is left alone
#: (D-47's bound). Two: the second attempt is worth buying because a rejected paragraph is
#: usually a length or a naming miss rather than a settled inability, and a third is not.
MAX_ATTEMPTS = 2

#: Pairs put to the model in one call. Eight paragraphs is ~1,000 output tokens against a
#: single cached instruction prefix; more than that and one answer starts to risk the
#: stage's ``max_tokens``, and the entries with more pairs than this are rare enough that
#: buying their remainder on the next sweep is cheaper than raising the ceiling for all.
MAX_EDGES_PER_CALL = 8

#: The relation types a contrast is worth writing for. Exactly the three where the two ends
#: are *close enough to be confused*: two synonyms, two poles of one axis, or two words a
#: writer actually mixes up. A hypernym contrast ("a spaniel is a kind of dog") is a
#: restatement of the edge, not a discrimination.
DEFAULT_KINDS: tuple[RelationType, ...] = (
    RelationType.SYNONYM,
    RelationType.ANTONYM,
    RelationType.CONFUSABLE_WITH,
)

#: The word band the instructions ask for.
TARGET_MIN_WORDS = 60
TARGET_MAX_WORDS = 120

#: The band actually enforced: the asked-for one with slack, because the point of the check
#: is to refuse a label or an essay, not to police a model's word count to the digit.
WORD_FLOOR = 45
WORD_CEILING = 160

#: A gloss shorter than this is not treated as quotable: "a large bird" appearing inside a
#: paragraph about large birds is a coincidence, not a restatement.
_MIN_QUOTABLE_GLOSS_WORDS = 6

#: Shown in place of an example for a sense that has none.
_NO_EXAMPLE = "(none)"

#: Everything but letters, digits and single spaces, removed before a paragraph is compared
#: against a gloss for verbatim containment.
_NORMALISE_RE = re.compile(r"[^a-z0-9 ]+")
_WHITESPACE_RE = re.compile(r"\s+")

#: How many progress lines a long sweep emits, matching every other sweep in the project.
PROGRESS_EVERY = 500


class RejectReason(StrEnum):
    """Why one drafted paragraph was not stored.

    There is no retry, so these counts are the workflow's feedback loop: a reason that
    dominates a sweep is what the next change to :data:`CONTRAST_INSTRUCTIONS` has to aim
    at.
    """

    #: The answer named a pair that was not asked about, or named one twice.
    UNWANTED = "unwanted"
    #: Empty once markdown was stripped from it.
    EMPTY = "empty"
    #: Fewer than :data:`WORD_FLOOR` words: a label, not a paragraph.
    TOO_SHORT = "too_short"
    #: More than :data:`WORD_CEILING` words.
    TOO_LONG = "too_long"
    #: Never names the entry's own headword.
    SOURCE_ABSENT = "source_absent"
    #: Never names the term on the far end.
    TARGET_ABSENT = "target_absent"
    #: Quotes one of the two glosses verbatim instead of discriminating between them.
    GLOSS_COPY = "gloss_copy"


@dataclass(frozen=True, slots=True)
class ContrastsPlan:
    """What one entry would cost this workflow, computed without a model call.

    Attributes:
        pairs: How many pairs would be written about in the next call — the outstanding
            set, capped at :data:`MAX_EDGES_PER_CALL`. ``0`` when the entry is not due.
        outstanding: How many pairs are outstanding in total, cap ignored, so a plan can
            say how much of an entry one sweep would leave behind.
        skipped_unresolved: Eligible-by-type edges whose target is not resolved to a sense.
        skipped_no_target: Eligible-by-type edges whose target sense is not in this store.
        deferred: Eligible edges owned by the far end of the pair (see the module
            docstring); they cost this entry nothing and are written elsewhere.
    """

    pairs: int = 0
    outstanding: int = 0
    skipped_unresolved: int = 0
    skipped_no_target: int = 0
    deferred: int = 0

    @property
    def due(self) -> bool:
        """Return whether this entry would cost anything at all."""
        return self.pairs > 0


@dataclass(slots=True)
class ContrastsOutcome:
    """What one :func:`run_contrasts` sweep did across the store."""

    entries_scanned: int = 0
    entries_changed: int = 0
    calls: int = 0
    paragraphs_generated: int = 0
    contrasts_stored: int = 0
    rejected_by_reason: dict[str, int] = field(default_factory=dict)
    verdicts: dict[str, int] = field(default_factory=dict)
    edges_skipped_unresolved: int = 0
    edges_skipped_no_target: int = 0
    edges_deferred_to_far_side: int = 0
    edges_over_cap: int = 0
    cost_usd: float = 0.0
    stopped_reason: str | None = None

    @property
    def rejected(self) -> int:
        """Return how many drafted paragraphs were rejected, for any reason."""
        return sum(self.rejected_by_reason.values())

    def as_dict(self) -> dict[str, object]:
        """Return a JSON-serialisable view for the CLI run summary."""
        return {
            "entries_scanned": self.entries_scanned,
            "entries_changed": self.entries_changed,
            "calls": self.calls,
            "paragraphs_generated": self.paragraphs_generated,
            "contrasts_stored": self.contrasts_stored,
            "rejected": self.rejected,
            "rejected_by_reason": dict(sorted(self.rejected_by_reason.items())),
            "verdicts": dict(sorted(self.verdicts.items())),
            "edges_skipped_unresolved": self.edges_skipped_unresolved,
            "edges_skipped_no_target": self.edges_skipped_no_target,
            "edges_deferred_to_far_side": self.edges_deferred_to_far_side,
            "edges_over_cap": self.edges_over_cap,
            "cost_usd": round(self.cost_usd, 6),
            "stopped_reason": self.stopped_reason,
        }


# --------------------------------------------------------------------------------------
# The contract and instructions (module-private, as ``examples.py`` argues for)
# --------------------------------------------------------------------------------------
#
# ``contracts.py`` and ``prompts.py`` hold the public prompt surface of the stages that
# every other workflow calls. This one is kept here for the reason ``examples.py`` gives
# for its sense-fit call: three sibling retrieval-data features are being built
# concurrently in separate worktrees against the same two shared files, and an
# append-only module cannot conflict with any of them.


class _DraftContrast(BaseModel):
    """One "X vs Y" paragraph and the verdict written alongside it."""

    model_config = ConfigDict(
        extra="forbid", validate_by_name=True, validate_by_alias=True, serialize_by_alias=True
    )

    pair_ref: Annotated[int, Field(ge=1, description="The number the pair was listed under.")]
    text: Annotated[str, Field(min_length=1)]
    verdict: ContrastVerdict


class _DraftContrasts(BaseModel):
    """Every paragraph asked for in one call, in any order."""

    model_config = ConfigDict(
        extra="forbid", validate_by_name=True, validate_by_alias=True, serialize_by_alias=True
    )

    contrasts: Annotated[list[_DraftContrast], Field(min_length=1)]


#: Static, byte-stable and comfortably past the provider's 1,024-token cache floor, so a
#: sweep pays the full prefix once and a tenth of it thereafter — the same discipline every
#: other stage's instructions follow.
CONTRAST_INSTRUCTIONS = f"""\
You are writing usage notes for a dictionary. Each note is about a PAIR of terms that the \
dictionary has linked to each other, and its whole job is to tell a reader what separates \
them. You are given one headword and a numbered list of pairs. Write one paragraph for \
each pair, and return a verdict about each pair alongside it.

WHAT A GOOD PARAGRAPH DOES. It answers the question a reader actually has, which is never \
"what do these two mean?" — the definitions are already on the page, directly above your \
note — but "which one do I use here, and what changes if I use the other?" A reader who \
finishes your paragraph should be able to look at a sentence containing one of the two \
terms and say whether the other would have done, and what it would have cost. If your \
paragraph would still be true with the two terms swapped, it is not a contrast.

WHAT A BAD PARAGRAPH DOES. It restates the two definitions one after the other and joins \
them with "whereas". That is the failure mode of this task and it is worth naming twice: \
you are NOT summarising the two senses. Assume the reader has just read both definitions \
and understood them, and start from there. Do not quote either definition. Do not open by \
saying that the two words are similar, related, or often confused — the pair is on the page \
because that is already known.

WHERE THE DIFFERENCE USUALLY LIVES. When two words genuinely overlap, the thing that \
separates them is almost always one of these, and naming which one is most of the work:

  - REGISTER AND AUDIENCE. One is what a specialist writes and the other what a passenger \
reads; one belongs in a contract and the other in a text message.
  - REGION. One is the British word and one the American, or one is the word used in one \
industry and not another.
  - INTENSITY OR SCALE. Both name the same thing but one names more of it — a scratch and \
a wound, a preference and a craving.
  - CONNOTATION. Both are accurate and one is an insult, or one is warm and one is cold.
  - WHAT THEY COMBINE WITH. One takes a person as its object and the other a thing; one is \
used of money and the other of time. Collocation is often the only real difference and it \
is a completely legitimate answer.
  - TYPICAL SITUATION. One is what happens in a court and the other in a kitchen.
  - GRAMMAR. One is transitive and one is not; one is countable and one is a mass noun.

Name the one that matters and be concrete about it. A paragraph that says the two differ \
"in nuance" or "in emphasis" and stops has said nothing.

BY RELATION TYPE.

  SYNONYM. Say when each is the right word. Two words that are exactly interchangeable in \
every context are vanishingly rare; if you genuinely believe this pair is one of them, say \
so plainly and say what the accident of history was that left the language with both. \
Otherwise pick the axis above that separates them and make the reader able to choose.

  ANTONYM. Say what AXIS the two sit at the ends of, in so many words — hot and cold sit on \
temperature, guilty and innocent on legal responsibility — and then say something a reader \
does not already know about that axis: whether there is a middle (warm; neither guilty nor \
innocent, merely unproven), whether the opposition holds in every sense of the two words or \
only one, and whether one of the pair is the unmarked, default member (you ask how *tall* \
someone is, not how *short*).

  CONFUSABLE_WITH. Say what causes the confusion — a shared root, a near-identical spelling, \
overlapping subject matter — and then say the one test a reader can apply to pick the right \
one. This is the pair where a concrete rule of thumb is worth more than any amount of \
description.

FORM. One paragraph of plain continuous prose per pair, {TARGET_MIN_WORDS} to \
{TARGET_MAX_WORDS} words. No bullet points, no headings, no markdown of any kind, no \
numbering inside the paragraph. Neutral register, plain language: this is the note that \
appears under the entry, not an academic aside. NAME BOTH TERMS explicitly, at least once \
each, in ordinary running text — do not write "the former" and "the latter" throughout, and \
do not write about them as "the first term" and "the second term". Write about the words as \
words; italics are unavailable to you, so simply use them in sentences.

DO NOT INVENT FACTS. Everything you say must be something you are confident is true of \
English usage. Do not attribute a term to a region, a period or a profession you are \
guessing at. If the only honest thing you can say about a pair is a single real difference, \
say that one thing and fill the paragraph by showing it — a short concrete illustration of \
each term used well is always better than a second, invented difference.

THE VERDICT. Alongside each paragraph return exactly one of:

  related_as_typed — the two terms really do stand in the relation the pair is labelled \
with. Use this whenever the link is defensible, even if you would have typed it slightly \
differently; a loose synonym is still a synonym.

  related_differently — the two terms are genuinely related, but not in the way the label \
says. A pair labelled antonym that is really a broader/narrower pair; a pair labelled \
synonym where one term is a kind of the other. Write the paragraph anyway: say what the \
real relation is and how the two differ.

  unrelated — the two senses have no useful lexical relation at all, in which case the link \
is an error somewhere upstream. This is the rare answer. Do not use it because the link is \
weak or imprecise; use it only when a reader shown the two definitions would not see a \
connection. Still write a paragraph: say what each term is actually about and why the two \
do not belong together.

The verdict is recorded for a later human review and nothing in the dictionary changes \
because of it, so answer it honestly rather than defensively.

WORKED EXAMPLE. Given the pair "synonym: abseil [verb] vs rappel [verb]", a good paragraph \
is: "The two verbs name one technique, and the choice between them is almost purely \
geographic. Abseil, taken straight from German, is what a British, Irish or Australian \
climber says and what British guidebooks and mountain-rescue reports print. Rappel, by way \
of French, is standard in North America and dominates climbing instruction written there. \
Neither carries any technical distinction: nobody abseils differently from the way they \
rappel. The only trap is a stylistic one, since mixing the two inside a single manual or \
route description reads as carelessness to climbers on either side of the Atlantic."

Answer for every pair you were given, identified by the number it was listed under."""


def _build_contrasts_prompt(headword: str, refs: Sequence[_EdgeRef]) -> str:
    """Return the volatile half of the contrast prompt.

    One numbered item per pair, each opening on a single indented line that names the
    relation and both terms — the shape :func:`~opengloss_generator.prompts.build_examples_prompt`
    and ``relation_hygiene`` both use, so the answer can refer back by number — and then
    four deeper-indented lines carrying the two glosses and the two examples.

    Args:
        headword: The entry's surface form.
        refs: The pairs to ask about, in the order they are listed and numbered.

    Returns:
        The per-call prompt body.
    """
    lines = [f"Headword: {headword}", f"Pairs ({len(refs)}):"]
    for ref in refs:
        lines.append(
            f"  {ref.ref}. {ref.relation.value}: "
            f"{ref.source_term} [{ref.source_pos}] vs {ref.target_term} [{ref.target_pos}]"
        )
        lines.append(f"     {ref.source_term} means: {ref.source_gloss}")
        lines.append(f"     {ref.source_term} in use: {ref.source_example}")
        lines.append(f"     {ref.target_term} means: {ref.target_gloss}")
        lines.append(f"     {ref.target_term} in use: {ref.target_example}")
    lines.append(
        f"Write one paragraph of {TARGET_MIN_WORDS}-{TARGET_MAX_WORDS} words for every pair "
        "above, plus its verdict."
    )
    return "\n".join(lines)


# --------------------------------------------------------------------------------------
# Eligibility, ownership, and the far-side read
# --------------------------------------------------------------------------------------


@dataclass(slots=True)
class _SenseView:
    """The far end of one pair, as the prompt needs it.

    Attributes:
        term: The target's surface form — the headword of the entry it lives in, which is
            what a paragraph should name, rather than the relation's own possibly inflected
            ``target.term``.
        pos: Its part of speech.
        gloss: Its canonical gloss.
        example: One of its examples, or :data:`_NO_EXAMPLE`.
        reciprocates: Whether the far sense points back at the near sense with a relation
            of the same type — what decides who owns the pair.
    """

    term: str
    pos: str
    gloss: str
    example: str
    reciprocates: bool


@dataclass(slots=True)
class _EdgeRef:
    """One pair put to the model, and everything the prompt and the store need about it.

    Attributes:
        ref: The 1-based number the pair is listed under, and the number the answer refers
            back to. Never an edge id — the model cannot invent a ref it was not shown.
        edge_id: The derived edge id (:func:`~opengloss_generator.identity.edge_id`), which
            is the contrast's identity in the store.
        relation: The edge's type.
        source_term: The entry's headword.
        source_pos: The asserting sense's part of speech.
        source_gloss: The asserting sense's canonical gloss.
        source_example: One of the asserting sense's examples, or :data:`_NO_EXAMPLE`.
        target_sense_id: The resolved far sense, stored on the contrast.
        target_term: The far entry's headword.
        target_pos: The far sense's part of speech.
        target_gloss: The far sense's canonical gloss.
        target_example: One of the far sense's examples, or :data:`_NO_EXAMPLE`.
    """

    ref: int
    edge_id: str
    relation: RelationType
    source_term: str
    source_pos: str
    source_gloss: str
    source_example: str
    target_sense_id: str
    target_term: str
    target_pos: str
    target_gloss: str
    target_example: str

    @property
    def key(self) -> str:
        """Return the digest key of this pair: its edge and the state of both glosses.

        A rewritten gloss on either end changes the key, so the sentinel that carries it
        stops matching and the entry earns a fresh attempt (see the module docstring).
        """
        return f"{self.edge_id}|{_digest(self.source_gloss)}|{_digest(self.target_gloss)}"


def _digest(text: str) -> str:
    """Return sixteen hex characters of SHA-256 over one string.

    SHA-256 rather than :func:`hash` because the value is written to disk and compared
    across processes.
    """
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _set_digest(keys: Iterable[str]) -> str:
    """Return a stable short hash of a set of pair keys, order-independently."""
    return _digest("\n".join(sorted(keys)))


def _first_example(examples: Renditions[Example]) -> str:
    """Return one example's text from a rendition set, or :data:`_NO_EXAMPLE`.

    The canonical ``(neutral, plain)`` example is preferred, as everywhere else in the
    project; any example will do when there is no canonical one, because this text is
    prompt context rather than something the answer is checked against.
    """
    canonical = examples.canonical()
    chosen = canonical if canonical is not None else next(iter(examples), None)
    if chosen is None or not chosen.content.text.strip():
        return _NO_EXAMPLE
    return " ".join(chosen.content.text.split())


def _far_side(
    store: LexemeStore,
    target_sense_id: str,
    source_sense_id: str,
    relation: RelationType,
    cache: dict[tuple[str, str, str], _SenseView | None],
) -> _SenseView | None:
    """Read the far end of one pair, without taking its lock.

    A read for prompt context only, never one this pass then writes back from — the same
    thing ``relation_hygiene._target_gloss`` does, and the reason no handler here ever holds
    two entry locks at once (D-31). Memoised per sweep, keyed on the near sense and the
    relation as well as the far sense, because ``reciprocates`` is a fact about the pair
    rather than about the far sense alone.

    Args:
        store: The store to read from.
        target_sense_id: The resolved far sense.
        source_sense_id: The near sense, for the reciprocity test.
        relation: The edge's type, for the reciprocity test.
        cache: The sweep's memo.

    Returns:
        The far side, or ``None`` when its entry is missing from this store, its sense is
        gone, or its sense has been retired.
    """
    memo_key = (target_sense_id, source_sense_id, relation.value)
    if memo_key in cache:
        return cache[memo_key]
    entry = store.read(target_sense_id.rsplit(":", 2)[0])
    view: _SenseView | None = None
    if entry is not None:
        for pos_entry, sense, sid in entry.iter_senses():
            if sid != target_sense_id or sense.retired:
                continue
            view = _SenseView(
                term=entry.headword,
                pos=pos_entry.pos.value,
                gloss=" ".join(strip_markdown(sense.canonical_gloss()).split()),
                example=_first_example(sense.examples),
                reciprocates=any(
                    r.type is relation and r.target.sense_id == source_sense_id
                    for r in sense.relations
                ),
            )
            break
    cache[memo_key] = view
    return view


@dataclass(slots=True)
class _Eligibility:
    """One entry's pairs, split into what is due and what was skipped and why.

    Attributes:
        refs: The outstanding pairs this entry owns, in document order and **not** yet
            capped, each already numbered from one.
        skipped_unresolved: Edges of an eligible type whose target has no ``sense_id``.
        skipped_no_target: Edges whose target sense is not readable in this store.
        deferred: Edges owned by the far end of the pair.
        done: Owned edges that already carry a stored contrast.
    """

    refs: list[_EdgeRef] = field(default_factory=list)
    skipped_unresolved: int = 0
    skipped_no_target: int = 0
    deferred: int = 0
    done: int = 0


def _owns(edge: Edge, far: _SenseView, target_sense_id: str) -> bool:
    """Return whether this end of a pair is the one that writes its contrast.

    The smaller sense id owns a reciprocated pair; a pair the far side does not reciprocate
    is owned by the end that asserts it, whatever the ids sort like, because otherwise
    nobody would write it (see the module docstring).
    """
    if not far.reciprocates:
        return True
    return edge.source_sense <= target_sense_id


def _eligible(
    entry: Lexeme,
    store: LexemeStore,
    kinds: Sequence[RelationType],
    cache: dict[tuple[str, str, str], _SenseView | None],
) -> _Eligibility:
    """Return the pairs one entry is due to write about, and the tally of what it is not.

    Args:
        entry: The entry to inspect. Never mutated.
        store: The store, read (lock-free) only for far-side prompt context.
        kinds: The relation types in scope for this sweep.
        cache: The sweep's far-side memo.

    Returns:
        The split, with :attr:`_Eligibility.refs` numbered from one in document order —
        the order the model is shown them in and refers to them by.
    """
    wanted = set(kinds)
    stored = {contrast.edge_id for contrast in entry.contrasts}
    glosses = {sid: sense for _, sense, sid in entry.iter_senses()}
    result = _Eligibility()
    for edge in entry.edges():
        if edge.relation not in wanted:
            continue
        if edge.target_sense is None:
            result.skipped_unresolved += 1
            continue
        far = _far_side(store, edge.target_sense, edge.source_sense, edge.relation, cache)
        if far is None:
            result.skipped_no_target += 1
            continue
        if not _owns(edge, far, edge.target_sense):
            result.deferred += 1
            continue
        if edge.edge_id in stored:
            result.done += 1
            continue
        sense = glosses[edge.source_sense]
        result.refs.append(
            _EdgeRef(
                ref=len(result.refs) + 1,
                edge_id=edge.edge_id,
                relation=edge.relation,
                source_term=entry.headword,
                source_pos=edge.pos.value,
                source_gloss=" ".join(strip_markdown(sense.canonical_gloss()).split()),
                source_example=_first_example(sense.examples),
                target_sense_id=edge.target_sense,
                target_term=far.term,
                target_pos=far.pos,
                target_gloss=far.gloss,
                target_example=far.example,
            )
        )
    return result


# --------------------------------------------------------------------------------------
# The D-47 marker
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Marker:
    """The most recent sentinel this workflow left on an entry.

    Attributes:
        digest: The outstanding-pair-set hash the marker was written for.
        attempts: How many calls have been made against *that* set, this one included.
    """

    digest: str
    attempts: int


def _latest_marker(entry: Lexeme) -> _Marker | None:
    """Return the last sentinel this workflow wrote on an entry, parsed.

    Provenance ids are handed out in insertion order and never reused, so the last matching
    record in the table is the most recently written one.
    """
    latest: _Marker | None = None
    for record in entry.provenance.values():
        note = record.note or ""
        if not note.startswith(f"{MARKER_PREFIX}:"):
            continue
        digest, _, attempts = note[len(MARKER_PREFIX) + 1 :].partition(_ATTEMPTS_SEPARATOR)
        latest = _Marker(digest, int(attempts) if attempts.isdigit() else 1)
    return latest


def _attempt_number(entry: Lexeme, refs: Sequence[_EdgeRef]) -> int | None:
    """Return which attempt is due on an entry, or ``None`` if none is.

    Unlike ``relation_hygiene``'s equivalent the counter **resets** on a changed digest;
    the module docstring says why at length. In short: there, a changed digest means a
    second opinion is being bought and accumulating bounds the spend; here it means the
    outstanding set moved, which is progress, and the bound is there to stop paying for an
    entry whose paragraphs keep being refused.

    Args:
        entry: The entry being considered.
        refs: Its outstanding pairs, uncapped.

    Returns:
        The 1-based attempt number, or ``None`` when the entry must be skipped — which is
        also the "do not bill this" signal for the caller.
    """
    if not refs:
        return None
    marker = _latest_marker(entry)
    if marker is None or marker.digest != _set_digest(ref.key for ref in refs):
        return 1
    if marker.attempts >= MAX_ATTEMPTS:
        return None
    return marker.attempts + 1


def _marker_note(refs: Iterable[_EdgeRef], attempt: int) -> str:
    """Return the sentinel to stamp for an attempt, in D-47's form."""
    return f"{MARKER_PREFIX}:{_set_digest(ref.key for ref in refs)}{_ATTEMPTS_SEPARATOR}{attempt}"


def plan_contrasts(
    entry: Lexeme, store: LexemeStore, kinds: Sequence[RelationType] = DEFAULT_KINDS
) -> ContrastsPlan:
    """Return what this entry would cost, without calling a model.

    The planning view ``contrasts --dry-run`` prices, and the same gate the sweep applies:
    the pair counts are exact — they come from the entry's own edges and the store — and
    only the money is estimated, by the caller, from the stage's measured per-call means.

    Args:
        entry: The entry to inspect. Never mutated.
        store: The store, read (lock-free) for far-side context.
        kinds: The relation types in scope.

    Returns:
        A :class:`ContrastsPlan`. ``due`` is ``False`` when the entry would cost $0.
    """
    split = _eligible(entry, store, kinds, {})
    attempt = _attempt_number(entry, split.refs)
    pairs = 0 if attempt is None else min(len(split.refs), MAX_EDGES_PER_CALL)
    return ContrastsPlan(
        pairs=pairs,
        outstanding=len(split.refs),
        skipped_unresolved=split.skipped_unresolved,
        skipped_no_target=split.skipped_no_target,
        deferred=split.deferred,
    )


# --------------------------------------------------------------------------------------
# Acceptance
# --------------------------------------------------------------------------------------


def _normalise(text: str) -> str:
    """Return a comparison key: lowercased, letters, digits and single spaces only."""
    lowered = _NORMALISE_RE.sub(" ", text.lower())
    return _WHITESPACE_RE.sub(" ", lowered).strip()


def _quotes(paragraph: str, gloss: str) -> bool:
    """Return whether a paragraph contains one gloss verbatim, modulo punctuation."""
    if word_count(gloss) < _MIN_QUOTABLE_GLOSS_WORDS:
        return False
    normalised = _normalise(gloss)
    return bool(normalised) and normalised in _normalise(paragraph)


def _shape_reason(text: str) -> RejectReason | None:
    """Return why a paragraph is not a paragraph of the right length, or ``None``.

    Args:
        text: The markdown-stripped, whitespace-collapsed candidate.

    Returns:
        The first shape defect found, or ``None`` when there is none.
    """
    if not text:
        return RejectReason.EMPTY
    words = word_count(text)
    if words < WORD_FLOOR:
        return RejectReason.TOO_SHORT
    if words > WORD_CEILING:
        return RejectReason.TOO_LONG
    return None


def _content_reason(text: str, ref: _EdgeRef) -> RejectReason | None:
    """Return why a paragraph is not *about the pair*, or ``None``.

    Args:
        text: The markdown-stripped, whitespace-collapsed candidate.
        ref: The pair it was written for.

    Returns:
        The first content defect found, or ``None`` when there is none.
    """
    if spans.find_span(text, ref.source_term, spans.generate_forms(ref.source_term)) is None:
        return RejectReason.SOURCE_ABSENT
    if spans.find_span(text, ref.target_term, spans.generate_forms(ref.target_term)) is None:
        return RejectReason.TARGET_ABSENT
    if _quotes(text, ref.source_gloss) or _quotes(text, ref.target_gloss):
        return RejectReason.GLOSS_COPY
    return None


def _judge(text: str, ref: _EdgeRef) -> str | RejectReason:
    """Accept one drafted paragraph, or say why it was not kept.

    Cheapest-and-most-fundamental first, first failure wins: a paragraph that is both too
    short and never names its target is counted once, as too short, because that is the
    defect a prompt change would have to fix first.

    Args:
        text: The paragraph as the model returned it, before markdown stripping.
        ref: The pair it was written for.

    Returns:
        The cleaned paragraph, or the reason it was not kept.
    """
    cleaned = " ".join(strip_markdown(text).split())
    reason = _shape_reason(cleaned) or _content_reason(cleaned, ref)
    if reason is not None:
        return reason
    return cleaned


@dataclass(slots=True)
class _Accepted:
    """One paragraph that passed every check, ready to be stored."""

    ref: _EdgeRef
    text: str
    verdict: ContrastVerdict


def _sift(
    drafted: Sequence[_DraftContrast], refs: Sequence[_EdgeRef]
) -> tuple[list[_Accepted], dict[str, int]]:
    """Run every drafted paragraph past :func:`_judge`, in the order it was returned.

    Args:
        drafted: The paragraphs as returned.
        refs: The pairs that were asked about, indexed by their 1-based ref.

    Returns:
        ``(accepted paragraphs, rejection counts by reason)``.
    """
    by_ref = {ref.ref: ref for ref in refs}
    accepted: list[_Accepted] = []
    rejected: dict[str, int] = {}
    answered: set[int] = set()

    def count(reason: RejectReason) -> None:
        rejected[reason.value] = rejected.get(reason.value, 0) + 1

    for draft in drafted:
        ref = by_ref.get(draft.pair_ref)
        if ref is None or draft.pair_ref in answered:
            count(RejectReason.UNWANTED)
            continue
        answered.add(draft.pair_ref)
        verdict = _judge(draft.text, ref)
        if isinstance(verdict, RejectReason):
            count(verdict)
            continue
        accepted.append(_Accepted(ref=ref, text=verdict, verdict=draft.verdict))
    return accepted, rejected


# --------------------------------------------------------------------------------------
# The call, and applying it
# --------------------------------------------------------------------------------------


async def _write_contrasts(
    entry: Lexeme, refs: Sequence[_EdgeRef], runner: StageRunner
) -> tuple[list[_DraftContrast], Provenance, float] | None:
    """Make the one call for an entry, or return ``None`` if it failed.

    Args:
        entry: The entry being written for.
        refs: The pairs to ask about, already capped and numbered.
        runner: The stage runner.

    Returns:
        ``(drafted paragraphs, the call's provenance, its cost)``, or ``None``.

    Raises:
        BudgetExceededError: A budget stop is a run-level condition and propagates.
    """
    try:
        result = await runner.run(
            stage=StageName.CONTRASTS,
            output_type=_DraftContrasts,
            instructions=CONTRAST_INSTRUCTIONS,
            prompt=_build_contrasts_prompt(entry.headword, refs),
            prompt_version=prompts.PROMPT_VERSION,
        )
    except BudgetExceededError:
        raise
    except GenerationError as exc:
        _LOG.warning("contrasts_generation_failed", headword=entry.headword, error=str(exc))
        return None
    return list(result.output.contrasts), result.provenance, result.cost_usd


def _store_contrasts(
    entry: Lexeme, accepted: Sequence[_Accepted], provenance_id: str | None
) -> tuple[int, dict[str, int]]:
    """Append every surviving paragraph to the entry, and count its verdict.

    One contrast per edge is the schema's uniqueness rule (D-62), and the outstanding set
    already excluded every edge that carries one; the guard below is for the concurrent
    case where another pass stored the same edge between this entry's read and its write.

    Args:
        entry: The entry, mutated in place.
        accepted: The paragraphs to store.
        provenance_id: Key of the call's record in the entry's provenance table.

    Returns:
        ``(contrasts stored, verdict counts)``.
    """
    stored = 0
    verdicts: dict[str, int] = {}
    for item in accepted:
        if entry.contrast_for(item.ref.edge_id) is not None:
            continue
        entry.contrasts.append(
            Contrast(
                edge_id=item.ref.edge_id,
                target_sense_id=item.ref.target_sense_id,
                text=Renditions[str](root=[canonical_rendition(item.text)]),
                verdict=item.verdict,
                provenance_id=provenance_id,
            )
        )
        stored += 1
        verdicts[item.verdict.value] = verdicts.get(item.verdict.value, 0) + 1
    return stored, verdicts


async def _fill_entry(
    entry: Lexeme,
    *,
    store: LexemeStore,
    runner: StageRunner,
    kinds: Sequence[RelationType],
    cache: dict[tuple[str, str, str], _SenseView | None],
    tally: _Tally,
) -> bool:
    """Write and store one entry's contrasts, in place.

    Args:
        entry: The entry to fill, mutated in place.
        store: The store, read (lock-free) for far-side prompt context.
        runner: The stage runner.
        kinds: The relation types in scope.
        cache: The sweep's far-side memo.
        tally: The sweep tally.

    Returns:
        Whether the entry needs writing. Not the same as "a paragraph was stored": a call
        that produced nothing usable still leaves the sentinel to be persisted, or the next
        sweep pays for the same answer.

    Raises:
        BudgetExceededError: A budget stop is a run-level condition and propagates —
            before anything is written, so the entry is left exactly as it was found.
    """
    split = _eligible(entry, store, kinds, cache)
    await tally.edges(
        unresolved=split.skipped_unresolved,
        no_target=split.skipped_no_target,
        deferred=split.deferred,
    )
    attempt = _attempt_number(entry, split.refs)
    if attempt is None:
        return False
    asked = split.refs[:MAX_EDGES_PER_CALL]
    await tally.over_cap(len(split.refs) - len(asked))

    written = await _write_contrasts(entry, asked, runner)
    if written is None:
        # A call that failed outright writes no marker, so the entry is retried whole on
        # the next sweep — the convention every model call in this project follows.
        return False
    drafted, provenance, cost = written
    await tally.call(cost)

    accepted, rejected = _sift(drafted, asked)
    await tally.paragraphs(len(drafted), rejected)

    # The marker rides the call's own record, so one record is both "what this cost" and
    # "these pairs have been asked about", exactly as ``examples`` and ``example_hygiene``
    # do it. It is written even when nothing survived: the sentinel is the only thing
    # stopping the next sweep buying the same refused answer, and D-47's attempt counter
    # is what bounds that to one repeat.
    provenance_id = entry.add_provenance(
        provenance.model_copy(update={"note": _marker_note(asked, attempt)})
    )
    stored, verdicts = _store_contrasts(entry, accepted, provenance_id)
    await tally.stored(stored, verdicts)

    _LOG.info(
        "contrasts_written",
        headword=entry.headword,
        pairs=len(asked),
        attempt=attempt,
        generated=len(drafted),
        stored=stored,
        rejected=sum(rejected.values()),
        cost_usd=round(cost, 6),
    )
    return True


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
        self._result = ContrastsOutcome()

    @property
    def result(self) -> ContrastsOutcome:
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
                    "contrasts_progress",
                    entries_scanned=self._result.entries_scanned,
                    contrasts_stored=self._result.contrasts_stored,
                    cost_usd=round(self._result.cost_usd, 6),
                )

    async def call(self, cost_usd: float) -> None:
        """Record one completed model call and what it cost."""
        async with self._lock:
            self._result.calls += 1
            self._result.cost_usd += cost_usd

    async def edges(self, *, unresolved: int, no_target: int, deferred: int) -> None:
        """Record the edges one entry could not or need not write about."""
        async with self._lock:
            self._result.edges_skipped_unresolved += unresolved
            self._result.edges_skipped_no_target += no_target
            self._result.edges_deferred_to_far_side += deferred

    async def over_cap(self, count: int) -> None:
        """Record outstanding pairs left for the next sweep by :data:`MAX_EDGES_PER_CALL`."""
        async with self._lock:
            self._result.edges_over_cap += count

    async def paragraphs(self, generated: int, rejected: dict[str, int]) -> None:
        """Record one call's paragraphs and why any of them were refused."""
        async with self._lock:
            self._result.paragraphs_generated += generated
            for reason, count in rejected.items():
                self._result.rejected_by_reason[reason] = (
                    self._result.rejected_by_reason.get(reason, 0) + count
                )

    async def stored(self, count: int, verdicts: dict[str, int]) -> None:
        """Record one entry's stored contrasts and the verdicts they carry."""
        async with self._lock:
            self._result.contrasts_stored += count
            for verdict, number in verdicts.items():
                self._result.verdicts[verdict] = self._result.verdicts.get(verdict, 0) + number

    async def note_stop(self, reason: str) -> None:
        """Record why the sweep stopped early, keeping the first reason given."""
        async with self._lock:
            if self._result.stopped_reason is None:
                self._result.stopped_reason = reason


async def run_contrasts(
    store: LexemeStore,
    runner: StageRunner,
    *,
    lexeme_ids: Iterable[str] | None = None,
    kinds: Sequence[RelationType] = DEFAULT_KINDS,
    workers: int,
    stop_event: asyncio.Event | None = None,
) -> ContrastsOutcome:
    """Write an "X vs Y" paragraph for every eligible relation edge in the store (D-57).

    Args:
        store: The store to fill. Each entry is read, written for — including its one model
            call — and written back inside one hold of its own lock (D-31); the far side of
            each pair is read separately and without a lock, for prompt context only.
        runner: The stage runner, whose ``CONTRASTS`` policy selects the model.
        lexeme_ids: Ids to visit; defaults to every id in the store, sorted.
        kinds: The relation types in scope — ``--only-kinds`` on the command line.
        workers: Pool size.
        stop_event: Shared stop event; set by a budget stop, and honoured if the caller
            sets it from outside.

    Returns:
        A :class:`ContrastsOutcome` carrying counts, the verdict histogram, the rejection
        breakdown and cost. A sweep that stopped early still returns its outcome, with
        ``stopped_reason`` set.
    """
    ids = list(lexeme_ids) if lexeme_ids is not None else sorted(store.iter_ids())
    tally = _Tally()
    # One memo for the whole sweep. Its values are small views of far-side senses, and the
    # sweep is single-threaded asyncio between awaits, so no lock is needed to share it:
    # two workers racing on the same key both do the same read and store the same value.
    cache: dict[tuple[str, str, str], _SenseView | None] = {}

    async def fill(lexeme_id: str) -> None:
        async with store.locked(lexeme_id):
            entry = store.read(lexeme_id)
            if entry is None:
                return
            needs_write = await _fill_entry(
                entry, store=store, runner=runner, kinds=kinds, cache=cache, tally=tally
            )
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
    _LOG.info("contrasts_complete", **result.as_dict())
    return result
