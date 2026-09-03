"""``audit.py`` against the pristine-entry checklist (``docs/CORE-DIARY.md``)."""

from __future__ import annotations

from pathlib import Path

from opengloss_generator.audit import audit_store
from opengloss_generator.config import StoreConfig
from opengloss_generator.schema import (
    Assessment,
    EntityType,
    Example,
    Lexeme,
    LexemeKind,
    Morphology,
    PartOfSpeech,
    POSEntry,
    ProperNounInfo,
    Provenance,
    QAFlag,
    ReadingLevel,
    Register,
    Relation,
    RelationTarget,
    RelationType,
    Rendition,
    Renditions,
    Sense,
    StageName,
    canonical_rendition,
)
from opengloss_generator.store import LexemeStore
from opengloss_generator.taxonomy import DomainTag
from tests.conftest import make_entry


def _store(tmp_path: Path) -> LexemeStore:
    """Return an empty store rooted under ``tmp_path``."""
    return LexemeStore(StoreConfig(root=tmp_path / "store", fsync_on_write=False))


def _simple_entry(headword: str, relations: list[Relation] | None = None) -> Lexeme:
    """Build a one-sense noun entry carrying ``relations``, for the graph checks."""
    sense = Sense.of(0, f"A definition of {headword}.", relations=relations or [])
    return Lexeme.empty(
        headword,
        kind=LexemeKind.SIMPLEX,
        pos_entries=[POSEntry(pos=PartOfSpeech.NOUN, senses=[sense], morphology=Morphology())],
    )


def _cat_entry() -> Lexeme:
    """Build a deliberately imperfect three-sense entry exercising every metric.

    Sense 0 and sense 2 share a canonical gloss that starts with the headword; sense 1
    is domain-less with zero relations and zero examples. Relations point at an
    unresolved synonym, an artifact-stoplist target, and a resolved hypernym whose
    target is not itself in the store.
    """
    sense0 = Sense(
        index=0,
        gloss=Renditions[str](
            root=[
                canonical_rendition("Cat is a small feline."),
                Rendition[str](
                    reading_level=ReadingLevel.GRADE_1,
                    style=Register.PLAIN,
                    content="A cat is small.",
                ),
            ]
        ),
        examples=Renditions[Example](
            root=[
                canonical_rendition(Example(text="The cat slept.", span=(4, 7))),
                Rendition[Example](
                    reading_level=ReadingLevel.GRADE_5,
                    style=Register.PLAIN,
                    content=Example(text="A young cat played."),
                ),
            ]
        ),
        domain=DomainTag.NATURE_GENERAL,
        relations=[
            Relation(type=RelationType.SYNONYM, target=RelationTarget(term="feline")),
            Relation(type=RelationType.SEE_ALSO, target=RelationTarget(term="descriptive term")),
        ],
    )
    sense1 = Sense(
        index=1,
        gloss=Renditions[str](root=[canonical_rendition("A small feline mammal.")]),
        examples=Renditions[Example](root=[]),
        domain=None,
        relations=[],
    )
    sense2 = Sense(
        index=2,
        gloss=Renditions[str](
            root=[
                canonical_rendition("Cat is a small feline."),
                Rendition[str](
                    reading_level=ReadingLevel.GRADE_1,
                    style=Register.PLAIN,
                    content="A cat, but shorter.",
                ),
            ]
        ),
        examples=Renditions[Example](
            root=[canonical_rendition(Example(text="A cat nap is short."))]
        ),
        domain=DomainTag.NATURE_GENERAL,
        relations=[
            Relation(
                type=RelationType.HYPERNYM,
                target=RelationTarget(term="mammal", sense_id="mammal:noun:0", confidence=0.9),
            ),
        ],
    )
    return Lexeme.empty(
        "cat",
        kind=LexemeKind.SIMPLEX,
        pos_entries=[
            POSEntry(
                pos=PartOfSpeech.NOUN, senses=[sense0, sense1, sense2], morphology=Morphology()
            )
        ],
        encyclopedia=Renditions[str](
            root=[
                canonical_rendition("Cats are small mammals kept as pets."),
                Rendition[str](
                    reading_level=ReadingLevel.GRADE_1,
                    style=Register.PLAIN,
                    content="Cats are small pets.",
                ),
            ]
        ),
    )


def test_kind_classified_counts_markers_and_non_placeholder_kinds(tmp_path: Path):
    store = _store(tmp_path)
    store.write(make_entry("abseil"))  # simplex, no marker: not classified

    reclassified = make_entry("rappel")
    reclassified.kind = LexemeKind.IDIOM  # not a migration placeholder: classified
    store.write(reclassified)

    marked = make_entry("descend")
    marked.add_provenance(
        Provenance(stage=StageName.CLASSIFY_KIND, model="rule:x", prompt_version="v1")
    )
    store.write(marked)

    report = audit_store(store)
    assert report.entries_total == 3
    assert report.kind_classified == 2


def test_metrics_on_a_single_imperfect_entry(tmp_path: Path):
    store = _store(tmp_path)
    store.write(_cat_entry())

    report = audit_store(store)
    payload = report.as_dict()

    assert report.entries_total == 1
    assert report.kind_classified == 0

    assert payload["senses_with_domain"] == {"count": 2, "total": 3, "pct": 66.67}
    assert payload["canonical_examples_with_span"] == {"count": 1, "total": 2, "pct": 50.0}

    assert payload["relations"]["total"] == 3
    assert payload["relations"]["resolved"] == 1
    assert payload["relations"]["by_target_location"] == {
        "not_in_store": {"total": 3, "resolved": 1, "resolved_pct": 33.33}
    }

    assert payload["artifact_relations"] == {"count": 1, "total": 3, "pct": 33.33}

    coverage = payload["rendition_coverage"]
    assert coverage["gloss"] == {
        "owners_total": 3,
        "targets": {
            "grade_1/plain": {"count": 2, "pct": 66.67},
            "neutral/plain": {"count": 3, "pct": 100.0},
        },
    }
    assert coverage["examples"] == {
        "owners_total": 3,
        "targets": {
            "grade_5/plain": {"count": 1, "pct": 33.33},
            "neutral/plain": {"count": 2, "pct": 66.67},
        },
    }
    assert coverage["encyclopedia"] == {
        "owners_total": 1,
        "targets": {
            "grade_1/plain": {"count": 1, "pct": 100.0},
            "neutral/plain": {"count": 1, "pct": 100.0},
        },
    }
    assert coverage["lexical_explanation"] == {"owners_total": 1, "targets": {}}

    assert payload["consistency"] == {
        "gloss_starts_with_headword": 2,
        # Both non-canonical gloss renditions ("A cat is small.", "A cat, but shorter.")
        # open with an article plus the headword, which is the defect D-39 measures.
        "gloss_renditions_headword_initial": {"count": 2, "total": 2, "pct": 100.0},
        "duplicate_canonical_gloss_entries": 1,
        "senses_zero_relations": 1,
        "entries_zero_examples": 0,
        "renditions_with_readability_miss_flag": 0,
    }


def test_gloss_starts_with_headword_skips_proper_nouns(tmp_path: Path):
    # A proper-noun definition legitimately names its entity (CORE-DIARY iteration 2;
    # D-30), so it must not count against the consistency check the way a common noun's
    # would.
    entry = Lexeme.empty(
        "Congo",
        kind=LexemeKind.PROPER_NOUN,
        proper_noun=ProperNounInfo(entity_type=EntityType.PLACE),
        pos_entries=[
            POSEntry(
                pos=PartOfSpeech.NOUN,
                senses=[
                    Sense.of(0, "The Congo River is a major central African river."),
                ],
                morphology=Morphology(),
            )
        ],
    )
    store = _store(tmp_path)
    store.write(entry)

    report = audit_store(store)

    assert report.gloss_starts_with_headword == 0


def test_readability_miss_flags_are_counted_but_never_written(tmp_path: Path):
    # B3 (docs/STANDARDS-PLAN.md § 3): audit.py reports counts only -- it never writes a
    # QAFlag itself, even though it can see one workflows/enrich.py wrote.
    entry = make_entry(variants=True)
    grade_1 = entry.pos_entries[0].senses[0].gloss.get(ReadingLevel.GRADE_1, Register.PLAIN)
    grade_1.assessment = Assessment(readability_grade=11.0)
    grade_1.assessment.flag(QAFlag.OG_READABILITY_MISS)

    store = _store(tmp_path)
    store.write(entry)

    report = audit_store(store)
    assert report.renditions_with_readability_miss_flag == 1
    assert report.as_dict()["consistency"]["renditions_with_readability_miss_flag"] == 1

    # Auditing again does not add a second flag or otherwise mutate the stored entry.
    reread = store.read(entry.lexeme_id)
    reread_grade_1 = reread.pos_entries[0].senses[0].gloss.get(ReadingLevel.GRADE_1, Register.PLAIN)
    assert reread_grade_1.assessment.qa_flags == [QAFlag.OG_READABILITY_MISS]
    audit_store(store)
    reread_again = store.read(entry.lexeme_id)
    again_grade_1 = (
        reread_again.pos_entries[0].senses[0].gloss.get(ReadingLevel.GRADE_1, Register.PLAIN)
    )
    assert again_grade_1.assessment.qa_flags == [QAFlag.OG_READABILITY_MISS]


def test_top_gaps_ranks_the_largest_shortfalls_first(tmp_path: Path):
    store = _store(tmp_path)
    store.write(_cat_entry())

    gaps = audit_store(store).top_gaps(3)
    assert len(gaps) == 3
    assert gaps[0].startswith("kind classified: 0/1")
    assert "gloss rendition coverage" in gaps[1]
    assert "examples rendition coverage" in gaps[2]


def test_artifact_relation_detected_by_stoplist_and_by_length(tmp_path: Path):
    store = _store(tmp_path)
    sense = Sense(
        index=0,
        gloss=Renditions[str](root=[canonical_rendition("A test definition.")]),
        relations=[
            Relation(type=RelationType.SEE_ALSO, target=RelationTarget(term="descriptive term")),
            Relation(
                type=RelationType.SEE_ALSO,
                target=RelationTarget(term="a very long six word phrase indeed"),
            ),
            Relation(type=RelationType.SYNONYM, target=RelationTarget(term="shortword")),
        ],
    )
    entry = Lexeme.empty(
        "widget",
        kind=LexemeKind.SIMPLEX,
        pos_entries=[POSEntry(pos=PartOfSpeech.NOUN, senses=[sense], morphology=Morphology())],
    )
    store.write(entry)

    report = audit_store(store)
    assert report.relations_total == 3
    assert report.artifact_relations == 2


def test_from_list_restricts_entries_and_defines_the_core_set(tmp_path: Path):
    store = _store(tmp_path)
    sense = Sense(
        index=0,
        gloss=Renditions[str](root=[canonical_rendition("A small feline.")]),
        examples=Renditions[Example](root=[canonical_rendition(Example(text="The cat ran."))]),
        relations=[
            Relation(
                type=RelationType.HYPERNYM,
                target=RelationTarget(term="mammal", sense_id="mammal:noun:0", confidence=0.9),
            ),
            Relation(type=RelationType.SYNONYM, target=RelationTarget(term="feline")),
        ],
    )
    cat = Lexeme.empty(
        "cat",
        kind=LexemeKind.SIMPLEX,
        pos_entries=[POSEntry(pos=PartOfSpeech.NOUN, senses=[sense], morphology=Morphology())],
    )
    mammal = Lexeme.empty(
        "mammal",
        kind=LexemeKind.SIMPLEX,
        pos_entries=[
            POSEntry(
                pos=PartOfSpeech.NOUN,
                senses=[Sense.of(0, "A warm-blooded vertebrate.")],
                morphology=Morphology(),
            )
        ],
    )
    outsider = Lexeme.empty(
        "outsider",
        kind=LexemeKind.SIMPLEX,
        pos_entries=[
            POSEntry(
                pos=PartOfSpeech.NOUN,
                senses=[Sense.of(0, "Not in the core list.")],
                morphology=Morphology(),
            )
        ],
    )
    for entry in (cat, mammal, outsider):
        store.write(entry)

    report = audit_store(store, core_words={"cat", "mammal"})
    assert report.entries_total == 2  # outsider excluded
    assert report.core_restricted is True
    by_location = report.as_dict()["relations"]["by_target_location"]
    assert by_location["in_core"] == {"total": 1, "resolved": 1, "resolved_pct": 100.0}
    assert by_location["not_in_store"] == {"total": 1, "resolved": 0, "resolved_pct": 0.0}

    # A word in the list that is not in the store is simply absent from the audit.
    partial = audit_store(store, core_words={"cat", "ghostword"})
    assert partial.entries_total == 1


def test_gloss_renditions_headword_initial_is_counted_read_only(tmp_path: Path):
    # D-39: the rendition-level twin of gloss_starts_with_headword. Only non-canonical
    # gloss renditions of a non-proper-noun entry are in the denominator -- the canonical
    # gloss has its own metric, and a proper noun's definition legitimately names its own
    # entity at every reading level (D-30).
    ban = Lexeme.empty(
        "ban",
        kind=LexemeKind.SIMPLEX,
        pos_entries=[
            POSEntry(
                pos=PartOfSpeech.NOUN,
                senses=[
                    Sense(
                        index=0,
                        gloss=Renditions[str](
                            root=[
                                canonical_rendition("Ban is an order that stops something."),
                                Rendition[str](
                                    reading_level=ReadingLevel.GRADE_1,
                                    style=Register.PLAIN,
                                    content="A ban is an order to stop.",
                                ),
                                Rendition[str](
                                    reading_level=ReadingLevel.GRADE_5,
                                    style=Register.PLAIN,
                                    content="An order from a leader that stops something.",
                                ),
                                Rendition[str](
                                    reading_level=ReadingLevel.NEUTRAL,
                                    style=Register.FORMAL,
                                    content="Bans are formal prohibitions on conduct.",
                                ),
                            ]
                        ),
                    )
                ],
                morphology=Morphology(),
            )
        ],
    )
    congo = Lexeme.empty(
        "Congo",
        kind=LexemeKind.PROPER_NOUN,
        proper_noun=ProperNounInfo(entity_type=EntityType.PLACE),
        pos_entries=[
            POSEntry(
                pos=PartOfSpeech.NOUN,
                senses=[
                    Sense(
                        index=0,
                        gloss=Renditions[str](
                            root=[
                                canonical_rendition("The Congo River is a major river."),
                                Rendition[str](
                                    reading_level=ReadingLevel.GRADE_1,
                                    style=Register.PLAIN,
                                    content="The Congo is a big river.",
                                ),
                            ]
                        ),
                    )
                ],
                morphology=Morphology(),
            )
        ],
    )
    store = _store(tmp_path)
    store.write(ban)
    store.write(congo)

    report = audit_store(store)

    # Three of ban's renditions are counted (its canonical is not); two of those three
    # open with the headword. Congo contributes nothing at all.
    assert report.gloss_renditions_checked == 3
    assert report.gloss_renditions_headword_initial == 2
    assert report.as_dict()["consistency"]["gloss_renditions_headword_initial"] == {
        "count": 2,
        "total": 3,
        "pct": 66.67,
    }


def test_hypernym_cycle_detected_across_three_entries(tmp_path: Path):
    # alpha -> beta -> gamma -> alpha, entirely via resolved hypernym relations across
    # three separate entries.
    alpha = _simple_entry(
        "alpha",
        [
            Relation(
                type=RelationType.HYPERNYM,
                target=RelationTarget(term="beta", sense_id="beta:noun:0"),
            )
        ],
    )
    beta = _simple_entry(
        "beta",
        [
            Relation(
                type=RelationType.HYPERNYM,
                target=RelationTarget(term="gamma", sense_id="gamma:noun:0"),
            )
        ],
    )
    gamma = _simple_entry(
        "gamma",
        [
            Relation(
                type=RelationType.HYPERNYM,
                target=RelationTarget(term="alpha", sense_id="alpha:noun:0"),
            )
        ],
    )
    store = _store(tmp_path)
    for entry in (alpha, beta, gamma):
        store.write(entry)

    report = audit_store(store)

    assert report.hypernym_cycle_count == 1
    assert len(report.hypernym_cycle_examples) == 1
    assert set(report.hypernym_cycle_examples[0]) == {
        "alpha:noun:0",
        "beta:noun:0",
        "gamma:noun:0",
    }
    assert report.as_dict()["graph"]["hypernym_cycles"]["count"] == 1


def test_hyponym_reversed_direction_consistent_is_not_a_cycle(tmp_path: Path):
    # alpha's hypernym is beta (alpha -> beta). beta separately claims alpha as ITS
    # hyponym -- reversed, a hyponym relation from beta to alpha is a hypernym edge
    # alpha -> beta, the very same edge alpha already asserts. One edge, no cycle.
    alpha = _simple_entry(
        "alpha",
        [
            Relation(
                type=RelationType.HYPERNYM,
                target=RelationTarget(term="beta", sense_id="beta:noun:0"),
            )
        ],
    )
    beta = _simple_entry(
        "beta",
        [
            Relation(
                type=RelationType.HYPONYM,
                target=RelationTarget(term="alpha", sense_id="alpha:noun:0"),
            )
        ],
    )
    store = _store(tmp_path)
    store.write(alpha)
    store.write(beta)

    report = audit_store(store)

    assert report.hypernym_cycle_count == 0
    assert report.hypernym_cycle_examples == []


def test_hyponym_reversed_direction_contradictory_is_a_cycle(tmp_path: Path):
    # alpha asserts both that beta is its hypernym (edge alpha -> beta) and that beta is
    # its hyponym (reversed: edge beta -> alpha) -- the two relations cannot both be
    # true, and together close a 2-cycle purely through the hyponym reversal.
    alpha = _simple_entry(
        "alpha",
        [
            Relation(
                type=RelationType.HYPERNYM,
                target=RelationTarget(term="beta", sense_id="beta:noun:0"),
            ),
            Relation(
                type=RelationType.HYPONYM,
                target=RelationTarget(term="beta", sense_id="beta:noun:0"),
            ),
        ],
    )
    beta = _simple_entry("beta")
    store = _store(tmp_path)
    store.write(alpha)
    store.write(beta)

    report = audit_store(store)

    assert report.hypernym_cycle_count == 1
    assert set(report.hypernym_cycle_examples[0]) == {"alpha:noun:0", "beta:noun:0"}


def test_hypernym_self_loop_flags_same_lexeme_different_senses(tmp_path: Path):
    sense0 = Sense.of(
        0,
        "First sense of dual.",
        relations=[
            Relation(
                type=RelationType.HYPERNYM,
                target=RelationTarget(term="dual", sense_id="dual:noun:1"),
            )
        ],
    )
    sense1 = Sense.of(1, "Second sense of dual.")
    entry = Lexeme.empty(
        "dual",
        kind=LexemeKind.SIMPLEX,
        pos_entries=[
            POSEntry(pos=PartOfSpeech.NOUN, senses=[sense0, sense1], morphology=Morphology())
        ],
    )
    store = _store(tmp_path)
    store.write(entry)

    report = audit_store(store)

    assert report.hypernym_self_loops == 1
    assert report.as_dict()["graph"]["hypernym_self_loops"] == 1
    # A same-lexeme edge with no return edge is not itself a cycle.
    assert report.hypernym_cycle_count == 0


def test_unresolved_hypernym_relations_are_ignored_by_graph_checks(tmp_path: Path):
    # Unresolved: would be a self-loop (and, paired with a return, a cycle) if resolved,
    # but an unresolved target cannot close anything.
    alpha = _simple_entry(
        "alpha",
        [Relation(type=RelationType.HYPERNYM, target=RelationTarget(term="alpha"))],
    )
    store = _store(tmp_path)
    store.write(alpha)

    report = audit_store(store)

    assert report.hypernym_cycle_count == 0
    assert report.hypernym_self_loops == 0
    assert report.hypernym_cycle_examples == []


def test_reciprocity_counts_exact_on_a_hand_built_pair(tmp_path: Path):
    alpha = _simple_entry(
        "alpha",
        [
            Relation(
                type=RelationType.SYNONYM,
                target=RelationTarget(term="beta", sense_id="beta:noun:0"),
            ),
            Relation(
                type=RelationType.ANTONYM,
                target=RelationTarget(term="gamma", sense_id="gamma:noun:0"),
            ),
        ],
    )
    beta = _simple_entry(
        "beta",
        [
            Relation(
                type=RelationType.SYNONYM,
                target=RelationTarget(term="alpha", sense_id="alpha:noun:0"),
            )
        ],
    )
    gamma = _simple_entry("gamma")  # never reciprocates alpha's antonym claim
    store = _store(tmp_path)
    for entry in (alpha, beta, gamma):
        store.write(entry)

    report = audit_store(store)
    reciprocity = report.as_dict()["graph"]["reciprocity"]

    # Both alpha -> beta and beta -> alpha are resolved synonym assertions, each counted
    # (and, since each finds the other, each reciprocated) on its own.
    assert reciprocity["synonym"] == {"asserted": 2, "reciprocated": 2, "pct": 100.0}
    assert reciprocity["antonym"] == {"asserted": 1, "reciprocated": 0, "pct": 0.0}
    assert reciprocity["confusable_with"] == {"asserted": 0, "reciprocated": 0, "pct": 0.0}


def test_reciprocity_ignores_unresolved_assertions_but_not_unresolved_reciprocation(
    tmp_path: Path,
):
    # alpha's claim is unresolved, so it is not counted as asserted at all.
    alpha = _simple_entry(
        "alpha",
        [Relation(type=RelationType.SYNONYM, target=RelationTarget(term="beta"))],
    )
    # beta's own claim IS resolved, and reciprocates unrelated to alpha's -- it asserts
    # synonym toward alpha and is itself the target of nothing resolved, so it counts as
    # asserted with no reciprocation.
    beta = _simple_entry(
        "beta",
        [
            Relation(
                type=RelationType.SYNONYM,
                target=RelationTarget(term="alpha", sense_id="alpha:noun:0"),
            )
        ],
    )
    store = _store(tmp_path)
    store.write(alpha)
    store.write(beta)

    report = audit_store(store)
    reciprocity = report.as_dict()["graph"]["reciprocity"]

    # beta -> alpha is the only resolved assertion; alpha's own relation to beta is
    # unresolved so it does not count as "asserted", but it is exactly the kind of thing
    # that makes beta's assertion reciprocated -- resolution is never required on the
    # far side (D-40).
    assert reciprocity["synonym"] == {"asserted": 1, "reciprocated": 1, "pct": 100.0}


def test_top_gaps_lists_hypernym_cycle_first_when_present(tmp_path: Path):
    alpha = _simple_entry(
        "alpha",
        [
            Relation(
                type=RelationType.HYPERNYM,
                target=RelationTarget(term="beta", sense_id="beta:noun:0"),
            )
        ],
    )
    beta = _simple_entry(
        "beta",
        [
            Relation(
                type=RelationType.HYPERNYM,
                target=RelationTarget(term="alpha", sense_id="alpha:noun:0"),
            )
        ],
    )
    store = _store(tmp_path)
    store.write(alpha)
    store.write(beta)

    gaps = audit_store(store).top_gaps(3)

    assert gaps[0].startswith("hypernym cycles: 1")
    assert len(gaps) == 3
