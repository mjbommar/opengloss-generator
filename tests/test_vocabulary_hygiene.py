"""Repair pass: rewrite stored renditions whose words their reader will not know (D-51).

Companion to ``test_enrich.py``'s generation-time D-51 tests: those cover the check for
renditions produced from here on, these cover what is already on disk. Every stored
offender here is *inside* its Flesch-Kincaid band, which is the whole point — the sibling
``readability_hygiene`` pass cannot see any of it.
"""

from __future__ import annotations

from opengloss_generator.schema import (
    Assessment,
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
from opengloss_generator.vocabulary import hard_word_share
from opengloss_generator.workflows import vocabulary_hygiene as vocabulary_hygiene_module
from opengloss_generator.workflows.vocabulary_hygiene import (
    VocabularyHygieneOutcome,
    run_vocabulary_hygiene,
)
from tests.conftest import (
    VOCAB_FIX_HEADWORD,
    VOCAB_INITIAL_HEADWORD,
    VOCAB_LOSES_HEADWORD,
)

# The judge's own example: short sentences of short words, so it passes its Flesch-Kincaid
# band, and words a six-year-old does not know, so it fails this one.
HARD_TEXT = "Monks made vows of poverty. Chastity and obedience were sworn."
EASY_TEXT = "It is a big yes that you say and keep."


def _entry(
    headword: str,
    *,
    gloss_renditions: list[Rendition[str]] | None = None,
    example_renditions: list[Rendition[Example]] | None = None,
    encyclopedia: list[Rendition[str]] | None = None,
    kind: LexemeKind = LexemeKind.SIMPLEX,
) -> Lexeme:
    """Build an entry carrying the renditions a test needs, and nothing else."""
    sense = Sense(
        index=0,
        gloss=Renditions[str](
            root=[
                canonical_rendition("A promise of a serious kind."),
                *(gloss_renditions or []),
            ]
        ),
        examples=Renditions[Example](
            root=[
                canonical_rendition(Example(text=f"The {headword} was kept.")),
                *(example_renditions or []),
            ]
        ),
    )
    entry = Lexeme.empty(
        headword,
        kind=kind,
        pos_entries=[POSEntry(pos=PartOfSpeech.NOUN, senses=[sense], morphology=Morphology())],
    )
    if encyclopedia:
        entry.encyclopedia = Renditions[str](
            root=[canonical_rendition("Encyclopedic prose about the headword."), *encyclopedia]
        )
    return entry


def _gloss(text: str, level: ReadingLevel = ReadingLevel.GRADE_1) -> Rendition[str]:
    return Rendition[str](reading_level=level, style=Register.PLAIN, content=text)


def _example(text: str, level: ReadingLevel = ReadingLevel.GRADE_1) -> Rendition[Example]:
    return Rendition[Example](reading_level=level, style=Register.PLAIN, content=Example(text=text))


# --------------------------------------------------------------------------------------
# Offender detection (deterministic, no model call)
# --------------------------------------------------------------------------------------


def test_a_grade_1_rendition_over_its_band_is_an_offender():
    entry = _entry("vow", gloss_renditions=[_gloss(HARD_TEXT)])
    offenders = vocabulary_hygiene_module._offenders(entry, 0.05)
    assert [o.field_name for o in offenders] == ["gloss"]
    assert offenders[0].rendition.reading_level is ReadingLevel.GRADE_1
    assert "chastity" in offenders[0].words


def test_an_easy_rendition_is_not_an_offender():
    entry = _entry("vow", gloss_renditions=[_gloss(EASY_TEXT)])
    assert vocabulary_hygiene_module._offenders(entry, 0.05) == []


def test_a_level_with_no_band_is_never_an_offender():
    # The same hard text at college and at grade_10, and the canonical rendition, which is
    # neutral: none of the three has a band, so none of them is selected.
    entry = _entry(
        "vow",
        gloss_renditions=[
            _gloss(HARD_TEXT, ReadingLevel.COLLEGE),
            _gloss(HARD_TEXT, ReadingLevel.GRADE_10),
        ],
    )
    assert vocabulary_hygiene_module._offenders(entry, 0.05) == []


def test_grade_5_has_a_wider_band_than_grade_1():
    # 2 hard words in 11 counted words: 0.18, over grade_1's 0.10+0.05 and under
    # grade_5's 0.25+0.05.
    text = "The vows were sworn and then the men went away."
    assert 0.15 < hard_word_share(text) <= 0.30
    entry = _entry(
        "promise",
        gloss_renditions=[
            _gloss(text, ReadingLevel.GRADE_1),
            _gloss(text, ReadingLevel.GRADE_5),
        ],
    )
    offenders = vocabulary_hygiene_module._offenders(entry, 0.05)
    assert [o.rendition.reading_level for o in offenders] == [ReadingLevel.GRADE_1]


def test_a_stored_share_is_used_when_it_is_there():
    # The figure the flag was set on, not a re-measurement of the same text.
    rendition = _gloss(EASY_TEXT)
    rendition.assessment = Assessment(hard_word_share=0.9)
    entry = _entry("vow", gloss_renditions=[rendition])
    offenders = vocabulary_hygiene_module._offenders(entry, 0.05)
    assert [o.share for o in offenders] == [0.9]


def test_every_text_bearing_field_is_covered():
    entry = _entry(
        "vow",
        gloss_renditions=[_gloss(HARD_TEXT)],
        example_renditions=[_example(HARD_TEXT)],
        encyclopedia=[_gloss(HARD_TEXT)],
    )
    offenders = vocabulary_hygiene_module._offenders(entry, 0.05)
    assert [o.field_name for o in offenders] == ["gloss", "examples", "encyclopedia"]
    # Every offender has an id of its own, which is what the marker digest is taken over.
    assert len({o.rendition_id for o in offenders}) == 3


# --------------------------------------------------------------------------------------
# The pass: rewrite, verify, flag, and stay idempotent
# --------------------------------------------------------------------------------------


async def test_rewrites_a_hard_gloss_and_clears_the_flag(session):
    entry = _entry(VOCAB_FIX_HEADWORD, gloss_renditions=[_gloss(HARD_TEXT)])
    session.store.write(entry)

    outcome = await run_vocabulary_hygiene(session.store, session.stages, workers=4)

    assert outcome.entries_scanned == 1
    assert outcome.entries_changed == 1
    assert outcome.renditions_rewritten == 1
    assert outcome.now_in_band == 1
    assert outcome.still_over == 0
    assert outcome.calls == 1
    assert outcome.cost_usd > 0.0

    stored = session.store.read(VOCAB_FIX_HEADWORD)
    rendition = stored.pos_entries[0].senses[0].gloss.get(ReadingLevel.GRADE_1, Register.PLAIN)
    assert "chastity" not in rendition.content.lower()
    assert rendition.assessment.hard_word_share <= 0.15
    assert rendition.assessment.readability_grade is not None
    assert QAFlag.OG_HARD_VOCABULARY not in rendition.assessment.qa_flags
    # The superseded text is kept, zero-cost, in its own provenance record.
    notes = [p.note for p in stored.provenance.values() if p.note]
    assert HARD_TEXT in notes


async def test_an_example_rewrite_keeps_the_headword_and_gets_its_span(session):
    entry = _entry(VOCAB_FIX_HEADWORD, example_renditions=[_example(HARD_TEXT)])
    session.store.write(entry)

    outcome = await run_vocabulary_hygiene(session.store, session.stages, workers=4)

    assert outcome.renditions_rewritten == 1
    stored = session.store.read(VOCAB_FIX_HEADWORD)
    rendition = stored.pos_entries[0].senses[0].examples.get(ReadingLevel.GRADE_1, Register.PLAIN)
    assert VOCAB_FIX_HEADWORD in rendition.content.text
    assert rendition.content.span is not None


async def test_an_encyclopedia_rendition_is_rewritten_too(session):
    entry = _entry(VOCAB_FIX_HEADWORD, encyclopedia=[_gloss(HARD_TEXT)])
    session.store.write(entry)

    outcome = await run_vocabulary_hygiene(session.store, session.stages, workers=4)

    assert outcome.renditions_rewritten == 1
    stored = session.store.read(VOCAB_FIX_HEADWORD)
    assert "chastity" not in stored.encyclopedia.get(ReadingLevel.GRADE_1, Register.PLAIN).content


async def test_a_rewrite_that_is_no_simpler_is_refused_and_flagged(session):
    # Any headword but the marker ones gets its own text echoed back, which is exactly as
    # hard as what is stored.
    entry = _entry("vow", gloss_renditions=[_gloss(HARD_TEXT)])
    session.store.write(entry)

    outcome = await run_vocabulary_hygiene(session.store, session.stages, workers=4)

    assert outcome.renditions_rewritten == 0
    assert outcome.still_over == 1
    assert outcome.now_in_band == 0
    assert outcome.calls == 1

    stored = session.store.read("vow")
    rendition = stored.pos_entries[0].senses[0].gloss.get(ReadingLevel.GRADE_1, Register.PLAIN)
    assert rendition.content == HARD_TEXT
    assert QAFlag.OG_HARD_VOCABULARY in rendition.assessment.qa_flags
    assert rendition.assessment.hard_word_share > 0.15


async def test_a_gloss_rewrite_that_opens_with_the_headword_is_refused(session):
    # D-47's regression: making a hard definition easy must not produce "A ban is an
    # order to stop."
    entry = _entry(VOCAB_INITIAL_HEADWORD, gloss_renditions=[_gloss(HARD_TEXT)])
    session.store.write(entry)

    outcome = await run_vocabulary_hygiene(session.store, session.stages, workers=4)

    assert outcome.renditions_rewritten == 0
    assert outcome.still_over == 1
    stored = session.store.read(VOCAB_INITIAL_HEADWORD)
    rendition = stored.pos_entries[0].senses[0].gloss.get(ReadingLevel.GRADE_1, Register.PLAIN)
    assert rendition.content == HARD_TEXT
    assert QAFlag.OG_HARD_VOCABULARY in rendition.assessment.qa_flags


async def test_the_same_rewrite_is_accepted_for_an_example_where_the_rule_does_not_apply(session):
    # A headword-initial *example* is ordinary English; only a definition is held to it.
    entry = _entry(VOCAB_INITIAL_HEADWORD, example_renditions=[_example(HARD_TEXT)])
    session.store.write(entry)

    outcome = await run_vocabulary_hygiene(session.store, session.stages, workers=4)

    assert outcome.renditions_rewritten == 1
    stored = session.store.read(VOCAB_INITIAL_HEADWORD)
    rendition = stored.pos_entries[0].senses[0].examples.get(ReadingLevel.GRADE_1, Register.PLAIN)
    assert rendition.content.span is not None


async def test_an_example_rewrite_that_loses_the_headword_is_refused(session):
    entry = _entry(VOCAB_LOSES_HEADWORD, example_renditions=[_example(HARD_TEXT)])
    session.store.write(entry)

    outcome = await run_vocabulary_hygiene(session.store, session.stages, workers=4)

    assert outcome.renditions_rewritten == 0
    assert outcome.still_over == 1
    stored = session.store.read(VOCAB_LOSES_HEADWORD)
    rendition = stored.pos_entries[0].senses[0].examples.get(ReadingLevel.GRADE_1, Register.PLAIN)
    assert rendition.content.text == HARD_TEXT


async def test_a_gloss_rewrite_that_loses_the_headword_is_still_accepted(session):
    # A definition is not required to say the word it defines -- the opposite, in fact.
    entry = _entry(VOCAB_LOSES_HEADWORD, gloss_renditions=[_gloss(HARD_TEXT)])
    session.store.write(entry)

    outcome = await run_vocabulary_hygiene(session.store, session.stages, workers=4)

    assert outcome.renditions_rewritten == 1
    assert outcome.now_in_band == 1


async def test_an_entry_with_nothing_to_fix_costs_nothing(session):
    entry = _entry("vow", gloss_renditions=[_gloss(EASY_TEXT)])
    session.store.write(entry)

    outcome = await run_vocabulary_hygiene(session.store, session.stages, workers=4)

    assert outcome.entries_scanned == 1
    assert outcome.entries_changed == 0
    assert outcome.calls == 0
    assert outcome.cost_usd == 0.0
    assert session.meter.summary().total_usd == 0.0


async def test_a_second_sweep_over_a_fixed_entry_is_free(session):
    entry = _entry(VOCAB_FIX_HEADWORD, gloss_renditions=[_gloss(HARD_TEXT)])
    session.store.write(entry)

    await run_vocabulary_hygiene(session.store, session.stages, workers=4)
    spent = session.meter.summary().total_usd
    text = (
        session.store.read(VOCAB_FIX_HEADWORD)
        .pos_entries[0]
        .senses[0]
        .gloss.get(ReadingLevel.GRADE_1, Register.PLAIN)
        .content
    )

    again = await run_vocabulary_hygiene(session.store, session.stages, workers=4)

    assert again.entries_scanned == 1
    assert again.calls == 0
    assert again.cost_usd == 0.0
    assert session.meter.summary().total_usd == spent
    # And the text the first sweep settled on is untouched.
    stored = session.store.read(VOCAB_FIX_HEADWORD)
    settled = stored.pos_entries[0].senses[0].gloss.get(ReadingLevel.GRADE_1, Register.PLAIN)
    assert settled.content == text


async def test_an_offender_that_could_not_be_fixed_is_not_rebilled(session):
    entry = _entry("vow", gloss_renditions=[_gloss(HARD_TEXT)])
    session.store.write(entry)

    await run_vocabulary_hygiene(session.store, session.stages, workers=4)
    spent = session.meter.summary().total_usd

    again = await run_vocabulary_hygiene(session.store, session.stages, workers=4)

    # The offending set has not changed, so the marker means no second answer is bought,
    # even though the defect persists (D-47).
    assert again.calls == 0
    assert again.cost_usd == 0.0
    assert again.still_over == 1
    assert session.meter.summary().total_usd == spent


async def test_only_the_named_ids_are_visited(session):
    session.store.write(_entry(VOCAB_FIX_HEADWORD, gloss_renditions=[_gloss(HARD_TEXT)]))
    session.store.write(_entry("vow", gloss_renditions=[_gloss(HARD_TEXT)]))

    outcome = await run_vocabulary_hygiene(
        session.store, session.stages, workers=4, lexeme_ids=[VOCAB_FIX_HEADWORD]
    )

    assert outcome.entries_scanned == 1
    assert outcome.renditions_rewritten == 1


def test_the_outcome_serialises_for_a_run_summary():
    outcome = VocabularyHygieneOutcome(
        entries_scanned=3, renditions_rewritten=2, now_in_band=2, still_over=1, cost_usd=0.1234567
    )
    as_dict = outcome.as_dict()
    assert as_dict["entries_scanned"] == 3
    assert as_dict["renditions_rewritten"] == 2
    assert as_dict["now_in_band"] == 2
    assert as_dict["still_over"] == 1
    assert as_dict["cost_usd"] == 0.123457
    assert as_dict["stopped_reason"] is None


def test_the_instructions_carry_the_level_constraints_verbatim():
    # Sliced out of RENDITIONS_INSTRUCTIONS rather than retyped, so a rewrite is held to
    # the same bar the original rendition was written against.
    from opengloss_generator.prompts import RENDITIONS_INSTRUCTIONS  # noqa: PLC0415

    instructions = vocabulary_hygiene_module.VOCABULARY_HYGIENE_INSTRUCTIONS
    assert "Only very common words" in instructions
    assert "Never begin a definition rendition" in instructions
    assert "gloss - one dictionary sentence" in instructions
    for line in ("Only very common words", "Never begin a definition rendition"):
        assert line in RENDITIONS_INSTRUCTIONS
