"""Example 6: make the hypernym graph a hierarchy again, deterministically and for $0.

Every test builds its own store under ``tmp_path``. Nothing here calls a model — the
workflow takes ``runner=None`` — so the assertions are about graph shape and stored
relations, never about cost.
"""

from __future__ import annotations

import random
import time
from pathlib import Path

from opengloss_generator.audit import _build_hypernym_graph, _find_hypernym_cycles, audit_store
from opengloss_generator.config import StoreConfig
from opengloss_generator.schema import (
    Lexeme,
    LexemeKind,
    Morphology,
    PartOfSpeech,
    POSEntry,
    Relation,
    RelationTarget,
    RelationType,
    Sense,
    StageName,
)
from opengloss_generator.store import LexemeStore
from opengloss_generator.workflows.graph_hygiene import (
    DEMOTION_MODEL,
    RECIPROCITY_MODEL,
    GraphHygieneOutcome,
    _break_cycles,
    _HypernymGraph,
    _RelationRef,
    _tarjan_scc,
    run_graph_hygiene,
)

WORKERS = 4


def _store(tmp_path: Path) -> LexemeStore:
    """Return an empty store rooted under ``tmp_path``."""
    return LexemeStore(StoreConfig(root=tmp_path / "store", fsync_on_write=False))


def _entry(headword: str, relations: list[Relation] | None = None, *, senses: int = 1) -> Lexeme:
    """Build a noun entry whose sense 0 carries ``relations``."""
    return Lexeme.empty(
        headword,
        kind=LexemeKind.SIMPLEX,
        pos_entries=[
            POSEntry(
                pos=PartOfSpeech.NOUN,
                senses=[
                    Sense.of(
                        index,
                        f"Sense {index} of {headword}, written for the graph tests.",
                        relations=(relations or []) if index == 0 else [],
                    )
                    for index in range(senses)
                ],
                morphology=Morphology(),
            )
        ],
    )


def _hypernym(term: str, sense: str | None, confidence: float | None = 0.9) -> Relation:
    """Build a resolved hypernym relation toward ``sense``."""
    return Relation(
        type=RelationType.HYPERNYM,
        target=RelationTarget(term=term, sense_id=sense, confidence=confidence),
    )


def _relations_of(store: LexemeStore, lexeme_id: str, index: int = 0) -> list[Relation]:
    """Return the stored relations of one sense."""
    entry = store.read(lexeme_id)
    assert entry is not None
    return entry.pos_entries[0].senses[index].relations


def _write(store: LexemeStore, *entries: Lexeme) -> None:
    """Persist every entry."""
    for entry in entries:
        store.write(entry)


def _ref(source: str, target: str, confidence: float) -> _RelationRef:
    """Build the projected hypernym relation behind one graph edge."""
    return _RelationRef(
        lexeme_id=source.split(":", maxsplit=1)[0],
        sense_id=source,
        index=0,
        type=RelationType.HYPERNYM,
        original_type=RelationType.HYPERNYM,
        term=target.split(":", maxsplit=1)[0],
        target_lexeme=target.split(":", maxsplit=1)[0],
        target_sense=target,
        confidence=confidence,
        note=None,
    )


# --------------------------------------------------------------------------------------
# Step 1 — self-loops
# --------------------------------------------------------------------------------------


async def test_a_same_lexeme_hypernym_is_demoted_to_see_also(tmp_path: Path):
    # alpha's sense 0 claims alpha's own sense 1 as its hypernym: nothing is its own
    # hypernym, whichever of its senses does the pointing.
    alpha = _entry("alpha", [_hypernym("alpha", "alpha:noun:1")], senses=2)
    store = _store(tmp_path)
    _write(store, alpha)

    outcome = await run_graph_hygiene(store, None, workers=WORKERS)

    assert outcome.self_loops_demoted == 1
    assert outcome.entries_changed == 1
    relation = _relations_of(store, "alpha")[0]
    assert relation.type is RelationType.SEE_ALSO
    assert relation.note == "demoted: self-loop"
    # Nothing was deleted, and the confidence the resolver recorded survives (D-1).
    assert relation.target.sense_id == "alpha:noun:1"
    assert relation.target.confidence == 0.9


async def test_a_self_loop_asserted_as_a_hyponym_is_demoted_too(tmp_path: Path):
    # The graph folds hyponym relations in reversed, so a same-lexeme hyponym is the same
    # defect asserted the other way round.
    alpha = _entry(
        "alpha",
        [
            Relation(
                type=RelationType.HYPONYM,
                target=RelationTarget(term="alpha", sense_id="alpha:noun:1", confidence=0.7),
            )
        ],
        senses=2,
    )
    store = _store(tmp_path)
    _write(store, alpha)

    outcome = await run_graph_hygiene(store, None, workers=WORKERS)

    assert outcome.self_loops_demoted == 1
    assert _relations_of(store, "alpha")[0].type is RelationType.SEE_ALSO


async def test_an_unresolved_self_loop_is_left_alone(tmp_path: Path):
    # An unresolved target contributes no edge to the graph, so there is nothing to fix.
    alpha = _entry("alpha", [_hypernym("alpha", None)], senses=2)
    store = _store(tmp_path)
    _write(store, alpha)

    outcome = await run_graph_hygiene(store, None, workers=WORKERS)

    assert outcome.self_loops_demoted == 0
    assert _relations_of(store, "alpha")[0].type is RelationType.HYPERNYM


# --------------------------------------------------------------------------------------
# Step 2 — mutual hypernymy
# --------------------------------------------------------------------------------------


async def test_a_mutual_hypernym_pair_becomes_a_synonym_pair(tmp_path: Path):
    # The measured shape: resource <-> supply, high confidence both ways, siblings the
    # model could not order.
    resource = _entry("resource", [_hypernym("supply", "supply:noun:0", 0.87)])
    supply = _entry("supply", [_hypernym("resource", "resource:noun:0", 0.87)])
    store = _store(tmp_path)
    _write(store, resource, supply)

    outcome = await run_graph_hygiene(store, None, workers=WORKERS)

    assert outcome.mutual_demoted == 2
    assert outcome.cycle_edges_demoted == 0
    for lexeme_id, target in (("resource", "supply"), ("supply", "resource")):
        relation = _relations_of(store, lexeme_id)[0]
        assert relation.type is RelationType.SYNONYM
        assert relation.note == "demoted: mutual hypernym"
        assert relation.target.term == target
        assert relation.target.confidence == 0.87


async def test_a_mutual_pair_whose_sense_already_has_that_synonym_is_demoted_to_see_also(
    tmp_path: Path,
):
    # Turning this hypernym into a synonym would give resource:noun:0 two synonym
    # relations toward supply -- one edge id, two edges. It becomes see_also instead, so
    # nothing is lost either way.
    resource = _entry(
        "resource",
        [
            Relation(
                type=RelationType.SYNONYM,
                target=RelationTarget(term="supply", sense_id="supply:noun:0", confidence=0.8),
            ),
            _hypernym("supply", "supply:noun:0", 0.87),
        ],
    )
    supply = _entry("supply", [_hypernym("resource", "resource:noun:0", 0.87)])
    store = _store(tmp_path)
    _write(store, resource, supply)

    outcome = await run_graph_hygiene(store, None, workers=WORKERS)

    assert outcome.mutual_demoted == 2
    demoted = _relations_of(store, "resource")[1]
    assert demoted.type is RelationType.SEE_ALSO
    assert demoted.note == "demoted: mutual hypernym (synonym already present)"
    # The other side had no synonym of its own, so it took the synonym demotion.
    assert _relations_of(store, "supply")[0].type is RelationType.SYNONYM


async def test_a_mutual_pair_asserted_from_one_side_is_still_found(tmp_path: Path):
    # alpha says beta is its hypernym AND that beta is its hyponym. Reversed, the second
    # claim is the edge beta -> alpha: a mutual pair closed from one entry alone.
    alpha = _entry(
        "alpha",
        [
            _hypernym("beta", "beta:noun:0", 0.9),
            Relation(
                type=RelationType.HYPONYM,
                target=RelationTarget(term="beta", sense_id="beta:noun:0", confidence=0.6),
            ),
        ],
    )
    beta = _entry("beta")
    store = _store(tmp_path)
    _write(store, alpha, beta)

    outcome = await run_graph_hygiene(store, None, workers=WORKERS)

    assert outcome.mutual_demoted == 2
    types = [relation.type for relation in _relations_of(store, "alpha")]
    # The first assertion takes the synonym; the second would duplicate it.
    assert types == [RelationType.SYNONYM, RelationType.SEE_ALSO]


# --------------------------------------------------------------------------------------
# Step 3 — remaining cycles
# --------------------------------------------------------------------------------------


async def test_a_three_cycle_is_broken_at_its_lowest_confidence_edge(tmp_path: Path):
    alpha = _entry("alpha", [_hypernym("beta", "beta:noun:0", 0.9)])
    beta = _entry("beta", [_hypernym("gamma", "gamma:noun:0", 0.8)])
    gamma = _entry("gamma", [_hypernym("alpha", "alpha:noun:0", 0.4)])
    store = _store(tmp_path)
    _write(store, alpha, beta, gamma)

    outcome = await run_graph_hygiene(store, None, workers=WORKERS)

    assert outcome.cycle_edges_demoted == 1
    assert outcome.sccs_broken == {"3": 1}
    assert outcome.cycle_edges_by_scc_size == {"3": 1}
    assert _relations_of(store, "gamma")[0].type is RelationType.SEE_ALSO
    assert _relations_of(store, "gamma")[0].note == "demoted: cycle break (conf=0.40)"
    # The two confident edges are untouched.
    assert _relations_of(store, "alpha")[0].type is RelationType.HYPERNYM
    assert _relations_of(store, "beta")[0].type is RelationType.HYPERNYM


async def test_the_out_degree_tie_break_decides_between_equal_confidences(tmp_path: Path):
    # All three edges of the cycle score 0.5, so the tie-break picks the source with the
    # most outgoing hypernyms: beta, which also points at an unrelated sink.
    alpha = _entry("alpha", [_hypernym("beta", "beta:noun:0", 0.5)])
    beta = _entry(
        "beta",
        [
            _hypernym("gamma", "gamma:noun:0", 0.5),
            _hypernym("delta", "delta:noun:0", 0.5),
        ],
    )
    gamma = _entry("gamma", [_hypernym("alpha", "alpha:noun:0", 0.5)])
    delta = _entry("delta")
    store = _store(tmp_path)
    _write(store, alpha, beta, gamma, delta)

    outcome = await run_graph_hygiene(store, None, workers=WORKERS)

    assert outcome.cycle_edges_demoted == 1
    assert _relations_of(store, "beta")[0].type is RelationType.SEE_ALSO
    # The out-of-cycle edge is not a candidate: it lies on no cycle.
    assert _relations_of(store, "beta")[1].type is RelationType.HYPERNYM


async def test_a_cycle_edge_asserted_twice_is_demoted_on_both_sides(tmp_path: Path):
    # gamma -> alpha is asserted by gamma's hypernym AND by alpha's hyponym. Removing the
    # edge has to rewrite both, or the edge survives its own removal.
    alpha = _entry(
        "alpha",
        [
            _hypernym("beta", "beta:noun:0", 0.9),
            Relation(
                type=RelationType.HYPONYM,
                target=RelationTarget(term="gamma", sense_id="gamma:noun:0", confidence=0.4),
            ),
        ],
    )
    beta = _entry("beta", [_hypernym("gamma", "gamma:noun:0", 0.9)])
    gamma = _entry("gamma", [_hypernym("alpha", "alpha:noun:0", 0.4)])
    store = _store(tmp_path)
    _write(store, alpha, beta, gamma)

    outcome = await run_graph_hygiene(store, None, workers=WORKERS)

    assert outcome.cycle_edges_demoted == 2
    assert _relations_of(store, "gamma")[0].type is RelationType.SEE_ALSO
    assert _relations_of(store, "alpha")[1].type is RelationType.SEE_ALSO
    assert audit_store(store).hypernym_cycle_count == 0


async def test_a_large_tangled_component_is_broken_quickly(tmp_path: Path):
    # 200 senses wired into one strongly connected component -- a ring, so it is
    # certainly one SCC, plus 600 random chords to tangle it. The real store's worst
    # component is 2,840 senses; this is the same shape, small enough for a test.
    rng = random.Random(20260902)  # noqa: S311 - a seeded, reproducible test fixture
    size = 200
    names = [f"n{index:03d}" for index in range(size)]
    relations: dict[str, list[Relation]] = {name: [] for name in names}
    for index, name in enumerate(names):
        nxt = names[(index + 1) % size]
        relations[name].append(_hypernym(nxt, f"{nxt}:noun:0", 0.9))
    for _ in range(600):
        source, target = rng.sample(names, 2)
        relations[source].append(
            _hypernym(target, f"{target}:noun:0", round(rng.uniform(0.1, 0.99), 2))
        )

    store = _store(tmp_path)
    _write(store, *(_entry(name, relations[name]) for name in names))

    started = time.monotonic()
    outcome = await run_graph_hygiene(store, None, workers=WORKERS)
    elapsed = time.monotonic() - started

    assert elapsed < 5.0
    assert outcome.cycle_edges_demoted > 0
    assert outcome.sccs_broken == {"100+": 1}
    assert sum(outcome.cycle_edges_by_scc_size.values()) > 0
    # The whole point: what is left is a hierarchy.
    assert audit_store(store).hypernym_cycle_count == 0


async def test_the_graph_is_acyclic_by_audits_own_checker_afterwards(tmp_path: Path):
    # One entry per defect class at once: a self-loop, a mutual pair, and a 4-cycle.
    entries = [
        _entry(
            "alpha",
            [_hypernym("alpha", "alpha:noun:1"), _hypernym("beta", "beta:noun:0")],
            senses=2,
        ),
        _entry("beta", [_hypernym("gamma", "gamma:noun:0", 0.3)]),
        _entry("gamma", [_hypernym("delta", "delta:noun:0")]),
        _entry("delta", [_hypernym("alpha", "alpha:noun:0")]),
        _entry("resource", [_hypernym("supply", "supply:noun:0", 0.87)]),
        _entry("supply", [_hypernym("resource", "resource:noun:0", 0.87)]),
    ]
    store = _store(tmp_path)
    _write(store, *entries)

    outcome = await run_graph_hygiene(store, None, workers=WORKERS)

    assert outcome.self_loops_demoted == 1
    assert outcome.mutual_demoted == 2
    assert outcome.cycle_edges_demoted == 1
    count, examples = _find_hypernym_cycles(_build_hypernym_graph(store.iter_entries()))
    assert (count, examples) == (0, [])


# --------------------------------------------------------------------------------------
# Step 4 — reciprocity
# --------------------------------------------------------------------------------------


async def test_a_one_sided_synonym_gains_its_reverse(tmp_path: Path):
    vow = _entry(
        "vow",
        [
            Relation(
                type=RelationType.SYNONYM,
                target=RelationTarget(term="promise", sense_id="promise:noun:0", confidence=0.75),
            )
        ],
    )
    promise = _entry("promise")
    store = _store(tmp_path)
    _write(store, vow, promise)

    outcome = await run_graph_hygiene(store, None, workers=WORKERS)

    assert outcome.reciprocal_added == {"synonym": 1}
    added = _relations_of(store, "promise")[0]
    assert added.type is RelationType.SYNONYM
    assert added.target.term == "vow"
    assert added.target.sense_id == "vow:noun:0"
    assert added.target.confidence == 0.75
    assert added.note == "reciprocal of vow:noun:0"
    entry = store.read("promise")
    assert entry is not None
    record = entry.provenance[added.provenance_id or ""]
    assert (record.stage, record.model, record.cost_usd) == (
        StageName.HYGIENE,
        RECIPROCITY_MODEL,
        0.0,
    )
    # audit's own reciprocity metric now reads 100% for the type.
    reciprocity = audit_store(store).reciprocity
    assert reciprocity["synonym"] == {"asserted": 2, "reciprocated": 2}


async def test_a_confusable_reverse_carries_the_original_note(tmp_path: Path):
    affect = _entry(
        "affect",
        [
            Relation(
                type=RelationType.CONFUSABLE_WITH,
                target=RelationTarget(term="effect", sense_id="effect:noun:0", confidence=0.9),
                note="One is usually the verb and the other usually the noun.",
            )
        ],
    )
    effect = _entry("effect")
    store = _store(tmp_path)
    _write(store, affect, effect)

    outcome = await run_graph_hygiene(store, None, workers=WORKERS)

    assert outcome.reciprocal_added == {"confusable_with": 1}
    added = _relations_of(store, "effect")[0]
    assert added.note == (
        "reciprocal of affect:noun:0: One is usually the verb and the other usually the noun."
    )


async def test_an_already_reciprocated_pair_gains_nothing(tmp_path: Path):
    vow = _entry(
        "vow",
        [
            Relation(
                type=RelationType.SYNONYM,
                target=RelationTarget(term="promise", sense_id="promise:noun:0"),
            )
        ],
    )
    promise = _entry(
        "promise",
        [Relation(type=RelationType.SYNONYM, target=RelationTarget(term="vow"))],
    )
    store = _store(tmp_path)
    _write(store, vow, promise)

    outcome = await run_graph_hygiene(store, None, workers=WORKERS)

    # The far side's claim need not be resolved to count, exactly as audit measures it.
    assert outcome.reciprocal_added == {}
    assert len(_relations_of(store, "promise")) == 1


async def test_two_senses_pointing_at_one_target_add_a_single_reciprocal(tmp_path: Path):
    vow = Lexeme.empty(
        "vow",
        kind=LexemeKind.SIMPLEX,
        pos_entries=[
            POSEntry(
                pos=PartOfSpeech.NOUN,
                senses=[
                    Sense.of(
                        index,
                        f"Sense {index} of vow, written for the reciprocity test.",
                        relations=[
                            Relation(
                                type=RelationType.SYNONYM,
                                target=RelationTarget(term="promise", sense_id="promise:noun:0"),
                            )
                        ],
                    )
                    for index in range(2)
                ],
                morphology=Morphology(),
            )
        ],
    )
    promise = _entry("promise")
    store = _store(tmp_path)
    _write(store, vow, promise)

    outcome = await run_graph_hygiene(store, None, workers=WORKERS)

    assert outcome.reciprocal_added == {"synonym": 1}
    assert len(_relations_of(store, "promise")) == 1


async def test_an_unresolved_symmetric_relation_gains_nothing(tmp_path: Path):
    vow = _entry(
        "vow",
        [Relation(type=RelationType.SYNONYM, target=RelationTarget(term="promise"))],
    )
    promise = _entry("promise")
    store = _store(tmp_path)
    _write(store, vow, promise)

    outcome = await run_graph_hygiene(store, None, workers=WORKERS)

    assert outcome.reciprocal_added == {}
    assert _relations_of(store, "promise") == []


async def test_a_demoted_mutual_pair_is_already_reciprocal_and_gains_nothing(tmp_path: Path):
    # Step 2 makes both sides synonyms of each other, so step 4 must see the pair as it
    # will be left on disk, not as it was read.
    resource = _entry("resource", [_hypernym("supply", "supply:noun:0", 0.87)])
    supply = _entry("supply", [_hypernym("resource", "resource:noun:0", 0.87)])
    store = _store(tmp_path)
    _write(store, resource, supply)

    outcome = await run_graph_hygiene(store, None, workers=WORKERS)

    assert outcome.mutual_demoted == 2
    assert outcome.reciprocal_added == {}
    assert len(_relations_of(store, "resource")) == 1
    assert len(_relations_of(store, "supply")) == 1


# --------------------------------------------------------------------------------------
# Dry run, idempotence, provenance
# --------------------------------------------------------------------------------------


async def test_a_dry_run_plans_everything_and_writes_nothing(tmp_path: Path):
    alpha = _entry("alpha", [_hypernym("alpha", "alpha:noun:1")], senses=2)
    vow = _entry(
        "vow",
        [
            Relation(
                type=RelationType.SYNONYM,
                target=RelationTarget(term="promise", sense_id="promise:noun:0"),
            )
        ],
    )
    promise = _entry("promise")
    store = _store(tmp_path)
    _write(store, alpha, vow, promise)
    before = {path: path.read_bytes() for path in sorted(store.root.rglob("*.json"))}

    outcome = await run_graph_hygiene(store, None, workers=WORKERS, dry_run=True)

    assert outcome.dry_run is True
    assert outcome.self_loops_demoted == 1
    assert outcome.reciprocal_added == {"synonym": 1}
    assert outcome.entries_changed == 2
    assert {path: path.read_bytes() for path in sorted(store.root.rglob("*.json"))} == before


async def test_a_second_sweep_finds_nothing(tmp_path: Path):
    entries = [
        _entry(
            "alpha",
            [_hypernym("alpha", "alpha:noun:1"), _hypernym("beta", "beta:noun:0")],
            senses=2,
        ),
        _entry("beta", [_hypernym("gamma", "gamma:noun:0", 0.3)]),
        _entry("gamma", [_hypernym("alpha", "alpha:noun:0")]),
        _entry("resource", [_hypernym("supply", "supply:noun:0", 0.87)]),
        _entry(
            "supply",
            [
                _hypernym("resource", "resource:noun:0", 0.87),
                Relation(
                    type=RelationType.ANTONYM,
                    target=RelationTarget(term="demand", sense_id="demand:noun:0"),
                ),
            ],
        ),
        _entry("demand"),
    ]
    store = _store(tmp_path)
    _write(store, *entries)

    first = await run_graph_hygiene(store, None, workers=WORKERS)
    assert first.changed is True
    after_first = {path: path.read_bytes() for path in sorted(store.root.rglob("*.json"))}

    second = await run_graph_hygiene(store, None, workers=WORKERS)

    assert second.changed is False
    assert second == GraphHygieneOutcome(
        entries_scanned=first.entries_scanned,
        hypernym_edges=second.hypernym_edges,
    )
    assert second.entries_changed == 0
    # Byte-identical: a no-op sweep does not even bump `updated_at`.
    assert {path: path.read_bytes() for path in sorted(store.root.rglob("*.json"))} == after_first


async def test_every_demotion_on_one_entry_shares_one_zero_cost_provenance_record(
    tmp_path: Path,
):
    alpha = _entry(
        "alpha",
        [_hypernym("alpha", "alpha:noun:1"), _hypernym("alpha", "alpha:noun:2")],
        senses=3,
    )
    store = _store(tmp_path)
    _write(store, alpha)

    await run_graph_hygiene(store, None, workers=WORKERS)

    entry = store.read("alpha")
    assert entry is not None
    ids = {relation.provenance_id for relation in entry.pos_entries[0].senses[0].relations}
    assert len(ids) == 1
    record = entry.provenance[ids.pop() or ""]
    assert (record.stage, record.model) == (StageName.HYGIENE, DEMOTION_MODEL)
    assert (record.cost_usd, record.input_tokens, record.output_tokens) == (0.0, 0, 0)


async def test_an_empty_store_is_a_clean_no_op(tmp_path: Path):
    outcome = await run_graph_hygiene(_store(tmp_path), None, workers=WORKERS)

    assert outcome == GraphHygieneOutcome()
    assert outcome.as_dict()["hypernym_edges"] == 0


async def test_retired_senses_are_never_read_or_written(tmp_path: Path):
    # A retired sense is a tombstone: audit skips it when it builds the graph, and so
    # must the repair, or the pass would resurrect a link nobody wants.
    alpha = _entry("alpha", [_hypernym("beta", "beta:noun:0")])
    alpha.pos_entries[0].senses[0].retired = True
    beta = _entry("beta", [_hypernym("alpha", "alpha:noun:0")])
    store = _store(tmp_path)
    _write(store, alpha, beta)

    outcome = await run_graph_hygiene(store, None, workers=WORKERS)

    assert outcome.changed is False
    assert _relations_of(store, "beta")[0].type is RelationType.HYPERNYM


def test_the_cycle_breaker_only_ever_removes_edges_that_lay_on_a_cycle():
    # Fifty random digraphs, checked for the two properties the demotions rest on: what
    # is left is acyclic, and no edge outside a non-trivial component was touched --
    # a low-confidence edge that lies on no cycle is not a defect and must survive.
    for trial in range(50):
        rng = random.Random(trial)  # noqa: S311 - a seeded, reproducible test fixture
        names = [f"x{index:02d}:noun:0" for index in range(rng.randrange(3, 40))]
        edges = {
            (source, target): [_ref(source, target, round(rng.uniform(0.1, 1.0), 2))]
            for source, target in (
                (rng.choice(names), rng.choice(names))
                for _ in range(rng.randrange(len(names), 4 * len(names)))
            )
            if source != target
        }
        graph = _HypernymGraph()
        graph.assertions = dict(edges)
        successors: dict[str, set[str]] = {}
        for source, target in edges:
            successors.setdefault(source, set()).add(target)
            successors.setdefault(target, set())
        graph.successors = {node: sorted(seen) for node, seen in sorted(successors.items())}
        tangled = {
            node
            for component in _tarjan_scc(sorted(graph.successors), graph.successors)
            if len(component) > 1
            for node in component
        }

        removed, _, per_bucket = _break_cycles(
            graph, out_degree={node: len(seen) for node, seen in graph.successors.items()}
        )

        assert [
            component
            for component in _tarjan_scc(sorted(graph.successors), graph.successors)
            if len(component) > 1
        ] == []
        gone = set(edges) - set(graph.assertions)
        assert all(source in tangled and target in tangled for source, target in gone)
        assert sum(len(refs) for refs in removed) == len(gone)
        assert sum(per_bucket.values()) == len(gone)


async def test_reciprocity_does_not_restore_a_pair_a_hygiene_pass_demoted(tmp_path: Path):
    """A ``see_also`` with a ``demoted:`` note blocks the far side's reciprocal (D-50)."""
    store = _store(tmp_path)
    # B still asserts a resolved synonym toward A; A's own synonym toward B was demoted.
    a = _entry("alpha", [])
    a.pos_entries[0].senses[0].relations.append(
        Relation(
            type=RelationType.SEE_ALSO,
            target=RelationTarget(term="beta", sense_id="beta:noun:0", confidence=0.9),
            note="demoted: nano invalid",
        )
    )
    b = _entry(
        "beta",
        [
            Relation(
                type=RelationType.SYNONYM,
                target=RelationTarget(term="alpha", sense_id="alpha:noun:0", confidence=0.9),
            )
        ],
    )
    store.write(a)
    store.write(b)

    outcome = await run_graph_hygiene(store, None, workers=WORKERS)

    alpha = store.read("alpha")
    assert alpha is not None
    synonyms = [
        r
        for r in alpha.pos_entries[0].senses[0].relations
        if r.type is RelationType.SYNONYM and r.target.lexeme_id == "beta"
    ]
    assert synonyms == []
    assert outcome.reciprocal_added.get("synonym", 0) == 0
