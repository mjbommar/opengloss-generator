"""Workflow 8 — judge finished entries with a second model, and record what it found.

Everything else in this package is generation or repair: a stage writes content, and a
deterministic check decides whether the content is acceptable. This workflow is the one
place where a *model* is asked for an opinion about content another model produced, which
makes two design constraints non-negotiable.

* **The judge is not the generator.** ``StageName.QA`` is the only stage configured onto
  a different provider (``claude-opus-5``, ``config.py``), because a model marking its
  own homework agrees with itself. The judge call goes through :class:`StageRunner` like
  every other call, so it is priced, rate-limited, budget-guarded and logged; nothing
  here talks to a provider directly.
* **A verdict is data, not an edit.** The judge never rewrites anything. What it says is
  written onto :class:`~opengloss_generator.schema.Assessment` records — a score and
  closed-vocabulary :class:`~opengloss_generator.schema.QAFlag` values on the entry, on
  each judged sense, and on each judged rendition — plus one zero-cost provenance note
  carrying its free text, so an issue the flags cannot express still survives on disk.

Two flags are deliberately *never* written from a verdict:
:attr:`~opengloss_generator.schema.QAFlag.OG_READABILITY_MISS` and
:attr:`~opengloss_generator.schema.QAFlag.OG_HEADWORD_ABSENT`. Both are owned by
deterministic machinery — ``readability_hygiene`` selects its offenders by the first, and
``example_hygiene`` clears the second — so a judge writing them would silently enqueue
model-priced rewrites on the strength of an opinion. The judge's view of the same two
properties lands on ``audience_inappropriate`` and ``off_topic`` instead (D-48).

Like ``resolve`` and every ``retrofit`` pass, each entry is read, judged and written
inside one hold of its own lock — the model call included (D-31) — and the sweep runs
through :func:`~opengloss_generator.runner.run_pool`.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import math
import random
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from opengloss_generator import prompts
from opengloss_generator.contracts import QA_MAX_SENSES, DraftQAVerdict
from opengloss_generator.errors import BudgetExceededError, GenerationError
from opengloss_generator.identity import slugify
from opengloss_generator.log import get_logger
from opengloss_generator.runner import run_pool
from opengloss_generator.schema import (
    Assessment,
    QAFlag,
    ReadingLevel,
    Register,
    StageName,
)

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from opengloss_generator.contracts import DraftRenditionVerdict, DraftSenseVerdict
    from opengloss_generator.schema import Lexeme, Rendition, Sense
    from opengloss_generator.stages import StageRunner
    from opengloss_generator.store import LexemeStore

__all__ = [
    "QAOutcome",
    "judge_entry",
    "run_qa",
    "stratified_sample",
]

_LOG = get_logger(__name__)

#: The six sense dimensions the rubric asks about, in the order the report lists them.
#: A sense's own ``qa_score`` is the share of these that came back true, so adding a
#: dimension to :class:`~opengloss_generator.contracts.DraftSenseVerdict` and forgetting
#: it here would silently inflate every score.
SENSE_DIMENSIONS: tuple[str, ...] = (
    "gloss_accurate",
    "distinct_from_other_senses",
    "examples_natural",
    "examples_fit_sense",
    "relations_valid",
    "domain_fits",
)

#: The three rendition dimensions, same contract.
RENDITION_DIMENSIONS: tuple[str, ...] = (
    "faithful",
    "level_appropriate",
    "register_appropriate",
)

#: Which sense-level defect earns which closed flag (``docs/STANDARDS.md`` § 9b's own
#: reinterpretation of MQM Core for a dictionary pipeline). A sense that fails a
#: dimension is flagged on the *sense's* assessment, never only on the entry's, so a
#: later pass can select the defective sense rather than re-reading the whole entry.
SENSE_DEFECT_FLAGS: dict[str, tuple[QAFlag, ...]] = {
    # MQM Accuracy > Mistranslation: the definition says something untrue.
    "gloss_accurate": (QAFlag.FACTUAL_ERROR,),
    # MQM Accuracy > Omission: a conflated pair is one meaning short of what it claims.
    # `og.duplicate_gloss` rides along because that is exactly the on-disk condition a
    # later dedupe pass looks for, and the judge sees the near-duplicates a string
    # comparison cannot.
    "distinct_from_other_senses": (QAFlag.MISSING_CONTENT, QAFlag.OG_DUPLICATE_GLOSS),
    # MQM Style > Awkward/Unidiomatic style.
    "examples_natural": (QAFlag.AWKWARD_STYLE,),
    # An example illustrating a different sense is content about something else.
    "examples_fit_sense": (QAFlag.OFF_TOPIC,),
    # MQM Terminology > Wrong term: a relation type that does not hold of the pair.
    "relations_valid": (QAFlag.TERMINOLOGY_ERROR,),
    # MQM Terminology > Inconsistent use, per § 9b's "inconsistent with the entry's
    # DomainTag" reading.
    "domain_fits": (QAFlag.TERMINOLOGY_ERROR,),
}

#: Which rendition-level defect earns which closed flag. ``og.readability_miss`` is
#: deliberately absent — see the module docstring.
RENDITION_DEFECT_FLAGS: dict[str, tuple[QAFlag, ...]] = {
    "faithful": (QAFlag.FACTUAL_ERROR,),
    # MQM Audience appropriateness: text unsuitable for the level it is labelled with.
    "level_appropriate": (QAFlag.AUDIENCE_INAPPROPRIATE,),
    # MQM Style > Language register.
    "register_appropriate": (QAFlag.REGISTER_MISMATCH,),
}

#: The gloss renditions sampled per sense: the two ends of the reading-level axis and one
#: point on the register axis. Judging all nine would triple the prompt for a signal the
#: defect rates already carry — a level that fails does so on grade_1 and college first.
_GLOSS_SAMPLE: tuple[tuple[ReadingLevel, Register], ...] = (
    (ReadingLevel.GRADE_1, Register.PLAIN),
    (ReadingLevel.COLLEGE, Register.PLAIN),
    (ReadingLevel.NEUTRAL, Register.TECHNICAL),
)
#: The example rendition sampled per sense: the hardest one to get right.
_EXAMPLE_SAMPLE: tuple[ReadingLevel, Register] = (ReadingLevel.GRADE_1, Register.PLAIN)
#: The encyclopedia renditions sampled per entry.
_ENCYCLOPEDIA_SAMPLE: tuple[tuple[ReadingLevel, Register], ...] = (
    (ReadingLevel.GRADE_1, Register.PLAIN),
    (ReadingLevel.COLLEGE, Register.PLAIN),
)

#: How many of the judge's free-text issues the report keeps. The rest are still on disk,
#: on each entry's own provenance note; this list exists so a diary iteration can read
#: the flavour of a sweep's findings without opening 50 entries.
TOP_ISSUES = 20

#: Sense-count strata for :func:`stratified_sample`.
_SENSE_BUCKETS: tuple[tuple[str, int, int], ...] = (
    ("1", 1, 1),
    ("2-3", 2, 3),
    ("4+", 4, 1_000_000),
)

_TERCILES = 3


# --------------------------------------------------------------------------------------
# Outcome and metrics
# --------------------------------------------------------------------------------------


@dataclass(slots=True)
class QAOutcome:
    """What a QA sweep judged, what it found, and what it cost."""

    entries_judged: int = 0
    entries_skipped: int = 0
    entries_failed: int = 0
    calls: int = 0
    cost_usd: float = 0.0
    scores: list[float] = field(default_factory=list)
    senses_judged: int = 0
    #: ``dimension -> senses that answered it false``.
    sense_defects: dict[str, int] = field(default_factory=dict)
    #: ``target -> renditions of that target judged``, where a target is a field plus a
    #: ``level/register`` key, e.g. ``"gloss grade_1/plain"``.
    renditions_judged: dict[str, int] = field(default_factory=dict)
    #: ``target -> renditions of that target that failed at least one dimension``.
    rendition_defects: dict[str, int] = field(default_factory=dict)
    encyclopedia_judged: int = 0
    encyclopedia_defects: int = 0
    #: ``flag value -> entries carrying it``, counting entry-level flags only.
    flags: dict[str, int] = field(default_factory=dict)
    issues: list[str] = field(default_factory=list)
    entries_changed: list[str] = field(default_factory=list)
    #: Why the sweep stopped early — ``"budget"`` or ``"stopped"`` — or ``None``.
    stopped_reason: str | None = None

    @property
    def mean_score(self) -> float | None:
        """Return the mean entry score, or ``None`` when nothing was judged."""
        if not self.scores:
            return None
        return sum(self.scores) / len(self.scores)

    def buckets(self) -> dict[str, int]:
        """Return the entry-score distribution as four counts.

        Returns:
            Counts under the keys ``"<60"``, ``"60-79"``, ``"80-89"`` and ``"90+"``.
        """
        counts = {"<60": 0, "60-79": 0, "80-89": 0, "90+": 0}
        for score in self.scores:
            if score < 60:  # noqa: PLR2004 - the rubric's own anchors
                counts["<60"] += 1
            elif score < 80:  # noqa: PLR2004
                counts["60-79"] += 1
            elif score < 90:  # noqa: PLR2004
                counts["80-89"] += 1
            else:
                counts["90+"] += 1
        return counts

    def defect_rates(self) -> dict[str, float]:
        """Return the share of judged senses that failed each dimension, 0-1."""
        if not self.senses_judged:
            return dict.fromkeys(SENSE_DIMENSIONS, 0.0)
        return {
            dimension: round(self.sense_defects.get(dimension, 0) / self.senses_judged, 4)
            for dimension in SENSE_DIMENSIONS
        }

    def rendition_defect_rates(self) -> dict[str, float]:
        """Return the share of judged renditions that failed, per rendition target."""
        return {
            target: round(self.rendition_defects.get(target, 0) / judged, 4)
            for target, judged in sorted(self.renditions_judged.items())
            if judged
        }

    def merge(self, other: QAOutcome) -> None:
        """Fold one entry's outcome into this one."""
        self.entries_judged += other.entries_judged
        self.entries_skipped += other.entries_skipped
        self.entries_failed += other.entries_failed
        self.calls += other.calls
        self.cost_usd += other.cost_usd
        self.scores.extend(other.scores)
        self.senses_judged += other.senses_judged
        self.encyclopedia_judged += other.encyclopedia_judged
        self.encyclopedia_defects += other.encyclopedia_defects
        self.entries_changed.extend(other.entries_changed)
        for source, target in (
            (other.sense_defects, self.sense_defects),
            (other.renditions_judged, self.renditions_judged),
            (other.rendition_defects, self.rendition_defects),
            (other.flags, self.flags),
        ):
            for key, value in source.items():
                target[key] = target.get(key, 0) + value
        self.issues.extend(other.issues)

    def as_dict(self) -> dict[str, object]:
        """Return the sweep's metrics as a JSON-serialisable report.

        Returns:
            The report written by ``opengloss qa --report`` and printed in the run
            summary: counts, the mean and the distribution of entry scores, the per
            dimension defect rates, the rendition defect rates by target, the flag
            histogram, the first :data:`TOP_ISSUES` free-text issues, and the cost.
        """
        mean = self.mean_score
        return {
            "entries_judged": self.entries_judged,
            "entries_skipped": self.entries_skipped,
            "entries_failed": self.entries_failed,
            "calls": self.calls,
            "cost_usd": round(self.cost_usd, 6),
            "cost_usd_per_entry": (
                round(self.cost_usd / self.entries_judged, 6) if self.entries_judged else None
            ),
            "mean_score": round(mean, 2) if mean is not None else None,
            "score_buckets": self.buckets(),
            "senses_judged": self.senses_judged,
            "sense_defect_rates": self.defect_rates(),
            "sense_defect_counts": {
                dimension: self.sense_defects.get(dimension, 0) for dimension in SENSE_DIMENSIONS
            },
            "renditions_judged": dict(sorted(self.renditions_judged.items())),
            "rendition_defect_rates": self.rendition_defect_rates(),
            "encyclopedia_judged": self.encyclopedia_judged,
            "encyclopedia_defects": self.encyclopedia_defects,
            "flag_histogram": dict(sorted(self.flags.items())),
            "top_issues": self.issues[:TOP_ISSUES],
            "stopped_reason": self.stopped_reason,
        }


# --------------------------------------------------------------------------------------
# The sample the judge is shown
# --------------------------------------------------------------------------------------


@dataclass(slots=True)
class _SampledSense:
    """One sense shown to the judge, with the label it was shown under."""

    sense: Sense
    label: str


@dataclass(slots=True)
class _SampledRendition:
    """One rendition shown to the judge.

    Attributes:
        target: The metrics bucket — the field plus the ``level/register`` key, with no
            sense identity in it, so rates aggregate across senses.
        label: What the prompt showed, which does carry the sense identity.
        rendition: The stored rendition the verdict is written back onto.
    """

    target: str
    label: str
    rendition: Rendition[Any]


def _rendition_key(reading_level: ReadingLevel, style: Register) -> str:
    """Return the ``level/register`` half of a rendition target key."""
    return f"{reading_level.value}/{style.value}"


def _sample_entry(
    entry: Lexeme,
) -> tuple[list[_SampledSense], list[_SampledRendition]]:
    """Return the senses and renditions the judge is shown for one entry.

    Retired senses are excluded — a tombstone is not content anyone reads — and the
    sense list is capped at :data:`~opengloss_generator.contracts.QA_MAX_SENSES`, the
    contract's own ceiling, so the judge is never shown a sense it has no room to
    answer for.

    Args:
        entry: The entry to sample. Never mutated.

    Returns:
        ``(senses, renditions)``, both in the order the prompt lists them.
    """
    senses = [
        _SampledSense(sense=sense, label=f"{pos_entry.pos.value} {sense.index}")
        for pos_entry, sense, _ in entry.iter_senses()
        if not sense.retired
    ][:QA_MAX_SENSES]

    sampled: list[_SampledRendition] = []
    for item in senses:
        for level, style in _GLOSS_SAMPLE:
            rendition = item.sense.gloss.get(level, style)
            if rendition is not None:
                sampled.append(
                    _SampledRendition(
                        target=f"gloss {_rendition_key(level, style)}",
                        label=f"{item.label} gloss {_rendition_key(level, style)}",
                        rendition=rendition,
                    )
                )
        example = item.sense.examples.get(*_EXAMPLE_SAMPLE)
        if example is not None:
            key = _rendition_key(*_EXAMPLE_SAMPLE)
            sampled.append(
                _SampledRendition(
                    target=f"example {key}",
                    label=f"{item.label} example {key}",
                    rendition=example,
                )
            )
    for level, style in _ENCYCLOPEDIA_SAMPLE:
        rendition = entry.encyclopedia.get(level, style)
        if rendition is not None:
            key = _rendition_key(level, style)
            sampled.append(
                _SampledRendition(
                    target=f"encyclopedia {key}",
                    label=f"encyclopedia {key}",
                    rendition=rendition,
                )
            )
    return senses, sampled


def _prompt_for(
    entry: Lexeme,
    senses: Sequence[_SampledSense],
    sampled: Sequence[_SampledRendition],
) -> str:
    """Render the volatile half of the judge prompt for one entry."""
    sense_views: list[prompts.QASenseView] = [
        (
            item.label,
            item.sense.canonical_gloss(),
            [rendition.content.text for rendition in item.sense.examples],
            [f"{r.type.value}->{r.target.term}" for r in item.sense.relations],
            item.sense.domain.value if item.sense.domain is not None else "(untagged)",
        )
        for item in senses
    ]
    rendition_views: list[prompts.QARenditionView] = [
        (item.label, _text_of(item.rendition)) for item in sampled
    ]
    canonical = entry.encyclopedia.canonical()
    return prompts.build_qa_prompt(
        entry.headword,
        entry.kind.value,
        sense_views,
        rendition_views,
        canonical.content if canonical is not None else None,
    )


def _text_of(rendition: Rendition[Any]) -> str:
    """Return a rendition's text, whether its content is a string or an example."""
    content = rendition.content
    return content if isinstance(content, str) else str(content.text)


# --------------------------------------------------------------------------------------
# Judging one entry
# --------------------------------------------------------------------------------------


def _already_judged(entry: Lexeme) -> bool:
    """Return whether a previous sweep already recorded a verdict on this entry.

    Both fields are required, not either: ``judge_model`` alone could be a hand-set
    value and ``judged_at`` alone says nothing about who judged.
    """
    assessment = entry.assessment
    return (
        assessment is not None
        and assessment.judge_model is not None
        and assessment.judged_at is not None
    )


def _assessment_of(owner: Sense | Rendition[Any] | Lexeme) -> Assessment:
    """Return the owner's assessment, creating an empty one if it has none."""
    if owner.assessment is None:
        owner.assessment = Assessment()
    return owner.assessment


def _apply_sense_verdict(
    item: _SampledSense, verdict: DraftSenseVerdict, outcome: QAOutcome
) -> list[str]:
    """Write one sense verdict onto its sense and count it.

    Args:
        item: The sense the verdict is about.
        verdict: The judge's answer for it.
        outcome: The per-entry outcome, updated with the dimension counts.

    Returns:
        The free-text issues this verdict carried, already prefixed with the sense
        label, for the entry's provenance note.
    """
    assessment = _assessment_of(item.sense)
    passed = 0
    for dimension in SENSE_DIMENSIONS:
        if getattr(verdict, dimension):
            passed += 1
            continue
        outcome.sense_defects[dimension] = outcome.sense_defects.get(dimension, 0) + 1
        for flag in SENSE_DEFECT_FLAGS[dimension]:
            assessment.flag(flag)
    assessment.qa_score = round(100.0 * passed / len(SENSE_DIMENSIONS), 1)
    outcome.senses_judged += 1

    issues: list[str] = []
    if verdict.gloss_issue:
        issues.append(f"{item.label} gloss: {verdict.gloss_issue}")
    if verdict.invalid_relations:
        issues.append(f"{item.label} relations: {', '.join(verdict.invalid_relations)}")
    if verdict.suggested_domain and not verdict.domain_fits:
        issues.append(f"{item.label} domain: suggested {verdict.suggested_domain}")
    return issues


def _apply_rendition_verdict(
    item: _SampledRendition, verdict: DraftRenditionVerdict, outcome: QAOutcome
) -> list[str]:
    """Write one rendition verdict onto its rendition and count it.

    Only failures are written: a rendition the judge passed keeps whatever assessment it
    already had, so a clean verdict never touches an entry's readability grade or its
    deterministic flags.

    Args:
        item: The rendition the verdict is about.
        verdict: The judge's answer for it.
        outcome: The per-entry outcome, updated with the target counts.

    Returns:
        The free-text issue this verdict carried, if any.
    """
    outcome.renditions_judged[item.target] = outcome.renditions_judged.get(item.target, 0) + 1
    failed = [d for d in RENDITION_DIMENSIONS if not getattr(verdict, d)]
    if not failed:
        return []
    outcome.rendition_defects[item.target] = outcome.rendition_defects.get(item.target, 0) + 1
    assessment = _assessment_of(item.rendition)
    for dimension in failed:
        for flag in RENDITION_DEFECT_FLAGS[dimension]:
            assessment.flag(flag)
    detail = verdict.issue or ", ".join(f"not {d}" for d in failed)
    return [f"{item.label}: {detail}"]


def _apply_verdict(
    entry: Lexeme,
    verdict: DraftQAVerdict,
    senses: Sequence[_SampledSense],
    sampled: Sequence[_SampledRendition],
    *,
    judge_model: str,
    outcome: QAOutcome,
) -> list[str]:
    """Write a whole verdict onto an entry, in place.

    Args:
        entry: The entry to annotate.
        verdict: The judge's answer.
        senses: The senses the judge was shown, in prompt order.
        sampled: The renditions the judge was shown, in prompt order.
        judge_model: The model id that produced the verdict.
        outcome: The per-entry outcome, updated with every count.

    Returns:
        The free-text issues to preserve on the entry's provenance note.
    """
    issues: list[str] = []
    seen_senses: set[int] = set()
    for drafted in verdict.sense_verdicts:
        position = drafted.sense_ref - 1
        if not 0 <= position < len(senses) or position in seen_senses:
            continue
        seen_senses.add(position)
        issues.extend(_apply_sense_verdict(senses[position], drafted, outcome))

    seen_renditions: set[int] = set()
    for drafted in verdict.rendition_verdicts:
        position = drafted.rendition_ref - 1
        if not 0 <= position < len(sampled) or position in seen_renditions:
            continue
        seen_renditions.add(position)
        issues.extend(_apply_rendition_verdict(sampled[position], drafted, outcome))

    assessment = _assessment_of(entry)
    assessment.qa_score = float(verdict.entry_score)
    assessment.judge_model = judge_model
    assessment.judged_at = dt.datetime.now(tz=dt.UTC)
    for flag in verdict.flags:
        assessment.flag(flag)
        outcome.flags[flag.value] = outcome.flags.get(flag.value, 0) + 1
    if entry.encyclopedia:
        outcome.encyclopedia_judged += 1
        if not verdict.encyclopedia_accurate:
            outcome.encyclopedia_defects += 1
            assessment.flag(QAFlag.FACTUAL_ERROR)
            issues.append(f"encyclopedia: {verdict.encyclopedia_issue or 'inaccurate'}")
    if verdict.notes.strip():
        issues.append(f"notes: {verdict.notes.strip()}")
    outcome.scores.append(float(verdict.entry_score))
    outcome.entries_judged += 1
    return issues


#: How much of the judge's free text one entry's provenance note keeps. Long enough for
#: every issue a full eight-sense verdict can carry, short enough that ``--force``
#: re-runs do not grow an entry without bound.
_NOTE_CHARS = 2000


async def judge_entry(entry: Lexeme, runner: StageRunner) -> QAOutcome:
    """Judge one entry with the QA model and annotate it in place.

    Args:
        entry: The entry to judge. Mutated in place when a verdict comes back.
        runner: The stage runner.

    Returns:
        A :class:`QAOutcome` covering this entry alone. ``entries_failed`` is 1 and
        nothing is written when the stage could not produce a verdict.

    Raises:
        BudgetExceededError: A budget stop is a run-level condition and propagates.
    """
    outcome = QAOutcome()
    senses, sampled = _sample_entry(entry)
    if not senses:
        _LOG.info("qa_noop", headword=entry.headword)
        outcome.entries_skipped = 1
        return outcome

    try:
        result = await runner.run(
            stage=StageName.QA,
            output_type=DraftQAVerdict,
            instructions=prompts.QA_INSTRUCTIONS,
            prompt=_prompt_for(entry, senses, sampled),
            prompt_version=prompts.PROMPT_VERSION,
        )
    except BudgetExceededError:
        raise
    except GenerationError as exc:
        # A failed judgement leaves the entry exactly as it was; QA never gates content.
        _LOG.warning("qa_stage_failed", headword=entry.headword, error=str(exc))
        outcome.entries_failed = 1
        return outcome

    outcome.calls = 1
    outcome.cost_usd = result.cost_usd
    issues = _apply_verdict(
        entry,
        result.output,
        senses,
        sampled,
        judge_model=result.provenance.model,
        outcome=outcome,
    )
    entry.add_provenance(result.provenance)
    if issues:
        # A zero-cost copy, exactly as `retrofit._note_provenance` does it: the call's
        # real cost is recorded once, on the record above, so preserving the judge's prose
        # here does not inflate a naive sum over the entry's provenance table.
        entry.add_provenance(
            result.provenance.model_copy(
                update={
                    "note": " | ".join(issues)[:_NOTE_CHARS],
                    "cost_usd": 0.0,
                    "input_tokens": 0,
                    "cached_input_tokens": 0,
                    "output_tokens": 0,
                    "attempts": 0,
                }
            )
        )
    outcome.issues = issues
    outcome.entries_changed.append(entry.lexeme_id)
    return outcome


async def run_qa(
    store: LexemeStore,
    runner: StageRunner,
    *,
    lexeme_ids: Iterable[str],
    workers: int | None = None,
    stop_event: asyncio.Event | None = None,
    force: bool = False,
) -> QAOutcome:
    """Judge every named entry and record the verdicts, concurrently.

    Args:
        store: The store to judge. Each entry is read, judged and written inside one
            hold of its own lock, the model call included.
        runner: The stage runner.
        lexeme_ids: The entries to judge, usually a :func:`stratified_sample`.
        workers: Pool size; defaults to the runner's configured ``concurrency.workers``.
        stop_event: Shared stop event. A budget stop sets it; a caller may also set it
            from outside to end the sweep after the entries in hand.
        force: Re-judge entries that already carry a verdict. Without it an entry whose
            assessment names a ``judge_model`` and a ``judged_at`` is skipped, so a
            re-run over a judged sample costs nothing.

    Returns:
        The merged :class:`QAOutcome`, with ``entries_changed`` sorted so the result
        does not depend on completion order. A budget stop is reported on
        ``stopped_reason`` rather than raised.
    """
    ids = list(lexeme_ids)
    pool_size = runner.config.concurrency.workers if workers is None else workers

    total = QAOutcome()
    merge_lock = asyncio.Lock()

    async def handle(lexeme_id: str) -> None:
        async with store.locked(lexeme_id):
            entry = store.read(lexeme_id)
            if entry is None:
                async with merge_lock:
                    total.entries_skipped += 1
                return
            if not force and _already_judged(entry):
                async with merge_lock:
                    total.entries_skipped += 1
                return
            outcome = await judge_entry(entry, runner)
            if outcome.entries_judged:
                store.write(entry)
        async with merge_lock:
            total.merge(outcome)

    async def guarded(lexeme_id: str) -> None:
        try:
            await handle(lexeme_id)
        except BudgetExceededError:
            async with merge_lock:
                total.stopped_reason = total.stopped_reason or "budget"
            raise

    await run_pool(ids, guarded, workers=pool_size, stop_event=stop_event)
    if stop_event is not None and stop_event.is_set():
        total.stopped_reason = total.stopped_reason or "stopped"
    total.entries_changed.sort()
    _LOG.info(
        "qa_complete",
        entries=len(ids),
        workers=pool_size,
        judged=total.entries_judged,
        skipped=total.entries_skipped,
        failed=total.entries_failed,
        mean_score=round(total.mean_score, 2) if total.mean_score is not None else None,
        cost_usd=round(total.cost_usd, 6),
        stopped_reason=total.stopped_reason,
    )
    return total


# --------------------------------------------------------------------------------------
# Sampling
# --------------------------------------------------------------------------------------


def _sense_bucket(sense_count: int) -> str:
    """Return the sense-count stratum a sense count falls in."""
    for name, low, high in _SENSE_BUCKETS:
        if low <= sense_count <= high:
            return name
    return _SENSE_BUCKETS[-1][0]


def _allocate(sizes: dict[str, int], total: int) -> dict[str, int]:
    """Split ``total`` across strata in proportion to their sizes, by largest remainder.

    Every non-empty stratum gets at least one slot before proportionality is applied, so
    a sweep of 30 entries still covers the rare strata a purely proportional split would
    round away — the whole point of stratifying.

    Args:
        sizes: ``stratum -> population``, non-empty strata only.
        total: How many entries to allocate in all.

    Returns:
        ``stratum -> slots``, summing to ``min(total, sum(sizes.values()))``.
    """
    keys = sorted(sizes)
    population = sum(sizes.values())
    budget = min(total, population)
    if not keys or budget <= 0:
        return {}
    allocation = {key: 1 if budget >= len(keys) else 0 for key in keys}
    remaining = budget - sum(allocation.values())
    if remaining > 0:
        shares = {key: remaining * sizes[key] / population for key in keys}
        for key in keys:
            allocation[key] += min(int(shares[key]), sizes[key] - allocation[key])
        # Largest remainder, ties broken by stratum name so the split is deterministic.
        order = sorted(keys, key=lambda k: (-(shares[k] - int(shares[k])), k))
        while sum(allocation.values()) < budget:
            grew = False
            for key in order:
                if sum(allocation.values()) >= budget:
                    break
                if allocation[key] < sizes[key]:
                    allocation[key] += 1
                    grew = True
            if not grew:  # every stratum is exhausted; the population is the answer
                break
    return {key: value for key, value in allocation.items() if value}


def stratified_sample(
    store: LexemeStore,
    core_words: Sequence[str],
    n: int,
    seed: int,
) -> list[str]:
    """Return a deterministic, stratified sample of ``n`` lexeme ids from the core list.

    Strata are the product of three axes, each cheap to read off an entry that is
    already on disk: the lexeme's ``kind``, its sense count bucketed at 1 / 2-3 / 4+, and
    its frequency tercile, taken from its *rank* in ``core_words`` rather than from a raw
    count — the list is already in rank order, so the tercile costs nothing and does not
    depend on which frequency corpus the entry was stamped from.

    Determinism is the property that makes a QA iteration comparable with the next one:
    the same ``(core_words, n, seed)`` always yields the same ids, whatever order the
    filesystem hands entries back in, because the population of each stratum is built in
    rank order and sampled with a seeded generator.

    Args:
        store: The store to draw from. Words with no entry are skipped.
        core_words: The core headword list, in rank order (rank 1 first).
        n: How many ids to return. A larger ``n`` than the population returns the whole
            population.
        seed: Seed for the per-stratum draw.

    Returns:
        The sampled lexeme ids, sorted, so the result is stable across runs.
    """
    population: dict[str, list[str]] = {}
    words = list(core_words)
    tercile_size = max(1, math.ceil(len(words) / _TERCILES))
    for rank, word in enumerate(words):
        lexeme_id = slugify(word)
        entry = store.read(lexeme_id)
        if entry is None:
            continue
        tercile = min(rank // tercile_size, _TERCILES - 1) + 1
        key = f"{entry.kind.value}|{_sense_bucket(entry.sense_count())}|t{tercile}"
        population.setdefault(key, []).append(lexeme_id)

    allocation = _allocate({key: len(ids) for key, ids in population.items()}, n)
    chosen: list[str] = []
    for key in sorted(allocation):
        rng = random.Random(f"{seed}:{key}")  # noqa: S311 - sampling, not crypto
        chosen.extend(rng.sample(population[key], allocation[key]))
    chosen.sort()
    _LOG.info(
        "qa_sample_built",
        requested=n,
        selected=len(chosen),
        strata=len(allocation),
        population=sum(len(ids) for ids in population.values()),
        seed=seed,
    )
    return chosen
