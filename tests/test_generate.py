"""Example 1: generate a new entry from a specification."""

from __future__ import annotations

import pytest

from opengloss_generator.errors import BudgetExceededError, StageFailedError
from opengloss_generator.prompts import PROMPT_VERSION
from opengloss_generator.runner import RunSession
from opengloss_generator.schema import (
    EntityType,
    EntryStatus,
    LexemeKind,
    PartOfSpeech,
    RelationType,
)
from opengloss_generator.taxonomy import DomainTag
from opengloss_generator.workflows.generate import EntrySpec, generate_entry
from tests.conftest import SCRIPTED_SENSE_DOMAIN


async def test_generates_a_complete_valid_entry(session):
    result = await generate_entry(EntrySpec(headword="abseil"), session.stages)
    entry = result.entry

    assert entry.lexeme_id == "abseil"
    assert entry.status is EntryStatus.COMPLETE
    assert result.complete
    assert {e.pos for e in entry.pos_entries} == {PartOfSpeech.NOUN, PartOfSpeech.VERB}
    assert entry.sense_count() == 3  # 2 noun senses + 1 verb sense, per the scripted plan
    assert entry.etymology is not None
    assert entry.encyclopedia.canonical() is not None
    assert entry.lexical_explanation.canonical() is not None


async def test_kind_and_proper_noun_come_from_the_overview(session):
    common = await generate_entry(EntrySpec(headword="abseil"), session.stages)
    assert common.entry.kind is LexemeKind.SIMPLEX
    assert common.entry.proper_noun is None

    named = await generate_entry(EntrySpec(headword="Einstein"), session.stages)
    assert named.entry.kind is LexemeKind.PROPER_NOUN
    assert named.entry.proper_noun is not None
    assert named.entry.proper_noun.entity_type is EntityType.PERSON
    assert named.entry.proper_noun.wikidata_qid == "Q937"
    assert named.entry.lexeme_id == "einstein"


async def test_relations_are_typed_and_confusables_carry_a_note(session):
    result = await generate_entry(EntrySpec(headword="abseil"), session.stages)
    sense = result.entry.pos_entries[0].senses[0]

    assert {r.type for r in sense.relations} == {
        RelationType.SYNONYM,
        RelationType.ANTONYM,
        RelationType.HYPERNYM,
        RelationType.HYPONYM,
        RelationType.CONFUSABLE_WITH,
    }
    confusables = sense.relations_of(RelationType.CONFUSABLE_WITH)
    assert len(confusables) == 1
    assert confusables[0].target.term == "confusable0"
    assert confusables[0].note
    # Nothing is resolved at generation time; the resolver is a separate, later pass.
    assert all(r.target.sense_id is None for r in sense.relations)


async def test_sense_domains_come_from_the_controlled_taxonomy(session):
    result = await generate_entry(EntrySpec(headword="abseil"), session.stages)
    domains = {sense.domain for _, sense, _ in result.entry.iter_senses()}
    assert domains == {DomainTag(SCRIPTED_SENSE_DOMAIN)}


async def test_example_spans_are_filled_free_first_then_by_model(session):
    result = await generate_entry(EntrySpec(headword="abseil"), session.stages)

    examples = [
        rendition.content
        for _, sense, _ in result.entry.iter_senses()
        for rendition in sense.examples
    ]
    assert examples
    assert all(example.span is not None for example in examples)
    # One example per sense contains the headword and is placed for free; the other does
    # not and reaches the LLM fallback.
    assert result.spans_found_deterministically == 3
    assert result.spans_found_by_model == 3
    assert result.spans_unresolved == 0
    placed = next(e for e in examples if e.matched == "abseil")
    assert placed.text[placed.span[0] : placed.span[1]] == "abseil"


async def test_span_fallback_can_be_switched_off(session):
    result = await generate_entry(
        EntrySpec(headword="abseil", with_span_fallback=False), session.stages
    )
    assert result.spans_unresolved == 3
    assert "spans" not in session.meter.summary().by_stage


async def test_provenance_is_a_table_referenced_by_relations_and_renditions(session):
    result = await generate_entry(EntrySpec(headword="abseil"), session.stages)
    entry = result.entry

    # FR-1.4: provenance is per stage, not per entry, and is now a keyed table.
    assert {p.stage.value for p in entry.provenance.values()} == {
        "overview",
        "senses",
        "spans",
        "etymology",
        "encyclopedia",
        "lexical_explanation",
    }
    assert all(p.cost_usd > 0 for p in entry.provenance.values())
    assert all(p.run_id == "test-run" for p in entry.provenance.values())
    assert all(p.service_tier == "flex" for p in entry.provenance.values())
    assert all(p.prompt_version == PROMPT_VERSION for p in entry.provenance.values())

    referenced = set()
    for _, sense, _ in entry.iter_senses():
        for rendition in sense.gloss:
            referenced.add(rendition.provenance_id)
        for rendition in sense.examples:
            referenced.add(rendition.provenance_id)
        for relation in sense.relations:
            referenced.add(relation.provenance_id)
    encyclopedia = entry.encyclopedia.canonical()
    assert encyclopedia is not None
    referenced.add(encyclopedia.provenance_id)

    assert None not in referenced
    assert referenced <= set(entry.provenance)

    assert result.cost_usd == pytest.approx(sum(p.cost_usd for p in entry.provenance.values()))
    assert session.meter.summary().total_usd == pytest.approx(result.cost_usd)


async def test_cost_by_stage_names_only_the_stages_that_ran(session):
    await generate_entry(EntrySpec(headword="abseil"), session.stages)
    by_stage = session.meter.summary().by_stage
    assert set(by_stage) == {
        "overview",
        "senses",
        "spans",
        "etymology",
        "encyclopedia",
        "lexical_explanation",
    }
    # The retrofit and enrichment stages exist in the config but cost nothing unasked.
    assert not {"classify_kind", "tag_domain", "resolve", "renditions"} & set(by_stage)


async def test_identifiers_are_derived_and_recomputable(session):
    result = await generate_entry(EntrySpec(headword="abseil"), session.stages)
    ids = [sid for _, _, sid in result.entry.iter_senses()]
    assert "abseil:noun:0" in ids
    assert "abseil:verb:0" in ids
    edges = result.entry.edges()
    assert any(e.edge_id == "abseil:noun:0-synonym->synonym0" for e in edges)
    assert all(e.target_sense is None for e in edges)


async def test_optional_sections_can_be_skipped(session):
    spec = EntrySpec(
        headword="abseil",
        with_etymology=False,
        with_encyclopedia=False,
        with_lexical_explanation=False,
    )
    result = await generate_entry(spec, session.stages)
    assert result.entry.etymology is None
    assert result.entry.encyclopedia.canonical() is None
    assert {p.stage.value for p in result.entry.provenance.values()} == {
        "overview",
        "senses",
        "spans",
    }


async def test_explicit_part_of_speech_restriction(session):
    spec = EntrySpec(headword="abseil", parts_of_speech=[PartOfSpeech.VERB])
    result = await generate_entry(spec, session.stages)
    assert [e.pos for e in result.entry.pos_entries] == [PartOfSpeech.VERB]


async def test_schema_failure_is_retried_then_reported(config, failing_model):
    async with RunSession(config, model_override=failing_model, run_id="fail-run") as session:
        with pytest.raises(StageFailedError) as excinfo:
            await generate_entry(EntrySpec(headword="abseil"), session.stages)
    assert excinfo.value.stage == "overview"
    assert excinfo.value.attempts == config.policy("overview").max_attempts


async def test_budget_ceiling_stops_generation(config, scripted_model):
    config.budget_usd = 1e-9  # smaller than any single call's reservation
    async with RunSession(config, model_override=scripted_model, run_id="broke") as session:
        with pytest.raises(BudgetExceededError):
            await generate_entry(EntrySpec(headword="abseil"), session.stages)
    assert session.meter.summary().total_usd == 0.0
