"""Synthetic retrieval queries per sense (D-55).

The stage's value is that everything it can check, it checks for nothing: what survives the
sieve, what each rejection is counted under, how many of the stored queries name their own
headword, and whether one sense's set spans all eight styles. Most of what is tested here
is that measurement, plus D-47's per-sense marker doing exactly two attempts and no more.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from opengloss_generator import cli
from opengloss_generator.identity import query_id
from opengloss_generator.schema import (
    Example,
    Lexeme,
    LexemeKind,
    Morphology,
    PartOfSpeech,
    POSEntry,
    Query,
    QueryStyle,
    Renditions,
    Sense,
    StageName,
    canonical_rendition,
)
from opengloss_generator.store import LexemeStore
from opengloss_generator.workflows.queries import (
    DEFAULT_PER_SENSE,
    MARKER_PREFIX,
    MAX_ATTEMPTS,
    MAX_PER_SENSE,
    MIN_PER_SENSE,
    QUERY_MAX_CHARS,
    RejectReason,
    SenseReport,
    _build_prompt,
    _slots,
    plan_queries,
    run_queries,
)
from tests.conftest import (
    QUERIES_LEXICAL_HEADWORD,
    QUERIES_MIXED_HEADWORD,
    QUERIES_SURPLUS_HEADWORD,
    QUERY_LEXICAL,
)

cli_runner = CliRunner()


def _entry(headword: str, glosses: list[str], *, example: str | None = None) -> Lexeme:
    """Build an entry with one noun part of speech and one sense per gloss."""
    senses = [
        Sense(
            index=index,
            gloss=Renditions[str](root=[canonical_rendition(gloss)]),
            examples=Renditions[Example](
                root=[canonical_rendition(Example(text=example))] if example and index == 0 else []
            ),
        )
        for index, gloss in enumerate(glosses)
    ]
    return Lexeme.empty(
        headword,
        kind=LexemeKind.SIMPLEX,
        pos_entries=[POSEntry(pos=PartOfSpeech.NOUN, senses=senses, morphology=Morphology())],
    )


def _stored(store: LexemeStore, headword: str, sense_index: int = 0) -> list[Query]:
    """Return one sense's stored queries, as they were written to disk."""
    entry = store.read(headword)
    assert entry is not None
    return entry.pos_entries[0].senses[sense_index].queries


def _rewrite_gloss(store: LexemeStore, headword: str, text: str) -> None:
    """Replace one entry's first sense's canonical gloss, keeping its provenance table."""
    entry = store.read(headword)
    assert entry is not None
    canonical = entry.pos_entries[0].senses[0].gloss.canonical()
    assert canonical is not None
    canonical.content = text
    store.write(entry)


# --------------------------------------------------------------------------------------
# The prompt
# --------------------------------------------------------------------------------------


def test_the_prompt_carries_the_sense_its_siblings_and_the_count():
    entry = _entry(
        "abseil",
        ["A way down a cliff on a rope.", "A quite different second meaning."],
        example="They abseiled at dawn.",
    )
    slots = _slots(entry)

    prompt = _build_prompt(
        "abseil", slots[0], [(s.pos_entry.pos.value, s.gloss) for s in slots[1:]], 12
    )

    assert "Headword: abseil" in prompt
    assert "Definition: A way down a cliff on a rope." in prompt
    assert "Example: They abseiled at dawn." in prompt
    # The sibling gloss is what the queries have to discriminate against, so it is in the
    # prompt; the sense's own gloss is never repeated in that list.
    assert "A quite different second meaning." in prompt
    assert prompt.count("A way down a cliff on a rope.") == 1
    assert "Write exactly 12 queries" in prompt


def test_a_single_sense_prompt_says_so_rather_than_listing_nothing():
    entry = _entry("abseil", ["A way down a cliff on a rope."])
    prompt = _build_prompt("abseil", _slots(entry)[0], [], 8)
    assert "one live sense" in prompt


# --------------------------------------------------------------------------------------
# What one call produces, and what it stores
# --------------------------------------------------------------------------------------


async def test_every_live_sense_gets_one_call_and_the_queries_it_asked_for(session):
    session.store.write(_entry("abseil", ["A way down a cliff.", "A second meaning."]))

    outcome = await run_queries(session.store, session.stages, workers=2)

    assert outcome.entries_scanned == 1
    assert outcome.entries_changed == 1
    assert outcome.senses_scanned == 2
    assert outcome.senses_answered == 2
    assert outcome.calls == 2
    assert outcome.queries_generated == 2 * DEFAULT_PER_SENSE
    assert outcome.stored == 2 * DEFAULT_PER_SENSE
    assert outcome.rejected == 0
    assert len(_stored(session.store, "abseil", 0)) == DEFAULT_PER_SENSE
    assert len(_stored(session.store, "abseil", 1)) == DEFAULT_PER_SENSE


async def test_a_stored_query_carries_its_style_and_the_call_that_wrote_it(session):
    session.store.write(_entry("abseil", ["A way down a cliff."]))

    await run_queries(session.store, session.stages, workers=2)

    entry = session.store.read("abseil")
    sense = entry.pos_entries[0].senses[0]
    first = sense.queries[0]
    assert first.style is QueryStyle.KEYWORD
    record = entry.provenance[first.provenance_id]
    assert record.stage is StageName.QUERIES
    assert record.note.startswith(f"{MARKER_PREFIX}:")
    # Ids are positional and derived, never stored (D-62).
    assert sense.query_ids("abseil:noun:0")[0] == query_id("abseil:noun:0", 0)


async def test_queries_are_appended_so_the_ids_of_the_existing_ones_do_not_move(session):
    session.store.write(_entry("abseil", ["A way down a cliff."]))
    await run_queries(session.store, session.stages, workers=2, per_sense=8)
    before = [q.text for q in _stored(session.store, "abseil")]

    # A rewritten gloss earns exactly one more call, whose queries land after these.
    _rewrite_gloss(session.store, "abseil", "A completely different way down a cliff.")
    await run_queries(session.store, session.stages, workers=2, per_sense=8)

    after = [q.text for q in _stored(session.store, "abseil")]
    assert after[: len(before)] == before


async def test_a_retired_sense_is_never_written_for(session):
    entry = _entry("abseil", ["A way down a cliff.", "A second meaning."])
    entry.pos_entries[0].senses[1].retired = True
    session.store.write(entry)

    outcome = await run_queries(session.store, session.stages, workers=2)

    assert outcome.senses_scanned == 1
    assert outcome.calls == 1
    assert len(_stored(session.store, "abseil", 1)) == 0


# --------------------------------------------------------------------------------------
# The sieve
# --------------------------------------------------------------------------------------


async def test_one_of_each_defect_is_counted_under_its_own_reason(session):
    # The mixed marker scripts, in slot order: acceptable, an exact repeat of it, one over
    # the character ceiling, one that is whitespace only, then four more acceptable ones.
    session.store.write(_entry(QUERIES_MIXED_HEADWORD, ["A test meaning."]))

    outcome = await run_queries(session.store, session.stages, workers=2, per_sense=8)

    assert outcome.queries_generated == 8
    assert outcome.stored == 5
    assert outcome.rejected_by_reason == {
        RejectReason.DUPLICATE.value: 1,
        RejectReason.TOO_LONG.value: 1,
        RejectReason.EMPTY.value: 1,
    }
    kept = _stored(session.store, QUERIES_MIXED_HEADWORD)
    assert len(kept) == 5
    assert all(len(query.text) <= QUERY_MAX_CHARS for query in kept)


async def test_queries_past_the_count_asked_for_are_refused_as_surplus(session):
    session.store.write(_entry(QUERIES_SURPLUS_HEADWORD, ["A test meaning."]))

    outcome = await run_queries(session.store, session.stages, workers=2, per_sense=8)

    assert outcome.queries_generated == 10
    assert outcome.stored == 8
    assert outcome.rejected_by_reason == {RejectReason.SURPLUS.value: 2}


async def test_a_query_the_sense_already_holds_is_refused_as_a_duplicate(session):
    session.store.write(_entry("abseil", ["A way down a cliff."]))
    await run_queries(session.store, session.stages, workers=2, per_sense=8)

    # The same gloss under a different per_sense earns one more call, whose scripted answer
    # repeats the first eight queries verbatim; not one of them may be stored twice.
    outcome = await run_queries(session.store, session.stages, workers=2, per_sense=9)

    assert outcome.calls == 1
    assert outcome.stored == 1
    assert outcome.rejected_by_reason == {RejectReason.DUPLICATE.value: 8}
    assert len(_stored(session.store, "abseil")) == 9


# --------------------------------------------------------------------------------------
# The two free measurements
# --------------------------------------------------------------------------------------


async def test_the_headword_free_share_is_measured_on_what_was_stored(session):
    session.store.write(_entry("abseil", ["A way down a cliff."]))

    outcome = await run_queries(session.store, session.stages, workers=2, per_sense=8)

    # The scripted answer names the headword in exactly one of its eight queries.
    assert outcome.with_headword == 1
    assert outcome.headword_free_share == pytest.approx(7 / 8)
    assert outcome.senses_below_headword_free_target == 0


async def test_a_sense_whose_queries_all_name_the_headword_is_flagged(session):
    session.store.write(_entry(QUERIES_LEXICAL_HEADWORD, ["A test meaning."]))

    outcome = await run_queries(session.store, session.stages, workers=2, per_sense=8)

    assert outcome.with_headword == 8
    assert outcome.headword_free_share == 0.0
    assert outcome.senses_below_headword_free_target == 1


async def test_style_coverage_is_counted_per_sense(session):
    session.store.write(_entry("abseil", ["A way down a cliff."]))

    outcome = await run_queries(session.store, session.stages, workers=2, per_sense=8)

    assert outcome.senses_with_full_style_coverage == 1
    assert outcome.stored_by_style == {style.value: 1 for style in QueryStyle}


async def test_a_sense_missing_a_style_is_not_counted_as_covered(session):
    # Five of the mixed headword's eight slots survive, so three styles never land.
    session.store.write(_entry(QUERIES_MIXED_HEADWORD, ["A test meaning."]))

    outcome = await run_queries(session.store, session.stages, workers=2, per_sense=8)

    assert outcome.senses_with_full_style_coverage == 0
    assert len(outcome.stored_by_style) == 5


# --------------------------------------------------------------------------------------
# Idempotence, and D-47's bound
# --------------------------------------------------------------------------------------


async def test_a_second_sweep_over_an_unchanged_sense_is_free(session):
    session.store.write(_entry("abseil", ["A way down a cliff."]))
    await run_queries(session.store, session.stages, workers=2)
    spent = session.meter.summary().total_usd

    again = await run_queries(session.store, session.stages, workers=2)

    assert again.entries_scanned == 1
    assert again.entries_changed == 0
    assert again.calls == 0
    assert again.cost_usd == 0.0
    assert session.meter.summary().total_usd == spent


async def test_a_rewritten_gloss_earns_exactly_one_more_call_and_then_no_more(session):
    session.store.write(_entry("abseil", ["A way down a cliff."]))
    await run_queries(session.store, session.stages, workers=2)

    _rewrite_gloss(session.store, "abseil", "A rope descent of a rock face.")
    second = await run_queries(session.store, session.stages, workers=2)
    assert second.calls == 1

    # D-47's bound: a third gloss is a third question, and this stage answers two.
    _rewrite_gloss(session.store, "abseil", "Yet another way of putting the same thing.")
    third = await run_queries(session.store, session.stages, workers=2)
    assert third.calls == 0
    assert third.cost_usd == 0.0
    assert MAX_ATTEMPTS == 2


async def test_the_marker_names_the_sense_it_was_written_for(session):
    session.store.write(_entry("abseil", ["A way down a cliff.", "A second meaning."]))

    await run_queries(session.store, session.stages, workers=2)

    entry = session.store.read("abseil")
    notes = sorted(r.note for r in entry.provenance.values() if r.note)
    assert len(notes) == 2
    assert notes[0].startswith(f"{MARKER_PREFIX}:abseil:noun:0:")
    assert notes[1].startswith(f"{MARKER_PREFIX}:abseil:noun:1:")
    assert notes[0].endswith(";attempts=1")


async def test_plan_queries_prices_an_entry_and_then_reports_it_done(session):
    entry = _entry("abseil", ["A way down a cliff.", "A second meaning."])
    session.store.write(entry)

    plan = plan_queries(entry, 12)
    assert (plan.due, plan.senses, plan.queries) == (True, 2, 24)

    await run_queries(session.store, session.stages, workers=2)

    after = plan_queries(session.store.read("abseil"), 12)
    assert after.due is False
    assert after.queries == 0


# --------------------------------------------------------------------------------------
# The ledger hook and the guard rails
# --------------------------------------------------------------------------------------


async def test_one_report_is_handed_to_the_caller_per_call(session):
    session.store.write(_entry("abseil", ["A way down a cliff.", "A second meaning."]))
    seen: list[SenseReport] = []

    async def collect(report: SenseReport) -> None:
        seen.append(report)

    await run_queries(session.store, session.stages, workers=2, on_sense=collect)

    assert sorted(r.sense_id for r in seen) == ["abseil:noun:0", "abseil:noun:1"]
    assert {r.outcome for r in seen} == {"stored"}
    assert all(r.stored == DEFAULT_PER_SENSE for r in seen)
    # The measured output-token count is what a pilot reads back to set the policy's
    # `expected_output_tokens` from (D-41), so it has to be on the record.
    assert all(r.output_tokens > 0 for r in seen)
    assert all(r.cost_usd > 0 for r in seen)


@pytest.mark.parametrize("count", [MIN_PER_SENSE - 1, MAX_PER_SENSE + 1])
async def test_a_per_sense_outside_the_band_is_refused_before_anything_is_billed(session, count):
    session.store.write(_entry("abseil", ["A way down a cliff."]))

    with pytest.raises(ValueError, match="per_sense"):
        await run_queries(session.store, session.stages, workers=2, per_sense=count)

    assert session.meter.summary().total_usd == 0.0


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
        cli.app, ["queries", "--all", "--store", str(root), "--per-sense", "10", "--dry-run"]
    )
    assert result.exit_code == 0, result.output
    summary = json.loads(result.stdout)

    assert summary["stop_reason"] == "dry_run"
    assert summary["cost_usd"] == 0.0
    assert summary["entries_scanned"] == 1
    assert summary["entries_due"] == 1
    assert summary["senses_due"] == 2
    assert summary["queries_planned"] == 20
    assert summary["estimated_calls"] == 2
    assert summary["estimated_cost_usd"] > 0.0


def test_cli_refuses_both_selectors_at_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("OPENGLOSS_LOG_DIR", str(tmp_path / "runs"))
    result = cli_runner.invoke(cli.app, ["queries", "--store", str(tmp_path / "store")])
    assert result.exit_code != 0


def test_the_scripted_lexical_query_names_its_headword():
    # Guards the two measurement tests above: if this template stopped containing the
    # headword they would both pass for the wrong reason.
    assert "abseil" in QUERY_LEXICAL.format(headword="abseil", index=0)
