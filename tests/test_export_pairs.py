"""F1 ``export-pairs``: WiC-style and positive pairs mined from stored senses (free).

Offline, like ``test_audit.py``: entries are built directly with the schema, no model
call is ever involved (there is no stage here to script a payload for), and pairs are
read back off the written JSONL file to check both content and determinism.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from opengloss_generator.config import StoreConfig
from opengloss_generator.export.pairs import export_pairs
from opengloss_generator.schema import (
    EntryStatus,
    Example,
    Lexeme,
    LexemeKind,
    Morphology,
    PartOfSpeech,
    POSEntry,
    ReadingLevel,
    Register,
    Rendition,
    Renditions,
    Sense,
    canonical_rendition,
)
from opengloss_generator.store import LexemeStore
from opengloss_generator.taxonomy import DomainTag


def _store(tmp_path: Path) -> LexemeStore:
    """Return an empty store rooted under ``tmp_path``."""
    return LexemeStore(StoreConfig(root=tmp_path / "store", fsync_on_write=False))


def _example(text: str, *, level: ReadingLevel = ReadingLevel.NEUTRAL) -> Rendition[Example]:
    """Build one example rendition at ``level``/plain, spanning nothing in particular."""
    return Rendition[Example](reading_level=level, style=Register.PLAIN, content=Example(text=text))


def _entry(
    headword: str,
    senses: list[Sense],
    *,
    kind: LexemeKind = LexemeKind.SIMPLEX,
    status: EntryStatus = EntryStatus.COMPLETE,
    encyclopedia: str | None = None,
) -> Lexeme:
    """Build a one-POS entry with the given senses."""
    return Lexeme.empty(
        headword,
        kind=kind,
        status=status,
        pos_entries=[POSEntry(pos=PartOfSpeech.NOUN, senses=senses, morphology=Morphology())],
        encyclopedia=(
            Renditions[str](root=[canonical_rendition(encyclopedia)])
            if encyclopedia is not None
            else Renditions[str](root=[])
        ),
    )


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


# --------------------------------------------------------------------------------------
# wic_positive: all pairs of one sense's own examples
# --------------------------------------------------------------------------------------


def test_two_examples_on_one_sense_make_one_positive_pair(tmp_path):
    sense = Sense(
        index=0,
        gloss=Renditions[str](root=[canonical_rendition("A definition.")]),
        examples=Renditions[Example](
            root=[_example("First usage sentence."), _example("Second usage sentence.")]
        ),
    )
    store = _store(tmp_path)
    store.write(_entry("gavel", [sense]))

    outcome = export_pairs(store, tmp_path / "pairs.jsonl")

    records = _read_jsonl(tmp_path / "pairs.jsonl")
    positives = [r for r in records if r["kind"] == "wic_positive"]
    assert len(positives) == 1
    assert positives[0]["label"] == 1
    assert positives[0]["sense_a"] == positives[0]["sense_b"] == "gavel:noun:0"
    assert {positives[0]["text_a"], positives[0]["text_b"]} == {
        "First usage sentence.",
        "Second usage sentence.",
    }
    assert outcome.by_kind["wic_positive"] == 1


def test_three_examples_make_three_positive_pairs(tmp_path):
    sense = Sense(
        index=0,
        gloss=Renditions[str](root=[canonical_rendition("A definition.")]),
        examples=Renditions[Example](
            root=[
                _example("One."),
                _example("Two.", level=ReadingLevel.GRADE_1),
                _example("Three."),
            ]
        ),
    )
    store = _store(tmp_path)
    store.write(_entry("gavel", [sense]))

    export_pairs(store, tmp_path / "pairs.jsonl")

    positives = [r for r in _read_jsonl(tmp_path / "pairs.jsonl") if r["kind"] == "wic_positive"]
    assert len(positives) == 3  # C(3, 2)


def test_a_single_example_makes_no_positive_pair(tmp_path):
    sense = Sense(
        index=0,
        gloss=Renditions[str](root=[canonical_rendition("A definition.")]),
        examples=Renditions[Example](root=[_example("Only one usage sentence.")]),
    )
    store = _store(tmp_path)
    store.write(_entry("gavel", [sense]))

    outcome = export_pairs(store, tmp_path / "pairs.jsonl")

    assert "wic_positive" not in outcome.by_kind


# --------------------------------------------------------------------------------------
# wic_hard_negative: one representative example per pair of live senses
# --------------------------------------------------------------------------------------


def test_two_senses_of_one_headword_make_one_hard_negative(tmp_path):
    senses = [
        Sense(
            index=0,
            gloss=Renditions[str](root=[canonical_rendition("Sense zero.")]),
            examples=Renditions[Example](root=[_example("Bank of the river was muddy.")]),
        ),
        Sense(
            index=1,
            gloss=Renditions[str](root=[canonical_rendition("Sense one.")]),
            examples=Renditions[Example](root=[_example("She deposited cash at the bank.")]),
        ),
    ]
    store = _store(tmp_path)
    store.write(_entry("bank", senses))

    outcome = export_pairs(store, tmp_path / "pairs.jsonl")

    records = _read_jsonl(tmp_path / "pairs.jsonl")
    negatives = [r for r in records if r["kind"] == "wic_hard_negative"]
    assert len(negatives) == 1
    assert negatives[0]["label"] == 0
    assert {negatives[0]["sense_a"], negatives[0]["sense_b"]} == {"bank:noun:0", "bank:noun:1"}
    assert outcome.by_label["0"] == 1


def test_three_live_senses_make_three_hard_negatives(tmp_path):
    senses = [
        Sense(
            index=i,
            gloss=Renditions[str](root=[canonical_rendition(f"Sense {i}.")]),
            examples=Renditions[Example](root=[_example(f"Usage for sense {i}.")]),
        )
        for i in range(3)
    ]
    store = _store(tmp_path)
    store.write(_entry("bank", senses))

    outcome = export_pairs(store, tmp_path / "pairs.jsonl")

    assert outcome.by_kind["wic_hard_negative"] == 3  # C(3, 2)


def test_a_retired_sense_contributes_no_pair_at_all(tmp_path):
    senses = [
        Sense(
            index=0,
            gloss=Renditions[str](root=[canonical_rendition("Sense zero.")]),
            examples=Renditions[Example](root=[_example("Live sense usage.")]),
        ),
        Sense(
            index=1,
            gloss=Renditions[str](root=[canonical_rendition("Sense one.")]),
            examples=Renditions[Example](root=[_example("Retired sense usage.")]),
            retired=True,
        ),
    ]
    store = _store(tmp_path)
    store.write(_entry("bank", senses))

    export_pairs(store, tmp_path / "pairs.jsonl")

    records = _read_jsonl(tmp_path / "pairs.jsonl")
    assert not any("Retired sense usage." in (r["text_a"], r["text_b"]) for r in records)
    assert not any(r["kind"] == "wic_hard_negative" for r in records)


def test_a_retired_entry_contributes_no_pair(tmp_path):
    sense = Sense(
        index=0,
        gloss=Renditions[str](root=[canonical_rendition("A definition.")]),
        examples=Renditions[Example](
            root=[_example("First usage sentence."), _example("Second usage sentence.")]
        ),
    )
    store = _store(tmp_path)
    store.write(_entry("gavel", [sense], status=EntryStatus.RETIRED))

    outcome = export_pairs(store, tmp_path / "pairs.jsonl")

    assert outcome.pairs_written == 0
    assert outcome.entries_scanned == 1
    assert outcome.entries_with_pairs == 0


# --------------------------------------------------------------------------------------
# example_gloss / example_encyclopedia
# --------------------------------------------------------------------------------------


def test_example_gloss_pair_uses_the_canonical_gloss(tmp_path):
    sense = Sense(
        index=0,
        gloss=Renditions[str](root=[canonical_rendition("A tool for striking things.")]),
        examples=Renditions[Example](root=[_example("The judge banged the gavel.")]),
    )
    store = _store(tmp_path)
    store.write(_entry("gavel", [sense]))

    export_pairs(store, tmp_path / "pairs.jsonl")

    gloss_pairs = [r for r in _read_jsonl(tmp_path / "pairs.jsonl") if r["kind"] == "example_gloss"]
    assert len(gloss_pairs) == 1
    assert gloss_pairs[0]["label"] == 1
    assert gloss_pairs[0]["text_a"] == "The judge banged the gavel."
    assert gloss_pairs[0]["text_b"] == "A tool for striking things."
    assert gloss_pairs[0]["span_b"] is None


def test_example_encyclopedia_pair_present_when_encyclopedia_exists(tmp_path):
    sense = Sense(
        index=0,
        gloss=Renditions[str](root=[canonical_rendition("A tool for striking things.")]),
        examples=Renditions[Example](root=[_example("The judge banged the gavel.")]),
    )
    store = _store(tmp_path)
    store.write(
        _entry(
            "gavel", [sense], encyclopedia="A gavel is a small ceremonial mallet used by judges."
        )
    )

    export_pairs(store, tmp_path / "pairs.jsonl")

    encyclopedia_pairs = [
        r for r in _read_jsonl(tmp_path / "pairs.jsonl") if r["kind"] == "example_encyclopedia"
    ]
    assert len(encyclopedia_pairs) == 1
    assert encyclopedia_pairs[0]["text_b"] == "A gavel is a small ceremonial mallet used by judges."
    assert encyclopedia_pairs[0]["sense_b"] is None


def test_no_example_encyclopedia_pair_without_an_encyclopedia_rendition(tmp_path):
    sense = Sense(
        index=0,
        gloss=Renditions[str](root=[canonical_rendition("A tool for striking things.")]),
        examples=Renditions[Example](root=[_example("The judge banged the gavel.")]),
    )
    store = _store(tmp_path)
    store.write(_entry("gavel", [sense]))

    outcome = export_pairs(store, tmp_path / "pairs.jsonl")

    assert "example_encyclopedia" not in outcome.by_kind


def test_no_example_encyclopedia_pair_for_a_polysemous_entry(tmp_path):
    # A two-sense entry's encyclopedia article is entry-level, not sense-level (D-71):
    # it must not be paired with either sense's example.
    senses = [
        Sense(
            index=i,
            gloss=Renditions[str](root=[canonical_rendition(f"Sense {i}.")]),
            examples=Renditions[Example](root=[_example(f"Usage of sense {i}.")]),
        )
        for i in range(2)
    ]
    store = _store(tmp_path)
    store.write(_entry("bank", senses, encyclopedia="A bank is a financial institution."))

    outcome = export_pairs(store, tmp_path / "pairs.jsonl")

    assert "example_encyclopedia" not in outcome.by_kind
    records = _read_jsonl(tmp_path / "pairs.jsonl")
    assert all(r["live_senses"] == 2 for r in records)


def test_example_encyclopedia_pair_kept_for_a_monosemous_entry(tmp_path):
    # The mirror case: exactly one live sense, so the entry-level encyclopedia article is
    # a legitimate positive for it (D-71).
    sense = Sense(
        index=0,
        gloss=Renditions[str](root=[canonical_rendition("A tool for striking things.")]),
        examples=Renditions[Example](root=[_example("The judge banged the gavel.")]),
    )
    store = _store(tmp_path)
    store.write(
        _entry(
            "gavel", [sense], encyclopedia="A gavel is a small ceremonial mallet used by judges."
        )
    )

    outcome = export_pairs(store, tmp_path / "pairs.jsonl")

    assert outcome.by_kind["example_encyclopedia"] == 1
    records = _read_jsonl(tmp_path / "pairs.jsonl")
    encyclopedia_pairs = [r for r in records if r["kind"] == "example_encyclopedia"]
    assert encyclopedia_pairs[0]["live_senses"] == 1


# --------------------------------------------------------------------------------------
# wic_easy_negative: opt-in, seeded, cross-headword
# --------------------------------------------------------------------------------------


def _domain_entry(headword: str, domain: DomainTag, text: str) -> Lexeme:
    sense = Sense(
        index=0,
        gloss=Renditions[str](root=[canonical_rendition(f"A definition of {headword}.")]),
        examples=Renditions[Example](root=[_example(text)]),
        domain=domain,
    )
    return _entry(headword, [sense])


def test_easy_negatives_are_off_by_default(tmp_path):
    store = _store(tmp_path)
    store.write(_domain_entry("alpha", DomainTag.ARTS_MUSIC, "Alpha plays the alpha instrument."))
    store.write(_domain_entry("beta", DomainTag.ARTS_MUSIC, "Beta plays the beta instrument."))

    outcome = export_pairs(store, tmp_path / "pairs.jsonl")

    assert "wic_easy_negative" not in outcome.by_kind


def test_easy_negatives_pair_different_headwords_in_the_same_domain(tmp_path):
    store = _store(tmp_path)
    store.write(_domain_entry("alpha", DomainTag.ARTS_MUSIC, "Alpha plays the alpha instrument."))
    store.write(_domain_entry("beta", DomainTag.ARTS_MUSIC, "Beta plays the beta instrument."))
    store.write(_domain_entry("gamma", DomainTag.BUSINESS_FINANCE, "Gamma files a tax return."))

    outcome = export_pairs(store, tmp_path / "pairs.jsonl", easy_negatives=1, seed=0)

    negatives = [
        r for r in _read_jsonl(tmp_path / "pairs.jsonl") if r["kind"] == "wic_easy_negative"
    ]
    # alpha <-> beta only: gamma is a different domain and has no other member to pair with.
    assert len(negatives) == 2
    for record in negatives:
        assert record["label"] == 0
        assert record["headword"] != record["headword_b"]
    assert outcome.by_kind["wic_easy_negative"] == 2


def test_easy_negatives_are_deterministic_for_a_fixed_seed(tmp_path):
    store = _store(tmp_path)
    for i in range(6):
        store.write(
            _domain_entry(f"word{i}", DomainTag.ARTS_MUSIC, f"Word {i} used in a sentence.")
        )

    export_pairs(store, tmp_path / "a.jsonl", easy_negatives=2, seed=7)
    export_pairs(store, tmp_path / "b.jsonl", easy_negatives=2, seed=7)

    assert _read_jsonl(tmp_path / "a.jsonl") == _read_jsonl(tmp_path / "b.jsonl")


def test_a_different_seed_can_change_the_easy_negative_draw(tmp_path):
    store = _store(tmp_path)
    for i in range(8):
        store.write(
            _domain_entry(f"word{i}", DomainTag.ARTS_MUSIC, f"Word {i} used in a sentence.")
        )

    export_pairs(store, tmp_path / "a.jsonl", easy_negatives=1, seed=1)
    export_pairs(store, tmp_path / "b.jsonl", easy_negatives=1, seed=2)

    a_partners = {
        (r["sense_a"], r["sense_b"])
        for r in _read_jsonl(tmp_path / "a.jsonl")
        if r["kind"] == "wic_easy_negative"
    }
    b_partners = {
        (r["sense_a"], r["sense_b"])
        for r in _read_jsonl(tmp_path / "b.jsonl")
        if r["kind"] == "wic_easy_negative"
    }
    assert a_partners != b_partners


def test_a_sense_with_no_domain_never_produces_an_easy_negative(tmp_path):
    no_domain_sense = Sense(
        index=0,
        gloss=Renditions[str](root=[canonical_rendition("A definition of delta.")]),
        examples=Renditions[Example](root=[_example("Delta has no domain tag.")]),
    )
    store = _store(tmp_path)
    store.write(_entry("delta", [no_domain_sense]))
    store.write(_domain_entry("beta", DomainTag.ARTS_MUSIC, "Beta plays the beta instrument."))

    outcome = export_pairs(store, tmp_path / "pairs.jsonl", easy_negatives=5, seed=0)

    assert "wic_easy_negative" not in outcome.by_kind


# --------------------------------------------------------------------------------------
# Output order and the run summary
# --------------------------------------------------------------------------------------


def test_output_order_is_by_lexeme_id_not_write_order(tmp_path):
    store = _store(tmp_path)
    for headword in ["zebra", "apple", "mango"]:
        sense = Sense(
            index=0,
            gloss=Renditions[str](root=[canonical_rendition(f"A definition of {headword}.")]),
            examples=Renditions[Example](
                root=[_example(f"One {headword} sentence."), _example(f"Another {headword} one.")]
            ),
        )
        store.write(_entry(headword, [sense]))

    export_pairs(store, tmp_path / "pairs.jsonl")

    headwords_seen = [r["headword"] for r in _read_jsonl(tmp_path / "pairs.jsonl")]
    assert headwords_seen == sorted(headwords_seen)


def test_from_list_restricts_the_export(tmp_path):
    store = _store(tmp_path)
    for headword in ["alpha", "beta"]:
        sense = Sense(
            index=0,
            gloss=Renditions[str](root=[canonical_rendition(f"A definition of {headword}.")]),
            examples=Renditions[Example](
                root=[_example(f"One {headword} sentence."), _example(f"Another {headword} one.")]
            ),
        )
        store.write(_entry(headword, [sense]))

    outcome = export_pairs(store, tmp_path / "pairs.jsonl", lexeme_ids=["alpha"])

    records = _read_jsonl(tmp_path / "pairs.jsonl")
    assert {r["headword"] for r in records} == {"alpha"}
    assert outcome.entries_scanned == 1


def test_outcome_as_dict_matches_the_written_file(tmp_path):
    senses = [
        Sense(
            index=i,
            gloss=Renditions[str](root=[canonical_rendition(f"Sense {i}.")]),
            examples=Renditions[Example](root=[_example(f"Usage {i}a."), _example(f"Usage {i}b.")]),
        )
        for i in range(2)
    ]
    store = _store(tmp_path)
    store.write(_entry("bank", senses, encyclopedia="Banks hold money."))

    outcome = export_pairs(store, tmp_path / "pairs.jsonl")
    records = _read_jsonl(tmp_path / "pairs.jsonl")

    assert outcome.pairs_written == len(records)
    assert sum(outcome.by_label.values()) == len(records)
    assert sum(outcome.by_kind.values()) == len(records)
    summary = outcome.as_dict()
    assert summary["pairs_written"] == len(records)
    # wic_positive: 1 pair per sense (2 examples each) = 2; hard_negative: C(2,2)->1 pair;
    # gloss: 1 per sense = 2; no encyclopedia pairs -- bank has two live senses, so its
    # encyclopedia article is entry-level, not a valid positive for either one (D-71).
    assert outcome.by_kind == {
        "example_gloss": 2,
        "wic_hard_negative": 1,
        "wic_positive": 2,
    }


def test_creates_parent_directories_for_the_output_path(tmp_path):
    sense = Sense(
        index=0,
        gloss=Renditions[str](root=[canonical_rendition("A definition.")]),
        examples=Renditions[Example](root=[_example("Only one usage sentence.")]),
    )
    store = _store(tmp_path)
    store.write(_entry("gavel", [sense]))

    out = tmp_path / "nested" / "dir" / "pairs.jsonl"
    export_pairs(store, out)

    assert out.is_file()


@pytest.mark.parametrize("kind_field", ["kind", "label"])
def test_every_record_carries_the_documented_fields(tmp_path, kind_field):
    sense = Sense(
        index=0,
        gloss=Renditions[str](root=[canonical_rendition("A tool for striking things.")]),
        examples=Renditions[Example](
            root=[_example("The judge banged the gavel."), _example("A gavel struck the desk.")]
        ),
    )
    store = _store(tmp_path)
    store.write(_entry("gavel", [sense]))

    export_pairs(store, tmp_path / "pairs.jsonl")

    for record in _read_jsonl(tmp_path / "pairs.jsonl"):
        assert kind_field in record
        for key in (
            "headword",
            "headword_b",
            "sense_a",
            "sense_b",
            "text_a",
            "text_b",
            "span_a",
            "span_b",
            "label",
            "level_a",
            "level_b",
            "kind",
            "live_senses",
        ):
            assert key in record
