"""Repair pass — rewrite stored example renditions that never use their own headword.

Companion to D-45's generation-time fix in ``workflows/enrich.py``: that fix catches the
defect for renditions produced from here on; this module is for what is already on disk.
Measured on the core lexicon (docs/CORE-DIARY.md Iteration 6): 2,921 example renditions
had no found span, and 2,575 of those contained no form of the headword at all — the
model wrote around the word ("custody" -> "The judge let both parents care for their
child.", "properties" -> "Dad owns two houses near our school."), worst at grade_1 (820)
and in the v1.3 canonicals (991). An example that does not use the word it illustrates is
defective whatever else is right about it.

:func:`run_example_hygiene` visits every entry once. For each, every example rendition —
any reading level or register, canonical included — whose stored span is ``None`` *and*
whose text :func:`~opengloss_generator.spans.find_span` cannot place at all (given the
sense's own inflected forms plus :func:`~opengloss_generator.spans.generate_forms`) is an
offender. An entry with no offenders costs nothing: no call is made and nothing is
written. An entry with offenders gets exactly one call, on the ``RENDITIONS`` policy
(luna) rather than ``hygiene``'s nano one — this is prose written for an audience, at a
level and register that must be held, not a structural verdict — listing every offender
with its headword, its reading level and register, and the sense's own canonical gloss
for context, so the model can write a sentence that actually fits the sense rather than a
generic one. Each returned sentence is markdown-stripped and re-checked with
:func:`~opengloss_generator.spans.find_span`: a sentence that still doesn't use the
headword is discarded outright, the old text kept, and
:data:`~opengloss_generator.schema.QAFlag.OG_HEADWORD_ABSENT` is set; a sentence that does
replaces the stored text, gets its span set from the same find, has its Flesch-Kincaid
grade re-measured into its own ``Assessment``, and has the flag cleared. The superseded
text is kept in a zero-cost ``Provenance.note``, exactly as
``workflows/retrofit.py``'s ``rendition_hygiene`` and ``readability_hygiene`` passes keep
one.

Concurrency and locking (D-31) mirror ``workflows/retrofit.py`` throughout: the unit of
work is one entry, and the handler holding that entry's lock reads it, does the
deterministic offender scan, makes the one call if one is due, and writes it back, all
inside the same lock hold — no entry is ever read outside the lock it is written under.
Counters are accumulated by :class:`_Tally`, mutated only while holding an
``asyncio.Lock``, for the same reason ``retrofit.py``'s own ``_Tally`` gives: many
handlers touch these counters around many awaits, and single-threaded asyncio only makes
one await-free statement atomic, not a whole read-modify-write spanning one.
Idempotence is by a private ``Provenance.note`` sentinel
(:data:`_EXAMPLE_HYGIENE_NOTE`), the same convention ``rendition_hygiene`` and
``readability_hygiene`` use, rather than a dedicated ``StageName``: this pass reuses the
``RENDITIONS`` stage's policy for its one call site instead of adding a stage for it, so
the marker cannot be the stage alone or it would collide with an ordinary
rendition-generation record.

This module is deliberately self-contained, importing nothing from
``workflows/retrofit.py`` and exporting nothing it depends on: two other passes of work
are landing in that module concurrently on this branch, and a new, independent module is
the only way to add this fix without conflicting with either. Wiring
``retrofit --only example_hygiene`` into a ``RetrofitPass`` member is left to whoever next
touches ``retrofit.py``/``cli.py``; :func:`run_example_hygiene` is written to be callable
exactly the way that wiring will need — store, runner, worker count, an optional shared
stop event, an optional explicit id list.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING, Annotated

from pydantic import BaseModel, ConfigDict, Field

from opengloss_generator import spans
from opengloss_generator.errors import BudgetExceededError, GenerationError
from opengloss_generator.log import get_logger
from opengloss_generator.prompts import PROMPT_VERSION
from opengloss_generator.readability import flesch_kincaid_grade, strip_markdown
from opengloss_generator.runner import run_pool
from opengloss_generator.schema import Assessment, QAFlag, StageName

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from opengloss_generator.schema import (
        Example,
        Lexeme,
        POSEntry,
        Provenance,
        Rendition,
        Sense,
    )
    from opengloss_generator.stages import StageRunner
    from opengloss_generator.store import LexemeStore

__all__ = ["ExampleHygieneOutcome", "run_example_hygiene"]

_LOG = get_logger(__name__)

#: Sentinel written to the ``note`` of this pass's one model call per entry, so a later
#: sweep can tell "example_hygiene already tried this entry" apart from an ordinary
#: ``enrich.py`` rendition-generation record (which carries no note at all) or one of
#: ``retrofit.py``'s own note-stamped records — all reuse a shared stage's policy rather
#: than adding one of their own, so each needs its own sentinel (see the module
#: docstring).
_EXAMPLE_HYGIENE_NOTE = "example_hygiene:rewritten"

#: Instructions for this pass's one luna call per entry. Kept short and byte-stable so it
#: caches like every other stage's instructions do.
EXAMPLE_HYGIENE_INSTRUCTIONS = """\
Write a replacement example sentence for each numbered item below. Each item names its \
headword, the sense it must illustrate, and the reading level and register the sentence \
must stay at.

The previous sentence for each item never used the headword at all, which is the defect \
you are fixing: the new sentence must contain the headword itself or a natural inflected \
form of it (a plural, a past tense, an -ing form, and so on). It must fit the numbered \
sense's meaning and no other, read naturally at the stated reading level and register, \
and be the kind of sentence a person would actually write or say, not a corpus-style or \
academic-register construction. Keep to about the same length as the sentence it \
replaces, and in any case at most 20 words for grade_1 or grade_5.

Plain prose, no markdown.

Answer every item you are given, identified by the number it was listed under."""


class _DraftExampleRewrite(BaseModel):
    """One rewritten example sentence for a headword-absent offender."""

    model_config = ConfigDict(
        extra="forbid", validate_by_name=True, validate_by_alias=True, serialize_by_alias=True
    )

    ref: Annotated[int, Field(ge=1)]
    text: Annotated[str, Field(min_length=3, max_length=1000)]


class _DraftExampleRewriteBatch(BaseModel):
    """Rewrites for every headword-absent example of one entry, produced together."""

    model_config = ConfigDict(
        extra="forbid", validate_by_name=True, validate_by_alias=True, serialize_by_alias=True
    )

    rewrites: Annotated[list[_DraftExampleRewrite], Field(min_length=1)]


@dataclass(slots=True)
class ExampleHygieneOutcome:
    """What one :func:`run_example_hygiene` sweep did across the store."""

    entries_scanned: int = 0
    entries_changed: int = 0
    examples_rewritten: int = 0
    spans_found: int = 0
    still_absent: int = 0
    calls: int = 0
    cost_usd: float = 0.0
    stopped_reason: str | None = None


def _forms_for(entry: Lexeme, pos_entry: POSEntry) -> list[str]:
    """Return the surface forms to try when locating the headword in an example.

    Mirrors ``workflows/enrich.py``'s and ``workflows/retrofit.py``'s own private
    ``_forms_for``: the sense's own morphology first, falling back to the cheap
    rule-based forms when the model supplied none.
    """
    forms = [*pos_entry.morphology.inflected_forms(), *pos_entry.morphology.derivations]
    return forms or list(spans.generate_forms(entry.headword))


@dataclass(slots=True)
class _ExampleOffender:
    """One stored example rendition whose text uses no form of its own headword.

    Attributes:
        rendition: The offending rendition, mutated in place once a rewrite is applied.
        pos_entry: The owning part-of-speech entry, needed to look up the headword's
            inflected forms when re-checking a candidate rewrite.
        gloss: The owning sense's canonical gloss, shown to the model for context so the
            replacement sentence fits this sense and not another.
        label: ``"level/register"``, shown to the model alongside the gloss.
    """

    rendition: Rendition[Example]
    pos_entry: POSEntry
    sense: Sense
    gloss: str
    label: str


def _headword_absent_examples(entry: Lexeme) -> list[_ExampleOffender]:
    """Return every stored example rendition that never uses its own headword.

    An example qualifies only when *both* of D-45's conditions hold: its span is not yet
    found, and the free, deterministic :func:`~opengloss_generator.spans.find_span`
    cannot place any known form of the headword in its text either. An example whose span
    is merely unplaced because the deterministic matcher missed an irregular form it
    (correctly) does not know about — the ``spans`` retrofit pass's job — is not an
    offender here: that example still uses the word, it just wasn't found yet.

    Args:
        entry: The entry to inspect. Never mutated.

    Returns:
        One :class:`_ExampleOffender` per offending rendition, in document order — the
        order the model is shown them in and refers to them by.
    """
    offenders: list[_ExampleOffender] = []
    for pos_entry, sense, _ in entry.iter_senses():
        if sense.retired:
            continue
        forms = _forms_for(entry, pos_entry)
        gloss = sense.canonical_gloss()
        for rendition in sense.examples:
            example = rendition.content
            if example.span is not None:
                continue
            if spans.find_span(example.text, entry.headword, forms) is not None:
                continue
            offenders.append(
                _ExampleOffender(
                    rendition=rendition,
                    pos_entry=pos_entry,
                    sense=sense,
                    gloss=gloss,
                    label=f"{rendition.reading_level.value}/{rendition.style.value}",
                )
            )
    return offenders


def _build_example_hygiene_prompt(headword: str, offenders: Sequence[_ExampleOffender]) -> str:
    """Return the volatile half of this pass's rewrite prompt.

    Args:
        headword: The lexeme's surface form.
        offenders: The examples to rewrite, in the order the model should answer them —
            ``ref`` in the reply is a 1-based position into this sequence.

    Returns:
        The per-call prompt body.
    """
    lines = []
    for i, offender in enumerate(offenders):
        # Collapsed to one line: the listing format below is one item per line.
        text = " ".join(offender.rendition.content.text.split())
        lines.append(f"  {i + 1}. [{offender.label}] (sense: {offender.gloss}) {text}")
    listed = "\n".join(lines)
    return f"Headword: {headword}\nExamples ({len(offenders)}):\n{listed}"


def _note_provenance(base: Provenance, note: str) -> Provenance:
    """Return a zero-cost copy of a stage's provenance record, carrying ``note``.

    The real cost and token counts of the call are recorded once, on the entry's generic
    call marker; this copy exists only so each rewritten example's superseded text is
    individually retrievable, without inflating a naive sum of ``cost_usd`` over the
    entry's provenance table. Mirrors ``workflows/retrofit.py``'s own ``_note_provenance``.

    Args:
        base: The call's own provenance record.
        note: The superseded example text to preserve.

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


def _has_rewritten_examples(entry: Lexeme) -> bool:
    """Return whether this pass has already made its one call for an entry."""
    return any(record.note == _EXAMPLE_HYGIENE_NOTE for record in entry.provenance.values())


def _collides(offender: _ExampleOffender, new_text: str) -> bool:
    """Return whether adopting ``new_text`` would duplicate a sibling rendition's key."""
    own = offender.rendition
    return any(
        other is not own
        and other.reading_level is own.reading_level
        and other.style is own.style
        and other.content.text == new_text
        for other in offender.sense.examples
    )


def _apply_example_rewrite(
    entry: Lexeme,
    offender: _ExampleOffender,
    drafted_text: str,
    base_provenance: Provenance,
) -> bool:
    """Apply one drafted rewrite to its offending example, if it actually uses the word.

    The rewrite is markdown-stripped and re-checked with
    :func:`~opengloss_generator.spans.find_span` before it is adopted at all: a rewrite
    the finder still cannot place has not fixed anything, whatever else is right about
    it, and the old text is kept untouched. A rewrite that is adopted gets its span set
    from the same find and its Flesch-Kincaid grade re-measured into its own
    ``Assessment`` (the headword scored as one syllable, matching how
    ``workflows/enrich.py`` measures every other rendition), and
    :data:`~opengloss_generator.schema.QAFlag.OG_HEADWORD_ABSENT` is cleared, since a span
    was just found for it.

    Args:
        entry: The entry the rendition belongs to, mutated in place.
        offender: The offending rendition and the context needed to rewrite it.
        drafted_text: The model's proposed replacement, before markdown stripping.
        base_provenance: The call's own provenance record, used to build the zero-cost
            note record that keeps the superseded text.

    Returns:
        Whether the rewrite was adopted (a span was found for it).
    """
    example = offender.rendition.content
    new_text = strip_markdown(drafted_text)
    if not new_text:
        return False
    forms = _forms_for(entry, offender.pos_entry)
    span = spans.find_span(new_text, entry.headword, forms)
    if span is None:
        return False

    # A rewrite that lands on text the sense already holds at the same level and
    # register would make two renditions share their uniqueness key, and the entry would
    # then fail validation on its next read (seen once on the tier-2 sweep, 2026-09-03).
    # Keep the old text instead; the offender stays flagged and the next sweep retries.
    if _collides(offender, new_text):
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
    if QAFlag.OG_HEADWORD_ABSENT in assessment.qa_flags:
        assessment.qa_flags.remove(QAFlag.OG_HEADWORD_ABSENT)
    offender.rendition.assessment = assessment
    return True


def _flag_still_absent(rendition: Rendition[Example]) -> None:
    """Flag an example whose rewrite (or lack of one) still doesn't use the headword."""
    assessment = rendition.assessment or Assessment()
    assessment.flag(QAFlag.OG_HEADWORD_ABSENT)
    rendition.assessment = assessment


async def _rewrite_examples(
    entry: Lexeme,
    offenders: Sequence[_ExampleOffender],
    runner: StageRunner,
    tally: _Tally,
) -> int:
    """Ask the model to rewrite one entry's headword-absent examples, and apply the reply.

    Every offender not successfully rewritten — its answer still didn't use the
    headword — is flagged :data:`~opengloss_generator.schema.QAFlag.OG_HEADWORD_ABSENT`
    rather than silently left as it was, so the defect stays queryable on the stored
    entry. A call that fails outright leaves every offender exactly as it was and writes
    no marker, so the entry is retried whole on the next sweep — the same convention
    every model call in ``workflows/retrofit.py`` follows for its own failures.

    Args:
        entry: The entry whose offending examples need rewriting, mutated in place.
        offenders: The examples to rewrite, in the order the model was shown them.
        runner: The stage runner.
        tally: The pass tally to accumulate cost and call count onto.

    Returns:
        How many examples were actually rewritten (a span was found for the rewrite).

    Raises:
        BudgetExceededError: A budget stop is a run-level condition and propagates.
    """
    try:
        stage_result = await runner.run(
            # Reuses the RENDITIONS policy (luna): this is prose for an audience, held to
            # a reading level and register, not a structural verdict — see the module
            # docstring.
            stage=StageName.RENDITIONS,
            output_type=_DraftExampleRewriteBatch,
            instructions=EXAMPLE_HYGIENE_INSTRUCTIONS,
            prompt=_build_example_hygiene_prompt(entry.headword, offenders),
            prompt_version=PROMPT_VERSION,
        )
    except BudgetExceededError:
        raise
    except GenerationError as exc:
        _LOG.warning("example_hygiene_failed", headword=entry.headword, error=str(exc))
        return 0

    await tally.call(stage_result.cost_usd)
    # Written unconditionally once the call itself succeeded, so an entry the model did
    # not usefully answer for is not re-billed on the next sweep — the same convention
    # every model call in ``workflows/retrofit.py`` uses for its own note-stamped passes.
    entry.add_provenance(stage_result.provenance.model_copy(update={"note": _EXAMPLE_HYGIENE_NOTE}))

    rewritten: set[int] = set()
    for drafted in stage_result.output.rewrites:
        position = drafted.ref - 1
        if not 0 <= position < len(offenders):
            continue
        if _apply_example_rewrite(
            entry, offenders[position], drafted.text, stage_result.provenance
        ):
            rewritten.add(position)

    for i, offender in enumerate(offenders):
        if i not in rewritten:
            _flag_still_absent(offender.rendition)
    return len(rewritten)


async def _clean_examples(
    entry: Lexeme,
    runner: StageRunner,
    tally: _Tally,
) -> tuple[int, dict[str, float], bool]:
    """Rewrite one entry's headword-absent examples, in place.

    Args:
        entry: The entry to clean, mutated in place.
        runner: The stage runner.
        tally: The pass tally, for the call and its cost.

    Returns:
        ``(examples rewritten, metric increments, whether the entry needs writing)``. The
        third element is not the first one's truth value: a call that succeeded but
        rewrote nothing still leaves the entry's idempotence marker to be persisted, or
        the next sweep pays for the same answer again.

    Raises:
        BudgetExceededError: A budget stop is a run-level condition and propagates —
            before anything is written, so the entry is left exactly as it was found.
    """
    offenders = _headword_absent_examples(entry)
    rewritten = 0
    called = False
    if offenders and not _has_rewritten_examples(entry):
        rewritten = await _rewrite_examples(entry, offenders, runner, tally)
        called = True

    still_absent = len(_headword_absent_examples(entry)) if offenders else 0
    metrics = {
        "examples_rewritten": float(rewritten),
        "spans_found": float(rewritten),
        "still_absent": float(still_absent),
    }
    return rewritten, metrics, called or bool(rewritten)


class _Tally:
    """One sweep's counters, mutated only while holding an ``asyncio.Lock``.

    Mirrors ``workflows/retrofit.py``'s own ``_Tally``: many handlers touch these
    counters around many awaits, and single-threaded asyncio only makes one await-free
    statement atomic, not a whole read-modify-write spanning one. Every mutation
    therefore goes through this class, inside the lock, so the discipline is visible,
    testable, and does not quietly break the first time a handler grows an ``await``
    between reading a counter and writing it back.
    """

    def __init__(self) -> None:
        """Start an empty outcome."""
        self._lock = asyncio.Lock()
        self._result = ExampleHygieneOutcome()

    @property
    def result(self) -> ExampleHygieneOutcome:
        """Return the accumulated outcome; read it once the pool has drained."""
        return self._result

    async def entry(self, *, changed: bool, metrics: dict[str, float]) -> None:
        """Fold one visited entry into the outcome.

        Args:
            changed: Whether anything in this entry actually changed.
            metrics: ``examples_rewritten``, ``spans_found`` and ``still_absent`` for
                this entry, from :func:`_clean_examples`.
        """
        async with self._lock:
            self._result.entries_scanned += 1
            if changed:
                self._result.entries_changed += 1
            self._result.examples_rewritten += int(metrics["examples_rewritten"])
            self._result.spans_found += int(metrics["spans_found"])
            self._result.still_absent += int(metrics["still_absent"])

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


async def run_example_hygiene(
    store: LexemeStore,
    runner: StageRunner,
    *,
    lexeme_ids: Iterable[str] | None = None,
    workers: int,
    stop_event: asyncio.Event | None = None,
) -> ExampleHygieneOutcome:
    """Rewrite every stored example that uses no form of its own headword (D-45).

    Args:
        store: The store to clean. Each entry is read, cleaned — including its one model
            call when it is due — and written inside one hold of its own lock, exactly
            the discipline ``workflows/retrofit.py`` documents and every one of its own
            passes follows.
        runner: The stage runner.
        lexeme_ids: Ids to visit; defaults to every id in the store, sorted. Lets a future
            ``retrofit --only example_hygiene`` wiring pass the same id list every other
            pass uses.
        workers: Pool size.
        stop_event: Shared stop event; set by a budget stop, and honoured if the caller
            sets it from outside.

    Returns:
        An :class:`ExampleHygieneOutcome` carrying counts and cost for the sweep. If the
        sweep stopped early its ``stopped_reason`` says why; the outcome is still
        returned rather than raised, so a partial sweep reports what it managed to do.
    """
    ids = list(lexeme_ids) if lexeme_ids is not None else sorted(store.iter_ids())
    tally = _Tally()

    async def clean(lexeme_id: str) -> None:
        async with store.locked(lexeme_id):
            entry = store.read(lexeme_id)
            if entry is None:
                return
            changed, metrics, needs_write = await _clean_examples(entry, runner, tally)
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
    _LOG.info(
        "example_hygiene_complete",
        entries_scanned=result.entries_scanned,
        entries_changed=result.entries_changed,
        examples_rewritten=result.examples_rewritten,
        spans_found=result.spans_found,
        still_absent=result.still_absent,
        calls=result.calls,
        cost_usd=round(result.cost_usd, 6),
        stopped_reason=result.stopped_reason,
    )
    return result
