"""The WordNet 3.0 importer.

Almost every test here runs against ``_FakeCorpus`` — a hand-built stand-in for NLTK's
reader holding a handful of synsets modelled on the real ones — so the suite stays
offline, needs neither the optional ``nltk`` dependency nor its corpus download, and can
assert on relations and sense orders that a real corpus would make tedious to pin.

The few tests that do want the real thing are gathered at the end behind
``_real_corpus``, which skips when WordNet 3.0 is not installed.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from opengloss_generator import cli
from opengloss_generator.config import StoreConfig
from opengloss_generator.errors import WordNetUnavailableError
from opengloss_generator.identity import sense_id
from opengloss_generator.migrate import V2_MODEL, V13_MODEL
from opengloss_generator.runner import RunSession
from opengloss_generator.schema import (
    EntityType,
    EntryStatus,
    LexemeKind,
    PartOfSpeech,
    RelationType,
    StageName,
)
from opengloss_generator.store import LexemeStore
from opengloss_generator.taxonomy import DomainTag, is_general
from opengloss_generator.wordnet_import import (
    LEXNAME_DOMAIN_MAP,
    PROVENANCE_FIELDS,
    WORDNET_MODEL,
    WORDNET_NOTE,
    candidate_index,
    clean_definition,
    clean_examples,
    entity_type_for_lexnames,
    entry_for,
    iter_entries,
    lexname_domain,
    read_candidate_rows,
    read_candidates,
    wordnet_kind,
)

runner = CliRunner()


# --------------------------------------------------------------------------------------
# A stand-in for NLTK's WordNet reader
# --------------------------------------------------------------------------------------


class _FakeLemma:
    """One word form inside a synset, with the two lemma-level pointers we read."""

    def __init__(self, name: str, synset: _FakeSynset) -> None:
        self._name = name
        self._synset = synset
        self.antonym_names: list[str] = []
        self.derivation_names: list[str] = []

    def name(self) -> str:
        return self._name

    def synset(self) -> _FakeSynset:
        return self._synset

    def _as_lemmas(self, names: list[str]) -> list[_FakeLemma]:
        return [_FakeLemma(name, self._synset) for name in names]

    def antonyms(self) -> list[_FakeLemma]:
        return self._as_lemmas(self.antonym_names)

    def derivationally_related_forms(self) -> list[_FakeLemma]:
        return self._as_lemmas(self.derivation_names)


_POINTERS = (
    "hypernyms",
    "instance_hypernyms",
    "hyponyms",
    "instance_hyponyms",
    "part_meronyms",
    "member_meronyms",
    "substance_meronyms",
    "part_holonyms",
    "member_holonyms",
    "substance_holonyms",
    "also_sees",
    "similar_tos",
)


class _FakeSynset:
    """One synset: lemmas, a gloss, examples, a lexname and twelve pointer lists."""

    def __init__(
        self,
        name: str,
        lemmas: list[str],
        definition: str,
        *,
        examples: list[str] | None = None,
        lexname: str = "noun.artifact",
        **pointers: list[_FakeSynset],
    ) -> None:
        self._name = name
        self._lemmas = [_FakeLemma(lemma, self) for lemma in lemmas]
        self._definition = definition
        self._examples = examples or []
        self._lexname = lexname
        self._pointers: dict[str, list[_FakeSynset]] = {p: [] for p in _POINTERS}
        for pointer, targets in pointers.items():
            assert pointer in self._pointers, pointer
            self._pointers[pointer] = targets

    def name(self) -> str:
        return self._name

    def pos(self) -> str:
        return self._name.split(".")[1]

    def lemmas(self) -> list[_FakeLemma]:
        return self._lemmas

    def lemma_names(self) -> list[str]:
        return [lemma.name() for lemma in self._lemmas]

    def lemma(self, name: str) -> _FakeLemma:
        return next(lemma for lemma in self._lemmas if lemma.name() == name)

    def definition(self) -> str:
        return self._definition

    def examples(self) -> list[str]:
        return list(self._examples)

    def lexname(self) -> str:
        return self._lexname

    def __getattr__(self, item: str) -> Any:  # noqa: ANN401 - the reader is untyped
        if item in _POINTERS:
            return lambda: list(self._pointers[item])
        raise AttributeError(item)


class _FakeCorpus:
    """The two reader methods the importer calls, over a fixed list of synsets."""

    def __init__(self, synsets: list[_FakeSynset], *, version: str = "3.0") -> None:
        self._synsets = synsets
        self._version = version

    def get_version(self) -> str:
        return self._version

    def synsets(self, word: str, pos: str | None = None) -> list[_FakeSynset]:
        """Return the synsets listing ``word``, in declaration order.

        Mirrors NLTK closely enough to matter: ``pos="a"`` covers satellites too, and the
        lookup is case-insensitive, so a capitalised WordNet lemma is found from a
        lower-cased candidate word.
        """
        wanted = {"a", "s"} if pos == "a" else ({pos} if pos else None)
        lowered = word.lower()
        return [
            synset
            for synset in self._synsets
            if (wanted is None or synset.pos() in wanted)
            and any(name.lower() == lowered for name in synset.lemma_names())
        ]


# --- the world ------------------------------------------------------------------------

CANINE = _FakeSynset("canine.n.02", ["canine"], "any of various fissiped mammals")
PUPPY = _FakeSynset("puppy.n.01", ["puppy"], "a young dog")
FLAG = _FakeSynset("flag.n.07", ["flag"], "a conspicuously marked tail")
PACK = _FakeSynset("pack.n.06", ["pack"], "a group of hunting animals")
BLOOD = _FakeSynset("blood.n.01", ["blood"], "the fluid that circulates")
TRACK = _FakeSynset("track.v.01", ["track"], "carry on the feet and deposit")
# `noun.person` is WordNet 3.0's own lexname for `physicist.n.01`, and it is spelled out
# here rather than left to the fixture default because the instance hypernym's supersense
# is now what types the entity (D-81): a default of `noun.artifact` would make this fake
# say Einstein is a work.
PHYSICIST = _FakeSynset(
    "physicist.n.01", ["physicist"], "a scientist trained in physics", lexname="noun.person"
)
BAD = _FakeSynset("bad.a.01", ["bad"], "having undesirable qualities", lexname="adj.all")

DOG_ANIMAL = _FakeSynset(
    "dog.n.01",
    ["dog", "domestic_dog", "Canis_familiaris"],
    "a member of the genus Canis that has been domesticated by man",
    examples=["the dog barked all night"],
    lexname="noun.animal",
    hypernyms=[CANINE],
    # Declared out of WordNet-name order on purpose: the importer must sort them, because
    # NLTK's own pointer order depends on PYTHONHASHSEED.
    hyponyms=[PUPPY, BLOOD],
    part_meronyms=[FLAG],
    member_holonyms=[PACK],
    substance_meronyms=[BLOOD],
    also_sees=[TRACK],
)
DOG_MAN = _FakeSynset(
    "dog.n.03",
    ["dog"],
    "informal term for a man",
    examples=["you lucky dog"],
    lexname="noun.person",
)
DOG_CHASE = _FakeSynset(
    "chase.v.01",
    ["chase", "dog"],
    "go after with the intent to catch",
    examples=["The policeman chased the mugger down the alley"],
    lexname="verb.motion",
    hypernyms=[TRACK],
)
DOG_ANIMAL.lemma("dog").derivation_names = ["doggy", "dog"]

GOOD_ADJ = _FakeSynset(
    "good.a.01",
    ["good"],
    "having desirable or positive qualities",
    lexname="adj.all",
    similar_tos=[],
)
GOOD_SAT = _FakeSynset(
    "full.s.04", ["full", "good"], "having the normally expected amount", lexname="adj.all"
)
GOOD_ADJ.lemma("good").antonym_names = ["bad"]

EINSTEIN = _FakeSynset(
    "einstein.n.01",
    ["Einstein", "Albert_Einstein"],
    "physicist born in Germany",
    lexname="noun.person",
    instance_hypernyms=[PHYSICIST],
)
A_BATTERY = _FakeSynset(
    "a_battery.n.01", ["A_battery"], "battery for supplying current", lexname="noun.artifact"
)
GO_OFF = _FakeSynset(
    "go_off.v.01", ["go_off", "sound"], "be discharged or activated", lexname="verb.change"
)
MOTHER_IN_LAW = _FakeSynset(
    "mother-in-law.n.01", ["mother-in-law"], "the mother of your spouse", lexname="noun.person"
)
THE_DET = _FakeSynset("the.n.01", ["the"], "a hypothetical noun sense of a determiner")
AXIS = _FakeSynset("axis.n.01", ["axis"], "a straight line about which a body rotates")

CORPUS = _FakeCorpus(
    [
        DOG_ANIMAL,
        DOG_MAN,
        DOG_CHASE,
        GOOD_ADJ,
        GOOD_SAT,
        EINSTEIN,
        A_BATTERY,
        GO_OFF,
        MOTHER_IN_LAW,
        THE_DET,
        AXIS,
        CANINE,
        PUPPY,
        BAD,
        TRACK,
    ]
)


@pytest.fixture
def corpus() -> _FakeCorpus:
    return CORPUS


# --------------------------------------------------------------------------------------
# Relations
# --------------------------------------------------------------------------------------


def _targets(entry, index: int, relation_type: RelationType, pos_index: int = 0) -> list[str]:
    sense = entry.pos_entries[pos_index].senses[index]
    return [r.target.term for r in sense.relations if r.type is relation_type]


def test_every_relation_type_is_mapped(corpus):
    entry = entry_for("dog", corpus=corpus)
    assert _targets(entry, 0, RelationType.SYNONYM) == ["domestic dog", "Canis familiaris"]
    assert _targets(entry, 0, RelationType.HYPERNYM) == ["canine"]
    assert _targets(entry, 0, RelationType.HYPONYM) == ["blood", "puppy"]
    assert _targets(entry, 0, RelationType.MERONYM) == ["flag", "blood"]
    assert _targets(entry, 0, RelationType.HOLONYM) == ["pack"]
    assert _targets(entry, 0, RelationType.SEE_ALSO) == ["track"]
    # Antonymy is lemma-level, and read off the headword's own lemma.
    assert _targets(entry_for("good", corpus=corpus), 0, RelationType.ANTONYM) == ["bad"]


def test_pointer_targets_are_sorted_by_wordnet_name(corpus):
    """`hyponyms` was declared [puppy, blood]; the stored order is WordNet-name order."""
    assert _targets(entry_for("dog", corpus=corpus), 0, RelationType.HYPONYM) == ["blood", "puppy"]


def test_instance_hypernym_becomes_instance_of(corpus):
    entry = entry_for("einstein", corpus=corpus)
    assert _targets(entry, 0, RelationType.INSTANCE_OF) == ["physicist"]
    assert _targets(entry, 0, RelationType.HYPERNYM) == []


def test_same_synset_lemmas_are_synonyms_and_never_self_targets(corpus):
    entry = entry_for("dog", corpus=corpus)
    synonyms = _targets(entry, 0, RelationType.SYNONYM)
    assert "dog" not in synonyms
    pointed_at = {
        r.target.lexeme_id for _, sense, _ in entry.iter_senses() for r in sense.relations
    }
    assert entry.lexeme_id not in pointed_at


def test_relations_are_unresolved(corpus):
    entry = entry_for("dog", corpus=corpus)
    relations = entry.pos_entries[0].senses[0].relations
    assert relations
    assert all(r.target.sense_id is None and r.target.confidence is None for r in relations)


# --------------------------------------------------------------------------------------
# Senses and parts of speech
# --------------------------------------------------------------------------------------


def test_sense_order_is_id_order(corpus):
    entry = entry_for("dog", corpus=corpus)
    noun = entry.pos_entries[0]
    assert [s.index for s in noun.senses] == [0, 1]
    assert noun.senses[0].canonical_gloss().startswith("a member of the genus Canis")
    assert noun.senses[1].canonical_gloss() == "informal term for a man"
    ids = [sid for _, _, sid in entry.iter_senses()]
    assert ids[:2] == [sense_id("dog", "noun", 0), sense_id("dog", "noun", 1)]


def test_multi_pos_lemma_gets_one_entry_per_pos(corpus):
    entry = entry_for("dog", corpus=corpus)
    assert [pos.pos for pos in entry.pos_entries] == [PartOfSpeech.NOUN, PartOfSpeech.VERB]
    assert [s.index for s in entry.pos_entries[1].senses] == [0]
    assert entry.pos_entries[1].senses[0].canonical_gloss().startswith("go after")


def test_adjective_head_and_satellite_share_one_pos_entry(corpus):
    entry = entry_for("good", corpus=corpus)
    assert [pos.pos for pos in entry.pos_entries] == [PartOfSpeech.ADJECTIVE]
    assert [s.index for s in entry.pos_entries[0].senses] == [0, 1]


def test_morphy_near_miss_is_not_this_headword(corpus):
    """A synset the stemmer reaches but that does not list the form is not an entry."""
    assert entry_for("axes", corpus=corpus) is None


def test_word_absent_from_wordnet_returns_none(corpus):
    assert entry_for("notaword", corpus=corpus) is None
    assert entry_for("   ", corpus=corpus) is None


def test_entries_are_partial_and_carry_no_generated_content(corpus):
    entry = entry_for("dog", corpus=corpus)
    assert entry.status is EntryStatus.PARTIAL
    assert entry.etymology is None
    assert len(entry.encyclopedia) == 0
    assert len(entry.lexical_explanation) == 0
    assert entry.frequency is None


# --------------------------------------------------------------------------------------
# Headword, case and kind
# --------------------------------------------------------------------------------------


def test_wordnet_casing_wins_over_the_candidate_list(corpus):
    entry = entry_for("a battery", corpus=corpus)
    assert entry.headword == "A battery"
    assert entry.lexeme_id == "a_battery"


def test_capitalised_lemma_is_a_proper_noun(corpus):
    entry = entry_for("einstein", corpus=corpus)
    assert entry.headword == "Einstein"
    assert entry.kind is LexemeKind.PROPER_NOUN
    assert entry.proper_noun is not None


def test_instance_synset_is_a_proper_noun_block(corpus):
    entry = entry_for("einstein", corpus=corpus)
    assert entry.proper_noun is not None
    # D-81: the instance hypernym's supersense types the entity, so this is `person`
    # rather than the `other` placeholder D-12/D-18 used to write unconditionally.
    assert entry.proper_noun.entity_type.value == "person"


@pytest.mark.parametrize(
    ("headword", "parts", "expected"),
    [
        ("go off", [PartOfSpeech.VERB], LexemeKind.PHRASAL_VERB),
        ("a battery", [PartOfSpeech.NOUN, PartOfSpeech.VERB], LexemeKind.COMPOUND),
        ("dog house", [PartOfSpeech.NOUN, PartOfSpeech.VERB], LexemeKind.COMPOUND),
        ("mother-in-law", [PartOfSpeech.NOUN], LexemeKind.COMPOUND),
        ("dog", [PartOfSpeech.NOUN], LexemeKind.SIMPLEX),
        ("the", [PartOfSpeech.NOUN], LexemeKind.FUNCTION_WORD),
        ("London", [PartOfSpeech.NOUN], LexemeKind.PROPER_NOUN),
    ],
)
def test_wordnet_kind(headword, parts, expected):
    assert wordnet_kind(headword, parts) is expected


def test_instance_forces_proper_noun_over_shape():
    assert wordnet_kind("black death", [PartOfSpeech.NOUN], is_instance=True) is (
        LexemeKind.PROPER_NOUN
    )


def test_kinds_on_built_entries(corpus):
    assert entry_for("go off", corpus=corpus).kind is LexemeKind.PHRASAL_VERB
    assert entry_for("mother-in-law", corpus=corpus).kind is LexemeKind.COMPOUND
    the = entry_for("the", corpus=corpus)
    assert the.kind is LexemeKind.FUNCTION_WORD
    assert the.is_stopword is True


# --------------------------------------------------------------------------------------
# Definitions and examples
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # A well-formed definition is untouched, semicolons and all.
        (
            "a member of the genus Canis; occurs in many breeds",
            "a member of the genus Canis; occurs in many breeds",
        ),
        # The eight WordNet 3.0 synsets whose own gloss quoting leaks into definition().
        (
            'capable of taking into a solution; "an assimilative substance',
            "capable of taking into a solution",
        ),
        (
            'characterized by errors; not following established rules; ; ; the wrong side"',
            "characterized by errors; not following established rules",
        ),
        (
            'of or pertaining to the ancient Greek cultures; ; "classical',
            "of or pertaining to the ancient Greek cultures",
        ),
        (
            'having a toe of a specified kind; often used in combination; five-toed"',
            "having a toe of a specified kind; often used in combination",
        ),
        ("fueled by wood; \"a wood-burning stove'", "fueled by wood"),
        (
            'lacking substance or significance; ; ; ; a fragile claim to fame"',
            "lacking substance or significance",
        ),
        (
            'composed of more than one part; compound flower heads"',
            "composed of more than one part",
        ),
        (
            '(language) having the form used by ancient authors; "classical Greek',
            "(language) having the form used by ancient authors",
        ),
    ],
)
def test_clean_definition(raw, expected):
    assert clean_definition(raw) == expected


def test_clean_examples_drops_debris_and_duplicates():
    assert clean_examples(["long-toed; ", "  ", "the dog barked", "the dog barked"]) == [
        "long-toed",
        "the dog barked",
    ]


def test_examples_come_from_examples_not_from_the_definition(corpus):
    entry = entry_for("dog", corpus=corpus)
    sense = entry.pos_entries[0].senses[0]
    assert [r.content.text for r in sense.examples] == ["the dog barked all night"]
    assert "the dog barked" not in sense.canonical_gloss()


def test_examples_are_span_less_and_canonical(corpus):
    entry = entry_for("dog", corpus=corpus)
    for _, sense, _ in entry.iter_senses():
        for rendition in sense.examples:
            assert rendition.is_canonical
            assert rendition.content.span is None


def test_a_sense_with_no_usable_definition_is_dropped():
    empty = _FakeSynset("blank.n.01", ["blank"], '; "only an example')
    real = _FakeSynset("blank.n.02", ["blank"], "an empty area")
    entry = entry_for("blank", corpus=_FakeCorpus([empty, real]))
    assert [s.canonical_gloss() for s in entry.pos_entries[0].senses] == ["an empty area"]


# --------------------------------------------------------------------------------------
# Domain
# --------------------------------------------------------------------------------------


def test_domain_from_lexname(corpus):
    entry = entry_for("dog", corpus=corpus)
    animal = entry.pos_entries[0].senses[0]
    assert animal.domain is DomainTag.NATURE_ANIMALS
    assert animal.domain_hint is None


def test_unmapped_lexname_becomes_a_hint_for_tag_domain(corpus):
    entry = entry_for("dog", corpus=corpus)
    person = entry.pos_entries[0].senses[1]
    assert person.domain is None
    assert person.domain_hint == "noun.person"


def test_lexname_map_never_points_at_a_general_leaf():
    """D-44: a supersense swept into a `.general` bucket is the failure this avoids."""
    assert LEXNAME_DOMAIN_MAP
    assert not [tag for tag in LEXNAME_DOMAIN_MAP.values() if is_general(tag)]


def test_lexname_domain_is_case_and_space_insensitive():
    assert lexname_domain("  Noun.Animal ") is DomainTag.NATURE_ANIMALS
    assert lexname_domain("noun.artifact") is None


# --------------------------------------------------------------------------------------
# Morphology
# --------------------------------------------------------------------------------------


def test_morphology_carries_derivations_and_nothing_else(corpus):
    morphology = entry_for("dog", corpus=corpus).pos_entries[0].morphology
    assert morphology.derivations == ["doggy"]  # "dog" itself is not a derived form
    assert morphology.inflected_forms() == []


# --------------------------------------------------------------------------------------
# Provenance
# --------------------------------------------------------------------------------------


def test_one_provenance_record_per_inherited_field(corpus):
    entry = entry_for("dog", corpus=corpus)
    notes = [record.note for record in entry.provenance_in_order()]
    assert notes == [f"{field}: {WORDNET_NOTE}" for field in PROVENANCE_FIELDS]
    for record in entry.provenance.values():
        assert record.stage is StageName.MIGRATE
        assert record.model == WORDNET_MODEL
        assert record.cost_usd == 0.0
        assert record.input_tokens == record.output_tokens == 0


def test_content_points_at_its_provenance_record(corpus):
    entry = entry_for("dog", corpus=corpus)
    by_field = {record.note.split(":", 1)[0]: key for key, record in entry.provenance.items()}
    for _, sense, _ in entry.iter_senses():
        assert {r.provenance_id for r in sense.gloss} == {by_field["gloss"]}
        assert {r.provenance_id for r in sense.examples} <= {by_field["examples"]}
        assert {r.provenance_id for r in sense.relations} <= {by_field["relations"]}


def test_wordnet_records_are_distinguishable_from_the_two_migrations(corpus):
    """All three importers use stage `migrate`; `model` is what tells them apart (D-78)."""
    models = {record.model for record in entry_for("dog", corpus=corpus).provenance.values()}
    assert models == {WORDNET_MODEL}
    assert WORDNET_MODEL not in {V13_MODEL, V2_MODEL}


def test_no_record_is_written_for_a_field_wordnet_did_not_supply(corpus):
    entry = entry_for("a battery", corpus=corpus)
    fields = {record.note.split(":", 1)[0] for record in entry.provenance.values()}
    assert "examples" not in fields  # the synset has none
    assert "morphology" not in fields  # nor any derivationally related form
    assert "gloss" in fields


def test_migration_writes_no_classify_kind_marker(corpus):
    """So the `classify_kind` retrofit revisits every entry, as it does after `migrate`."""
    entry = entry_for("dog", corpus=corpus)
    assert not [r for r in entry.provenance.values() if r.stage is StageName.CLASSIFY_KIND]


# --------------------------------------------------------------------------------------
# Candidate list
# --------------------------------------------------------------------------------------


def _tsv(tmp_path: Path, rows: str) -> Path:
    path = tmp_path / "candidates.tsv"
    path.write_text(rows, encoding="utf-8")
    return path


def test_read_candidates_filters_by_source(tmp_path):
    path = _tsv(
        tmp_path,
        "word\tsource\tn_synsets\ndog\twordnet\t7\nalluding\tv1.3\t3\ncat\twordnet\t8\n",
    )
    assert read_candidates(path) == ["dog", "cat"]
    assert read_candidates(path, source="v1.3") == ["alluding"]
    assert read_candidates(path, source=None) == ["dog", "alluding", "cat"]


def test_read_candidates_drops_blanks_and_duplicates(tmp_path):
    path = _tsv(tmp_path, "word\tsource\ndog\twordnet\n\twordnet\ndog\twordnet\n")
    assert read_candidates(path) == ["dog"]


def test_read_candidates_needs_the_columns_it_filters_on(tmp_path):
    with pytest.raises(ValueError, match="no 'word' column"):
        read_candidates(_tsv(tmp_path, "term\tsource\ndog\twordnet\n"))
    with pytest.raises(ValueError, match="no 'source' column"):
        read_candidates(_tsv(tmp_path, "word\tn_synsets\ndog\t7\n"))


def test_read_candidates_on_an_empty_file(tmp_path):
    assert read_candidates(_tsv(tmp_path, "")) == []


# --------------------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------------------


@pytest.fixture
def offline_cli(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Point the CLI at the fake corpus and keep run artefacts out of the repository."""
    monkeypatch.setenv("OPENGLOSS_LOG_DIR", str(tmp_path / "runs"))
    monkeypatch.setattr(cli, "load_wordnet", lambda: CORPUS)
    original = RunSession.__init__

    def patched(
        self, config, *, model_override=None, run_id=None, install_signal_handler=False
    ) -> None:
        original(self, config, model_override=None, run_id=run_id, install_signal_handler=False)

    monkeypatch.setattr(RunSession, "__init__", patched)


def _candidates(tmp_path: Path) -> Path:
    return _tsv(
        tmp_path,
        "word\tsource\ndog\twordnet\nalluding\tv1.3\neinstein\twordnet\nnotaword\twordnet\n",
    )


def _invoke(args: list[str]) -> dict:
    result = runner.invoke(cli.app, args)
    assert result.exit_code == 0, result.output
    # Structured logs go to stderr; only stdout carries the JSON summary.
    return json.loads(result.stdout)


def test_cli_import_wordnet(tmp_path, offline_cli):
    store = tmp_path / "store"
    summary = _invoke(
        ["import-wordnet", "--from-list", str(_candidates(tmp_path)), "--store", str(store)]
    )
    assert summary["imported"] == 2  # dog and einstein; the v1.3 row is not ours
    assert summary["absent"] == 1
    assert summary["absent_sample"] == ["notaword"]
    assert summary["skipped"] == 0
    assert summary["pos_entries"] == 3
    assert summary["senses"] == 4
    assert summary["domains_from_lexname"] == 2  # noun.animal and verb.motion
    assert summary["cost_usd"] == 0.0
    entry = LexemeStore(StoreConfig(root=store)).read("dog")
    assert entry is not None
    assert entry.headword == "dog"


def test_cli_import_wordnet_is_idempotent(tmp_path, offline_cli):
    store = tmp_path / "store"
    args = ["import-wordnet", "--from-list", str(_candidates(tmp_path)), "--store", str(store)]
    _invoke(args)
    again = _invoke(args)
    assert again["imported"] == 0
    assert again["skipped"] == 2
    forced = _invoke([*args, "--force"])
    assert forced["imported"] == 2


def test_cli_import_wordnet_limit_and_offset(tmp_path, offline_cli):
    store = tmp_path / "store"
    summary = _invoke(
        [
            "import-wordnet",
            "--from-list",
            str(_candidates(tmp_path)),
            "--store",
            str(store),
            "--offset",
            "1",
            "--limit",
            "1",
        ]
    )
    assert summary["words"] == 1
    assert summary["imported"] == 1
    assert LexemeStore(StoreConfig(root=store)).read("dog") is None


def test_cli_import_wordnet_rejects_a_missing_list(tmp_path, offline_cli):
    result = runner.invoke(cli.app, ["import-wordnet", "--from-list", str(tmp_path / "nope.tsv")])
    assert result.exit_code != 0


def test_cli_import_wordnet_reports_an_unavailable_corpus(tmp_path, monkeypatch):
    def unavailable() -> None:
        raise WordNetUnavailableError("nltk is not installed")

    monkeypatch.setattr(cli, "load_wordnet", unavailable)
    result = runner.invoke(cli.app, ["import-wordnet", "--from-list", str(_candidates(tmp_path))])
    assert result.exit_code != 0
    assert "nltk is not installed" in result.output


# --------------------------------------------------------------------------------------
# Against the real corpus, when it is installed
# --------------------------------------------------------------------------------------


@pytest.fixture
def real_corpus():
    from opengloss_generator.wordnet_import import load_wordnet  # noqa: PLC0415 - optional extra

    try:
        return load_wordnet()
    except WordNetUnavailableError as exc:
        pytest.skip(str(exc))


def test_real_dog_entry(real_corpus):
    entry = entry_for("dog", corpus=real_corpus)
    assert entry is not None
    assert entry.headword == "dog"
    assert [pos.pos for pos in entry.pos_entries] == [PartOfSpeech.NOUN, PartOfSpeech.VERB]
    noun = entry.pos_entries[0]
    assert [s.index for s in noun.senses] == list(range(len(noun.senses)))
    assert noun.senses[0].domain is DomainTag.NATURE_ANIMALS
    assert "Canis familiaris" in _targets(entry, 0, RelationType.SYNONYM)


def test_real_import_is_byte_stable(real_corpus):
    """Two builds of one word agree on everything but the timestamps (see `_pointed_at`)."""
    first = entry_for("good", corpus=real_corpus).model_dump(mode="json")
    second = entry_for("good", corpus=real_corpus).model_dump(mode="json")
    for payload in (first, second):
        payload.pop("created_at")
        payload.pop("updated_at")
        for record in payload["provenance"].values():
            record.pop("generated_at")
    assert first == second


def test_real_wordnet_casing(real_corpus):
    assert entry_for("a battery", corpus=real_corpus).headword == "A battery"
    assert entry_for("11 november", corpus=real_corpus).headword == "11 November"


# --------------------------------------------------------------------------------------
# D-81 — entity_type and wikidata_qid are written, not defaulted
# --------------------------------------------------------------------------------------
#
# The store's 20,743 proper nouns all carried `entity_type = other` because D-12, D-18 and
# this importer each wrote the placeholder and nothing ever replaced it. Two sources
# replace it here, and the order between them is the point: a candidate list that *says*
# what a name is beats WordNet's supersense, and WordNet's supersense beats the
# placeholder.


def _candidates_tsv(tmp_path: Path, rows: str, *, header: str | None = None) -> Path:
    """Write a candidate TSV and return its path."""
    columns = header or "name\tword\tentity_type\tsource\tqid\tnotes"
    path = tmp_path / "candidates.tsv"
    path.write_text(f"{columns}\n{rows}", encoding="utf-8")
    return path


def test_entity_type_and_qid_come_from_the_candidate_row(corpus):
    entry = entry_for("einstein", corpus=corpus, entity_type=EntityType.PERSON, wikidata_qid="Q937")
    assert entry.proper_noun is not None
    assert entry.proper_noun.entity_type is EntityType.PERSON
    assert entry.proper_noun.wikidata_qid == "Q937"


def test_the_candidate_row_beats_the_instance_hypernyms_supersense(corpus):
    # `physicist.n.01` is `noun.person`, so WordNet alone would say `person`; a list that
    # says otherwise is a better source than a supersense and wins.
    entry = entry_for("einstein", corpus=corpus, entity_type=EntityType.ORGANIZATION)
    assert entry.proper_noun.entity_type is EntityType.ORGANIZATION


def test_a_proper_noun_with_no_instance_hypernym_keeps_the_placeholder(corpus):
    # "A battery" is capitalised, so `wordnet_kind` calls it a proper noun, but it has no
    # instance hypernym at all — there is no supersense to read, and `other` stays the
    # honest answer for the `entity_type` retrofit pass to buy.
    entry = entry_for("a battery", corpus=corpus)
    assert entry.proper_noun is not None
    assert entry.proper_noun.entity_type is EntityType.OTHER
    assert entry.proper_noun.wikidata_qid is None


def test_entity_type_for_lexnames_maps_the_documented_supersenses():
    assert entity_type_for_lexnames(["noun.person"]) is EntityType.PERSON
    assert entity_type_for_lexnames(["noun.location"]) is EntityType.PLACE
    assert entity_type_for_lexnames(["noun.group"]) is EntityType.ORGANIZATION
    assert entity_type_for_lexnames(["noun.communication"]) is EntityType.WORK
    assert entity_type_for_lexnames(["noun.artifact"]) is EntityType.WORK
    assert entity_type_for_lexnames(["noun.act"]) is EntityType.EVENT
    assert entity_type_for_lexnames(["noun.event"]) is EntityType.EVENT


def test_entity_type_for_lexnames_is_none_rather_than_other_when_it_cannot_say():
    # `None` is "this source has nothing to say", which the caller distinguishes from
    # `other`, "this source says none of the seven fits".
    assert entity_type_for_lexnames(["noun.substance", "verb.motion"]) is None
    assert entity_type_for_lexnames([]) is None


def test_entity_type_for_lexnames_breaks_a_tie_by_a_fixed_precedence():
    # "Washington" is an instance of both a statesman and a national capital, and pointer
    # order is WordNet's business, so the answer must not depend on it.
    both = ["noun.location", "noun.person"]
    assert entity_type_for_lexnames(both) is EntityType.PERSON
    assert entity_type_for_lexnames(list(reversed(both))) is EntityType.PERSON


def test_read_candidate_rows_reads_the_typed_columns(tmp_path):
    path = _candidates_tsv(
        tmp_path,
        "Abraham Lincoln\tAbraham Lincoln\tperson\twordnet\tQ91\t"
        "alias_of candidate: store has 'lincoln'; wn\n"
        "Denver\tDenver\tplace\tname_seed\tQ16554\t\n",
    )
    rows = read_candidate_rows(path)
    assert [row.lexeme_id for row in rows] == ["abraham_lincoln", "denver"]
    assert rows[0].entity_type is EntityType.PERSON
    assert rows[0].wikidata_qid == "Q91"
    assert rows[0].alias_target == "lincoln"
    assert rows[1].alias_target is None


def test_read_candidate_rows_filters_by_source(tmp_path):
    path = _candidates_tsv(
        tmp_path,
        "Abraham Lincoln\tAbraham Lincoln\tperson\twordnet\tQ91\t\n"
        "Denver\tDenver\tplace\tname_seed\tQ16554\t\n",
    )
    assert [row.word for row in read_candidate_rows(path, source="wordnet")] == ["Abraham Lincoln"]


def test_read_candidate_rows_drops_an_unknown_type_and_a_malformed_qid(tmp_path):
    # A candidate list is an outside file: a widened builder must not be able to smuggle
    # an out-of-vocabulary type or a bad join key into the store.
    path = _candidates_tsv(tmp_path, "X\tX\tspaceship\tname_seed\tnot-a-qid\t\n")
    row = read_candidate_rows(path)[0]
    assert row.entity_type is None
    assert row.wikidata_qid is None


def test_read_candidate_rows_of_a_missing_file_is_empty(tmp_path):
    # The tier lists are gitignored (D-75), so an absent file is an ordinary state of the
    # tree and must make the readers a no-op rather than an error.
    assert read_candidate_rows(tmp_path / "nope.tsv") == []
    assert candidate_index(tmp_path / "nope.tsv") == {}


def test_read_candidate_rows_needs_a_word_column(tmp_path):
    path = _candidates_tsv(tmp_path, "x\n", header="name\tqid")
    with pytest.raises(ValueError, match="no 'word' column"):
        read_candidate_rows(path)


def test_iter_entries_types_from_the_candidate_index(corpus, tmp_path):
    path = _candidates_tsv(tmp_path, "Einstein\tEinstein\torganization\tname_seed\tQ937\t\n")
    ((_, entry),) = list(
        iter_entries(["einstein"], corpus=corpus, candidates=candidate_index(path))
    )
    assert entry.proper_noun.entity_type is EntityType.ORGANIZATION
    assert entry.proper_noun.wikidata_qid == "Q937"
