"""Workflow 3 — expand or improve an existing entry.

In v3 enrichment is **one uniform operation**. Every text-bearing field on an entry is a
:class:`~opengloss_generator.schema.Renditions` set, so "add graded definitions", "add
register variants of the examples" and "rewrite the encyclopedia for a ten-year-old" are
the same request against different owners:

    RenditionRequest(field=gloss|examples|encyclopedia|explanation, levels, styles)

For each request the workflow computes ``Renditions.missing(...)`` on the owning object —
the sense for ``gloss`` and ``examples``, the entry for the two prose sections — and
issues exactly one model call per ``(owner, field)`` covering every missing target at
once. One call that sees the canonical text and produces four graded rewrites is cheaper
than four calls and, more importantly, lets the model differentiate them from each other
(FR-3.4). An empty diff means no call and no cost (FR-3.5).

Every returned rendition is then measured. A reading level the model was told to hit is
not a reading level it hit — the first core pilot produced a "grade 1" encyclopedia entry
containing ``m/s^2`` — so each rewrite is scored with
:func:`~opengloss_generator.readability.flesch_kincaid_grade`, the score is stored on its
``Assessment``, and a ``grade_1``/``grade_5`` rewrite that misses its band by more than
``config.readability.tolerance`` is re-requested **once**, for the failing targets only,
with the measured grade and the limit named in the prompt. The better of the two (the
lower grade) is kept; both calls are priced. One retry, never a loop: the second attempt
is where nearly all of the improvement is, and the prompt prefix is identical so its
input is served from the provider's cache.

A gloss rendition is checked a second way, and the two checks share that one retry
(D-39). ``RENDITIONS_INSTRUCTIONS`` forbids a definition that opens by naming its own
headword, and forbidding it is not enough: over 400 swept core entries the canonical
glosses offend at 2.7% but their renditions at 10-15% at every non-canonical target,
because a ten-word sentence budget pulls the model straight to "A ban is an order to
stop." So :func:`~opengloss_generator.hygiene.is_headword_initial` runs over every gloss
rendition of a non-proper-noun entry (proper nouns are exempt, D-30) and a hit is a miss
exactly like a readability miss: the same targets, the same single retry — a target
failing both checks gets one retry carrying both feedback lines, never two — with the
non-initial candidate kept, and :data:`~opengloss_generator.schema.QAFlag.OG_HEADWORD_INITIAL`
set on whatever still opens badly afterwards. ``workflows/retrofit.py``'s
``rendition_hygiene`` pass is the same fix applied to renditions already on disk.

An example rendition gets its own third check, on the same shared-retry footing (D-45).
Measured on the core lexicon (docs/CORE-DIARY.md Iteration 6): 2,921 example renditions
had no found span, and 2,575 of those did not contain any form of the headword at all —
the model wrote around the word ("custody" -> "The judge let both parents care for their
child."). An example whose text is scored by :func:`~opengloss_generator.spans.find_span`
(with the sense's :meth:`~opengloss_generator.schema.Morphology.inflected_forms` and
:func:`~opengloss_generator.spans.generate_forms` as candidate forms) as containing no
form at all is a miss exactly like the other two: it joins the same single combined
retry, with feedback from :func:`~opengloss_generator.prompts.build_headword_absent_feedback`,
and what is still absent afterwards carries
:data:`~opengloss_generator.schema.QAFlag.OG_HEADWORD_ABSENT`. Unlike the headword-initial
check, this one is not exempt for proper nouns — an example sentence has to use its
headword whatever kind of entry it belongs to. ``workflows/example_hygiene.py``'s
``run_example_hygiene`` is the same fix applied to examples already on disk.

A fourth check rides the same retry (D-51), and it is the one the judge found matters
most. Flesch-Kincaid measures sentence and syllable length, not whether the reader knows
the words: a judged sample of the core (docs/QA-DIARY.md, iteration 1) found **46.6% of
grade_1 encyclopedia renditions not level-appropriate although every one of them passed
its FK band** — "Monks made vows of poverty, chastity, and obedience." is nine short
words. So every rendition is also measured with
:func:`~opengloss_generator.vocabulary.hard_word_share`, the share of its words that are
not on the Dale-Chall familiar-word list, and that share is stored on its ``Assessment``
at *every* level because it is free and it is the only familiarity signal the pipeline
has. At ``grade_1`` and ``grade_5`` — the only levels with a band
(:func:`~opengloss_generator.vocabulary.vocabulary_band`: 0.10 and 0.25) — a share over
its band plus ``config.readability.vocabulary_tolerance`` is a miss like the other three:
the same single retry, carrying feedback from
:func:`~opengloss_generator.prompts.build_vocabulary_feedback` that *names the offending
words*, the lower share kept, and
:data:`~opengloss_generator.schema.QAFlag.OG_HARD_VOCABULARY` on whatever is still over.
``workflows/vocabulary_hygiene.py``'s ``run_vocabulary_hygiene`` is the same fix applied
to renditions already on disk.

A fifth check rides the same retry, and it is about wording rather than difficulty
(D-59, F7). ``RENDITIONS_INSTRUCTIONS`` now asks for a lexical diversity of 0.30-0.60
against the canonical gloss on every register rewrite and bans copying it verbatim, and
asking is not enough any more than it was for the headword-initial rule: a model asked
for several registers of a ten-word sentence at once can satisfy the letter of the
request by swapping a synonym or two. So every non-``plain`` gloss rendition is measured
with :func:`~opengloss_generator.hygiene.is_near_copy` against the sense's canonical
gloss, and a rendition whose content-word set overlaps it at 0.9 Jaccard similarity or
more — 0.1 or less lexical diversity, well below the 0.30 floor the prompt asks for — is
a miss like the other four: the same single retry, carrying feedback from
:func:`~opengloss_generator.prompts.build_near_copy_feedback`, the more diverse candidate
kept, and :data:`~opengloss_generator.schema.QAFlag.OG_NEAR_COPY` set on whatever still
copies afterwards. Unlike the headword-initial check there is no proper-noun exemption:
a proper noun's registers still have to read differently from each other.
``workflows/retrofit.py``'s ``rendition_hygiene`` pass applies the same check to
renditions already on disk, but only to flag them — a paraphrase a model was already
asked to write differently is not fixed by asking it again in the same words, so that
pass does not spend a call on it the way it does on a headword-initial rewrite.

Section filling (etymology, encyclopedia, lexical explanation) is the other half of the
workflow and is unchanged: it creates the *canonical* rendition that the rendition
requests then rewrite, which is why it runs first.
"""

from __future__ import annotations

import asyncio
import itertools
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from opengloss_generator import prompts, spans
from opengloss_generator.contracts import (
    DraftEncyclopedia,
    DraftEtymology,
    DraftLexicalExplanation,
    DraftRenditionSet,
)
from opengloss_generator.errors import BudgetExceededError, StageFailedError
from opengloss_generator.hygiene import is_headword_initial, is_near_copy
from opengloss_generator.log import get_logger
from opengloss_generator.readability import (
    flesch_kincaid_grade,
    grade_band,
    strip_markdown,
)
from opengloss_generator.schema import (
    Assessment,
    Example,
    Lexeme,
    LexemeKind,
    POSEntry,
    Provenance,
    QAFlag,
    ReadingLevel,
    Register,
    Rendition,
    Renditions,
    Sense,
    StageName,
)
from opengloss_generator.vocabulary import hard_word_share, hard_words, vocabulary_band
from opengloss_generator.workflows.generate import attach_long_form

if TYPE_CHECKING:
    from opengloss_generator.config import ReadabilityConfig
    from opengloss_generator.contracts import DraftRendition
    from opengloss_generator.stages import StageResult, StageRunner

__all__ = [
    "EnrichmentOutcome",
    "EnrichmentSpec",
    "RenditionField",
    "RenditionRequest",
    "enrich_entry",
    "plan_renditions",
]

_LOG = get_logger(__name__)


class RenditionField(StrEnum):
    """Which text-bearing field a rendition request targets."""

    GLOSS = "gloss"
    EXAMPLES = "examples"
    ENCYCLOPEDIA = "encyclopedia"
    EXPLANATION = "explanation"


@dataclass(slots=True)
class RenditionRequest:
    """A set of rendition targets wanted for one field.

    The two axes are crossed. Four reading levels and four registers ask for sixteen
    renditions; reading levels alone pair each with ``plain``, and registers alone pair
    each with ``neutral`` — which is what makes "graded definitions" and "parallel
    registers" the same request with a different axis filled in.
    """

    field: RenditionField
    levels: list[ReadingLevel] = field(default_factory=list)
    styles: list[Register] = field(default_factory=list)

    def targets(self) -> list[tuple[ReadingLevel, Register]]:
        """Return the requested ``(reading_level, register)`` pairs, in a stable order."""
        levels = self.levels or [ReadingLevel.NEUTRAL]
        styles = self.styles or [Register.PLAIN]
        return list(itertools.product(levels, styles))


@dataclass(slots=True)
class EnrichmentSpec:
    """What to add to an existing entry."""

    renditions: list[RenditionRequest] = field(default_factory=list)
    with_etymology: bool = False
    with_encyclopedia: bool = False
    with_lexical_explanation: bool = False
    replace: bool = False

    @classmethod
    def for_glosses(
        cls,
        reading_levels: Sequence[ReadingLevel] = (),
        registers: Sequence[Register] = (),
        **kwargs: Any,  # noqa: ANN401 - the remaining EnrichmentSpec field values
    ) -> EnrichmentSpec:
        """Build a spec asking for gloss renditions only.

        Args:
            reading_levels: Requested reading levels.
            registers: Requested registers.
            **kwargs: Any other :class:`EnrichmentSpec` field values.

        Returns:
            A spec with a single ``gloss`` request, or none if neither axis was given.
        """
        requests = []
        if reading_levels or registers:
            requests.append(
                RenditionRequest(
                    field=RenditionField.GLOSS,
                    levels=list(reading_levels),
                    styles=list(registers),
                )
            )
        return cls(renditions=requests, **kwargs)


@dataclass(slots=True)
class EnrichmentOutcome:
    """The enriched entry and the accounting for producing it."""

    entry: Lexeme
    cost_usd: float
    calls: int
    renditions_added: int
    sections_added: list[str] = field(default_factory=list)
    failed_stages: list[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        """Return whether anything was actually added."""
        return bool(self.renditions_added or self.sections_added)


@dataclass(slots=True)
class _Work:
    """One ``(owner, field)`` unit of rendition work: exactly one model call."""

    field: RenditionField
    label: str
    source: str
    renditions: Renditions[Any]
    targets: list[tuple[ReadingLevel, Register]]
    existing: list[tuple[str, str, str]]
    forms: list[str]


@dataclass(slots=True)
class _Measured:
    """One produced rendition: its stored text and the grade level that text measures."""

    text: str
    grade: float
    from_retry: bool = False
    #: Whether this rendition still misses its reading level's band in its final,
    #: post-retry state (see :func:`_misses_band`). Drives :data:`QAFlag.OG_READABILITY_MISS`
    #: in :func:`_apply_renditions` (docs/STANDARDS-PLAN.md § 3, B3).
    missed_band: bool = False
    #: Whether this rendition's own text begins by naming the headword
    #: (:func:`~opengloss_generator.hygiene.is_headword_initial`). Unlike ``missed_band``
    #: this is a property of the text itself, so it is measured once per candidate and
    #: whichever candidate is kept carries its own verdict; it drives
    #: :data:`QAFlag.OG_HEADWORD_INITIAL` in :func:`_apply_renditions` (D-39). Always
    #: ``False`` where the check does not apply: a non-gloss field, a proper noun, or the
    #: check switched off.
    headword_initial: bool = False
    #: Whether this rendition's own text contains no form of the headword at all
    #: (:func:`~opengloss_generator.spans.find_span` finds nothing). Like
    #: ``headword_initial`` this is a property of the text itself, measured once per
    #: candidate. Only ever ``True`` for the ``examples`` field with the check enabled —
    #: it drives :data:`~opengloss_generator.schema.QAFlag.OG_HEADWORD_ABSENT` in
    #: :func:`_apply_renditions` (D-45).
    headword_absent: bool = False
    #: The share of this rendition's words that are not on the familiar-word list
    #: (:func:`~opengloss_generator.vocabulary.hard_word_share`), measured on every
    #: candidate at every level and stored on the kept one's ``Assessment`` (D-51).
    hard_share: float = 0.0
    #: The offending words themselves, in order and without repeats, so a retry's
    #: feedback can name them.
    hard_terms: list[str] = field(default_factory=list)
    #: Whether this rendition still carries too many unfamiliar words for its level in
    #: its final, post-retry state (see :func:`_misses_vocabulary`). Like ``missed_band``
    #: and unlike the two headword verdicts this depends on the target's level as well as
    #: on the text, so it is set once at the end rather than per candidate; it drives
    #: :data:`QAFlag.OG_HARD_VOCABULARY` in :func:`_apply_renditions`.
    over_vocabulary: bool = False
    #: Whether this rendition's own text is a near-copy of the canonical gloss it was
    #: rewritten from (:func:`~opengloss_generator.hygiene.is_near_copy`). Like
    #: ``headword_initial`` and ``headword_absent`` this is a property of the text itself,
    #: measured once per candidate, so whichever candidate survives already carries its
    #: own verdict. Only ever ``True`` for a non-``plain``-register ``gloss`` rendition
    #: with the check enabled — it drives
    #: :data:`~opengloss_generator.schema.QAFlag.OG_NEAR_COPY` in :func:`_apply_renditions`
    #: (D-59).
    near_copy: bool = False


@dataclass(slots=True)
class _Rendered:
    """Everything one ``(owner, field)`` produced, across its call and optional retry."""

    produced: dict[tuple[ReadingLevel, Register], _Measured]
    first: Provenance
    cost_usd: float
    calls: int
    retry: Provenance | None = None

    def provenance_ids(self, entry: Lexeme) -> dict[bool, str | None]:
        """Register the provenance actually referenced and return ids by origin.

        Args:
            entry: The entry whose provenance table receives the records.

        Returns:
            A ``{from_retry: provenance_id}`` map. A call whose every rendition was
            superseded by its retry contributes no record, so the table has no orphans.
        """
        origins = {measured.from_retry for measured in self.produced.values()}
        ids: dict[bool, str | None] = {False: None, True: None}
        if False in origins:
            ids[False] = entry.add_provenance(self.first)
        if True in origins and self.retry is not None:
            ids[True] = entry.add_provenance(self.retry)
        return ids


def plan_renditions(entry: Lexeme, spec: EnrichmentSpec) -> list[tuple[str, str, int]]:
    """Return the work this spec implies, as ``(owner label, field, missing count)``.

    A planning view for callers that want to price or preview an enrichment without
    running it. The empty list means no model call will be made.

    Args:
        entry: The entry to inspect.
        spec: The requested renditions.

    Returns:
        One triple per ``(owner, field)`` that has missing targets.
    """
    return [(work.label, work.field.value, len(work.targets)) for work in _plan(entry, spec)]


async def enrich_entry(
    entry: Lexeme,
    spec: EnrichmentSpec,
    runner: StageRunner,
) -> EnrichmentOutcome:
    """Add the requested content to an entry, skipping anything already present.

    Args:
        entry: The entry to enrich. Mutated in place and also returned.
        spec: What to add.
        runner: The stage runner.

    Returns:
        An :class:`EnrichmentOutcome`. ``changed`` is ``False`` and ``cost_usd`` is
        exactly ``0.0`` when the entry already had everything requested.
    """
    cost = 0.0
    calls = 0
    failures: list[str] = []

    # Sections first: filling one creates the canonical rendition a rendition request for
    # that field would otherwise have nothing to rewrite from.
    s_cost, s_calls, sections, s_failures = await _add_sections(entry, spec, runner)
    cost += s_cost
    calls += s_calls
    failures.extend(s_failures)

    r_cost, r_calls, added, r_failures = await _add_renditions(entry, spec, runner)
    cost += r_cost
    calls += r_calls
    failures.extend(r_failures)

    return EnrichmentOutcome(
        entry=entry,
        cost_usd=cost,
        calls=calls,
        renditions_added=added,
        sections_added=sections,
        failed_stages=failures,
    )


# --------------------------------------------------------------------------------------
# Renditions
# --------------------------------------------------------------------------------------


def _forms_for(entry: Lexeme, pos_entry: POSEntry) -> list[str]:
    """Return the surface forms used to place the headword in a rewritten example."""
    forms = [*pos_entry.morphology.inflected_forms(), *pos_entry.morphology.derivations]
    return forms or list(spans.generate_forms(entry.headword))


def _existing_view(renditions: Renditions[Any]) -> list[tuple[str, str, str]]:
    """Return ``(reading_level, register, text)`` for what a set already holds."""
    view: list[tuple[str, str, str]] = []
    for rendition in renditions:
        content = rendition.content
        text = content.text if isinstance(content, Example) else str(content)
        view.append((rendition.reading_level.value, rendition.style.value, text))
    return view


def _sense_work(
    entry: Lexeme,
    pos_entry: POSEntry,
    sense: Sense,
    sense_id: str,
    request: RenditionRequest,
) -> _Work | None:
    """Return the rendition work for one sense and one field, or ``None`` if none."""
    if request.field is RenditionField.GLOSS:
        renditions: Renditions[Any] = sense.gloss
        source = strip_markdown(sense.canonical_gloss())
    else:
        renditions = sense.examples
        canonical = sense.examples.canonical()
        if canonical is None:
            return None
        # Only the first canonical example is rewritten per target. Rewriting all of them
        # would multiply output tokens by the example count for a set the reader sees one
        # of; the canonical set stays the place to add more examples.
        source = strip_markdown(canonical.content.text)
    missing = renditions.missing(request.targets())
    if not missing:
        return None
    return _Work(
        field=request.field,
        label=sense_id,
        source=source,
        renditions=renditions,
        targets=missing,
        existing=_existing_view(renditions),
        forms=_forms_for(entry, pos_entry),
    )


def _entry_work(entry: Lexeme, request: RenditionRequest) -> _Work | None:
    """Return the rendition work for one entry-level prose section, or ``None``."""
    renditions = (
        entry.encyclopedia
        if request.field is RenditionField.ENCYCLOPEDIA
        else entry.lexical_explanation
    )
    canonical = renditions.canonical()
    if canonical is None:
        return None
    missing = renditions.missing(request.targets())
    if not missing:
        return None
    return _Work(
        field=request.field,
        label=f"{entry.lexeme_id}:{request.field.value}",
        source=strip_markdown(canonical.content),
        renditions=renditions,
        targets=missing,
        existing=_existing_view(renditions),
        forms=[],
    )


def _plan(entry: Lexeme, spec: EnrichmentSpec) -> list[_Work]:
    """Return one work item per ``(owner, field)`` that has missing targets."""
    plan: list[_Work] = []
    for request in spec.renditions:
        if request.field in {RenditionField.GLOSS, RenditionField.EXAMPLES}:
            for pos_entry, sense, sense_id in entry.iter_senses():
                if sense.retired:
                    continue
                work = _sense_work(entry, pos_entry, sense, sense_id, request)
                if work is not None:
                    plan.append(work)
        else:
            work = _entry_work(entry, request)
            if work is not None:
                plan.append(work)
    return plan


async def _add_renditions(
    entry: Lexeme,
    spec: EnrichmentSpec,
    runner: StageRunner,
) -> tuple[float, int, int, list[str]]:
    """Generate every missing rendition, one call per ``(owner, field)``.

    Returns:
        ``(cost, call_count, renditions_added, failed_stage_names)``.
    """
    plan = _plan(entry, spec)
    if not plan:
        _LOG.info("enrich_renditions_noop", headword=entry.headword)
        return 0.0, 0, 0, []

    policy = _readability_policy(runner)
    results = await asyncio.gather(
        *(
            _render(
                entry.headword,
                work,
                runner,
                policy,
                check_initial=_checks_headword_initial(entry, work, policy),
                check_absent=_checks_headword_absent(work, policy),
                check_near_copy=_checks_near_copy(work, policy),
            )
            for work in plan
        ),
        return_exceptions=True,
    )

    _reraise_budget_stop(results)
    cost = 0.0
    calls = 0
    added = 0
    failures: list[str] = []
    for work, result in zip(plan, results, strict=True):
        if isinstance(result, BaseException):
            failures.append(f"{StageName.RENDITIONS.value}:{work.field.value}:{work.label}")
            _LOG.warning(
                "rendition_stage_failed",
                headword=entry.headword,
                owner=work.label,
                field=work.field.value,
                error=str(result),
            )
            continue
        cost += result.cost_usd
        calls += result.calls
        added += _apply_renditions(entry, work, result)
    return cost, calls, added, failures


def _readability_policy(runner: StageRunner) -> ReadabilityConfig:
    """Return the run's readability policy.

    The stage runner already carries the whole :class:`~opengloss_generator.config.AppConfig`;
    reading the one block this workflow needs from it keeps ``enrich_entry``'s signature
    unchanged for every existing caller.

    Args:
        runner: The stage runner the workflow was handed.

    Returns:
        The configured :class:`~opengloss_generator.config.ReadabilityConfig`.
    """
    return runner.config.readability


def _checks_headword_initial(entry: Lexeme, work: _Work, policy: ReadabilityConfig) -> bool:
    """Return whether this work item's renditions are checked for a headword-initial open.

    Three conditions, all of them the caller's to know rather than
    :func:`~opengloss_generator.hygiene.is_headword_initial`'s: the check is enabled, the
    field is ``gloss`` — an example sentence naturally begins with its headword, and a
    prose section is not a definition — and the entry is not a proper noun, whose
    definition legitimately names its own entity (D-30).

    Args:
        entry: The entry being enriched.
        work: The unit of work.
        policy: The run's rendition-check policy.

    Returns:
        Whether to measure and act on the check for this work item.
    """
    return (
        policy.headword_initial_retry
        and work.field is RenditionField.GLOSS
        and entry.kind is not LexemeKind.PROPER_NOUN
    )


def _checks_headword_absent(work: _Work, policy: ReadabilityConfig) -> bool:
    """Return whether this work item's renditions are checked for using the headword.

    Only ``examples`` renditions are checked — a gloss, an encyclopedia passage, and a
    usage note are not required to say the headword itself. Unlike
    :func:`_checks_headword_initial`, there is no proper-noun exemption (D-45): an
    example sentence has to use its headword whatever kind of entry it illustrates.

    Args:
        work: The unit of work.
        policy: The run's rendition-check policy.

    Returns:
        Whether to measure and act on the check for this work item.
    """
    return policy.headword_absent_retry and work.field is RenditionField.EXAMPLES


def _checks_near_copy(work: _Work, policy: ReadabilityConfig) -> bool:
    """Return whether this work item's renditions are checked for copying the canonical.

    Only ``gloss`` renditions are checked — a register axis is not requested for the other
    fields the way it is for the gloss's REGISTERS block, and the comparison text is the
    canonical gloss :func:`_sense_work` already put in ``work.source``. There is no
    proper-noun exemption (D-59), unlike :func:`_checks_headword_initial`: whatever an
    entry names, its formal and slang registers still have to read differently from each
    other. Which *targets* the check actually acts on (registers other than ``plain``) is
    decided per candidate in :func:`_measure`, not here.

    Args:
        work: The unit of work.
        policy: The run's rendition-check policy.

    Returns:
        Whether to measure and act on the check for this work item.
    """
    return policy.near_copy_retry and work.field is RenditionField.GLOSS


async def _render(
    headword: str,
    work: _Work,
    runner: StageRunner,
    policy: ReadabilityConfig,
    *,
    check_initial: bool = False,
    check_absent: bool = False,
    check_near_copy: bool = False,
) -> _Rendered:
    """Produce every rendition for one ``(owner, field)``, retrying what missed.

    Five checks can make a target a miss: its measured grade is outside its reading
    level's band (:func:`_misses_band`), for gloss renditions of a common word its text
    begins by naming the headword (:func:`_checks_headword_initial`), for example
    renditions its text contains no form of the headword at all
    (:func:`_checks_headword_absent`, D-45), at ``grade_1`` and ``grade_5`` too many of
    its words are ones its reader will not know (:func:`_misses_vocabulary`, D-51), or a
    non-``plain``-register gloss rendition is a near-copy of the canonical it was
    rewritten from (:func:`_checks_near_copy`, D-59). They share one retry: a target
    failing any combination of them is re-requested once, with a feedback section per
    failing check, and never twice. That is the whole reason the last four checks live
    here rather than in a pass of their own — the call they need has already been made.

    Args:
        headword: The entry's surface form.
        work: The unit of work: source text, targets, and what already exists.
        runner: The stage runner.
        policy: The policy governing measurement and the single retry.
        check_initial: Whether the headword-initial check applies to this work item; see
            :func:`_checks_headword_initial`.
        check_absent: Whether the headword-absent check applies to this work item; see
            :func:`_checks_headword_absent`.
        check_near_copy: Whether the near-copy check applies to this work item; see
            :func:`_checks_near_copy`.

    Returns:
        The measured renditions and the accounting for the one or two calls made.
    """
    first = await runner.run(
        stage=StageName.RENDITIONS,
        output_type=DraftRenditionSet,
        instructions=prompts.RENDITIONS_INSTRUCTIONS,
        prompt=prompts.build_renditions_prompt(
            headword, work.field.value, work.source, work.existing, work.targets
        ),
        prompt_version=prompts.PROMPT_VERSION,
        writer_key=work.label,
    )
    produced = _measure(
        first.output.renditions,
        set(work.targets),
        headword=headword,
        check_initial=check_initial,
        check_absent=check_absent,
        check_near_copy=check_near_copy,
        source=work.source,
        forms=work.forms,
    )
    rendered = _Rendered(
        produced=produced,
        first=first.provenance,
        cost_usd=first.cost_usd,
        calls=1,
    )

    misses = _readability_misses(produced, policy)
    vocabulary = _vocabulary_misses(produced, policy)
    initial = [key for key, measured in produced.items() if measured.headword_initial]
    absent = [key for key, measured in produced.items() if measured.headword_absent]
    near_copy = [key for key, measured in produced.items() if measured.near_copy]
    failing = [
        key
        for key, measured in produced.items()
        if _misses_band(key, measured, policy)
        or measured.headword_initial
        or measured.headword_absent
        or measured.near_copy
        or _misses_vocabulary(key, measured, policy)
    ]
    if not failing:
        return rendered

    retry = await _retry_renditions(
        headword,
        work,
        runner,
        failing,
        _build_feedback(
            headword,
            misses,
            vocabulary,
            headword_initial=bool(initial),
            headword_absent=bool(absent),
            near_copy=bool(near_copy),
        ),
    )
    if retry is not None:
        rendered.cost_usd += retry.cost_usd
        rendered.calls += 1
        rendered.retry = retry.provenance
        _keep_better(
            produced,
            _measure(
                retry.output.renditions,
                set(failing),
                headword=headword,
                check_initial=check_initial,
                check_absent=check_absent,
                check_near_copy=check_near_copy,
                source=work.source,
                forms=work.forms,
            ),
            check_initial=check_initial,
            check_absent=check_absent,
            check_near_copy=check_near_copy,
            policy=policy,
        )

    # Mark each rendition's *final* miss status (after any retry), not its first-draft
    # one: a fixed retry must not carry the flag forward (docs/STANDARDS-PLAN.md § 3, B3).
    # ``headword_initial``, ``headword_absent`` and ``near_copy`` need no equivalent sweep
    # — each is measured on each candidate's own text, so whichever candidate survives
    # already carries its own verdict.
    for key, measured in produced.items():
        measured.missed_band = _misses_band(key, measured, policy)
        measured.over_vocabulary = _misses_vocabulary(key, measured, policy)

    _log_misses(headword, work, misses, produced, policy, retried=retry is not None)
    _log_initial_misses(headword, work, initial, produced, retried=retry is not None)
    _log_absent_misses(headword, work, absent, produced, retried=retry is not None)
    _log_vocabulary_misses(headword, work, vocabulary, produced, policy, retried=retry is not None)
    _log_near_copy_misses(headword, work, near_copy, produced, retried=retry is not None)
    return rendered


def _build_feedback(
    headword: str,
    misses: Sequence[prompts.RenditionMiss],
    vocabulary: Sequence[tuple[ReadingLevel, list[str]]] = (),
    *,
    headword_initial: bool,
    headword_absent: bool = False,
    near_copy: bool = False,
) -> str:
    """Return the retry note, carrying a section for each check the batch failed.

    Args:
        headword: The entry's surface form.
        misses: The readability misses, one per failing level; may be empty.
        vocabulary: The unfamiliar-word misses, one ``(level, words)`` pair per failing
            level (D-51); may be empty.
        headword_initial: Whether at least one target began with the headword.
        headword_absent: Whether at least one target used no form of the headword at all
            (D-45).
        near_copy: Whether at least one target was a near-copy of the canonical gloss it
            was rewritten from (D-59).

    Returns:
        The combined feedback text. A section is present for every check that failed,
        which is what keeps a target failing more than one check to one retry rather
        than two, three, four or five.
    """
    parts = []
    if misses:
        parts.append(prompts.build_readability_feedback(misses))
    parts.extend(
        prompts.build_vocabulary_feedback(level, words) for level, words in vocabulary if words
    )
    if headword_initial:
        parts.append(prompts.build_headword_initial_feedback(headword))
    if headword_absent:
        parts.append(prompts.build_headword_absent_feedback(headword))
    if near_copy:
        parts.append(prompts.build_near_copy_feedback(headword))
    return "\n\n".join(parts)


async def _retry_renditions(
    headword: str,
    work: _Work,
    runner: StageRunner,
    failing: Sequence[tuple[ReadingLevel, Register]],
    feedback: str,
) -> StageResult[DraftRenditionSet] | None:
    """Re-request the failing targets once, or return ``None`` if the retry failed.

    A retry that fails is not a failure of the enrichment: the first rewrite is still
    there and still stored, merely at the wrong level or with the wrong opening. Only a
    budget stop propagates.

    Args:
        headword: The entry's surface form.
        work: The unit of work being re-requested.
        runner: The stage runner.
        failing: The targets to re-request, and no others.
        feedback: What was wrong with the first attempt, from :func:`_build_feedback`.

    Returns:
        The retry's stage result, or ``None`` if the call failed.

    Raises:
        BudgetExceededError: If the run's ceiling was reached; the caller must stop.
    """
    try:
        return await runner.run(
            stage=StageName.RENDITIONS,
            output_type=DraftRenditionSet,
            instructions=prompts.RENDITIONS_INSTRUCTIONS,
            prompt=prompts.build_renditions_prompt(
                headword,
                work.field.value,
                work.source,
                work.existing,
                failing,
                feedback=feedback,
            ),
            prompt_version=prompts.PROMPT_VERSION,
            writer_key=work.label,
        )
    except StageFailedError as exc:
        _LOG.warning(
            "rendition_readability_retry_failed",
            headword=headword,
            owner=work.label,
            field=work.field.value,
            error=str(exc),
        )
        return None


def _measure(
    drafted: Sequence[DraftRendition],
    wanted: set[tuple[ReadingLevel, Register]],
    *,
    headword: str,
    check_initial: bool = False,
    check_absent: bool = False,
    check_near_copy: bool = False,
    source: str = "",
    forms: Sequence[str] = (),
) -> dict[tuple[ReadingLevel, Register], _Measured]:
    """Return the wanted renditions, markdown-stripped and scored.

    Args:
        drafted: What the model returned.
        wanted: The targets that were actually requested; anything else is discarded.
        headword: Scored as one syllable, and excused from the unfamiliar-word count,
            since a definition cannot avoid its own headword.
        check_initial: Whether to also record whether each text opens with the headword.
        check_absent: Whether to also record whether each text contains no form of the
            headword at all (D-45).
        check_near_copy: Whether to also record whether a non-``plain``-register text is
            a near-copy of ``source`` (D-59). ``plain`` itself is never checked: it is the
            canonical's own register, not a rewrite meant to diverge from it.
        source: The canonical text a register rendition is compared against when
            ``check_near_copy`` is set; ignored otherwise.
        forms: The headword's inflected/derived forms, tried alongside the bare headword
            when ``check_absent`` is set; ignored otherwise.

    Returns:
        One :class:`_Measured` per target the model filled, keyed by target.
    """
    produced: dict[tuple[ReadingLevel, Register], _Measured] = {}
    for draft in drafted:
        key = (draft.reading_level, draft.style)
        if key not in wanted or key in produced:
            continue
        text = strip_markdown(draft.content)
        if not text:
            continue
        produced[key] = _Measured(
            text=text,
            grade=flesch_kincaid_grade(text, ignore=(headword,)),
            headword_initial=check_initial and is_headword_initial(text, headword),
            headword_absent=check_absent and spans.find_span(text, headword, forms) is None,
            near_copy=(
                check_near_copy and key[1] is not Register.PLAIN and is_near_copy(text, source)
            ),
            # Measured at every level, not only the two that are acted on: it costs a
            # pass over a short text and it is the only familiarity signal on disk (D-51).
            hard_share=hard_word_share(text, ignore=(headword,)),
            hard_terms=hard_words(text, ignore=(headword,)),
        )
    return produced


def _misses_band(
    key: tuple[ReadingLevel, Register],
    measured: _Measured,
    policy: ReadabilityConfig,
) -> bool:
    """Return whether one rendition is too hard for the level it was written for."""
    level = key[0]
    if not policy.enabled or level not in policy.retry_levels:
        return False
    return measured.grade > grade_band(level)[1] + policy.tolerance


def _checks_vocabulary(level: ReadingLevel, policy: ReadabilityConfig) -> bool:
    """Return whether the familiar-word check applies to one target's reading level.

    Two conditions: the check is enabled, and the level has a band at all
    (:func:`~opengloss_generator.vocabulary.vocabulary_band` — ``grade_1`` and
    ``grade_5``; a grade_10 or college reader is expected to meet words they do not know,
    and a neutral rendition has no audience to fail).

    Args:
        level: The target's reading level.
        policy: The run's rendition-check policy.

    Returns:
        Whether to act on the check for this level. Note that the *measurement* happens
        at every level regardless; only the acting on it is gated here (D-51).
    """
    return policy.vocabulary_check and vocabulary_band(level) is not None


def _misses_vocabulary(
    key: tuple[ReadingLevel, Register],
    measured: _Measured,
    policy: ReadabilityConfig,
) -> bool:
    """Return whether one rendition uses too many words its reader will not know."""
    band = vocabulary_band(key[0])
    if not policy.vocabulary_check or band is None:
        return False
    return measured.hard_share > band + policy.vocabulary_tolerance


#: How many offending words one level's retry note lists. Naming them is what makes the
#: feedback act (:func:`~opengloss_generator.prompts.build_vocabulary_feedback`); naming
#: forty of them from a 1,600-word encyclopedia passage would spend more input tokens on
#: the note than on the passage, and the first dozen are the ones a rewrite has to reach
#: for anyway.
_MAX_LISTED_HARD_WORDS = 12


def _vocabulary_misses(
    produced: dict[tuple[ReadingLevel, Register], _Measured],
    policy: ReadabilityConfig,
) -> list[tuple[ReadingLevel, list[str]]]:
    """Return one retry note's worth of offending words per failing *level*.

    Several registers can fail at the same level, and the note names the level, so the
    words of every failing register at one level are pooled into a single note — exactly
    the grouping :func:`_readability_misses` uses, and for the same reason.

    Args:
        produced: The measured renditions.
        policy: The run's rendition-check policy.

    Returns:
        ``(level, words)`` per failing level, each word list de-duplicated, in first
        appearance order, and capped at :data:`_MAX_LISTED_HARD_WORDS`.
    """
    pooled: dict[ReadingLevel, dict[str, None]] = {}
    for key, measured in produced.items():
        if not _misses_vocabulary(key, measured, policy):
            continue
        words = pooled.setdefault(key[0], {})
        for word in measured.hard_terms:
            words.setdefault(word, None)
    return [(level, list(words)[:_MAX_LISTED_HARD_WORDS]) for level, words in pooled.items()]


def _readability_misses(
    produced: dict[tuple[ReadingLevel, Register], _Measured],
    policy: ReadabilityConfig,
) -> list[prompts.RenditionMiss]:
    """Return one retry note per failing *level*, carrying its worst measured grade.

    Several registers can fail at the same level; the feedback names the level and the
    limit, so one note per level is enough and repeating it would only cost tokens.
    """
    worst: dict[ReadingLevel, float] = {}
    for key, measured in produced.items():
        if _misses_band(key, measured, policy):
            worst[key[0]] = max(worst.get(key[0], measured.grade), measured.grade)
    return [(level, grade, grade_band(level)[1]) for level, grade in worst.items()]


def _keep_better(
    produced: dict[tuple[ReadingLevel, Register], _Measured],
    retried: dict[tuple[ReadingLevel, Register], _Measured],
    *,
    check_initial: bool = False,
    check_absent: bool = False,
    check_near_copy: bool = False,
    policy: ReadabilityConfig | None = None,
) -> None:
    """Replace a rendition with its retry only when the retry is actually better.

    Args:
        produced: The first call's renditions, mutated in place.
        retried: What the retry produced for the failing targets.
        check_initial: Whether the headword-initial verdict participates in the
            comparison.
        check_absent: Whether the headword-absent verdict participates in the comparison
            (D-45). Never both ``True`` for the same work item: one applies to glosses,
            the other to examples.
        check_near_copy: Whether the near-copy verdict participates in the comparison
            (D-59). Can be ``True`` alongside ``check_initial`` for the same work item —
            both apply to glosses — but never alongside ``check_absent``.
        policy: The run's rendition-check policy, read only to decide whether the
            unfamiliar-word share participates for a given target's level (D-51). ``None``
            leaves it out of the comparison entirely.
    """
    for key, candidate in retried.items():
        current = produced.get(key)
        check_vocabulary = policy is not None and _checks_vocabulary(key[0], policy)
        if current is None or _is_better(
            candidate,
            current,
            check_initial=check_initial,
            check_absent=check_absent,
            check_near_copy=check_near_copy,
            check_vocabulary=check_vocabulary,
            band=vocabulary_band(key[0]),
            tolerance=policy.vocabulary_tolerance if policy is not None else 0.0,
        ):
            candidate.from_retry = True
            produced[key] = candidate


def _is_better(
    candidate: _Measured,
    current: _Measured,
    *,
    check_initial: bool,
    check_absent: bool = False,
    check_near_copy: bool = False,
    check_vocabulary: bool = False,
    band: float | None = None,
    tolerance: float = 0.0,
) -> bool:
    """Return whether a retried rendition should replace the one it was retried for.

    Not opening with the headword, not using the headword at all, and copying the
    canonical gloss all outrank reading easier. None of these checks are commensurable —
    a grade is continuous, the others are right or wrong — so they are ordered rather than
    combined, and the ordering puts the defect that no amount of grade improvement
    compensates for first, and the unfamiliar-word share — which the judge found is what
    actually decides whether a grade_1 passage reads as grade_1 — ahead of the grade
    itself (D-51). Where both candidates share the same hard-defect verdict that defect is
    unfixed either way: for headword-initial, the shorter text wins, since it is the one a
    later ``rendition_hygiene`` rewrite has least to carry over; for headword-absent and
    for near-copy there is no such tiebreak — neither candidate has anywhere the headword
    could be scored against, or there is no continuous score on hand to break the tie by —
    so the comparison falls through to the next check, and ultimately to grade, like any
    other tie.

    Args:
        candidate: The retry's rendition.
        current: The rendition it would replace.
        check_initial: Whether the headword-initial verdict participates at all; when it
            does not, this is the plain "reads easier" comparison it has always been.
        check_absent: Whether the headword-absent verdict participates at all (D-45).
        check_near_copy: Whether the near-copy verdict participates at all (D-59).
        check_vocabulary: Whether the unfamiliar-word share participates at all (D-51);
            it does only at the two levels that have a band.
        band: The level's unfamiliar-word band, used with ``tolerance`` to decide which
            candidate is *inside* it. Ignored when ``check_vocabulary`` is ``False``.
        tolerance: How far above the band a share still counts as inside it.

    Returns:
        Whether to keep the candidate.
    """
    if check_initial and candidate.headword_initial != current.headword_initial:
        return not candidate.headword_initial
    if check_initial and candidate.headword_initial:
        return len(candidate.text) < len(current.text)
    if check_absent and candidate.headword_absent != current.headword_absent:
        return not candidate.headword_absent
    if check_near_copy and candidate.near_copy != current.near_copy:
        return not candidate.near_copy
    if check_vocabulary and band is not None:
        verdict = _vocabulary_tiebreak(candidate, current, band=band, tolerance=tolerance)
        if verdict is not None:
            return verdict
    return candidate.grade < current.grade


def _vocabulary_tiebreak(
    candidate: _Measured,
    current: _Measured,
    *,
    band: float,
    tolerance: float,
) -> bool | None:
    """Return the unfamiliar-word verdict for one candidate/current pair, or ``None``.

    Split out of :func:`_is_better` only to keep that function's own branching readable;
    it carries no rule of its own beyond what D-51 already states there.

    Returns:
        ``True``/``False`` if the unfamiliar-word share decides the comparison,
        ``None`` if both are on the same side of the band and equally over it, in which
        case :func:`_is_better` falls through to the grade comparison.
    """
    limit = band + tolerance
    candidate_over = candidate.hard_share > limit
    current_over = current.hard_share > limit
    if candidate_over != current_over:
        return not candidate_over
    if candidate_over and candidate.hard_share != current.hard_share:
        # Both still over: the one a reader trips on less often is the better of two
        # imperfect answers, exactly as the lower grade is for the band check.
        return candidate.hard_share < current.hard_share
    return None


def _log_misses(
    headword: str,
    work: _Work,
    misses: Sequence[prompts.RenditionMiss],
    produced: dict[tuple[ReadingLevel, Register], _Measured],
    policy: ReadabilityConfig,
    *,
    retried: bool,
) -> None:
    """Emit one ``rendition_readability_miss`` event per level that missed its band."""
    for level, grade, limit in misses:
        fixed = retried and not any(
            _misses_band(key, measured, policy)
            for key, measured in produced.items()
            if key[0] is level
        )
        _LOG.info(
            "rendition_readability_miss",
            headword=headword,
            owner=work.label,
            field=work.field.value,
            level=level.value,
            measured=round(grade, 2),
            limit=limit,
            retried=retried,
            fixed=fixed,
        )


def _log_initial_misses(
    headword: str,
    work: _Work,
    initial: Sequence[tuple[ReadingLevel, Register]],
    produced: dict[tuple[ReadingLevel, Register], _Measured],
    *,
    retried: bool,
) -> None:
    """Emit one ``rendition_headword_initial`` event per target that opened badly.

    One event per *target* rather than per level, as
    :func:`_log_misses` does: the readability feedback names a level, so several
    registers failing at one level are one note, but this defect is per rendition and its
    fix rate is the number iteration 5 is watching.

    Args:
        headword: The entry's surface form.
        work: The unit of work the renditions belong to.
        initial: The targets whose first draft opened with the headword.
        produced: The final renditions, after any retry.
        retried: Whether a retry was actually made.
    """
    for key in initial:
        measured = produced.get(key)
        _LOG.info(
            "rendition_headword_initial",
            headword=headword,
            owner=work.label,
            field=work.field.value,
            level=key[0].value,
            register=key[1].value,
            retried=retried,
            fixed=retried and measured is not None and not measured.headword_initial,
        )


def _log_absent_misses(
    headword: str,
    work: _Work,
    absent: Sequence[tuple[ReadingLevel, Register]],
    produced: dict[tuple[ReadingLevel, Register], _Measured],
    *,
    retried: bool,
) -> None:
    """Emit one ``rendition_headword_absent`` event per example missing the headword.

    One event per target, the same granularity :func:`_log_initial_misses` uses and for
    the same reason: this defect is per rendition, not per level (D-45).

    Args:
        headword: The entry's surface form.
        work: The unit of work the renditions belong to.
        absent: The targets whose first draft used no form of the headword at all.
        produced: The final renditions, after any retry.
        retried: Whether a retry was actually made.
    """
    for key in absent:
        measured = produced.get(key)
        _LOG.info(
            "rendition_headword_absent",
            headword=headword,
            owner=work.label,
            field=work.field.value,
            level=key[0].value,
            register=key[1].value,
            retried=retried,
            fixed=retried and measured is not None and not measured.headword_absent,
        )


def _log_vocabulary_misses(
    headword: str,
    work: _Work,
    vocabulary: Sequence[tuple[ReadingLevel, list[str]]],
    produced: dict[tuple[ReadingLevel, Register], _Measured],
    policy: ReadabilityConfig,
    *,
    retried: bool,
) -> None:
    """Emit one ``rendition_hard_vocabulary`` event per level that used unknown words.

    One event per *level*, the granularity :func:`_log_misses` uses and for the same
    reason: the feedback names a level, so several registers failing at one level are one
    note. The words themselves are logged, capped as the note caps them, because the
    recurring ones are what a prompt change would have to anticipate (D-51).

    Args:
        headword: The entry's surface form.
        work: The unit of work the renditions belong to.
        vocabulary: The failing levels and their pooled offending words.
        produced: The final renditions, after any retry.
        policy: The run's rendition-check policy, for the post-retry verdict.
        retried: Whether a retry was actually made.
    """
    for level, words in vocabulary:
        fixed = retried and not any(
            _misses_vocabulary(key, measured, policy)
            for key, measured in produced.items()
            if key[0] is level
        )
        _LOG.info(
            "rendition_hard_vocabulary",
            headword=headword,
            owner=work.label,
            field=work.field.value,
            level=level.value,
            limit=vocabulary_band(level),
            words=words,
            retried=retried,
            fixed=fixed,
        )


def _log_near_copy_misses(
    headword: str,
    work: _Work,
    near_copy: Sequence[tuple[ReadingLevel, Register]],
    produced: dict[tuple[ReadingLevel, Register], _Measured],
    *,
    retried: bool,
) -> None:
    """Emit one ``rendition_near_copy`` event per register that copied the canonical.

    One event per target, the same granularity :func:`_log_initial_misses` and
    :func:`_log_absent_misses` use and for the same reason: this defect is per rendition,
    not per level (D-59).

    Args:
        headword: The entry's surface form.
        work: The unit of work the renditions belong to.
        near_copy: The targets whose first draft was a near-copy of the canonical gloss.
        produced: The final renditions, after any retry.
        retried: Whether a retry was actually made.
    """
    for key in near_copy:
        measured = produced.get(key)
        _LOG.info(
            "rendition_near_copy",
            headword=headword,
            owner=work.label,
            field=work.field.value,
            level=key[0].value,
            register=key[1].value,
            retried=retried,
            fixed=retried and measured is not None and not measured.near_copy,
        )


def _apply_renditions(entry: Lexeme, work: _Work, rendered: _Rendered) -> int:
    """Merge a measured rendition set onto its owner.

    Provenance is added here rather than in :func:`_render` so that the ids stay in plan
    order regardless of which call finished first, and so that a call whose output was
    entirely superseded by its retry leaves no orphan record behind.

    Args:
        entry: The entry that owns the provenance table.
        work: The work item the renditions belong to.
        rendered: What :func:`_render` produced.

    Returns:
        How many renditions were actually added.
    """
    provenance_ids = rendered.provenance_ids(entry)
    added = 0
    for key, measured in rendered.produced.items():
        # Skip a target a concurrent pass already filled.
        if work.renditions.has(*key):
            continue
        content: Any = measured.text
        if work.field is RenditionField.EXAMPLES:
            content = Example(
                text=measured.text,
                span=spans.find_span(measured.text, entry.headword, work.forms),
            )
        assessment = Assessment(
            readability_grade=round(measured.grade, 2),
            # Written at every level, not only the two that are acted on (D-51).
            hard_word_share=round(measured.hard_share, 3),
        )
        if measured.missed_band:
            # A rendition that still misses its band after any retry is logged as
            # "rendition_readability_miss" (see _log_misses); the flag makes that same
            # fact queryable on the stored entry (docs/STANDARDS-PLAN.md § 3, B3).
            assessment.flag(QAFlag.OG_READABILITY_MISS)
        if measured.headword_initial:
            # Same contract for the second check: the retry did not fix the opening, so
            # the stored rendition says so and the `rendition_hygiene` retrofit pass can
            # find it later without re-deriving the verdict (D-39).
            assessment.flag(QAFlag.OG_HEADWORD_INITIAL)
        if measured.over_vocabulary:
            # Same contract as the other three checks, for the fourth: the retry did not
            # get the unfamiliar words out, so the stored rendition says so and
            # `vocabulary_hygiene`'s retrofit pass can find it later without re-deriving
            # the verdict (D-51).
            assessment.flag(QAFlag.OG_HARD_VOCABULARY)
        if measured.headword_absent:
            # Same contract again, for the third check: the retry did not put the
            # headword into the example, so the stored rendition says so and
            # `example_hygiene`'s retrofit pass can find it later without re-deriving
            # the verdict (D-45).
            assessment.flag(QAFlag.OG_HEADWORD_ABSENT)
        if measured.near_copy:
            # Same contract again, for the fifth check: the retry did not diverge from
            # the canonical gloss's own wording, so the stored rendition says so and
            # `rendition_hygiene`'s retrofit pass can find it later without re-deriving
            # the verdict (D-59).
            assessment.flag(QAFlag.OG_NEAR_COPY)
        work.renditions.add(
            Rendition[Any](
                reading_level=key[0],
                style=key[1],
                content=content,
                provenance_id=provenance_ids[measured.from_retry],
                assessment=assessment,
            )
        )
        added += 1
    return added


# --------------------------------------------------------------------------------------
# Sections
# --------------------------------------------------------------------------------------


async def _add_sections(
    entry: Lexeme,
    spec: EnrichmentSpec,
    runner: StageRunner,
) -> tuple[float, int, list[str], list[str]]:
    """Generate the missing long-form sections.

    Returns:
        ``(cost, call_count, sections_added, failed_stage_names)``.
    """
    primary = next((s.canonical_gloss() for _, s, _ in entry.iter_senses()), None)
    wanted: list[tuple[StageName, type[BaseModel], str, str]] = []

    if spec.with_etymology and (entry.etymology is None or spec.replace):
        wanted.append(
            (
                StageName.ETYMOLOGY,
                DraftEtymology,
                prompts.ETYMOLOGY_INSTRUCTIONS,
                prompts.build_etymology_prompt(entry.headword, primary),
            )
        )
    if spec.with_encyclopedia and (entry.encyclopedia.canonical() is None or spec.replace):
        wanted.append(
            (
                StageName.ENCYCLOPEDIA,
                DraftEncyclopedia,
                prompts.ENCYCLOPEDIA_INSTRUCTIONS,
                prompts.build_encyclopedia_prompt(entry.headword, primary),
            )
        )
    if spec.with_lexical_explanation and (
        entry.lexical_explanation.canonical() is None or spec.replace
    ):
        wanted.append(
            (
                StageName.LEXICAL_EXPLANATION,
                DraftLexicalExplanation,
                prompts.LEXICAL_EXPLANATION_INSTRUCTIONS,
                prompts.build_lexical_explanation_prompt(entry.headword, primary),
            )
        )
    if not wanted:
        return 0.0, 0, [], []

    results = await asyncio.gather(
        *(
            runner.run(
                stage=stage,
                output_type=output_type,
                instructions=instructions,
                prompt=prompt,
                prompt_version=prompts.PROMPT_VERSION,
            )
            for stage, output_type, instructions, prompt in wanted
        ),
        return_exceptions=True,
    )

    _reraise_budget_stop(results)
    cost = 0.0
    calls = 0
    sections: list[str] = []
    failures: list[str] = []
    for (stage, _, _, _), result in zip(wanted, results, strict=True):
        if isinstance(result, BaseException):
            failures.append(stage.value)
            continue
        cost += result.cost_usd
        calls += 1
        provenance_id = entry.add_provenance(result.provenance)
        attach_long_form(entry, result.output, provenance_id)
        sections.append(stage.value)
    return cost, calls, sections, failures


def _reraise_budget_stop(results: Sequence[object]) -> None:
    """Propagate a budget stop out of a ``gather(return_exceptions=True)`` batch (D-7).

    Raises:
        BudgetExceededError: If any result in the batch is one.
    """
    for result in results:
        if isinstance(result, BudgetExceededError):
            raise result
