"""Example 3: enrich an existing entry.

v3 makes this one uniform operation: every text-bearing field is a rendition set, so
graded definitions, parallel registers, rewritten examples and a graded encyclopedia are
the same request against different owners.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from opengloss_generator import prompts, spans
from opengloss_generator.hygiene import is_headword_initial
from opengloss_generator.readability import flesch_kincaid_grade, grade_band
from opengloss_generator.runner import RunSession
from opengloss_generator.schema import (
    EntityType,
    LexemeKind,
    ProperNounInfo,
    Provenance,
    QAFlag,
    ReadingLevel,
    Register,
    Renditions,
    StageName,
    canonical_rendition,
)
from opengloss_generator.vocabulary import hard_word_share
from opengloss_generator.workflows import enrich as enrich_module
from opengloss_generator.workflows.enrich import (
    EnrichmentSpec,
    RenditionField,
    RenditionRequest,
    enrich_entry,
    plan_renditions,
)
from tests.conftest import (
    ABSENT_HEADWORD,
    BOTH_HEADWORD,
    COMPLEX_HEADWORD,
    HARD_VOCAB_HEADWORD,
    HARD_VOCAB_RENDITION,
    INITIAL_HEADWORD,
    MARKDOWN_HEADWORD,
    make_entry,
)

ALL_LEVELS = [
    ReadingLevel.GRADE_1,
    ReadingLevel.GRADE_5,
    ReadingLevel.GRADE_10,
    ReadingLevel.COLLEGE,
]
ALL_REGISTERS = [
    Register.INFORMAL,
    Register.TECHNICAL,
    Register.FORMAL,
    Register.MARKETING,
]


def _glosses(levels=(), styles=()) -> EnrichmentSpec:
    return EnrichmentSpec.for_glosses(levels, styles)


async def test_generates_definitions_at_four_reading_levels(session):
    entry = make_entry()
    result = await enrich_entry(entry, _glosses(ALL_LEVELS), session.stages)

    assert result.renditions_added == 4
    sense = entry.pos_entries[0].senses[0]
    added = [r for r in sense.gloss if not r.is_canonical]
    assert {r.reading_level for r in added} == set(ALL_LEVELS)
    assert all(r.style is Register.PLAIN for r in added)
    assert "abseil:verb:0#grade_1/plain" in entry.rendition_ids()


async def test_generates_parallel_registers(session):
    entry = make_entry()
    result = await enrich_entry(entry, _glosses(styles=ALL_REGISTERS), session.stages)

    assert result.renditions_added == 4
    sense = entry.pos_entries[0].senses[0]
    added = [r for r in sense.gloss if not r.is_canonical]
    assert {r.style for r in added} == set(ALL_REGISTERS)
    assert all(r.reading_level is ReadingLevel.NEUTRAL for r in added)


async def test_levels_and_registers_are_crossed(session):
    entry = make_entry()
    result = await enrich_entry(entry, _glosses(ALL_LEVELS, ALL_REGISTERS), session.stages)
    assert result.renditions_added == 16


async def test_every_missing_target_for_one_owner_comes_from_one_call(config, scripted_model):
    # FR-3.4: one call per (owner, field), not one call per target. Separate calls would
    # cost more and produce renditions that converge on the same middle register.
    #
    # D-51's vocabulary check is switched off for this one test: the scripted rendition
    # text carries four words that are not on the familiar-word list ("text", "rewrite",
    # "gloss" and the register's own name), which is over the grade_1 band and buys the
    # single shared retry. That retry is a *retry*, not a second target's call, but it
    # does spend money, and this test is about the cost identity rather than about the
    # check. `test_a_grade_1_vocabulary_miss_is_regenerated_once` covers the retry itself.
    config.readability.vocabulary_check = False
    async with RunSession(config, model_override=scripted_model, run_id="test-run") as run:
        entry = make_entry()
        await enrich_entry(entry, _glosses(ALL_LEVELS, ALL_REGISTERS), run.stages)
        calls = [p for p in entry.provenance.values() if p.stage.value == "renditions"]
        assert len(calls) == 1
        assert run.meter.summary().by_stage["renditions"] == pytest.approx(calls[0].cost_usd)


async def test_gloss_and_examples_are_separate_owners_and_separate_calls(session):
    entry = make_entry()
    spec = EnrichmentSpec(
        renditions=[
            RenditionRequest(field=RenditionField.GLOSS, levels=[ReadingLevel.GRADE_1]),
            RenditionRequest(field=RenditionField.EXAMPLES, levels=[ReadingLevel.GRADE_1]),
        ]
    )
    result = await enrich_entry(entry, spec, session.stages)

    assert result.renditions_added == 2
    assert result.calls == 2
    sense = entry.pos_entries[0].senses[0]
    rewritten = sense.examples.get(ReadingLevel.GRADE_1, Register.PLAIN)
    assert rewritten is not None
    # The span is found post-hoc by find_span, never asked of the model.
    assert rewritten.content.span is not None
    assert rewritten.content.matched.lower() == "abseil"


async def test_encyclopedia_defaults_to_reading_levels_only(session, config):
    # The encyclopedia is the one field whose output is the length of its input, so the
    # default target set deliberately omits the register axis.
    expected = [(level, Register.PLAIN) for level in ALL_LEVELS]
    assert config.encyclopedia_rendition_targets == expected

    entry = make_entry()
    spec = EnrichmentSpec(
        renditions=[
            RenditionRequest(
                field=RenditionField.ENCYCLOPEDIA,
                levels=[level for level, _ in config.encyclopedia_rendition_targets],
            )
        ],
        with_encyclopedia=True,
    )
    result = await enrich_entry(entry, spec, session.stages)

    assert result.sections_added == ["encyclopedia"]
    assert result.renditions_added == 4
    assert {r.reading_level for r in entry.encyclopedia if not r.is_canonical} == set(ALL_LEVELS)
    assert {r.style for r in entry.encyclopedia} == {Register.PLAIN}


async def test_enrichment_is_idempotent_and_free_the_second_time(session):
    entry = make_entry()
    first = await enrich_entry(entry, _glosses(ALL_LEVELS), session.stages)
    cost_after_first = session.meter.summary().total_usd

    second = await enrich_entry(entry, _glosses(ALL_LEVELS), session.stages)

    assert first.changed
    assert not second.changed
    assert second.cost_usd == 0.0
    assert second.calls == 0
    assert session.meter.summary().total_usd == cost_after_first


async def test_a_wholly_empty_request_costs_nothing(session):
    entry = make_entry()
    result = await enrich_entry(entry, EnrichmentSpec(), session.stages)
    assert not result.changed
    assert result.cost_usd == 0.0
    assert result.calls == 0


async def test_only_the_missing_renditions_are_requested(session):
    entry = make_entry(variants=True)  # already has grade_1/plain
    result = await enrich_entry(entry, _glosses(ALL_LEVELS), session.stages)
    assert result.renditions_added == 3
    assert len(entry.pos_entries[0].senses[0].gloss) == 5  # canonical + 4 levels


async def test_missing_sections_are_filled_without_touching_existing_ones(session):
    entry = make_entry()
    await enrich_entry(entry, EnrichmentSpec(with_lexical_explanation=True), session.stages)
    written = entry.lexical_explanation.canonical().content

    spec = EnrichmentSpec(
        with_etymology=True, with_encyclopedia=True, with_lexical_explanation=True
    )
    result = await enrich_entry(entry, spec, session.stages)

    assert set(result.sections_added) == {"etymology", "encyclopedia"}
    assert entry.lexical_explanation.canonical().content == written
    assert entry.etymology is not None


async def test_replace_regenerates_an_existing_section(session):
    entry = make_entry()
    entry.encyclopedia.add(canonical_rendition("stale text"))
    spec = EnrichmentSpec(with_encyclopedia=True, replace=True)
    await enrich_entry(entry, spec, session.stages)
    assert entry.encyclopedia.canonical().content != "stale text"


def test_plan_renditions_reports_only_gaps():
    entry = make_entry(variants=True)
    plan = plan_renditions(entry, _glosses(ALL_LEVELS))
    assert plan == [("abseil:verb:0", "gloss", 3)]
    assert plan_renditions(entry, EnrichmentSpec()) == []


def test_request_targets_cross_the_two_axes():
    request = RenditionRequest(
        field=RenditionField.GLOSS, levels=ALL_LEVELS, styles=[Register.PLAIN]
    )
    assert len(request.targets()) == 4
    assert RenditionRequest(field=RenditionField.GLOSS).targets() == [
        (ReadingLevel.NEUTRAL, Register.PLAIN)
    ]


# --------------------------------------------------------------------------------------
# Instructions: long enough to be cached, and static
# --------------------------------------------------------------------------------------

# OpenAI caches a prompt prefix only from 1,024 tokens up. The iteration-1 pilot got zero
# cache hits on 177K input tokens because the renditions prefix was ~350 tokens; at ~3.7
# characters per token, 4,500 characters clears the threshold with room to spare.
CACHEABLE_PREFIX_CHARS = 4500


def test_renditions_instructions_clear_the_prompt_cache_minimum():
    assert len(prompts.RENDITIONS_INSTRUCTIONS) >= CACHEABLE_PREFIX_CHARS


def test_renditions_instructions_are_byte_stable():
    # Byte-stability is the whole basis of prefix caching, so this asserts that a fresh
    # execution of the module produces the identical string: no timestamp, no uuid, no
    # set iteration order can have leaked into the instructions.
    spec = importlib.util.spec_from_file_location("prompts_reloaded", Path(prompts.__file__))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.RENDITIONS_INSTRUCTIONS == prompts.RENDITIONS_INSTRUCTIONS
    assert module.PROMPT_VERSION == prompts.PROMPT_VERSION


def test_renditions_instructions_carry_the_binding_per_level_constraints():
    text = prompts.RENDITIONS_INSTRUCTIONS
    for required in (
        "at most 10 words",  # grade_1
        "at most 16 words",  # grade_5
        "no markdown",
        "It is not a paraphrase of the source",
        "WORKED EXAMPLE",
    ):
        assert required in text


# --------------------------------------------------------------------------------------
# Readability: measurement, and one regeneration on a miss
# --------------------------------------------------------------------------------------


async def test_every_rendition_carries_its_measured_readability_grade(session):
    entry = make_entry()
    await enrich_entry(entry, _glosses(ALL_LEVELS), session.stages)

    added = [r for r in entry.pos_entries[0].senses[0].gloss if not r.is_canonical]
    assert len(added) == 4
    for rendition in added:
        assert rendition.assessment is not None
        assert rendition.assessment.readability_grade == pytest.approx(
            flesch_kincaid_grade(rendition.content, ignore=(entry.headword,)), abs=0.005
        )


async def test_an_example_rendition_is_measured_too(session):
    entry = make_entry()
    spec = EnrichmentSpec(
        renditions=[RenditionRequest(field=RenditionField.EXAMPLES, levels=[ReadingLevel.GRADE_1])]
    )
    await enrich_entry(entry, spec, session.stages)

    rewritten = entry.pos_entries[0].senses[0].examples.get(ReadingLevel.GRADE_1, Register.PLAIN)
    assert rewritten.assessment.readability_grade == pytest.approx(
        flesch_kincaid_grade(rewritten.content.text, ignore=(entry.headword,)), abs=0.005
    )


async def test_a_grade_1_miss_is_regenerated_once_and_the_better_one_kept(session):
    # The scripted model returns an unreadable grade_1 rewrite for this headword, and a
    # simple one only when the prompt carries the readability feedback.
    entry = make_entry(COMPLEX_HEADWORD)
    result = await enrich_entry(entry, _glosses(ALL_LEVELS), session.stages)

    assert result.renditions_added == 4
    assert result.calls == 2  # one call, one retry, and never a loop
    assert session.meter.summary().calls == 2  # both were priced
    assert session.meter.summary().by_stage["renditions"] == pytest.approx(result.cost_usd)

    gloss = entry.pos_entries[0].senses[0].gloss
    grade_1 = gloss.get(ReadingLevel.GRADE_1, Register.PLAIN)
    assert "extraordinarily" not in grade_1.content  # the retry's text won
    assert grade_1.assessment.readability_grade <= grade_band(ReadingLevel.GRADE_1)[1]
    # The retry fixed it, so the miss flag must not carry forward onto the kept rendition.
    assert QAFlag.OG_READABILITY_MISS not in grade_1.assessment.qa_flags

    # Only the regenerated rendition points at the retry's provenance record.
    others = {gloss.get(level, Register.PLAIN).provenance_id for level in ALL_LEVELS[1:]}
    assert len(others) == 1
    assert grade_1.provenance_id not in others
    assert len(entry.provenance) == 2


async def test_a_level_that_passes_is_not_regenerated(session):
    # grade_5 and up are inside their bands on the first call, so only grade_1 is retried
    # and the other three renditions are the ones the first call produced.
    entry = make_entry(COMPLEX_HEADWORD)
    await enrich_entry(
        entry, _glosses([ReadingLevel.GRADE_5, ReadingLevel.COLLEGE]), session.stages
    )
    assert session.meter.summary().calls == 1


async def test_the_readability_pass_can_be_switched_off(config, scripted_model):
    config.readability.enabled = False
    # The scripted unreadable text is also full of words no six-year-old knows, so D-51's
    # independent vocabulary check would buy its own retry; this test is about the band
    # check alone, so both are switched off.
    config.readability.vocabulary_check = False
    async with RunSession(config, model_override=scripted_model, run_id="test-run") as off:
        entry = make_entry(COMPLEX_HEADWORD)
        result = await enrich_entry(entry, _glosses([ReadingLevel.GRADE_1]), off.stages)

    assert result.calls == 1
    grade_1 = entry.pos_entries[0].senses[0].gloss.get(ReadingLevel.GRADE_1, Register.PLAIN)
    assert "extraordinarily" in grade_1.content
    # Measurement still happens; only the regeneration is off.
    assert grade_1.assessment.readability_grade > 10.0
    # The readability pass is off, so a miss is neither retried nor flagged.
    assert QAFlag.OG_READABILITY_MISS not in grade_1.assessment.qa_flags


# --------------------------------------------------------------------------------------
# B3: the readability-miss QAFlag (docs/STANDARDS-PLAN.md § 3)
# --------------------------------------------------------------------------------------


def _rendered_with(measured: enrich_module._Measured) -> enrich_module._Rendered:
    """Build a minimal ``_Rendered`` wrapping one measured grade_1/plain rendition."""
    return enrich_module._Rendered(
        produced={(ReadingLevel.GRADE_1, Register.PLAIN): measured},
        first=Provenance(stage=StageName.RENDITIONS, model="m", prompt_version="v1"),
        cost_usd=0.0,
        calls=1,
    )


def test_apply_renditions_flags_a_rendition_that_still_misses_its_band():
    # _apply_renditions is the only place the flag is written; this exercises it
    # directly rather than depending on the scripted model producing an unfixable miss.
    entry = make_entry()
    sense = entry.pos_entries[0].senses[0]
    work = enrich_module._Work(
        field=RenditionField.GLOSS,
        label="sense",
        source=sense.canonical_gloss(),
        renditions=sense.gloss,
        targets=[(ReadingLevel.GRADE_1, Register.PLAIN)],
        existing=[],
        forms=[],
    )
    measured = enrich_module._Measured(text="A hard sentence.", grade=9.0, missed_band=True)
    added = enrich_module._apply_renditions(entry, work, _rendered_with(measured))

    assert added == 1
    new_rendition = sense.gloss.get(ReadingLevel.GRADE_1, Register.PLAIN)
    assert new_rendition.assessment is not None
    assert new_rendition.assessment.qa_flags == [QAFlag.OG_READABILITY_MISS]


def test_apply_renditions_does_not_flag_a_rendition_inside_its_band():
    entry = make_entry()
    sense = entry.pos_entries[0].senses[0]
    work = enrich_module._Work(
        field=RenditionField.GLOSS,
        label="sense",
        source=sense.canonical_gloss(),
        renditions=sense.gloss,
        targets=[(ReadingLevel.GRADE_1, Register.PLAIN)],
        existing=[],
        forms=[],
    )
    measured = enrich_module._Measured(text="A short one.", grade=1.0, missed_band=False)
    added = enrich_module._apply_renditions(entry, work, _rendered_with(measured))

    assert added == 1
    new_rendition = sense.gloss.get(ReadingLevel.GRADE_1, Register.PLAIN)
    assert new_rendition.assessment is not None
    assert new_rendition.assessment.qa_flags == []


# --------------------------------------------------------------------------------------
# Markdown hygiene
# --------------------------------------------------------------------------------------


async def test_markdown_is_stripped_from_the_source_and_from_the_rewrite(session):
    # The scripted model quotes back the source it was shown, wrapped in markdown, so one
    # assertion covers both ends: what the model is shown, and what we store.
    entry = make_entry(MARKDOWN_HEADWORD)
    sense = entry.pos_entries[0].senses[0]
    sense.gloss = Renditions[str](root=[canonical_rendition("A **strong** rope `holds` you.")])

    await enrich_entry(entry, _glosses([ReadingLevel.GRADE_5]), session.stages)

    rewritten = sense.gloss.get(ReadingLevel.GRADE_5, Register.PLAIN).content
    assert rewritten == "The source was: A strong rope holds you. It is short and easy."
    assert not set(rewritten) & {"*", "`"}


# --------------------------------------------------------------------------------------
# D-39: a gloss rendition that opens with the headword is a miss too
# --------------------------------------------------------------------------------------


async def test_a_headword_initial_gloss_rendition_is_regenerated_once(session):
    # The scripted model opens every rendition of this headword with the headword itself,
    # and writes a clean one only when the prompt carries the headword feedback.
    entry = make_entry(INITIAL_HEADWORD)
    result = await enrich_entry(entry, _glosses(ALL_LEVELS), session.stages)

    assert result.renditions_added == 4
    assert result.calls == 2  # one call, one retry, and never a loop
    assert session.meter.summary().calls == 2  # both were priced

    added = [r for r in entry.pos_entries[0].senses[0].gloss if not r.is_canonical]
    assert len(added) == 4
    for rendition in added:
        assert not is_headword_initial(rendition.content, INITIAL_HEADWORD)
        # The retry fixed it, so the flag must not carry forward onto the kept rendition.
        assert QAFlag.OG_HEADWORD_INITIAL not in rendition.assessment.qa_flags


async def test_a_target_failing_both_checks_is_retried_once_not_twice(session):
    # This headword's grade_1 rendition opens with the headword *and* measures far outside
    # the grade_1 band. Two defects, one retry: the second call carries both notes.
    entry = make_entry(BOTH_HEADWORD)
    result = await enrich_entry(entry, _glosses(ALL_LEVELS), session.stages)

    assert result.calls == 2
    assert session.meter.summary().calls == 2

    grade_1 = entry.pos_entries[0].senses[0].gloss.get(ReadingLevel.GRADE_1, Register.PLAIN)
    assert not is_headword_initial(grade_1.content, BOTH_HEADWORD)
    assert grade_1.assessment.readability_grade <= grade_band(ReadingLevel.GRADE_1)[1]
    assert grade_1.assessment.qa_flags == []


def test_the_retry_note_carries_a_section_for_each_failing_check():
    misses = [(ReadingLevel.GRADE_1, 11.0, 3.0)]
    both = enrich_module._build_feedback("ban", misses, headword_initial=True)
    assert "Measured Flesch-Kincaid" in both
    assert "began with the headword" in both

    readability_only = enrich_module._build_feedback("ban", misses, headword_initial=False)
    assert "began with the headword" not in readability_only

    initial_only = enrich_module._build_feedback("ban", [], headword_initial=True)
    assert "Measured Flesch-Kincaid" not in initial_only
    # Alone, the note still has to say which targets to answer for.
    assert "for no other target" in initial_only


async def test_an_example_rendition_is_not_checked_for_a_headword_initial_opening(session):
    # The scripted example opens "The <headword> is here." -- which is exactly what an
    # example sentence is supposed to be free to do. Only glosses are definitions.
    entry = make_entry(INITIAL_HEADWORD)
    spec = EnrichmentSpec(
        renditions=[RenditionRequest(field=RenditionField.EXAMPLES, levels=[ReadingLevel.GRADE_1])]
    )
    result = await enrich_entry(entry, spec, session.stages)

    assert result.calls == 1
    rewritten = entry.pos_entries[0].senses[0].examples.get(ReadingLevel.GRADE_1, Register.PLAIN)
    assert is_headword_initial(rewritten.content.text, INITIAL_HEADWORD)
    assert rewritten.assessment.qa_flags == []


async def test_a_proper_noun_is_exempt_from_the_headword_initial_check(session):
    # A proper noun's definition legitimately names its own entity at every reading level
    # (D-30), so neither the retry nor the flag applies to it.
    entry = make_entry(INITIAL_HEADWORD)
    entry.kind = LexemeKind.PROPER_NOUN
    entry.proper_noun = ProperNounInfo(entity_type=EntityType.PLACE)
    result = await enrich_entry(entry, _glosses([ReadingLevel.GRADE_1]), session.stages)

    assert result.calls == 1
    grade_1 = entry.pos_entries[0].senses[0].gloss.get(ReadingLevel.GRADE_1, Register.PLAIN)
    assert is_headword_initial(grade_1.content, INITIAL_HEADWORD)
    assert grade_1.assessment.qa_flags == []


async def test_the_headword_initial_check_can_be_switched_off(config, scripted_model):
    config.readability.headword_initial_retry = False
    async with RunSession(config, model_override=scripted_model, run_id="test-run") as off:
        entry = make_entry(INITIAL_HEADWORD)
        result = await enrich_entry(entry, _glosses([ReadingLevel.GRADE_1]), off.stages)

    assert result.calls == 1
    grade_1 = entry.pos_entries[0].senses[0].gloss.get(ReadingLevel.GRADE_1, Register.PLAIN)
    assert is_headword_initial(grade_1.content, INITIAL_HEADWORD)
    # The check is off, so the opening is neither retried nor flagged.
    assert QAFlag.OG_HEADWORD_INITIAL not in grade_1.assessment.qa_flags


def test_apply_renditions_flags_a_rendition_that_still_opens_with_the_headword():
    entry = make_entry()
    sense = entry.pos_entries[0].senses[0]
    work = enrich_module._Work(
        field=RenditionField.GLOSS,
        label="sense",
        source=sense.canonical_gloss(),
        renditions=sense.gloss,
        targets=[(ReadingLevel.GRADE_1, Register.PLAIN)],
        existing=[],
        forms=[],
    )
    measured = enrich_module._Measured(
        text="An abseil is a way down a cliff.", grade=1.0, headword_initial=True
    )
    added = enrich_module._apply_renditions(entry, work, _rendered_with(measured))

    assert added == 1
    new_rendition = sense.gloss.get(ReadingLevel.GRADE_1, Register.PLAIN)
    assert new_rendition.assessment.qa_flags == [QAFlag.OG_HEADWORD_INITIAL]


def test_a_retry_that_opens_badly_never_displaces_one_that_does_not():
    clean = enrich_module._Measured(text="A way down a cliff.", grade=9.0)
    initial = enrich_module._Measured(text="Abseil is it.", grade=1.0, headword_initial=True)

    # Not opening with the headword outranks reading easier ...
    assert not enrich_module._is_better(initial, clean, check_initial=True)
    assert enrich_module._is_better(clean, initial, check_initial=True)
    # ... but only where the check applies at all.
    assert enrich_module._is_better(initial, clean, check_initial=False)
    # Where both open badly the defect is unfixed either way, so the shorter text wins.
    longer = enrich_module._Measured(
        text="Abseil is a much longer way of saying it.", grade=0.5, headword_initial=True
    )
    assert not enrich_module._is_better(longer, initial, check_initial=True)
    assert enrich_module._is_better(initial, longer, check_initial=True)


# --------------------------------------------------------------------------------------
# D-45: an example rendition that uses no form of its own headword is a miss too
# --------------------------------------------------------------------------------------


async def test_an_example_missing_the_headword_is_regenerated_once(session):
    # The scripted example for this headword never mentions it at all, and writes one
    # that does only when the prompt carries the headword-absent feedback. COLLEGE is not
    # a readability retry level, so only the new check can be driving the retry here.
    entry = make_entry(ABSENT_HEADWORD)
    spec = EnrichmentSpec(
        renditions=[RenditionRequest(field=RenditionField.EXAMPLES, levels=[ReadingLevel.COLLEGE])]
    )
    result = await enrich_entry(entry, spec, session.stages)

    assert result.renditions_added == 1
    assert result.calls == 2  # one call, one retry, and never a loop
    assert session.meter.summary().calls == 2  # both were priced

    rewritten = entry.pos_entries[0].senses[0].examples.get(ReadingLevel.COLLEGE, Register.PLAIN)
    assert rewritten.content.span is not None
    assert spans.find_span(rewritten.content.text, ABSENT_HEADWORD, []) is not None
    # The retry fixed it, so the flag must not carry forward onto the kept rendition.
    assert QAFlag.OG_HEADWORD_ABSENT not in rewritten.assessment.qa_flags


async def test_an_example_failing_both_readability_and_absence_is_retried_once(session):
    # This headword's scripted grade_1 example both measures far outside the grade_1 band
    # and never mentions the headword. Two defects, one retry: the second call carries
    # both notes, exactly as BOTH_HEADWORD exercises for the other pair of checks.
    entry = make_entry(ABSENT_HEADWORD)
    spec = EnrichmentSpec(
        renditions=[RenditionRequest(field=RenditionField.EXAMPLES, levels=[ReadingLevel.GRADE_1])]
    )
    result = await enrich_entry(entry, spec, session.stages)

    assert result.calls == 2
    assert session.meter.summary().calls == 2

    grade_1 = entry.pos_entries[0].senses[0].examples.get(ReadingLevel.GRADE_1, Register.PLAIN)
    assert grade_1.content.span is not None
    assert grade_1.assessment.readability_grade <= grade_band(ReadingLevel.GRADE_1)[1]
    assert grade_1.assessment.qa_flags == []


async def test_a_gloss_rendition_is_not_checked_for_headword_absence(session):
    # Only examples are required to use the headword; a gloss, an encyclopedia passage
    # and a usage note are not.
    entry = make_entry(ABSENT_HEADWORD)
    result = await enrich_entry(entry, _glosses([ReadingLevel.COLLEGE]), session.stages)

    assert result.calls == 1
    gloss = entry.pos_entries[0].senses[0].gloss.get(ReadingLevel.COLLEGE, Register.PLAIN)
    assert gloss.assessment.qa_flags == []


async def test_a_proper_noun_is_not_exempt_from_the_headword_absent_check(session):
    # Unlike the headword-initial check (D-30), there is no proper-noun exemption here:
    # an example sentence has to use its headword whatever kind of entry it is.
    entry = make_entry(ABSENT_HEADWORD)
    entry.kind = LexemeKind.PROPER_NOUN
    entry.proper_noun = ProperNounInfo(entity_type=EntityType.PLACE)
    spec = EnrichmentSpec(
        renditions=[RenditionRequest(field=RenditionField.EXAMPLES, levels=[ReadingLevel.COLLEGE])]
    )
    result = await enrich_entry(entry, spec, session.stages)

    assert result.calls == 2


async def test_the_headword_absent_check_can_be_switched_off(config, scripted_model):
    config.readability.headword_absent_retry = False
    async with RunSession(config, model_override=scripted_model, run_id="test-run") as off:
        entry = make_entry(ABSENT_HEADWORD)
        spec = EnrichmentSpec(
            renditions=[
                RenditionRequest(field=RenditionField.EXAMPLES, levels=[ReadingLevel.COLLEGE])
            ]
        )
        result = await enrich_entry(entry, spec, off.stages)

    assert result.calls == 1
    rewritten = entry.pos_entries[0].senses[0].examples.get(ReadingLevel.COLLEGE, Register.PLAIN)
    assert rewritten.content.span is None
    # The check is off, so the absence is neither retried nor flagged.
    assert QAFlag.OG_HEADWORD_ABSENT not in rewritten.assessment.qa_flags


def test_apply_renditions_flags_a_rendition_missing_the_headword():
    entry = make_entry()
    sense = entry.pos_entries[0].senses[0]
    work = enrich_module._Work(
        field=RenditionField.EXAMPLES,
        label="sense",
        source=sense.examples.canonical().content.text,
        renditions=sense.examples,
        targets=[(ReadingLevel.GRADE_1, Register.PLAIN)],
        existing=[],
        forms=[],
    )
    measured = enrich_module._Measured(
        text="Nothing here names the missing word at all.", grade=1.0, headword_absent=True
    )
    added = enrich_module._apply_renditions(entry, work, _rendered_with(measured))

    assert added == 1
    new_rendition = sense.examples.get(ReadingLevel.GRADE_1, Register.PLAIN)
    assert new_rendition.content.span is None
    assert new_rendition.assessment.qa_flags == [QAFlag.OG_HEADWORD_ABSENT]


def test_apply_renditions_does_not_flag_an_example_that_uses_the_headword():
    entry = make_entry()
    sense = entry.pos_entries[0].senses[0]
    work = enrich_module._Work(
        field=RenditionField.EXAMPLES,
        label="sense",
        source=sense.examples.canonical().content.text,
        renditions=sense.examples,
        targets=[(ReadingLevel.GRADE_1, Register.PLAIN)],
        existing=[],
        forms=["abseiled"],
    )
    measured = enrich_module._Measured(
        text="They abseiled again.", grade=1.0, headword_absent=False
    )
    added = enrich_module._apply_renditions(entry, work, _rendered_with(measured))

    assert added == 1
    new_rendition = sense.examples.get(ReadingLevel.GRADE_1, Register.PLAIN)
    assert new_rendition.content.span is not None
    assert new_rendition.assessment.qa_flags == []


def test_the_retry_note_carries_the_headword_absent_section_too():
    absent_only = enrich_module._build_feedback(
        "ban", [], headword_initial=False, headword_absent=True
    )
    assert "did not use the word" in absent_only
    assert "began with the headword" not in absent_only
    assert "for no other target" in absent_only

    none_of_them = enrich_module._build_feedback(
        "ban", [], headword_initial=False, headword_absent=False
    )
    assert none_of_them == ""


def test_a_retry_missing_the_headword_never_displaces_one_that_has_it():
    found = enrich_module._Measured(text="Uses the word right here.", grade=9.0)
    absent = enrich_module._Measured(text="Says nothing about it.", grade=1.0, headword_absent=True)

    # Not using the headword outranks reading easier ...
    assert not enrich_module._is_better(absent, found, check_initial=False, check_absent=True)
    assert enrich_module._is_better(found, absent, check_initial=False, check_absent=True)
    # ... but only where the check applies at all.
    assert enrich_module._is_better(absent, found, check_initial=False, check_absent=False)


# --------------------------------------------------------------------------------------
# D-51: a grade_1/grade_5 rendition whose words the reader will not know is a miss too
# --------------------------------------------------------------------------------------


def test_the_scripted_hard_vocabulary_text_passes_the_readability_band():
    # The premise of the whole check: this text is inside grade_1's Flesch-Kincaid band,
    # so nothing but the vocabulary check can be what makes it a miss -- exactly the
    # situation the judge found in 46.6% of grade_1 encyclopedia renditions.
    grade = flesch_kincaid_grade(HARD_VOCAB_RENDITION, ignore=(HARD_VOCAB_HEADWORD,))
    assert grade <= grade_band(ReadingLevel.GRADE_1)[1]
    assert hard_word_share(HARD_VOCAB_RENDITION, ignore=(HARD_VOCAB_HEADWORD,)) > 0.15


async def test_every_rendition_carries_its_measured_hard_word_share(session):
    # Measured at every level, not only the two that are acted on: it is free and it is
    # the only word-familiarity signal on disk.
    entry = make_entry()
    await enrich_entry(entry, _glosses(ALL_LEVELS), session.stages)

    added = [r for r in entry.pos_entries[0].senses[0].gloss if not r.is_canonical]
    assert len(added) == 4
    for rendition in added:
        assert rendition.assessment.hard_word_share == pytest.approx(
            hard_word_share(rendition.content, ignore=(entry.headword,)), abs=0.0005
        )


async def test_a_grade_1_vocabulary_miss_is_regenerated_once(session):
    # The scripted model answers this headword with short sentences full of words no
    # six-year-old knows, and answers simply only when the prompt carries the vocabulary
    # feedback.
    entry = make_entry(HARD_VOCAB_HEADWORD)
    result = await enrich_entry(entry, _glosses([ReadingLevel.GRADE_1]), session.stages)

    assert result.renditions_added == 1
    assert result.calls == 2  # one call, one retry, and never a loop
    assert session.meter.summary().calls == 2  # both were priced

    grade_1 = entry.pos_entries[0].senses[0].gloss.get(ReadingLevel.GRADE_1, Register.PLAIN)
    assert "chastity" not in grade_1.content.lower()  # the retry's text won
    assert grade_1.assessment.hard_word_share <= 0.15
    # The retry fixed it, so the flag must not carry forward onto the kept rendition.
    assert QAFlag.OG_HARD_VOCABULARY not in grade_1.assessment.qa_flags


async def test_a_level_with_no_vocabulary_band_is_not_regenerated(session):
    # college and grade_10 readers are expected to meet words they do not know, so the
    # same text is measured, stored, and left alone at those levels.
    entry = make_entry(HARD_VOCAB_HEADWORD)
    result = await enrich_entry(entry, _glosses([ReadingLevel.COLLEGE]), session.stages)

    assert result.calls == 1
    college = entry.pos_entries[0].senses[0].gloss.get(ReadingLevel.COLLEGE, Register.PLAIN)
    assert college.assessment.hard_word_share > 0.15
    assert college.assessment.qa_flags == []


async def test_the_vocabulary_check_can_be_switched_off(config, scripted_model):
    config.readability.vocabulary_check = False
    async with RunSession(config, model_override=scripted_model, run_id="test-run") as off:
        entry = make_entry(HARD_VOCAB_HEADWORD)
        result = await enrich_entry(entry, _glosses([ReadingLevel.GRADE_1]), off.stages)

    assert result.calls == 1
    grade_1 = entry.pos_entries[0].senses[0].gloss.get(ReadingLevel.GRADE_1, Register.PLAIN)
    assert "chastity" in grade_1.content.lower()
    # Measurement still happens; only the regeneration is off.
    assert grade_1.assessment.hard_word_share > 0.15
    assert QAFlag.OG_HARD_VOCABULARY not in grade_1.assessment.qa_flags


async def test_a_target_failing_readability_and_vocabulary_is_retried_once(session):
    # COMPLEX_HEADWORD's scripted grade_1 text is both unreadable and full of hard words.
    # Two defects, one retry, exactly as the other pairs of checks share theirs.
    entry = make_entry(COMPLEX_HEADWORD)
    result = await enrich_entry(entry, _glosses([ReadingLevel.GRADE_1]), session.stages)

    assert result.calls == 2
    grade_1 = entry.pos_entries[0].senses[0].gloss.get(ReadingLevel.GRADE_1, Register.PLAIN)
    assert grade_1.assessment.readability_grade <= grade_band(ReadingLevel.GRADE_1)[1]
    assert grade_1.assessment.qa_flags == []


def test_apply_renditions_flags_a_rendition_that_still_carries_hard_words():
    entry = make_entry()
    sense = entry.pos_entries[0].senses[0]
    work = enrich_module._Work(
        field=RenditionField.GLOSS,
        label="sense",
        source=sense.canonical_gloss(),
        renditions=sense.gloss,
        targets=[(ReadingLevel.GRADE_1, Register.PLAIN)],
        existing=[],
        forms=[],
    )
    measured = enrich_module._Measured(
        text="Monks made vows of poverty.", grade=1.0, hard_share=0.6, over_vocabulary=True
    )
    added = enrich_module._apply_renditions(entry, work, _rendered_with(measured))

    assert added == 1
    new_rendition = sense.gloss.get(ReadingLevel.GRADE_1, Register.PLAIN)
    assert new_rendition.assessment.hard_word_share == 0.6
    assert new_rendition.assessment.qa_flags == [QAFlag.OG_HARD_VOCABULARY]


def test_apply_renditions_stores_the_share_without_flagging_an_in_band_rendition():
    entry = make_entry()
    sense = entry.pos_entries[0].senses[0]
    work = enrich_module._Work(
        field=RenditionField.GLOSS,
        label="sense",
        source=sense.canonical_gloss(),
        renditions=sense.gloss,
        targets=[(ReadingLevel.GRADE_1, Register.PLAIN)],
        existing=[],
        forms=[],
    )
    measured = enrich_module._Measured(text="A short one.", grade=1.0, hard_share=0.04)
    enrich_module._apply_renditions(entry, work, _rendered_with(measured))

    new_rendition = sense.gloss.get(ReadingLevel.GRADE_1, Register.PLAIN)
    assert new_rendition.assessment.hard_word_share == 0.04
    assert new_rendition.assessment.qa_flags == []


def test_the_retry_note_names_the_offending_words():
    note = enrich_module._build_feedback(
        "vow",
        [],
        [(ReadingLevel.GRADE_1, ["mesopotamia", "chastity", "obedience"])],
        headword_initial=False,
    )
    assert "too hard for grade_1: mesopotamia, chastity, obedience" in note
    assert "for no other target" in note
    assert "Measured Flesch-Kincaid" not in note


def test_the_retry_note_carries_every_failing_check_at_once():
    note = enrich_module._build_feedback(
        "vow",
        [(ReadingLevel.GRADE_1, 9.0, 3.0)],
        [(ReadingLevel.GRADE_1, ["chastity"])],
        headword_initial=True,
        headword_absent=False,
    )
    assert "Measured Flesch-Kincaid" in note
    assert "too hard for grade_1" in note
    assert "began with the headword" in note


def test_a_retry_with_more_hard_words_never_displaces_one_with_fewer():
    easy = enrich_module._Measured(text="A big promise you keep.", grade=2.0, hard_share=0.0)
    hard = enrich_module._Measured(text="Chastity and obedience.", grade=0.5, hard_share=0.6)
    # The candidate reads easier by grade and is still refused: the words decide first.
    assert not enrich_module._is_better(
        hard, easy, check_initial=False, check_vocabulary=True, band=0.10, tolerance=0.05
    )
    assert enrich_module._is_better(
        easy, hard, check_initial=False, check_vocabulary=True, band=0.10, tolerance=0.05
    )
    # Both over the band: the lower share wins even though neither is fixed.
    worse = enrich_module._Measured(text="Chastity, obedience, poverty.", grade=0.1, hard_share=0.9)
    assert not enrich_module._is_better(
        worse, hard, check_initial=False, check_vocabulary=True, band=0.10, tolerance=0.05
    )
    # Where the level has no band the comparison is the grade one it has always been.
    assert enrich_module._is_better(hard, easy, check_initial=False, check_vocabulary=False)
