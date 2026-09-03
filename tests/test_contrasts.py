"""The "X vs Y" contrast stage (D-57).

Two things carry most of the risk here and most of the tests: **who owns a pair**, because
a reciprocated edge is visible from both ends and paying twice for one paragraph is the
easiest possible waste, and **what the sieve refuses**, because the whole claim of the
stage is that what it stores discriminates rather than restates.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from typer.testing import CliRunner

from opengloss_generator import cli
from opengloss_generator.schema import (
    ContrastVerdict,
    Example,
    Lexeme,
    LexemeKind,
    Morphology,
    PartOfSpeech,
    POSEntry,
    Relation,
    RelationTarget,
    RelationType,
    Renditions,
    Sense,
    canonical_rendition,
)
from opengloss_generator.store import LexemeStore
from opengloss_generator.workflows.contrasts import (
    MARKER_PREFIX,
    MAX_EDGES_PER_CALL,
    RejectReason,
    plan_contrasts,
    run_contrasts,
)
from tests.conftest import (
    CONTRASTS_EXTRA_HEADWORD,
    CONTRASTS_GLOSS_COPY_HEADWORD,
    CONTRASTS_ONE_SIDED_HEADWORD,
    CONTRASTS_SHORT_HEADWORD,
    CONTRASTS_UNRELATED_HEADWORD,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

cli_runner = CliRunner()

_GLOSS = "A test meaning written so the contrast pass has something to read."


def _relation(
    term: str,
    *,
    sense_id: str | None,
    relation: RelationType = RelationType.SYNONYM,
) -> Relation:
    """Return one relation, resolved unless ``sense_id`` is ``None``."""
    return Relation(
        type=relation,
        target=RelationTarget(term=term, sense_id=sense_id, confidence=0.9 if sense_id else None),
        note="They are easy to mix up." if relation is RelationType.CONFUSABLE_WITH else None,
    )


def _entry(
    headword: str,
    *,
    gloss: str = _GLOSS,
    relations: Sequence[Relation] = (),
    pos: PartOfSpeech = PartOfSpeech.NOUN,
    retired: bool = False,
) -> Lexeme:
    """Build a one-sense entry carrying the given relations."""
    sense = Sense(
        index=0,
        gloss=Renditions[str](root=[canonical_rendition(gloss)]),
        examples=Renditions[Example](
            root=[canonical_rendition(Example(text=f"Everyone noticed the {headword} at once."))]
        ),
        relations=list(relations),
        retired=retired,
    )
    return Lexeme.empty(
        headword,
        kind=LexemeKind.SIMPLEX,
        pos_entries=[POSEntry(pos=pos, senses=[sense], morphology=Morphology())],
    )


def _sense_of(headword: str, pos: PartOfSpeech = PartOfSpeech.NOUN) -> str:
    """Return the derived sense id of the first sense of a one-sense test entry."""
    return f"{headword}:{pos.value}:0"


def _linked(
    store: LexemeStore,
    near: str,
    far: str,
    *,
    relation: RelationType = RelationType.SYNONYM,
    reciprocal: bool = False,
) -> None:
    """Write two entries linked by a resolved edge, optionally reciprocated."""
    store.write(
        _entry(near, relations=[_relation(far, sense_id=_sense_of(far), relation=relation)])
    )
    back = [_relation(near, sense_id=_sense_of(near), relation=relation)] if reciprocal else []
    store.write(_entry(far, relations=back))


def _contrasts(store: LexemeStore, headword: str) -> list[object]:
    """Return the contrasts one entry holds, as they were written to disk."""
    entry = store.read(headword)
    assert entry is not None
    return list(entry.contrasts)


# --------------------------------------------------------------------------------------
# Eligibility: which edges are worth a paragraph at all
# --------------------------------------------------------------------------------------


async def test_a_resolved_synonym_edge_earns_one_stored_contrast(session):
    _linked(session.store, "alpha", "bravo")

    outcome = await run_contrasts(session.store, session.stages, workers=2)

    assert outcome.calls == 1
    assert outcome.contrasts_stored == 1
    stored = _contrasts(session.store, "alpha")
    assert len(stored) == 1
    contrast = stored[0]
    assert contrast.edge_id == "alpha:noun:0-synonym->bravo"
    assert contrast.target_sense_id == "bravo:noun:0"
    assert contrast.verdict is ContrastVerdict.RELATED_AS_TYPED
    assert "alpha" in contrast.canonical_text()
    assert "bravo" in contrast.canonical_text()


async def test_the_contrast_points_at_the_call_that_paid_for_it(session):
    _linked(session.store, "alpha", "bravo")

    await run_contrasts(session.store, session.stages, workers=2)

    entry = session.store.read("alpha")
    record = entry.provenance[entry.contrasts[0].provenance_id]
    assert record.stage.value == "contrasts"
    assert record.note.startswith(f"{MARKER_PREFIX}:")
    assert record.cost_usd > 0.0


async def test_an_unresolved_target_is_skipped_and_counted(session):
    session.store.write(_entry("alpha", relations=[_relation("bravo", sense_id=None)]))

    outcome = await run_contrasts(session.store, session.stages, workers=2)

    assert outcome.calls == 0
    assert outcome.edges_skipped_unresolved == 1
    assert outcome.edges_skipped_no_target == 0
    assert _contrasts(session.store, "alpha") == []


async def test_a_target_lexeme_absent_from_the_store_is_skipped_and_counted(session):
    session.store.write(
        _entry("alpha", relations=[_relation("bravo", sense_id=_sense_of("bravo"))])
    )

    outcome = await run_contrasts(session.store, session.stages, workers=2)

    assert outcome.calls == 0
    assert outcome.edges_skipped_no_target == 1
    assert outcome.edges_skipped_unresolved == 0


async def test_a_retired_far_sense_is_treated_as_no_target(session):
    session.store.write(
        _entry("alpha", relations=[_relation("bravo", sense_id=_sense_of("bravo"))])
    )
    session.store.write(_entry("bravo", retired=True))

    outcome = await run_contrasts(session.store, session.stages, workers=2)

    assert outcome.calls == 0
    assert outcome.edges_skipped_no_target == 1


async def test_a_relation_type_outside_the_three_is_never_contrasted(session):
    _linked(session.store, "alpha", "bravo", relation=RelationType.HYPERNYM)

    outcome = await run_contrasts(session.store, session.stages, workers=2)

    assert outcome.calls == 0
    assert outcome.edges_skipped_unresolved == 0
    assert outcome.edges_skipped_no_target == 0
    assert outcome.contrasts_stored == 0


async def test_only_kinds_narrows_the_sweep_to_the_types_asked_for(session):
    session.store.write(
        _entry(
            "alpha",
            relations=[
                _relation("bravo", sense_id=_sense_of("bravo")),
                _relation("delta", sense_id=_sense_of("delta"), relation=RelationType.ANTONYM),
            ],
        )
    )
    session.store.write(_entry("bravo"))
    session.store.write(_entry("delta"))

    outcome = await run_contrasts(
        session.store, session.stages, kinds=(RelationType.ANTONYM,), workers=2
    )

    assert outcome.contrasts_stored == 1
    assert _contrasts(session.store, "alpha")[0].edge_id == "alpha:noun:0-antonym->delta"


async def test_a_confusable_edge_is_contrasted_like_the_other_two(session):
    _linked(session.store, "alpha", "bravo", relation=RelationType.CONFUSABLE_WITH)

    outcome = await run_contrasts(session.store, session.stages, workers=2)

    assert outcome.contrasts_stored == 1
    assert _contrasts(session.store, "alpha")[0].edge_id == "alpha:noun:0-confusable_with->bravo"


# --------------------------------------------------------------------------------------
# One contrast per undirected pair
# --------------------------------------------------------------------------------------


async def test_a_reciprocated_pair_is_written_once_on_the_smaller_end(session):
    _linked(session.store, "alpha", "bravo", reciprocal=True)

    outcome = await run_contrasts(session.store, session.stages, workers=2)

    assert outcome.calls == 1
    assert outcome.contrasts_stored == 1
    assert outcome.edges_deferred_to_far_side == 1
    assert len(_contrasts(session.store, "alpha")) == 1
    assert _contrasts(session.store, "bravo") == []


async def test_the_larger_end_defers_whichever_order_the_sweep_visits_them_in(session):
    _linked(session.store, "alpha", "bravo", reciprocal=True)

    outcome = await run_contrasts(
        session.store, session.stages, lexeme_ids=["bravo", "alpha"], workers=1
    )

    assert outcome.contrasts_stored == 1
    assert len(_contrasts(session.store, "alpha")) == 1
    assert _contrasts(session.store, "bravo") == []


async def test_a_one_directional_edge_is_owned_by_the_end_that_asserts_it(session):
    # "zulu" sorts after "alpha", so the lexicographic rule alone would defer this pair to
    # an end that does not carry it — and nobody would ever write it.
    _linked(session.store, "zulu", "alpha", reciprocal=False)

    outcome = await run_contrasts(session.store, session.stages, workers=2)

    assert outcome.edges_deferred_to_far_side == 0
    assert len(_contrasts(session.store, "zulu")) == 1


# --------------------------------------------------------------------------------------
# The sieve
# --------------------------------------------------------------------------------------


async def test_a_paragraph_that_is_only_a_label_is_refused(session):
    _linked(session.store, CONTRASTS_SHORT_HEADWORD, "bravo")

    outcome = await run_contrasts(session.store, session.stages, workers=2)

    assert outcome.calls == 1
    assert outcome.paragraphs_generated == 1
    assert outcome.contrasts_stored == 0
    assert outcome.rejected_by_reason == {RejectReason.TOO_SHORT.value: 1}


async def test_a_paragraph_that_never_names_the_far_term_is_refused(session):
    _linked(session.store, CONTRASTS_ONE_SIDED_HEADWORD, "bravo")

    outcome = await run_contrasts(session.store, session.stages, workers=2)

    assert outcome.contrasts_stored == 0
    assert outcome.rejected_by_reason == {RejectReason.TARGET_ABSENT.value: 1}


async def test_a_paragraph_that_quotes_a_gloss_verbatim_is_refused(session):
    _linked(session.store, CONTRASTS_GLOSS_COPY_HEADWORD, "bravo")

    outcome = await run_contrasts(session.store, session.stages, workers=2)

    assert outcome.contrasts_stored == 0
    assert outcome.rejected_by_reason == {RejectReason.GLOSS_COPY.value: 1}


async def test_a_paragraph_for_a_pair_nobody_asked_about_is_refused(session):
    _linked(session.store, CONTRASTS_EXTRA_HEADWORD, "bravo")

    outcome = await run_contrasts(session.store, session.stages, workers=2)

    assert outcome.paragraphs_generated == 2
    assert outcome.contrasts_stored == 1
    assert outcome.rejected_by_reason == {RejectReason.UNWANTED.value: 1}


# --------------------------------------------------------------------------------------
# Verdicts are recorded, never acted on (D-50)
# --------------------------------------------------------------------------------------


async def test_an_unrelated_verdict_is_stored_and_counted_and_changes_no_relation(session):
    _linked(session.store, CONTRASTS_UNRELATED_HEADWORD, "bravo")

    outcome = await run_contrasts(session.store, session.stages, workers=2)

    assert outcome.verdicts == {ContrastVerdict.UNRELATED.value: 1}
    entry = session.store.read(CONTRASTS_UNRELATED_HEADWORD)
    assert entry.contrasts[0].verdict is ContrastVerdict.UNRELATED
    # The relation itself is untouched: relation hygiene owns relation edits.
    relations = entry.pos_entries[0].senses[0].relations
    assert [r.type for r in relations] == [RelationType.SYNONYM]
    assert relations[0].note is None


async def test_the_summary_reports_the_verdict_histogram(session):
    _linked(session.store, "alpha", "bravo")

    outcome = await run_contrasts(session.store, session.stages, workers=2)

    assert outcome.as_dict()["verdicts"] == {ContrastVerdict.RELATED_AS_TYPED.value: 1}


# --------------------------------------------------------------------------------------
# Idempotence (D-47)
# --------------------------------------------------------------------------------------


async def test_a_second_sweep_over_an_unchanged_entry_is_free(session):
    _linked(session.store, "alpha", "bravo")
    await run_contrasts(session.store, session.stages, workers=2)
    spent = session.meter.summary().total_usd

    again = await run_contrasts(session.store, session.stages, workers=2)

    assert again.entries_scanned == 2
    assert again.entries_changed == 0
    assert again.calls == 0
    assert again.cost_usd == 0.0
    assert session.meter.summary().total_usd == spent


async def test_an_entry_whose_paragraphs_are_all_refused_gets_exactly_one_more_attempt(session):
    _linked(session.store, CONTRASTS_SHORT_HEADWORD, "bravo")

    first = await run_contrasts(session.store, session.stages, workers=2)
    second = await run_contrasts(session.store, session.stages, workers=2)
    third = await run_contrasts(session.store, session.stages, workers=2)

    assert (first.calls, second.calls, third.calls) == (1, 1, 0)
    assert third.cost_usd == 0.0


async def test_a_new_edge_on_a_written_entry_earns_one_more_call(session):
    _linked(session.store, "alpha", "bravo")
    await run_contrasts(session.store, session.stages, workers=2)

    session.store.write(_entry("delta"))
    entry = session.store.read("alpha")
    entry.pos_entries[0].senses[0].relations.append(
        _relation("delta", sense_id=_sense_of("delta"), relation=RelationType.ANTONYM)
    )
    session.store.write(entry)

    again = await run_contrasts(session.store, session.stages, workers=2)

    assert again.calls == 1
    assert again.contrasts_stored == 1
    assert {c.edge_id for c in _contrasts(session.store, "alpha")} == {
        "alpha:noun:0-synonym->bravo",
        "alpha:noun:0-antonym->delta",
    }


async def test_pairs_past_the_cap_are_counted_and_bought_by_the_next_sweep(session):
    targets = [f"target{index:02d}" for index in range(MAX_EDGES_PER_CALL + 2)]
    for target in targets:
        session.store.write(_entry(target))
    session.store.write(
        _entry(
            "zulu",
            relations=[_relation(t, sense_id=_sense_of(t)) for t in targets],
        )
    )

    first = await run_contrasts(session.store, session.stages, lexeme_ids=["zulu"], workers=1)
    assert first.calls == 1
    assert first.contrasts_stored == MAX_EDGES_PER_CALL
    assert first.edges_over_cap == 2

    second = await run_contrasts(session.store, session.stages, lexeme_ids=["zulu"], workers=1)
    assert second.calls == 1
    assert second.contrasts_stored == 2
    assert second.edges_over_cap == 0

    third = await run_contrasts(session.store, session.stages, lexeme_ids=["zulu"], workers=1)
    assert third.calls == 0


# --------------------------------------------------------------------------------------
# Planning
# --------------------------------------------------------------------------------------


async def test_plan_contrasts_prices_an_entry_and_then_reports_it_done(session):
    _linked(session.store, "alpha", "bravo")

    plan = plan_contrasts(session.store.read("alpha"), session.store)
    assert (plan.due, plan.pairs, plan.outstanding) == (True, 1, 1)

    await run_contrasts(session.store, session.stages, workers=2)

    after = plan_contrasts(session.store.read("alpha"), session.store)
    assert after.due is False
    assert after.pairs == 0


def test_plan_contrasts_separates_the_three_reasons_an_edge_costs_nothing(config):
    from opengloss_generator.config import StoreConfig  # noqa: PLC0415 - one test only

    store = LexemeStore(StoreConfig(root=config.store.root, fsync_on_write=False))
    store.write(_entry("bravo", relations=[_relation("alpha", sense_id=_sense_of("alpha"))]))
    store.write(
        _entry(
            "alpha",
            relations=[
                _relation("bravo", sense_id=_sense_of("bravo")),  # reciprocated, owned here
                _relation("charlie", sense_id=None),  # unresolved
                _relation("delta", sense_id=_sense_of("delta")),  # not in the store
            ],
        )
    )

    near = plan_contrasts(store.read("alpha"), store)
    far = plan_contrasts(store.read("bravo"), store)

    assert (near.pairs, near.skipped_unresolved, near.skipped_no_target, near.deferred) == (
        1,
        1,
        1,
        0,
    )
    assert (far.pairs, far.deferred) == (0, 1)


# --------------------------------------------------------------------------------------
# The CLI
# --------------------------------------------------------------------------------------


def test_cli_dry_run_reports_the_plan_without_calling_a_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("OPENGLOSS_LOG_DIR", str(tmp_path / "runs"))
    root = tmp_path / "store"
    from opengloss_generator.config import StoreConfig  # noqa: PLC0415 - one CLI test only

    store = LexemeStore(StoreConfig(root=root, fsync_on_write=False))
    store.write(_entry("alpha", relations=[_relation("bravo", sense_id=_sense_of("bravo"))]))
    store.write(_entry("bravo"))

    result = cli_runner.invoke(cli.app, ["contrasts", "--store", str(root), "--dry-run"])
    assert result.exit_code == 0, result.output
    summary = json.loads(result.stdout)

    assert summary["stop_reason"] == "dry_run"
    assert summary["cost_usd"] == 0.0
    assert summary["entries_scanned"] == 2
    assert summary["entries_due"] == 1
    assert summary["contrasts_planned"] == 1
    assert summary["estimated_calls"] == 1
    assert summary["estimated_cost_usd"] > 0.0


def test_cli_only_kinds_restricts_the_plan(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("OPENGLOSS_LOG_DIR", str(tmp_path / "runs"))
    root = tmp_path / "store"
    from opengloss_generator.config import StoreConfig  # noqa: PLC0415 - one CLI test only

    store = LexemeStore(StoreConfig(root=root, fsync_on_write=False))
    store.write(_entry("alpha", relations=[_relation("bravo", sense_id=_sense_of("bravo"))]))
    store.write(_entry("bravo"))

    result = cli_runner.invoke(
        cli.app, ["contrasts", "--store", str(root), "--only-kinds", "antonym", "--dry-run"]
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["contrasts_planned"] == 0


def test_cli_refuses_a_relation_type_that_does_not_exist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("OPENGLOSS_LOG_DIR", str(tmp_path / "runs"))
    result = cli_runner.invoke(
        cli.app,
        ["contrasts", "--store", str(tmp_path / "store"), "--only-kinds", "nonsense", "--dry-run"],
    )
    assert result.exit_code != 0
