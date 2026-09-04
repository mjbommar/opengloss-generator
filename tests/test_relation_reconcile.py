"""Relation reconcile: make the stored list say what the hygiene passes decided.

Companion to ``test_relation_hygiene.py``, which covers the *verdicts* this pass acts on.
Everything here is about what those verdicts left behind: a demoted ``see_also`` the QA
judge is still shown, a symmetric pair whose two directional verdicts disagree, and a
sense carrying more relations of one type than anybody reads.

Offline throughout — the pass makes no model call, so these tests need a store and a
worker count and nothing else.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from opengloss_generator.config import AppConfig, ConcurrencyConfig, StoreConfig
from opengloss_generator.schema import (
    Contrast,
    ContrastVerdict,
    Example,
    Lexeme,
    LexemeKind,
    PartOfSpeech,
    POSEntry,
    Provenance,
    Relation,
    RelationTarget,
    RelationType,
    Renditions,
    Sense,
    StageName,
    canonical_rendition,
)
from opengloss_generator.store import LexemeStore
from opengloss_generator.workflows.relation_hygiene import (
    FAR_SIDE_NOTE_PREFIX,
    HEADWORD_INFLECTION_NOTE,
    NANO_INVALID_NOTE,
    NANO_RETYPE_NOTE,
)
from opengloss_generator.workflows.relation_reconcile import (
    ASYMMETRIC_NOTE_PREFIX,
    CAP_LINE_PREFIX,
    CAP_RECORD_PREFIX,
    DEDUP_RECORD_PREFIX,
    MARKER_PREFIX,
    TOMBSTONE_LINE_PREFIX,
    TOMBSTONE_RECORD_PREFIX,
    VERDICT_NOTE_PREFIX,
    RelationCaps,
    RelationReconcileStep,
    is_demotion_note,
    run_relation_reconcile,
)

DEFAULT_GLOSS = "A test definition written for the pass under test."

#: The ``validity`` step's D-47 sentinel, as ``relation_hygiene`` writes it. Its presence
#: is what tells ``cap`` that every live typed relation on an entry was judged and kept.
VALIDITY_MARKER = "relation_hygiene:validity:abcdef0123456789;attempts=1"


@pytest.fixture
def store(tmp_path: Path) -> LexemeStore:
    """Return a store on a temporary directory, with fsync off for speed."""
    config = AppConfig(
        store=StoreConfig(root=tmp_path / "store", fsync_on_write=False),
        concurrency=ConcurrencyConfig(workers=4, requests_per_minute=10_000),
        log_dir=tmp_path / "runs",
    )
    return LexemeStore(config.store)


def _sense(index: int, relations: list[Relation], *, retired: bool = False) -> Sense:
    """Build one sense carrying whatever relations the test under way needs."""
    return Sense(
        index=index,
        gloss=Renditions[str](root=[canonical_rendition(f"{DEFAULT_GLOSS} ({index})")]),
        examples=Renditions[Example](
            root=[canonical_rendition(Example(text="A sentence written for a test."))]
        ),
        relations=relations,
        retired=retired,
    )


def _entry(
    headword: str,
    *,
    relations: list[Relation] | None = None,
    senses: list[Sense] | None = None,
    pos: PartOfSpeech = PartOfSpeech.NOUN,
) -> Lexeme:
    """Build an entry with one sense, or with the sense list the test supplies."""
    return Lexeme.empty(
        headword,
        kind=LexemeKind.SIMPLEX,
        pos_entries=[POSEntry(pos=pos, senses=senses or [_sense(0, relations or [])])],
    )


def _relation(
    relation_type: RelationType,
    term: str,
    *,
    sense_id: str | None = None,
    note: str | None = None,
) -> Relation:
    """Build one typed relation, optionally already resolved and already noted."""
    return Relation(
        type=relation_type,
        target=RelationTarget(term=term, sense_id=sense_id),
        note=note,
    )


def _relations_of(entry: Lexeme, index: int = 0) -> list[Relation]:
    """Return one sense's relations."""
    return entry.pos_entries[0].senses[index].relations


def _terms(entry: Lexeme, index: int = 0) -> list[str]:
    """Return one sense's relation target terms, in stored order."""
    return [relation.target.term for relation in _relations_of(entry, index)]


def _notes(entry: Lexeme) -> list[str]:
    """Return every provenance note on an entry."""
    return [record.note or "" for record in entry.provenance.values()]


def _mark_validity_judged(entry: Lexeme) -> None:
    """Stamp ``relation_hygiene``'s ``validity`` sentinel on an entry, as that pass does."""
    entry.add_provenance(
        Provenance(
            stage=StageName.HYGIENE,
            model="rule:relation_hygiene",
            prompt_version="test",
            cost_usd=0.0,
            attempts=0,
            note=VALIDITY_MARKER,
        )
    )


def _pair(
    near_type: RelationType = RelationType.SYNONYM,
    *,
    far_note: str = NANO_INVALID_NOTE,
) -> tuple[Lexeme, Lexeme]:
    """Return ``(alpha, beta)`` where alpha asserts a live edge beta already demoted.

    ``alpha`` holds a resolved symmetric edge toward ``beta``'s only sense; ``beta`` holds
    the reverse, already demoted to ``see_also`` by whichever hygiene step ``far_note``
    names.
    """
    alpha = _entry("alpha", relations=[_relation(near_type, "beta", sense_id="beta:noun:0")])
    beta = _entry(
        "beta",
        relations=[
            _relation(RelationType.SEE_ALSO, "alpha", sense_id="alpha:noun:0", note=far_note)
        ],
    )
    return alpha, beta


def _contrast(
    edge: str,
    verdict: ContrastVerdict,
    *,
    target_sense_id: str | None = None,
    text: str = "Alpha and beta differ in register and in the situations each one fits.",
) -> Contrast:
    """Build one stored contrast, keyed by the edge id the ``contrasts`` stage derived."""
    return Contrast(
        edge_id=edge,
        target_sense_id=target_sense_id,
        text=Renditions[str](root=[canonical_rendition(text)]),
        verdict=verdict,
    )


def _judged_pair(
    verdict: ContrastVerdict, *, relation_type: RelationType = RelationType.SYNONYM
) -> tuple[Lexeme, Lexeme]:
    """Return ``(alpha, beta)``: a reciprocated symmetric pair, judged from alpha's end.

    The shape the ``contrasts`` stage actually leaves behind (D-57 §1): one contrast on
    the lexicographically smaller end, none on the other, and both ends asserting the
    edge because ``graph_hygiene`` reciprocated it.
    """
    alpha = _entry("alpha", relations=[_relation(relation_type, "beta", sense_id="beta:noun:0")])
    alpha.contrasts.append(
        _contrast(
            f"alpha:noun:0-{relation_type.value}->beta", verdict, target_sense_id="beta:noun:0"
        )
    )
    beta = _entry("beta", relations=[_relation(relation_type, "alpha", sense_id="alpha:noun:0")])
    return alpha, beta


# --------------------------------------------------------------------------------------
# Step 1 — verdicts
# --------------------------------------------------------------------------------------


async def test_an_unrelated_verdict_demotes_its_edge(store):
    alpha, beta = _judged_pair(ContrastVerdict.UNRELATED)
    store.write(alpha)
    store.write(beta)

    outcome = await run_relation_reconcile(store, workers=4, only={RelationReconcileStep.VERDICTS})

    result = outcome.steps[RelationReconcileStep.VERDICTS]
    assert result.by_verdict == {"unrelated": 2}  # near side and far side
    assert result.by_type == {"synonym": 2}
    assert result.far_side_demoted == 1
    assert result.calls == 0
    assert result.cost_usd == 0.0

    relation = _relations_of(store.read("alpha"))[0]
    assert relation.type is RelationType.SEE_ALSO
    assert relation.note == f"{VERDICT_NOTE_PREFIX}unrelated"
    assert is_demotion_note(relation.note)


async def test_a_related_differently_verdict_demotes_with_its_own_note(store):
    # The paragraph says the two are related but not as typed; it does not say how, so
    # the edge is demoted rather than retyped to a guess.
    alpha, beta = _judged_pair(ContrastVerdict.RELATED_DIFFERENTLY)
    store.write(alpha)
    store.write(beta)

    outcome = await run_relation_reconcile(store, workers=4, only={RelationReconcileStep.VERDICTS})

    assert outcome.steps[RelationReconcileStep.VERDICTS].by_verdict == {"related_differently": 2}
    relation = _relations_of(store.read("alpha"))[0]
    assert relation.type is RelationType.SEE_ALSO
    assert relation.note == f"{VERDICT_NOTE_PREFIX}related_differently"


async def test_a_related_as_typed_verdict_leaves_its_edge_alone(store):
    alpha, beta = _judged_pair(ContrastVerdict.RELATED_AS_TYPED)
    store.write(alpha)
    store.write(beta)

    outcome = await run_relation_reconcile(store, workers=4, only={RelationReconcileStep.VERDICTS})

    result = outcome.steps[RelationReconcileStep.VERDICTS]
    assert result.demoted == 0
    assert result.by_verdict == {}
    assert outcome.entries_changed == 0
    assert _relations_of(store.read("alpha"))[0].type is RelationType.SYNONYM
    assert _relations_of(store.read("beta"))[0].type is RelationType.SYNONYM


async def test_a_verdict_demotion_demotes_the_reverse_edge_on_the_far_side(store):
    # A contrast is written once per undirected pair, so nothing but the far-side phase
    # will ever act on beta's half of it.
    alpha, beta = _judged_pair(ContrastVerdict.UNRELATED, relation_type=RelationType.ANTONYM)
    store.write(alpha)
    store.write(beta)

    await run_relation_reconcile(store, workers=4, only={RelationReconcileStep.VERDICTS})

    reverse = _relations_of(store.read("beta"))[0]
    assert reverse.type is RelationType.SEE_ALSO
    assert reverse.note == f"{FAR_SIDE_NOTE_PREFIX}alpha:noun:0 (contrast unrelated)"
    assert is_demotion_note(reverse.note)


async def test_the_far_side_phase_leaves_a_reverse_about_another_sense_alone(store):
    # Sense-level, unlike ``cap``'s far side: the verdict is a judgement about these two
    # senses, and beta's second sense may hold a perfectly good synonym toward alpha.
    alpha, _ = _judged_pair(ContrastVerdict.UNRELATED)
    beta = _entry(
        "beta",
        senses=[
            _sense(0, [_relation(RelationType.SYNONYM, "alpha", sense_id="alpha:noun:0")]),
            _sense(1, [_relation(RelationType.SYNONYM, "alpha", sense_id="alpha:noun:1")]),
        ],
    )
    store.write(alpha)
    store.write(beta)

    await run_relation_reconcile(store, workers=4, only={RelationReconcileStep.VERDICTS})

    stored = store.read("beta")
    assert _relations_of(stored, 0)[0].type is RelationType.SEE_ALSO
    assert _relations_of(stored, 1)[0].type is RelationType.SYNONYM


async def test_a_contrast_keyed_on_another_sense_does_not_touch_this_one(store):
    # The join is by edge id, which begins with the sense id, so a contrast written about
    # sense 1 cannot demote sense 0's identically typed edge toward the same target.
    alpha = _entry(
        "alpha",
        senses=[
            _sense(0, [_relation(RelationType.SYNONYM, "beta", sense_id="beta:noun:0")]),
            _sense(1, [_relation(RelationType.SYNONYM, "beta", sense_id="beta:noun:0")]),
        ],
    )
    alpha.contrasts.append(_contrast("alpha:noun:1-synonym->beta", ContrastVerdict.UNRELATED))
    store.write(alpha)

    outcome = await run_relation_reconcile(store, workers=4, only={RelationReconcileStep.VERDICTS})

    assert outcome.steps[RelationReconcileStep.VERDICTS].demoted == 1
    stored = store.read("alpha")
    assert _relations_of(stored, 0)[0].type is RelationType.SYNONYM
    assert _relations_of(stored, 1)[0].type is RelationType.SEE_ALSO


async def test_a_verdict_on_an_unresolved_edge_demotes_but_queues_no_far_side_work(store):
    alpha = _entry("alpha", relations=[_relation(RelationType.SYNONYM, "beta")])
    alpha.contrasts.append(_contrast("alpha:noun:0-synonym->beta", ContrastVerdict.UNRELATED))
    beta = _entry("beta", relations=[_relation(RelationType.SYNONYM, "alpha")])
    store.write(alpha)
    store.write(beta)

    outcome = await run_relation_reconcile(store, workers=4, only={RelationReconcileStep.VERDICTS})

    result = outcome.steps[RelationReconcileStep.VERDICTS]
    assert result.demoted == 1
    assert result.far_side_demoted == 0
    assert _relations_of(store.read("beta"))[0].type is RelationType.SYNONYM


async def test_a_verdict_on_an_asymmetric_type_queues_no_far_side_work(store):
    alpha = _entry(
        "alpha", relations=[_relation(RelationType.HYPERNYM, "beta", sense_id="beta:noun:0")]
    )
    alpha.contrasts.append(_contrast("alpha:noun:0-hypernym->beta", ContrastVerdict.UNRELATED))
    beta = _entry(
        "beta", relations=[_relation(RelationType.HYPERNYM, "alpha", sense_id="alpha:noun:0")]
    )
    store.write(alpha)
    store.write(beta)

    outcome = await run_relation_reconcile(store, workers=4, only={RelationReconcileStep.VERDICTS})

    result = outcome.steps[RelationReconcileStep.VERDICTS]
    assert result.by_type == {"hypernym": 1}
    assert result.far_side_demoted == 0


async def test_the_verdicts_step_is_idempotent(store):
    alpha, beta = _judged_pair(ContrastVerdict.UNRELATED)
    store.write(alpha)
    store.write(beta)

    await run_relation_reconcile(store, workers=4, only={RelationReconcileStep.VERDICTS})
    before = store.read("alpha").model_dump_json()
    beta_before = store.read("beta").model_dump_json()

    again = await run_relation_reconcile(store, workers=4, only={RelationReconcileStep.VERDICTS})

    assert again.steps[RelationReconcileStep.VERDICTS].demoted == 0
    assert store.read("alpha").model_dump_json() == before
    assert store.read("beta").model_dump_json() == beta_before


async def test_the_contrast_survives_the_edge_it_demoted(store):
    # D-62: a contrast whose edge has gone is evidence about a removed relation, not a
    # validation error, so nothing here prunes the entry's contrast list.
    alpha, beta = _judged_pair(ContrastVerdict.UNRELATED)
    store.write(alpha)
    store.write(beta)

    await run_relation_reconcile(store, workers=4)

    stored = store.read("alpha")
    assert _terms(stored) == []
    assert [c.edge_id for c in stored.contrasts] == ["alpha:noun:0-synonym->beta"]


async def test_a_full_sweep_demotes_a_verdict_edge_and_tombstones_it_in_the_same_sweep(store):
    # The whole reason ``verdicts`` is first in the step order: run the other way round,
    # the edge the contrasts stage rejected would still be in the list the QA judge reads
    # until somebody ran the pass a second time.
    alpha, beta = _judged_pair(ContrastVerdict.RELATED_DIFFERENTLY)
    store.write(alpha)
    store.write(beta)

    outcome = await run_relation_reconcile(store, workers=4)

    assert outcome.steps[RelationReconcileStep.VERDICTS].demoted == 2
    assert outcome.steps[RelationReconcileStep.TOMBSTONE].removed == 1
    stored = store.read("alpha")
    assert _terms(stored) == []
    listing = next(note for note in _notes(stored) if note.startswith(TOMBSTONE_RECORD_PREFIX))
    assert listing.splitlines()[1] == (
        f"{TOMBSTONE_LINE_PREFIX}see_also -> beta [{VERDICT_NOTE_PREFIX}related_differently]"
    )


async def test_the_far_side_demotion_is_tombstoned_by_the_next_sweep(store):
    # The far-side phase runs after ``tombstone`` has already visited beta, so it writes
    # no marker: the stale one disagrees with beta's new digest and the next sweep
    # re-examines the entry, which is what takes the demoted see_also out of the list.
    alpha, beta = _judged_pair(ContrastVerdict.UNRELATED)
    store.write(alpha)
    store.write(beta)

    await run_relation_reconcile(store, workers=4)
    assert _terms(store.read("beta")) == ["alpha"]

    second = await run_relation_reconcile(store, workers=4)

    assert second.steps[RelationReconcileStep.TOMBSTONE].removed == 1
    assert _terms(store.read("beta")) == []


async def test_contrasts_arriving_after_a_sweep_reopen_the_entry(store):
    # A ``contrasts`` sweep adds verdicts without touching a relation, so a digest over
    # edge ids alone would skip the entry forever and never apply them.
    alpha, beta = _judged_pair(ContrastVerdict.UNRELATED)
    contrasts = list(alpha.contrasts)
    alpha.contrasts.clear()
    _relations_of(alpha).append(_relation(RelationType.SEE_ALSO, "x", note=NANO_INVALID_NOTE))
    store.write(alpha)
    store.write(beta)

    first = await run_relation_reconcile(store, workers=4)
    assert first.steps[RelationReconcileStep.TOMBSTONE].removed == 1
    assert first.steps[RelationReconcileStep.VERDICTS].demoted == 0

    reopened = store.read("alpha")
    reopened.contrasts.extend(contrasts)
    store.write(reopened)

    second = await run_relation_reconcile(store, workers=4)

    assert second.entries_skipped == 0
    assert second.steps[RelationReconcileStep.VERDICTS].demoted == 2
    assert _terms(store.read("alpha")) == []


# --------------------------------------------------------------------------------------
# Step 2 — asymmetric
# --------------------------------------------------------------------------------------


async def test_a_synonym_whose_reverse_the_validity_step_demoted_is_demoted_too(store):
    alpha, beta = _pair(far_note=NANO_INVALID_NOTE)
    store.write(alpha)
    store.write(beta)

    outcome = await run_relation_reconcile(
        store, workers=4, only={RelationReconcileStep.ASYMMETRIC}
    )

    result = outcome.steps[RelationReconcileStep.ASYMMETRIC]
    assert result.demoted == 1
    assert result.by_type == {"synonym": 1}
    assert result.calls == 0
    assert result.cost_usd == 0.0

    relation = _relations_of(store.read("alpha"))[0]
    assert relation.type is RelationType.SEE_ALSO
    assert relation.note == f"{ASYMMETRIC_NOTE_PREFIX}beta:noun:0"


async def test_a_synonym_whose_reverse_the_far_side_phase_demoted_is_demoted_too(store):
    # D-50's far-side note, the other half of the demotions the store actually carries.
    alpha, beta = _pair(far_note=f"{FAR_SIDE_NOTE_PREFIX}gamma:noun:0 (nano invalid)")
    store.write(alpha)
    store.write(beta)

    outcome = await run_relation_reconcile(
        store, workers=4, only={RelationReconcileStep.ASYMMETRIC}
    )

    assert outcome.steps[RelationReconcileStep.ASYMMETRIC].demoted == 1
    assert _relations_of(store.read("alpha"))[0].type is RelationType.SEE_ALSO


async def test_a_reverse_retyped_to_see_also_by_the_model_also_counts_as_demoted(store):
    # ``validity``'s retype path can name see_also as the better type; an edge that
    # reached see_also through a model verdict is a tombstone however the note phrases it.
    alpha, beta = _pair(far_note=f"{NANO_RETYPE_NOTE}synonym→see_also")
    store.write(alpha)
    store.write(beta)

    outcome = await run_relation_reconcile(
        store, workers=4, only={RelationReconcileStep.ASYMMETRIC}
    )

    assert outcome.steps[RelationReconcileStep.ASYMMETRIC].demoted == 1


async def test_an_antonym_pair_is_demoted_and_counted_under_its_own_type(store):
    alpha = _entry(
        "alpha", relations=[_relation(RelationType.ANTONYM, "beta", sense_id="beta:noun:0")]
    )
    beta = _entry(
        "beta",
        relations=[
            _relation(
                RelationType.SEE_ALSO, "alpha", sense_id="alpha:noun:0", note=NANO_INVALID_NOTE
            )
        ],
    )
    store.write(alpha)
    store.write(beta)

    outcome = await run_relation_reconcile(
        store, workers=4, only={RelationReconcileStep.ASYMMETRIC}
    )

    assert outcome.steps[RelationReconcileStep.ASYMMETRIC].by_type == {"antonym": 1}


async def test_an_unresolved_reverse_still_decides_the_near_side(store):
    # ``relation_hygiene._is_far_side_of`` accepts an unresolved far side as the
    # reciprocal, and so does this pass's index.
    alpha, beta = _pair()
    _relations_of(beta)[0].target.sense_id = None
    store.write(alpha)
    store.write(beta)

    outcome = await run_relation_reconcile(
        store, workers=4, only={RelationReconcileStep.ASYMMETRIC}
    )

    assert outcome.steps[RelationReconcileStep.ASYMMETRIC].demoted == 1


async def test_a_reverse_demoted_on_another_sense_still_decides_the_near_side(store):
    # The shape that made a sense-level index miss 47 of 61 one-sided pairs on the sample:
    # the near side resolves to a sense D-52 later retired, and the surviving demotion
    # lives on a different sense id. The pairing is entry-level for exactly this reason.
    alpha = _entry(
        "alpha", relations=[_relation(RelationType.SYNONYM, "beta", sense_id="beta:noun:1")]
    )
    beta = _entry(
        "beta",
        senses=[
            _sense(
                0,
                [
                    _relation(
                        RelationType.SEE_ALSO,
                        "alpha",
                        sense_id="alpha:noun:0",
                        note=NANO_INVALID_NOTE,
                    )
                ],
            ),
            _sense(1, [], retired=True),
        ],
    )
    store.write(alpha)
    store.write(beta)

    outcome = await run_relation_reconcile(
        store, workers=4, only={RelationReconcileStep.ASYMMETRIC}
    )

    assert outcome.steps[RelationReconcileStep.ASYMMETRIC].demoted == 1


async def test_a_far_side_that_still_asserts_the_type_elsewhere_protects_the_near_side(store):
    # beta demoted its edge on one sense but still asserts the synonym on another, so the
    # pair is reciprocated and there is no disagreement to settle.
    alpha = _entry(
        "alpha", relations=[_relation(RelationType.SYNONYM, "beta", sense_id="beta:noun:0")]
    )
    beta = _entry(
        "beta",
        senses=[
            _sense(
                0,
                [
                    _relation(
                        RelationType.SEE_ALSO,
                        "alpha",
                        sense_id="alpha:noun:0",
                        note=NANO_INVALID_NOTE,
                    )
                ],
            ),
            _sense(1, [_relation(RelationType.SYNONYM, "alpha", sense_id="alpha:noun:0")]),
        ],
    )
    store.write(alpha)
    store.write(beta)

    outcome = await run_relation_reconcile(
        store, workers=4, only={RelationReconcileStep.ASYMMETRIC}
    )

    assert outcome.steps[RelationReconcileStep.ASYMMETRIC].demoted == 0
    assert _relations_of(store.read("alpha"))[0].type is RelationType.SYNONYM


async def test_a_live_reverse_leaves_the_near_side_alone(store):
    alpha = _entry(
        "alpha", relations=[_relation(RelationType.SYNONYM, "beta", sense_id="beta:noun:0")]
    )
    beta = _entry(
        "beta", relations=[_relation(RelationType.SYNONYM, "alpha", sense_id="alpha:noun:0")]
    )
    store.write(alpha)
    store.write(beta)

    outcome = await run_relation_reconcile(
        store, workers=4, only={RelationReconcileStep.ASYMMETRIC}
    )

    assert outcome.steps[RelationReconcileStep.ASYMMETRIC].demoted == 0
    assert _relations_of(store.read("alpha"))[0].type is RelationType.SYNONYM


async def test_an_asymmetric_type_is_not_reconciled_by_this_step(store):
    # Only synonym/antonym/confusable_with hold in both directions; a hypernym whose
    # reverse was demoted is not evidence about the hypernym.
    alpha = _entry(
        "alpha", relations=[_relation(RelationType.HYPERNYM, "beta", sense_id="beta:noun:0")]
    )
    beta = _entry(
        "beta",
        relations=[
            _relation(
                RelationType.SEE_ALSO, "alpha", sense_id="alpha:noun:0", note=NANO_INVALID_NOTE
            )
        ],
    )
    store.write(alpha)
    store.write(beta)

    outcome = await run_relation_reconcile(
        store, workers=4, only={RelationReconcileStep.ASYMMETRIC}
    )

    assert outcome.steps[RelationReconcileStep.ASYMMETRIC].demoted == 0


async def test_a_demoted_reverse_outside_the_from_list_still_decides_the_near_side(store):
    # The index is built over the whole store, never over ``lexeme_ids``: the far side of
    # an edge on a named list is very often not on that list.
    alpha, beta = _pair()
    store.write(alpha)
    store.write(beta)

    outcome = await run_relation_reconcile(
        store, workers=4, only={RelationReconcileStep.ASYMMETRIC}, lexeme_ids=["alpha"]
    )

    assert outcome.steps[RelationReconcileStep.ASYMMETRIC].demoted == 1
    assert outcome.entries_scanned == 1


# --------------------------------------------------------------------------------------
# Step 3 — tombstone
# --------------------------------------------------------------------------------------


async def test_a_demoted_see_also_is_removed_from_the_list_and_written_to_provenance(store):
    store.write(
        _entry(
            "alpha",
            relations=[
                _relation(RelationType.SEE_ALSO, "banners", note=HEADWORD_INFLECTION_NOTE),
                _relation(RelationType.HYPERNYM, "flag"),
            ],
        )
    )

    outcome = await run_relation_reconcile(store, workers=4, only={RelationReconcileStep.TOMBSTONE})

    result = outcome.steps[RelationReconcileStep.TOMBSTONE]
    assert result.removed == 1
    assert result.by_type == {"see_also": 1}

    entry = store.read("alpha")
    assert _terms(entry) == ["flag"]
    listing = next(note for note in _notes(entry) if note.startswith(TOMBSTONE_RECORD_PREFIX))
    lines = listing.splitlines()
    assert lines[0] == f"{TOMBSTONE_RECORD_PREFIX}alpha:noun:0"
    assert lines[1] == (f"{TOMBSTONE_LINE_PREFIX}see_also -> banners [{HEADWORD_INFLECTION_NOTE}]")


async def test_an_authored_see_also_survives_the_tombstone_step(store):
    store.write(
        _entry(
            "alpha",
            relations=[
                _relation(RelationType.SEE_ALSO, "beta"),
                _relation(RelationType.SEE_ALSO, "gamma", note="compare the two spellings"),
                _relation(RelationType.SEE_ALSO, "delta", note=NANO_INVALID_NOTE),
            ],
        )
    )

    outcome = await run_relation_reconcile(store, workers=4, only={RelationReconcileStep.TOMBSTONE})

    assert outcome.steps[RelationReconcileStep.TOMBSTONE].removed == 1
    assert _terms(store.read("alpha")) == ["beta", "gamma"]


async def test_a_typed_relation_is_never_tombstoned(store):
    # The step's subject is the see_also floor, not the graph: a live hypernym stays even
    # if some other pass wrote a note on it.
    store.write(
        _entry(
            "alpha", relations=[_relation(RelationType.HYPERNYM, "beta", note="reciprocal of x")]
        )
    )

    outcome = await run_relation_reconcile(store, workers=4, only={RelationReconcileStep.TOMBSTONE})

    assert outcome.steps[RelationReconcileStep.TOMBSTONE].removed == 0
    assert _terms(store.read("alpha")) == ["beta"]


async def test_a_full_sweep_demotes_then_tombstones_the_same_edge(store):
    alpha, beta = _pair()
    store.write(alpha)
    store.write(beta)

    outcome = await run_relation_reconcile(store, workers=4)

    assert outcome.steps[RelationReconcileStep.ASYMMETRIC].demoted == 1
    # alpha's demoted edge and beta's pre-existing tombstone both go.
    assert outcome.steps[RelationReconcileStep.TOMBSTONE].removed == 2
    assert _terms(store.read("alpha")) == []
    assert _terms(store.read("beta")) == []


def test_is_demotion_note_recognises_every_hygiene_prefix():
    assert is_demotion_note(NANO_INVALID_NOTE)
    assert is_demotion_note(f"{NANO_RETYPE_NOTE}synonym→see_also")
    assert is_demotion_note(f"{FAR_SIDE_NOTE_PREFIX}beta:noun:0 (meta-label)")
    assert is_demotion_note(f"{ASYMMETRIC_NOTE_PREFIX}beta:noun:0")
    # graph_hygiene's and content_hygiene's own demotions share the generic shape.
    assert is_demotion_note("demoted: self-loop")
    assert not is_demotion_note(None)
    assert not is_demotion_note("compare the two spellings")


# --------------------------------------------------------------------------------------
# Step 3 — cap
# --------------------------------------------------------------------------------------


async def test_overflow_beyond_the_per_type_cap_is_tombstoned(store):
    store.write(
        _entry(
            "alpha",
            relations=[_relation(RelationType.HYPERNYM, f"parent{i}") for i in range(5)],
        )
    )

    outcome = await run_relation_reconcile(store, workers=4, only={RelationReconcileStep.CAP})

    result = outcome.steps[RelationReconcileStep.CAP]
    assert result.removed == 2  # hypernym cap is 3
    assert result.by_type == {"hypernym": 2}
    assert result.senses_capped == 1

    entry = store.read("alpha")
    assert _terms(entry) == ["parent0", "parent1", "parent2"]
    listing = next(note for note in _notes(entry) if note.startswith(CAP_RECORD_PREFIX))
    assert listing.splitlines()[1].startswith(f"{CAP_LINE_PREFIX}hypernym -> parent3 ")


async def test_a_type_under_its_cap_is_untouched(store):
    store.write(
        _entry(
            "alpha",
            relations=[_relation(RelationType.SYNONYM, f"syn{i}") for i in range(8)],
        )
    )

    outcome = await run_relation_reconcile(store, workers=4, only={RelationReconcileStep.CAP})

    assert outcome.steps[RelationReconcileStep.CAP].removed == 0
    assert len(_terms(store.read("alpha"))) == 8


async def test_the_cap_keeps_resolved_targets_before_unresolved_ones(store):
    # Document order puts the unresolved ones first; the keep order overrules it, because
    # a resolved target is a real entry and the only kind that can carry a reciprocal.
    store.write(
        _entry(
            "alpha",
            relations=[
                _relation(RelationType.HYPERNYM, "loose1"),
                _relation(RelationType.HYPERNYM, "loose2"),
                _relation(RelationType.HYPERNYM, "loose3"),
                _relation(RelationType.HYPERNYM, "bound1", sense_id="bound1:noun:0"),
                _relation(RelationType.HYPERNYM, "bound2", sense_id="bound2:noun:0"),
            ],
        )
    )

    await run_relation_reconcile(store, workers=4, only={RelationReconcileStep.CAP})

    assert _terms(store.read("alpha")) == ["loose1", "bound1", "bound2"]


async def test_the_cap_keeps_a_judged_edge_before_a_never_judged_one(store):
    # Same resolution state throughout, so the second key decides: a demoted see_also was
    # never accepted by ``validity`` and loses to the authored one, whatever the order.
    entry = _entry(
        "alpha",
        relations=[
            _relation(RelationType.SEE_ALSO, "tombstone1", note=NANO_INVALID_NOTE),
            _relation(RelationType.SEE_ALSO, "tombstone2", note=NANO_INVALID_NOTE),
            _relation(RelationType.SEE_ALSO, "authored1"),
            _relation(RelationType.SEE_ALSO, "authored2"),
            _relation(RelationType.SEE_ALSO, "authored3"),
        ],
    )
    _mark_validity_judged(entry)
    store.write(entry)

    # ``cap`` alone, so the tombstones are still in the list for it to rank.
    await run_relation_reconcile(store, workers=4, only={RelationReconcileStep.CAP})

    assert _terms(store.read("alpha")) == [
        "tombstone1",
        "tombstone2",
        "authored1",
        "authored2",
    ]


async def test_original_order_breaks_a_tie(store):
    store.write(
        _entry("alpha", relations=[_relation(RelationType.ANTONYM, f"opp{i}") for i in range(6)])
    )

    await run_relation_reconcile(store, workers=4, only={RelationReconcileStep.CAP})

    assert _terms(store.read("alpha")) == ["opp0", "opp1", "opp2", "opp3"]


async def test_caps_are_configurable(store):
    store.write(
        _entry("alpha", relations=[_relation(RelationType.SYNONYM, f"syn{i}") for i in range(4)])
    )

    outcome = await run_relation_reconcile(
        store, workers=4, only={RelationReconcileStep.CAP}, caps=RelationCaps(synonym=2)
    )

    assert outcome.steps[RelationReconcileStep.CAP].removed == 2
    assert _terms(store.read("alpha")) == ["syn0", "syn1"]


async def test_capping_one_half_of_a_reciprocated_pair_removes_the_other(store):
    # The far-side phase: cap must not leave the store asserting one side of a pair.
    caps = RelationCaps(synonym=1)
    alpha = _entry(
        "alpha",
        relations=[
            _relation(RelationType.SYNONYM, "gamma", sense_id="gamma:noun:0"),
            _relation(RelationType.SYNONYM, "beta", sense_id="beta:noun:0"),
        ],
    )
    beta = _entry(
        "beta", relations=[_relation(RelationType.SYNONYM, "alpha", sense_id="alpha:noun:0")]
    )
    gamma = _entry(
        "gamma", relations=[_relation(RelationType.SYNONYM, "alpha", sense_id="alpha:noun:0")]
    )
    for entry in (alpha, beta, gamma):
        store.write(entry)

    outcome = await run_relation_reconcile(
        store, workers=4, only={RelationReconcileStep.CAP}, caps=caps
    )

    result = outcome.steps[RelationReconcileStep.CAP]
    assert result.far_side_removed == 1
    assert _terms(store.read("alpha")) == ["gamma"]
    assert _terms(store.read("beta")) == []
    assert _terms(store.read("gamma")) == ["alpha"]


async def test_a_far_side_on_another_sense_is_removed_too(store):
    # Entry-level, like audit's own reciprocity measure: the claim alpha has stopped
    # making is "alpha and beta are synonyms", wherever beta keeps its half of it.
    caps = RelationCaps(synonym=1)
    alpha = _entry(
        "alpha",
        relations=[
            _relation(RelationType.SYNONYM, "gamma", sense_id="gamma:noun:0"),
            _relation(RelationType.SYNONYM, "beta", sense_id="beta:noun:0"),
        ],
    )
    beta = _entry(
        "beta", relations=[_relation(RelationType.SYNONYM, "alpha", sense_id="alpha:noun:7")]
    )
    gamma = _entry("gamma", relations=[])
    for entry in (alpha, beta, gamma):
        store.write(entry)

    outcome = await run_relation_reconcile(
        store, workers=4, only={RelationReconcileStep.CAP}, caps=caps
    )

    assert outcome.steps[RelationReconcileStep.CAP].far_side_removed == 1
    assert _terms(store.read("beta")) == []


async def test_a_pair_the_entry_still_asserts_elsewhere_queues_no_far_side_work(store):
    # One sense loses its synonym toward beta to the cap; another sense still asserts it,
    # so the entry still makes the claim and beta's half must stay.
    caps = RelationCaps(synonym=1)
    alpha = _entry(
        "alpha",
        senses=[
            _sense(
                0,
                [
                    _relation(RelationType.SYNONYM, "gamma", sense_id="gamma:noun:0"),
                    _relation(RelationType.SYNONYM, "beta", sense_id="beta:noun:0"),
                ],
            ),
            _sense(1, [_relation(RelationType.SYNONYM, "beta", sense_id="beta:noun:0")]),
        ],
    )
    beta = _entry(
        "beta", relations=[_relation(RelationType.SYNONYM, "alpha", sense_id="alpha:noun:0")]
    )
    gamma = _entry("gamma", relations=[])
    for entry in (alpha, beta, gamma):
        store.write(entry)

    outcome = await run_relation_reconcile(
        store, workers=4, only={RelationReconcileStep.CAP}, caps=caps
    )

    assert outcome.steps[RelationReconcileStep.CAP].far_side_removed == 0
    assert _terms(store.read("beta")) == ["alpha"]


async def test_capping_an_asymmetric_type_queues_no_far_side_work(store):
    alpha = _entry(
        "alpha",
        relations=[
            _relation(RelationType.HYPERNYM, f"parent{i}", sense_id=f"parent{i}:noun:0")
            for i in range(4)
        ],
    )
    store.write(alpha)
    store.write(_entry("parent3", relations=[_relation(RelationType.HYPONYM, "alpha")]))

    outcome = await run_relation_reconcile(store, workers=4, only={RelationReconcileStep.CAP})

    assert outcome.steps[RelationReconcileStep.CAP].far_side_removed == 0
    assert _terms(store.read("parent3")) == ["alpha"]


# --------------------------------------------------------------------------------------
# Retired senses, idempotence, dry run
# --------------------------------------------------------------------------------------


async def test_a_retired_sense_is_never_touched(store):
    # Its relations are the record of what that sense claimed before it was merged away
    # (D-52); shortening that record would be rewriting history, not reconciling it.
    retired = _sense(
        0,
        [
            _relation(RelationType.SEE_ALSO, "banners", note=HEADWORD_INFLECTION_NOTE),
            *[_relation(RelationType.HYPERNYM, f"parent{i}") for i in range(5)],
        ],
        retired=True,
    )
    live = _sense(1, [_relation(RelationType.SEE_ALSO, "gamma", note=NANO_INVALID_NOTE)])
    store.write(_entry("alpha", senses=[retired, live]))

    outcome = await run_relation_reconcile(store, workers=4)

    entry = store.read("alpha")
    assert len(_relations_of(entry, 0)) == 6
    assert _terms(entry, 1) == []
    assert outcome.steps[RelationReconcileStep.TOMBSTONE].removed == 1
    assert outcome.steps[RelationReconcileStep.CAP].removed == 0


async def test_a_retired_sense_never_supplies_a_demoted_reverse(store):
    alpha, beta = _pair()
    beta.pos_entries[0].senses[0].retired = True
    store.write(alpha)
    store.write(beta)

    outcome = await run_relation_reconcile(
        store, workers=4, only={RelationReconcileStep.ASYMMETRIC}
    )

    assert outcome.steps[RelationReconcileStep.ASYMMETRIC].demoted == 0


async def test_an_entry_the_far_side_phase_touched_is_skipped_next_sweep(store):
    # The far-side phase writes after the main sweep stamped its marker, so the marker it
    # left describes an edge set that has since changed; it is refreshed there (never
    # created, see _remove_far_side) so a rerun is a true no-op.
    caps = RelationCaps(synonym=1)
    alpha = _entry(
        "alpha",
        relations=[
            _relation(RelationType.SYNONYM, "gamma", sense_id="gamma:noun:0"),
            _relation(RelationType.SYNONYM, "beta", sense_id="beta:noun:0"),
        ],
    )
    beta = _entry(
        "beta",
        relations=[
            _relation(RelationType.SYNONYM, "alpha", sense_id="alpha:noun:0"),
            # Enough hypernyms that the main sweep changes beta too, so it carries a
            # marker for the far-side phase to refresh rather than create.
            *[_relation(RelationType.HYPERNYM, f"parent{i}") for i in range(5)],
        ],
    )
    gamma = _entry("gamma", relations=[])
    for entry in (alpha, beta, gamma):
        store.write(entry)

    first = await run_relation_reconcile(
        store, workers=4, only={RelationReconcileStep.CAP}, caps=caps
    )
    assert first.steps[RelationReconcileStep.CAP].far_side_removed == 1
    second = await run_relation_reconcile(
        store, workers=4, only={RelationReconcileStep.CAP}, caps=caps
    )

    assert second.entries_skipped == 2  # alpha and beta; gamma never changed, so unmarked
    assert second.entries_changed == 0


async def test_the_far_side_phase_never_marks_an_entry_the_sweep_skipped(store):
    # beta is not on the id list, so no step ran over it; a marker there would make a
    # later whole-store sweep skip an entry that has never been reconciled.
    caps = RelationCaps(synonym=1)
    alpha = _entry(
        "alpha",
        relations=[
            _relation(RelationType.SYNONYM, "gamma", sense_id="gamma:noun:0"),
            _relation(RelationType.SYNONYM, "beta", sense_id="beta:noun:0"),
        ],
    )
    beta = _entry(
        "beta",
        relations=[
            _relation(RelationType.SYNONYM, "alpha", sense_id="alpha:noun:0"),
            _relation(RelationType.SEE_ALSO, "junk", note=NANO_INVALID_NOTE),
        ],
    )
    gamma = _entry("gamma", relations=[])
    for entry in (alpha, beta, gamma):
        store.write(entry)

    await run_relation_reconcile(store, workers=4, lexeme_ids=["alpha"], caps=caps)
    assert not [n for n in _notes(store.read("beta")) if n.startswith(MARKER_PREFIX)]

    outcome = await run_relation_reconcile(store, workers=4, caps=caps)

    assert outcome.entries_skipped == 1  # alpha only
    assert _terms(store.read("beta")) == []


async def test_a_second_sweep_is_a_no_op_and_leaves_the_marker_alone(store):
    alpha, beta = _pair()
    _relations_of(alpha).extend(_relation(RelationType.HYPERNYM, f"parent{i}") for i in range(5))
    store.write(alpha)
    store.write(beta)

    first = await run_relation_reconcile(store, workers=4)
    assert first.entries_changed == 2
    before = store.read("alpha").model_dump_json()

    second = await run_relation_reconcile(store, workers=4)

    assert second.entries_changed == 0
    assert second.entries_skipped == 2
    assert second.demoted == 0
    assert second.removed == 0
    assert store.read("alpha").model_dump_json() == before
    markers = [note for note in _notes(store.read("alpha")) if note.startswith(MARKER_PREFIX)]
    assert len(markers) == 1


async def test_the_marker_survives_a_hundred_provenance_records(store):
    # The store serialises with orjson's OPT_SORT_KEYS, so a table with a hundred records
    # reads back p1, p10, p100, p101, p11, ... — "the last record" is not the newest one.
    entry = _entry(
        "alpha", relations=[_relation(RelationType.SEE_ALSO, "x", note=NANO_INVALID_NOTE)]
    )
    for _ in range(120):
        entry.add_provenance(
            Provenance(
                stage=StageName.HYGIENE,
                model="rule:filler",
                prompt_version="test",
                note="unrelated",
            )
        )
    store.write(entry)

    await run_relation_reconcile(store, workers=4)
    second = await run_relation_reconcile(store, workers=4)

    assert second.entries_skipped == 1


async def test_a_marker_from_a_partial_sweep_does_not_block_a_full_one(store):
    store.write(
        _entry(
            "alpha",
            relations=[
                _relation(RelationType.SEE_ALSO, "banners", note=HEADWORD_INFLECTION_NOTE),
                *[_relation(RelationType.HYPERNYM, f"parent{i}") for i in range(5)],
            ],
        )
    )

    await run_relation_reconcile(store, workers=4, only={RelationReconcileStep.TOMBSTONE})
    outcome = await run_relation_reconcile(store, workers=4)

    assert outcome.entries_skipped == 0
    assert outcome.steps[RelationReconcileStep.CAP].removed == 2


async def test_an_unchanged_entry_gains_no_provenance_record(store):
    store.write(_entry("alpha", relations=[_relation(RelationType.SYNONYM, "beta")]))
    before = len(store.read("alpha").provenance)

    outcome = await run_relation_reconcile(store, workers=4)

    assert outcome.entries_changed == 0
    assert len(store.read("alpha").provenance) == before


async def test_a_dry_run_reports_everything_and_writes_nothing(store):
    alpha, beta = _pair()
    store.write(alpha)
    store.write(beta)
    before = store.read("alpha").model_dump_json()

    outcome = await run_relation_reconcile(store, workers=4, dry_run=True)

    assert outcome.dry_run is True
    assert outcome.steps[RelationReconcileStep.ASYMMETRIC].demoted == 1
    assert outcome.steps[RelationReconcileStep.TOMBSTONE].removed == 2
    assert store.read("alpha").model_dump_json() == before


async def test_an_unknown_step_is_refused(store):
    with pytest.raises(ValueError, match="unknown relation reconcile step"):
        await run_relation_reconcile(store, workers=4, only={"nonsense"})


async def test_a_stopped_sweep_reports_that_it_stopped(store):
    for index in range(4):
        store.write(
            _entry(
                f"word{index}",
                relations=[_relation(RelationType.SEE_ALSO, "x", note=NANO_INVALID_NOTE)],
            )
        )
    stop_event = asyncio.Event()
    stop_event.set()

    outcome = await run_relation_reconcile(store, workers=1, stop_event=stop_event)

    assert outcome.stopped_reason == "stopped"
    assert outcome.entries_changed == 0


async def test_dedup_removes_exact_duplicate_edges_and_records_them(session):
    entry = _entry(
        "famine",
        relations=[
            _relation(RelationType.ENTAILS, "hunger", sense_id="hunger:noun:0"),
            _relation(RelationType.HYPERNYM, "shortage", sense_id="shortage:noun:0"),
            _relation(RelationType.ENTAILS, "hunger", sense_id="hunger:noun:0"),
            _relation(RelationType.ENTAILS, "hunger"),  # unresolved: a different key, kept
        ],
    )
    session.store.write(entry)

    outcome = await run_relation_reconcile(
        session.store, workers=2, only={RelationReconcileStep.DEDUP}
    )

    stored = session.store.read("famine")
    relations = stored.pos_entries[0].senses[0].relations
    assert [(r.type, r.target.term, r.target.sense_id) for r in relations] == [
        (RelationType.ENTAILS, "hunger", "hunger:noun:0"),
        (RelationType.HYPERNYM, "shortage", "shortage:noun:0"),
        (RelationType.ENTAILS, "hunger", None),
    ]
    step = outcome.steps[RelationReconcileStep.DEDUP]
    assert step.removed == 1
    assert any((p.note or "").startswith(DEDUP_RECORD_PREFIX) for p in stored.provenance_in_order())
    # Idempotent.
    again = await run_relation_reconcile(
        session.store, workers=2, only={RelationReconcileStep.DEDUP}
    )
    assert again.steps[RelationReconcileStep.DEDUP].removed == 0
