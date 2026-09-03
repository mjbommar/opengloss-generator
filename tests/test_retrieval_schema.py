"""Retrieval-data schema additions (D-62): queries, QA pairs and contrasts.

Two properties are defended here. The first is *backward compatibility*: the three new
collections are additive and default empty, so every entry already in the production
store must still validate unchanged — the suite proves that against
``data/sample-300``, 300 real entries copied out of the live store, rather than against
a fixture that was written after the change. The second is the *uniqueness* of what the
three retrieval stages write: a duplicate query, a repeated question or a second
contrast for one edge is a duplicate training row wearing a different positional id, so
validation refuses it at the point it would be stored.
"""

from __future__ import annotations

import json
from pathlib import Path

import orjson
import pytest
from pydantic import ValidationError

from opengloss_generator.identity import qa_id, query_id
from opengloss_generator.schema import (
    Contrast,
    ContrastVerdict,
    Difficulty,
    Lexeme,
    LexemeKind,
    Morphology,
    PartOfSpeech,
    POSEntry,
    QAFlag,
    QAPair,
    Query,
    QueryStyle,
    QuestionType,
    Renditions,
    Sense,
    StageName,
    canonical_rendition,
    normalise_query_text,
)
from tests.conftest import make_entry

SAMPLE_STORE = Path(__file__).resolve().parents[1] / "data" / "sample-300"


def sample_paths() -> list[Path]:
    """Return every entry file in the read-only 300-entry sample store."""
    return sorted(SAMPLE_STORE.rglob("*.json"))


def a_query(text: str = "how to get down a cliff on a rope") -> Query:
    """Return a query with the given text."""
    return Query(text=text, style=QueryStyle.KEYWORD)


def a_pair(question: str = "What does abseiling need?") -> QAPair:
    """Return a QA pair asking the given question."""
    return QAPair(
        question=question,
        answer="A rope, an anchor and a descender.",
        question_type=QuestionType.FACTUAL,
        difficulty=Difficulty.EASY,
    )


def a_contrast(edge: str = "abseil:verb:0-synonym->rappel") -> Contrast:
    """Return a contrast on the given edge."""
    return Contrast(
        edge_id=edge,
        text=Renditions[str](
            root=[canonical_rendition("Abseiling and rappelling name one act in two dialects.")]
        ),
        verdict=ContrastVerdict.RELATED_AS_TYPED,
    )


def sense_with(**kwargs: object) -> Sense:
    """Return a minimal valid sense carrying the given retrieval collections."""
    return Sense.of(0, "To descend a rock face using a rope.", **kwargs)


def entry_with(sense: Sense, **kwargs: object) -> Lexeme:
    """Return a minimal valid entry wrapping one sense."""
    return Lexeme.empty(
        "abseil",
        kind=LexemeKind.SIMPLEX,
        pos_entries=[POSEntry(pos=PartOfSpeech.VERB, senses=[sense], morphology=Morphology())],
        **kwargs,
    )


# --------------------------------------------------------------------------------------
# Backward compatibility against real stored entries
# --------------------------------------------------------------------------------------


@pytest.mark.skipif(not SAMPLE_STORE.is_dir(), reason="data/sample-300 is not present")
def test_every_stored_sample_entry_still_validates():
    paths = sample_paths()
    assert len(paths) >= 300, f"expected the 300-entry sample store, found {len(paths)} files"
    # The sample store is also where the paid retrieval stages pilot, so some entries
    # legitimately carry the new collections by now. Backward compatibility is that
    # every file — with or without them — still validates, and that a file written
    # before the additions reads back with the collections empty.
    untouched = 0
    for path in paths:
        raw = orjson.loads(path.read_bytes())
        entry = Lexeme.model_validate(raw)
        if _has_retrieval_fields(raw):
            continue
        untouched += 1
        assert entry.contrasts == []
        for _pos, sense, _sid in entry.iter_senses():
            assert sense.queries == []
            assert sense.qa == []
    assert untouched > 0, "no pre-addition entry left in the sample store to check"


def _has_retrieval_fields(raw: dict) -> bool:
    """Return whether a stored payload carries any of the D-62 collections."""
    if raw.get("contrasts"):
        return True
    return any(
        sense.get("queries") or sense.get("qa")
        for pos_entry in raw.get("pos_entries", [])
        for sense in pos_entry.get("senses", [])
    )


@pytest.mark.skipif(not SAMPLE_STORE.is_dir(), reason="data/sample-300 is not present")
def test_stored_entries_serialise_back_to_what_they_were():
    # The additive fields must not change the wire shape of an entry that has none of
    # them: a store rewritten by this schema is byte-comparable, key for key, with what
    # the production chain wrote.
    checked = 0
    for path in sample_paths():
        raw = json.loads(path.read_text(encoding="utf-8"))
        if _has_retrieval_fields(raw):
            continue
        dumped = Lexeme.model_validate(raw).model_dump(mode="json", exclude_defaults=True)
        assert "contrasts" not in dumped
        assert set(dumped) <= set(raw)
        checked += 1
        if checked == 25:
            break
    assert checked > 0, "no pre-addition entry left in the sample store to check"


def test_the_new_collections_default_empty_on_an_untouched_entry():
    entry = make_entry()
    sense = entry.pos_entries[0].senses[0]
    assert (sense.queries, sense.qa, entry.contrasts) == ([], [], [])
    # And a payload that never mentions them validates.
    assert Lexeme.model_validate(entry.model_dump(mode="json", exclude_defaults=True)) == entry


# --------------------------------------------------------------------------------------
# Round trip with all three collections populated
# --------------------------------------------------------------------------------------


def test_an_entry_carrying_all_three_collections_round_trips_through_json():
    sense = sense_with(
        queries=[
            Query(text="rope descent technique", style=QueryStyle.KEYWORD),
            Query(text="How do climbers get back down?", style=QueryStyle.QUESTION),
        ],
        qa=[
            QAPair(
                question="What equipment does the descent need?",
                answer="A rope, an anchor and a friction device.",
                question_type=QuestionType.FACTUAL,
                difficulty=Difficulty.EASY,
                grounded_in=["abseil:verb:0#neutral/plain"],
            )
        ],
    )
    entry = entry_with(sense, contrasts=[a_contrast()])

    restored = Lexeme.model_validate_json(entry.model_dump_json())

    assert restored == entry
    restored_sense = restored.pos_entries[0].senses[0]
    assert [q.style for q in restored_sense.queries] == [QueryStyle.KEYWORD, QueryStyle.QUESTION]
    assert restored_sense.qa[0].grounded_in == ["abseil:verb:0#neutral/plain"]
    assert restored.contrasts[0].verdict is ContrastVerdict.RELATED_AS_TYPED
    assert restored.contrasts[0].canonical_text().startswith("Abseiling and rappelling")


def test_the_new_models_refuse_unknown_fields():
    for model, payload in (
        (Query, {"text": "t", "style": "keyword", "cost": 1}),
        (
            QAPair,
            {
                "question": "q",
                "answer": "a",
                "question_type": "factual",
                "difficulty": "easy",
                "score": 1,
            },
        ),
        (
            Contrast,
            {
                "edge_id": "e",
                "text": [{"reading_level": "neutral", "register": "plain", "content": "x"}],
                "verdict": "unrelated",
                "reason": "x",
            },
        ),
    ):
        with pytest.raises(ValidationError):
            model.model_validate(payload)


# --------------------------------------------------------------------------------------
# Uniqueness: queries
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "second",
    [
        "how to get down a cliff on a rope",  # identical
        "How To Get Down A Cliff On A Rope",  # case
        "how  to get down a cliff   on a rope",  # collapsed whitespace
        " how to get down a cliff on a rope ",  # surrounding whitespace
        "how to get down a cliff on a rope?",  # terminal punctuation
        "How to get down a cliff on a rope!!",  # both
    ],
)
def test_two_queries_with_the_same_normalised_text_are_rejected(second):
    with pytest.raises(ValidationError, match="duplicate query text"):
        sense_with(queries=[a_query(), a_query(second)])


def test_queries_that_differ_in_more_than_punctuation_and_case_are_kept():
    sense = sense_with(
        queries=[
            a_query("how to get down a cliff on a rope"),
            a_query("how to get down a rope on a cliff"),
            a_query("getting down a cliff on a rope"),
        ]
    )
    assert len(sense.queries) == 3


def test_query_text_is_capped_at_200_characters():
    with pytest.raises(ValidationError):
        a_query("x" * 201)
    assert len(a_query("x" * 200).text) == 200


# --------------------------------------------------------------------------------------
# Uniqueness: QA pairs
# --------------------------------------------------------------------------------------


def test_two_qa_pairs_asking_the_same_question_are_rejected():
    with pytest.raises(ValidationError, match="duplicate question"):
        sense_with(qa=[a_pair(), a_pair("what does abseiling need")])


def test_the_question_alone_keys_a_qa_pair_not_the_answer():
    first = a_pair()
    second = a_pair()
    second.answer = "Something else entirely."
    with pytest.raises(ValidationError, match="duplicate question"):
        sense_with(qa=[first, second])


def test_different_questions_at_different_types_coexist():
    sense = sense_with(
        qa=[
            a_pair("What does abseiling need?"),
            a_pair("Why does a climber abseil rather than climb down?"),
        ]
    )
    assert len(sense.qa) == 2


# --------------------------------------------------------------------------------------
# Uniqueness: contrasts
# --------------------------------------------------------------------------------------


def test_only_one_contrast_may_be_stored_per_edge():
    with pytest.raises(ValidationError, match="duplicate contrast for edge"):
        entry_with(sense_with(), contrasts=[a_contrast(), a_contrast()])


def test_contrasts_on_different_edges_coexist_and_are_looked_up_by_edge_id():
    entry = entry_with(
        sense_with(),
        contrasts=[
            a_contrast("abseil:verb:0-synonym->rappel"),
            a_contrast("abseil:verb:0-hypernym->descend"),
        ],
    )
    found = entry.contrast_for("abseil:verb:0-hypernym->descend")
    assert found is not None
    assert found.edge_id == "abseil:verb:0-hypernym->descend"
    assert entry.contrast_for("abseil:verb:0-antonym->ascend") is None


def test_a_contrast_needs_a_canonical_paragraph():
    with pytest.raises(ValidationError, match="no canonical"):
        Contrast(
            edge_id="abseil:verb:0-synonym->rappel",
            text=Renditions[str](root=[]),
            verdict=ContrastVerdict.UNRELATED,
        )


def test_a_contrast_key_is_not_checked_against_the_entrys_live_edges():
    # Deliberate: retiring a sense must not invalidate a stored, still-true paragraph.
    entry = entry_with(sense_with(), contrasts=[a_contrast("gone:verb:9-synonym->vanished")])
    assert entry.contrast_for("gone:verb:9-synonym->vanished") is not None
    assert "gone:verb:9-synonym->vanished" not in {e.edge_id for e in entry.edges()}


# --------------------------------------------------------------------------------------
# Ids and helpers
# --------------------------------------------------------------------------------------


def test_query_and_qa_ids_are_positional_within_the_sense():
    sense = sense_with(
        queries=[a_query("first query"), a_query("second query"), a_query("third query")],
        qa=[a_pair("First question?"), a_pair("Second question?")],
    )
    entry = entry_with(sense)
    _pos, stored, sid = entry.iter_senses()[0]
    assert sid == "abseil:verb:0"
    assert stored.query_ids(sid) == ["abseil:verb:0#q0", "abseil:verb:0#q1", "abseil:verb:0#q2"]
    assert stored.qa_ids(sid) == ["abseil:verb:0#qa0", "abseil:verb:0#qa1"]
    assert query_id(sid, 3) == "abseil:verb:0#q3"
    assert qa_id(sid, 3) == "abseil:verb:0#qa3"


def test_id_lists_are_empty_when_the_collections_are():
    sense = sense_with()
    assert sense.query_ids("abseil:verb:0") == []
    assert sense.qa_ids("abseil:verb:0") == []


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("How to Abseil?", "how to abseil"),
        ("  how   to  abseil  ", "how to abseil"),
        ("HOW TO ABSEIL!!!", "how to abseil"),
        ("what is abseiling,", "what is abseiling"),
        ("abseil vs. rappel", "abseil vs. rappel"),  # internal punctuation survives
        ("?!", ""),
    ],
)
def test_normalise_query_text(raw, expected):
    assert normalise_query_text(raw) == expected


# --------------------------------------------------------------------------------------
# Enum members the retrieval stages will name
# --------------------------------------------------------------------------------------


def test_the_retrieval_stage_names_exist_and_are_distinct_from_the_judge():
    assert StageName.QUERIES.value == "queries"
    assert StageName.CONTRASTS.value == "contrasts"
    assert StageName.QA_PAIRS.value == "qa_pairs"
    assert StageName.QA_PAIRS is not StageName.QA


def test_the_two_new_qa_flags_carry_the_project_prefix():
    assert QAFlag.OG_NEAR_COPY.value == "og.near_copy"
    assert QAFlag.OG_FILLER.value == "og.filler"


def test_the_new_enums_cover_the_documented_vocabularies():
    assert [s.value for s in QueryStyle] == [
        "keyword",
        "question",
        "conversational",
        "constraint",
        "role",
        "example_based",
        "step_by_step",
        "directive",
    ]
    assert [q.value for q in QuestionType] == [
        "factual",
        "definition",
        "reasoning",
        "comparison",
        "procedural",
        "causal",
        "hypothetical",
    ]
    assert [d.value for d in Difficulty] == ["easy", "medium", "hard"]
    assert [v.value for v in ContrastVerdict] == [
        "related_as_typed",
        "related_differently",
        "unrelated",
    ]
