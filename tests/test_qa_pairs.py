"""Grounded question/answer pairs, one call per sense (D-58).

The stage's whole claim is that nothing it stores says anything the store did not already
say, so most of what is tested here is the sieve: exactly which drafted pairs survive, and
exactly which reason each of the others is counted under. The rest is D-47's per-sense
marker, because this is the first stage in the project whose unit of work is a sense while
its unit of storage is still an entry.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from opengloss_generator import cli
from opengloss_generator.identity import encyclopedia_owner_id, rendition_id
from opengloss_generator.schema import (
    Difficulty,
    Etymology,
    Example,
    Lexeme,
    LexemeKind,
    Morphology,
    PartOfSpeech,
    POSEntry,
    QAPair,
    QuestionType,
    Renditions,
    Sense,
    StageName,
    canonical_rendition,
)
from opengloss_generator.store import LexemeStore
from opengloss_generator.workflows.qa_pairs import (
    MARKER_PREFIX,
    MAX_ATTEMPTS,
    DropReason,
    meta_reference,
    plan_qa_pairs,
    run_qa_pairs,
)
from tests.conftest import (
    QA_GLOSS_ECHO_HEADWORD,
    QA_META_REFERENCE_HEADWORD,
    QA_META_REPAIR_CLAUSE,
    QA_META_REPAIR_HEADWORD,
    QA_MIXED_HEADWORD,
    QA_QUESTION_TYPES,
    QA_UNGROUNDED_HEADWORD,
)

cli_runner = CliRunner()

GLOSS = "A rope descent down a steep rock face."
SECOND_GLOSS = "A quick retreat from a difficult conversation."
EXAMPLE = "They abseiled slowly down the wet granite cliff."
ENCYCLOPEDIA = (
    "Abseiling lowers a climber down a fixed rope under friction. The friction device "
    "converts the climber's weight into heat, so the descent stays controlled."
)
ETYMOLOGY = "From German abseilen, to rope down, itself from Seil, a rope."


def _entry(
    headword: str,
    glosses: list[str] | None = None,
    *,
    examples: list[str] | None = None,
    encyclopedia: str | None = None,
    etymology: str | None = None,
) -> Lexeme:
    """Build an entry with one verb part of speech and one sense per gloss."""
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
        for index, gloss in enumerate(glosses or [GLOSS])
    ]
    return Lexeme.empty(
        headword,
        kind=LexemeKind.SIMPLEX,
        pos_entries=[POSEntry(pos=PartOfSpeech.VERB, senses=senses, morphology=Morphology())],
        encyclopedia=Renditions[str](
            root=[canonical_rendition(encyclopedia)] if encyclopedia else []
        ),
        etymology=Etymology(summary=etymology) if etymology else None,
    )


def _pairs(store: LexemeStore, headword: str, sense_index: int = 0) -> list[QAPair]:
    """Return one sense's QA pairs as they were written to disk."""
    entry = store.read(headword)
    assert entry is not None
    return entry.pos_entries[0].senses[sense_index].qa


# --------------------------------------------------------------------------------------
# What one call produces, and what it stores
# --------------------------------------------------------------------------------------


async def test_every_live_sense_earns_one_call_and_seven_pairs(session):
    session.store.write(_entry("abseil", [GLOSS, SECOND_GLOSS]))

    outcome = await run_qa_pairs(session.store, session.stages, workers=2)

    assert (outcome.entries_scanned, outcome.entries_changed) == (1, 1)
    assert outcome.calls == 2
    assert outcome.senses_written == 2
    assert outcome.pairs_generated == 14
    assert outcome.accepted == 14
    assert outcome.dropped_by_reason == {}
    assert len(_pairs(session.store, "abseil", 0)) == 7
    assert len(_pairs(session.store, "abseil", 1)) == 7


async def test_one_pair_of_each_question_type_at_more_than_one_difficulty(session):
    session.store.write(_entry("abseil"))

    outcome = await run_qa_pairs(session.store, session.stages, workers=2)

    assert outcome.senses_with_full_type_coverage == 1
    assert set(outcome.accepted_by_type) == {kind.value for kind in QuestionType}
    assert len(outcome.accepted_by_difficulty) > 1
    stored = _pairs(session.store, "abseil")
    assert [pair.question_type.value for pair in stored] == list(QA_QUESTION_TYPES)
    assert stored[0].difficulty is Difficulty.EASY


async def test_a_stored_pair_carries_its_citation_and_the_call_that_wrote_it(session):
    session.store.write(_entry("abseil", examples=[EXAMPLE]))

    await run_qa_pairs(session.store, session.stages, workers=2)

    entry = session.store.read("abseil")
    pair = entry.pos_entries[0].senses[0].qa[0]
    assert pair.grounded_in == [rendition_id("abseil:verb:0", "neutral", "plain")]
    record = entry.provenance[pair.provenance_id]
    assert record.stage is StageName.QA_PAIRS
    assert record.note.startswith(f"{MARKER_PREFIX}:abseil:verb:0:")


async def test_the_ids_offered_to_the_model_cover_every_kind_of_stored_text(session):
    session.store.write(
        _entry(
            "abseil",
            examples=[EXAMPLE],
            encyclopedia=ENCYCLOPEDIA,
            etymology=ETYMOLOGY,
        )
    )

    await run_qa_pairs(session.store, session.stages, workers=2)

    # The scripted answer cites the sources round-robin, so seven pairs over four sources
    # cite every one of them at least once — which is what proves the prompt labelled all
    # four and that the sieve accepted all four id forms.
    cited = {source for pair in _pairs(session.store, "abseil") for source in pair.grounded_in}
    assert cited == {
        rendition_id("abseil:verb:0", "neutral", "plain"),
        "abseil:verb:0#ex0",
        rendition_id(encyclopedia_owner_id("abseil"), "neutral", "plain"),
        "abseil:etymology",
    }


# --------------------------------------------------------------------------------------
# The sieve
# --------------------------------------------------------------------------------------


async def test_one_of_each_defect_is_counted_under_its_own_reason(session):
    # The mixed marker scripts, in type order: a clean pair, one citing an id that was
    # never supplied, one whose answer shares nothing with what it cites, one citing
    # nothing at all, one repeating the first pair's question, then two more clean ones.
    session.store.write(_entry(QA_MIXED_HEADWORD))

    outcome = await run_qa_pairs(session.store, session.stages, workers=2)

    assert outcome.pairs_generated == 7
    assert outcome.accepted == 3
    assert outcome.dropped_by_reason == {
        DropReason.UNKNOWN_CITATION.value: 1,
        DropReason.NOT_GROUNDED.value: 1,
        DropReason.NO_CITATION.value: 1,
        DropReason.DUPLICATE_QUESTION.value: 1,
    }
    assert outcome.dropped == 4
    assert len(_pairs(session.store, QA_MIXED_HEADWORD)) == 3


async def test_an_answer_that_shares_nothing_with_its_citation_is_dropped(session):
    session.store.write(_entry(QA_UNGROUNDED_HEADWORD))

    outcome = await run_qa_pairs(session.store, session.stages, workers=2)

    assert outcome.accepted == 0
    assert outcome.dropped_by_reason == {DropReason.NOT_GROUNDED.value: 7}
    assert _pairs(session.store, QA_UNGROUNDED_HEADWORD) == []


async def test_a_call_whose_every_pair_was_dropped_still_records_that_it_happened(session):
    # Otherwise the next sweep buys the same rejected answer again, forever.
    session.store.write(_entry(QA_UNGROUNDED_HEADWORD))

    await run_qa_pairs(session.store, session.stages, workers=2)
    entry = session.store.read(QA_UNGROUNDED_HEADWORD)
    assert any(
        (record.note or "").startswith(f"{MARKER_PREFIX}:{QA_UNGROUNDED_HEADWORD}:verb:0:")
        for record in entry.provenance.values()
    )

    again = await run_qa_pairs(session.store, session.stages, workers=2)
    assert again.calls == 0


async def test_a_question_the_sense_already_holds_is_dropped(session):
    session.store.write(_entry("abseil"))
    await run_qa_pairs(session.store, session.stages, workers=2)

    # A rewritten gloss changes the digest, so the sense earns a second attempt — whose
    # scripted questions are the same seven, every one of them already stored.
    entry = session.store.read("abseil")
    entry.pos_entries[0].senses[0].gloss.root[0].content = "A different definition entirely."
    session.store.write(entry)

    again = await run_qa_pairs(session.store, session.stages, workers=2)

    assert again.calls == 1
    assert again.accepted == 0
    assert again.dropped_by_reason == {DropReason.DUPLICATE_QUESTION.value: 7}
    assert len(_pairs(session.store, "abseil")) == 7


# --------------------------------------------------------------------------------------
# Meta-reference and gloss echo (D-69)
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "answer",
    [
        "According to the sources, the current cuts fastest on the outer bend.",
        "As described in the example, the climber rappelled down slowly.",
        "As stated above, friction controls the descent.",
        "As shown in the example above, the rope stayed taut.",
        "In the passage above, the author explains the mechanism.",
        "The given text does not mention the second sense.",
        "The provided examples all describe the same technique.",
        "The supplied sources agree on this point.",
        "The text provided does not go further.",
        "The passage explains how friction converts to heat.",
        "The sources describe two distinct meanings.",
        "What does the passage say about vegetation?",
    ],
)
def test_meta_reference_catches_every_scripted_shape(answer):
    assert meta_reference(answer) is not None


@pytest.mark.parametrize(
    "answer",
    [
        "For example, a climber might use a figure-eight descender.",
        "An example of this is a river cutting into its outer bank.",
        "Willow roots are a good example of what holds the bank together.",
        "This is just one example among many.",
        "The current cuts fastest on the outer bend of a river.",
        "Friction converts the climber's kinetic energy into heat.",
    ],
)
def test_meta_reference_does_not_fire_on_ordinary_use_of_example(answer):
    assert meta_reference(answer) is None


async def test_a_leading_meta_reference_clause_is_repaired_rather_than_dropped(session):
    # QA_META_REPAIR_HEADWORD's factual answer opens with "According to the sources, " --
    # a leading clause the free repair can remove without touching the fact after it.
    session.store.write(_entry(QA_META_REPAIR_HEADWORD))

    outcome = await run_qa_pairs(session.store, session.stages, workers=2)

    assert outcome.dropped_by_reason == {}
    assert outcome.meta_reference_repairs == 1
    assert outcome.accepted == 7
    stored = _pairs(session.store, QA_META_REPAIR_HEADWORD)
    factual = next(pair for pair in stored if pair.question_type.value == "factual")
    assert not factual.answer.startswith(QA_META_REPAIR_CLAUSE)
    assert meta_reference(factual.answer) is None


async def test_an_unrepairable_meta_reference_is_dropped(session):
    # QA_META_REFERENCE_HEADWORD's factual answer names the scaffolding mid-sentence, with
    # no leading clause to strip, so the repair cannot help and the pair is dropped.
    session.store.write(_entry(QA_META_REFERENCE_HEADWORD))

    outcome = await run_qa_pairs(session.store, session.stages, workers=2)

    assert outcome.dropped_by_reason == {DropReason.META_REFERENCE.value: 1}
    assert outcome.meta_reference_repairs == 0
    assert outcome.accepted == 6
    assert len(_pairs(session.store, QA_META_REFERENCE_HEADWORD)) == 6


async def test_a_definition_answer_that_echoes_the_gloss_verbatim_is_dropped(session):
    session.store.write(_entry(QA_GLOSS_ECHO_HEADWORD))

    outcome = await run_qa_pairs(session.store, session.stages, workers=2)

    assert outcome.dropped_by_reason == {DropReason.ECHOES_GLOSS.value: 1}
    assert outcome.accepted == 6
    stored = _pairs(session.store, QA_GLOSS_ECHO_HEADWORD)
    assert "definition" not in {pair.question_type.value for pair in stored}


async def test_the_summary_reports_meta_reference_and_echoes_gloss_drops(session):
    session.store.write(_entry(QA_META_REFERENCE_HEADWORD))
    session.store.write(_entry(QA_GLOSS_ECHO_HEADWORD))
    session.store.write(_entry(QA_META_REPAIR_HEADWORD))

    outcome = await run_qa_pairs(session.store, session.stages, workers=2)
    summary = outcome.as_dict()

    assert summary["dropped_by_reason"]["meta_reference"] == 1
    assert summary["dropped_by_reason"]["echoes_gloss"] == 1
    assert summary["meta_reference_repairs"] == 1


# --------------------------------------------------------------------------------------
# Idempotence and the gate (D-47)
# --------------------------------------------------------------------------------------


async def test_a_second_sweep_over_an_unchanged_sense_is_free(session):
    session.store.write(_entry("abseil", [GLOSS, SECOND_GLOSS]))

    await run_qa_pairs(session.store, session.stages, workers=2)
    spent = session.meter.summary().total_usd

    again = await run_qa_pairs(session.store, session.stages, workers=2)

    assert again.entries_scanned == 1
    assert again.entries_changed == 0
    assert again.calls == 0
    assert again.cost_usd == 0.0
    assert session.meter.summary().total_usd == spent


async def test_a_sense_that_gains_an_example_earns_one_more_call_and_then_no_more(session):
    session.store.write(_entry("abseil"))
    await run_qa_pairs(session.store, session.stages, workers=2)

    for round_number in range(1, MAX_ATTEMPTS + 2):
        entry = session.store.read("abseil")
        entry.pos_entries[0].senses[0].examples.add(
            canonical_rendition(Example(text=f"They abseiled on day {round_number}."))
        )
        session.store.write(entry)
        again = await run_qa_pairs(session.store, session.stages, workers=2)
        # One more attempt is bought, and one only: the bound is two calls per sense per
        # lifetime, not two per change.
        assert again.calls == (1 if round_number == 1 else 0), round_number


async def test_a_retired_sense_costs_nothing(session):
    entry = _entry("abseil")
    entry.pos_entries[0].senses[0].retired = True
    session.store.write(entry)

    outcome = await run_qa_pairs(session.store, session.stages, workers=2)

    assert (outcome.entries_scanned, outcome.entries_changed, outcome.calls) == (1, 0, 0)
    assert session.meter.summary().total_usd == 0.0


async def test_plan_qa_pairs_prices_an_entry_and_then_reports_it_done(session):
    entry = _entry("abseil", [GLOSS, SECOND_GLOSS])
    session.store.write(entry)

    plan = plan_qa_pairs(entry)
    assert (plan.due, plan.senses, plan.pairs) == (True, 2, 14)

    await run_qa_pairs(session.store, session.stages, workers=2)

    after = plan_qa_pairs(session.store.read("abseil"))
    assert after.due is False
    assert after.pairs == 0


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
        _entry("abseil", [GLOSS, SECOND_GLOSS])
    )

    result = cli_runner.invoke(cli.app, ["qa-pairs", "--store", str(root), "--dry-run"])
    assert result.exit_code == 0, result.output
    summary = json.loads(result.stdout)

    assert summary["stop_reason"] == "dry_run"
    assert summary["cost_usd"] == 0.0
    assert summary["entries_scanned"] == 1
    assert summary["entries_due"] == 1
    assert summary["senses_due"] == 2
    assert summary["pairs_planned"] == 14
    assert summary["estimated_calls"] == 2
    assert summary["estimated_cost_usd"] > 0.0


def test_cli_is_not_the_judge(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    # `opengloss qa` is the Opus judge and predates this stage; `qa-pairs` must not have
    # shadowed it or been registered under its name.
    monkeypatch.setenv("OPENGLOSS_LOG_DIR", str(tmp_path / "runs"))
    result = cli_runner.invoke(cli.app, ["--help"])
    assert "qa-pairs" in result.output
    assert result.exit_code == 0
