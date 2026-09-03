"""Example 4: resolve relation targets from surface terms to senses.

Resolution is what turns the word graph into a sense graph. The economics are the point:
a target with no entry in the store is never sent to a model, and a whole source entry's
targets share one call.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from opengloss_generator import prompts
from opengloss_generator.config import AppConfig
from opengloss_generator.contracts import DraftResolution, DraftTargetResolution
from opengloss_generator.schema import Lexeme, Relation, RelationTarget, RelationType, StageName
from opengloss_generator.store import LexemeStore
from opengloss_generator.workflows import resolve
from opengloss_generator.workflows.resolve import resolve_entry, resolve_store
from tests.conftest import make_entry, make_target
from tests.test_retrofit import _Inflight, _sweep_session, _watched_model

# OpenAI caches a prompt prefix only from 1,024 tokens up (docs/CORE-DIARY.md Iteration 2
# finding 3; the same reasoning `RENDITIONS_INSTRUCTIONS` is held to in `test_enrich.py`).
# At ~4.5 characters/token, 4,800 characters clears the floor with room to spare.
_CACHEABLE_PREFIX_CHARS = 4800

#: The cheapest `openai_reasoning_effort` value pydantic-ai's OpenAI Responses settings
#: accept, confirmed live for `gpt-5.4-nano` (`openai_supports_reasoning_effort_none`):
#: it disables reasoning outright, unlike `"minimal"`, which still reasons a little.
_CHEAPEST_REASONING_EFFORT = "none"

#: Every stage whose output is an enum, an integer, a float, or an offset pair -- never
#: prose -- and so should not pay for reasoning tokens it cannot use (D-38).
_NO_REASONING_STAGES = (
    StageName.CLASSIFY_KIND,
    StageName.TAG_DOMAIN,
    StageName.RESOLVE,
    StageName.SPANS,
    StageName.FRONTIER,
)


# --------------------------------------------------------------------------------------
# D-38: instructions long enough to be cached, static, and the cheapest reasoning effort
# --------------------------------------------------------------------------------------


def test_resolve_instructions_clear_the_prompt_cache_minimum():
    assert len(prompts.RESOLVE_INSTRUCTIONS) >= _CACHEABLE_PREFIX_CHARS


def test_resolve_instructions_are_byte_stable():
    # A fresh execution of the module must produce the identical string: no timestamp,
    # no uuid, no set-iteration order can have leaked into the instructions, or the
    # provider's prefix cache silently stops matching between processes.
    spec = importlib.util.spec_from_file_location("prompts_reloaded", Path(prompts.__file__))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.RESOLVE_INSTRUCTIONS == prompts.RESOLVE_INSTRUCTIONS
    assert module.PROMPT_VERSION == prompts.PROMPT_VERSION


def test_resolve_instructions_carry_the_decision_rules_and_output_contract():
    text = prompts.RESOLVE_INSTRUCTIONS
    for required in (
        "DECISION RULES",
        "CONFIDENCE",
        "WORKED EXAMPLE",
        "part of speech",
        "0.85-1.0",
        "below 0.5",
        "nothing else",
    ):
        assert required in text


def test_resolve_contract_has_no_free_text_field():
    # The model should never be invited to write prose: no `reason`/`explanation` field
    # on the per-target answer, only the choice and a bounded confidence (D-38).
    fields = DraftTargetResolution.model_fields
    assert set(fields) == {"target_ref", "sense_choice", "confidence"}
    assert fields["confidence"].annotation is float


def test_scripted_resolution_payload_matches_the_contract():
    # tests/conftest.py's scripted model builds exactly this shape; verifying it here
    # (read-only, per the task's instruction not to edit conftest.py) pins the contract
    # backward-compatible with what a live model and the test harness both send.
    payload = {"resolutions": [{"target_ref": 1, "sense_choice": 0, "confidence": 0.9}]}
    parsed = DraftResolution.model_validate(payload)
    assert parsed.resolutions[0].sense_choice == 0
    assert parsed.resolutions[0].confidence == 0.9


def test_resolve_policy_uses_the_cheapest_reasoning_effort():
    policies = AppConfig().policies
    assert policies[StageName.RESOLVE].reasoning_effort == _CHEAPEST_REASONING_EFFORT


def test_every_non_prose_nano_stage_uses_the_cheapest_reasoning_effort():
    policies = AppConfig().policies
    for stage in _NO_REASONING_STAGES:
        assert policies[stage].reasoning_effort == _CHEAPEST_REASONING_EFFORT, stage


async def test_resolves_only_the_targets_that_exist_in_the_store(session):
    entry = make_entry()  # points at "rappel" and "descend"
    session.store.write(make_target("rappel", senses=2))

    outcome = await resolve_entry(entry, session.store, session.stages)

    relations = {r.target.term: r.target for r in entry.pos_entries[0].senses[0].relations}
    assert relations["rappel"].sense_id == "rappel:verb:0"
    assert relations["rappel"].confidence == 0.9
    # "descend" has no entry, so it was never sent and stays unresolved at zero cost.
    assert relations["descend"].sense_id is None
    assert relations["descend"].confidence is None

    assert outcome.resolved == 1
    assert outcome.absent_targets == 1
    assert outcome.calls == 1
    assert outcome.cost_usd > 0
    assert outcome.entries_changed == ["abseil"]


async def test_resolution_shows_up_on_the_derived_edges(session):
    entry = make_entry()
    session.store.write(make_target("rappel"))
    await resolve_entry(entry, session.store, session.stages)

    edge = next(e for e in entry.edges() if e.target == "rappel")
    # The edge id is built from the target *term*, so resolving does not renumber it.
    assert edge.edge_id == "abseil:verb:0-synonym->rappel"
    assert edge.target_sense == "rappel:verb:0"
    assert edge.confidence == 0.9


async def test_nothing_to_resolve_costs_nothing(session):
    entry = make_entry()  # neither target is in the store
    outcome = await resolve_entry(entry, session.store, session.stages)
    assert outcome.cost_usd == 0.0
    assert outcome.calls == 0
    assert not outcome.changed
    assert session.meter.summary().total_usd == 0.0


async def test_resolving_twice_is_free_the_second_time(session):
    entry = make_entry()
    session.store.write(make_target("rappel"))
    await resolve_entry(entry, session.store, session.stages)
    spent = session.meter.summary().total_usd

    again = await resolve_entry(entry, session.store, session.stages)
    assert again.calls == 0
    assert again.cost_usd == 0.0
    assert session.meter.summary().total_usd == spent


async def test_targets_are_chunked_at_forty_per_call(session):
    entry = make_entry()
    sense = entry.pos_entries[0].senses[0]
    sense.relations.clear()
    for index in range(45):
        term = f"target{index:02d}"
        session.store.write(make_target(term, senses=1))
        sense.relations.append(
            Relation(type=RelationType.SYNONYM, target=RelationTarget(term=term))
        )

    outcome = await resolve_entry(entry, session.store, session.stages)

    assert outcome.resolved == 45
    assert outcome.calls == 2  # 40 + 5
    assert all(r.target.sense_id is not None for r in sense.relations)


async def test_resolve_store_sweeps_and_persists(session):
    session.store.write(make_entry("abseil"))
    session.store.write(make_target("rappel"))
    session.store.write(make_target("descend"))

    outcome = await resolve_store(session.store, session.stages)

    assert outcome.resolved >= 2
    assert "abseil" in outcome.entries_changed
    reloaded = session.store.read("abseil")
    assert reloaded is not None
    relations = reloaded.pos_entries[0].senses[0].relations
    resolved = {r.target.term: r.target.sense_id for r in relations}
    assert resolved == {"rappel": "rappel:verb:0", "descend": "descend:verb:0"}


async def test_resolve_store_honours_a_limit(session):
    session.store.write(make_entry("abseil"))
    session.store.write(make_target("rappel"))
    outcome = await resolve_store(session.store, session.stages, lexeme_ids=["rappel"])
    # "rappel" asserts no relations of its own, so the sweep spends nothing.
    assert outcome.calls == 0
    assert outcome.cost_usd == 0.0


# --------------------------------------------------------------------------------------
# D-31: the sweep runs through the worker pool, locked across each entry's call
# --------------------------------------------------------------------------------------

#: Enough source entries for a pool of 8 to overlap on, few enough to stay quick at 1.
_SWEEP_SIZE = 40

#: A ceiling the sweep reaches part way through; see the note on the retrofit constant.
_TIGHT_BUDGET_USD = 0.01


def _seed_graph(store: LexemeStore, count: int = _SWEEP_SIZE) -> None:
    """Write ``count`` source entries that all point at the same two target entries.

    Sharing the targets is deliberate: every worker reads them at once, which is exactly
    the read pattern the sweep has in production.
    """
    store.write(make_target("rappel"))
    store.write(make_target("descend"))
    for index in range(count):
        store.write(make_entry(f"source{index:02d}"))


def _resolutions(store: LexemeStore) -> dict[str, dict[str, str | None]]:
    """Return every source entry's ``{target term: sense id}``, keyed by entry."""
    resolved: dict[str, dict[str, str | None]] = {}
    for lexeme_id in sorted(store.iter_ids()):
        entry = store.read(lexeme_id)
        assert entry is not None
        for _, sense, _ in entry.iter_senses():
            if sense.relations:
                resolved[lexeme_id] = {r.target.term: r.target.sense_id for r in sense.relations}
    return resolved


async def test_resolve_store_at_eight_workers_matches_one_worker(tmp_path, scripted_model):
    outcomes = {}
    content = {}
    for workers in (1, 8):
        async with _sweep_session(tmp_path, scripted_model, name=f"resolve-w{workers}") as active:
            _seed_graph(active.store)
            outcomes[workers] = await resolve_store(active.store, active.stages, workers=workers)
            content[workers] = _resolutions(active.store)

    one, eight = outcomes[1], outcomes[8]
    assert eight.stopped_reason is None
    assert eight.calls == one.calls == _SWEEP_SIZE
    assert eight.resolved == one.resolved == _SWEEP_SIZE * 2
    assert eight.declined == one.declined
    assert eight.absent_targets == one.absent_targets
    assert eight.cost_usd == pytest.approx(one.cost_usd)
    # Sorted on the way out, so the sweep's report does not depend on completion order.
    assert eight.entries_changed == one.entries_changed == sorted(one.entries_changed)
    assert content[8] == content[1]


async def test_resolve_store_overlaps_its_model_calls(tmp_path, scripted_model):
    inflight = _Inflight()
    async with _sweep_session(
        tmp_path, _watched_model(scripted_model, inflight), name="resolve-peak"
    ) as active:
        _seed_graph(active.store, 24)
        outcome = await resolve_store(active.store, active.stages, workers=8)

    assert outcome.calls == 24
    assert 1 < inflight.peak <= 8


async def test_resolve_store_reads_each_source_entry_under_its_lock(
    tmp_path, scripted_model, monkeypatch
):
    async with _sweep_session(tmp_path, scripted_model, name="resolve-lock") as active:
        store = active.store
        _seed_graph(store, 8)
        sources = {f"source{index:02d}" for index in range(8)}
        real_read = store.read
        unlocked: list[str] = []
        seen: list[str] = []

        def read(headword_or_id: str) -> Lexeme | None:
            if headword_or_id in sources:
                seen.append(headword_or_id)
                if not store.path_for(headword_or_id).with_suffix(".lock").exists():
                    unlocked.append(headword_or_id)
            return real_read(headword_or_id)

        monkeypatch.setattr(store, "read", read)
        await resolve_store(store, active.stages, workers=8)

    # Target entries are read without a lock on purpose -- they are never written here --
    # so only the entry the handler is about to write has to be locked across its call.
    assert sorted(seen) == sorted(sources)
    assert unlocked == []


async def test_resolve_store_reports_a_budget_stop_instead_of_raising(tmp_path, scripted_model):
    async with _sweep_session(
        tmp_path, scripted_model, name="resolve-budget", budget_usd=_TIGHT_BUDGET_USD
    ) as active:
        store = active.store
        _seed_graph(store)
        outcome = await resolve_store(store, active.stages, workers=8)

    assert outcome.stopped_reason == "budget"
    assert 0 < outcome.calls < _SWEEP_SIZE
    # Nothing half-written: no lock left behind, no temp file, every entry still parses,
    # and each source is either fully resolved or wholly untouched.
    assert list(store.root.rglob("*.lock")) == []
    assert [path for path in store.root.rglob(".*") if path.is_file()] == []
    assert store.count() == _SWEEP_SIZE + 2
    for lexeme_id, targets in _resolutions(store).items():
        ids = set(targets.values())
        assert ids in ({None}, {"rappel:verb:0", "descend:verb:0"}), lexeme_id


async def test_resolve_store_defaults_to_the_runners_configured_worker_count(session, monkeypatch):
    seen: list[int] = []
    real_pool = resolve.run_pool

    async def spy(items, handler, *, workers, stop_event=None, fail_fast=False) -> tuple[int, int]:
        seen.append(workers)
        return await real_pool(
            items, handler, workers=workers, stop_event=stop_event, fail_fast=fail_fast
        )

    monkeypatch.setattr(resolve, "run_pool", spy)
    session.store.write(make_entry("abseil"))

    await resolve_store(session.store, session.stages)

    assert seen == [session.config.concurrency.workers]
