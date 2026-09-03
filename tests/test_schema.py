"""Schema v3 invariants.

The properties defended here are the ones that would silently corrupt the graph if they
regressed: edges are derived rather than stored, rendition keys are unique, sense
indices are positional and contiguous, ids are recomputable, unknown fields are refused,
and an edge id survives resolution unchanged.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from opengloss_generator.identity import rendition_id, variant_id
from opengloss_generator.schema import (
    SCHEMA_VERSION,
    Assessment,
    EntityType,
    Example,
    Lexeme,
    LexemeKind,
    PartOfSpeech,
    POSEntry,
    ProperNounInfo,
    QAFlag,
    ReadingLevel,
    Register,
    Relation,
    RelationTarget,
    RelationType,
    Rendition,
    Renditions,
    Sense,
    canonical_rendition,
    project_concept_id,
)
from tests.conftest import make_entry


def gloss(text: str = "a gloss") -> Renditions[str]:
    """Return a gloss set holding only the canonical rendition."""
    return Renditions[str](root=[canonical_rendition(text)])


def example(text: str, span: tuple[int, int] | None = None) -> Rendition[Example]:
    """Return a canonical example rendition."""
    return canonical_rendition(Example(text=text, span=span))


# --------------------------------------------------------------------------------------
# Derived edges
# --------------------------------------------------------------------------------------


def test_edges_are_derived_not_stored():
    entry = make_entry()
    edges = entry.edges()
    assert {e.relation.value for e in edges} == {"synonym", "hypernym"}
    assert all(e.source_sense == "abseil:verb:0" for e in edges)
    # The projection is a function of the senses; it cannot drift from them.
    assert entry.edges() == edges
    assert "edges" not in entry.model_dump()


def test_retired_senses_do_not_emit_edges():
    entry = make_entry()
    entry.pos_entries[0].senses[0].retired = True
    assert entry.edges() == []


def test_edge_ids_use_the_target_slug():
    entry = make_entry()
    entry.pos_entries[0].senses[0].relations.append(
        Relation(type=RelationType.SEE_ALSO, target=RelationTarget(term="Rope Access"))
    )
    assert entry.edges()[-1].edge_id == "abseil:verb:0-see_also->rope_access"
    assert entry.edges()[-1].target == "Rope Access"


def test_edge_id_is_unchanged_by_resolution():
    entry = make_entry()
    before = [e.edge_id for e in entry.edges()]
    target = entry.pos_entries[0].senses[0].relations[0].target
    target.sense_id = "rappel:verb:0"
    target.confidence = 0.91
    resolved = entry.edges()
    assert [e.edge_id for e in resolved] == before
    assert resolved[0].target_sense == "rappel:verb:0"
    assert resolved[0].confidence == 0.91
    assert target.resolved


def test_relation_targets_and_sense_helpers():
    entry = make_entry()
    sense = entry.pos_entries[0].senses[0]
    assert entry.relation_targets() == {"rappel", "descend"}
    assert sense.relation_targets() == {"rappel", "descend"}
    assert [r.target.term for r in sense.relations_of(RelationType.SYNONYM)] == ["rappel"]
    assert sense.relations_of(RelationType.MERONYM) == []
    assert sense.canonical_gloss().startswith("To descend")
    assert entry.sense_count() == 1


def test_relation_target_lexeme_id_is_derived():
    target = RelationTarget(term="  Ice Axe  ")
    assert target.term == "Ice Axe"
    assert target.lexeme_id == "ice_axe"
    assert not target.resolved


def test_a_target_with_no_sluggable_content_is_rejected():
    with pytest.raises(ValidationError, match="empty slug"):
        RelationTarget(term="!!!")


# --------------------------------------------------------------------------------------
# Validators
# --------------------------------------------------------------------------------------


def test_confusable_relations_require_a_note():
    with pytest.raises(ValidationError, match="requires a note"):
        Relation(type=RelationType.CONFUSABLE_WITH, target=RelationTarget(term="abseil"))
    ok = Relation(
        type=RelationType.CONFUSABLE_WITH,
        target=RelationTarget(term="rappel"),
        note="'rappel' is the American term for the same manoeuvre.",
    )
    assert ok.note is not None


def test_other_relation_types_do_not_require_a_note():
    assert Relation(type=RelationType.SYNONYM, target=RelationTarget(term="rappel")).note is None


def test_a_sense_needs_a_canonical_gloss():
    only_grade_1 = Renditions[str](
        root=[
            Rendition[str](
                reading_level=ReadingLevel.GRADE_1, style=Register.PLAIN, content="simple"
            )
        ]
    )
    with pytest.raises(ValidationError, match="no canonical"):
        Sense(index=0, gloss=only_grade_1)


def test_a_blank_canonical_gloss_is_rejected():
    with pytest.raises(ValidationError, match="empty canonical gloss"):
        Sense(index=0, gloss=Renditions[str](root=[canonical_rendition("   ")]))


@pytest.mark.parametrize("span", [(-1, 3), (3, 3), (5, 2), (0, 99)])
def test_example_spans_must_lie_within_the_text(span):
    with pytest.raises(ValidationError, match="out of bounds"):
        Example(text="They abseiled.", span=span)


def test_example_span_selects_the_headword_form():
    instance = Example(text="They abseiled down.", span=(5, 13))
    assert instance.matched == "abseiled"
    assert Example(text="No span here.").matched is None


def test_proper_noun_block_is_required_exactly_for_proper_nouns():
    with pytest.raises(ValidationError, match="proper_noun block is missing"):
        Lexeme.empty("Everest", kind=LexemeKind.PROPER_NOUN)
    with pytest.raises(ValidationError, match="proper_noun block present"):
        Lexeme.empty(
            "abseil",
            kind=LexemeKind.SIMPLEX,
            proper_noun=ProperNounInfo(entity_type=EntityType.PLACE),
        )
    ok = Lexeme.empty(
        "Everest",
        kind=LexemeKind.PROPER_NOUN,
        proper_noun=ProperNounInfo(entity_type=EntityType.PLACE, wikidata_qid="Q513"),
    )
    assert ok.proper_noun is not None
    assert ok.proper_noun.wikidata_qid == "Q513"


def test_wikidata_qids_must_look_like_qids():
    with pytest.raises(ValidationError):
        ProperNounInfo(entity_type=EntityType.PLACE, wikidata_qid="513")


def test_function_words_are_stopwords_by_construction():
    entry = Lexeme.empty("the", kind=LexemeKind.FUNCTION_WORD)
    assert entry.is_stopword is True


def test_sense_indices_must_be_contiguous():
    # Sense ids are positional, so a gap would silently re-point every downstream edge.
    with pytest.raises(ValidationError, match="must be"):
        POSEntry(
            pos=PartOfSpeech.NOUN,
            senses=[Sense(index=0, gloss=gloss("first")), Sense(index=2, gloss=gloss("third"))],
        )


def test_lexeme_id_must_match_headword():
    with pytest.raises(ValidationError, match="slug"):
        Lexeme(lexeme_id="wrong", headword="abseil", kind=LexemeKind.SIMPLEX)


def test_duplicate_parts_of_speech_are_rejected():
    with pytest.raises(ValidationError, match="duplicate part-of-speech"):
        Lexeme.empty(
            "abseil",
            pos_entries=[
                POSEntry(pos=PartOfSpeech.NOUN, senses=[Sense(index=0, gloss=gloss("a"))]),
                POSEntry(pos=PartOfSpeech.NOUN, senses=[Sense(index=0, gloss=gloss("b"))]),
            ],
        )


def test_unknown_fields_are_rejected():
    with pytest.raises(ValidationError):
        Lexeme.model_validate(
            {"lexeme_id": "abseil", "headword": "abseil", "kind": "simplex", "surprise": 1}
        )


def test_kind_is_required():
    with pytest.raises(ValidationError, match="kind"):
        Lexeme.model_validate({"lexeme_id": "abseil", "headword": "abseil"})


# --------------------------------------------------------------------------------------
# Renditions
# --------------------------------------------------------------------------------------


def test_duplicate_rendition_keys_are_rejected():
    duplicate = Rendition[str](
        reading_level=ReadingLevel.GRADE_1, style=Register.PLAIN, content="simple text"
    )
    with pytest.raises(ValidationError, match="duplicate rendition"):
        Renditions[str](root=[canonical_rendition("a gloss"), duplicate, duplicate.model_copy()])


def test_add_refuses_a_duplicate_key():
    renditions = gloss()
    with pytest.raises(ValueError, match="duplicate rendition"):
        renditions.add(canonical_rendition("a different gloss at the same key"))


def test_examples_are_unique_on_level_style_and_text():
    # Several canonical examples per sense are legitimate; the same text twice is not.
    ok = Renditions[Example](root=[example("First sentence."), example("Second sentence.")])
    assert len(ok) == 2
    with pytest.raises(ValidationError, match="duplicate rendition"):
        Renditions[Example](root=[example("Same sentence."), example("Same sentence.")])


def test_rendition_lookup_helpers():
    renditions = gloss("neutral text")
    grade_5 = Rendition[str](
        reading_level=ReadingLevel.GRADE_5, style=Register.PLAIN, content="simpler text"
    )
    renditions.add(grade_5)
    canonical = renditions.canonical()
    assert canonical is not None
    assert canonical.content == "neutral text"
    assert canonical.is_canonical
    assert canonical.key == (ReadingLevel.NEUTRAL, Register.PLAIN)
    assert renditions.has(ReadingLevel.GRADE_5, Register.PLAIN)
    assert not renditions.has(ReadingLevel.GRADE_5, Register.TECHNICAL)
    assert renditions.get(ReadingLevel.GRADE_5, Register.PLAIN) is grade_5
    assert renditions.get(ReadingLevel.COLLEGE, Register.PLAIN) is None
    assert len(renditions) == 2
    assert list(renditions) == [canonical, grade_5]
    assert renditions[1] is grade_5
    assert bool(renditions)


def test_missing_returns_the_absent_targets_in_request_order():
    renditions = gloss()
    wanted = [
        (ReadingLevel.COLLEGE, Register.TECHNICAL),
        (ReadingLevel.NEUTRAL, Register.PLAIN),
        (ReadingLevel.GRADE_1, Register.PLAIN),
        (ReadingLevel.COLLEGE, Register.TECHNICAL),
    ]
    assert renditions.missing(wanted) == [
        (ReadingLevel.COLLEGE, Register.TECHNICAL),
        (ReadingLevel.GRADE_1, Register.PLAIN),
    ]
    assert gloss().missing([]) == []


def test_renditions_serialise_as_a_bare_list_with_the_register_alias():
    dumped = make_entry(variants=True).model_dump(mode="json")
    glosses = dumped["pos_entries"][0]["senses"][0]["gloss"]
    assert isinstance(glosses, list)
    assert glosses[0]["register"] == "plain"
    assert "style" not in glosses[0]


def test_an_empty_rendition_set_is_the_default_for_optional_prose():
    entry = Lexeme.empty("abseil")
    assert len(entry.encyclopedia) == 0
    assert len(entry.lexical_explanation) == 0
    assert entry.encyclopedia.canonical() is None
    # Defaults must not be shared between instances.
    entry.encyclopedia.add(canonical_rendition("prose"))
    assert len(Lexeme.empty("rappel").encyclopedia) == 0


def test_assessments_ride_along_with_a_rendition():
    rendition = canonical_rendition("a gloss")
    rendition.assessment = Assessment(readability_grade=8.2, qa_flags=[QAFlag.AWKWARD_STYLE])
    assert rendition.assessment.human_verified is False


# --------------------------------------------------------------------------------------
# Register (D-27)
# --------------------------------------------------------------------------------------


def test_only_marketing_is_a_genre_register():
    for member in Register:
        expected = member is Register.MARKETING
        assert member.is_genre is expected


def test_tbx_register_map_has_an_entry_for_every_member():
    from opengloss_generator.schema import TBX_REGISTER_MAP  # noqa: PLC0415

    assert set(TBX_REGISTER_MAP) == set(Register)
    assert TBX_REGISTER_MAP[Register.PLAIN] == "neutralRegister"
    assert TBX_REGISTER_MAP[Register.INFORMAL] == "colloquialRegister"
    assert TBX_REGISTER_MAP[Register.TECHNICAL] == "technicalRegister"
    assert TBX_REGISTER_MAP[Register.SLANG] == "slangRegister"
    assert TBX_REGISTER_MAP[Register.IN_HOUSE] == "in-houseRegister"
    assert TBX_REGISTER_MAP[Register.FORMAL] is None
    assert TBX_REGISTER_MAP[Register.MARKETING] is None


def test_formality_order_excludes_technical_in_house_and_marketing():
    from opengloss_generator.schema import FORMALITY_ORDER  # noqa: PLC0415

    assert FORMALITY_ORDER == (
        Register.SLANG,
        Register.INFORMAL,
        Register.PLAIN,
        Register.FORMAL,
    )
    assert Register.TECHNICAL not in FORMALITY_ORDER
    assert Register.IN_HOUSE not in FORMALITY_ORDER
    assert Register.MARKETING not in FORMALITY_ORDER


def test_the_legacy_professional_value_loads_as_formal_and_round_trips():
    # D-27: "professional" was renamed to "formal"; old stored data must keep loading.
    assert Register("professional") is Register.FORMAL
    rendition = Rendition[str].model_validate(
        {"reading_level": "neutral", "register": "professional", "content": "a gloss"}
    )
    assert rendition.style is Register.FORMAL
    assert rendition.model_dump(mode="json")["register"] == "formal"


# --------------------------------------------------------------------------------------
# Identity and round-tripping
# --------------------------------------------------------------------------------------


def test_rendition_ids_cover_glosses_and_entry_level_prose():
    entry = make_entry(variants=True)
    entry.encyclopedia.add(canonical_rendition("Encyclopedic prose."))
    entry.lexical_explanation.add(canonical_rendition("When to reach for it."))
    assert entry.rendition_ids() == [
        "abseil:verb:0#neutral/plain",
        "abseil:verb:0#grade_1/plain",
        "abseil:encyclopedia#neutral/plain",
        "abseil:explanation#neutral/plain",
    ]


def test_variant_id_is_a_deprecated_alias_of_rendition_id():
    assert variant_id("abseil:verb:0", "grade_5", "plain") == rendition_id(
        "abseil:verb:0", "grade_5", "plain"
    )


def test_provenance_table_is_keyed_and_ids_are_handed_out_in_order():
    entry = make_entry()
    from opengloss_generator.schema import Provenance, StageName  # noqa: PLC0415

    first = entry.add_provenance(Provenance(stage=StageName.SENSES, model="m", prompt_version="v1"))
    second = entry.add_provenance(
        Provenance(stage=StageName.RENDITIONS, model="m", prompt_version="v1")
    )
    assert (first, second) == ("p1", "p2")
    assert set(entry.provenance) == {"p1", "p2"}


def test_pos_ids_and_sense_ids_are_positional():
    entry = make_entry()
    assert entry.pos_ids() == ["abseil:verb"]
    assert [sid for _, _, sid in entry.iter_senses()] == ["abseil:verb:0"]


def test_round_trips_through_json():
    entry = make_entry(variants=True)
    restored = Lexeme.model_validate(entry.model_dump(mode="json"))
    assert restored == entry
    assert restored.rendition_ids() == entry.rendition_ids()
    assert restored.edges() == entry.edges()
    assert restored.schema_version == SCHEMA_VERSION
    assert restored.pos_entries[0].senses[0].examples[0].content.span == (5, 13)


# --------------------------------------------------------------------------------------
# A3 — Zipf frequency (docs/STANDARDS-PLAN.md § 2)
# --------------------------------------------------------------------------------------


def test_compute_zipf_fills_zipf_when_frequency_and_corpus_tokens_are_set():
    entry = make_entry()
    entry.frequency = 1000
    entry.frequency_corpus_tokens = 1_000_000_000
    entry.compute_zipf()
    assert entry.zipf == pytest.approx(3.0, abs=1e-3)


@pytest.mark.parametrize(
    ("frequency", "frequency_corpus_tokens"),
    [(None, 1_000_000_000), (1000, None), (None, None)],
)
def test_compute_zipf_leaves_zipf_none_when_either_input_is_missing(
    frequency, frequency_corpus_tokens
):
    entry = make_entry()
    entry.frequency = frequency
    entry.frequency_corpus_tokens = frequency_corpus_tokens
    entry.compute_zipf()
    assert entry.zipf is None


def test_zipf_fields_default_to_none():
    entry = make_entry()
    assert entry.zipf is None
    assert entry.frequency_corpus is None
    assert entry.frequency_corpus_tokens is None


# --------------------------------------------------------------------------------------
# A5 — concept_id format (docs/STANDARDS-PLAN.md § 2)
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("concept_id", ["ili:i1", "ili:i35545", "og:c-0123456789abcdef"])
def test_concept_id_accepts_ili_and_project_shapes(concept_id):
    sense = Sense.of(0, "a gloss", concept_id=concept_id)
    assert sense.concept_id == concept_id


@pytest.mark.parametrize(
    "concept_id",
    [
        "ili:i0",  # leading zero
        "ili:i01",  # leading zero
        "ili:i",  # no digits
        "og:c-0123456789abcde",  # 15 hex chars
        "og:c-0123456789abcdefg",  # 17 chars, and 'g' is not hex
        "og:c-0123456789ABCDEF",  # uppercase not allowed
        "wn:35545",  # wrong scheme entirely
    ],
)
def test_concept_id_rejects_anything_else(concept_id):
    with pytest.raises(ValidationError, match="concept_id"):
        Sense.of(0, "a gloss", concept_id=concept_id)


def test_project_concept_id_is_deterministic_and_order_independent():
    members = ["abseil:verb:0", "rappel:verb:0"]
    first = project_concept_id(members)
    second = project_concept_id(reversed(members))
    assert first == second
    assert first.startswith("og:c-")
    assert len(first) == len("og:c-") + 16
    assert all(c in "0123456789abcdef" for c in first.removeprefix("og:c-"))


def test_project_concept_id_differs_for_different_members():
    assert project_concept_id(["a:verb:0"]) != project_concept_id(["b:verb:0"])


# --------------------------------------------------------------------------------------
# A1 -- PartOfSpeech -> UPOS / LexInfo (docs/STANDARDS-PLAN.md § 2)
# --------------------------------------------------------------------------------------


def test_upos_map_covers_every_part_of_speech():
    from opengloss_generator.schema import UPOS_MAP  # noqa: PLC0415

    assert set(UPOS_MAP) == set(PartOfSpeech)
    assert UPOS_MAP[PartOfSpeech.NOUN] == "NOUN"
    assert UPOS_MAP[PartOfSpeech.VERB] == "VERB"
    assert UPOS_MAP[PartOfSpeech.ADJECTIVE] == "ADJ"
    assert UPOS_MAP[PartOfSpeech.ADVERB] == "ADV"
    assert UPOS_MAP[PartOfSpeech.PRONOUN] == "PRON"
    assert UPOS_MAP[PartOfSpeech.PREPOSITION] == "ADP"
    # Lossy: UD's CCONJ/SCONJ split collapses onto our single `conjunction` value.
    assert UPOS_MAP[PartOfSpeech.CONJUNCTION] == "CCONJ"
    assert UPOS_MAP[PartOfSpeech.DETERMINER] == "DET"
    assert UPOS_MAP[PartOfSpeech.INTERJECTION] == "INTJ"
    assert UPOS_MAP[PartOfSpeech.NUMERAL] == "NUM"


def test_lexinfo_map_covers_every_part_of_speech():
    from opengloss_generator.schema import LEXINFO_MAP  # noqa: PLC0415

    assert set(LEXINFO_MAP) == set(PartOfSpeech)
    assert all(value.startswith("lexinfo:") for value in LEXINFO_MAP.values())


def test_part_of_speech_upos_property_matches_the_map():
    from opengloss_generator.schema import UPOS_MAP  # noqa: PLC0415

    for member in PartOfSpeech:
        assert member.upos == UPOS_MAP[member]


def test_upos_for_reports_propn_only_for_a_proper_noun_common_noun_pos():
    from opengloss_generator.schema import upos_for  # noqa: PLC0415

    proper = Lexeme.empty(
        "Paris",
        kind=LexemeKind.PROPER_NOUN,
        proper_noun=ProperNounInfo(entity_type=EntityType.PLACE),
    )
    common = Lexeme.empty("city")
    assert upos_for(proper, PartOfSpeech.NOUN) == "PROPN"
    # A proper noun's non-noun POS entries (rare, but not forbidden) are unaffected.
    assert upos_for(proper, PartOfSpeech.ADJECTIVE) == PartOfSpeech.ADJECTIVE.upos
    assert upos_for(common, PartOfSpeech.NOUN) == "NOUN"


# --------------------------------------------------------------------------------------
# A2 -- EtymologySegment.language_code -> ISO 639-3 (docs/STANDARDS-PLAN.md § 2)
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("language_code", ["lat", "grc", "ang", "ine-pro", "gem-pro"])
def test_language_code_accepts_iso_639_3_and_reconstructed_exceptions(language_code):
    from opengloss_generator.schema import EtymologySegment  # noqa: PLC0415

    segment = EtymologySegment(language="x", language_code=language_code, form="y")
    assert segment.language_code == language_code


@pytest.mark.parametrize(
    "language_code",
    [
        "ENG",  # uppercase
        "en",  # ISO 639-1, not 639-3
        "engl",  # four letters
        "proto-indo-european",  # display name, not a code
    ],
)
def test_language_code_rejects_anything_else(language_code):
    from opengloss_generator.schema import EtymologySegment  # noqa: PLC0415

    with pytest.raises(ValidationError, match="language_code"):
        EtymologySegment(language="x", language_code=language_code, form="y")


def test_language_code_defaults_to_none():
    from opengloss_generator.schema import EtymologySegment  # noqa: PLC0415

    segment = EtymologySegment(language="Latin", form="habilis")
    assert segment.language_code is None


# --------------------------------------------------------------------------------------
# A6 -- ReadingLevel crosswalk (docs/STANDARDS-PLAN.md § 2)
# --------------------------------------------------------------------------------------


def test_reading_level_crosswalk_covers_every_member():
    from opengloss_generator.schema import READING_LEVEL_CROSSWALK, LevelCrosswalk  # noqa: PLC0415

    assert set(READING_LEVEL_CROSSWALK) == set(ReadingLevel)
    for level in ReadingLevel:
        assert isinstance(level.crosswalk, LevelCrosswalk)
        assert level.crosswalk == READING_LEVEL_CROSSWALK[level]
    assert ReadingLevel.GRADE_5.crosswalk.ccss_band == "4th-5th"
    assert ReadingLevel.COLLEGE.crosswalk.cefr == "C1/C2"


def test_fk_bands_covers_every_reading_level():
    from opengloss_generator.schema import FK_BANDS  # noqa: PLC0415

    assert set(FK_BANDS) == set(ReadingLevel)


# --------------------------------------------------------------------------------------
# B1-lite -- RelationType namespace + WordNet/SKOS export maps (docs/STANDARDS-PLAN.md § 3, § 8)
# --------------------------------------------------------------------------------------


def test_relation_type_namespace_is_og_only_for_the_three_project_only_relations():
    for member in RelationType:
        expected = member in {
            RelationType.CONFUSABLE_WITH,
            RelationType.USED_WITH,
            RelationType.COLLOCATION,
        }
        assert (member.namespace == "og") is expected
        if not expected:
            assert member.namespace == "wn"


def test_wn_relation_map_covers_every_non_og_relation_type():
    from opengloss_generator.schema import WN_RELATION_MAP  # noqa: PLC0415

    for member in RelationType:
        if member.namespace == "wn":
            assert member in WN_RELATION_MAP
        else:
            assert member not in WN_RELATION_MAP
    assert WN_RELATION_MAP[RelationType.HYPERNYM] == "hypernym"
    assert WN_RELATION_MAP[RelationType.INSTANCE_OF] == "instance_hypernym"


def test_skos_relation_map_is_a_partial_supplement():
    from opengloss_generator.schema import SKOS_RELATION_MAP  # noqa: PLC0415

    assert set(SKOS_RELATION_MAP) <= set(RelationType)
    assert SKOS_RELATION_MAP[RelationType.HYPERNYM] == "skos:broader"


# --------------------------------------------------------------------------------------
# B2-lite -- EntityType OntoNotes/Schema.org export maps (docs/STANDARDS-PLAN.md § 3, § 8)
# --------------------------------------------------------------------------------------


def test_ontonotes_map_covers_every_entity_type_including_the_species_gap():
    from opengloss_generator.schema import ONTONOTES_MAP  # noqa: PLC0415

    assert set(ONTONOTES_MAP) == set(EntityType)
    assert ONTONOTES_MAP[EntityType.PERSON] == "PERSON"
    assert ONTONOTES_MAP[EntityType.PLACE] == "GPE"  # lossy: GPE vs LOC not distinguished
    # No OntoNotes type covers species/organisms -- a documented gap, not a guess.
    assert ONTONOTES_MAP[EntityType.SPECIES] is None
    assert ONTONOTES_MAP[EntityType.OTHER] is None


def test_schema_org_map_covers_every_entity_type():
    from opengloss_generator.schema import SCHEMA_ORG_MAP  # noqa: PLC0415

    assert set(SCHEMA_ORG_MAP) == set(EntityType)
    assert SCHEMA_ORG_MAP[EntityType.PERSON] == "Person"
    # Species has no OntoNotes home but does have a dedicated Schema.org type.
    assert SCHEMA_ORG_MAP[EntityType.SPECIES] == "Taxon"
    assert SCHEMA_ORG_MAP[EntityType.OTHER] == "Thing"


# --------------------------------------------------------------------------------------
# B3 -- QAFlag (docs/STANDARDS-PLAN.md § 3)
# --------------------------------------------------------------------------------------


def test_qa_flags_rejects_unknown_strings():
    with pytest.raises(ValidationError, match="qa_flags"):
        Assessment.model_validate({"qa_flags": ["terse"]})


def test_qa_flags_accepts_project_flags():
    assessment = Assessment(qa_flags=[QAFlag.OG_HEADWORD_INITIAL, QAFlag.OG_READABILITY_MISS])
    assert assessment.qa_flags == [QAFlag.OG_HEADWORD_INITIAL, QAFlag.OG_READABILITY_MISS]


def test_assessment_flag_helper_is_idempotent():
    assessment = Assessment()
    assessment.flag(QAFlag.GRAMMAR_ERROR)
    assessment.flag(QAFlag.GRAMMAR_ERROR)
    assert assessment.qa_flags == [QAFlag.GRAMMAR_ERROR]
