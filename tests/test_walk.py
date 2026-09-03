"""Example 2: walk the graph to discover and generate new entries."""

from __future__ import annotations

import pytest

from opengloss_generator.config import StoreConfig
from opengloss_generator.runner import RunSession
from opengloss_generator.schema import LexemeKind, Relation, RelationTarget, RelationType
from opengloss_generator.store import LexemeStore
from opengloss_generator.taxonomy import DomainTag
from opengloss_generator.workflows.walk import SamplingStrategy, WalkSpec, sample_seeds, walk_graph
from tests.conftest import make_entry


def _synonym(term: str) -> Relation:
    """Build a synonym relation, the v3 replacement for appending to ``sense.synonyms``."""
    return Relation(type=RelationType.SYNONYM, target=RelationTarget(term=term))


async def test_walk_generates_dangling_relation_targets(session):
    # The seed points at "rappel" and "descend", neither of which exists yet.
    session.store.write(make_entry("abseil"))

    outcome = await walk_graph(
        WalkSpec(seeds=["abseil"], max_new_entries=10, max_depth=1),
        store=session.store,
        runner=session.stages,
    )

    assert sorted(outcome.generated) == ["descend", "rappel"]
    assert outcome.stop_reason == "frontier_exhausted"
    for lexeme_id in outcome.generated:
        entry = session.store.read(lexeme_id)
        assert entry is not None
        assert entry.discovered_from == "abseil"
        assert entry.sense_count() > 0
    assert outcome.cost_usd > 0
    assert session.meter.summary().total_usd == pytest.approx(outcome.cost_usd)


async def test_walk_stops_at_max_new_entries(session):
    session.store.write(make_entry("abseil"))
    outcome = await walk_graph(
        WalkSpec(seeds=["abseil"], max_new_entries=1, max_depth=3),
        store=session.store,
        runner=session.stages,
    )
    assert outcome.generated_count == 1
    assert outcome.stop_reason == "max_new_entries"


async def test_walk_follows_depth_and_never_regenerates(session):
    session.store.write(make_entry("abseil"))
    # Depth 2: generated entries' own targets (synonym0, broader_thing, ...) are explored.
    outcome = await walk_graph(
        WalkSpec(seeds=["abseil"], max_new_entries=50, max_depth=2),
        store=session.store,
        runner=session.stages,
    )
    assert outcome.generated_count > 2
    assert len(outcome.generated) == len(set(outcome.generated))
    # "shared_target" is named by every scripted sense; it must be generated exactly once.
    assert outcome.generated.count("shared_target") == 1
    assert session.store.count() == outcome.generated_count + 1


async def test_walk_respects_the_budget(config, scripted_model):
    config.budget_usd = 0.002  # enough for roughly one entry with the scripted usage
    async with RunSession(config, model_override=scripted_model, run_id="walk-budget") as s:
        s.store.write(make_entry("abseil"))
        outcome = await walk_graph(
            WalkSpec(seeds=["abseil"], max_new_entries=100, max_depth=3),
            store=s.store,
            runner=s.stages,
        )
    assert outcome.stop_reason == "budget"
    assert s.meter.summary().total_usd <= config.budget_usd
    # Whatever was written is complete; nothing half-generated reaches the store.
    for entry in s.store.iter_entries():
        assert entry.sense_count() > 0


async def test_classifier_rejections_are_recorded(session):
    entry = make_entry("abseil")
    entry.pos_entries[0].senses[0].relations.append(_synonym("reject_me"))
    session.store.write(entry)

    outcome = await walk_graph(
        WalkSpec(seeds=["abseil"], max_new_entries=10, max_depth=1),
        store=session.store,
        runner=session.stages,
    )
    assert "reject_me" not in outcome.generated
    assert outcome.skipped["reject_me"].startswith("classifier:")


async def test_free_filters_run_before_the_classifier(session):
    entry = make_entry("abseil")
    entry.pos_entries[0].senses[0].relations.extend(
        _synonym(term) for term in ("*sneu-", "see also", "abseil")
    )
    session.store.write(entry)

    outcome = await walk_graph(
        WalkSpec(seeds=["abseil"], max_new_entries=10, max_depth=1),
        store=session.store,
        runner=session.stages,
    )
    assert outcome.rejection_counts == {"etymon": 1, "meta_label": 1, "self_reference": 1}


async def test_propose_related_widens_the_frontier(session):
    session.store.write(make_entry("abseil"))
    outcome = await walk_graph(
        WalkSpec(seeds=["abseil"], max_new_entries=10, max_depth=1, propose_related=True),
        store=session.store,
        runner=session.stages,
    )
    assert {"proposed_one", "proposed_two"} <= set(outcome.generated)


async def test_walk_with_empty_store_and_no_seed_does_nothing(session):
    outcome = await walk_graph(WalkSpec(), store=session.store, runner=session.stages)
    assert outcome.stop_reason == "no_seed"
    assert outcome.cost_usd == 0.0


def test_sampling_strategies(tmp_path):
    store = LexemeStore(StoreConfig(root=tmp_path / "s", fsync_on_write=False))
    rich = make_entry("rich")
    rich.pos_entries[0].senses[0].relations.extend(_synonym(t) for t in ("a", "b", "c"))
    store.write(rich)
    store.write(make_entry("poor"))

    assert sample_seeds(store, WalkSpec(seeds=["Rich"])) == ["rich"]
    assert sample_seeds(store, WalkSpec(strategy="least-connected")) == ["poor"]
    random_pick = sample_seeds(store, WalkSpec(strategy="random", rng_seed=1))
    assert random_pick
    assert random_pick[0] in {"rich", "poor"}


def test_domain_deficit_prefers_the_underrepresented_root(tmp_path):
    # This strategy is free (no model call): it only reads sense.domain and computes
    # taxonomy.deficit_table. Root "science" is heavily represented (5 entries); root
    # "arts" has one. Arts is the more deficient root, so its entry is the seed.
    store = LexemeStore(StoreConfig(root=tmp_path / "s", fsync_on_write=False))
    for i in range(5):
        entry = make_entry(f"sci{i}")
        entry.pos_entries[0].senses[0].domain = DomainTag.SCIENCE_PHYSICS
        store.write(entry)
    rare = make_entry("art1")
    rare.pos_entries[0].senses[0].domain = DomainTag.ARTS_MUSIC
    store.write(rare)

    seeds = sample_seeds(
        store, WalkSpec(strategy=SamplingStrategy.DOMAIN_DEFICIT, seed_count=1, rng_seed=0)
    )
    assert seeds == ["art1"]


async def test_domain_deficit_reports_untagged(session):
    tagged = make_entry("tagged1")
    tagged.pos_entries[0].senses[0].domain = DomainTag.SCIENCE_PHYSICS
    session.store.write(tagged)
    session.store.write(make_entry("untagged1"))
    session.store.write(make_entry("untagged2"))

    outcome = await walk_graph(
        WalkSpec(
            strategy=SamplingStrategy.DOMAIN_DEFICIT,
            seed_count=1,
            max_new_entries=1,
            max_depth=1,
            rng_seed=0,
        ),
        store=session.store,
        runner=session.stages,
    )
    # "untagged1" and "untagged2" each have exactly one untagged sense.
    assert outcome.domain_deficit["untagged"] == 2.0
    assert "science" in outcome.domain_deficit
    assert len(outcome.domain_deficit) == 16  # 15 taxonomy roots + "untagged"


async def test_function_word_seeds_do_not_expand_by_default(session):
    entry = make_entry("the")
    entry.kind = LexemeKind.FUNCTION_WORD
    session.store.write(entry)

    outcome = await walk_graph(
        WalkSpec(seeds=["the"], max_new_entries=10, max_depth=2),
        store=session.store,
        runner=session.stages,
    )
    assert outcome.generated == []
    assert outcome.stop_reason == "frontier_exhausted"


async def test_expand_kinds_none_lifts_the_restriction(session):
    entry = make_entry("the")
    entry.kind = LexemeKind.FUNCTION_WORD
    session.store.write(entry)

    outcome = await walk_graph(
        WalkSpec(seeds=["the"], max_new_entries=10, max_depth=2, expand_kinds=None),
        store=session.store,
        runner=session.stages,
    )
    assert outcome.generated
