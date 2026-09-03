"""Content hygiene: contradictory relations, junk examples, degenerate renditions.

Companion to ``test_graph_hygiene.py``, which covers the *shape* repairs. Everything here
is about content the shape checks cannot see: two relation types about one target that
cannot both be true, an example that is not a sentence, a canonical example in an
academic register, and a rendition set whose members are the same text twice.
"""

from __future__ import annotations

import pytest

from opengloss_generator.schema import (
    Assessment,
    EntityType,
    Example,
    Lexeme,
    LexemeKind,
    Morphology,
    PartOfSpeech,
    POSEntry,
    ProperNounInfo,
    QAFlag,
    ReadingLevel,
    Register,
    Relation,
    RelationTarget,
    RelationType,
    Rendition,
    Renditions,
    Sense,
    canonical_rendition,
)
from opengloss_generator.workflows import content_hygiene as module
from opengloss_generator.workflows.content_hygiene import (
    FILLER_EXAMPLE_NOTE,
    FRAGMENT_EXAMPLE_NOTE,
    GARBAGE_EXAMPLE_NOTE,
    PROPER_NOUN_RETYPE_NOTE,
    SELF_SYNONYM_NOTE,
    SYNONYM_ANTONYM_NOTE,
    ContentHygieneStep,
    run_content_hygiene,
)
from tests.conftest import (
    DEGENERATE_ECHO_HEADWORD,
    DEGENERATE_INITIAL_HEADWORD,
    FRAGMENT_STILL_HEADWORD,
    NO_SPAN_HEADWORD,
)

DEFAULT_GLOSS = "A test definition written for the pass under test."


def _entry(
    headword: str,
    *,
    relations: list[Relation] | None = None,
    examples: list[str] | None = None,
    gloss: str = DEFAULT_GLOSS,
    extra_glosses: list[Rendition[str]] | None = None,
    kind: LexemeKind = LexemeKind.SIMPLEX,
    pos: PartOfSpeech = PartOfSpeech.NOUN,
) -> Lexeme:
    """Build a one-sense entry with whatever the test under way needs on it."""
    gloss_set = Renditions[str](root=[canonical_rendition(gloss)])
    for rendition in extra_glosses or []:
        gloss_set.add(rendition)
    sense = Sense(
        index=0,
        gloss=gloss_set,
        examples=Renditions[Example](
            root=[canonical_rendition(Example(text=text)) for text in examples or []]
        ),
        relations=relations or [],
    )
    return Lexeme.empty(
        headword,
        kind=kind,
        proper_noun=(
            ProperNounInfo(entity_type=EntityType.PLACE) if kind is LexemeKind.PROPER_NOUN else None
        ),
        pos_entries=[POSEntry(pos=pos, senses=[sense], morphology=Morphology())],
    )


def _relation(
    relation_type: RelationType,
    term: str,
    *,
    sense_id: str | None = None,
    note: str | None = None,
) -> Relation:
    """Build one typed relation, optionally already resolved."""
    return Relation(
        type=relation_type,
        target=RelationTarget(term=term, sense_id=sense_id),
        note=note,
    )


def _relations_of(entry: Lexeme) -> list[Relation]:
    """Return the first sense's relations."""
    return entry.pos_entries[0].senses[0].relations


def _gloss_texts(entry: Lexeme) -> list[str]:
    """Return every gloss rendition text of the first sense, in document order."""
    return [rendition.content for rendition in entry.pos_entries[0].senses[0].gloss]


def _notes(entry: Lexeme) -> list[str]:
    """Return every non-empty provenance note on an entry."""
    return [record.note for record in entry.provenance.values() if record.note]


# --------------------------------------------------------------------------------------
# Step 1 — self_synonym
# --------------------------------------------------------------------------------------


async def test_a_self_synonym_is_demoted_to_see_also_for_free(session):
    entry = _entry("teach", relations=[_relation(RelationType.SYNONYM, "teach")])
    session.store.write(entry)

    outcome = await run_content_hygiene(
        session.store, session.stages, workers=4, only={ContentHygieneStep.SELF_SYNONYM}
    )

    result = outcome.steps[ContentHygieneStep.SELF_SYNONYM]
    assert result.demoted == 1
    assert result.entries_changed == 1
    assert result.calls == 0
    assert result.cost_usd == 0.0

    stored = session.store.read("teach")
    relation = _relations_of(stored)[0]
    assert relation.type is RelationType.SEE_ALSO
    assert relation.note == SELF_SYNONYM_NOTE


async def test_a_synonym_toward_another_lexeme_is_left_alone(session):
    entry = _entry("teach", relations=[_relation(RelationType.SYNONYM, "instruct")])
    session.store.write(entry)

    outcome = await run_content_hygiene(
        session.store, session.stages, workers=4, only={ContentHygieneStep.SELF_SYNONYM}
    )

    assert outcome.steps[ContentHygieneStep.SELF_SYNONYM].demoted == 0
    assert _relations_of(session.store.read("teach"))[0].type is RelationType.SYNONYM


async def test_a_self_synonym_keeps_any_note_it_already_carried(session):
    entry = _entry(
        "teach", relations=[_relation(RelationType.SYNONYM, "teach", note="imported from v1.3")]
    )
    session.store.write(entry)

    await run_content_hygiene(
        session.store, session.stages, workers=4, only={ContentHygieneStep.SELF_SYNONYM}
    )

    assert _relations_of(session.store.read("teach"))[0].note == (
        f"{SELF_SYNONYM_NOTE} | imported from v1.3"
    )


# --------------------------------------------------------------------------------------
# Step 2 — synonym_antonym
# --------------------------------------------------------------------------------------


async def test_an_antonym_contradicted_by_a_synonym_is_demoted(session):
    entry = _entry(
        "refrigerator",
        relations=[
            _relation(RelationType.SYNONYM, "fridge"),
            _relation(RelationType.ANTONYM, "fridge"),
        ],
    )
    session.store.write(entry)

    outcome = await run_content_hygiene(
        session.store, session.stages, workers=4, only={ContentHygieneStep.SYNONYM_ANTONYM}
    )

    assert outcome.steps[ContentHygieneStep.SYNONYM_ANTONYM].demoted == 1
    synonym, antonym = _relations_of(session.store.read("refrigerator"))
    assert synonym.type is RelationType.SYNONYM
    assert antonym.type is RelationType.SEE_ALSO
    assert antonym.note == SYNONYM_ANTONYM_NOTE


async def test_the_far_side_reciprocal_is_demoted_under_its_own_lock(session):
    session.store.write(
        _entry(
            "refrigerator",
            relations=[
                _relation(RelationType.SYNONYM, "fridge"),
                _relation(RelationType.ANTONYM, "fridge", sense_id="fridge:noun:0"),
            ],
        )
    )
    session.store.write(
        _entry(
            "fridge",
            relations=[
                _relation(
                    RelationType.ANTONYM,
                    "refrigerator",
                    sense_id="refrigerator:noun:0",
                    note="reciprocal of refrigerator:noun:0",
                )
            ],
        )
    )

    outcome = await run_content_hygiene(
        session.store, session.stages, workers=4, only={ContentHygieneStep.SYNONYM_ANTONYM}
    )

    result = outcome.steps[ContentHygieneStep.SYNONYM_ANTONYM]
    assert result.demoted == 2
    assert result.entries_changed == 2
    assert result.calls == 0

    far_side = _relations_of(session.store.read("fridge"))[0]
    assert far_side.type is RelationType.SEE_ALSO
    assert "reciprocal of refrigerator:noun:0" in (far_side.note or "")


async def test_a_far_side_antonym_about_another_sense_survives(session):
    session.store.write(
        _entry(
            "refrigerator",
            relations=[
                _relation(RelationType.SYNONYM, "fridge"),
                _relation(RelationType.ANTONYM, "fridge", sense_id="fridge:noun:0"),
            ],
        )
    )
    # Resolved to a *different* sense of refrigerator, and carrying no reciprocal note:
    # this assertion is not the copy of the one that was demoted.
    session.store.write(
        _entry(
            "fridge",
            relations=[
                _relation(RelationType.ANTONYM, "refrigerator", sense_id="refrigerator:noun:7")
            ],
        )
    )

    outcome = await run_content_hygiene(
        session.store, session.stages, workers=4, only={ContentHygieneStep.SYNONYM_ANTONYM}
    )

    assert outcome.steps[ContentHygieneStep.SYNONYM_ANTONYM].demoted == 1
    assert _relations_of(session.store.read("fridge"))[0].type is RelationType.ANTONYM


# --------------------------------------------------------------------------------------
# Step 3 — synonym_hypernym
# --------------------------------------------------------------------------------------


async def test_a_proper_noun_pair_becomes_instance_of_for_free(session):
    entry = _entry(
        "tahoe",
        kind=LexemeKind.PROPER_NOUN,
        gloss="A large freshwater lake in the Sierra Nevada.",
        relations=[
            _relation(RelationType.SYNONYM, "lake"),
            _relation(RelationType.HYPERNYM, "lake"),
        ],
    )
    session.store.write(entry)

    outcome = await run_content_hygiene(
        session.store, session.stages, workers=4, only={ContentHygieneStep.SYNONYM_HYPERNYM}
    )

    result = outcome.steps[ContentHygieneStep.SYNONYM_HYPERNYM]
    assert result.retyped == 1
    assert result.demoted == 1
    assert result.calls == 0
    assert result.cost_usd == 0.0

    synonym, hypernym = _relations_of(session.store.read("tahoe"))
    assert synonym.type is RelationType.SEE_ALSO
    assert hypernym.type is RelationType.INSTANCE_OF
    assert hypernym.note == PROPER_NOUN_RETYPE_NOTE


async def test_the_nano_verdict_demotes_the_losing_relation(session):
    # The scripted model reads the target term out of each row: "syn..." keeps the
    # synonym, "neither..." keeps neither, anything else keeps the hypernym.
    entry = _entry(
        "teach",
        relations=[
            _relation(RelationType.SYNONYM, "synonymous"),
            _relation(RelationType.HYPERNYM, "synonymous"),
            _relation(RelationType.SYNONYM, "title"),
            _relation(RelationType.HYPERNYM, "title"),
            _relation(RelationType.SYNONYM, "neitherone"),
            _relation(RelationType.HYPERNYM, "neitherone"),
        ],
    )
    session.store.write(entry)

    outcome = await run_content_hygiene(
        session.store, session.stages, workers=4, only={ContentHygieneStep.SYNONYM_HYPERNYM}
    )

    result = outcome.steps[ContentHygieneStep.SYNONYM_HYPERNYM]
    assert result.calls == 1
    assert result.cost_usd > 0.0
    assert result.accepted == 3
    assert result.rejected == 0
    # synonym kept + hypernym demoted, hypernym kept + synonym demoted, both demoted.
    assert result.demoted == 4

    by_term = {
        (relation.target.term, relation.type)
        for relation in _relations_of(session.store.read("teach"))
    }
    assert ("synonymous", RelationType.SYNONYM) in by_term
    assert ("synonymous", RelationType.SEE_ALSO) in by_term
    assert ("title", RelationType.HYPERNYM) in by_term
    assert ("title", RelationType.SEE_ALSO) in by_term
    assert ("neitherone", RelationType.SEE_ALSO) in by_term
    assert ("neitherone", RelationType.SYNONYM) not in by_term
    assert ("neitherone", RelationType.HYPERNYM) not in by_term


async def test_the_prompt_carries_the_resolved_targets_own_gloss(session):
    session.store.write(
        _entry("lake", gloss="A large inland body of standing water.", pos=PartOfSpeech.NOUN)
    )
    entry = _entry(
        "tarn",
        relations=[
            _relation(RelationType.SYNONYM, "lake", sense_id="lake:noun:0"),
            _relation(RelationType.HYPERNYM, "lake", sense_id="lake:noun:0"),
        ],
    )
    pairs = module._collect_pairs(entry, session.store, {})

    assert len(pairs) == 1
    assert pairs[0].target_gloss == "A large inland body of standing water."
    prompt = module._build_relation_choice_prompt("tarn", pairs)
    assert 'target="lake"' in prompt
    assert "A large inland body of standing water." in prompt


def test_an_unresolved_target_is_listed_as_unresolved(session):
    entry = _entry(
        "tarn",
        relations=[
            _relation(RelationType.SYNONYM, "lake"),
            _relation(RelationType.HYPERNYM, "lake"),
        ],
    )
    pairs = module._collect_pairs(entry, session.store, {})
    assert pairs[0].target_gloss == module.UNRESOLVED_GLOSS


async def test_an_entry_with_no_contradiction_costs_nothing(session):
    session.store.write(_entry("teach", relations=[_relation(RelationType.SYNONYM, "instruct")]))

    outcome = await run_content_hygiene(
        session.store, session.stages, workers=4, only={ContentHygieneStep.SYNONYM_HYPERNYM}
    )

    assert outcome.steps[ContentHygieneStep.SYNONYM_HYPERNYM].calls == 0
    assert session.meter.summary().total_usd == 0.0


# --------------------------------------------------------------------------------------
# Step 4 — garbage_examples
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("text", ["hypernyms([", "?", "duo", "...", "12 34"])
def test_garbage_example_texts_are_recognised(text):
    assert module._is_garbage_example(text)


@pytest.mark.parametrize(
    "text", ["Rain fell all day.", "They abseiled down the cliff.", "It is a big lake."]
)
def test_real_example_texts_are_not_garbage(text):
    assert not module._is_garbage_example(text)


async def test_a_garbage_example_is_removed_and_its_text_kept_in_a_note(session):
    entry = _entry(
        "duo",
        examples=["hypernyms([", "The duo played two songs at the fair."],
    )
    session.store.write(entry)

    outcome = await run_content_hygiene(
        session.store, session.stages, workers=4, only={ContentHygieneStep.GARBAGE_EXAMPLES}
    )

    result = outcome.steps[ContentHygieneStep.GARBAGE_EXAMPLES]
    assert result.removed == 1
    assert result.entries_changed == 1
    assert result.calls == 0
    assert result.cost_usd == 0.0

    stored = session.store.read("duo")
    texts = [r.content.text for r in stored.pos_entries[0].senses[0].examples]
    assert texts == ["The duo played two songs at the fair."]
    assert f"{GARBAGE_EXAMPLE_NOTE}hypernyms([" in _notes(stored)


async def test_removing_the_only_example_leaves_the_sense_without_one(session):
    # The repair pass regenerates it; this step deliberately does not.
    session.store.write(_entry("duo", examples=["?"]))

    outcome = await run_content_hygiene(
        session.store, session.stages, workers=4, only={ContentHygieneStep.GARBAGE_EXAMPLES}
    )

    assert outcome.steps[ContentHygieneStep.GARBAGE_EXAMPLES].removed == 1
    stored = session.store.read("duo")
    assert len(stored.pos_entries[0].senses[0].examples) == 0


# --------------------------------------------------------------------------------------
# Step 5 — stilted_examples
# --------------------------------------------------------------------------------------


async def test_a_stilted_canonical_example_is_rewritten(session):
    entry = _entry("duo", examples=["Two researchers formed a duo to complete the project."])
    session.store.write(entry)

    outcome = await run_content_hygiene(
        session.store, session.stages, workers=4, only={ContentHygieneStep.STILTED_EXAMPLES}
    )

    result = outcome.steps[ContentHygieneStep.STILTED_EXAMPLES]
    assert result.calls == 1
    assert result.accepted == 1
    assert result.rejected == 0
    assert result.rewritten == 1
    assert result.cost_usd > 0.0

    stored = session.store.read("duo")
    rendition = stored.pos_entries[0].senses[0].examples[0]
    assert "researchers" not in rendition.content.text
    assert rendition.content.span is not None
    assert "duo" in (rendition.content.matched or "").lower()
    assert rendition.assessment is not None
    assert rendition.assessment.readability_grade is not None
    assert "Two researchers formed a duo to complete the project." in _notes(stored)


async def test_a_rewrite_that_loses_the_headword_is_refused(session):
    entry = _entry(
        NO_SPAN_HEADWORD, examples=["The participants in this study reported the effect."]
    )
    session.store.write(entry)

    outcome = await run_content_hygiene(
        session.store, session.stages, workers=4, only={ContentHygieneStep.STILTED_EXAMPLES}
    )

    result = outcome.steps[ContentHygieneStep.STILTED_EXAMPLES]
    assert result.calls == 1
    assert result.accepted == 0
    assert result.rejected == 1
    assert result.rewritten == 0

    stored = session.store.read(NO_SPAN_HEADWORD)
    assert (
        stored.pos_entries[0].senses[0].examples[0].content.text
        == "The participants in this study reported the effect."
    )


async def test_a_non_canonical_rendition_using_the_same_words_is_left_alone(session):
    sense = Sense(
        index=0,
        gloss=Renditions[str](root=[canonical_rendition(DEFAULT_GLOSS)]),
        examples=Renditions[Example](
            root=[
                canonical_rendition(Example(text="The duo played two songs at the fair.")),
                Rendition[Example](
                    reading_level=ReadingLevel.COLLEGE,
                    style=Register.TECHNICAL,
                    content=Example(text="Observers recorded the duo across the whole data set."),
                ),
            ]
        ),
    )
    entry = Lexeme.empty(
        "duo",
        kind=LexemeKind.SIMPLEX,
        pos_entries=[POSEntry(pos=PartOfSpeech.NOUN, senses=[sense], morphology=Morphology())],
    )
    session.store.write(entry)

    outcome = await run_content_hygiene(
        session.store, session.stages, workers=4, only={ContentHygieneStep.STILTED_EXAMPLES}
    )

    assert outcome.steps[ContentHygieneStep.STILTED_EXAMPLES].calls == 0
    assert session.meter.summary().total_usd == 0.0
    stored = session.store.read("duo")
    assert (
        stored.pos_entries[0].senses[0].examples[1].content.text
        == "Observers recorded the duo across the whole data set."
    )


# --------------------------------------------------------------------------------------
# Step 6 — fragment_examples
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "the mile-long bridge opened to traffic",
        "a mile-high peak towers above the valley",
        "household energy consumption patterns were analyzed",
        "The bridge opened to traffic",  # capitalised but still no terminal punctuation
    ],
)
def test_fragment_example_texts_are_recognised(text):
    assert module._is_fragment_example(text)


@pytest.mark.parametrize(
    "text",
    [
        "The mile-long bridge opened to traffic.",
        "A mile-high peak towers above the valley!",
        '"Go!" she said.',
    ],
)
def test_complete_sentences_are_not_fragments(text):
    assert not module._is_fragment_example(text)


async def test_a_fragment_canonical_example_is_rewritten(session):
    entry = _entry("duo", examples=["the duo split the prize money"])
    session.store.write(entry)

    outcome = await run_content_hygiene(
        session.store, session.stages, workers=4, only={ContentHygieneStep.FRAGMENT_EXAMPLES}
    )

    result = outcome.steps[ContentHygieneStep.FRAGMENT_EXAMPLES]
    assert result.calls == 1
    assert result.accepted == 1
    assert result.rejected == 0
    assert result.rewritten == 1
    assert result.cost_usd > 0.0

    stored = session.store.read("duo")
    rendition = stored.pos_entries[0].senses[0].examples[0]
    text = rendition.content.text
    assert text[0].isupper()
    assert text[-1] in module._FRAGMENT_TERMINAL_CHARS
    assert rendition.content.span is not None
    assert "duo" in (rendition.content.matched or "").lower()
    assert rendition.assessment is not None
    assert rendition.assessment.readability_grade is not None
    assert f"{FRAGMENT_EXAMPLE_NOTE}the duo split the prize money" in _notes(stored)


async def test_a_rewrite_that_is_still_a_fragment_is_refused(session):
    entry = _entry(FRAGMENT_STILL_HEADWORD, examples=["the fragment stayed broken"])
    session.store.write(entry)

    outcome = await run_content_hygiene(
        session.store, session.stages, workers=4, only={ContentHygieneStep.FRAGMENT_EXAMPLES}
    )

    result = outcome.steps[ContentHygieneStep.FRAGMENT_EXAMPLES]
    assert result.calls == 1
    assert result.accepted == 0
    assert result.rejected == 1
    assert result.rewritten == 0

    stored = session.store.read(FRAGMENT_STILL_HEADWORD)
    assert stored.pos_entries[0].senses[0].examples[0].content.text == "the fragment stayed broken"


async def test_a_fragment_rewrite_that_loses_the_headword_is_refused(session):
    entry = _entry(NO_SPAN_HEADWORD, examples=["the missing word never shows up here"])
    session.store.write(entry)

    outcome = await run_content_hygiene(
        session.store, session.stages, workers=4, only={ContentHygieneStep.FRAGMENT_EXAMPLES}
    )

    result = outcome.steps[ContentHygieneStep.FRAGMENT_EXAMPLES]
    assert result.calls == 1
    assert result.accepted == 0
    assert result.rejected == 1
    assert result.rewritten == 0

    stored = session.store.read(NO_SPAN_HEADWORD)
    assert (
        stored.pos_entries[0].senses[0].examples[0].content.text
        == "the missing word never shows up here"
    )


async def test_a_complete_canonical_example_costs_nothing(session):
    session.store.write(_entry("duo", examples=["The duo played two songs at the fair."]))

    outcome = await run_content_hygiene(
        session.store, session.stages, workers=4, only={ContentHygieneStep.FRAGMENT_EXAMPLES}
    )

    assert outcome.steps[ContentHygieneStep.FRAGMENT_EXAMPLES].calls == 0
    assert session.meter.summary().total_usd == 0.0


async def test_a_second_fragment_sweep_is_free_and_changes_nothing(session):
    session.store.write(_entry("duo", examples=["the duo split the prize money"]))

    await run_content_hygiene(
        session.store, session.stages, workers=4, only={ContentHygieneStep.FRAGMENT_EXAMPLES}
    )
    spent = session.meter.summary().total_usd

    again = await run_content_hygiene(
        session.store, session.stages, workers=4, only={ContentHygieneStep.FRAGMENT_EXAMPLES}
    )

    assert again.steps[ContentHygieneStep.FRAGMENT_EXAMPLES].calls == 0
    assert session.meter.summary().total_usd == spent


async def test_only_fragment_examples_runs_alone(session):
    session.store.write(
        _entry(
            "duo",
            relations=[_relation(RelationType.SYNONYM, "duo")],
            examples=["the duo split the prize money"],
        )
    )

    outcome = await run_content_hygiene(
        session.store, session.stages, workers=4, only={"fragment_examples"}
    )

    assert set(outcome.steps) == {"fragment_examples"}
    assert outcome.steps[ContentHygieneStep.FRAGMENT_EXAMPLES].accepted == 1
    # self_synonym was not selected, so its own defect on the same entry survives.
    assert _relations_of(session.store.read("duo"))[0].type is RelationType.SYNONYM


# --------------------------------------------------------------------------------------
# Step 7 — degenerate_renditions
# --------------------------------------------------------------------------------------


def _leveled(level: ReadingLevel, style: Register, text: str) -> Rendition[str]:
    """Build one non-canonical gloss rendition."""
    return Rendition[str](reading_level=level, style=style, content=text)


async def test_two_siblings_with_one_text_leave_the_first_alone(session):
    duplicate = "The tired feeling that comes after a long day of work."
    entry = _entry(
        "fatigue",
        extra_glosses=[
            _leveled(ReadingLevel.NEUTRAL, Register.FORMAL, duplicate),
            _leveled(ReadingLevel.NEUTRAL, Register.INFORMAL, duplicate),
        ],
    )
    session.store.write(entry)

    outcome = await run_content_hygiene(
        session.store, session.stages, workers=4, only={ContentHygieneStep.DEGENERATE_RENDITIONS}
    )

    result = outcome.steps[ContentHygieneStep.DEGENERATE_RENDITIONS]
    assert result.calls == 1
    assert result.accepted == 1
    assert result.rewritten == 1

    texts = _gloss_texts(session.store.read("fatigue"))
    assert texts[1] == duplicate  # the first of the pair survives untouched
    assert texts[2] != duplicate
    assert "neutral/informal" in texts[2]
    assert duplicate in _notes(session.store.read("fatigue"))


async def test_a_rendition_copying_the_canonical_is_rewritten(session):
    entry = _entry(
        "fatigue",
        gloss=DEFAULT_GLOSS,
        extra_glosses=[_leveled(ReadingLevel.GRADE_5, Register.PLAIN, DEFAULT_GLOSS)],
    )
    session.store.write(entry)

    outcome = await run_content_hygiene(
        session.store, session.stages, workers=4, only={ContentHygieneStep.DEGENERATE_RENDITIONS}
    )

    assert outcome.steps[ContentHygieneStep.DEGENERATE_RENDITIONS].accepted == 1
    texts = _gloss_texts(session.store.read("fatigue"))
    assert texts[0] == DEFAULT_GLOSS
    assert texts[1] != DEFAULT_GLOSS


async def test_a_headword_initial_rewrite_is_refused(session):
    entry = _entry(
        DEGENERATE_INITIAL_HEADWORD,
        extra_glosses=[_leveled(ReadingLevel.GRADE_5, Register.PLAIN, DEFAULT_GLOSS)],
    )
    session.store.write(entry)

    outcome = await run_content_hygiene(
        session.store, session.stages, workers=4, only={ContentHygieneStep.DEGENERATE_RENDITIONS}
    )

    result = outcome.steps[ContentHygieneStep.DEGENERATE_RENDITIONS]
    assert result.calls == 1
    assert result.accepted == 0
    assert result.rejected == 1
    assert _gloss_texts(session.store.read(DEGENERATE_INITIAL_HEADWORD))[1] == DEFAULT_GLOSS


async def test_a_rewrite_that_is_still_the_canonical_is_refused(session):
    entry = _entry(
        DEGENERATE_ECHO_HEADWORD,
        extra_glosses=[_leveled(ReadingLevel.GRADE_5, Register.PLAIN, DEFAULT_GLOSS)],
    )
    session.store.write(entry)

    outcome = await run_content_hygiene(
        session.store, session.stages, workers=4, only={ContentHygieneStep.DEGENERATE_RENDITIONS}
    )

    result = outcome.steps[ContentHygieneStep.DEGENERATE_RENDITIONS]
    assert result.calls == 1
    assert result.accepted == 0
    assert result.rejected == 1
    assert _gloss_texts(session.store.read(DEGENERATE_ECHO_HEADWORD))[1] == DEFAULT_GLOSS


async def test_a_distinct_rendition_set_costs_nothing(session):
    entry = _entry(
        "fatigue",
        extra_glosses=[
            _leveled(ReadingLevel.GRADE_1, Register.PLAIN, "Being very tired after doing a lot."),
            _leveled(ReadingLevel.COLLEGE, Register.FORMAL, "Cumulative physical depletion."),
        ],
    )
    session.store.write(entry)

    outcome = await run_content_hygiene(
        session.store, session.stages, workers=4, only={ContentHygieneStep.DEGENERATE_RENDITIONS}
    )

    assert outcome.steps[ContentHygieneStep.DEGENERATE_RENDITIONS].calls == 0
    assert session.meter.summary().total_usd == 0.0


# --------------------------------------------------------------------------------------
# Idempotence, selection, and the outcome shape
# --------------------------------------------------------------------------------------


def _defective_entry() -> Lexeme:
    """Build one entry carrying a defect for every step at once."""
    return _entry(
        "duo",
        relations=[
            _relation(RelationType.SYNONYM, "duo"),
            _relation(RelationType.SYNONYM, "pair"),
            _relation(RelationType.ANTONYM, "pair"),
            _relation(RelationType.SYNONYM, "title"),
            _relation(RelationType.HYPERNYM, "title"),
        ],
        examples=["?", "Two researchers formed a duo to complete the project."],
        extra_glosses=[_leveled(ReadingLevel.GRADE_5, Register.PLAIN, DEFAULT_GLOSS)],
    )


async def test_a_full_sweep_fixes_every_defect_once(session):
    session.store.write(_defective_entry())

    outcome = await run_content_hygiene(session.store, session.stages, workers=4)

    assert set(outcome.steps) == set(ContentHygieneStep.ALL)
    assert outcome.entries_changed == 1
    assert outcome.calls == 3  # one per model step
    assert outcome.cost_usd > 0.0
    assert outcome.stopped_reason is None
    assert outcome.changed

    stored = session.store.read("duo")
    types = {(r.target.term, r.type) for r in _relations_of(stored)}
    assert ("duo", RelationType.SEE_ALSO) in types
    assert ("pair", RelationType.SEE_ALSO) in types
    assert ("pair", RelationType.SYNONYM) in types
    assert ("title", RelationType.HYPERNYM) in types
    examples = [r.content.text for r in stored.pos_entries[0].senses[0].examples]
    assert len(examples) == 1
    assert "researchers" not in examples[0]
    assert _gloss_texts(stored)[1] != DEFAULT_GLOSS


async def test_a_second_sweep_is_free_and_changes_nothing(session):
    session.store.write(_defective_entry())

    await run_content_hygiene(session.store, session.stages, workers=4)
    spent = session.meter.summary().total_usd
    before = session.store.read("duo").model_dump(mode="json")
    before.pop("updated_at")

    again = await run_content_hygiene(session.store, session.stages, workers=4)

    assert again.calls == 0
    assert again.cost_usd == 0.0
    assert again.entries_changed == 0
    assert not again.changed
    assert session.meter.summary().total_usd == spent

    after = session.store.read("duo").model_dump(mode="json")
    after.pop("updated_at")
    assert after == before


async def test_only_runs_just_the_named_steps(session):
    session.store.write(_defective_entry())

    outcome = await run_content_hygiene(
        session.store,
        session.stages,
        workers=4,
        only={ContentHygieneStep.SELF_SYNONYM, ContentHygieneStep.GARBAGE_EXAMPLES},
    )

    assert set(outcome.steps) == {
        ContentHygieneStep.SELF_SYNONYM,
        ContentHygieneStep.GARBAGE_EXAMPLES,
    }
    assert outcome.calls == 0
    assert outcome.cost_usd == 0.0

    stored = session.store.read("duo")
    # The steps that were not selected left their own defects in place.
    types = {(r.target.term, r.type) for r in _relations_of(stored)}
    assert ("pair", RelationType.ANTONYM) in types
    assert ("title", RelationType.HYPERNYM) in types
    assert _gloss_texts(stored)[1] == DEFAULT_GLOSS


async def test_lexeme_ids_limits_the_sweep(session):
    session.store.write(_entry("teach", relations=[_relation(RelationType.SYNONYM, "teach")]))
    session.store.write(_entry("learn", relations=[_relation(RelationType.SYNONYM, "learn")]))

    outcome = await run_content_hygiene(
        session.store,
        session.stages,
        workers=4,
        only={ContentHygieneStep.SELF_SYNONYM},
        lexeme_ids=["teach"],
    )

    assert outcome.steps[ContentHygieneStep.SELF_SYNONYM].entries_scanned == 1
    assert _relations_of(session.store.read("teach"))[0].type is RelationType.SEE_ALSO
    assert _relations_of(session.store.read("learn"))[0].type is RelationType.SYNONYM


async def test_an_unknown_step_name_is_rejected(session):
    with pytest.raises(ValueError, match="unknown content hygiene step"):
        await run_content_hygiene(session.store, session.stages, workers=4, only={"not_a_step"})


async def test_as_dict_reports_every_step(session):
    session.store.write(_defective_entry())

    outcome = await run_content_hygiene(session.store, session.stages, workers=4)
    payload = outcome.as_dict()

    assert payload["entries_changed"] == 1
    assert payload["stopped_reason"] is None
    steps = payload["steps"]
    assert set(steps) == set(ContentHygieneStep.ALL)
    assert steps[ContentHygieneStep.SELF_SYNONYM]["demoted"] == 1
    assert steps[ContentHygieneStep.GARBAGE_EXAMPLES]["removed"] == 1
    assert steps[ContentHygieneStep.STILTED_EXAMPLES]["accepted"] == 1
    assert steps[ContentHygieneStep.SYNONYM_HYPERNYM]["calls"] == 1


# --------------------------------------------------------------------------------------
# The offending-set sentinel (D-47's shape)
# --------------------------------------------------------------------------------------


async def test_a_new_offender_earns_one_more_attempt(session):
    session.store.write(
        _entry("fatigue", extra_glosses=[_leveled(ReadingLevel.GRADE_5, Register.PLAIN, "x y z.")])
    )
    entry = session.store.read("fatigue")
    entry.pos_entries[0].senses[0].gloss[1].content = DEFAULT_GLOSS
    session.store.write(entry)

    first = await run_content_hygiene(
        session.store, session.stages, workers=4, only={ContentHygieneStep.DEGENERATE_RENDITIONS}
    )
    assert first.calls == 1

    # A second offender appears, so the set hashes differently and one more attempt is due.
    entry = session.store.read("fatigue")
    entry.pos_entries[0].senses[0].gloss.add(
        _leveled(
            ReadingLevel.GRADE_10, Register.PLAIN, entry.pos_entries[0].senses[0].gloss[0].content
        )
    )
    session.store.write(entry)

    second = await run_content_hygiene(
        session.store, session.stages, workers=4, only={ContentHygieneStep.DEGENERATE_RENDITIONS}
    )
    assert second.calls == 1


async def test_the_attempt_bound_stops_at_two(session):
    session.store.write(
        _entry(
            DEGENERATE_ECHO_HEADWORD,
            extra_glosses=[_leveled(ReadingLevel.GRADE_5, Register.PLAIN, DEFAULT_GLOSS)],
        )
    )
    only = {ContentHygieneStep.DEGENERATE_RENDITIONS}

    # The scripted rewrite never fixes anything, so the offending set is unchanged and
    # the entry is not re-billed at all after its first attempt.
    assert (
        await run_content_hygiene(session.store, session.stages, workers=4, only=only)
    ).calls == 1
    assert (
        await run_content_hygiene(session.store, session.stages, workers=4, only=only)
    ).calls == 0


def test_a_pre_existing_marker_is_parsed():
    entry = _entry("teach")
    assert module._attempt_due(entry, module._STILTED_PREFIX, []) is None
    note = module._attempt_due(entry, module._STILTED_PREFIX, ["a", "b"])
    assert note is not None
    assert note.startswith(f"{module._STILTED_PREFIX}:")
    assert note.endswith(";attempts=1")


# --------------------------------------------------------------------------------------
# Step 8 — filler_examples
# --------------------------------------------------------------------------------------


def _flag_filler(rendition: Rendition[Example]) -> None:
    """Set ``OG_FILLER`` on one example rendition, as ``qc filler --flag`` would."""
    rendition.assessment = Assessment(qa_flags=[QAFlag.OG_FILLER])


async def test_a_filler_flagged_example_is_rewritten_and_the_flag_cleared(session):
    entry = _entry(
        "duo", examples=["During the festival the researchers observed the duo perform."]
    )
    _flag_filler(entry.pos_entries[0].senses[0].examples[0])
    session.store.write(entry)

    outcome = await run_content_hygiene(
        session.store, session.stages, workers=4, only={ContentHygieneStep.FILLER_EXAMPLES}
    )

    result = outcome.steps[ContentHygieneStep.FILLER_EXAMPLES]
    assert result.calls == 1
    assert result.accepted == 1
    assert result.rejected == 0
    assert result.rewritten == 1
    assert result.cost_usd > 0.0

    stored = session.store.read("duo")
    rendition = stored.pos_entries[0].senses[0].examples[0]
    assert rendition.content.text != "During the festival the researchers observed the duo perform."
    assert rendition.content.span is not None
    assert "duo" in (rendition.content.matched or "").lower()
    assert rendition.assessment is not None
    assert QAFlag.OG_FILLER not in rendition.assessment.qa_flags
    assert rendition.assessment.readability_grade is not None
    assert (
        f"{FILLER_EXAMPLE_NOTE}During the festival the researchers observed the duo perform."
        in _notes(stored)
    )


async def test_a_filler_rewrite_that_loses_the_headword_is_refused(session):
    entry = _entry(NO_SPAN_HEADWORD, examples=["Researchers observed the spanlessword closely."])
    _flag_filler(entry.pos_entries[0].senses[0].examples[0])
    session.store.write(entry)

    outcome = await run_content_hygiene(
        session.store, session.stages, workers=4, only={ContentHygieneStep.FILLER_EXAMPLES}
    )

    result = outcome.steps[ContentHygieneStep.FILLER_EXAMPLES]
    assert result.calls == 1
    assert result.accepted == 0
    assert result.rejected == 1
    assert result.rewritten == 0

    stored = session.store.read(NO_SPAN_HEADWORD)
    rendition = stored.pos_entries[0].senses[0].examples[0]
    assert rendition.content.text == "Researchers observed the spanlessword closely."
    assert rendition.assessment is not None
    assert QAFlag.OG_FILLER in rendition.assessment.qa_flags


async def test_a_filler_rewrite_that_collides_with_a_sibling_is_refused(session):
    # Both offenders are scripted the exact same replacement sentence (see
    # ``FILLER_REWRITE_TEMPLATE``): the first is adopted, and the second then collides
    # with what the first just wrote, at the shared (level, register) canonical key.
    sense = Sense(
        index=0,
        gloss=Renditions[str](root=[canonical_rendition(DEFAULT_GLOSS)]),
        examples=Renditions[Example](
            root=[
                canonical_rendition(
                    Example(text="During the festival the researchers observed the duo.")
                ),
                canonical_rendition(
                    Example(text="The data set showed the duo performing twice a week.")
                ),
            ]
        ),
    )
    entry = Lexeme.empty(
        "duo",
        kind=LexemeKind.SIMPLEX,
        pos_entries=[POSEntry(pos=PartOfSpeech.NOUN, senses=[sense], morphology=Morphology())],
    )
    for rendition in sense.examples:
        _flag_filler(rendition)
    session.store.write(entry)

    outcome = await run_content_hygiene(
        session.store, session.stages, workers=4, only={ContentHygieneStep.FILLER_EXAMPLES}
    )

    result = outcome.steps[ContentHygieneStep.FILLER_EXAMPLES]
    assert result.calls == 1
    assert result.accepted == 1
    assert result.rejected == 1
    assert result.rewritten == 1

    stored = session.store.read("duo")
    texts = [rendition.content.text for rendition in stored.pos_entries[0].senses[0].examples]
    # The first offender was rewritten (to the scripted template, shared by both since
    # they carry the same headword); the second collided with what the first just wrote
    # and kept its own original text instead.
    assert "During the festival the researchers observed the duo." not in texts
    assert "The data set showed the duo performing twice a week." in texts
    flags = [
        QAFlag.OG_FILLER in (rendition.assessment.qa_flags if rendition.assessment else [])
        for rendition in stored.pos_entries[0].senses[0].examples
    ]
    assert flags.count(True) == 1
    assert flags.count(False) == 1


async def test_filler_examples_covers_non_canonical_renditions_too(session):
    sense = Sense(
        index=0,
        gloss=Renditions[str](root=[canonical_rendition(DEFAULT_GLOSS)]),
        examples=Renditions[Example](
            root=[
                Rendition[Example](
                    reading_level=ReadingLevel.COLLEGE,
                    style=Register.FORMAL,
                    content=Example(text="The committee noted that the duo performed well."),
                )
            ]
        ),
    )
    entry = Lexeme.empty(
        "duo",
        kind=LexemeKind.SIMPLEX,
        pos_entries=[POSEntry(pos=PartOfSpeech.NOUN, senses=[sense], morphology=Morphology())],
    )
    _flag_filler(sense.examples[0])
    session.store.write(entry)

    outcome = await run_content_hygiene(
        session.store, session.stages, workers=4, only={ContentHygieneStep.FILLER_EXAMPLES}
    )

    result = outcome.steps[ContentHygieneStep.FILLER_EXAMPLES]
    assert result.accepted == 1

    stored = session.store.read("duo")
    rendition = stored.pos_entries[0].senses[0].examples[0]
    assert rendition.reading_level is ReadingLevel.COLLEGE
    assert rendition.style is Register.FORMAL
    assert rendition.assessment is not None
    assert QAFlag.OG_FILLER not in rendition.assessment.qa_flags


async def test_an_unflagged_example_costs_nothing(session):
    session.store.write(_entry("duo", examples=["The duo played two songs at the fair."]))

    outcome = await run_content_hygiene(
        session.store, session.stages, workers=4, only={ContentHygieneStep.FILLER_EXAMPLES}
    )

    assert outcome.steps[ContentHygieneStep.FILLER_EXAMPLES].calls == 0
    assert session.meter.summary().total_usd == 0.0


async def test_a_second_filler_sweep_is_free_once_resolved(session):
    entry = _entry(
        "duo", examples=["During the festival the researchers observed the duo perform."]
    )
    _flag_filler(entry.pos_entries[0].senses[0].examples[0])
    session.store.write(entry)
    only = {ContentHygieneStep.FILLER_EXAMPLES}

    first = await run_content_hygiene(session.store, session.stages, workers=4, only=only)
    assert first.steps[ContentHygieneStep.FILLER_EXAMPLES].calls == 1
    spent = session.meter.summary().total_usd

    second = await run_content_hygiene(session.store, session.stages, workers=4, only=only)
    assert second.steps[ContentHygieneStep.FILLER_EXAMPLES].calls == 0
    assert session.meter.summary().total_usd == spent
