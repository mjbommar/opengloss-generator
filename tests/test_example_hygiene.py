"""Repair pass: rewrite stored examples that never use their own headword (D-45).

Companion to ``test_enrich.py``'s generation-time D-45 tests: those cover the check for
renditions produced from here on, these cover what is already on disk.
"""

from __future__ import annotations

from opengloss_generator.schema import (
    Example,
    Lexeme,
    LexemeKind,
    Morphology,
    PartOfSpeech,
    POSEntry,
    QAFlag,
    ReadingLevel,
    Register,
    Rendition,
    Renditions,
    Sense,
    canonical_rendition,
)
from opengloss_generator.workflows import example_hygiene as example_hygiene_module
from opengloss_generator.workflows.example_hygiene import run_example_hygiene
from tests.conftest import NO_SPAN_HEADWORD


def _entry_with_examples(headword: str, texts: list[str]) -> Lexeme:
    """Build an entry whose canonical examples have no spans yet."""
    sense = Sense(
        index=0,
        gloss=Renditions[str](root=[canonical_rendition("A test definition of the headword.")]),
        examples=Renditions[Example](
            root=[canonical_rendition(Example(text=text)) for text in texts]
        ),
    )
    return Lexeme.empty(
        headword,
        kind=LexemeKind.SIMPLEX,
        pos_entries=[POSEntry(pos=PartOfSpeech.VERB, senses=[sense], morphology=Morphology())],
    )


def _entry_with_leveled_example(headword: str, level: ReadingLevel, text: str) -> Lexeme:
    """Build an entry whose only example is a non-canonical rendition, with no span."""
    sense = Sense(
        index=0,
        gloss=Renditions[str](root=[canonical_rendition("A test definition of the headword.")]),
        examples=Renditions[Example](
            root=[
                Rendition[Example](
                    reading_level=level, style=Register.PLAIN, content=Example(text=text)
                )
            ]
        ),
    )
    return Lexeme.empty(
        headword,
        kind=LexemeKind.SIMPLEX,
        pos_entries=[POSEntry(pos=PartOfSpeech.VERB, senses=[sense], morphology=Morphology())],
    )


# --------------------------------------------------------------------------------------
# Offender detection (deterministic, no model call)
# --------------------------------------------------------------------------------------


def test_a_good_canonical_example_is_not_an_offender():
    entry = _entry_with_examples("abseil", ["They abseiled down the cliff."])
    example = entry.pos_entries[0].senses[0].examples[0].content
    example.span = (5, 13)  # as the spans pass would have set it
    assert example_hygiene_module._headword_absent_examples(entry) == []


def test_a_span_less_example_that_still_uses_a_form_is_not_an_offender():
    # No span yet, but "abseiling" is a generated -ing form the free spans pass would
    # place -- that pass's job, not this one's.
    entry = _entry_with_examples("abseil", ["They went abseiling yesterday."])
    assert example_hygiene_module._headword_absent_examples(entry) == []


def test_an_example_using_no_form_at_all_is_an_offender():
    entry = _entry_with_examples("custody", ["The judge let both parents care for their child."])
    offenders = example_hygiene_module._headword_absent_examples(entry)
    assert len(offenders) == 1
    assert offenders[0].label == "neutral/plain"
    assert offenders[0].gloss == "A test definition of the headword."


def test_a_retired_senses_examples_are_never_offenders():
    entry = _entry_with_examples("custody", ["The judge let both parents care for their child."])
    entry.pos_entries[0].senses[0].retired = True
    assert example_hygiene_module._headword_absent_examples(entry) == []


# --------------------------------------------------------------------------------------
# The pass: rewrite, verify, flag, and stay idempotent
# --------------------------------------------------------------------------------------


async def test_rewrites_a_span_less_example_and_finds_the_span(session):
    entry = _entry_with_examples("custody", ["The judge let both parents care for their child."])
    session.store.write(entry)

    outcome = await run_example_hygiene(session.store, session.stages, workers=4)

    assert outcome.entries_scanned == 1
    assert outcome.entries_changed == 1
    assert outcome.examples_rewritten == 1
    assert outcome.spans_found == 1
    assert outcome.still_absent == 0
    assert outcome.calls == 1
    assert outcome.cost_usd > 0.0

    stored = session.store.read("custody")
    rendition = stored.pos_entries[0].senses[0].examples[0]
    assert rendition.content.span is not None
    assert "custody" in rendition.content.matched.lower()
    assert rendition.assessment is not None
    assert rendition.assessment.readability_grade is not None
    assert QAFlag.OG_HEADWORD_ABSENT not in rendition.assessment.qa_flags
    # The superseded text is kept, zero-cost, in its own provenance record.
    notes = [p.note for p in stored.provenance.values() if p.note]
    assert "The judge let both parents care for their child." in notes


async def test_a_non_canonical_offender_is_found_and_rewritten_too(session):
    entry = _entry_with_leveled_example(
        "custody", ReadingLevel.GRADE_1, "The judge let both parents care for their child."
    )
    session.store.write(entry)

    outcome = await run_example_hygiene(session.store, session.stages, workers=4)

    assert outcome.examples_rewritten == 1
    stored = session.store.read("custody")
    rendition = stored.pos_entries[0].senses[0].examples.get(ReadingLevel.GRADE_1, Register.PLAIN)
    assert rendition.content.span is not None


async def test_leaves_a_good_example_alone_at_zero_cost(session):
    entry = _entry_with_examples("abseil", ["They abseiled down the cliff."])
    entry.pos_entries[0].senses[0].examples[0].content.span = (5, 13)
    session.store.write(entry)

    outcome = await run_example_hygiene(session.store, session.stages, workers=4)

    assert outcome.entries_scanned == 1
    assert outcome.entries_changed == 0
    assert outcome.examples_rewritten == 0
    assert outcome.calls == 0
    assert outcome.cost_usd == 0.0
    stored = session.store.read("abseil")
    assert (
        stored.pos_entries[0].senses[0].examples[0].content.text == "They abseiled down the cliff."
    )


async def test_a_second_sweep_over_a_fixed_entry_is_free(session):
    entry = _entry_with_examples("custody", ["The judge let both parents care for their child."])
    session.store.write(entry)

    await run_example_hygiene(session.store, session.stages, workers=4)
    spent = session.meter.summary().total_usd

    again = await run_example_hygiene(session.store, session.stages, workers=4)

    assert again.entries_scanned == 1
    assert again.calls == 0
    assert again.cost_usd == 0.0
    assert session.meter.summary().total_usd == spent


async def test_a_still_absent_example_is_flagged_and_never_rebilled(session):
    # NO_SPAN_HEADWORD's scripted rewrite never mentions the headword either, so the
    # rewrite is discarded and the old text kept, flagged.
    entry = _entry_with_examples(NO_SPAN_HEADWORD, ["Nothing here matches the entry at all."])
    session.store.write(entry)

    outcome = await run_example_hygiene(session.store, session.stages, workers=4)

    assert outcome.examples_rewritten == 0
    assert outcome.spans_found == 0
    assert outcome.still_absent == 1
    assert outcome.calls == 1
    assert outcome.cost_usd > 0.0

    stored = session.store.read(NO_SPAN_HEADWORD)
    rendition = stored.pos_entries[0].senses[0].examples[0]
    assert rendition.content.span is None
    assert rendition.content.text == "Nothing here matches the entry at all."
    assert rendition.assessment is not None
    assert QAFlag.OG_HEADWORD_ABSENT in rendition.assessment.qa_flags

    spent = session.meter.summary().total_usd
    again = await run_example_hygiene(session.store, session.stages, workers=4)

    # The marker means the entry is not re-billed, even though the defect persists.
    assert again.calls == 0
    assert again.cost_usd == 0.0
    assert again.still_absent == 1
    assert session.meter.summary().total_usd == spent


async def test_an_entry_with_nothing_to_fix_costs_nothing(session):
    entry = _entry_with_examples("abseil", ["They abseiled down the cliff."])
    entry.pos_entries[0].senses[0].examples[0].content.span = (5, 13)
    session.store.write(entry)

    outcome = await run_example_hygiene(session.store, session.stages, workers=4)

    assert outcome.calls == 0
    assert outcome.cost_usd == 0.0
    assert session.meter.summary().total_usd == 0.0


async def test_a_rewrite_that_duplicates_a_sibling_example_is_refused(session):
    # The scripted model answers "The custody showed up again in example 1." for the one
    # offender; a canonical example with exactly that text already exists on the sense.
    # Adopting the rewrite would give two renditions one uniqueness key and the stored
    # entry would fail validation on its next read (seen on the tier-2 sweep, 2026-09-03).
    entry = _entry_with_examples(
        "custody",
        [
            "The custody showed up again in example 1.",
            "The judge let both parents care for their child.",
        ],
    )
    session.store.write(entry)

    outcome = await run_example_hygiene(session.store, session.stages, workers=4)

    assert outcome.calls == 1
    assert outcome.examples_rewritten == 0
    assert outcome.still_absent == 1
    stored = session.store.read("custody")  # must still validate
    texts = [r.content.text for r in stored.pos_entries[0].senses[0].examples]
    assert texts == [
        "The custody showed up again in example 1.",
        "The judge let both parents care for their child.",
    ]
    offender = stored.pos_entries[0].senses[0].examples[1]
    assert offender.assessment is not None
    assert QAFlag.OG_HEADWORD_ABSENT in offender.assessment.qa_flags
