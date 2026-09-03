"""Verified, sense-disambiguated example sentences in volume (D-53).

The workflow's whole value is that nothing it writes is kept unless it passes a
deterministic check, so most of what is tested here is the sieve: exactly which sentences
survive, and exactly which reason each of the others is counted under.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from opengloss_generator import cli
from opengloss_generator.config import ExamplesConfig
from opengloss_generator.schema import (
    Example,
    Lexeme,
    LexemeKind,
    Morphology,
    PartOfSpeech,
    POSEntry,
    ReadingLevel,
    Register,
    Rendition,
    Renditions,
    Sense,
    StageName,
    canonical_rendition,
)
from opengloss_generator.store import LexemeStore
from opengloss_generator.workflows.examples import (
    MARKER_PREFIX,
    RejectReason,
    plan_examples,
    run_examples,
)
from tests.conftest import (
    EXAMPLE_SENTENCE,
    EXAMPLES_ECHO_HEADWORD,
    EXAMPLES_MIXED_HEADWORD,
)

cli_runner = CliRunner()


def _entry(headword: str, glosses: list[str], *, examples: list[str] | None = None) -> Lexeme:
    """Build an entry with one noun part of speech and one sense per gloss."""
    senses = [
        Sense(
            index=index,
            gloss=Renditions[str](root=[canonical_rendition(gloss)]),
            examples=Renditions[Example](
                root=[canonical_rendition(Example(text=text)) for text in (examples or [])]
            )
            if index == 0
            else Renditions[Example](root=[]),
        )
        for index, gloss in enumerate(glosses)
    ]
    return Lexeme.empty(
        headword,
        kind=LexemeKind.SIMPLEX,
        pos_entries=[POSEntry(pos=PartOfSpeech.NOUN, senses=senses, morphology=Morphology())],
    )


def _stored_examples(
    store: LexemeStore, headword: str, sense_index: int = 0
) -> list[Rendition[Example]]:
    """Return one sense's example renditions as they were written to disk."""
    entry = store.read(headword)
    assert entry is not None
    return list(entry.pos_entries[0].senses[sense_index].examples)


# --------------------------------------------------------------------------------------
# The target cycle (config)
# --------------------------------------------------------------------------------------


def test_targets_pair_each_level_with_plain_and_each_register_with_neutral():
    # The axes are deliberately not crossed: a grade_1 technical example is not a thing.
    assert ExamplesConfig().targets() == [
        (ReadingLevel.GRADE_1, Register.PLAIN),
        (ReadingLevel.GRADE_5, Register.PLAIN),
        (ReadingLevel.GRADE_10, Register.PLAIN),
        (ReadingLevel.COLLEGE, Register.PLAIN),
        (ReadingLevel.NEUTRAL, Register.INFORMAL),
        (ReadingLevel.NEUTRAL, Register.FORMAL),
        (ReadingLevel.NEUTRAL, Register.TECHNICAL),
        (ReadingLevel.NEUTRAL, Register.SLANG),
    ]


def test_targets_cycle_when_per_sense_outruns_the_two_axes():
    targets = ExamplesConfig(per_sense=10).targets()
    assert len(targets) == 10
    assert targets[8:] == targets[:2]


def test_targets_fall_back_to_the_canonical_pair_when_both_axes_are_empty():
    config = ExamplesConfig(per_sense=2, reading_levels=[], registers=[])
    assert config.targets() == [(ReadingLevel.NEUTRAL, Register.PLAIN)] * 2


def test_a_word_band_whose_floor_is_above_its_ceiling_is_refused():
    with pytest.raises(ValueError, match="min_words"):
        ExamplesConfig(min_words=20, max_words=10)


# --------------------------------------------------------------------------------------
# What one call produces, and what it stores
# --------------------------------------------------------------------------------------


async def test_every_sense_gets_one_sentence_at_each_configured_target(session):
    session.config.examples.per_sense = 3
    session.store.write(_entry("abseil", ["A way down a cliff.", "A second meaning."]))

    outcome = await run_examples(session.store, session.stages, workers=2)

    assert outcome.entries_scanned == 1
    assert outcome.entries_changed == 1
    assert outcome.sentences_generated == 6
    # The scripted sense-fit checker files every sentence under sense 1, so sense 2's are
    # dropped; sense 1 keeps one sentence per target, in target order.
    assert [r.key for r in _stored_examples(session.store, "abseil")] == [
        (ReadingLevel.GRADE_1, Register.PLAIN),
        (ReadingLevel.GRADE_5, Register.PLAIN),
        (ReadingLevel.GRADE_10, Register.PLAIN),
    ]


async def test_an_accepted_sentence_carries_its_span_assessment_and_provenance(session):
    session.config.examples.per_sense = 1
    session.store.write(_entry("abseil", ["A way down a cliff."]))

    await run_examples(session.store, session.stages, workers=2)

    entry = session.store.read("abseil")
    rendition = entry.pos_entries[0].senses[0].examples[0]
    assert rendition.content.text == EXAMPLE_SENTENCE.format(tag="A", headword="abseil")
    assert rendition.content.matched == "abseil"
    assert rendition.assessment.readability_grade is not None
    assert rendition.assessment.hard_word_share is not None
    record = entry.provenance[rendition.provenance_id]
    assert record.stage is StageName.EXAMPLES
    assert record.note.startswith(f"{MARKER_PREFIX}:")


# --------------------------------------------------------------------------------------
# The sieve
# --------------------------------------------------------------------------------------


async def test_one_of_each_defect_is_counted_under_its_own_reason(session):
    # The mixed marker scripts, in target order: acceptable, an exact repeat of it, one
    # over the word cap, one that never names the headword, one shaped like a definition,
    # then three more acceptable ones.
    session.store.write(_entry(EXAMPLES_MIXED_HEADWORD, ["A test meaning."]))

    outcome = await run_examples(session.store, session.stages, workers=2)

    assert outcome.sentences_generated == 8
    assert outcome.accepted == 4
    assert outcome.rejected_by_reason == {
        RejectReason.DUPLICATE.value: 1,
        RejectReason.TOO_LONG.value: 1,
        RejectReason.HEADWORD_ABSENT.value: 1,
        RejectReason.GLOSS_SHAPED.value: 1,
    }
    assert outcome.rejected == 4
    assert len(_stored_examples(session.store, EXAMPLES_MIXED_HEADWORD)) == 4


async def test_a_sentence_the_sense_already_holds_is_refused_as_a_duplicate(session):
    session.config.examples.per_sense = 1
    stored = EXAMPLE_SENTENCE.format(tag="A", headword="abseil")
    # Same sentence, differently punctuated and cased: normalisation sees one sentence.
    session.store.write(_entry("abseil", ["A way down a cliff."], examples=[stored.upper()]))

    outcome = await run_examples(session.store, session.stages, workers=2)

    assert outcome.accepted == 0
    assert outcome.rejected_by_reason == {RejectReason.DUPLICATE.value: 1}
    assert len(_stored_examples(session.store, "abseil")) == 1


async def test_only_the_first_of_several_sentences_sharing_an_opening_is_kept(session):
    session.config.examples.per_sense = 3
    session.store.write(_entry(EXAMPLES_ECHO_HEADWORD, ["A test meaning."]))

    outcome = await run_examples(session.store, session.stages, workers=2)

    assert outcome.accepted == 1
    assert outcome.rejected_by_reason == {RejectReason.REPEATED_OPENING.value: 2}


# --------------------------------------------------------------------------------------
# The sense-fit check
# --------------------------------------------------------------------------------------


async def test_a_sentence_the_checker_files_under_another_sense_is_dropped(session):
    session.config.examples.per_sense = 2
    session.store.write(_entry("abseil", ["A way down a cliff.", "A second meaning."]))

    outcome = await run_examples(session.store, session.stages, workers=2)

    # Two calls: the generation call and the sense-fit check.
    assert outcome.calls == 2
    assert outcome.sentences_generated == 4
    assert outcome.refiled_dropped == 2
    assert outcome.accepted == 2
    assert len(_stored_examples(session.store, "abseil", 0)) == 2
    assert len(_stored_examples(session.store, "abseil", 1)) == 0


async def test_switching_the_sense_check_off_keeps_every_accepted_sentence(session):
    session.config.examples.per_sense = 2
    session.config.examples.sense_check = False
    session.store.write(_entry("abseil", ["A way down a cliff.", "A second meaning."]))

    outcome = await run_examples(session.store, session.stages, workers=2)

    assert outcome.calls == 1
    assert outcome.refiled_dropped == 0
    assert outcome.accepted == 4
    assert len(_stored_examples(session.store, "abseil", 1)) == 2


async def test_a_single_sense_entry_never_buys_the_sense_check(session):
    session.config.examples.per_sense = 2
    session.store.write(_entry("abseil", ["A way down a cliff."]))

    outcome = await run_examples(session.store, session.stages, workers=2)

    # Nothing to be confused with, so the only possible verdict is "sense 1".
    assert outcome.calls == 1
    assert outcome.accepted == 2


# --------------------------------------------------------------------------------------
# Idempotence and the gate
# --------------------------------------------------------------------------------------


async def test_a_second_sweep_over_an_unchanged_entry_is_free(session):
    session.config.examples.per_sense = 2
    session.store.write(_entry("abseil", ["A way down a cliff."]))

    await run_examples(session.store, session.stages, workers=2)
    spent = session.meter.summary().total_usd

    again = await run_examples(session.store, session.stages, workers=2)

    assert again.entries_scanned == 1
    assert again.entries_changed == 0
    assert again.calls == 0
    assert again.cost_usd == 0.0
    assert session.meter.summary().total_usd == spent


async def test_changing_per_sense_earns_exactly_one_more_call(session):
    session.config.examples.per_sense = 2
    session.store.write(_entry("abseil", ["A way down a cliff."]))
    await run_examples(session.store, session.stages, workers=2)

    session.config.examples.per_sense = 3
    again = await run_examples(session.store, session.stages, workers=2)

    assert again.calls == 1
    # The first two slots repeat sentences the entry already holds; only the third is new.
    assert again.accepted == 1
    assert again.rejected_by_reason == {RejectReason.DUPLICATE.value: 2}
    assert len(_stored_examples(session.store, "abseil")) == 3


async def test_an_entry_whose_only_sense_is_retired_costs_nothing(session):
    entry = _entry("abseil", ["A way down a cliff."])
    entry.pos_entries[0].senses[0].retired = True
    session.store.write(entry)

    outcome = await run_examples(session.store, session.stages, workers=2)

    assert outcome.entries_scanned == 1
    assert outcome.entries_changed == 0
    assert outcome.calls == 0
    assert outcome.cost_usd == 0.0
    assert session.meter.summary().total_usd == 0.0


async def test_plan_examples_prices_an_entry_and_then_reports_it_done(session):
    session.config.examples.per_sense = 2
    entry = _entry("abseil", ["A way down a cliff.", "A second meaning."])
    session.store.write(entry)

    plan = plan_examples(entry, session.config.examples)
    assert (plan.due, plan.senses, plan.sentences, plan.sense_check) == (True, 2, 4, True)

    await run_examples(session.store, session.stages, workers=2)

    after = plan_examples(session.store.read("abseil"), session.config.examples)
    assert after.due is False
    assert after.sentences == 0


# --------------------------------------------------------------------------------------
# The CLI
# --------------------------------------------------------------------------------------


def test_cli_dry_run_reports_the_plan_without_calling_a_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("OPENGLOSS_LOG_DIR", str(tmp_path / "runs"))
    root = tmp_path / "store"
    from opengloss_generator.config import StoreConfig  # noqa: PLC0415 - one CLI test only

    LexemeStore(StoreConfig(root=root, fsync_on_write=False)).write(
        _entry("abseil", ["A way down a cliff.", "A second meaning."])
    )

    result = cli_runner.invoke(
        cli.app, ["examples", "--all", "--store", str(root), "--per-sense", "4", "--dry-run"]
    )
    assert result.exit_code == 0, result.output
    summary = json.loads(result.stdout)

    assert summary["stop_reason"] == "dry_run"
    assert summary["cost_usd"] == 0.0
    assert summary["entries_scanned"] == 1
    assert summary["entries_due"] == 1
    assert summary["sentences_planned"] == 8
    # One generation call plus one sense-fit call: the entry carries two live senses.
    assert summary["estimated_calls"] == 2
    assert summary["estimated_cost_usd"] > 0.0


def test_cli_refuses_both_selectors_at_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("OPENGLOSS_LOG_DIR", str(tmp_path / "runs"))
    result = cli_runner.invoke(cli.app, ["examples", "--store", str(tmp_path / "store")])
    assert result.exit_code != 0
