"""Sense hygiene: phantom parts of speech, near-duplicate senses, misfiled examples.

Companion to ``test_content_hygiene.py`` and ``test_relation_hygiene.py``. Those cover defects a
rule can see and edges that point at the wrong thing; everything here is about the entry's own
inventory — a part of speech the headword never had, two senses that are one meaning written
twice, and an example that illustrates a sense other than the one it sits under. All three are
questions only a model can answer, so every test drives the scripted model through the markers
``tests/conftest.py`` registers for this pass.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from opengloss_generator import cli
from opengloss_generator.config import StoreConfig
from opengloss_generator.runner import RunSession
from opengloss_generator.schema import (
    Example,
    Lexeme,
    LexemeKind,
    Morphology,
    PartOfSpeech,
    POSEntry,
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
from opengloss_generator.store import LexemeStore
from opengloss_generator.workflows import sense_hygiene as module
from opengloss_generator.workflows.sense_hygiene import (
    MAX_CANONICAL_EXAMPLES,
    MOVED_OUT_NOTE,
    PHANTOM_RELATION_NOTE,
    REMOVED_EXAMPLE_NOTE,
    RETIRED_PHANTOM_NOTE,
    RETIRED_SENSE_NOTE,
    SenseHygieneStep,
    run_sense_hygiene,
)
from tests.conftest import (
    PHANTOM_COMPONENT_MARKER,
    PHANTOM_DUPLICATE_MARKER,
    SENSE_DUPLICATE_MARKER,
    SENSE_FIT_NONE_MARKER,
)

DISTINCT_GLOSS = "A distinct definition number {index} written for the pass under test."
DUPLICATE_GLOSS = f"A {SENSE_DUPLICATE_MARKER} definition number {{index}} of one meaning."


def _example(text: str, *, level: ReadingLevel | None = None) -> Rendition[Example]:
    """Build one example rendition, canonical unless a reading level is named."""
    if level is None:
        return canonical_rendition(Example(text=text))
    return Rendition[Example](reading_level=level, style=Register.PLAIN, content=Example(text=text))


def _sense(
    index: int,
    gloss: str,
    *,
    examples: list[Rendition[Example]] | None = None,
    relations: list[Relation] | None = None,
    gloss_renditions: list[Rendition[str]] | None = None,
) -> Sense:
    """Build one sense with whatever the test under way needs on it."""
    gloss_set = Renditions[str](root=[canonical_rendition(gloss)])
    for rendition in gloss_renditions or []:
        gloss_set.add(rendition)
    return Sense(
        index=index,
        gloss=gloss_set,
        examples=Renditions[Example](root=list(examples or [])),
        relations=relations or [],
    )


def _entry(
    headword: str,
    senses: list[Sense],
    *,
    pos: PartOfSpeech = PartOfSpeech.NOUN,
    extra: list[tuple[PartOfSpeech, list[Sense]]] | None = None,
    kind: LexemeKind = LexemeKind.SIMPLEX,
) -> Lexeme:
    """Build an entry holding one part-of-speech block, plus any extra blocks named."""
    entries = [POSEntry(pos=pos, senses=senses, morphology=Morphology())]
    for other_pos, other_senses in extra or []:
        entries.append(POSEntry(pos=other_pos, senses=other_senses, morphology=Morphology()))
    return Lexeme.empty(headword, kind=kind, pos_entries=entries)


def _relation(relation_type: RelationType, term: str) -> Relation:
    """Build one typed relation."""
    return Relation(type=relation_type, target=RelationTarget(term=term))


def _senses_of(entry: Lexeme, pos_index: int = 0) -> list[Sense]:
    """Return the senses of one part-of-speech block, retired ones included."""
    return entry.pos_entries[pos_index].senses


def _canonical_texts(sense: Sense) -> list[str]:
    """Return the sense's canonical example texts, in document order."""
    return [r.content.text for r in sense.examples if r.is_canonical]


def _notes(entry: Lexeme) -> list[str]:
    """Return every non-empty provenance note on an entry."""
    return [record.note for record in entry.provenance.values() if record.note]


def _duplicate_pair_entry(headword: str = "vow") -> Lexeme:
    """Build a two-sense noun entry the scripted judge groups, with content only on sense 1."""
    return _entry(
        headword,
        [
            _sense(0, DUPLICATE_GLOSS.format(index=0), examples=[_example("He kept his vow.")]),
            _sense(
                1,
                DUPLICATE_GLOSS.format(index=1),
                examples=[
                    _example("The monk took a vow."),
                    _example("A vow for a young reader.", level=ReadingLevel.GRADE_1),
                ],
                relations=[_relation(RelationType.SYNONYM, "pledge")],
                gloss_renditions=[
                    Rendition[str](
                        reading_level=ReadingLevel.GRADE_1,
                        style=Register.PLAIN,
                        content="A big promise you keep.",
                    )
                ],
            ),
        ],
    )


# --------------------------------------------------------------------------------------
# Step 1 — phantom_pos
# --------------------------------------------------------------------------------------

GENUINE_GLOSS = "An empty cell in a spreadsheet or table that currently holds no data."
COMPONENT_GLOSS = f"Not filled in or completed; a definition that {PHANTOM_COMPONENT_MARKER} word."
DUPLICATE_POS_GLOSS = (
    f"Relating to an empty cell in a table; a definition that {PHANTOM_DUPLICATE_MARKER}."
)


def _block(
    pos: PartOfSpeech, senses: list[Sense], *, morphology: Morphology | None = None
) -> POSEntry:
    """Build one part-of-speech block, with morphology where a test needs the free signal."""
    return POSEntry(pos=pos, senses=senses, morphology=morphology or Morphology())


def _multi_pos_entry(
    headword: str, blocks: list[POSEntry], *, kind: LexemeKind = LexemeKind.COMPOUND
) -> Lexeme:
    """Build an entry holding several part-of-speech blocks, compound unless told otherwise."""
    return Lexeme.empty(headword, kind=kind, pos_entries=blocks)


def _phantom_entry(headword: str = "blank cell") -> Lexeme:
    """Build the measured shape: a nominal compound carrying an adjective component block."""
    return _multi_pos_entry(
        headword,
        [
            _block(PartOfSpeech.NOUN, [_sense(0, GENUINE_GLOSS)]),
            _block(
                PartOfSpeech.ADJECTIVE,
                [
                    _sense(
                        0,
                        COMPONENT_GLOSS,
                        relations=[
                            _relation(RelationType.HYPERNYM, "state of emptiness"),
                            _relation(RelationType.SEE_ALSO, "already weak"),
                        ],
                    ),
                    _sense(
                        1, f"Lacking expression; another gloss that {PHANTOM_COMPONENT_MARKER}."
                    ),
                ],
                morphology=Morphology(comparative="blanker", superlative="blankest"),
            ),
        ],
    )


async def test_a_phantom_component_part_of_speech_is_retired_whole(session):
    session.store.write(_phantom_entry())

    outcome = await run_sense_hygiene(
        session.store, session.stages, workers=4, only={SenseHygieneStep.PHANTOM_POS}
    )

    result = outcome.steps[SenseHygieneStep.PHANTOM_POS]
    assert result.calls == 1
    assert result.pos_entries_judged == 2
    assert result.pos_entries_retired == 1
    assert result.retired_component == 1
    assert result.retired_duplicate == 0
    assert result.senses_retired == 2
    assert result.skipped_last_pos == 0
    assert result.entries_changed == 1

    stored = session.store.read("blank_cell")
    assert [sense.retired for sense in _senses_of(stored)] == [False]
    assert [sense.retired for sense in _senses_of(stored, 1)] == [True, True]
    # Nothing is deleted: the retired block keeps every definition it had.
    assert _senses_of(stored, 1)[0].canonical_gloss() == COMPONENT_GLOSS


async def test_a_phantom_component_retirement_notes_its_reason_per_sense(session):
    session.store.write(_phantom_entry())

    await run_sense_hygiene(
        session.store, session.stages, workers=4, only={SenseHygieneStep.PHANTOM_POS}
    )

    notes = _notes(session.store.read("blank_cell"))
    for index in (0, 1):
        expected = RETIRED_PHANTOM_NOTE.format(
            retired=f"blank_cell:adjective:{index}", reason="phantom_component"
        )
        assert expected in notes


async def test_a_phantom_duplicate_part_of_speech_is_retired_under_its_own_reason(session):
    session.store.write(
        _multi_pos_entry(
            "field trip",
            [
                _block(PartOfSpeech.NOUN, [_sense(0, GENUINE_GLOSS)]),
                _block(PartOfSpeech.ADJECTIVE, [_sense(0, DUPLICATE_POS_GLOSS)]),
            ],
        )
    )

    outcome = await run_sense_hygiene(
        session.store, session.stages, workers=4, only={SenseHygieneStep.PHANTOM_POS}
    )

    result = outcome.steps[SenseHygieneStep.PHANTOM_POS]
    assert result.retired_duplicate == 1
    assert result.retired_component == 0
    assert result.senses_retired == 1
    stored = session.store.read("field_trip")
    assert [sense.retired for sense in _senses_of(stored, 1)] == [True]
    assert RETIRED_PHANTOM_NOTE.format(
        retired="field_trip:adjective:0", reason="phantom_duplicate"
    ) in _notes(stored)


async def test_relations_on_a_retired_phantom_sense_are_demoted_not_deleted(session):
    session.store.write(_phantom_entry())

    outcome = await run_sense_hygiene(
        session.store, session.stages, workers=4, only={SenseHygieneStep.PHANTOM_POS}
    )

    # One demotion, not two: a relation that was already a ``see_also`` has nowhere weaker to go.
    assert outcome.steps[SenseHygieneStep.PHANTOM_POS].relations_demoted == 1
    retired = _senses_of(session.store.read("blank_cell"), 1)[0]
    assert [relation.type for relation in retired.relations] == [
        RelationType.SEE_ALSO,
        RelationType.SEE_ALSO,
    ]
    demoted, untouched = retired.relations
    assert demoted.target.term == "state of emptiness"
    assert demoted.note is not None
    assert demoted.note.startswith(PHANTOM_RELATION_NOTE)
    assert untouched.note is None


async def test_the_last_live_part_of_speech_is_never_retired(session):
    session.store.write(
        _multi_pos_entry(
            "blank cell",
            [
                _block(PartOfSpeech.NOUN, [_sense(0, f"A gloss that {PHANTOM_COMPONENT_MARKER}.")]),
                _block(PartOfSpeech.ADJECTIVE, [_sense(0, COMPONENT_GLOSS)]),
            ],
        )
    )

    outcome = await run_sense_hygiene(
        session.store, session.stages, workers=4, only={SenseHygieneStep.PHANTOM_POS}
    )

    result = outcome.steps[SenseHygieneStep.PHANTOM_POS]
    assert result.pos_entries_retired == 1
    assert result.skipped_last_pos == 1
    stored = session.store.read("blank_cell")
    # The kept block is the one whose part of speech is natural for a compound.
    assert [sense.retired for sense in _senses_of(stored)] == [False]
    assert [sense.retired for sense in _senses_of(stored, 1)] == [True]


async def test_a_genuine_inventory_is_left_alone(session):
    session.store.write(
        _multi_pos_entry(
            "part time",
            [
                _block(PartOfSpeech.ADJECTIVE, [_sense(0, "Done for fewer than the usual hours.")]),
                _block(PartOfSpeech.ADVERB, [_sense(0, "For fewer than the usual hours.")]),
            ],
        )
    )

    outcome = await run_sense_hygiene(
        session.store, session.stages, workers=4, only={SenseHygieneStep.PHANTOM_POS}
    )

    result = outcome.steps[SenseHygieneStep.PHANTOM_POS]
    assert result.calls == 1
    assert result.pos_entries_judged == 2
    assert result.pos_entries_retired == 0
    assert result.entries_changed == 0
    stored = session.store.read("part_time")
    assert [sense.retired for sense in _senses_of(stored)] == [False]
    assert [sense.retired for sense in _senses_of(stored, 1)] == [False]


async def test_a_simplex_with_one_part_of_speech_is_never_called_for(session):
    session.store.write(_entry("abseil", [_sense(0, COMPONENT_GLOSS)], kind=LexemeKind.SIMPLEX))

    outcome = await run_sense_hygiene(
        session.store, session.stages, workers=4, only={SenseHygieneStep.PHANTOM_POS}
    )

    assert outcome.steps[SenseHygieneStep.PHANTOM_POS].calls == 0
    assert outcome.cost_usd == 0.0


async def test_a_compound_with_only_an_unnatural_part_of_speech_is_judged_but_kept(session):
    session.store.write(
        _multi_pos_entry(
            "blank cell", [_block(PartOfSpeech.ADJECTIVE, [_sense(0, COMPONENT_GLOSS)])]
        )
    )

    outcome = await run_sense_hygiene(
        session.store, session.stages, workers=4, only={SenseHygieneStep.PHANTOM_POS}
    )

    # The kind gate asks the question even with one block, and the guard refuses the answer.
    result = outcome.steps[SenseHygieneStep.PHANTOM_POS]
    assert result.calls == 1
    assert result.skipped_last_pos == 1
    assert result.pos_entries_retired == 0
    assert [sense.retired for sense in _senses_of(session.store.read("blank_cell"))] == [False]


async def test_phantom_pos_is_idempotent(session):
    session.store.write(_phantom_entry())
    only = {SenseHygieneStep.PHANTOM_POS}

    first = await run_sense_hygiene(session.store, session.stages, workers=4, only=only)
    assert first.calls == 1
    assert first.steps[SenseHygieneStep.PHANTOM_POS].pos_entries_retired == 1

    # One live block is left, and it is natural for a compound, so the entry no longer qualifies.
    second = await run_sense_hygiene(session.store, session.stages, workers=4, only=only)
    assert second.calls == 0
    assert second.cost_usd == 0.0
    assert second.steps[SenseHygieneStep.PHANTOM_POS].pos_entries_retired == 0


async def test_a_judged_genuine_entry_is_not_re_billed_until_a_gloss_changes(session):
    session.store.write(
        _multi_pos_entry(
            "part time",
            [
                _block(PartOfSpeech.ADJECTIVE, [_sense(0, "Done for fewer than the usual hours.")]),
                _block(PartOfSpeech.ADVERB, [_sense(0, "For fewer than the usual hours.")]),
            ],
        )
    )
    only = {SenseHygieneStep.PHANTOM_POS}

    assert (await run_sense_hygiene(session.store, session.stages, workers=4, only=only)).calls == 1
    assert (await run_sense_hygiene(session.store, session.stages, workers=4, only=only)).calls == 0

    # The marker is keyed on the glosses, not on the sense ids: a rewritten definition is a
    # different question about the same block and earns the second of D-47's two attempts.
    entry = session.store.read("part_time")
    entry.pos_entries[1].senses[0].gloss = Renditions[str](
        root=[canonical_rendition("A rewritten definition of the same block.")]
    )
    session.store.write(entry)

    assert (await run_sense_hygiene(session.store, session.stages, workers=4, only=only)).calls == 1
    assert (await run_sense_hygiene(session.store, session.stages, workers=4, only=only)).calls == 0


def test_the_free_signal_fires_on_a_component_definition():
    entry = _phantom_entry()
    noun, adjective = entry.pos_entries

    # The adjective block names neither "blank" nor "cell" and inflects "blank" alone.
    assert module._defines_a_component(entry, adjective) is True
    # The noun block's own definition names "cell", so it is talking about the compound.
    assert module._defines_a_component(entry, noun) is False


def test_the_free_signal_never_fires_on_a_single_word_headword():
    entry = _entry("communal", [_sense(0, COMPONENT_GLOSS)], pos=PartOfSpeech.ADJECTIVE)
    assert module._defines_a_component(entry, entry.pos_entries[0]) is False


# --------------------------------------------------------------------------------------
# Step 2 — distinctness
# --------------------------------------------------------------------------------------


async def test_a_duplicate_group_merges_onto_the_lowest_index_and_retires_the_rest(session):
    session.store.write(_duplicate_pair_entry())

    outcome = await run_sense_hygiene(
        session.store, session.stages, workers=4, only={SenseHygieneStep.DISTINCTNESS}
    )

    result = outcome.steps[SenseHygieneStep.DISTINCTNESS]
    assert result.groups_merged == 1
    assert result.senses_retired == 1
    assert result.calls == 1
    assert result.entries_changed == 1

    survivor, retired = _senses_of(session.store.read("vow"))
    assert survivor.index == 0
    assert survivor.retired is False
    assert retired.index == 1
    assert retired.retired is True
    # D-1: nothing is renumbered and nothing is deleted, so the retired sense keeps its own.
    assert _canonical_texts(retired) == ["The monk took a vow."]


async def test_a_merge_carries_examples_relations_and_renditions_to_the_survivor(session):
    session.store.write(_duplicate_pair_entry())

    await run_sense_hygiene(
        session.store, session.stages, workers=4, only={SenseHygieneStep.DISTINCTNESS}
    )

    survivor = _senses_of(session.store.read("vow"))[0]
    assert _canonical_texts(survivor) == ["He kept his vow.", "The monk took a vow."]
    assert survivor.examples.has(ReadingLevel.GRADE_1, Register.PLAIN)
    assert [r.target.term for r in survivor.relations] == ["pledge"]
    assert survivor.gloss.has(ReadingLevel.GRADE_1, Register.PLAIN)
    # The moved example's span is re-found against the text that is actually stored.
    moved = next(r for r in survivor.examples if r.content.text == "The monk took a vow.")
    assert moved.content.matched == "vow"


async def test_a_retirement_writes_a_note_naming_the_survivor(session):
    session.store.write(_duplicate_pair_entry())

    await run_sense_hygiene(
        session.store, session.stages, workers=4, only={SenseHygieneStep.DISTINCTNESS}
    )

    expected = RETIRED_SENSE_NOTE.format(retired="vow:noun:1", survivor="vow:noun:0")
    assert expected in _notes(session.store.read("vow"))


async def test_a_merge_does_not_duplicate_content_the_survivor_already_has(session):
    shared = "They shared one example."
    session.store.write(
        _entry(
            "vow",
            [
                _sense(
                    0,
                    DUPLICATE_GLOSS.format(index=0),
                    examples=[_example(shared)],
                    relations=[_relation(RelationType.SYNONYM, "pledge")],
                ),
                _sense(
                    1,
                    DUPLICATE_GLOSS.format(index=1),
                    examples=[_example(shared)],
                    relations=[_relation(RelationType.SYNONYM, "Pledge")],
                ),
            ],
        )
    )

    await run_sense_hygiene(
        session.store, session.stages, workers=4, only={SenseHygieneStep.DISTINCTNESS}
    )

    survivor = _senses_of(session.store.read("vow"))[0]
    assert _canonical_texts(survivor) == [shared]
    assert len(survivor.relations) == 1


async def test_a_distinct_pair_is_left_alone(session):
    session.store.write(
        _entry(
            "bank",
            [
                _sense(0, DISTINCT_GLOSS.format(index=0), examples=[_example("We sat on it.")]),
                _sense(1, DISTINCT_GLOSS.format(index=1), examples=[_example("It lent money.")]),
            ],
        )
    )

    outcome = await run_sense_hygiene(
        session.store, session.stages, workers=4, only={SenseHygieneStep.DISTINCTNESS}
    )

    result = outcome.steps[SenseHygieneStep.DISTINCTNESS]
    assert result.calls == 1
    assert result.groups_merged == 0
    assert result.entries_changed == 0
    assert [sense.retired for sense in _senses_of(session.store.read("bank"))] == [False, False]


async def test_a_group_spanning_two_parts_of_speech_is_refused(session):
    session.store.write(
        _entry(
            "vow",
            [
                _sense(0, DUPLICATE_GLOSS.format(index=0)),
                _sense(1, DUPLICATE_GLOSS.format(index=1)),
            ],
            extra=[(PartOfSpeech.VERB, [_sense(0, DUPLICATE_GLOSS.format(index=2))])],
        )
    )

    outcome = await run_sense_hygiene(
        session.store, session.stages, workers=4, only={SenseHygieneStep.DISTINCTNESS}
    )

    result = outcome.steps[SenseHygieneStep.DISTINCTNESS]
    assert result.groups_merged == 0
    assert result.rejected == 1
    stored = session.store.read("vow")
    assert [sense.retired for sense in _senses_of(stored)] == [False, False]
    assert [sense.retired for sense in _senses_of(stored, 1)] == [False]


async def test_a_single_sense_entry_is_never_called_for(session):
    session.store.write(_entry("abseil", [_sense(0, DUPLICATE_GLOSS.format(index=0))]))

    outcome = await run_sense_hygiene(session.store, session.stages, workers=4)

    assert outcome.calls == 0
    assert outcome.cost_usd == 0.0
    assert outcome.entries_changed == 0


async def test_one_sense_per_part_of_speech_is_never_called_for_by_distinctness(session):
    session.store.write(
        _entry(
            "vow",
            [_sense(0, DUPLICATE_GLOSS.format(index=0))],
            extra=[(PartOfSpeech.VERB, [_sense(0, DUPLICATE_GLOSS.format(index=1))])],
        )
    )

    outcome = await run_sense_hygiene(
        session.store, session.stages, workers=4, only={SenseHygieneStep.DISTINCTNESS}
    )

    assert outcome.steps[SenseHygieneStep.DISTINCTNESS].calls == 0


async def test_distinctness_is_idempotent(session):
    session.store.write(_duplicate_pair_entry())
    only = {SenseHygieneStep.DISTINCTNESS}

    first = await run_sense_hygiene(session.store, session.stages, workers=4, only=only)
    assert first.calls == 1
    assert first.steps[SenseHygieneStep.DISTINCTNESS].senses_retired == 1

    # One live sense is left under the only part of speech, so the entry no longer qualifies.
    second = await run_sense_hygiene(session.store, session.stages, workers=4, only=only)
    assert second.calls == 0
    assert second.cost_usd == 0.0
    assert second.steps[SenseHygieneStep.DISTINCTNESS].senses_retired == 0


async def test_a_judged_distinct_entry_is_not_re_billed(session):
    session.store.write(
        _entry(
            "bank",
            [
                _sense(0, DISTINCT_GLOSS.format(index=0)),
                _sense(1, DISTINCT_GLOSS.format(index=1)),
            ],
        )
    )
    only = {SenseHygieneStep.DISTINCTNESS}

    assert (await run_sense_hygiene(session.store, session.stages, workers=4, only=only)).calls == 1
    assert (await run_sense_hygiene(session.store, session.stages, workers=4, only=only)).calls == 0


# --------------------------------------------------------------------------------------
# Step 3 — example_fit
# --------------------------------------------------------------------------------------


def _misfiled_entry(headword: str = "vow") -> Lexeme:
    """Build a two-sense entry whose sense 2 holds an example belonging to sense 1."""
    return _entry(
        headword,
        [
            _sense(0, DISTINCT_GLOSS.format(index=0), examples=[_example("A plain vow.")]),
            _sense(
                1,
                DISTINCT_GLOSS.format(index=1),
                examples=[
                    _example("This vow belongs to sense 1."),
                    _example("A simple vow for young readers.", level=ReadingLevel.GRADE_1),
                ],
            ),
        ],
    )


async def test_an_example_is_moved_with_its_level_renditions(session):
    session.store.write(_misfiled_entry())

    outcome = await run_sense_hygiene(
        session.store, session.stages, workers=4, only={SenseHygieneStep.EXAMPLE_FIT}
    )

    result = outcome.steps[SenseHygieneStep.EXAMPLE_FIT]
    assert result.examples_moved == 1
    assert result.examples_removed == 0
    assert result.calls == 1

    first, second = _senses_of(session.store.read("vow"))
    assert _canonical_texts(first) == ["A plain vow.", "This vow belongs to sense 1."]
    assert first.examples.has(ReadingLevel.GRADE_1, Register.PLAIN)
    assert len(second.examples) == 0
    assert result.senses_emptied == 1


async def test_a_moved_example_has_its_span_re_found_on_the_destination(session):
    session.store.write(_misfiled_entry())

    await run_sense_hygiene(
        session.store, session.stages, workers=4, only={SenseHygieneStep.EXAMPLE_FIT}
    )

    survivor = _senses_of(session.store.read("vow"))[0]
    moved = next(r for r in survivor.examples if r.content.text == "This vow belongs to sense 1.")
    assert moved.content.matched == "vow"


async def test_an_example_that_fits_no_sense_is_removed_with_its_text_in_a_note(session):
    text = f"This one {SENSE_FIT_NONE_MARKER} at all."
    session.store.write(
        _entry(
            "vow",
            [
                _sense(0, DISTINCT_GLOSS.format(index=0), examples=[_example("A plain vow.")]),
                _sense(1, DISTINCT_GLOSS.format(index=1), examples=[_example(text)]),
            ],
        )
    )

    outcome = await run_sense_hygiene(
        session.store, session.stages, workers=4, only={SenseHygieneStep.EXAMPLE_FIT}
    )

    result = outcome.steps[SenseHygieneStep.EXAMPLE_FIT]
    assert result.examples_removed == 1
    assert result.examples_moved == 0

    stored = session.store.read("vow")
    assert _canonical_texts(_senses_of(stored)[1]) == []
    assert f"{REMOVED_EXAMPLE_NOTE}{text}" in _notes(stored)


async def test_an_example_is_dropped_into_a_note_when_the_destination_is_full(session):
    text = "This crowded vow belongs to sense 1."
    session.store.write(
        _entry(
            "vow",
            [
                _sense(
                    0,
                    DISTINCT_GLOSS.format(index=0),
                    examples=[_example(f"A plain vow number {i}.") for i in range(3)],
                ),
                _sense(1, DISTINCT_GLOSS.format(index=1), examples=[_example(text)]),
            ],
        )
    )
    assert MAX_CANONICAL_EXAMPLES == 3

    outcome = await run_sense_hygiene(
        session.store, session.stages, workers=4, only={SenseHygieneStep.EXAMPLE_FIT}
    )

    result = outcome.steps[SenseHygieneStep.EXAMPLE_FIT]
    assert result.examples_moved == 0
    assert result.examples_removed == 1

    stored = session.store.read("vow")
    assert len(_canonical_texts(_senses_of(stored)[0])) == MAX_CANONICAL_EXAMPLES
    assert _canonical_texts(_senses_of(stored)[1]) == []
    assert f"{MOVED_OUT_NOTE}{text}" in _notes(stored)


async def test_an_example_that_is_already_where_it_belongs_is_left_alone(session):
    session.store.write(
        _entry(
            "bank",
            [
                _sense(0, DISTINCT_GLOSS.format(index=0), examples=[_example("We sat on it.")]),
                _sense(1, DISTINCT_GLOSS.format(index=1), examples=[_example("It lent money.")]),
            ],
        )
    )

    outcome = await run_sense_hygiene(
        session.store, session.stages, workers=4, only={SenseHygieneStep.EXAMPLE_FIT}
    )

    result = outcome.steps[SenseHygieneStep.EXAMPLE_FIT]
    assert result.calls == 1
    assert result.examples_moved == 0
    assert result.examples_removed == 0
    assert result.entries_changed == 0
    assert result.senses_emptied == 0


async def test_example_fit_looks_across_parts_of_speech(session):
    session.store.write(
        _entry(
            "vow",
            [_sense(0, DISTINCT_GLOSS.format(index=0), examples=[_example("A plain vow.")])],
            extra=[
                (
                    PartOfSpeech.VERB,
                    [
                        _sense(
                            0,
                            DISTINCT_GLOSS.format(index=1),
                            examples=[_example("This vow belongs to sense 1.")],
                        )
                    ],
                )
            ],
        )
    )

    outcome = await run_sense_hygiene(
        session.store, session.stages, workers=4, only={SenseHygieneStep.EXAMPLE_FIT}
    )

    assert outcome.steps[SenseHygieneStep.EXAMPLE_FIT].examples_moved == 1
    stored = session.store.read("vow")
    assert len(_canonical_texts(_senses_of(stored)[0])) == 2
    assert _canonical_texts(_senses_of(stored, 1)[0]) == []


async def test_a_multi_sense_entry_with_no_examples_is_never_called_for(session):
    session.store.write(
        _entry(
            "bank",
            [
                _sense(0, DISTINCT_GLOSS.format(index=0)),
                _sense(1, DISTINCT_GLOSS.format(index=1)),
            ],
        )
    )

    outcome = await run_sense_hygiene(
        session.store, session.stages, workers=4, only={SenseHygieneStep.EXAMPLE_FIT}
    )

    assert outcome.steps[SenseHygieneStep.EXAMPLE_FIT].calls == 0


async def test_example_fit_is_idempotent(session):
    session.store.write(_misfiled_entry())
    only = {SenseHygieneStep.EXAMPLE_FIT}

    first = await run_sense_hygiene(session.store, session.stages, workers=4, only=only)
    assert first.calls == 1
    assert first.steps[SenseHygieneStep.EXAMPLE_FIT].examples_moved == 1

    # The example now sits where the judge put it, so a second sweep answers "keep" for it --
    # but the marker's digest is over the set the move left behind, so no call is made at all.
    second = await run_sense_hygiene(session.store, session.stages, workers=4, only=only)
    assert second.calls == 0
    assert second.steps[SenseHygieneStep.EXAMPLE_FIT].examples_moved == 0

    stored = session.store.read("vow")
    assert _canonical_texts(_senses_of(stored)[0]) == [
        "A plain vow.",
        "This vow belongs to sense 1.",
    ]


# --------------------------------------------------------------------------------------
# The run: selection, reporting, and the marker
# --------------------------------------------------------------------------------------


async def test_only_runs_just_the_named_step(session):
    session.store.write(_duplicate_pair_entry())

    outcome = await run_sense_hygiene(
        session.store, session.stages, workers=4, only={SenseHygieneStep.EXAMPLE_FIT}
    )

    assert set(outcome.steps) == {SenseHygieneStep.EXAMPLE_FIT}
    assert [sense.retired for sense in _senses_of(session.store.read("vow"))] == [False, False]


async def test_every_step_runs_by_default_in_order(session):
    session.store.write(_duplicate_pair_entry())

    outcome = await run_sense_hygiene(session.store, session.stages, workers=4)

    assert list(outcome.steps) == list(SenseHygieneStep.ALL)
    # A one-block simplex has a foregone phantom_pos answer, so that step never bills for it.
    assert outcome.steps[SenseHygieneStep.PHANTOM_POS].calls == 0
    # distinctness then left one live sense, so example_fit had nothing to ask about.
    assert outcome.steps[SenseHygieneStep.DISTINCTNESS].calls == 1
    assert outcome.steps[SenseHygieneStep.EXAMPLE_FIT].calls == 0


def _phantom_duplicate_gloss(opening: str) -> str:
    """Return a gloss both scripted judges react to: a duplicate sense inside a phantom block."""
    return f"{opening} {SENSE_DUPLICATE_MARKER} gloss that {PHANTOM_COMPONENT_MARKER}."


async def test_phantom_pos_runs_before_distinctness(session):
    """A phantom block's senses are retired before distinctness is asked to group them."""
    session.store.write(
        _multi_pos_entry(
            "blank cell",
            [
                _block(PartOfSpeech.NOUN, [_sense(0, GENUINE_GLOSS)]),
                _block(
                    PartOfSpeech.ADJECTIVE,
                    [
                        _sense(0, _phantom_duplicate_gloss("A")),
                        _sense(1, _phantom_duplicate_gloss("Another")),
                    ],
                ),
            ],
        )
    )

    outcome = await run_sense_hygiene(session.store, session.stages, workers=4)

    # Both adjective senses are gone as phantoms, so distinctness never saw a pair to merge.
    assert outcome.steps[SenseHygieneStep.PHANTOM_POS].senses_retired == 2
    assert outcome.steps[SenseHygieneStep.DISTINCTNESS].calls == 0
    assert outcome.steps[SenseHygieneStep.DISTINCTNESS].groups_merged == 0


async def test_an_unknown_step_name_is_rejected(session):
    with pytest.raises(ValueError, match="unknown sense hygiene step"):
        await run_sense_hygiene(session.store, session.stages, workers=4, only={"not_a_step"})


async def test_as_dict_reports_every_step(session):
    session.store.write(_misfiled_entry())

    outcome = await run_sense_hygiene(session.store, session.stages, workers=4)
    payload = outcome.as_dict()

    assert payload["entries_changed"] == 1
    assert payload["stopped_reason"] is None
    steps = payload["steps"]
    assert set(steps) == set(SenseHygieneStep.ALL)
    assert steps[SenseHygieneStep.EXAMPLE_FIT]["examples_moved"] == 1
    assert steps[SenseHygieneStep.EXAMPLE_FIT]["senses_emptied"] == 1
    assert steps[SenseHygieneStep.DISTINCTNESS]["groups_merged"] == 0
    assert payload["cost_usd"] > 0


async def test_lexeme_ids_limits_the_sweep(session):
    session.store.write(_duplicate_pair_entry("vow"))
    session.store.write(_duplicate_pair_entry("oath"))

    outcome = await run_sense_hygiene(
        session.store,
        session.stages,
        workers=4,
        only={SenseHygieneStep.DISTINCTNESS},
        lexeme_ids=["vow"],
    )

    assert outcome.steps[SenseHygieneStep.DISTINCTNESS].entries_scanned == 1
    assert [sense.retired for sense in _senses_of(session.store.read("oath"))] == [False, False]


async def test_a_new_sense_earns_one_more_attempt(session):
    session.store.write(
        _entry(
            "bank",
            [
                _sense(0, DISTINCT_GLOSS.format(index=0)),
                _sense(1, DISTINCT_GLOSS.format(index=1)),
            ],
        )
    )
    only = {SenseHygieneStep.DISTINCTNESS}

    assert (await run_sense_hygiene(session.store, session.stages, workers=4, only=only)).calls == 1

    entry = session.store.read("bank")
    entry.pos_entries[0].senses.append(_sense(2, DISTINCT_GLOSS.format(index=2)))
    session.store.write(entry)

    assert (await run_sense_hygiene(session.store, session.stages, workers=4, only=only)).calls == 1
    # The bound is two attempts per entry, so a third sense buys nothing further.
    entry = session.store.read("bank")
    entry.pos_entries[0].senses.append(_sense(3, DISTINCT_GLOSS.format(index=3)))
    session.store.write(entry)
    assert (await run_sense_hygiene(session.store, session.stages, workers=4, only=only)).calls == 0


def test_a_pre_existing_marker_is_parsed():
    entry = _entry("vow", [_sense(0, DISTINCT_GLOSS.format(index=0))])
    assert module._attempt_number(entry, module._DISTINCTNESS_PREFIX, []) is None
    assert module._attempt_number(entry, module._DISTINCTNESS_PREFIX, ["a", "b"]) == 1
    note = module._marker_note(module._DISTINCTNESS_PREFIX, ["a", "b"], 1)
    assert note.startswith(f"{module._DISTINCTNESS_PREFIX}:")
    assert note.endswith(";attempts=1")
    entry.add_provenance(module._rule_provenance(note))
    assert module._attempt_number(entry, module._DISTINCTNESS_PREFIX, ["a", "b"]) is None
    assert module._attempt_number(entry, module._DISTINCTNESS_PREFIX, ["a", "c"]) == 2


def test_canonical_groups_pair_a_rendition_with_its_own_canonical_by_position():
    sense = _sense(
        0,
        DISTINCT_GLOSS.format(index=0),
        examples=[
            _example("First canonical."),
            _example("Second canonical."),
            _example("First for a child.", level=ReadingLevel.GRADE_1),
            _example("Second for a child.", level=ReadingLevel.GRADE_5),
        ],
    )

    groups = module._canonical_groups(sense)

    assert [group[0].content.text for group in groups] == ["First canonical.", "Second canonical."]
    assert [r.content.text for r in groups[0]][1:] == ["First for a child.", "Second for a child."]
    assert len(groups[1]) == 1


# --------------------------------------------------------------------------------------
# The CLI
# --------------------------------------------------------------------------------------


@pytest.fixture
def _cli_offline(monkeypatch: pytest.MonkeyPatch, scripted_model, tmp_path: Path) -> None:
    """Route CLI-constructed sessions through the scripted model, isolating run output.

    The same shape ``test_cli.py``'s own fixture uses: ``AppConfig.log_dir`` defaults to
    ``runs`` relative to the process's cwd, so without the override every invocation would
    litter the repository's ``runs/`` with a ledger and a log.
    """
    monkeypatch.setenv("OPENGLOSS_LOG_DIR", str(tmp_path / "runs"))
    original = RunSession.__init__

    def patched(
        self, config, *, model_override=None, run_id=None, install_signal_handler=False
    ) -> None:
        original(
            self,
            config,
            model_override=scripted_model,
            run_id=run_id,
            install_signal_handler=False,
        )

    monkeypatch.setattr(RunSession, "__init__", patched)


def _cli(*args: str) -> dict:
    """Invoke the CLI and return its JSON summary; structured logs go to stderr."""
    result = CliRunner().invoke(cli.app, list(args))
    assert result.exit_code == 0, result.output
    return json.loads(result.stdout)


@pytest.mark.usefixtures("_cli_offline")
def test_the_cli_dry_run_spends_nothing(tmp_path: Path):
    summary = _cli("sense-hygiene", "--store", str(tmp_path / "store"), "--dry-run")

    assert summary["stop_reason"] == "dry_run"
    assert summary["cost_usd"] == 0.0


@pytest.mark.usefixtures("_cli_offline")
def test_the_cli_dry_run_over_phantom_pos_writes_nothing(tmp_path: Path):
    store = tmp_path / "store"
    LexemeStore(StoreConfig(root=store, fsync_on_write=False)).write(_phantom_entry())

    summary = _cli("sense-hygiene", "--only", "phantom_pos", "--store", str(store), "--dry-run")

    assert summary["stop_reason"] == "dry_run"
    assert summary["cost_usd"] == 0.0
    stored = LexemeStore(StoreConfig(root=store, fsync_on_write=False)).read("blank_cell")
    assert [sense.retired for sense in stored.pos_entries[1].senses] == [False, False]


@pytest.mark.usefixtures("_cli_offline")
def test_the_cli_runs_phantom_pos(tmp_path: Path):
    store = tmp_path / "store"
    LexemeStore(StoreConfig(root=store, fsync_on_write=False)).write(_phantom_entry())

    summary = _cli("sense-hygiene", "--only", "phantom_pos", "--store", str(store))

    assert set(summary["steps"]) == {"phantom_pos"}
    assert summary["steps"]["phantom_pos"]["pos_entries_retired"] == 1
    assert summary["steps"]["phantom_pos"]["retired_component"] == 1
    assert summary["cost_usd"] > 0


@pytest.mark.usefixtures("_cli_offline")
def test_the_cli_runs_one_named_step(tmp_path: Path):
    store = tmp_path / "store"
    LexemeStore(StoreConfig(root=store, fsync_on_write=False)).write(_duplicate_pair_entry())

    summary = _cli("sense-hygiene", "--only", "distinctness", "--store", str(store))

    assert set(summary["steps"]) == {"distinctness"}
    assert summary["steps"]["distinctness"]["senses_retired"] == 1
    assert summary["cost_usd"] > 0
