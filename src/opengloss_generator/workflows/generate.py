"""Workflow 1 — generate a complete entry from a specification.

Stage graph::

    overview ──► senses[pos] x N  (concurrent)
             │      └─► find_span (free) ──► spans[pos] (LLM, residue only)
             └─► etymology, encyclopedia, lexical_explanation (concurrent, optional)

The per-POS sense calls and the three long-form calls all run concurrently within one
entry, bounded by the same rate limiter as everything else. A stage that fails does not
abort the entry: the entry is written with ``status=partial`` and the failure is recorded,
because a partial entry with honest provenance is more useful than nothing and can be
completed later by ``enrich``.

Two v3 shapes matter here. Sense domains come from the senses stage, whose ``domain``
field is the ``DomainTag`` enum — structured output constrains it, so no separate tagging
call is needed for a freshly generated entry. Example spans come from
:func:`opengloss_generator.spans.find_span`, which is free; only the examples it cannot
place reach the ``spans`` model stage, in batches of 40.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from pydantic import BaseModel

from opengloss_generator import prompts, spans
from opengloss_generator.contracts import (
    SPAN_BATCH_SIZE,
    DraftEncyclopedia,
    DraftEtymology,
    DraftLexicalExplanation,
    DraftOverview,
    DraftSense,
    DraftSenseSet,
    DraftSpanBatch,
)
from opengloss_generator.errors import BudgetExceededError, GenerationError, StageFailedError
from opengloss_generator.identity import slugify
from opengloss_generator.log import get_logger
from opengloss_generator.schema import (
    EntityType,
    EntryStatus,
    Etymology,
    EtymologySegment,
    Example,
    Lexeme,
    LexemeKind,
    Morphology,
    PartOfSpeech,
    POSEntry,
    ProperNounInfo,
    Relation,
    RelationTarget,
    RelationType,
    Renditions,
    Sense,
    StageName,
    canonical_rendition,
)

if TYPE_CHECKING:
    from opengloss_generator.contracts import DraftProperNoun
    from opengloss_generator.stages import StageRunner

__all__ = ["EntrySpec", "GenerationOutcome", "entry_id_for", "generate_entry"]

_LOG = get_logger(__name__)


@dataclass(slots=True)
class EntrySpec:
    """What to generate for one headword.

    Every field except ``headword`` has a usable default, so the caller may specify as
    little as the word itself (FR-1.1).
    """

    headword: str
    language: str = "en"
    parts_of_speech: list[PartOfSpeech] | None = None
    max_senses_per_pos: int = 4
    domain: str | None = None
    with_etymology: bool = True
    with_encyclopedia: bool = True
    with_lexical_explanation: bool = True
    with_span_fallback: bool = True
    discovered_from: str | None = None


@dataclass(slots=True)
class GenerationOutcome:
    """An entry and the accounting for producing it."""

    entry: Lexeme
    cost_usd: float
    calls: int
    failed_stages: list[str] = field(default_factory=list)
    spans_found_deterministically: int = 0
    spans_found_by_model: int = 0
    spans_unresolved: int = 0

    @property
    def complete(self) -> bool:
        """Return whether every requested stage succeeded."""
        return not self.failed_stages


async def generate_entry(spec: EntrySpec, runner: StageRunner) -> GenerationOutcome:
    """Generate a complete entry for a headword.

    Args:
        spec: What to generate.
        runner: The stage runner that performs and prices the model calls.

    Returns:
        A :class:`GenerationOutcome`. The entry is marked ``partial`` if any stage failed.

    Raises:
        StageFailedError: Only if the overview stage fails, since nothing downstream can
            proceed without a part-of-speech plan.
        BudgetExceededError: If the run's ceiling is reached mid-entry.
    """
    overview = await runner.run(
        stage=StageName.OVERVIEW,
        output_type=DraftOverview,
        instructions=prompts.OVERVIEW_INSTRUCTIONS,
        prompt=prompts.build_overview_prompt(spec.headword, spec.language),
        prompt_version=prompts.PROMPT_VERSION,
    )
    plan = overview.output
    entry = Lexeme.empty(
        spec.headword,
        kind=plan.kind,
        proper_noun=_proper_noun_block(plan.kind, plan.proper_noun),
        language=spec.language,
        is_stopword=plan.is_stopword,
        discovered_from=spec.discovered_from,
    )
    entry.add_provenance(overview.provenance)
    domain_hint = spec.domain or plan.domain

    failures: list[str] = []
    cost = overview.cost_usd
    calls = 1

    plans = _selected_plans(plan, spec)
    sense_results = await asyncio.gather(
        *(
            runner.run(
                stage=StageName.SENSES,
                output_type=DraftSenseSet,
                instructions=prompts.SENSES_INSTRUCTIONS,
                prompt=prompts.build_senses_prompt(
                    spec.headword,
                    pos_plan.pos.value,
                    min(pos_plan.sense_count, spec.max_senses_per_pos),
                    domain_hint=domain_hint,
                ),
                prompt_version=prompts.PROMPT_VERSION,
            )
            for pos_plan in plans
        ),
        return_exceptions=True,
    )

    _reraise_budget_stop(sense_results)
    for pos_plan, result in zip(plans, sense_results, strict=True):
        if isinstance(result, BaseException):
            failures.append(f"{StageName.SENSES.value}:{pos_plan.pos.value}")
            _LOG.warning(
                "sense_stage_failed",
                headword=spec.headword,
                pos=pos_plan.pos.value,
                error=str(result),
            )
            continue
        cost += result.cost_usd
        calls += 1
        provenance_id = entry.add_provenance(result.provenance)
        entry.pos_entries.append(
            _pos_entry_from(
                result.output,
                max_senses=spec.max_senses_per_pos,
                provenance_id=provenance_id,
                domain_hint=domain_hint,
            )
        )

    if not entry.pos_entries:
        raise StageFailedError(
            StageName.SENSES.value, 1, f"no part of speech produced senses for {spec.headword!r}"
        )

    outcome = GenerationOutcome(entry=entry, cost_usd=cost, calls=calls, failed_stages=failures)
    await _place_spans(entry, spec, runner, outcome)

    extra_cost, extra_calls, extra_failures = await _add_long_form(entry, spec, runner)
    outcome.cost_usd += extra_cost
    outcome.calls += extra_calls
    outcome.failed_stages.extend(extra_failures)

    entry.status = EntryStatus.COMPLETE if not outcome.failed_stages else EntryStatus.PARTIAL
    return outcome


# --------------------------------------------------------------------------------------
# Overview
# --------------------------------------------------------------------------------------


def _proper_noun_block(kind: LexemeKind, draft: DraftProperNoun | None) -> ProperNounInfo | None:
    """Reconcile the overview's kind and entity block into what the schema requires.

    ``Lexeme`` validates that the block is present exactly when the kind is
    ``proper_noun``. A model that names a kind and forgets the block, or fills the block
    for a common noun, would otherwise fail the whole entry over a field we can repair:
    a missing block becomes ``entity_type=other`` and a stray one is dropped.
    """
    if kind is not LexemeKind.PROPER_NOUN:
        return None
    if draft is None:
        return ProperNounInfo(entity_type=EntityType.OTHER)
    return ProperNounInfo(entity_type=draft.entity_type, wikidata_qid=draft.wikidata_qid)


def _selected_plans(overview: DraftOverview, spec: EntrySpec) -> list:
    """Return the part-of-speech plans to generate, honouring an explicit request."""
    if spec.parts_of_speech is None:
        return list(overview.pos_plans)
    wanted = set(spec.parts_of_speech)
    chosen = [pos_plan for pos_plan in overview.pos_plans if pos_plan.pos in wanted]
    return chosen or list(overview.pos_plans)


# --------------------------------------------------------------------------------------
# Senses
# --------------------------------------------------------------------------------------


def _pos_entry_from(
    draft: DraftSenseSet,
    *,
    max_senses: int,
    provenance_id: str,
    domain_hint: str | None,
) -> POSEntry:
    """Convert a sense-set draft into a stored part-of-speech entry.

    Sense indices are assigned here, contiguously from zero, which is what makes sense
    identifiers derivable (``docs/DESIGN.md`` § 2.1).
    """
    senses = [
        _sense_from(draft_sense, index, provenance_id=provenance_id, domain_hint=domain_hint)
        for index, draft_sense in enumerate(draft.senses[:max_senses])
    ]
    return POSEntry(
        pos=draft.pos,
        senses=senses,
        collocations=list(draft.collocations),
        morphology=Morphology(
            plural=draft.plural,
            past_tense=draft.past_tense,
            past_participle=draft.past_participle,
            present_participle=draft.present_participle,
            third_person_singular=draft.third_person_singular,
            comparative=draft.comparative,
            superlative=draft.superlative,
            derivations=list(draft.derivations),
        ),
    )


def _sense_from(
    draft: DraftSense,
    index: int,
    *,
    provenance_id: str,
    domain_hint: str | None,
) -> Sense:
    """Build one stored sense from its draft.

    The gloss and every example become canonical ``(neutral, plain)`` renditions carrying
    the id of the sense call that produced them; spans are filled later.
    """
    examples: list[Example] = []
    seen: set[str] = set()
    for text in draft.examples:
        stripped = text.strip()
        # Renditions key examples on (level, register, text), so a repeated sentence
        # would fail validation for the whole entry. Drop it instead.
        if stripped and stripped not in seen:
            seen.add(stripped)
            examples.append(Example(text=stripped))
    return Sense(
        index=index,
        gloss=Renditions[str](root=[canonical_rendition(draft.gloss, provenance_id=provenance_id)]),
        examples=Renditions[Example](
            root=[canonical_rendition(example, provenance_id=provenance_id) for example in examples]
        ),
        relations=_relations_from(draft, provenance_id),
        domain=draft.domain,
        secondary_domains=list(draft.secondary_domains),
        domain_hint=domain_hint,
    )


def _relations_from(draft: DraftSense, provenance_id: str) -> list[Relation]:
    """Build the sense's typed relations, confusables included.

    Confusables arrive on their own field precisely so the "how they differ" text is
    mandatory; they are folded in here as ``confusable_with`` relations whose ``note``
    carries that text. A ``confusable_with`` that appears in the plain relations list has
    no note and would fail validation, so it is dropped rather than allowed to fail the
    entry.
    """
    relations: list[Relation] = []
    seen: set[tuple[RelationType, str]] = set()

    def add(relation_type: RelationType, term: str, note: str | None) -> None:
        try:
            target = RelationTarget(term=term)
        except ValueError:
            _LOG.warning("relation_target_unusable", term=term, type=relation_type.value)
            return
        key = (relation_type, target.lexeme_id)
        if key in seen:
            return
        seen.add(key)
        relations.append(
            Relation(type=relation_type, target=target, note=note, provenance_id=provenance_id)
        )

    for drafted in draft.relations:
        if drafted.type is RelationType.CONFUSABLE_WITH:
            _LOG.warning("confusable_in_relations_list", term=drafted.term)
            continue
        add(drafted.type, drafted.term, None)
    for confusable in draft.confusables:
        add(RelationType.CONFUSABLE_WITH, confusable.term, confusable.how_they_differ)
    return relations


# --------------------------------------------------------------------------------------
# Spans
# --------------------------------------------------------------------------------------


def _forms_for(entry: Lexeme, pos_entry: POSEntry) -> list[str]:
    """Return the surface forms to try when locating the headword in an example.

    The model's own morphology block is preferred; the rule-based generator in
    ``spans.py`` is only a fallback for a part of speech that reported none, since it
    happily invents "runned".
    """
    forms = [*pos_entry.morphology.inflected_forms(), *pos_entry.morphology.derivations]
    if not forms:
        forms = list(spans.generate_forms(entry.headword))
    return forms


async def _place_spans(
    entry: Lexeme,
    spec: EntrySpec,
    runner: StageRunner,
    outcome: GenerationOutcome,
) -> None:
    """Fill every example's span, deterministically first and by model only if needed.

    Mutates ``outcome`` with the cost, call count, and the three span counters, so a run
    summary can show what fraction the free finder placed.
    """
    residue: list[tuple[POSEntry, Example]] = []
    for pos_entry in entry.pos_entries:
        forms = _forms_for(entry, pos_entry)
        for sense in pos_entry.senses:
            for rendition in sense.examples:
                example = rendition.content
                if example.span is not None:
                    continue
                span = spans.find_span(example.text, entry.headword, forms)
                if span is None:
                    residue.append((pos_entry, example))
                else:
                    example.span = span
                    outcome.spans_found_deterministically += 1

    if not residue:
        return
    if not spec.with_span_fallback:
        outcome.spans_unresolved = len(residue)
        return

    by_pos: dict[PartOfSpeech, list[Example]] = {}
    for pos_entry, example in residue:
        by_pos.setdefault(pos_entry.pos, []).append(example)

    for pos_entry in entry.pos_entries:
        pending = by_pos.get(pos_entry.pos, [])
        forms = _forms_for(entry, pos_entry)
        for start in range(0, len(pending), SPAN_BATCH_SIZE):
            batch = pending[start : start + SPAN_BATCH_SIZE]
            outcome.spans_unresolved += await _span_batch(entry, forms, batch, runner, outcome)


async def _span_batch(
    entry: Lexeme,
    forms: Sequence[str],
    batch: Sequence[Example],
    runner: StageRunner,
    outcome: GenerationOutcome,
) -> int:
    """Ask the model to place one batch of examples.

    Returns:
        How many of the batch are still unplaced afterwards.

    Raises:
        BudgetExceededError: A budget stop is a run-level condition, never a per-entry
            one, so it propagates rather than degrading the entry to ``partial``.
    """
    try:
        result = await runner.run(
            stage=StageName.SPANS,
            output_type=DraftSpanBatch,
            instructions=prompts.SPANS_INSTRUCTIONS,
            prompt=prompts.build_spans_prompt(
                entry.headword, list(forms), [example.text for example in batch]
            ),
            prompt_version=prompts.PROMPT_VERSION,
        )
    except BudgetExceededError:
        raise
    except GenerationError as exc:
        _LOG.warning("span_fallback_failed", headword=entry.headword, error=str(exc))
        return len(batch)

    outcome.cost_usd += result.cost_usd
    outcome.calls += 1
    # The span call is recorded on the entry, but nothing points at it: a span is a
    # property of an example whose rendition already names the call that wrote the text.
    entry.add_provenance(result.provenance)
    placed = 0
    for drafted in result.output.spans:
        position = drafted.example_ref - 1
        if not 0 <= position < len(batch):
            continue
        example = batch[position]
        if example.span is not None:
            continue
        if not 0 <= drafted.start < drafted.end <= len(example.text):
            _LOG.warning("span_out_of_bounds", text=example.text, span=(drafted.start, drafted.end))
            continue
        example.span = (drafted.start, drafted.end)
        placed += 1
    outcome.spans_found_by_model += placed
    return len(batch) - placed


# --------------------------------------------------------------------------------------
# Long-form sections
# --------------------------------------------------------------------------------------


async def _add_long_form(
    entry: Lexeme,
    spec: EntrySpec,
    runner: StageRunner,
) -> tuple[float, int, list[str]]:
    """Run the optional long-form stages concurrently and attach their output.

    Returns:
        ``(cost, call_count, failed_stage_names)``.
    """
    primary = next(
        (sense.canonical_gloss() for _, sense, _ in entry.iter_senses()),
        None,
    )
    wanted: list[tuple[StageName, type[BaseModel], str, str]] = []
    if spec.with_etymology and entry.etymology is None:
        wanted.append(
            (
                StageName.ETYMOLOGY,
                DraftEtymology,
                prompts.ETYMOLOGY_INSTRUCTIONS,
                prompts.build_etymology_prompt(entry.headword, primary),
            )
        )
    if spec.with_encyclopedia and entry.encyclopedia.canonical() is None:
        wanted.append(
            (
                StageName.ENCYCLOPEDIA,
                DraftEncyclopedia,
                prompts.ENCYCLOPEDIA_INSTRUCTIONS,
                prompts.build_encyclopedia_prompt(entry.headword, primary),
            )
        )
    if spec.with_lexical_explanation and entry.lexical_explanation.canonical() is None:
        wanted.append(
            (
                StageName.LEXICAL_EXPLANATION,
                DraftLexicalExplanation,
                prompts.LEXICAL_EXPLANATION_INSTRUCTIONS,
                prompts.build_lexical_explanation_prompt(entry.headword, primary),
            )
        )
    if not wanted:
        return 0.0, 0, []

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
    failures: list[str] = []
    for (stage, _, _, _), result in zip(wanted, results, strict=True):
        if isinstance(result, BaseException):
            failures.append(stage.value)
            _LOG.warning(
                "long_form_stage_failed",
                headword=entry.headword,
                stage=stage.value,
                error=str(result),
            )
            continue
        cost += result.cost_usd
        calls += 1
        provenance_id = entry.add_provenance(result.provenance)
        attach_long_form(entry, result.output, provenance_id)
    return cost, calls, failures


def attach_long_form(entry: Lexeme, output: object, provenance_id: str) -> None:
    """Write one long-form stage's output onto the entry as a canonical rendition.

    Shared with ``enrich``: the two workflows must agree on what "the encyclopedia
    section" means, and it is the ``(neutral, plain)`` rendition of
    :attr:`Lexeme.encyclopedia`, not a bare string.

    Args:
        entry: The entry to write onto.
        output: A draft from the etymology, encyclopedia, or explanation stage.
        provenance_id: Key into the entry's provenance table for the producing call.
    """
    if isinstance(output, DraftEtymology):
        entry.etymology = Etymology(
            summary=output.summary,
            segments=[
                EtymologySegment(
                    language=step.language, form=step.form, meaning=step.meaning, era=step.era
                )
                for step in output.segments
            ],
            cognates=list(output.cognates),
        )
    elif isinstance(output, DraftEncyclopedia):
        _replace_canonical(entry.encyclopedia, output.text, provenance_id)
    elif isinstance(output, DraftLexicalExplanation):
        _replace_canonical(entry.lexical_explanation, output.text, provenance_id)
    else:  # pragma: no cover - defensive; the stage list above is closed
        _LOG.warning("unexpected_long_form_output", kind=type(output).__name__)


def _replace_canonical(renditions: Renditions[str], text: str, provenance_id: str) -> None:
    """Set the canonical rendition of a prose section, replacing any existing one."""
    existing = renditions.canonical()
    if existing is not None:
        existing.content = text
        existing.provenance_id = provenance_id
        return
    renditions.add(canonical_rendition(text, provenance_id=provenance_id))


def _reraise_budget_stop(results: Sequence[object]) -> None:
    """Propagate a budget stop out of a ``gather(return_exceptions=True)`` batch.

    Every other stage failure degrades the entry to ``partial``. A budget stop is not a
    property of the entry; it is a run-level condition, and swallowing it here would let
    a walk keep dispatching work that the guard is refusing (D-7).

    Raises:
        BudgetExceededError: If any result in the batch is one.
    """
    for result in results:
        if isinstance(result, BudgetExceededError):
            raise result


def entry_id_for(headword: str) -> str:
    """Return the store id a headword will occupy."""
    return slugify(headword)
