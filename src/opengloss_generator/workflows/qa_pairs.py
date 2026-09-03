"""Workflow 12 — question/answer pairs grounded in one sense's own stored text (D-58).

Every other prose stage in this project writes *about* a word: a definition, a passage, a
sentence that uses it. This one writes the shape a reader-facing model is actually
evaluated on — a question and the answer to it — and it writes them from text the store
already holds rather than from the model's own knowledge. That distinction is the whole
design. A QA pair generated freely is a fact the pipeline cannot check and did not pay to
verify; a QA pair generated *from* a sense's canonical gloss, its example sentences, its
entry's encyclopedia passage and its etymology is a restatement of content that has
already been through the judge, the hygiene passes and the readability bands, and its
grounding can be checked for free.

Why one call per sense, not per entry
-------------------------------------

``workflows/examples.py`` makes one call per *entry* because its output has to
discriminate between senses: sense 2's sentences are better when the model has just read
sense 1's definition. Nothing here needs that. A question about sense 2 is answered out of
sense 2's own text, and showing the model the other senses would invite exactly the
failure the grounding check exists to catch — an answer that quietly borrows a fact from
the sense next door and then cites this one. So the unit of work is the sense, the prompt
carries that sense's material and nothing else from the inventory, and the seven pairs it
buys are seven independent rows.

The unit of *locking* is still the entry (D-31): the handler takes the entry's lock, reads
it, makes one call per live sense, applies what survived and writes it back inside the
same hold, exactly as every other sweep in the project does.

What the prompt carries, and how it is addressed
------------------------------------------------

Each piece of supplied text is labelled with an id, and the model is required to return,
per pair, the ids its answer is supported by. Three of the four id forms are the project's
derived ids:

* the canonical gloss — ``<sense_id>#neutral/plain``
  (:func:`~opengloss_generator.identity.rendition_id`);
* the entry's encyclopedia passage — ``<lexeme_id>:encyclopedia#neutral/plain``;
* nothing else at entry level is passed except the etymology summary, addressed
  ``<lexeme_id>:etymology`` — a *stage-local* id (see below).

The fourth is examples, and they are the awkward case. ``Lexeme.rendition_ids`` documents
why they have no derived id: several examples may share one ``(reading level, register)``
key, so a rendition id does not identify one of them — and the gloss at ``(neutral,
plain)`` would collide with an example at ``(neutral, plain)`` on the same sense, which is
the id form :func:`~opengloss_generator.identity.rendition_id` gives both. This stage
therefore addresses an example positionally, ``<sense_id>#ex<n>``, in the same family as
``#q<n>`` and ``#qa<n>``, and does so *here* rather than in ``identity.py``: an id form
with one consumer belongs next to that consumer, and F1/F3's exports are the second
consumer that would justify promoting it. The same reasoning covers ``:etymology``, which
is an owner id for a field that is not a rendition set at all.

Post-checks, all free
---------------------

The model is told to ground every answer and to cite what it used. Three deterministic
checks decide whether it did, and each rejection is counted by reason:

* **Citation.** ``grounded_in`` must be non-empty and must name only ids that were
  actually supplied. An answer citing ``abseil:noun:0#neutral/plain`` on a verb sense's
  call is not a mis-formatted citation, it is a fact from somewhere else.
* **Overlap.** The answer must share at least
  :data:`MIN_SHARED_CONTENT_WORDS` content words — lowercase alphabetic tokens, minus a
  small stopword list and minus one-and-two-letter tokens — with the text of the
  renditions it cited. This is a floor, not a similarity score: it catches the answer that
  cites a passage it did not read, and it deliberately does not punish paraphrase, which
  is what a good answer to a *reasoning* or *hypothetical* question mostly is.
* **Distinctness.** Two pairs asking the same normalised question are one row twice
  (``Sense._questions_are_distinct``), so the second is dropped — against the pairs
  accepted earlier in the same answer *and* against the pairs the sense already holds.

There is no retry. A dropped pair costs the sweep nothing to replace, because the next
sense's call is already buying seven more; the reason counters are the feedback loop, in
the same spirit as ``examples.py``'s.

Idempotence
-----------

D-47's marker, per sense, on the generating call's own zero-cost provenance record:
``qa_pairs:<sense_id>:<digest>;attempts=<n>``. The digest is over the ids of the text that
was supplied *plus the canonical gloss text itself* — the gloss's rendition id does not
change when the gloss is rewritten, and a rewritten gloss is exactly the case where the
old pairs are stale. A rerun over an unchanged sense costs $0; a sense that gains an
example, gains an encyclopedia passage, or has its gloss rewritten earns one more call, up
to :data:`MAX_ATTEMPTS` of them, after which the sense is left alone rather than billed a
third time for the same answer.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Annotated

from pydantic import BaseModel, ConfigDict, Field

from opengloss_generator.errors import BudgetExceededError, GenerationError
from opengloss_generator.identity import encyclopedia_owner_id, rendition_id
from opengloss_generator.log import get_logger
from opengloss_generator.prompts import PROMPT_VERSION
from opengloss_generator.readability import strip_markdown
from opengloss_generator.runner import run_pool
from opengloss_generator.schema import (
    CANONICAL_KEY,
    Difficulty,
    QAPair,
    QuestionType,
    StageName,
    normalise_query_text,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Iterable, Sequence

    from opengloss_generator.schema import Lexeme, POSEntry, Sense
    from opengloss_generator.stages import StageRunner
    from opengloss_generator.store import LexemeStore

__all__ = [
    "MARKER_PREFIX",
    "MAX_ATTEMPTS",
    "MIN_SHARED_CONTENT_WORDS",
    "DropReason",
    "QACallRecord",
    "QAPairsOutcome",
    "QAPairsPlan",
    "plan_qa_pairs",
    "run_qa_pairs",
]

_LOG = get_logger(__name__)

#: Prefix of this stage's idempotence sentinel. The full note is
#: ``qa_pairs:<sense_id>:<digest>;attempts=<n>`` — per *sense*, because the unit of work is
#: a sense and one entry carries one marker per sense it has been written for.
MARKER_PREFIX = "qa_pairs"

#: Separates the digest from the attempt counter inside a marker note (D-47's form).
_ATTEMPTS_SEPARATOR = ";attempts="

#: How many calls one sense may earn for one state of its supplied text. Two: the first
#: answer is usually usable, a second is worth buying when the first was dropped wholesale,
#: and a third has never paid for itself anywhere else in this project (D-47's bound).
MAX_ATTEMPTS = 2

#: How many content words an answer must share with the renditions it cites. A floor, not
#: a similarity threshold: two is enough to catch an answer that cites text it did not
#: read, and low enough that a genuine paraphrase is never punished for being one.
MIN_SHARED_CONTENT_WORDS = 2

#: The most example sentences one prompt carries, after de-duplication. A sense holds a
#: mean of six and as many as fifteen; past half a dozen they stop adding material a
#: question could be built out of and start adding input tokens.
MAX_EXAMPLES = 6

#: The most words of encyclopedia passage one prompt carries. The stored passages run to a
#: median of ~350 words, so this truncates almost nothing; the cap exists so a single long
#: entry cannot quietly triple the cost of its own senses' calls. The check reads the same
#: truncated text the model was shown, so grounding stays honest.
MAX_ENCYCLOPEDIA_WORDS = 500

#: How many progress lines a long sweep emits, matching every other sweep in the project.
PROGRESS_EVERY = 250

#: Content-word tokenisation: runs of lowercase letters, so numbers, punctuation and
#: markup never count towards the overlap floor.
_WORD_RE = re.compile(r"[a-z]+")

#: The shortest token that counts as content. Two-letter words are function words in
#: English almost without exception, and excluding them costs the overlap check nothing.
_MIN_CONTENT_WORD_LENGTH = 3

#: A deliberately small stopword list: closed-class English function words and the handful
#: of light verbs that carry no subject matter. Its job is to stop "the sense" and "of the"
#: from satisfying the overlap floor, not to be a linguistic resource -- a longer list would
#: start removing words that carry a definition's meaning, and every word removed makes the
#: floor harder for an honest paraphrase to clear.
_STOPWORDS = frozenset(
    (
        "the",
        "and",
        "but",
        "not",
        "for",
        "nor",
        "yet",
        "all",
        "any",
        "each",
        "every",
        "some",
        "such",
        "other",
        "another",
        "same",
        "both",
        "either",
        "neither",
        "this",
        "that",
        "these",
        "those",
        "there",
        "here",
        "then",
        "than",
        "when",
        "where",
        "which",
        "what",
        "who",
        "whom",
        "whose",
        "how",
        "why",
        "you",
        "your",
        "yours",
        "they",
        "them",
        "their",
        "theirs",
        "she",
        "her",
        "hers",
        "him",
        "his",
        "its",
        "our",
        "ours",
        "one",
        "ones",
        "itself",
        "with",
        "from",
        "into",
        "onto",
        "upon",
        "over",
        "under",
        "above",
        "below",
        "between",
        "among",
        "through",
        "during",
        "before",
        "after",
        "about",
        "against",
        "within",
        "without",
        "across",
        "around",
        "along",
        "behind",
        "beyond",
        "toward",
        "towards",
        "because",
        "while",
        "since",
        "until",
        "unless",
        "though",
        "although",
        "was",
        "were",
        "been",
        "being",
        "are",
        "have",
        "has",
        "had",
        "having",
        "can",
        "could",
        "will",
        "would",
        "shall",
        "should",
        "may",
        "might",
        "must",
        "does",
        "did",
        "done",
        "doing",
        "very",
        "just",
        "also",
        "only",
        "more",
        "most",
        "much",
        "many",
        "less",
        "least",
        "even",
        "still",
        "already",
        "often",
        "always",
        "never",
        "sometimes",
        "usually",
        "rather",
        "quite",
        "well",
        "way",
        "ways",
    )
)


class DropReason(StrEnum):
    """Why one drafted QA pair was not kept.

    Every drop is counted under exactly one of these, and the counts are this stage's only
    feedback loop: there is no retry inside a call, so a reason that dominates a sweep is
    what the next change to :data:`QA_PAIRS_INSTRUCTIONS` has to address.
    """

    #: The question or the answer was empty once markdown was stripped from it.
    EMPTY = "empty"
    #: ``grounded_in`` was empty: the model declined to say what the answer rests on.
    NO_CITATION = "no_citation"
    #: ``grounded_in`` named an id that was not supplied to this call — a fact from
    #: outside the sense, wearing a citation.
    UNKNOWN_CITATION = "unknown_citation"
    #: The answer shares fewer than :data:`MIN_SHARED_CONTENT_WORDS` content words with the
    #: text it cited.
    NOT_GROUNDED = "not_grounded"
    #: The same normalised question as a pair accepted earlier in this answer, or as one
    #: the sense already holds.
    DUPLICATE_QUESTION = "duplicate_question"


# --------------------------------------------------------------------------------------
# The supplied text
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Source:
    """One piece of stored text a call is allowed to build answers out of.

    Attributes:
        source_id: How the model addresses it in ``grounded_in``. A derived rendition id
            for the gloss and the encyclopedia passage; a stage-local positional id for an
            example; a stage-local owner id for the etymology (see the module docstring).
        label: What kind of text it is, shown to the model so it knows what it is reading.
        text: The text itself, exactly as the model is shown it — so the overlap check and
            the model read the same words.
    """

    source_id: str
    label: str
    text: str


def _example_source_id(owner_sense_id: str, index: int) -> str:
    """Return this stage's address for one example rendition of a sense.

    Positional and zero-based, in the ``#q<n>`` / ``#qa<n>`` family. Not in ``identity.py``
    and not a rendition id: :meth:`~opengloss_generator.schema.Lexeme.rendition_ids`
    documents that example renditions have no unique derived id, because several may share
    one ``(reading level, register)`` key — and a sense's ``(neutral, plain)`` example
    would collide with its ``(neutral, plain)`` gloss under
    :func:`~opengloss_generator.identity.rendition_id`, which is precisely the ambiguity a
    citation must not have.

    Args:
        owner_sense_id: The sense the example belongs to.
        index: Zero-based position within the sense's example renditions.

    Returns:
        An example address, e.g. ``abseil:verb:0#ex2``.
    """
    return f"{owner_sense_id}#ex{index}"


def _etymology_source_id(lexeme_id: str) -> str:
    """Return this stage's address for an entry's etymology summary.

    In the family of :func:`~opengloss_generator.identity.encyclopedia_owner_id` and
    :func:`~opengloss_generator.identity.explanation_owner_id`, but with no
    ``#level/register`` suffix: etymology is a structured field, not a rendition set, so
    there is nothing for a rendition id to name.
    """
    return f"{lexeme_id}:etymology"


def _clean(text: str) -> str:
    """Return one line of markdown-free, whitespace-collapsed prose."""
    return " ".join(strip_markdown(text).split())


def _sources(entry: Lexeme, sense: Sense, owner_sense_id: str) -> list[_Source]:
    """Return every piece of stored text one sense's call may build answers out of.

    Order is the order the model is shown them in, cheapest and most specific first: the
    definition, then the sentences that use it, then the entry-level passage, then the
    word's history. Nothing from another sense is included, deliberately — see the module
    docstring.

    Args:
        entry: The owning entry, for its encyclopedia passage and etymology.
        sense: The sense being written for. Never mutated.
        owner_sense_id: That sense's derived id, which its sources are addressed under.

    Returns:
        The sources, de-duplicated on text, in listing order.
    """
    sources = [
        _Source(
            source_id=rendition_id(owner_sense_id, *(key.value for key in CANONICAL_KEY)),
            label="definition",
            text=_clean(sense.canonical_gloss()),
        )
    ]
    seen = {sources[0].text}
    for index, rendition in enumerate(sense.examples):
        if len(sources) > MAX_EXAMPLES:
            break
        text = _clean(rendition.content.text)
        if not text or text in seen:
            continue
        seen.add(text)
        sources.append(
            _Source(
                source_id=_example_source_id(owner_sense_id, index),
                label="example sentence",
                text=text,
            )
        )

    passage = entry.encyclopedia.canonical()
    if passage is not None and passage.content.strip():
        words = _clean(passage.content).split()
        sources.append(
            _Source(
                source_id=rendition_id(
                    encyclopedia_owner_id(entry.lexeme_id), *(key.value for key in CANONICAL_KEY)
                ),
                label="encyclopedia passage",
                text=" ".join(words[:MAX_ENCYCLOPEDIA_WORDS]),
            )
        )

    if entry.etymology is not None and entry.etymology.summary.strip():
        sources.append(
            _Source(
                source_id=_etymology_source_id(entry.lexeme_id),
                label="etymology",
                text=_clean(entry.etymology.summary),
            )
        )
    return sources


def _content_words(text: str) -> set[str]:
    """Return the content words of a piece of text, for the overlap floor.

    Lowercase alphabetic runs, minus the stopword list and minus anything shorter than
    :data:`_MIN_CONTENT_WORD_LENGTH`. Deliberately crude: this is a floor on whether an
    answer read what it cited, not a similarity measure.

    Args:
        text: Any prose.

    Returns:
        The distinct content words in it.
    """
    return {
        word
        for word in _WORD_RE.findall(text.lower())
        if len(word) >= _MIN_CONTENT_WORD_LENGTH and word not in _STOPWORDS
    }


# --------------------------------------------------------------------------------------
# The output contract and the instructions
# --------------------------------------------------------------------------------------
#
# Module-private, following ``sense_hygiene`` and ``relation_hygiene``: ``contracts.py``
# and ``prompts.py`` are being edited concurrently by the sibling retrieval-data agents,
# and a stage whose whole prompt surface lives in its own module cannot conflict with any
# of that. ``PROMPT_VERSION`` is still imported and recorded, so provenance says which
# vintage of the shared prompt set this stage shipped against.


class _DraftQAPair(BaseModel):
    """One question, its answer, and what the answer rests on."""

    model_config = ConfigDict(
        extra="forbid", validate_by_name=True, validate_by_alias=True, serialize_by_alias=True
    )

    question: Annotated[str, Field(min_length=1, max_length=500)]
    answer: Annotated[str, Field(min_length=1, max_length=2000)]
    question_type: QuestionType
    difficulty: Difficulty
    # Required, with no default, deliberately: the schema is the only place the model can
    # be *made* to say what it used, and a pair that arrives uncited is a pair whose
    # grounding cannot be checked at all.
    grounded_in: Annotated[
        list[str], Field(description="Ids of the supplied text this answer is supported by.")
    ]


class _DraftQASet(BaseModel):
    """Every pair one call produced for one sense."""

    model_config = ConfigDict(
        extra="forbid", validate_by_name=True, validate_by_alias=True, serialize_by_alias=True
    )

    pairs: Annotated[list[_DraftQAPair], Field(min_length=1)]


QA_PAIRS_INSTRUCTIONS = """\
You write question-and-answer pairs for one meaning of one dictionary headword, using \
only the text you are given.

You will be given the headword, its part of speech, and a numbered list of SOURCES. Each \
source has an id in square brackets, a label saying what kind of text it is (a \
definition, an example sentence, an encyclopedia passage, an etymology), and the text \
itself. Write seven pairs: exactly one of each question type listed below. Tag each pair \
with its type, with a difficulty, and with the ids of the sources its answer is \
supported by.

THE ONE RULE THAT MATTERS: EVERY ANSWER COMES OUT OF THE SOURCES. You know a great deal \
about the world. None of it may appear in an answer here. If a question cannot be \
answered from the text in front of you, it is the wrong question -- change the question, \
never supplement the text. Do not add a date, a number, a name, a place, a mechanism or a \
consequence that is not on the page, however certain you are of it and however obviously \
true it is. An answer that is correct about the world but unsupported by the sources is \
worse than useless here: it is the one failure this whole task exists to avoid.

CITE WHAT YOU USED. For every pair, list in `grounded_in` the id of every source your \
answer actually draws on, and no others. Copy the ids exactly as they appear in the \
brackets. Never invent an id, never cite a source you did not use, and never leave the \
list empty. An answer built from the definition and one example cites both; an answer \
that is a restatement of the definition cites the definition alone. Your citation is \
checked against the text: an answer sharing no vocabulary with what it cites is thrown \
away.

THE SEVEN QUESTION TYPES. Write one pair of each, and make each one the kind of question \
its type names:

factual -- asks for a specific piece of information that is stated in the sources. Who, \
what, where, when, how many, what kind. The answer is in the text and can be pointed at.

definition -- asks what the word means in this sense, or what a term used in the sources \
means. The answer is the meaning, phrased in your own words rather than copied, but adding \
nothing the definition does not already say.

reasoning -- asks something that requires putting two things in the sources together. Not \
a lookup: the answer should follow from the text without being written in it, in one step \
a careful reader could take. "Given that X and that Y, what follows?"

comparison -- asks how two things in the sources differ or resemble each other: two uses, \
two contexts, the word's sense against a neighbouring notion the sources name. Only \
compare things the sources actually mention.

procedural -- asks how something in the sources is done, or in what order, or what steps \
it involves. If the sources describe no procedure, ask how the thing they describe is \
carried out *according to them*, and answer only from what they say.

causal -- asks why something in the sources happens, or what it leads to. Answer with the \
cause or the effect the text gives. If the text gives no cause, ask about the one \
relationship it does state and stay inside it.

hypothetical -- asks what would happen, or what would be true, under a condition. The \
answer must still follow from the sources: it reasons about the text, it does not go \
beyond it. "If the sources describe A, what would B look like?" is fair; "what would \
happen in 2030" is not.

MIX THE DIFFICULTY. Label each pair easy, medium or hard, and use at least two of the \
three across your seven. Easy means the answer is stated almost verbatim in one source. \
Medium means the reader has to locate it or restate it. Hard means the reader has to \
combine two sources, or reason a step past what either one says. Do not mark everything \
medium; the label is used, not decorative.

WRITE QUESTIONS SOMEONE WOULD ASK. A real question is specific and is about the subject \
matter, not about the dictionary entry. Ask "Why does a map projection distort a \
continent's area?", never "What does the encyclopedia passage say about distortion?" or \
"According to source 3, ...". The reader of these questions has never seen your sources \
and does not know they exist. Do not refer to "the text", "the passage", "the definition", \
"the sources" or their ids anywhere in a question or an answer; put the citation in \
`grounded_in`, which is where it belongs.

ANSWER LENGTH AND SHAPE. One to three sentences. Complete sentences, plain prose, no \
markdown, no bullets, no headings, no quotation marks around the whole answer. Answer the \
question that was asked and stop: a hedge, a restatement of the question, or a closing \
sentence that adds nothing are all waste. Never write "I don't know", never write "the \
sources do not say" -- if that is the honest answer, you asked the wrong question, so ask \
a different one.

DO NOT REPEAT YOURSELF. The seven questions must be seven different questions. Two \
questions that differ only in wording are one question, and the second is discarded. \
Neither may two answers be the same sentence with a word changed: if your comparison \
answer and your definition answer say the same thing, one of the two questions is wrong.

USE THE WORD'S OWN MEANING. Every pair is about this sense of this headword, the one whose \
definition you were given. A headword often has other meanings; none of them are in front \
of you and none of them belong in an answer. If an example sentence seems to use the word \
in a different meaning from the definition, trust the definition.

WORKED EXAMPLE. Suppose the headword is "bank", the part of speech is "noun", and the \
sources are:

  [bank:noun:0#neutral/plain] definition: The land alongside a river or a lake.
  [bank:noun:0#ex0] example sentence: Willow roots held the bank together where the \
current cut hardest.
  [bank:noun:0#ex1] example sentence: Rain had washed away part of the bank overnight.
  [bank:encyclopedia#neutral/plain] encyclopedia passage: Riverbanks are shaped by the \
water that runs against them. On the outside of a bend the current runs fastest and cuts \
into the bank; on the inside it slows and drops the sediment it was carrying. Vegetation \
slows this process, because roots bind the soil and take the force of the water before \
the soil does.

Good pairs:

  factual, easy -- "What holds a riverbank together where the current is strongest?" / \
"Willow roots bind the soil and hold the bank in place where the current cuts hardest." \
grounded_in: [bank:noun:0#ex0]

  causal, medium -- "Why does a river cut into the bank on the outside of a bend?" / \
"The current runs fastest on the outside of a bend, and the faster water cuts into the \
bank there. On the inside the current slows instead and drops the sediment it was \
carrying." grounded_in: [bank:encyclopedia#neutral/plain]

  hypothetical, hard -- "What would happen to a bend in a river if the vegetation along \
its outer bank were cleared?" / "The soil would no longer be bound by roots, so the fast \
current on the outside of the bend would take the force of the water directly and cut \
into the bank more quickly." grounded_in: [bank:encyclopedia#neutral/plain, \
bank:noun:0#ex0]

Notice what these do. Each question is about rivers, not about the entry. Each answer says \
only what the sources say, in different words. The hypothetical one reasons -- roots are \
gone, so the soil takes the force -- but every link in that chain is on the page. And each \
cites exactly the sources it used.

Bad pairs, and why:

  "How long does bank erosion take?" -- the sources do not say. Wrong question.
  "The Mississippi loses about 30 metres of bank a year." -- a real fact, not in the \
sources. Thrown away.
  "What does the passage say about vegetation?" -- asks about the entry, not the subject.
  "What is a bank?" answered "A financial institution." -- the other meaning of the \
headword, which is not the sense you were given."""


def _build_prompt(headword: str, pos: str, sources: Sequence[_Source]) -> str:
    """Return the volatile half of one sense's prompt.

    Args:
        headword: The lexeme's surface form.
        pos: The part-of-speech tag of the sense being written for.
        sources: Every piece of text the call may build answers out of, in listing order.

    Returns:
        The per-call prompt body.
    """
    lines = [
        f"Headword: {headword}",
        f"Part of speech: {pos}",
        f"Sources ({len(sources)}):",
    ]
    lines.extend(f"  [{source.source_id}] {source.label}: {source.text}" for source in sources)
    lines.append(
        f"Write {len(QuestionType)} pairs, one of each question type, "
        "answered only from the sources above."
    )
    return "\n".join(lines)


# --------------------------------------------------------------------------------------
# The D-47 marker
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Marker:
    """The most recent sentinel this stage left on one sense of an entry."""

    digest: str
    attempts: int


def _digest(sources: Sequence[_Source]) -> str:
    """Return a stable short hash of the text one call was made for.

    Over the sorted source ids *and* the first source's text, which is the canonical
    gloss: an id set alone would call a rewritten gloss unchanged, and a rewritten gloss is
    exactly the case where the stored pairs have gone stale.

    Args:
        sources: The sources as they would be supplied, gloss first.

    Returns:
        Sixteen hex characters of SHA-256. Sorted before hashing so the digest does not
        depend on listing order, and SHA-256 rather than :func:`hash` because the value is
        written to disk and compared across processes.
    """
    ids = "\n".join(sorted(source.source_id for source in sources))
    gloss = sources[0].text if sources else ""
    return hashlib.sha256(f"{ids}\n--\n{gloss}".encode()).hexdigest()[:16]


def _marker_prefix(owner_sense_id: str) -> str:
    """Return the note prefix this stage's marker for one sense starts with."""
    return f"{MARKER_PREFIX}:{owner_sense_id}:"


def _latest_marker(entry: Lexeme, owner_sense_id: str) -> _Marker | None:
    """Return the last sentinel this stage wrote for one sense, parsed.

    Args:
        entry: The entry to inspect.
        owner_sense_id: The sense whose markers are wanted.

    Returns:
        The most recent marker, or ``None`` if the sense has never been written for.
        Provenance ids are assigned in insertion order and never reused, so the last
        matching record in the table is the most recently written one.
    """
    prefix = _marker_prefix(owner_sense_id)
    latest: _Marker | None = None
    for record in entry.provenance_in_order():
        note = record.note or ""
        if not note.startswith(prefix):
            continue
        digest, _, attempts = note[len(prefix) :].partition(_ATTEMPTS_SEPARATOR)
        latest = _Marker(digest, int(attempts) if attempts.isdigit() else 1)
    return latest


def _attempt_number(entry: Lexeme, owner_sense_id: str, sources: Sequence[_Source]) -> int | None:
    """Return which attempt is due on one sense, or ``None`` if none is (D-47).

    Args:
        entry: The entry being considered.
        owner_sense_id: The sense in question.
        sources: The text that would be supplied now.

    Returns:
        The 1-based attempt number, or ``None`` when the sense must be skipped — which is
        also the "do not bill this" signal for the caller.
    """
    if not sources:
        return None
    marker = _latest_marker(entry, owner_sense_id)
    if marker is None:
        return 1
    if marker.digest == _digest(sources) or marker.attempts >= MAX_ATTEMPTS:
        return None
    return marker.attempts + 1


def _marker_note(owner_sense_id: str, sources: Sequence[_Source], attempt: int) -> str:
    """Return the sentinel to stamp for one attempt, in D-47's form."""
    return f"{_marker_prefix(owner_sense_id)}{_digest(sources)}{_ATTEMPTS_SEPARATOR}{attempt}"


# --------------------------------------------------------------------------------------
# Planning
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class QAPairsPlan:
    """What one entry would cost this stage, computed without a model call.

    Attributes:
        senses: How many live senses are due a call — ``0`` when the entry would cost $0.
        pairs: How many pairs would be asked for: ``senses * len(QuestionType)``.
    """

    senses: int = 0
    pairs: int = 0

    @property
    def due(self) -> bool:
        """Return whether this entry would cost anything at all."""
        return self.senses > 0


def plan_qa_pairs(entry: Lexeme) -> QAPairsPlan:
    """Return what this entry would cost, without calling a model.

    The plan is exact, not estimated: whether a sense is due a call is decided by its
    sources and its marker, both of which are free to read. Only the money the CLI puts
    around this is an estimate.

    Args:
        entry: The entry to inspect. Never mutated.

    Returns:
        A :class:`QAPairsPlan`.
    """
    due = sum(
        1
        for _, sense, owner_sense_id in entry.iter_senses()
        if not sense.retired
        and _attempt_number(entry, owner_sense_id, _sources(entry, sense, owner_sense_id))
        is not None
    )
    return QAPairsPlan(senses=due, pairs=due * len(QuestionType))


# --------------------------------------------------------------------------------------
# Acceptance
# --------------------------------------------------------------------------------------


def _judge(
    draft: _DraftQAPair,
    by_id: dict[str, _Source],
    taken: set[str],
) -> QAPair | DropReason:
    """Accept one drafted pair, or say why it was not kept.

    Checks run cheapest first and the first failure wins, so a pair that is both
    uncited and duplicated is counted once, as uncited — the defect a prompt change would
    have to fix first. An accepted pair adds its question to ``taken`` on the way out, so
    the pairs after it in the same answer are compared against it.

    Args:
        draft: One pair as the model returned it.
        by_id: The sources supplied to this call, keyed by the id the model cites them by.
        taken: Normalised questions already spoken for — the sense's stored pairs plus
            everything accepted earlier in this answer. Updated on acceptance.

    Returns:
        The pair ready to store, or the reason it was not kept.
    """
    question = _clean(draft.question)
    answer = _clean(draft.answer)
    if not question or not answer:
        return DropReason.EMPTY

    cited = list(dict.fromkeys(draft.grounded_in))
    if not cited:
        return DropReason.NO_CITATION
    if any(source_id not in by_id for source_id in cited):
        return DropReason.UNKNOWN_CITATION

    supporting = _content_words(" ".join(by_id[source_id].text for source_id in cited))
    if len(_content_words(answer) & supporting) < MIN_SHARED_CONTENT_WORDS:
        return DropReason.NOT_GROUNDED

    key = normalise_query_text(question)
    if not key or key in taken:
        return DropReason.DUPLICATE_QUESTION
    taken.add(key)

    return QAPair(
        question=question,
        answer=answer,
        question_type=draft.question_type,
        difficulty=draft.difficulty,
        grounded_in=cited,
    )


def _sift(
    drafted: Sequence[_DraftQAPair],
    sources: Sequence[_Source],
    taken: set[str],
) -> tuple[list[QAPair], dict[str, int]]:
    """Run every drafted pair past :func:`_judge`, in the order it was returned.

    Order is the model's own and nothing here re-orders or prefers, which keeps the result
    a pure function of the answer: the first of two colliding questions survives.

    Args:
        drafted: The pairs as returned.
        sources: The text that was supplied to the call.
        taken: Normalised questions already spoken for; updated as pairs are accepted.

    Returns:
        ``(pairs to store, drop counts by reason)``.
    """
    by_id = {source.source_id: source for source in sources}
    accepted: list[QAPair] = []
    dropped: dict[str, int] = {}
    for draft in drafted:
        verdict = _judge(draft, by_id, taken)
        if isinstance(verdict, DropReason):
            dropped[verdict.value] = dropped.get(verdict.value, 0) + 1
            continue
        accepted.append(verdict)
    return accepted, dropped


# --------------------------------------------------------------------------------------
# The sweep's accounting types
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class QACallRecord:
    """One completed call, as the CLI needs it for the run ledger.

    The sweep's own totals are enough for the run summary; this is what makes the *pilot*
    measurable — cost per sense, cost per accepted pair, and the mean output tokens that
    the stage's ``expected_output_tokens`` is supposed to be set from (D-41) are all
    per-call quantities and cannot be recovered from a total.
    """

    sense_id: str
    cost_usd: float
    input_tokens: int
    cached_input_tokens: int
    output_tokens: int
    attempts: int
    duration_seconds: float
    generated: int
    accepted: int


@dataclass(slots=True)
class QAPairsOutcome:
    """What one :func:`run_qa_pairs` sweep did across the store."""

    entries_scanned: int = 0
    entries_changed: int = 0
    senses_written: int = 0
    calls: int = 0
    pairs_generated: int = 0
    accepted: int = 0
    dropped_by_reason: dict[str, int] = field(default_factory=dict)
    accepted_by_type: dict[str, int] = field(default_factory=dict)
    accepted_by_difficulty: dict[str, int] = field(default_factory=dict)
    senses_with_full_type_coverage: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    stopped_reason: str | None = None

    @property
    def dropped(self) -> int:
        """Return how many drafted pairs were dropped, for any reason."""
        return sum(self.dropped_by_reason.values())

    def as_dict(self) -> dict[str, object]:
        """Return a JSON-serialisable view for the CLI run summary."""
        return {
            "entries_scanned": self.entries_scanned,
            "entries_changed": self.entries_changed,
            "senses_written": self.senses_written,
            "calls": self.calls,
            "pairs_generated": self.pairs_generated,
            "accepted": self.accepted,
            "dropped": self.dropped,
            "dropped_by_reason": dict(sorted(self.dropped_by_reason.items())),
            "accepted_by_type": dict(sorted(self.accepted_by_type.items())),
            "accepted_by_difficulty": dict(sorted(self.accepted_by_difficulty.items())),
            "senses_with_full_type_coverage": self.senses_with_full_type_coverage,
            "mean_output_tokens_per_call": (
                round(self.output_tokens / self.calls, 1) if self.calls else 0.0
            ),
            "cost_usd_per_sense": (
                round(self.cost_usd / self.senses_written, 8) if self.senses_written else 0.0
            ),
            "cost_usd_per_accepted_pair": (
                round(self.cost_usd / self.accepted, 8) if self.accepted else 0.0
            ),
            "cost_usd": round(self.cost_usd, 6),
            "stopped_reason": self.stopped_reason,
        }


class _Tally:
    """One sweep's counters, mutated only while holding an ``asyncio.Lock``.

    Mirrors ``retrofit.py``'s own ``_Tally`` and every sweep that followed it: many
    handlers touch these counters around many awaits, and single-threaded asyncio only
    makes one await-free statement atomic, not a whole read-modify-write spanning one.
    """

    def __init__(self) -> None:
        """Start an empty outcome."""
        self._lock = asyncio.Lock()
        self._result = QAPairsOutcome()

    @property
    def result(self) -> QAPairsOutcome:
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
                    "qa_pairs_progress",
                    entries_scanned=self._result.entries_scanned,
                    accepted=self._result.accepted,
                    cost_usd=round(self._result.cost_usd, 6),
                )

    async def call(
        self,
        *,
        cost_usd: float,
        output_tokens: int,
        generated: int,
        accepted: Sequence[QAPair],
        dropped: dict[str, int],
    ) -> None:
        """Record one completed call: its money, its answer, and what survived it."""
        async with self._lock:
            result = self._result
            result.calls += 1
            result.senses_written += 1
            result.cost_usd += cost_usd
            result.output_tokens += output_tokens
            result.pairs_generated += generated
            result.accepted += len(accepted)
            for reason, count in dropped.items():
                result.dropped_by_reason[reason] = result.dropped_by_reason.get(reason, 0) + count
            for pair in accepted:
                kind = pair.question_type.value
                level = pair.difficulty.value
                result.accepted_by_type[kind] = result.accepted_by_type.get(kind, 0) + 1
                result.accepted_by_difficulty[level] = (
                    result.accepted_by_difficulty.get(level, 0) + 1
                )
            if {pair.question_type for pair in accepted} == set(QuestionType):
                result.senses_with_full_type_coverage += 1

    async def note_stop(self, reason: str) -> None:
        """Record why the sweep stopped early, keeping the first reason given."""
        async with self._lock:
            if self._result.stopped_reason is None:
                self._result.stopped_reason = reason


# --------------------------------------------------------------------------------------
# The call, and applying what survives it
# --------------------------------------------------------------------------------------


async def _write_sense(
    entry: Lexeme,
    pos_entry: POSEntry,
    sense: Sense,
    owner_sense_id: str,
    *,
    runner: StageRunner,
    tally: _Tally,
    on_call: Callable[[QACallRecord], Awaitable[None]] | None,
) -> bool:
    """Buy, sift and store one sense's QA pairs, in place.

    Args:
        entry: The owning entry, mutated through its provenance table and this sense.
        pos_entry: The sense's part-of-speech entry, for the tag shown to the model.
        sense: The sense being written for, mutated when a pair is accepted.
        owner_sense_id: Its derived id.
        runner: The stage runner.
        tally: The sweep tally.
        on_call: Optional per-call sink, for the run ledger.

    Returns:
        Whether the entry now needs writing. Not the same as "a pair was stored": a call
        whose every pair was dropped still leaves a marker to persist, or the next sweep
        pays for the same answer.

    Raises:
        BudgetExceededError: A budget stop is a run-level condition and propagates —
            before anything is written, so the entry is left exactly as it was found.
    """
    sources = _sources(entry, sense, owner_sense_id)
    attempt = _attempt_number(entry, owner_sense_id, sources)
    if attempt is None:
        return False

    try:
        result = await runner.run(
            stage=StageName.QA_PAIRS,
            output_type=_DraftQASet,
            instructions=QA_PAIRS_INSTRUCTIONS,
            prompt=_build_prompt(entry.headword, pos_entry.pos.value, sources),
            prompt_version=PROMPT_VERSION,
            writer_key=owner_sense_id,
        )
    except BudgetExceededError:
        raise
    except GenerationError as exc:
        # A call that failed outright writes no marker, so the sense is retried whole on
        # the next sweep — the convention every model call in this project follows.
        _LOG.warning("qa_pairs_generation_failed", sense=owner_sense_id, error=str(exc))
        return False

    drafted = list(result.output.pairs)
    taken = {pair.key for pair in sense.qa}
    accepted, dropped = _sift(drafted, sources, taken)
    await tally.call(
        cost_usd=result.cost_usd,
        output_tokens=result.output_tokens,
        generated=len(drafted),
        accepted=accepted,
        dropped=dropped,
    )

    # The marker rides the generating call's own record, so one record is both "what this
    # cost" and "this sense has been written for", exactly as ``examples`` does it.
    provenance_id = entry.add_provenance(
        result.provenance.model_copy(
            update={"note": _marker_note(owner_sense_id, sources, attempt)}
        )
    )
    for pair in accepted:
        sense.qa.append(pair.model_copy(update={"provenance_id": provenance_id}))

    if on_call is not None:
        await on_call(
            QACallRecord(
                sense_id=owner_sense_id,
                cost_usd=result.cost_usd,
                input_tokens=result.input_tokens,
                cached_input_tokens=result.cached_input_tokens,
                output_tokens=result.output_tokens,
                attempts=result.attempts,
                duration_seconds=result.duration_seconds,
                generated=len(drafted),
                accepted=len(accepted),
            )
        )
    _LOG.info(
        "qa_pairs_written",
        sense=owner_sense_id,
        attempt=attempt,
        generated=len(drafted),
        accepted=len(accepted),
        dropped=sum(dropped.values()),
        cost_usd=round(result.cost_usd, 6),
    )
    return True


async def _fill_entry(
    entry: Lexeme,
    runner: StageRunner,
    tally: _Tally,
    on_call: Callable[[QACallRecord], Awaitable[None]] | None,
) -> tuple[bool, BudgetExceededError | None]:
    """Write QA pairs for every live sense of one entry, in place.

    Retired senses are skipped entirely: they are tombstones (D-52), and buying questions
    about one would be paying to interrogate a grave.

    A budget stop is *returned*, not raised, because an entry with five senses may have had
    three of them answered and billed before the fourth was refused. Raising here would
    unwind past the caller's ``store.write`` and throw away work that has already been paid
    for; the caller writes what survived and then re-raises what it was handed.

    Args:
        entry: The entry to fill, mutated in place.
        runner: The stage runner.
        tally: The sweep tally.
        on_call: Optional per-call sink, for the run ledger.

    Returns:
        ``(whether the entry needs writing back, the budget stop that ended it or None)``.
    """
    changed = False
    for pos_entry, sense, owner_sense_id in entry.iter_senses():
        if sense.retired:
            continue
        try:
            changed |= await _write_sense(
                entry,
                pos_entry,
                sense,
                owner_sense_id,
                runner=runner,
                tally=tally,
                on_call=on_call,
            )
        except BudgetExceededError as exc:
            return changed, exc
    return changed, None


async def run_qa_pairs(
    store: LexemeStore,
    runner: StageRunner,
    *,
    lexeme_ids: Iterable[str] | None = None,
    workers: int,
    stop_event: asyncio.Event | None = None,
    on_call: Callable[[QACallRecord], Awaitable[None]] | None = None,
) -> QAPairsOutcome:
    """Write grounded question/answer pairs for every live sense in the store (D-58).

    Args:
        store: The store to fill. Each entry is read, written for — including one model
            call per live sense — and written back inside one hold of its own lock,
            exactly the discipline ``workflows/retrofit.py`` documents (D-31).
        runner: The stage runner, which supplies the ``qa_pairs`` model policy.
        lexeme_ids: Ids to visit; defaults to every id in the store, sorted.
        workers: Pool size.
        stop_event: Shared stop event; set by a budget stop, and honoured if the caller
            sets it from outside.
        on_call: Called once per completed model call, with everything the run ledger
            wants. Optional: the sweep's own totals do not depend on it.

    Returns:
        A :class:`QAPairsOutcome` carrying counts, the drop breakdown and cost. A sweep
        that stopped early still returns its outcome, with ``stopped_reason`` set.
    """
    ids = list(lexeme_ids) if lexeme_ids is not None else sorted(store.iter_ids())
    tally = _Tally()

    async def fill(lexeme_id: str) -> None:
        async with store.locked(lexeme_id):
            entry = store.read(lexeme_id)
            if entry is None:
                return
            changed, budget_stop = await _fill_entry(entry, runner, tally, on_call)
            if changed:
                store.write(entry)
        await tally.entry(changed=changed)
        if budget_stop is not None:
            raise budget_stop

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
    _LOG.info("qa_pairs_complete", **result.as_dict())
    return result
