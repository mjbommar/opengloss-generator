"""Repair pass — rewrite stored renditions whose words their reader will not know.

Companion to D-51's generation-time fix in ``workflows/enrich.py``: that fix catches the
defect for renditions produced from here on; this module is for what is already on disk.

The defect is the largest one the QA judge found (docs/QA-DIARY.md, iteration 1):
**46.6% of judged grade_1 encyclopedia renditions were not level-appropriate although
every one of them passed its Flesch-Kincaid band.** FK measures sentence and syllable
length, and "Ancient people in Mesopotamia, Greece, and Rome used oaths." and "Monks made
vows of poverty, chastity, and obedience." are short sentences of short words. Grade-1
glosses failed at 10.6% and grade-1 examples at 7.8% for the same reason.
``workflows/retrofit.py``'s ``readability_hygiene`` pass cannot see any of it: everything
here is already inside the band that pass selects on.

:func:`run_vocabulary_hygiene` visits every entry once. For each, every rendition at a
reading level that *has* an unfamiliar-word band — ``grade_1`` (0.10) and ``grade_5``
(0.25), from :func:`~opengloss_generator.vocabulary.vocabulary_band` — whose measured
share exceeds that band plus ``config.readability.vocabulary_tolerance`` is an offender.
Every text-bearing field is covered: sense glosses and examples, the encyclopedia and the
lexical explanation, the same field set ``readability_hygiene`` sweeps, because a hard
word is a property of the text and not of which field holds it. An entry with no
offenders costs nothing. An entry with offenders gets one call per word-budget chunk, on
the ``RENDITIONS`` policy (luna) rather than ``hygiene``'s nano one — this is prose
written for an audience, not a structural verdict — listing each offender with its field,
its level and register, **and the offending words themselves**, which is what makes the
instruction actionable: told "use simpler words", a model shortens sentences that are
already short.

A rewrite is verified before it is adopted, never trusted, and it has to clear four bars:
its unfamiliar-word share must be *lower* than what is stored; its Flesch-Kincaid grade
must still be inside its level's band plus ``config.readability.tolerance``, so trading
vocabulary for sentence length is refused; a gloss rewrite must not open by naming its
own headword (D-47's regression, where making a definition easy produces "A ban is an
order to stop."); and an example rewrite must still contain a form of its headword
(D-45). A rewrite that fails any of them is discarded and the old text kept. Whatever
ends up stored has *both* metrics re-measured into its own ``Assessment`` and both flags
— :data:`~opengloss_generator.schema.QAFlag.OG_HARD_VOCABULARY` and
:data:`~opengloss_generator.schema.QAFlag.OG_READABILITY_MISS` — set or cleared to match,
so neither flag lags behind the text it describes. The superseded text is kept in a
zero-cost ``Provenance.note``, exactly as every other rewriting pass keeps one.

Concurrency and locking (D-31) mirror ``workflows/retrofit.py`` and
``workflows/example_hygiene.py`` throughout: the unit of work is one entry, and the
handler holding that entry's lock reads it, does the deterministic offender scan, makes
its call(s) if any are due, and writes it back, all inside the same lock hold. Counters
are accumulated by :class:`_Tally` under an ``asyncio.Lock`` for the reason those modules
give. Idempotence is D-47's offending-*set* marker rather than a plain "visited" boolean:
the note carries a digest of exactly which renditions the pass answered for, so a set
that has changed since earns one more attempt and a set that has not costs nothing, and
no entry is answered for more than :data:`_MAX_ATTEMPTS` times however its offenders
churn.

This module is deliberately self-contained, importing nothing from
``workflows/retrofit.py``: passes are landing in that module concurrently, and a new,
independent module is the only way to add this fix without conflicting with them. That
costs one small duplicated helper each for the instructions slice and the marker note,
both marked below. Wiring ``retrofit --only vocabulary_hygiene`` into a ``RetrofitPass``
member is left to whoever next touches ``retrofit.py``/``cli.py``;
:func:`run_vocabulary_hygiene` is written to be callable exactly the way that wiring will
need — store, runner, worker count, an optional shared stop event, an optional explicit
id list.
"""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass
from typing import TYPE_CHECKING, Annotated, Any

from pydantic import BaseModel, ConfigDict, Field

from opengloss_generator import prompts, spans
from opengloss_generator.errors import BudgetExceededError, GenerationError
from opengloss_generator.hygiene import is_headword_initial
from opengloss_generator.identity import (
    encyclopedia_owner_id,
    explanation_owner_id,
    rendition_id,
)
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
    LexemeKind,
    QAFlag,
    StageName,
)
from opengloss_generator.vocabulary import hard_word_share, hard_words, vocabulary_band

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from opengloss_generator.schema import Lexeme, POSEntry, Provenance, Rendition
    from opengloss_generator.stages import StageRunner
    from opengloss_generator.store import LexemeStore

__all__ = [
    "VOCABULARY_HYGIENE_INSTRUCTIONS",
    "VocabularyHygieneOutcome",
    "run_vocabulary_hygiene",
]

_LOG = get_logger(__name__)

#: Prefix of the sentinel written to the ``note`` of this pass's call(s) for one entry, so
#: a later sweep can tell "vocabulary_hygiene already answered for this entry" apart from
#: an ordinary ``enrich.py`` rendition-generation record under the same
#: ``StageName.RENDITIONS`` stage (which carries no note at all) and from the other
#: note-stamped passes that share that stage. What follows the prefix is the offending
#: set's digest and the attempt count, exactly as ``retrofit.py``'s own rendition-rewriting
#: markers carry them (D-47).
_MARKER_PREFIX = "vocabulary_hygiene"

#: Separates the offending-set digest from the attempt count inside a marker note. Mirrors
#: ``workflows/retrofit.py``'s constant of the same shape; duplicated rather than imported
#: to keep this module free of that one (see the module docstring).
_ATTEMPTS_SEPARATOR = ";attempts="

#: How many attempts this pass makes on one entry before leaving what is still offending
#: flagged rather than billing a third answer for it (D-47).
_MAX_ATTEMPTS = 2

#: How many offending words one listed offender carries into the prompt. The whole point
#: of this pass is naming them, but a 300-word grade_1 encyclopedia passage can offend on
#: forty and the first dozen are the ones a rewrite has to reach for anyway.
_MAX_LISTED_WORDS = 12

#: Word budget for one call's worth of source text, and the reason it exists is
#: ``readability_hygiene``'s: an entry whose flagged text runs to several encyclopedia
#: passages is split across however many calls keep each one inside the ``RENDITIONS``
#: policy's token budget.
_WORD_BUDGET = 3000


def _extract_instructions_block(source: str, start_marker: str, end_marker: str) -> str:
    """Return the substring of ``source`` between two markers, trimmed.

    Used only at import time, to lift a section of
    :data:`~opengloss_generator.prompts.RENDITIONS_INSTRUCTIONS` verbatim into this pass's
    own instructions rather than retyping it, so the constraints a rewrite is held to
    cannot drift from the constraints the original rendition was written against. A copy
    of ``workflows/retrofit.py``'s private helper of the same name, duplicated rather than
    imported for the reason the module docstring gives.

    Args:
        source: The text to slice.
        start_marker: The literal text the wanted section begins with.
        end_marker: The literal text that follows the wanted section.

    Returns:
        The text from ``start_marker`` up to (not including) ``end_marker``, stripped.

    Raises:
        ValueError: If either marker is absent — a signal that
            ``RENDITIONS_INSTRUCTIONS`` changed shape and this pass's instructions need
            re-slicing.
    """
    start = source.index(start_marker)
    end = source.index(end_marker, start)
    return source[start:end].strip()


#: The "READING LEVELS." section of ``RENDITIONS_INSTRUCTIONS`` verbatim, which is where
#: the per-level vocabulary rule this pass enforces is actually written down ("Only very
#: common words: words a six-year-old already says out loud").
_LEVEL_CONSTRAINTS = _extract_instructions_block(
    prompts.RENDITIONS_INSTRUCTIONS, "READING LEVELS.", "\n\nREGISTERS."
)

#: The "WHAT THE FIELD MEANS FOR YOUR OUTPUT." section verbatim: this pass's batch mixes
#: glosses, examples and prose sections, and each kind has to be treated as its own thing.
_FIELD_MEANINGS = _extract_instructions_block(
    prompts.RENDITIONS_INSTRUCTIONS,
    "WHAT THE FIELD MEANS FOR YOUR OUTPUT.",
    "\n\nWORKED EXAMPLE.",
)

#: The one-sentence headword-initial rule verbatim. Without it this pass rewrites a hard
#: gloss into "A ban is an order to stop." — a vocabulary win that is a headword-initial
#: regression, which is exactly how the core's headword-initial gloss renditions rose from
#: 4,546 to 6,480 under the sibling pass (D-47).
_HEADWORD_RULE = _extract_instructions_block(
    prompts.RENDITIONS_INSTRUCTIONS, "Never begin a definition rendition", "\n\nFormatting,"
)

#: Instructions for this pass's call(s). The three blocks above are reused verbatim rather
#: than restated; only the framing and the answer format around them are new.
VOCABULARY_HYGIENE_INSTRUCTIONS = f"""\
Rewrite each numbered passage below so a reader at the reading level named for it knows \
every word in it. Each one was already written for that level and register, and each one \
already has short enough sentences — an automatic check found the words themselves too \
hard, and lists them for you.

For every word listed as too hard: either replace it with an everyday word that means the \
same thing, or keep it and explain it in the same sentence in words the reader does know. \
Do not simply delete the idea the hard word carried, and do not replace one hard word with \
another. A name of a person, a place or an organisation is not listed and does not need \
replacing; explaining it in a few plain words is welcome where the passage has room.

Keep the meaning, the facts and the register exactly as they are, and keep the sentences \
at least as short as they already are. A rewrite that trades a hard word for a longer \
sentence has not helped.

{_HEADWORD_RULE}

{_LEVEL_CONSTRAINTS}

{_FIELD_MEANINGS}

Formatting: plain prose, no markdown. No bold, no italics, no backticks, no bullets, no \
headings, no numbered lists, and no asterisks or underscores used for emphasis.

Each passage below is labelled with its field, its reading level and register, and the \
words the check found too hard for that level.

Answer every passage you are given, identified by the number it was listed under."""


class _DraftVocabularyRewrite(BaseModel):
    """One rewritten passage for a rendition carrying words its reader will not know."""

    model_config = ConfigDict(
        extra="forbid", validate_by_name=True, validate_by_alias=True, serialize_by_alias=True
    )

    ref: Annotated[int, Field(ge=1)]
    text: Annotated[str, Field(min_length=3, max_length=6000)]


class _DraftVocabularyRewriteBatch(BaseModel):
    """Rewrites for every vocabulary offender in one call, produced together."""

    model_config = ConfigDict(
        extra="forbid", validate_by_name=True, validate_by_alias=True, serialize_by_alias=True
    )

    rewrites: Annotated[list[_DraftVocabularyRewrite], Field(min_length=1)]


@dataclass(slots=True)
class VocabularyHygieneOutcome:
    """What one :func:`run_vocabulary_hygiene` sweep did across the store.

    Attributes:
        entries_scanned: Entries visited.
        entries_changed: Entries something was written for, including the ones where only
            the idempotence marker changed.
        renditions_rewritten: Offenders whose stored text was actually replaced.
        now_in_band: Offenders that, after the sweep, are inside their level's band.
        still_over: Offenders still over it, each carrying ``og.hard_vocabulary``.
        calls: Model calls made.
        cost_usd: What they cost.
        stopped_reason: ``None`` when the sweep ran to completion; ``"budget"`` when the
            run's ceiling was reached; ``"stopped"`` when the caller's event was set.
    """

    entries_scanned: int = 0
    entries_changed: int = 0
    renditions_rewritten: int = 0
    now_in_band: int = 0
    still_over: int = 0
    calls: int = 0
    cost_usd: float = 0.0
    stopped_reason: str | None = None

    def as_dict(self) -> dict[str, Any]:
        """Return the outcome as a plain mapping, for a run summary or a JSON report."""
        return {
            "entries_scanned": self.entries_scanned,
            "entries_changed": self.entries_changed,
            "renditions_rewritten": self.renditions_rewritten,
            "now_in_band": self.now_in_band,
            "still_over": self.still_over,
            "calls": self.calls,
            "cost_usd": round(self.cost_usd, 6),
            "stopped_reason": self.stopped_reason,
        }


@dataclass(slots=True)
class _Offender:
    """One stored rendition carrying more unfamiliar words than its level allows.

    Attributes:
        rendition: The offending rendition, mutated in place once a rewrite is applied.
        field_name: ``gloss``, ``examples``, ``encyclopedia`` or ``explanation`` — the
            vocabulary :data:`~opengloss_generator.prompts.RENDITIONS_INSTRUCTIONS` uses.
        pos_entry: The owning part-of-speech entry, needed only for ``examples``, to
            re-find the headword's span in a rewritten sentence; ``None`` otherwise.
        rendition_id: Its derived identifier, which the entry's marker digest is taken
            over (D-47). Example renditions carry their position within the sense's
            example list too, since several may share one ``(level, register)``.
        share: The measured unfamiliar-word share of the stored text.
        words: The offending words, in order, capped at :data:`_MAX_LISTED_WORDS`.
    """

    rendition: Rendition[Any]
    field_name: str
    pos_entry: POSEntry | None
    rendition_id: str
    share: float
    words: list[str]


def _forms_for(entry: Lexeme, pos_entry: POSEntry) -> list[str]:
    """Return the surface forms to try when locating the headword in an example.

    Mirrors the private ``_forms_for`` of ``workflows/enrich.py``,
    ``workflows/retrofit.py`` and ``workflows/example_hygiene.py``: the sense's own
    morphology first, falling back to the cheap rule-based forms when the model gave none.
    """
    forms = [*pos_entry.morphology.inflected_forms(), *pos_entry.morphology.derivations]
    return forms or list(spans.generate_forms(entry.headword))


def _text_of(rendition: Rendition[Any]) -> str:
    """Return the plain text of one rendition, whichever content shape it carries."""
    content = rendition.content
    return content.text if isinstance(content, Example) else str(content)


def _share_of(rendition: Rendition[Any], headword: str) -> float:
    """Return a rendition's unfamiliar-word share, stored or freshly measured.

    Args:
        rendition: The rendition to measure.
        headword: The entry's surface form, excused from the count.

    Returns:
        The stored ``hard_word_share`` when present — that is the figure the flag was set
        on — otherwise a fresh measurement of the stored text, which is what every
        rendition written before D-51 needs.
    """
    assessment = rendition.assessment
    if assessment is not None and assessment.hard_word_share is not None:
        return assessment.hard_word_share
    return hard_word_share(_text_of(rendition), ignore=(headword,))


def _over_band(share: float, rendition: Rendition[Any], tolerance: float) -> bool:
    """Return whether a share is over its rendition's level band plus ``tolerance``."""
    band = vocabulary_band(rendition.reading_level)
    return band is not None and share > band + tolerance


def _offenders(entry: Lexeme, tolerance: float) -> list[_Offender]:
    """Return every rendition of this entry carrying too many unfamiliar words.

    Covers every text-bearing field a rendition can live on, at the two reading levels
    that have a band at all. Canonical renditions are included exactly like the graded
    ones: a canonical rendition's level is ``neutral``, which has no band, so it is never
    selected — the filter is the level, not the rank.

    Args:
        entry: The entry to inspect. Never mutated.
        tolerance: How far over its band a share may sit before it counts.

    Returns:
        One :class:`_Offender` per offending rendition, in document order — the order the
        model is shown them in and refers to them by within each call.
    """
    headword = entry.headword
    offenders: list[_Offender] = []

    def consider(
        rendition: Rendition[Any], field_name: str, pos_entry: POSEntry | None, rid: str
    ) -> None:
        share = _share_of(rendition, headword)
        if not _over_band(share, rendition, tolerance):
            return
        offenders.append(
            _Offender(
                rendition=rendition,
                field_name=field_name,
                pos_entry=pos_entry,
                rendition_id=rid,
                share=share,
                words=hard_words(_text_of(rendition), ignore=(headword,))[:_MAX_LISTED_WORDS],
            )
        )

    def rid(owner: str, rendition: Rendition[Any], position: str = "") -> str:
        return rendition_id(owner, rendition.reading_level.value, rendition.style.value) + position

    for pos_entry, sense, sense_id in entry.iter_senses():
        if sense.retired:
            continue
        for rendition in sense.gloss:
            consider(rendition, "gloss", None, rid(sense_id, rendition))
        for index, rendition in enumerate(sense.examples):
            consider(rendition, "examples", pos_entry, rid(sense_id, rendition, f"[{index}]"))
    for rendition in entry.encyclopedia:
        consider(
            rendition, "encyclopedia", None, rid(encyclopedia_owner_id(entry.lexeme_id), rendition)
        )
    for rendition in entry.lexical_explanation:
        consider(
            rendition, "explanation", None, rid(explanation_owner_id(entry.lexeme_id), rendition)
        )
    return offenders


def _chunk_by_word_budget(offenders: Sequence[_Offender]) -> list[list[_Offender]]:
    """Split offenders into chunks whose source text stays under :data:`_WORD_BUDGET`.

    Args:
        offenders: The entry's offending renditions, in document order.

    Returns:
        One or more chunks, each a contiguous slice in the order given. A single offender
        whose own text exceeds the budget is chunked alone rather than dropped: one
        over-budget passage is still one call, not zero.
    """
    chunks: list[list[_Offender]] = []
    current: list[_Offender] = []
    current_words = 0
    for offender in offenders:
        words = word_count(_text_of(offender.rendition))
        if current and current_words + words > _WORD_BUDGET:
            chunks.append(current)
            current = []
            current_words = 0
        current.append(offender)
        current_words += words
    if current:
        chunks.append(current)
    return chunks


def _build_prompt(headword: str, chunk: Sequence[_Offender]) -> str:
    """Return one call's prompt body: every offender in ``chunk``, with its hard words.

    Args:
        headword: The lexeme's surface form.
        chunk: The offenders to list, in the order the model should answer them — ``ref``
            in the reply is a 1-based position into this sequence.

    Returns:
        The per-call prompt body.
    """
    lines = []
    for i, offender in enumerate(chunk):
        rendition = offender.rendition
        # Collapsed to one line: an encyclopedia passage may hold internal newlines, and
        # the listing format is one item per line.
        text = " ".join(_text_of(rendition).split())
        listed = ", ".join(offender.words)
        lines.append(
            f"  {i + 1}. [{offender.field_name} {rendition.reading_level.value}/"
            f"{rendition.style.value}] (too hard: {listed}) {text}"
        )
    listed_items = "\n".join(lines)
    return f"Headword: {headword}\nPassages ({len(chunk)}):\n{listed_items}"


def _note_provenance(base: Provenance, note: str) -> Provenance:
    """Return a zero-cost copy of a call's provenance record, carrying ``note``.

    The real cost of the call is recorded once, on the entry's marker record; this copy
    exists only so each rewritten rendition's superseded text is individually retrievable
    without inflating a naive sum of ``cost_usd`` over the entry's provenance table.
    Mirrors ``workflows/retrofit.py``'s and ``workflows/example_hygiene.py``'s own.

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


def _digest(rendition_ids: Iterable[str]) -> str:
    """Return a stable short hash of the ids a pass is about to answer for (D-47).

    Args:
        rendition_ids: The offending renditions' ids, in any order.

    Returns:
        Sixteen hex characters of SHA-256 over the sorted, newline-joined ids. Sorted so
        the digest does not depend on document order, and SHA-256 rather than :func:`hash`
        because the value is written to disk and compared across processes.
    """
    return hashlib.sha256("\n".join(sorted(rendition_ids)).encode("utf-8")).hexdigest()[:16]


def _attempt_due(entry: Lexeme, rendition_ids: Sequence[str]) -> str | None:
    """Return the marker note to stamp on this entry's next attempt, or ``None``.

    An entry is due an attempt when it has something to fix and either this pass has never
    visited it or the set of things to fix has changed since it last answered — and it has
    not already had :data:`_MAX_ATTEMPTS` of them. This is D-47's rule, re-implemented here
    rather than imported (see the module docstring).

    Args:
        entry: The entry being considered.
        rendition_ids: The ids offending *now*, which is what the attempt would cover.

    Returns:
        The note to write on the call's provenance record, or ``None`` when the entry must
        be skipped — which is also the "do not bill this" signal for the caller.
    """
    if not rendition_ids:
        return None
    digest = _digest(rendition_ids)
    previous_digest: str | None = None
    attempts = 0
    for record in entry.provenance.values():
        note = record.note or ""
        if not note.startswith(f"{_MARKER_PREFIX}:"):
            continue
        previous_digest, _, counted = note[len(_MARKER_PREFIX) + 1 :].partition(_ATTEMPTS_SEPARATOR)
        attempts = int(counted) if counted.isdigit() else 1
    if previous_digest is None:
        return f"{_MARKER_PREFIX}:{digest}{_ATTEMPTS_SEPARATOR}1"
    if previous_digest == digest or attempts >= _MAX_ATTEMPTS:
        return None
    return f"{_MARKER_PREFIX}:{digest}{_ATTEMPTS_SEPARATOR}{attempts + 1}"


def _rejects(entry: Lexeme, offender: _Offender, new_text: str, tolerance: float) -> str | None:
    """Return why a proposed rewrite must be refused, or ``None`` to accept it.

    Four bars, each of them a defect no vocabulary improvement compensates for:

    * the Flesch-Kincaid grade must still be inside its level's band plus ``tolerance``,
      so a rewrite cannot trade five hard words for one very long sentence;
    * a ``gloss`` rewrite of a non-proper-noun entry must not open by naming its own
      headword (D-30, D-47);
    * an ``examples`` rewrite must still contain a form of its headword (D-45);
    * and the share must actually be lower than what is stored, checked by the caller
      since it needs the number either way.

    Args:
        entry: The entry the rendition belongs to.
        offender: The offending rendition and its field.
        new_text: The markdown-stripped rewrite under consideration.
        tolerance: The readability tolerance, from
            :attr:`~opengloss_generator.config.ReadabilityConfig.tolerance`.

    Returns:
        A short reason string for logging, or ``None`` when the rewrite is acceptable.
    """
    level = offender.rendition.reading_level
    if flesch_kincaid_grade(new_text, ignore=(entry.headword,)) > grade_band(level)[1] + tolerance:
        return "readability"
    if (
        offender.field_name == "gloss"
        and entry.kind is not LexemeKind.PROPER_NOUN
        and is_headword_initial(new_text, entry.headword)
    ):
        return "headword_initial"
    if offender.field_name == "examples":
        forms = _forms_for(entry, offender.pos_entry) if offender.pos_entry else []
        if spans.find_span(new_text, entry.headword, forms) is None:
            return "headword_absent"
    return None


def _apply_rewrite(
    entry: Lexeme,
    offender: _Offender,
    drafted_text: str,
    base_provenance: Provenance,
    *,
    tolerance: float,
    vocabulary_tolerance: float,
) -> bool:
    """Apply one drafted rewrite to its offender, if it is verifiably better.

    Whether or not the rewrite is adopted, the rendition leaves this function with both
    metrics measured on whatever text it actually holds and both flags agreeing with them.

    Args:
        entry: The entry the rendition belongs to, mutated in place.
        offender: The offending rendition and the context needed to judge a rewrite.
        drafted_text: The model's proposal, before markdown stripping.
        base_provenance: The call's own provenance record, for the zero-cost note record
            that keeps the superseded text.
        tolerance: The readability tolerance (Flesch-Kincaid grades).
        vocabulary_tolerance: The vocabulary tolerance (unfamiliar-word share).

    Returns:
        Whether the rendition's stored text actually changed.
    """
    rendition = offender.rendition
    headword = entry.headword
    new_text = strip_markdown(drafted_text)
    old_text = _text_of(rendition)
    adopted = False

    if new_text and new_text != old_text:
        new_share = hard_word_share(new_text, ignore=(headword,))
        if new_share >= offender.share:
            _LOG.info(
                "vocabulary_rewrite_rejected",
                headword=headword,
                rendition=offender.rendition_id,
                reason="not_simpler",
                was=round(offender.share, 3),
                now=round(new_share, 3),
            )
        else:
            reason = _rejects(entry, offender, new_text, tolerance)
            if reason is None:
                content = rendition.content
                if isinstance(content, Example):
                    content.text = new_text
                    content.span = spans.find_span(
                        new_text,
                        headword,
                        _forms_for(entry, offender.pos_entry) if offender.pos_entry else [],
                    )
                else:
                    rendition.content = new_text
                adopted = True
            else:
                _LOG.info(
                    "vocabulary_rewrite_rejected",
                    headword=headword,
                    rendition=offender.rendition_id,
                    reason=reason,
                )

    if adopted:
        rendition.provenance_id = entry.add_provenance(_note_provenance(base_provenance, old_text))

    final_text = _text_of(rendition)
    share = hard_word_share(final_text, ignore=(headword,))
    grade = flesch_kincaid_grade(final_text, ignore=(headword,))
    assessment = rendition.assessment or Assessment()
    assessment.hard_word_share = round(share, 3)
    assessment.readability_grade = round(grade, 2)
    _set_flag(
        assessment, QAFlag.OG_HARD_VOCABULARY, _over_band(share, rendition, vocabulary_tolerance)
    )
    _set_flag(
        assessment,
        QAFlag.OG_READABILITY_MISS,
        grade > grade_band(rendition.reading_level)[1] + tolerance,
    )
    rendition.assessment = assessment
    return adopted


def _set_flag(assessment: Assessment, flag: QAFlag, present: bool) -> None:
    """Set or clear one flag on an assessment, so it never lags behind the text."""
    if present:
        assessment.flag(flag)
    elif flag in assessment.qa_flags:
        assessment.qa_flags.remove(flag)


async def _rewrite_chunk(
    entry: Lexeme,
    chunk: Sequence[_Offender],
    runner: StageRunner,
    tally: _Tally,
    *,
    marker_note: str,
) -> int:
    """Rewrite one call's worth of offenders and apply what comes back.

    A call that fails outright leaves every offender exactly as it was and writes no
    marker, so the entry is retried whole on the next sweep — the convention every model
    call in ``workflows/retrofit.py`` follows for its own failures.

    Args:
        entry: The entry being rewritten, mutated in place.
        chunk: The offenders going into this one call.
        runner: The stage runner.
        tally: The pass tally, for the call and its cost.
        marker_note: The offending-set marker to stamp on the call's provenance record.

    Returns:
        How many renditions in ``chunk`` were actually rewritten.

    Raises:
        BudgetExceededError: A budget stop is a run-level condition and propagates.
    """
    try:
        stage_result = await runner.run(
            # Reuses the RENDITIONS policy (luna): audience-held prose, not a structural
            # verdict, so it gets the model the renditions were written by rather than
            # hygiene's nano. See the module docstring.
            stage=StageName.RENDITIONS,
            output_type=_DraftVocabularyRewriteBatch,
            instructions=VOCABULARY_HYGIENE_INSTRUCTIONS,
            prompt=_build_prompt(entry.headword, chunk),
            prompt_version=prompts.PROMPT_VERSION,
        )
    except BudgetExceededError:
        raise
    except GenerationError as exc:
        _LOG.warning("vocabulary_hygiene_failed", headword=entry.headword, error=str(exc))
        return 0

    await tally.call(stage_result.cost_usd)
    # Written unconditionally once the call itself succeeded, so an offender the model did
    # not usefully rewrite is not re-billed for the same offending set on the next sweep.
    entry.add_provenance(stage_result.provenance.model_copy(update={"note": marker_note}))

    policy = runner.config.readability
    rewritten = 0
    answered: set[int] = set()
    for drafted in stage_result.output.rewrites:
        position = drafted.ref - 1
        if not 0 <= position < len(chunk) or position in answered:
            continue
        answered.add(position)
        if _apply_rewrite(
            entry,
            chunk[position],
            drafted.text,
            stage_result.provenance,
            tolerance=policy.tolerance,
            vocabulary_tolerance=policy.vocabulary_tolerance,
        ):
            rewritten += 1

    # An offender the model skipped entirely still has to leave this sweep with its flag
    # and its measurements agreeing with the text it kept.
    for position, offender in enumerate(chunk):
        if position not in answered:
            _apply_rewrite(
                entry,
                offender,
                _text_of(offender.rendition),
                stage_result.provenance,
                tolerance=policy.tolerance,
                vocabulary_tolerance=policy.vocabulary_tolerance,
            )
    return rewritten


async def _clean_entry(
    entry: Lexeme,
    runner: StageRunner,
    tally: _Tally,
) -> tuple[int, dict[str, float], bool]:
    """Rewrite one entry's vocabulary offenders, in place.

    Args:
        entry: The entry to clean, mutated in place.
        runner: The stage runner.
        tally: The pass tally, for the call(s) and their cost.

    Returns:
        ``(renditions rewritten, metric increments, whether the entry needs writing)``.
        The third element is not the first one's truth value: a call that succeeded but
        rewrote nothing still leaves the entry's idempotence marker to be persisted, or
        the next sweep pays for the same answer again.

    Raises:
        BudgetExceededError: A budget stop is a run-level condition and propagates — a
            chunk already applied before the stop stays applied, and the entry is written
            with whatever it holds.
    """
    policy = runner.config.readability
    offenders = _offenders(entry, policy.vocabulary_tolerance)
    marker_note = _attempt_due(entry, [offender.rendition_id for offender in offenders])
    rewritten = 0
    called = False
    if marker_note is not None:
        for chunk in _chunk_by_word_budget(offenders):
            rewritten += await _rewrite_chunk(entry, chunk, runner, tally, marker_note=marker_note)
        called = True

    still_over = sum(
        1
        for offender in offenders
        if _over_band(
            _share_of(offender.rendition, entry.headword),
            offender.rendition,
            policy.vocabulary_tolerance,
        )
    )
    metrics = {
        "renditions_rewritten": float(rewritten),
        "now_in_band": float(len(offenders) - still_over),
        "still_over": float(still_over),
    }
    return rewritten, metrics, called or bool(rewritten)


class _Tally:
    """One sweep's counters, mutated only while holding an ``asyncio.Lock``.

    Mirrors ``workflows/retrofit.py``'s and ``workflows/example_hygiene.py``'s own tallies:
    many handlers touch these counters around many awaits, and single-threaded asyncio only
    makes one await-free statement atomic, not a whole read-modify-write spanning one.
    """

    def __init__(self) -> None:
        """Start an empty outcome."""
        self._lock = asyncio.Lock()
        self._result = VocabularyHygieneOutcome()

    @property
    def result(self) -> VocabularyHygieneOutcome:
        """Return the accumulated outcome; read it once the pool has drained."""
        return self._result

    async def entry(self, *, changed: bool, metrics: dict[str, float]) -> None:
        """Fold one visited entry into the outcome.

        Args:
            changed: Whether anything in this entry actually changed.
            metrics: ``renditions_rewritten``, ``now_in_band`` and ``still_over`` for this
                entry, from :func:`_clean_entry`.
        """
        async with self._lock:
            self._result.entries_scanned += 1
            if changed:
                self._result.entries_changed += 1
            self._result.renditions_rewritten += int(metrics["renditions_rewritten"])
            self._result.now_in_band += int(metrics["now_in_band"])
            self._result.still_over += int(metrics["still_over"])

    async def call(self, cost_usd: float) -> None:
        """Record one completed model call and what it cost."""
        async with self._lock:
            self._result.calls += 1
            self._result.cost_usd += cost_usd

    async def note_stop(self, reason: str) -> None:
        """Record why the sweep stopped early, keeping the first reason given."""
        async with self._lock:
            if self._result.stopped_reason is None:
                self._result.stopped_reason = reason


async def run_vocabulary_hygiene(
    store: LexemeStore,
    runner: StageRunner,
    *,
    workers: int,
    stop_event: asyncio.Event | None = None,
    lexeme_ids: Iterable[str] | None = None,
) -> VocabularyHygieneOutcome:
    """Rewrite every stored grade_1/grade_5 rendition whose words are too hard (D-51).

    Args:
        store: The store to clean. Each entry is read, cleaned — including its call(s)
            when they are due — and written inside one hold of its own lock, exactly the
            discipline ``workflows/retrofit.py`` documents and every one of its passes
            follows.
        runner: The stage runner. Its ``config.readability`` supplies both tolerances.
        workers: Pool size.
        stop_event: Shared stop event; set by a budget stop, and honoured if the caller
            sets it from outside.
        lexeme_ids: Ids to visit; defaults to every id in the store, sorted.

    Returns:
        A :class:`VocabularyHygieneOutcome` carrying counts and cost. If the sweep stopped
        early its ``stopped_reason`` says why; the outcome is still returned rather than
        raised, so a partial sweep reports what it managed to do.
    """
    ids = list(lexeme_ids) if lexeme_ids is not None else sorted(store.iter_ids())
    tally = _Tally()

    async def clean(lexeme_id: str) -> None:
        async with store.locked(lexeme_id):
            entry = store.read(lexeme_id)
            if entry is None:
                return
            changed, metrics, needs_write = await _clean_entry(entry, runner, tally)
            if needs_write:
                store.write(entry)
        await tally.entry(changed=bool(changed), metrics=metrics)

    async def guarded(lexeme_id: str) -> None:
        try:
            await clean(lexeme_id)
        except BudgetExceededError:
            await tally.note_stop("budget")
            raise

    await run_pool(ids, guarded, workers=workers, stop_event=stop_event)
    if stop_event is not None and stop_event.is_set():
        await tally.note_stop("stopped")

    result = tally.result
    _LOG.info("vocabulary_hygiene_complete", **result.as_dict())
    return result
