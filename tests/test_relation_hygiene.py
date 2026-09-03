"""Relation hygiene: is the edge true, not merely well-shaped.

Companion to ``test_content_hygiene.py``, which covers the relation *contradictions*, and
to ``test_graph_hygiene.py``, which covers the graph's shape. Everything here is about the
defect the QA judge measured at 44.9% of edges: a relation whose target is not the thing
the type claims it is, or is not a lexical unit at all.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from opengloss_generator.config import AppConfig, ConcurrencyConfig, StoreConfig
from opengloss_generator.router import estimate_tokens
from opengloss_generator.runner import RunSession
from opengloss_generator.schema import (
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
from opengloss_generator.workflows import relation_hygiene as module
from opengloss_generator.workflows.relation_hygiene import (
    FAR_SIDE_NOTE_PREFIX,
    HEADWORD_INFLECTION_NOTE,
    HEADWORD_PHRASE_NOTE,
    MAX_REFS_PER_CALL,
    META_LABEL_NOTE,
    NANO_INVALID_NOTE,
    NANO_RETYPE_NOTE,
    RELATION_VALIDITY_INSTRUCTIONS,
    SIBLING_INFLECTION_NOTE,
    RelationHygieneStep,
    is_meta_label,
    run_relation_hygiene,
)
from tests.conftest import (
    RELATION_INVALID_TARGET,
    RELATION_RETYPE_TARGET,
    RELATION_RETYPE_TO,
)

DEFAULT_GLOSS = "A test definition written for the pass under test."

#: Every free step is meant to leave ``see_also`` alone, so a fixture that wants a
#: relation to survive uses a type no free step touches for the reason under test.
FREE_STEPS = {
    RelationHygieneStep.INFLECTIONS,
    RelationHygieneStep.HEADWORD_PHRASES,
    RelationHygieneStep.META_LABELS,
}


def _entry(
    headword: str,
    *,
    relations: list[Relation] | None = None,
    gloss: str = DEFAULT_GLOSS,
    kind: LexemeKind = LexemeKind.SIMPLEX,
    pos: PartOfSpeech = PartOfSpeech.NOUN,
    morphology: Morphology | None = None,
) -> Lexeme:
    """Build a one-sense entry carrying whatever relations the test under way needs."""
    sense = Sense(
        index=0,
        gloss=Renditions[str](root=[canonical_rendition(gloss)]),
        examples=Renditions[Example](
            root=[canonical_rendition(Example(text=f"A sentence about the {headword} here."))]
        ),
        relations=relations or [],
    )
    return Lexeme.empty(
        headword,
        kind=kind,
        pos_entries=[POSEntry(pos=pos, senses=[sense], morphology=morphology or Morphology())],
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


def _by_term(entry: Lexeme) -> dict[str, Relation]:
    """Return the first sense's relations keyed by their target term."""
    return {relation.target.term: relation for relation in _relations_of(entry)}


# --------------------------------------------------------------------------------------
# Step 1 — inflections
# --------------------------------------------------------------------------------------


async def test_a_plural_of_the_headword_is_demoted_for_free(session):
    session.store.write(_entry("stay", relations=[_relation(RelationType.SYNONYM, "stays")]))

    outcome = await run_relation_hygiene(
        session.store, session.stages, workers=4, only={RelationHygieneStep.INFLECTIONS}
    )

    result = outcome.steps[RelationHygieneStep.INFLECTIONS]
    assert result.demoted == 1
    assert result.calls == 0
    assert result.cost_usd == 0.0

    relation = _relations_of(session.store.read("stay"))[0]
    assert relation.type is RelationType.SEE_ALSO
    assert relation.note == HEADWORD_INFLECTION_NOTE


async def test_a_morphology_form_of_the_headword_is_demoted(session):
    # "went" is not a form ``generate_forms`` can produce; the morphology block is the
    # only place it can come from, which is why both sources are unioned.
    session.store.write(
        _entry(
            "go",
            pos=PartOfSpeech.VERB,
            relations=[_relation(RelationType.SYNONYM, "went")],
            morphology=Morphology(past_tense="went"),
        )
    )

    await run_relation_hygiene(
        session.store, session.stages, workers=4, only={RelationHygieneStep.INFLECTIONS}
    )

    assert _relations_of(session.store.read("go"))[0].type is RelationType.SEE_ALSO


async def test_an_inflection_of_a_sibling_target_is_demoted_and_the_base_kept(session):
    session.store.write(
        _entry(
            "banner",
            relations=[
                _relation(RelationType.SYNONYM, "flag"),
                _relation(RelationType.SYNONYM, "flags"),
            ],
        )
    )

    outcome = await run_relation_hygiene(
        session.store, session.stages, workers=4, only={RelationHygieneStep.INFLECTIONS}
    )

    assert outcome.steps[RelationHygieneStep.INFLECTIONS].demoted == 1
    relations = _by_term(session.store.read("banner"))
    assert relations["flag"].type is RelationType.SYNONYM
    assert relations["flags"].type is RelationType.SEE_ALSO
    assert relations["flags"].note == f"{SIBLING_INFLECTION_NOTE}flag"


async def test_a_sibling_inflection_of_another_type_is_left_alone(session):
    # The pair is only a duplicate when both assertions are the same claim; a synonym and
    # a hypernym toward two forms of one word are two different claims.
    session.store.write(
        _entry(
            "banner",
            relations=[
                _relation(RelationType.SYNONYM, "flag"),
                _relation(RelationType.HYPERNYM, "flags"),
            ],
        )
    )

    await run_relation_hygiene(
        session.store, session.stages, workers=4, only={RelationHygieneStep.INFLECTIONS}
    )

    assert _by_term(session.store.read("banner"))["flags"].type is RelationType.HYPERNYM


async def test_a_derivation_relation_is_exempt_from_the_inflection_rule(session):
    session.store.write(_entry("stay", relations=[_relation(RelationType.DERIVATION, "stays")]))

    outcome = await run_relation_hygiene(
        session.store, session.stages, workers=4, only={RelationHygieneStep.INFLECTIONS}
    )

    assert outcome.steps[RelationHygieneStep.INFLECTIONS].demoted == 0
    assert _relations_of(session.store.read("stay"))[0].type is RelationType.DERIVATION


async def test_an_unrelated_target_survives_the_inflection_rule(session):
    session.store.write(_entry("stay", relations=[_relation(RelationType.SYNONYM, "remain")]))

    await run_relation_hygiene(
        session.store, session.stages, workers=4, only={RelationHygieneStep.INFLECTIONS}
    )

    assert _relations_of(session.store.read("stay"))[0].type is RelationType.SYNONYM


# --------------------------------------------------------------------------------------
# Step 2 — headword_phrases
# --------------------------------------------------------------------------------------


async def test_a_modifier_phrase_on_the_headword_is_demoted_for_free(session):
    session.store.write(
        _entry(
            "benjamin",
            relations=[
                _relation(RelationType.HYPONYM, "crisp benjamin"),
                _relation(RelationType.HYPONYM, "counterfeit benjamin"),
                _relation(RelationType.HYPERNYM, "banknote"),
            ],
        )
    )

    outcome = await run_relation_hygiene(
        session.store, session.stages, workers=4, only={RelationHygieneStep.HEADWORD_PHRASES}
    )

    result = outcome.steps[RelationHygieneStep.HEADWORD_PHRASES]
    assert result.demoted == 2
    assert result.calls == 0

    relations = _by_term(session.store.read("benjamin"))
    assert relations["crisp benjamin"].type is RelationType.SEE_ALSO
    assert relations["crisp benjamin"].note == HEADWORD_PHRASE_NOTE
    assert relations["counterfeit benjamin"].type is RelationType.SEE_ALSO
    assert relations["banknote"].type is RelationType.HYPERNYM


async def test_a_phrase_that_is_itself_an_entry_is_kept(session):
    session.store.write(_entry("ice axe"))
    session.store.write(_entry("ice", relations=[_relation(RelationType.HYPONYM, "ice axe")]))

    outcome = await run_relation_hygiene(
        session.store, session.stages, workers=4, only={RelationHygieneStep.HEADWORD_PHRASES}
    )

    result = outcome.steps[RelationHygieneStep.HEADWORD_PHRASES]
    assert result.demoted == 0
    assert result.rejected == 1  # the exception is counted, not silent
    assert _by_term(session.store.read("ice"))["ice axe"].type is RelationType.HYPONYM


async def test_a_collocation_on_the_headword_is_exempt(session):
    session.store.write(
        _entry(
            "vow",
            relations=[
                _relation(RelationType.COLLOCATION, "solemn vow"),
                _relation(RelationType.HYPONYM, "solemn vow"),
            ],
        )
    )

    await run_relation_hygiene(
        session.store, session.stages, workers=4, only={RelationHygieneStep.HEADWORD_PHRASES}
    )

    types = [relation.type for relation in _relations_of(session.store.read("vow"))]
    assert types == [RelationType.COLLOCATION, RelationType.SEE_ALSO]


async def test_a_single_word_target_is_never_a_modifier_phrase(session):
    # "benjamins" contains the headword but is one word: it is the inflection step's
    # business, not this one's.
    entry = _entry("benjamin", relations=[_relation(RelationType.HYPONYM, "benjamins")])
    session.store.write(entry)

    outcome = await run_relation_hygiene(
        session.store, session.stages, workers=4, only={RelationHygieneStep.HEADWORD_PHRASES}
    )

    assert outcome.steps[RelationHygieneStep.HEADWORD_PHRASES].demoted == 0


async def test_a_multi_word_target_without_the_headword_survives(session):
    session.store.write(_entry("ivy", relations=[_relation(RelationType.HYPONYM, "indoor plant")]))

    await run_relation_hygiene(
        session.store, session.stages, workers=4, only={RelationHygieneStep.HEADWORD_PHRASES}
    )

    # Not a rule's business — this is exactly what the nano step is for.
    assert _relations_of(session.store.read("ivy"))[0].type is RelationType.HYPONYM


# --------------------------------------------------------------------------------------
# Step 3 — meta_labels
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "term",
    ["slang term", "Slang Term", "modifier", "biblical name", "popular given name", "plural form"],
)
def test_a_meta_label_is_recognised(term):
    assert is_meta_label(term)


@pytest.mark.parametrize("term", ["life form", "code name", "indoor plant", "banknote", "vow"])
def test_a_real_lexical_unit_is_not_a_meta_label(term):
    assert not is_meta_label(term)


async def test_meta_label_targets_are_demoted_for_free(session):
    session.store.write(
        _entry(
            "benjamin",
            relations=[
                _relation(RelationType.SYNONYM, "slang term"),
                _relation(RelationType.HYPERNYM, "banknote"),
            ],
        )
    )

    outcome = await run_relation_hygiene(
        session.store, session.stages, workers=4, only={RelationHygieneStep.META_LABELS}
    )

    result = outcome.steps[RelationHygieneStep.META_LABELS]
    assert result.demoted == 1
    assert result.calls == 0

    relations = _by_term(session.store.read("benjamin"))
    assert relations["slang term"].type is RelationType.SEE_ALSO
    assert relations["slang term"].note == META_LABEL_NOTE
    assert relations["banknote"].type is RelationType.HYPERNYM


# --------------------------------------------------------------------------------------
# Step 4 — validity (nano)
# --------------------------------------------------------------------------------------


async def test_the_nano_step_demotes_what_the_model_calls_invalid(session):
    session.store.write(
        _entry(
            "benjamin",
            relations=[
                _relation(RelationType.HYPONYM, RELATION_INVALID_TARGET),
                _relation(RelationType.HYPERNYM, "banknote"),
            ],
        )
    )

    outcome = await run_relation_hygiene(
        session.store, session.stages, workers=4, only={RelationHygieneStep.VALIDITY}
    )

    result = outcome.steps[RelationHygieneStep.VALIDITY]
    assert result.calls == 1
    assert result.cost_usd > 0.0
    assert result.demoted == 1
    assert result.retyped == 0
    assert result.accepted == 2

    relations = _by_term(session.store.read("benjamin"))
    assert relations[RELATION_INVALID_TARGET].type is RelationType.SEE_ALSO
    assert relations[RELATION_INVALID_TARGET].note == NANO_INVALID_NOTE
    assert relations["banknote"].type is RelationType.HYPERNYM


async def test_the_nano_step_retypes_rather_than_demotes_when_a_better_type_is_given(session):
    session.store.write(
        _entry("benjamin", relations=[_relation(RelationType.ANTONYM, RELATION_RETYPE_TARGET)])
    )

    outcome = await run_relation_hygiene(
        session.store, session.stages, workers=4, only={RelationHygieneStep.VALIDITY}
    )

    result = outcome.steps[RelationHygieneStep.VALIDITY]
    assert result.retyped == 1
    assert result.demoted == 0

    relation = _relations_of(session.store.read("benjamin"))[0]
    assert relation.type is RelationType.HYPERNYM
    assert relation.note == f"{NANO_RETYPE_NOTE}antonym→{RELATION_RETYPE_TO}"


async def test_a_see_also_relation_is_never_put_to_the_model(session):
    session.store.write(
        _entry("benjamin", relations=[_relation(RelationType.SEE_ALSO, RELATION_INVALID_TARGET)])
    )

    outcome = await run_relation_hygiene(
        session.store, session.stages, workers=4, only={RelationHygieneStep.VALIDITY}
    )

    assert outcome.steps[RelationHygieneStep.VALIDITY].calls == 0
    assert outcome.cost_usd == 0.0


async def test_the_prompt_carries_the_target_gloss_when_the_relation_is_resolved(session):
    target = _entry("banknote", gloss="A piece of paper money issued by a bank.")
    session.store.write(target)
    session.store.write(
        _entry(
            "benjamin",
            relations=[
                _relation(RelationType.HYPERNYM, "banknote", sense_id="banknote:noun:0"),
                _relation(RelationType.HYPERNYM, "nowhere"),
            ],
        )
    )

    entry = session.store.read("benjamin")
    refs = module._collect_refs(entry, session.store, {})
    prompt = module._build_validity_prompt(entry.headword, refs)

    assert "A piece of paper money issued by a bank." in prompt
    assert module.UNRESOLVED_GLOSS in prompt
    # The asserting sense's own gloss is printed once, not once per relation.
    assert prompt.count(DEFAULT_GLOSS) == 1


async def test_a_long_relation_set_is_chunked_at_the_cap(session):
    relations = [
        _relation(RelationType.HYPERNYM, f"target{index}") for index in range(MAX_REFS_PER_CALL + 1)
    ]
    session.store.write(_entry("benjamin", relations=relations))

    outcome = await run_relation_hygiene(
        session.store, session.stages, workers=4, only={RelationHygieneStep.VALIDITY}
    )

    result = outcome.steps[RelationHygieneStep.VALIDITY]
    assert result.calls == 2
    assert result.accepted == MAX_REFS_PER_CALL + 1
    assert result.demoted == 0


def test_the_instructions_are_long_enough_to_cache():
    # A provider prompt cache needs 1,024 tokens before it will match at all, and the
    # rubric has to define every relation type it asks the model to choose between.
    assert estimate_tokens("", RELATION_VALIDITY_INSTRUCTIONS, 0) >= 1_100
    for member in RelationType:
        assert f'"{member.value}"' in RELATION_VALIDITY_INSTRUCTIONS


# --------------------------------------------------------------------------------------
# Far-side reciprocity (D-50's amendment)
# --------------------------------------------------------------------------------------
#
# A demotion of a symmetric relation type (synonym, antonym, confusable_with) toward a
# resolved target lexeme can leave that lexeme asserting the reverse of a pair this pass
# just judged invalid. These exercise the far-side phase through the ``inflections`` step
# (free, deterministic) and once more through ``validity`` (nano), to prove the same
# mechanism is wired into both a rule-based demotion and a model-driven one.


def _two_sense_entry(
    headword: str, relations0: list[Relation], relations1: list[Relation]
) -> Lexeme:
    """Build a two-sense noun entry, one relation list per sense."""
    senses = [
        Sense(
            index=index,
            gloss=Renditions[str](root=[canonical_rendition(f"{DEFAULT_GLOSS} ({index})")]),
            examples=Renditions[Example](
                root=[canonical_rendition(Example(text=f"A sentence about the {headword} here."))]
            ),
            relations=relations,
        )
        for index, relations in enumerate((relations0, relations1))
    ]
    return Lexeme.empty(
        headword,
        kind=LexemeKind.SIMPLEX,
        pos_entries=[POSEntry(pos=PartOfSpeech.NOUN, senses=senses, morphology=Morphology())],
    )


async def test_near_side_synonym_demotion_demotes_the_far_side_reciprocal(session):
    session.store.write(_entry("stay", relations=[_relation(RelationType.SYNONYM, "stays")]))
    session.store.write(
        _entry("stays", relations=[_relation(RelationType.SYNONYM, "stay", sense_id="stay:noun:0")])
    )

    outcome = await run_relation_hygiene(
        session.store, session.stages, workers=4, only={RelationHygieneStep.INFLECTIONS}
    )

    result = outcome.steps[RelationHygieneStep.INFLECTIONS]
    assert result.demoted == 2
    assert result.far_side_demoted == 1
    assert outcome.far_side_demoted == 1

    near = _relations_of(session.store.read("stay"))[0]
    assert near.type is RelationType.SEE_ALSO
    assert near.note == HEADWORD_INFLECTION_NOTE

    far = _relations_of(session.store.read("stays"))[0]
    assert far.type is RelationType.SEE_ALSO
    assert far.note == f"{FAR_SIDE_NOTE_PREFIX}stay:noun:0 (inflection of headword)"


async def test_a_far_side_relation_toward_a_different_sense_of_a_is_untouched(session):
    session.store.write(
        _two_sense_entry(
            "stay",
            [_relation(RelationType.SYNONYM, "stays")],
            [],
        )
    )
    session.store.write(
        _entry("stays", relations=[_relation(RelationType.SYNONYM, "stay", sense_id="stay:noun:1")])
    )

    outcome = await run_relation_hygiene(
        session.store, session.stages, workers=4, only={RelationHygieneStep.INFLECTIONS}
    )

    result = outcome.steps[RelationHygieneStep.INFLECTIONS]
    assert result.demoted == 1
    assert result.far_side_demoted == 0

    near = session.store.read("stay").pos_entries[0].senses[0].relations[0]
    assert near.type is RelationType.SEE_ALSO

    far = _relations_of(session.store.read("stays"))[0]
    assert far.type is RelationType.SYNONYM


async def test_an_asymmetric_relation_type_has_no_far_side(session):
    session.store.write(_entry("cat", relations=[_relation(RelationType.HYPERNYM, "cats")]))
    session.store.write(
        _entry("cats", relations=[_relation(RelationType.HYPERNYM, "cat", sense_id="cat:noun:0")])
    )

    outcome = await run_relation_hygiene(
        session.store, session.stages, workers=4, only={RelationHygieneStep.INFLECTIONS}
    )

    result = outcome.steps[RelationHygieneStep.INFLECTIONS]
    assert result.demoted == 1
    assert result.far_side_demoted == 0
    assert outcome.far_side_demoted == 0

    far = _relations_of(session.store.read("cats"))[0]
    assert far.type is RelationType.HYPERNYM


async def test_a_second_run_of_the_far_side_phase_is_a_noop(session):
    session.store.write(_entry("stay", relations=[_relation(RelationType.SYNONYM, "stays")]))
    session.store.write(
        _entry("stays", relations=[_relation(RelationType.SYNONYM, "stay", sense_id="stay:noun:0")])
    )
    only = {RelationHygieneStep.INFLECTIONS}

    first = await run_relation_hygiene(session.store, session.stages, workers=4, only=only)
    assert first.far_side_demoted == 1

    second = await run_relation_hygiene(session.store, session.stages, workers=4, only=only)
    result = second.steps[RelationHygieneStep.INFLECTIONS]
    assert result.demoted == 0
    assert result.far_side_demoted == 0
    assert second.far_side_demoted == 0


async def test_the_nano_step_also_demotes_the_far_side_reciprocal(session):
    session.store.write(
        _entry(
            "widget",
            relations=[
                _relation(
                    RelationType.SYNONYM, RELATION_INVALID_TARGET, sense_id="invalidword:noun:0"
                )
            ],
        )
    )
    session.store.write(
        _entry(
            RELATION_INVALID_TARGET,
            relations=[_relation(RelationType.SYNONYM, "widget", sense_id="widget:noun:0")],
        )
    )

    outcome = await run_relation_hygiene(
        session.store, session.stages, workers=4, only={RelationHygieneStep.VALIDITY}
    )

    result = outcome.steps[RelationHygieneStep.VALIDITY]
    assert result.demoted == 2
    assert result.far_side_demoted == 1
    assert outcome.far_side_demoted == 1

    near = _relations_of(session.store.read("widget"))[0]
    assert near.type is RelationType.SEE_ALSO
    assert near.note == NANO_INVALID_NOTE

    far = _relations_of(session.store.read(RELATION_INVALID_TARGET))[0]
    assert far.type is RelationType.SEE_ALSO
    assert far.note == f"{FAR_SIDE_NOTE_PREFIX}widget:noun:0 (nano invalid)"


# --------------------------------------------------------------------------------------
# The far-side phase and a budget stop (D-50's amendment, second finding)
# --------------------------------------------------------------------------------------
#
# The far side is the *repair* of a demotion the near-side phase has already written to
# disk, and it costs nothing. A budget stop must therefore not cancel it: the near-side
# writes are banked whatever the budget says, and skipping the repair is what leaves the
# store asserting one half of a pair this pass has already judged untrue.

_FAR_SIDE_SWEEP_SIZE = 40

#: Tight enough that the ``validity`` sweep over :data:`_FAR_SIDE_SWEEP_SIZE` near-side
#: entries stops part way. As in ``test_retrofit``, no test asserts a particular call
#: count -- where the guard's in-flight reservations land the stop is the pool's business.
_FAR_SIDE_BUDGET_USD = 0.01


def _near_side_headword(index: int) -> str:
    """Return the headword of one of the near-side entries the budget sweep judges."""
    return f"alphaword{index:04d}"


def _seed_far_side_sweep(store: LexemeStore) -> None:
    """Seed many near-side entries and the one far-side entry they all point at.

    Every near-side entry asserts a synonym of :data:`RELATION_INVALID_TARGET`, which the
    scripted model calls invalid, so each entry the sweep reaches before its budget runs
    out queues one far-side request. The far side answers each of them, alternating the
    two shapes the real store holds: a reciprocal resolved to exactly the sense that was
    demoted, and one the resolver never resolved at all.
    """
    far_side_relations = []
    for index in range(_FAR_SIDE_SWEEP_SIZE):
        headword = _near_side_headword(index)
        store.write(
            _entry(headword, relations=[_relation(RelationType.SYNONYM, RELATION_INVALID_TARGET)])
        )
        far_side_relations.append(
            _relation(
                RelationType.SYNONYM,
                headword,
                sense_id=f"{headword}:noun:0" if index % 2 == 0 else None,
            )
        )
    store.write(_entry(RELATION_INVALID_TARGET, relations=far_side_relations))


def _far_side_session(tmp_path, model, *, name: str, budget_usd: float) -> RunSession:
    """Return a session over its own temporary store, with its own budget."""
    return RunSession(
        AppConfig(
            store=StoreConfig(root=tmp_path / name, fsync_on_write=False),
            concurrency=ConcurrencyConfig(workers=4, requests_per_minute=100_000),
            log_dir=tmp_path / "runs",
            budget_usd=budget_usd,
        ),
        model_override=model,
        run_id="test-run",
    )


async def test_a_budget_stop_still_drains_the_far_side_phase(tmp_path, scripted_model):
    # The session's own stop event is passed, as the CLI passes it: a budget stop sets it,
    # and every worker of every pool afterwards returns before pulling an item.
    async with _far_side_session(
        tmp_path, scripted_model, name="budget", budget_usd=_FAR_SIDE_BUDGET_USD
    ) as active:
        store = active.store
        _seed_far_side_sweep(store)
        outcome = await run_relation_hygiene(
            store,
            active.stages,
            workers=4,
            only={RelationHygieneStep.VALIDITY},
            stop_event=active.stop_event,
        )

    result = outcome.steps[RelationHygieneStep.VALIDITY]
    assert result.stopped_reason == "budget"
    near_side = result.demoted - result.far_side_demoted
    # Stopped part way: some near sides were judged, not all of them.
    assert 0 < near_side < _FAR_SIDE_SWEEP_SIZE

    # Every near-side demotion the run banked had its reciprocal demoted too, whether the
    # far side named the demoted sense or was never resolved at all.
    demoted_sources = {
        lexeme_id
        for index in range(_FAR_SIDE_SWEEP_SIZE)
        if (lexeme_id := _near_side_headword(index))
        and _relations_of(store.read(lexeme_id))[0].type is RelationType.SEE_ALSO
    }
    assert len(demoted_sources) == near_side
    assert result.far_side_demoted == near_side
    assert outcome.far_side_demoted == near_side

    for relation in _relations_of(store.read(RELATION_INVALID_TARGET)):
        source = relation.target.lexeme_id
        if source in demoted_sources:
            assert relation.type is RelationType.SEE_ALSO
            assert relation.note == f"{FAR_SIDE_NOTE_PREFIX}{source}:noun:0 (nano invalid)"
        else:
            assert relation.type is RelationType.SYNONYM


async def test_an_external_stop_mid_sweep_still_drains_the_far_side_phase(
    tmp_path, scripted_model, monkeypatch
):
    # The same holds for a stop the caller set from outside (the CLI passes the event its
    # ``SIGINT`` handler sets): whatever the near-side phase banked before it stopped is
    # still repaired on the far side.
    stop = asyncio.Event()
    async with _far_side_session(tmp_path, scripted_model, name="stopped", budget_usd=5.0) as one:
        store = one.store
        _seed_far_side_sweep(store)
        real_write = store.write

        def write(entry: Lexeme) -> Path:
            path = real_write(entry)
            if entry.lexeme_id.startswith("alphaword"):
                stop.set()
            return path

        monkeypatch.setattr(store, "write", write)
        outcome = await run_relation_hygiene(
            store, one.stages, workers=4, only={RelationHygieneStep.VALIDITY}, stop_event=stop
        )

    result = outcome.steps[RelationHygieneStep.VALIDITY]
    assert result.stopped_reason == "stopped"
    near_side = result.demoted - result.far_side_demoted
    assert 0 < near_side < _FAR_SIDE_SWEEP_SIZE
    assert result.far_side_demoted == near_side

    reciprocals = {
        relation.target.lexeme_id: relation
        for relation in _relations_of(store.read(RELATION_INVALID_TARGET))
    }
    for index in range(_FAR_SIDE_SWEEP_SIZE):
        lexeme_id = _near_side_headword(index)
        near = _relations_of(store.read(lexeme_id))[0]
        far = reciprocals[lexeme_id]
        assert (far.type is RelationType.SEE_ALSO) == (near.type is RelationType.SEE_ALSO)


# --------------------------------------------------------------------------------------
# Idempotence, selection, and the outcome shape
# --------------------------------------------------------------------------------------


def _defective_entry() -> Lexeme:
    """Build one entry carrying a defect for every step at once."""
    return _entry(
        "banner",
        relations=[
            _relation(RelationType.SYNONYM, "banners"),
            _relation(RelationType.SYNONYM, "flag"),
            _relation(RelationType.SYNONYM, "flags"),
            _relation(RelationType.HYPONYM, "crisp banner"),
            _relation(RelationType.SYNONYM, "slang term"),
            _relation(RelationType.HYPONYM, RELATION_INVALID_TARGET),
            _relation(RelationType.ANTONYM, RELATION_RETYPE_TARGET),
            _relation(RelationType.HYPERNYM, "sign"),
        ],
    )


async def test_a_full_sweep_settles_every_shape_once(session):
    session.store.write(_defective_entry())

    outcome = await run_relation_hygiene(session.store, session.stages, workers=4)

    assert set(outcome.steps) == set(RelationHygieneStep.ALL)
    assert outcome.entries_changed == 1
    assert outcome.calls == 1
    assert outcome.cost_usd > 0.0
    assert outcome.stopped_reason is None
    assert outcome.changed

    relations = _by_term(session.store.read("banner"))
    assert relations["banners"].type is RelationType.SEE_ALSO
    assert relations["flags"].type is RelationType.SEE_ALSO
    assert relations["crisp banner"].type is RelationType.SEE_ALSO
    assert relations["slang term"].type is RelationType.SEE_ALSO
    assert relations[RELATION_INVALID_TARGET].type is RelationType.SEE_ALSO
    assert relations[RELATION_RETYPE_TARGET].type is RelationType.HYPERNYM
    assert relations["flag"].type is RelationType.SYNONYM
    assert relations["sign"].type is RelationType.HYPERNYM


async def test_a_second_sweep_is_free_and_changes_nothing(session):
    session.store.write(_defective_entry())

    await run_relation_hygiene(session.store, session.stages, workers=4)
    spent = session.meter.summary().total_usd
    before = session.store.read("banner").model_dump(mode="json")
    before.pop("updated_at")

    again = await run_relation_hygiene(session.store, session.stages, workers=4)

    assert again.calls == 0
    assert again.cost_usd == 0.0
    assert again.entries_changed == 0
    assert not again.changed
    assert session.meter.summary().total_usd == spent

    after = session.store.read("banner").model_dump(mode="json")
    after.pop("updated_at")
    assert after == before


async def test_a_new_relation_earns_one_more_attempt(session):
    session.store.write(_entry("banner", relations=[_relation(RelationType.HYPERNYM, "sign")]))
    only = {RelationHygieneStep.VALIDITY}

    assert (
        await run_relation_hygiene(session.store, session.stages, workers=4, only=only)
    ).calls == 1
    assert (
        await run_relation_hygiene(session.store, session.stages, workers=4, only=only)
    ).calls == 0

    entry = session.store.read("banner")
    entry.pos_entries[0].senses[0].relations.append(_relation(RelationType.SYNONYM, "flag"))
    session.store.write(entry)

    assert (
        await run_relation_hygiene(session.store, session.stages, workers=4, only=only)
    ).calls == 1
    # The bound is two attempts per entry, whatever changes afterwards.
    entry = session.store.read("banner")
    entry.pos_entries[0].senses[0].relations.append(_relation(RelationType.SYNONYM, "standard"))
    session.store.write(entry)
    assert (
        await run_relation_hygiene(session.store, session.stages, workers=4, only=only)
    ).calls == 0


async def test_only_runs_just_the_named_steps(session):
    session.store.write(_defective_entry())

    outcome = await run_relation_hygiene(
        session.store, session.stages, workers=4, only=set(FREE_STEPS)
    )

    assert set(outcome.steps) == FREE_STEPS
    assert outcome.calls == 0
    assert outcome.cost_usd == 0.0

    relations = _by_term(session.store.read("banner"))
    # The step that was not selected left its own defects in place.
    assert relations[RELATION_INVALID_TARGET].type is RelationType.HYPONYM
    assert relations[RELATION_RETYPE_TARGET].type is RelationType.ANTONYM


async def test_lexeme_ids_limits_the_sweep(session):
    session.store.write(_entry("stay", relations=[_relation(RelationType.SYNONYM, "stays")]))
    session.store.write(_entry("banner", relations=[_relation(RelationType.SYNONYM, "banners")]))

    outcome = await run_relation_hygiene(
        session.store,
        session.stages,
        workers=4,
        only={RelationHygieneStep.INFLECTIONS},
        lexeme_ids=["stay"],
    )

    assert outcome.steps[RelationHygieneStep.INFLECTIONS].entries_scanned == 1
    assert _relations_of(session.store.read("stay"))[0].type is RelationType.SEE_ALSO
    assert _relations_of(session.store.read("banner"))[0].type is RelationType.SYNONYM


async def test_an_unknown_step_name_is_rejected(session):
    with pytest.raises(ValueError, match="unknown relation hygiene step"):
        await run_relation_hygiene(session.store, session.stages, workers=4, only={"not_a_step"})


async def test_as_dict_reports_every_step(session):
    session.store.write(_defective_entry())

    outcome = await run_relation_hygiene(session.store, session.stages, workers=4)
    payload = outcome.as_dict()

    assert payload["entries_changed"] == 1
    assert payload["stopped_reason"] is None
    assert payload["retyped"] == 1
    assert payload["demoted"] == 5
    steps = payload["steps"]
    assert set(steps) == set(RelationHygieneStep.ALL)
    assert steps[RelationHygieneStep.INFLECTIONS]["demoted"] == 2
    assert steps[RelationHygieneStep.HEADWORD_PHRASES]["demoted"] == 1
    assert steps[RelationHygieneStep.META_LABELS]["demoted"] == 1
    assert steps[RelationHygieneStep.VALIDITY]["calls"] == 1
    assert steps[RelationHygieneStep.VALIDITY]["retyped"] == 1


def test_a_pre_existing_marker_is_parsed():
    entry = _entry("banner")
    assert module._attempt_number(entry, module._VALIDITY_PREFIX, []) is None
    assert module._attempt_number(entry, module._VALIDITY_PREFIX, ["a", "b"]) == 1
    note = module._marker_note(module._VALIDITY_PREFIX, ["a", "b"], 1)
    assert note.startswith(f"{module._VALIDITY_PREFIX}:")
    assert note.endswith(";attempts=1")

    entry.add_provenance(module._rule_provenance(note))
    # The same set is not re-judged; a changed set earns the second and last attempt.
    assert module._attempt_number(entry, module._VALIDITY_PREFIX, ["a", "b"]) is None
    assert module._attempt_number(entry, module._VALIDITY_PREFIX, ["a", "b", "c"]) == 2
