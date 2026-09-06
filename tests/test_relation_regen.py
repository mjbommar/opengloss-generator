"""Relation regeneration for senses left with zero relations (D-74).

Companion to ``test_relation_hygiene.py`` (which demotes an untrue edge) and
``test_relation_reconcile.py`` (which tombstones it out of the list): this covers the
pass that fills the hole those two leave behind, and the one property that matters most
about it — that it does not simply hand a sense back the exact target a previous pass
already rejected.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from opengloss_generator import cli
from opengloss_generator.config import StoreConfig
from opengloss_generator.identity import sense_id as derive_sense_id
from opengloss_generator.schema import (
    Lexeme,
    LexemeKind,
    Morphology,
    PartOfSpeech,
    POSEntry,
    Provenance,
    RelationType,
    Sense,
    StageName,
)
from opengloss_generator.store import LexemeStore
from opengloss_generator.workflows import relation_regen as module
from opengloss_generator.workflows.relation_reconcile import (
    TOMBSTONE_LINE_PREFIX,
    TOMBSTONE_RECORD_PREFIX,
    RelationCaps,
)
from opengloss_generator.workflows.relation_regen import (
    MARKER_PREFIX,
    MAX_ATTEMPTS,
    RelationRegenOutcome,
    plan_relation_regen,
    run_relation_regen,
)
from tests.conftest import (
    RELATION_REGEN_CAP_HEADWORD,
    RELATION_REGEN_EMPTY_HEADWORD,
)

cli_runner = CliRunner()

DEFAULT_GLOSS = "A test definition for the sense under test."
OTHER_GLOSS = "A second, unrelated meaning of the same headword."


def _entry(
    headword: str,
    glosses: list[str],
    *,
    tombstoned: list[str] | None = None,
) -> tuple[Lexeme, str]:
    """Build a one-POS entry whose first sense has no relations.

    Args:
        headword: The entry's surface form.
        glosses: One sense per gloss; the first sense is left with no relations, every
            other sense exists purely as "other senses" discrimination context.
        tombstoned: Terms to record as already tombstoned for sense 0, via a
            ``relation_reconcile``-shaped provenance record.

    Returns:
        ``(entry, sense_id of sense 0)``.
    """
    senses = [Sense.of(index, gloss) for index, gloss in enumerate(glosses)]
    entry = Lexeme.empty(
        headword,
        kind=LexemeKind.SIMPLEX,
        pos_entries=[POSEntry(pos=PartOfSpeech.NOUN, senses=senses, morphology=Morphology())],
    )
    sid = derive_sense_id(entry.lexeme_id, PartOfSpeech.NOUN.value, 0)
    if tombstoned:
        lines = [
            f"{TOMBSTONE_LINE_PREFIX}see_also -> {term} [demoted: nano invalid]"
            for term in tombstoned
        ]
        entry.add_provenance(
            Provenance(
                stage=StageName.HYGIENE,
                model="rule:relation_reconcile",
                prompt_version="1",
                cost_usd=0.0,
                attempts=0,
                note="\n".join([f"{TOMBSTONE_RECORD_PREFIX}{sid}", *lines]),
            )
        )
    return entry, sid


# --------------------------------------------------------------------------------------
# Planning
# --------------------------------------------------------------------------------------


def test_plan_counts_only_zero_relation_live_senses_due_an_attempt():
    entry, _ = _entry("abseil", [DEFAULT_GLOSS, OTHER_GLOSS])
    # Give the second sense a relation so it is not "due" at all.
    entry.pos_entries[0].senses[1].relations.append(
        module.Relation(type=RelationType.SEE_ALSO, target=module.RelationTarget(term="rope"))
    )

    plan = plan_relation_regen(entry)

    assert plan.due is True
    assert plan.senses_due == 1


def test_plan_reports_nothing_due_when_every_sense_has_relations():
    entry, _ = _entry("abseil", [DEFAULT_GLOSS])
    entry.pos_entries[0].senses[0].relations.append(
        module.Relation(type=RelationType.SEE_ALSO, target=module.RelationTarget(term="rope"))
    )

    assert plan_relation_regen(entry).due is False


# --------------------------------------------------------------------------------------
# Filling a sense
# --------------------------------------------------------------------------------------


async def test_an_empty_sense_is_filled_with_unresolved_regen_relations(session):
    entry, sid = _entry("abseil", [DEFAULT_GLOSS])
    session.store.write(entry)

    outcome = await run_relation_regen(session.store, session.stages, workers=2)

    assert isinstance(outcome, RelationRegenOutcome)
    assert outcome.entries_scanned == 1
    assert outcome.entries_changed == 1
    assert outcome.senses_scanned == 1
    assert outcome.senses_filled == 1
    assert outcome.calls == 1

    stored = session.store.read("abseil")
    relations = stored.pos_entries[0].senses[0].relations
    assert relations, "the accepted synonym must have been written"
    accepted = next(r for r in relations if r.type is RelationType.SYNONYM)
    assert accepted.target.sense_id is None  # unresolved: resolve() handles this later
    assert accepted.note.startswith("regen: ")
    record = stored.provenance[accepted.provenance_id]
    assert record.stage is StageName.SENSES
    assert record.note.startswith(f"{MARKER_PREFIX}:{sid}:")


async def test_the_self_target_is_dropped(session):
    # The scripted default answer always proposes an antonym naming the headword itself.
    entry, _ = _entry("abseil", [DEFAULT_GLOSS])
    session.store.write(entry)

    outcome = await run_relation_regen(session.store, session.stages, workers=2)

    assert outcome.dropped_self == 1
    stored = session.store.read("abseil")
    terms = {r.target.term for r in stored.pos_entries[0].senses[0].relations}
    assert "abseil" not in terms


async def test_an_exact_duplicate_within_one_answer_is_dropped(session):
    # The scripted default answer proposes the same synonym twice.
    entry, _ = _entry("abseil", [DEFAULT_GLOSS])
    session.store.write(entry)

    outcome = await run_relation_regen(session.store, session.stages, workers=2)

    assert outcome.dropped_duplicate == 1
    stored = session.store.read("abseil")
    synonyms = [
        r for r in stored.pos_entries[0].senses[0].relations if r.type is RelationType.SYNONYM
    ]
    assert len(synonyms) == 1


async def test_a_previously_tombstoned_target_is_not_reproposed(session):
    entry, _ = _entry("abseil", [DEFAULT_GLOSS], tombstoned=["rejectedword"])
    session.store.write(entry)

    outcome = await run_relation_regen(session.store, session.stages, workers=2)

    # The scripted model echoes the first rejected term it is shown back as a proposal;
    # the post-check must be what keeps it out, not the model declining to offer it.
    assert outcome.dropped_rejected == 1
    stored = session.store.read("abseil")
    terms = {r.target.term for r in stored.pos_entries[0].senses[0].relations}
    assert "rejectedword" not in terms


async def test_rejected_targets_are_parsed_from_tombstone_records_and_from_live_notes():
    entry, sid = _entry("abseil", [DEFAULT_GLOSS], tombstoned=["banners", "crisp abseil"])
    sense = entry.pos_entries[0].senses[0]
    # A live relation a hygiene pass demoted but `relation-reconcile --only tombstone`
    # has not yet removed — the "not expected on a zero-relation sense, but checked for
    # correctness independent of that precondition" source the module docstring names.
    sense.relations.append(
        module.Relation(
            type=RelationType.SEE_ALSO,
            target=module.RelationTarget(term="liveflagged"),
            note="demoted: nano invalid",
        )
    )

    rejected = module._rejected_targets(entry, sense, sid)

    assert set(rejected.terms) == {"banners", "crisp abseil", "liveflagged"}
    assert rejected.slugs == {"banners", "crisp_abseil", "liveflagged"}


async def test_relation_type_cap_is_enforced_within_one_answer(session):
    entry, _ = _entry(RELATION_REGEN_CAP_HEADWORD, [DEFAULT_GLOSS])
    session.store.write(entry)

    outcome = await run_relation_regen(session.store, session.stages, workers=2)

    hypernym_cap = RelationCaps().for_type(RelationType.HYPERNYM)
    assert hypernym_cap == 3
    assert outcome.dropped_capped == 1
    stored = session.store.read(RELATION_REGEN_CAP_HEADWORD)
    hypernyms = [
        r for r in stored.pos_entries[0].senses[0].relations if r.type is RelationType.HYPERNYM
    ]
    assert len(hypernyms) == hypernym_cap


async def test_a_filled_sense_is_never_revisited(session):
    entry, _ = _entry("abseil", [DEFAULT_GLOSS])
    session.store.write(entry)

    first = await run_relation_regen(session.store, session.stages, workers=2)
    assert first.senses_filled == 1

    second = await run_relation_regen(session.store, session.stages, workers=2)
    assert second.senses_scanned == 0
    assert second.calls == 0
    assert plan_relation_regen(session.store.read("abseil")).due is False


# --------------------------------------------------------------------------------------
# Idempotence (D-47)
# --------------------------------------------------------------------------------------


async def test_a_call_that_accepts_nothing_still_writes_a_marker(session):
    entry, sid = _entry(RELATION_REGEN_EMPTY_HEADWORD, [DEFAULT_GLOSS])
    session.store.write(entry)

    outcome = await run_relation_regen(session.store, session.stages, workers=2)

    assert outcome.senses_scanned == 1
    assert outcome.senses_filled == 0
    stored = session.store.read(RELATION_REGEN_EMPTY_HEADWORD)
    assert not stored.pos_entries[0].senses[0].relations
    marker = module._latest_marker(stored, sid)
    assert marker is not None
    assert marker.attempts == 1


async def test_a_second_sweep_over_an_unchanged_gloss_makes_no_call(session):
    entry, _ = _entry(RELATION_REGEN_EMPTY_HEADWORD, [DEFAULT_GLOSS])
    session.store.write(entry)

    await run_relation_regen(session.store, session.stages, workers=2)
    second = await run_relation_regen(session.store, session.stages, workers=2)

    assert second.senses_scanned == 0
    assert second.calls == 0


async def test_a_changed_gloss_earns_one_more_attempt(session):
    entry, sid = _entry(RELATION_REGEN_EMPTY_HEADWORD, [DEFAULT_GLOSS])
    session.store.write(entry)
    await run_relation_regen(session.store, session.stages, workers=2)

    changed = session.store.read(RELATION_REGEN_EMPTY_HEADWORD)
    changed.pos_entries[0].senses[0] = Sense.of(0, "A rewritten definition, changed by hygiene.")
    session.store.write(changed)

    second = await run_relation_regen(session.store, session.stages, workers=2)

    assert second.senses_scanned == 1
    marker = module._latest_marker(session.store.read(RELATION_REGEN_EMPTY_HEADWORD), sid)
    assert marker is not None
    assert marker.attempts == 2


async def test_max_attempts_bounds_retries_even_across_gloss_changes(session):
    entry, sid = _entry(RELATION_REGEN_EMPTY_HEADWORD, [DEFAULT_GLOSS])
    session.store.write(entry)

    for i in range(4):
        current = session.store.read(RELATION_REGEN_EMPTY_HEADWORD)
        current.pos_entries[0].senses[0] = Sense.of(0, f"Definition revision number {i}.")
        session.store.write(current)
        await run_relation_regen(session.store, session.stages, workers=2)

    marker = module._latest_marker(session.store.read(RELATION_REGEN_EMPTY_HEADWORD), sid)
    assert marker is not None
    assert marker.attempts == MAX_ATTEMPTS
    assert MAX_ATTEMPTS == 2


# --------------------------------------------------------------------------------------
# The contract is a strict subset of RelationType
# --------------------------------------------------------------------------------------


def test_the_contract_rejects_a_type_outside_the_allowed_six():
    with pytest.raises(ValidationError):
        module._DraftRegenRelation(type="see_also", term="x", justification="not allowed")


# --------------------------------------------------------------------------------------
# The CLI, and dry-run pricing
# --------------------------------------------------------------------------------------


def test_cli_dry_run_reports_senses_due_and_an_estimated_cost_without_calling_a_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("OPENGLOSS_LOG_DIR", str(tmp_path / "runs"))
    root = tmp_path / "store"
    entry, _ = _entry("abseil", [DEFAULT_GLOSS])
    LexemeStore(StoreConfig(root=root, fsync_on_write=False)).write(entry)

    result = cli_runner.invoke(cli.app, ["relation-regen", "--store", str(root), "--dry-run"])

    assert result.exit_code == 0, result.output
    summary = json.loads(result.stdout)
    assert summary["stop_reason"] == "dry_run"
    assert summary["cost_usd"] == 0.0
    assert summary["calls"] == 0
    assert summary["entries_scanned"] == 1
    assert summary["senses_due"] == 1
    assert summary["estimated_calls"] == 1
    assert summary["estimated_cost_usd"] > 0.0


def test_cli_from_list_restricts_the_sweep(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("OPENGLOSS_LOG_DIR", str(tmp_path / "runs"))
    root = tmp_path / "store"
    store = LexemeStore(StoreConfig(root=root, fsync_on_write=False))
    wanted, _ = _entry("abseil", [DEFAULT_GLOSS])
    skipped, _ = _entry("zephyr", [DEFAULT_GLOSS])
    store.write(wanted)
    store.write(skipped)
    word_list = tmp_path / "words.txt"
    word_list.write_text("abseil\n", encoding="utf-8")

    result = cli_runner.invoke(
        cli.app,
        ["relation-regen", "--store", str(root), "--from-list", str(word_list), "--dry-run"],
    )

    assert result.exit_code == 0, result.output
    summary = json.loads(result.stdout)
    assert summary["entries_scanned"] == 1
    assert summary["senses_due"] == 1
