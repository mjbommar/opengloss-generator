"""Migration of v1.3 and v2.0 payloads onto the v3 schema.

``V13_ALLUDING`` is a trimmed but otherwise verbatim copy of
``/nas4/data/workspace/curriculum/data/lexicon/alluding.json``: three verb senses, the
real definitions, examples, morphology and etymology, with the second part-of-speech
entry and most of the list tails removed. ``V13_EINSTEIN`` is the same treatment of
``einstein.json``, kept because it is the case D-26 exists for: a lowercase headword whose
own prose is the only evidence it names a person. The tests never touch ``/nas4``.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from opengloss_generator.migrate import (
    classify_kind_deterministic,
    detect_version,
    from_v2,
    from_v13,
    migrate,
)
from opengloss_generator.schema import (
    EntityType,
    EntryStatus,
    Lexeme,
    LexemeKind,
    PartOfSpeech,
    ReadingLevel,
    Register,
    RelationType,
)
from opengloss_generator.taxonomy import DomainTag

V13_ALLUDING: dict = {
    "id": "alluding",
    "created_at": "2025-11-28T11:11:33.218073Z",
    "updated_at": "2025-11-28T11:11:33.218073Z",
    "language": "en",
    "tags": ["domain:language"],
    "word": "alluding",
    "stopword": {"id": "9f95", "is_stopword": False, "reason": "carries lexical meaning"},
    "entries": [
        {
            "id": "1956",
            "pos": "verb",
            "senses": [
                {
                    "id": "5f98",
                    "definition": (
                        "In general usage, to refer to something indirectly by invoking a "
                        "well known source or idea without direct naming."
                    ),
                    "synonyms": ["refer", "hint"],
                    "antonyms": ["explicit statement"],
                    "examples": ["The passage alludes to a classical myth without naming it."],
                    "hypernyms": ["reference"],
                    "hyponyms": ["literary allusion"],
                },
                {
                    "id": "4ab6",
                    "definition": (
                        "To imply or hint at something without direct statement, often as a "
                        "rhetorical strategy to preserve nuance."
                    ),
                    "synonyms": ["imply", "hint"],
                    "antonyms": ["explicit statement"],
                    "examples": ["The author alludes to uncertainty in the data."],
                    "hypernyms": ["reference"],
                    "hyponyms": ["figurative reference"],
                },
                {
                    "id": "cc97",
                    "definition": (
                        "In literary or scholarly analysis, to evoke a known work, tradition, "
                        "or cultural moment by alluding; the reference relies on reader "
                        "familiarity to enrich meaning."
                    ),
                    "synonyms": ["refer", "hint"],
                    "antonyms": ["explicit statement"],
                    "examples": ["The novel alludes to Dante through symbolic imagery."],
                    "hypernyms": ["reference"],
                    "hyponyms": ["intertextual reference"],
                },
            ],
            "morphology": {
                "id": "25a0",
                "base_form": "allude",
                "inflections": {
                    "id": "8e69",
                    "plural": [],
                    "past_tense": ["alluded"],
                    "past_participle": ["alluded"],
                    "present_participle": ["alluding"],
                    "third_person_singular": ["alludes"],
                    "comparative": [],
                    "superlative": [],
                },
                "derivations": {
                    "id": "713e",
                    "noun_forms": ["allusion"],
                    "verb_forms": [],
                    "adjective_forms": [],
                    "adverb_forms": [],
                },
            },
            "collocations": ["indirect reference", "literary allusion"],
        }
    ],
    "edges": [
        {
            "id": "1079",
            "source_word": "alluding",
            "target_word": "refer",
            "relationship_type": "synonym",
            "source_pos": "verb",
            "target_pos": None,
            "sense_index": 0,
        }
    ],
    "etymology": {
        "id": "e6eb",
        "summary": (
            "Alluding is the present participle form of the English verb allude, meaning to "
            "refer to something indirectly or by suggestion."
        ),
        "segments": [
            {
                "id": "7278",
                "language": "English",
                "headword": "alluding",
                "gloss": "present participle form of allude; making indirect references",
                "era": "Modern English, c. 17th century",
                "order": 0,
            },
            {
                "id": "a3b8",
                "language": "English",
                "headword": "allude",
                "gloss": "to refer to indirectly or by suggestion",
                "era": "Early Modern English, c. 16th century",
                "order": 1,
            },
        ],
        "cognates": ["aludir (Spanish)", "alluder (French)"],
        "references": ["https://www.etymonline.com/word/allude"],
    },
    "encyclopedia_entry": (
        "**Alluding** is the present participle of the verb *allude*, defined as referring "
        "to someone or something indirectly rather than naming it directly."
    ),
    "wiki_frequency": 4041,
    "wiki_frequency_rank": 30105,
    "lexical_explanation": (
        "Alluding is the act of referring to something indirectly rather than naming it outright."
    ),
}

V2_ABSEIL: dict = {
    "schema_version": "2.0",
    "lexeme_id": "abseil",
    "headword": "abseil",
    "language": "en",
    "status": "complete",
    "pos_entries": [
        {
            "pos": "verb",
            "senses": [
                {
                    "index": 0,
                    "gloss": "To descend a rock face using a rope.",
                    "variants": [
                        {
                            "reading_level": "grade_1",
                            "register": "plain",
                            "text": "To go down a big rock using a rope.",
                            "measured_grade_level": 1.4,
                            "provenance": {
                                "stage": "variants",
                                "model": "gpt-5.6-luna",
                                "prompt_version": "v1",
                            },
                        }
                    ],
                    "examples": [
                        "They abseiled down the cliff.",
                        "They abseiled down the cliff.",
                    ],
                    "synonyms": ["rappel"],
                    "antonyms": ["ascend"],
                    "hypernyms": ["descend"],
                    "hyponyms": [],
                    "meronyms": [],
                    "holonyms": ["climbing"],
                    "domain": "sports",
                    "retired": False,
                },
                {
                    "index": 1,
                    "gloss": "To lower equipment on a rope.",
                    "variants": [],
                    "examples": [],
                    "synonyms": [],
                    "antonyms": [],
                    "hypernyms": [],
                    "hyponyms": [],
                    "meronyms": [],
                    "holonyms": [],
                    "domain": "nonsense domain that is not in the legacy map",
                    "retired": True,
                },
            ],
            "morphology": {"past_tense": "abseiled", "derivations": ["abseiler"]},
            "collocations": ["abseil down"],
        }
    ],
    "etymology": {"summary": "From German abseilen.", "segments": [], "cognates": []},
    "encyclopedia_entry": "Abseiling is a rope-based descent technique.",
    "lexical_explanation": "Use it for a controlled descent on a rope.",
    "is_stopword": False,
    "domain": "sports",
    "frequency": 12.5,
    "discovered_from": "climbing",
    "provenance": [{"stage": "senses", "model": "gpt-5.4-nano", "prompt_version": "v1"}],
    "created_at": "2026-01-01T00:00:00Z",
    "updated_at": "2026-01-02T00:00:00Z",
}


# --------------------------------------------------------------------------------------
# detect_version
# --------------------------------------------------------------------------------------


def test_detect_version_recognises_every_shape():
    assert detect_version(V13_ALLUDING) == "1.3"
    assert detect_version(V2_ABSEIL) == "2.0"
    assert detect_version(from_v13(V13_ALLUDING).model_dump(mode="json")) == "3.0"


def test_detect_version_falls_back_to_shape_when_undeclared():
    undeclared = {k: v for k, v in V2_ABSEIL.items() if k != "schema_version"}
    assert detect_version(undeclared) == "2.0"
    assert detect_version({**undeclared, "kind": "simplex"}) == "3.0"


def test_detect_version_rejects_an_unknown_shape():
    with pytest.raises(ValueError, match="no known OpenGloss schema version"):
        detect_version({"nothing": "familiar"})


def test_v2_payloads_are_rejected_by_the_v3_model():
    # The point of extra="forbid": a v2 file must not validate as v3 by accident.
    with pytest.raises(ValidationError):
        Lexeme.model_validate(V2_ABSEIL)


def test_migrate_dispatches_on_detected_version():
    assert migrate(V13_ALLUDING).headword == "alluding"
    assert migrate(V2_ABSEIL).headword == "abseil"
    migrated = migrate(V2_ABSEIL)
    # A v3 payload passes straight through validation, unchanged.
    assert migrate(migrated.model_dump(mode="json")) == migrated


# --------------------------------------------------------------------------------------
# v1.3
# --------------------------------------------------------------------------------------


def test_from_v13_maps_the_entry_level_fields():
    entry = from_v13(V13_ALLUDING)
    assert entry.headword == "alluding"
    assert entry.lexeme_id == "alluding"
    assert entry.schema_version == "3.0"
    assert entry.kind is LexemeKind.SIMPLEX
    assert entry.frequency == 4041.0
    assert entry.frequency_corpus == "wikimedia/wikipedia:20231101.en"
    assert entry.frequency_corpus_tokens is None
    assert entry.zipf is None
    assert entry.is_stopword is False
    assert entry.status is EntryStatus.COMPLETE
    assert entry.created_at.year == 2025


def test_from_v13_preserves_sense_order_and_numbering():
    entry = from_v13(V13_ALLUDING)
    pos_entry = entry.pos_entries[0]
    assert pos_entry.pos is PartOfSpeech.VERB
    assert [s.index for s in pos_entry.senses] == [0, 1, 2]
    source = [s["definition"] for s in V13_ALLUDING["entries"][0]["senses"]]
    assert [s.canonical_gloss() for s in pos_entry.senses] == source


def test_from_v13_turns_parallel_lists_into_typed_relations():
    sense = from_v13(V13_ALLUDING).pos_entries[0].senses[0]
    assert [(r.type.value, r.target.term) for r in sense.relations] == [
        ("synonym", "refer"),
        ("synonym", "hint"),
        ("antonym", "explicit statement"),
        ("hypernym", "reference"),
        ("hyponym", "literary allusion"),
    ]
    assert all(r.target.sense_id is None and r.target.confidence is None for r in sense.relations)


def test_from_v13_fills_example_spans_deterministically():
    sense = from_v13(V13_ALLUDING).pos_entries[0].senses[0]
    example = sense.examples.canonical()
    assert example is not None
    assert example.content.matched == "alludes"


def test_from_v13_maps_domain_tags_through_the_taxonomy():
    sense = from_v13(V13_ALLUDING).pos_entries[0].senses[0]
    assert sense.domain is DomainTag.LANGUAGE_GENERAL
    assert sense.domain_hint is None


def test_from_v13_keeps_an_unmappable_domain_as_a_hint():
    payload = {**V13_ALLUDING, "tags": ["domain:underwater basket weaving"]}
    sense = from_v13(payload).pos_entries[0].senses[0]
    assert sense.domain is None
    assert sense.domain_hint == "underwater basket weaving"


def test_from_v13_drops_random_ids_and_rederives_edges():
    entry = from_v13(V13_ALLUDING)
    dumped = entry.model_dump(mode="json")
    assert "edges" not in dumped
    assert "5f98" not in repr(dumped)
    # Edges come back from the relations, not from the stored ``edges`` list.
    assert len(entry.edges()) == 15
    assert entry.edges()[0].edge_id == "alluding:verb:0-synonym->refer"


def test_from_v13_flattens_morphology_and_etymology():
    pos_entry = from_v13(V13_ALLUDING).pos_entries[0]
    assert pos_entry.morphology.past_tense == "alluded"
    assert pos_entry.morphology.present_participle == "alluding"
    assert pos_entry.morphology.derivations == ["allusion"]
    etymology = from_v13(V13_ALLUDING).etymology
    assert etymology is not None
    assert [s.form for s in etymology.segments] == ["alluding", "allude"]
    assert etymology.segments[0].meaning is not None


def test_from_v13_promotes_prose_sections_to_canonical_renditions():
    entry = from_v13(V13_ALLUDING)
    encyclopedia = entry.encyclopedia.canonical()
    explanation = entry.lexical_explanation.canonical()
    assert encyclopedia is not None
    assert explanation is not None
    assert encyclopedia.content.startswith("**Alluding**")
    assert explanation.content.startswith("Alluding is the act")


def test_from_v13_skips_a_part_of_speech_it_cannot_type():
    payload = {
        **V13_ALLUDING,
        "entries": [{"pos": "particle", "senses": [{"definition": "x"}]}],
    }
    assert from_v13(payload).pos_entries == []


def test_from_v13_marks_stopwords_as_function_words():
    payload = {**V13_ALLUDING, "stopword": {"is_stopword": True, "reason": "closed class"}}
    entry = from_v13(payload)
    assert entry.kind is LexemeKind.FUNCTION_WORD
    assert entry.is_stopword is True


# --------------------------------------------------------------------------------------
# v2.0
# --------------------------------------------------------------------------------------


def test_from_v2_maps_the_entry_level_fields():
    entry = from_v2(V2_ABSEIL)
    assert entry.schema_version == "3.0"
    assert entry.lexeme_id == "abseil"
    assert entry.kind is LexemeKind.SIMPLEX
    assert entry.frequency == 12.5
    assert entry.discovered_from == "climbing"
    assert entry.etymology is not None


def test_from_v2_folds_variants_into_the_gloss_rendition_set():
    sense = from_v2(V2_ABSEIL).pos_entries[0].senses[0]
    assert sense.canonical_gloss() == "To descend a rock face using a rope."
    grade_1 = sense.gloss.get(ReadingLevel.GRADE_1, Register.PLAIN)
    assert grade_1 is not None
    assert grade_1.content == "To go down a big rock using a rope."
    assert grade_1.assessment is not None
    assert grade_1.assessment.readability_grade == 1.4


def test_from_v2_moves_provenance_into_the_keyed_table():
    entry = from_v2(V2_ABSEIL)
    assert sorted(entry.provenance) == ["p1", "p2"]
    grade_1 = entry.pos_entries[0].senses[0].gloss.get(ReadingLevel.GRADE_1, Register.PLAIN)
    assert grade_1 is not None
    assert grade_1.provenance_id == "p2"
    assert entry.provenance["p2"].model == "gpt-5.6-luna"


def test_from_v2_flattens_the_six_relation_lists_in_order():
    sense = from_v2(V2_ABSEIL).pos_entries[0].senses[0]
    assert [(r.type, r.target.term) for r in sense.relations] == [
        (RelationType.SYNONYM, "rappel"),
        (RelationType.ANTONYM, "ascend"),
        (RelationType.HYPERNYM, "descend"),
        (RelationType.HOLONYM, "climbing"),
    ]


def test_from_v2_makes_examples_structured_and_deduplicated():
    sense = from_v2(V2_ABSEIL).pos_entries[0].senses[0]
    # The source repeats one example; the uniqueness key for examples includes the text.
    assert len(sense.examples) == 1
    assert sense.examples[0].content.matched == "abseiled"


def test_from_v2_maps_domains_and_keeps_the_unmappable_as_a_hint():
    senses = from_v2(V2_ABSEIL).pos_entries[0].senses
    assert senses[0].domain is DomainTag.SPORTS_RECREATION_GENERAL
    assert senses[0].domain_hint is None
    assert senses[1].domain is None
    assert senses[1].domain_hint == "nonsense domain that is not in the legacy map"


def test_from_v2_preserves_sense_order_and_the_retired_tombstone():
    entry = from_v2(V2_ABSEIL)
    senses = entry.pos_entries[0].senses
    assert [s.index for s in senses] == [0, 1]
    assert senses[1].retired is True
    assert entry.sense_count() == 1


def test_from_v2_promotes_prose_sections_to_canonical_renditions():
    entry = from_v2(V2_ABSEIL)
    encyclopedia = entry.encyclopedia.canonical()
    assert encyclopedia is not None
    assert encyclopedia.content == "Abseiling is a rope-based descent technique."


def test_migrated_entries_round_trip_through_json():
    for payload in (V13_ALLUDING, V2_ABSEIL):
        entry = migrate(payload)
        restored = Lexeme.model_validate(entry.model_dump(mode="json"))
        assert restored == entry


# --------------------------------------------------------------------------------------
# classify_kind_deterministic
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("headword", "expected"),
    [
        ("abseil", LexemeKind.SIMPLEX),
        ("colour", LexemeKind.SIMPLEX),
        ("-ness", LexemeKind.AFFIX),
        ("pre-", LexemeKind.AFFIX),
        ("FBI", LexemeKind.ABBREVIATION),
        ("UK", LexemeKind.ABBREVIATION),
        ("U.S.A.", LexemeKind.ABBREVIATION),
        ("NASDAQ", LexemeKind.PROPER_NOUN),
        ("the", LexemeKind.FUNCTION_WORD),
        ("because", LexemeKind.FUNCTION_WORD),
        ("The", LexemeKind.FUNCTION_WORD),
        ("Paris", LexemeKind.PROPER_NOUN),
        ("New York", LexemeKind.PROPER_NOUN),
        ("mother-in-law", LexemeKind.COMPOUND),
        ("give up", None),
        ("kick the bucket", None),
        ("3d model", None),
        ("", None),
        ("   ", None),
    ],
)
def test_classify_kind_deterministic(headword, expected):
    assert classify_kind_deterministic(headword) is expected


def test_ambiguous_multiword_headwords_migrate_as_compounds():
    # Migration cannot leave ``kind`` unset, so the ambiguous residue becomes COMPOUND
    # and the ``classify_kind`` retrofit pass revisits it.
    payload = {**V13_ALLUDING, "word": "3d model", "id": "3d_model"}
    assert classify_kind_deterministic("3d model") is None
    assert from_v13(payload).kind is LexemeKind.COMPOUND


def test_a_migrated_proper_noun_gets_a_proper_noun_block():
    payload = {**V13_ALLUDING, "word": "Dante", "id": "dante"}
    entry = from_v13(payload)
    assert entry.kind is LexemeKind.PROPER_NOUN
    assert entry.proper_noun is not None


#: A verbatim slice of the ``lexical_explanation`` of einstein.json. Five of the six
#: mentions of the headword here are capitalised and none of those five is
#: sentence-initial, which is the whole signal D-26 reads.
EINSTEIN_EXPLANATION = (
    "Einstein is a proper noun and surname most famously associated with Albert "
    "Einstein, the 20th-century theoretical physicist whose work transformed modern "
    "science. Synonyms include Albert Einstein, the physicist, genius, bright mind, "
    "and prodigy, though these are not exact equivalents in every context. As a broader "
    "category, Einstein belongs to the class of names, surnames, people, and "
    "intellectuals. Narrower or more specific uses include Albert Einstein himself."
)

#: A verbatim slice of the ``encyclopedia_entry`` of einstein.json, which writes the
#: unit of radiant energy in lower case throughout.
EINSTEIN_ENCYCLOPEDIA = (
    "**einstein** is a historical unit of radiant energy used chiefly in photochemistry "
    "and photobiology. Named after **Albert Einstein**, who advanced the concept of "
    "light quanta in 1905, the unit quantifies the energy carried by photons when "
    "treated as discrete quanta. In this framework, one einstein equals the energy of "
    "one mole of photons at a given wavelength, so the actual energy per einstein "
    "depends on the spectral composition of the light."
)

V13_EINSTEIN: dict = {
    "id": "einstein",
    "created_at": "2025-11-28T11:11:33.218073Z",
    "updated_at": "2025-11-28T11:11:33.218073Z",
    "language": "en",
    "tags": ["domain:science", "domain:history"],
    "word": "einstein",
    "stopword": {"id": "3c11", "is_stopword": False, "reason": "carries lexical meaning"},
    "entries": [
        {
            "id": "77aa",
            "pos": "noun",
            "senses": [
                {
                    "id": "0b21",
                    "definition": (
                        "The surname of Albert Einstein, used as a proper noun to designate "
                        "the renowned 20th-century theoretical physicist."
                    ),
                    "synonyms": ["Albert Einstein"],
                    "examples": ["Einstein published the theory of relativity."],
                },
                {
                    "id": "0b22",
                    "definition": (
                        "A very intelligent person, used informally to describe someone with "
                        "exceptional analytical or problem-solving ability."
                    ),
                    "synonyms": ["genius"],
                    "examples": ["She is the einstein of our department."],
                },
            ],
            "morphology": {"base_form": "einstein", "inflections": {"plural": ["einsteins"]}},
            "collocations": [],
        }
    ],
    "edges": [],
    "encyclopedia_entry": EINSTEIN_ENCYCLOPEDIA,
    "lexical_explanation": EINSTEIN_EXPLANATION,
    "wiki_frequency": 1877,
}


# --------------------------------------------------------------------------------------
# D-26: the evidence rule for lowercase headwords
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("headword", "evidence", "expected"),
    [
        pytest.param(
            "london",
            "The capital, London, grew along the Thames. Rail links reach London in an hour.",
            LexemeKind.PROPER_NOUN,
            id="two-mid-sentence-capitals-promote",
        ),
        pytest.param(
            "einstein",
            EINSTEIN_EXPLANATION,
            LexemeKind.PROPER_NOUN,
            id="real-einstein-explanation-promotes",
        ),
        pytest.param(
            "abseil",
            "Abseil is a controlled descent. Abseil techniques rely on friction, and an "
            "abseil needs a rope.",
            LexemeKind.SIMPLEX,
            id="only-sentence-initial-capitals-are-no-signal",
        ),
        pytest.param(
            "london",
            "### London\n\n**London** is a city.\n- **London** sits on the Thames.",
            LexemeKind.SIMPLEX,
            id="markdown-line-starts-are-sentence-initial-too",
        ),
        pytest.param(
            "einstein",
            "Named after Albert Einstein, the einstein is a unit. One einstein is a mole "
            "of photons.",
            None,
            id="a-lone-eponym-mention-is-undecided",
        ),
        pytest.param(
            "einstein",
            "Albert Einstein named it. An Einstein of light. One einstein is a mole; the "
            "einstein varies; each einstein differs.",
            None,
            id="capitals-without-a-majority-are-undecided",
        ),
        pytest.param(
            "new york",
            "The bagels of New York are famous. Nothing beats New York in autumn.",
            LexemeKind.PROPER_NOUN,
            id="a-lowercase-phrase-is-tested-before-the-whitespace-rule",
        ),
        pytest.param(
            "give up",
            "You should give up smoking. Do not give up now.",
            None,
            id="a-phrase-with-no-capitals-still-reaches-the-classifier",
        ),
        pytest.param(
            "mother-in-law",
            "**mother-in-law** is a kinship term. His mother-in-law visited.",
            LexemeKind.COMPOUND,
            id="an-internal-hyphen-still-wins-when-there-is-no-signal",
        ),
        pytest.param(
            "london",
            None,
            LexemeKind.SIMPLEX,
            id="without-evidence-the-old-rules-are-unchanged",
        ),
        pytest.param(
            "the",
            "The The is a band. The The toured widely.",
            LexemeKind.FUNCTION_WORD,
            id="a-function-word-never-reaches-the-evidence-rule",
        ),
        pytest.param(
            "Paris",
            "paris is written oddly here. paris again, and paris once more.",
            LexemeKind.PROPER_NOUN,
            id="a-capitalised-headword-never-reaches-the-evidence-rule",
        ),
    ],
)
def test_classify_kind_uses_the_entrys_own_capitalisation(headword, evidence, expected):
    assert classify_kind_deterministic(headword, evidence=evidence) is expected


def test_a_lowercased_proper_noun_migrates_as_a_proper_noun():
    # The v1.3 store lowercases ``word``, so rule 4 cannot fire; only the entry's own
    # prose distinguishes "einstein" the person from "einstein" the unit of energy.
    assert classify_kind_deterministic("einstein") is LexemeKind.SIMPLEX
    entry = from_v13(V13_EINSTEIN)
    assert entry.kind is LexemeKind.PROPER_NOUN
    assert entry.proper_noun is not None
    assert entry.proper_noun.entity_type is EntityType.OTHER


def test_a_lowercased_proper_noun_migrates_from_v2_too():
    payload = {
        **V2_ABSEIL,
        "lexeme_id": "london",
        "headword": "london",
        "encyclopedia_entry": (
            "The capital, London, grew along the Thames. Rail links reach London in an hour."
        ),
    }
    entry = from_v2(payload)
    assert entry.kind is LexemeKind.PROPER_NOUN
    assert entry.proper_noun is not None


def test_an_undecided_single_word_headword_migrates_as_a_simplex():
    # D-12's ``compound`` placeholder is for space-separated forms. A single word the
    # evidence rule cannot settle keeps its structural kind and no ``classify_kind``
    # marker, so the retrofit pass revisits it.
    payload = {
        **V13_EINSTEIN,
        "lexical_explanation": (
            "Named after Albert Einstein, the einstein is a unit. One einstein is a mole "
            "of photons."
        ),
        "encyclopedia_entry": "One einstein is a mole of photons.",
        "entries": [
            {
                "id": "77aa",
                "pos": "noun",
                "senses": [{"id": "0b21", "definition": "A unit of radiant energy."}],
            }
        ],
    }
    assert (
        classify_kind_deterministic(
            "einstein",
            evidence="Named after Albert Einstein, the einstein is a unit. One einstein "
            "is a mole of photons.",
        )
        is None
    )
    assert from_v13(payload).kind is LexemeKind.SIMPLEX
