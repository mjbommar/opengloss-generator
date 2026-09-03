"""F9 `export-pretrain`: dictionary/thesaurus/encyclopedia/usage-note documents (D-61).

Offline throughout: every entry is built in code (docs/RETRIEVAL-DATA-PLAN.md rule 4),
no model is ever constructed. ``export_pretrain`` and ``documents_for_entry`` make no
model calls at all, so there is nothing to script.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from opengloss_generator import cli
from opengloss_generator.config import StoreConfig
from opengloss_generator.export.pretrain import (
    TEMPLATES,
    documents_for_entry,
    export_pretrain,
)
from opengloss_generator.identity import edge_id, sense_id
from opengloss_generator.readability import word_count
from opengloss_generator.schema import (
    Contrast,
    ContrastVerdict,
    Etymology,
    EtymologySegment,
    Example,
    Lexeme,
    LexemeKind,
    Morphology,
    PartOfSpeech,
    POSEntry,
    ReadingLevel,
    Register,
    Relation,
    RelationTarget,
    RelationType,
    Rendition,
    Renditions,
    Sense,
    canonical_rendition,
)
from opengloss_generator.store import LexemeStore


def _store(tmp_path: Path) -> LexemeStore:
    return LexemeStore(StoreConfig(root=tmp_path / "store", fsync_on_write=False))


def _rich_entry(headword: str = "abseil") -> Lexeme:
    """Build an entry exercising every renderer path.

    Two live senses, one retired, morphology, all four relation types, register
    variants, grade_5 renditions on only part of the content (so a fallback is
    exercised), etymology, encyclopedia, lexical explanation, and one contrast.
    """
    live_sense = Sense(
        index=0,
        gloss=Renditions[str](
            root=[
                canonical_rendition("To descend a rock face using a rope."),
                Rendition[str](
                    reading_level=ReadingLevel.GRADE_5,
                    style=Register.PLAIN,
                    content="To climb down a cliff using a rope.",
                ),
                Rendition[str](
                    reading_level=ReadingLevel.NEUTRAL,
                    style=Register.INFORMAL,
                    content="To rope your way down a cliff.",
                ),
                Rendition[str](
                    reading_level=ReadingLevel.NEUTRAL,
                    style=Register.TECHNICAL,
                    content="To perform a controlled descent of a vertical face via rope.",
                ),
            ]
        ),
        examples=Renditions[Example](
            root=[
                canonical_rendition(Example(text="They abseiled down the cliff.", span=(5, 13))),
                Rendition[Example](
                    reading_level=ReadingLevel.NEUTRAL,
                    style=Register.PLAIN,
                    content=Example(text="The instructor abseiled first to check the anchor."),
                ),
                Rendition[Example](
                    reading_level=ReadingLevel.GRADE_5,
                    style=Register.PLAIN,
                    content=Example(text="She abseiled down the small hill."),
                ),
            ]
        ),
        relations=[
            Relation(type=RelationType.SYNONYM, target=RelationTarget(term="rappel")),
            Relation(type=RelationType.ANTONYM, target=RelationTarget(term="ascend")),
            Relation(type=RelationType.HYPERNYM, target=RelationTarget(term="descend")),
            Relation(type=RelationType.SEE_ALSO, target=RelationTarget(term="climb")),
        ],
    )
    bare_sense = Sense.of(1, "A second, relation-less sense of abseil.")
    retired_sense = Sense.of(2, "A retired sense that must never appear anywhere.", retired=True)

    entry = Lexeme.empty(
        headword,
        kind=LexemeKind.SIMPLEX,
        pos_entries=[
            POSEntry(
                pos=PartOfSpeech.VERB,
                senses=[live_sense, bare_sense, retired_sense],
                morphology=Morphology(
                    past_tense="abseiled",
                    present_participle="abseiling",
                    third_person_singular="abseils",
                    derivations=["abseiler"],
                ),
            )
        ],
        etymology=Etymology(
            summary="From German Abseil, to rope down.",
            segments=[
                EtymologySegment(language="German", form="abseilen", meaning="to rope down"),
            ],
            cognates=["Abseil"],
        ),
        encyclopedia=Renditions[str](
            root=[canonical_rendition("Abseiling is a controlled descent.")]
        ),
        lexical_explanation=Renditions[str](
            root=[canonical_rendition("Abseil entered English from mountaineering German.")]
        ),
    )
    entry.contrasts.append(
        Contrast(
            edge_id=edge_id(sense_id(entry.lexeme_id, "verb", 0), "synonym", "rappel"),
            text=Renditions[str](root=[canonical_rendition("Rappel is the American spelling.")]),
            verdict=ContrastVerdict.RELATED_AS_TYPED,
        )
    )
    return entry


def _sparse_entry(headword: str = "sparseword") -> Lexeme:
    """An entry with nothing beyond a canonical gloss.

    No relations, no register variants, no encyclopedia/etymology/explanation, no
    contrasts. Only the dictionary template should ever have anything to say about it.
    """
    return Lexeme.empty(
        headword,
        kind=LexemeKind.SIMPLEX,
        pos_entries=[
            POSEntry(
                pos=PartOfSpeech.NOUN,
                senses=[Sense.of(0, "A word with nothing else attached.")],
            )
        ],
    )


def _all_retired_entry(headword: str = "goneword") -> Lexeme:
    """An entry whose only sense is retired: nothing should ever be rendered."""
    return Lexeme.empty(
        headword,
        kind=LexemeKind.SIMPLEX,
        pos_entries=[
            POSEntry(
                pos=PartOfSpeech.NOUN,
                senses=[Sense.of(0, "A retired sense.", retired=True)],
            )
        ],
    )


# --------------------------------------------------------------------------------------
# Each template renders, with the content it is documented to render.
# --------------------------------------------------------------------------------------


def test_dictionary_template_renders_headword_pos_forms_senses_and_examples():
    entry = _rich_entry()
    docs = documents_for_entry(entry, templates=["dictionary"])
    assert len(docs) == 1
    text = docs[0].text
    assert text.startswith("# abseil")
    assert "## Verb" in text
    assert "past tense: abseiled" in text
    assert "derived forms: abseiler" in text
    assert "1. To descend a rock face using a rope." in text
    assert '"They abseiled down the cliff."' in text
    assert "2. A second, relation-less sense of abseil." in text
    assert text.count('"They abseiled down the cliff."') == 1


def test_thesaurus_template_renders_prose_relation_lists():
    entry = _rich_entry()
    docs = documents_for_entry(entry, templates=["thesaurus"])
    assert len(docs) == 1
    text = docs[0].text
    assert "Synonyms: rappel." in text
    assert "Antonyms: ascend." in text
    assert "Broader terms: descend." in text
    assert "See also: climb." in text
    # the relation-less sense contributes nothing
    assert "relation-less" not in text


def test_encyclopedia_template_renders_overview_etymology_and_explanation():
    entry = _rich_entry()
    docs = documents_for_entry(entry, templates=["encyclopedia"])
    assert len(docs) == 1
    text = docs[0].text
    assert "## Overview" in text
    assert "Abseiling is a controlled descent." in text
    assert "## Etymology" in text
    assert "From German Abseil, to rope down." in text
    assert 'appeared as "abseilen"' in text
    assert "Cognates include Abseil." in text
    assert "## Why This Word" in text
    assert "Abseil entered English from mountaineering German." in text


def test_usage_note_template_renders_registers_side_by_side_and_contrast():
    entry = _rich_entry()
    docs = documents_for_entry(entry, templates=["usage_note"])
    assert len(docs) == 1
    text = docs[0].text
    technical_line = (
        "In technical writing: To perform a controlled descent of a vertical face via rope."
    )
    assert "Informally: To rope your way down a cliff." in text
    assert technical_line in text
    assert "## Related Terms" in text
    assert "Compared to rappel: Rappel is the American spelling." in text


def test_usage_note_omits_registers_never_written(monkeypatch: pytest.MonkeyPatch):
    """A register with no rendition anywhere is simply absent, not a blank line."""
    entry = _rich_entry()
    docs = documents_for_entry(entry, templates=["usage_note"])
    text = docs[0].text
    assert "In slang" not in text
    assert "In marketing" not in text
    assert "In-house" not in text


# --------------------------------------------------------------------------------------
# Retired senses are absent everywhere.
# --------------------------------------------------------------------------------------


def test_retired_sense_absent_from_every_template():
    entry = _rich_entry()
    for template in TEMPLATES:
        docs = documents_for_entry(entry, templates=[template])
        for doc in docs:
            assert "retired sense that must never appear" not in doc.text


def test_entry_with_only_a_retired_sense_renders_nothing():
    entry = _all_retired_entry()
    docs = documents_for_entry(entry, templates=list(TEMPLATES))
    assert docs == []


# --------------------------------------------------------------------------------------
# A template with nothing to say for an entry is skipped, never emitted empty.
# --------------------------------------------------------------------------------------


def test_sparse_entry_only_renders_dictionary():
    entry = _sparse_entry()
    docs = documents_for_entry(entry, templates=list(TEMPLATES))
    rendered_templates = {doc.template for doc in docs}
    assert rendered_templates == {"dictionary"}


def test_no_document_ever_contains_an_empty_heading():
    """A heading is always followed by real content, never another heading or the end.

    Either of those would be the signature of a section that was emitted empty.
    """
    entry = _rich_entry()
    for template in TEMPLATES:
        for level in (ReadingLevel.NEUTRAL, ReadingLevel.GRADE_5, ReadingLevel.COLLEGE):
            for doc in documents_for_entry(entry, templates=[template], levels=[level]):
                lines = doc.text.split("\n")
                for index, line in enumerate(lines):
                    if not line.startswith("## "):
                        continue
                    where = f"{template}/{level}"
                    assert index + 1 < len(lines), f"{where}: trailing empty heading"
                    following = lines[index + 1]
                    assert not following.startswith("#"), f"{where}: back-to-back headings"
                    assert following.strip(), f"{where}: blank line after heading"


# --------------------------------------------------------------------------------------
# Fallback to neutral is recorded.
# --------------------------------------------------------------------------------------


def test_fallback_to_neutral_is_recorded_in_level_used():
    entry = _rich_entry()
    # grade_10 has no dedicated renditions anywhere on this entry -> every section
    # falls back to (neutral, plain)/(neutral, register).
    docs = documents_for_entry(entry, templates=["encyclopedia"], levels=[ReadingLevel.GRADE_10])
    assert len(docs) == 1
    assert docs[0].level == "grade_10"
    assert docs[0].level_used == "neutral"


def test_exact_level_match_is_not_reported_as_fallback():
    entry = _rich_entry()
    docs = documents_for_entry(entry, templates=["dictionary"], levels=[ReadingLevel.GRADE_5])
    assert len(docs) == 1
    # sense 0's gloss and one example exist at grade_5; sense 1 has none there and
    # falls back -- so the document as a whole is still flagged as a fallback.
    assert docs[0].level_used == "neutral"

    # A level with genuinely nothing but the canonical text everywhere renders neutral
    # cleanly, with no fallback flag at all.
    neutral_docs = documents_for_entry(
        entry, templates=["thesaurus"], levels=[ReadingLevel.NEUTRAL]
    )
    assert neutral_docs[0].level == neutral_docs[0].level_used == "neutral"


# --------------------------------------------------------------------------------------
# Ids, word counts, and determinism.
# --------------------------------------------------------------------------------------


def test_document_id_is_derived_and_stable():
    entry = _rich_entry()
    docs = documents_for_entry(entry, templates=["dictionary"], levels=[ReadingLevel.COLLEGE])
    assert docs[0].id == "abseil#pretrain-dictionary-college"
    # the id is a pure function of (lexeme_id, template, level), not randomly assigned
    again = documents_for_entry(entry, templates=["dictionary"], levels=[ReadingLevel.COLLEGE])
    assert docs[0].id == again[0].id


def test_n_words_matches_word_count_of_text():
    entry = _rich_entry()
    for doc in documents_for_entry(entry, templates=list(TEMPLATES)):
        assert doc.n_words == word_count(doc.text)
        assert doc.n_words > 0


def test_documents_for_entry_is_deterministic():
    entry = _rich_entry()
    first = documents_for_entry(entry, templates=list(TEMPLATES), per_entry=2, seed=7)
    second = documents_for_entry(entry, templates=list(TEMPLATES), per_entry=2, seed=7)
    assert [d.as_dict() for d in first] == [d.as_dict() for d in second]


def test_per_entry_seed_selects_a_deterministic_subset_and_mixes_across_entries():
    templates_per_headword: dict[str, set[str]] = {}
    for i in range(12):
        entry = _rich_entry(headword=f"abseil{i}")
        docs = documents_for_entry(entry, templates=list(TEMPLATES), per_entry=2, seed=3)
        chosen = {d.template for d in docs}
        assert len(chosen) == 2
        templates_per_headword[entry.headword] = chosen
    # the corpus mixes -- not every entry drew the same pair of templates
    assert len(set(map(frozenset, templates_per_headword.values()))) > 1


def test_per_entry_none_or_at_or_above_available_count_renders_every_template():
    entry = _rich_entry()
    all_templates = {d.template for d in documents_for_entry(entry, templates=list(TEMPLATES))}
    assert all_templates == set(TEMPLATES)
    capped_high = {
        d.template for d in documents_for_entry(entry, templates=list(TEMPLATES), per_entry=99)
    }
    assert capped_high == set(TEMPLATES)


def test_per_entry_zero_renders_nothing():
    entry = _rich_entry()
    docs = documents_for_entry(entry, templates=list(TEMPLATES), per_entry=0)
    assert docs == []


# --------------------------------------------------------------------------------------
# No JSON/YAML duplicate content and no special tokens leak into the prose.
# --------------------------------------------------------------------------------------


def test_text_is_plain_prose_not_json_or_yaml():
    entry = _rich_entry()
    for doc in documents_for_entry(entry, templates=list(TEMPLATES)):
        assert "{" not in doc.text
        assert "}" not in doc.text
        assert "<|" not in doc.text


# --------------------------------------------------------------------------------------
# export_pretrain: the whole-store JSONL writer.
# --------------------------------------------------------------------------------------


def test_export_pretrain_writes_jsonl_and_summarises_words(tmp_path: Path):
    store = _store(tmp_path)
    store.write(_rich_entry("abseil"))
    store.write(_rich_entry("rappel"))
    store.write(_sparse_entry())
    store.write(_all_retired_entry())

    out = tmp_path / "pretrain.jsonl"
    summary = export_pretrain(
        store, out, templates=list(TEMPLATES), levels=[ReadingLevel.NEUTRAL, ReadingLevel.GRADE_5]
    )

    assert summary.entries_scanned == 4
    assert summary.documents_written > 0
    assert summary.words_total == sum(summary.words_by_template.values())
    assert summary.words_total == sum(summary.words_by_level.values())
    assert set(summary.documents_by_template) <= set(TEMPLATES)
    assert set(summary.documents_by_level) == {"neutral", "grade_5"}

    lines = out.read_text(encoding="utf-8").splitlines()
    assert len(lines) == summary.documents_written
    rows = [json.loads(line) for line in lines]
    for row in rows:
        assert set(row) == {"id", "headword", "template", "level", "level_used", "text", "n_words"}
    # the all-retired entry never contributes a row
    assert all(row["headword"] != "goneword" for row in rows)


def test_export_pretrain_is_deterministic_across_runs(tmp_path: Path):
    store = _store(tmp_path)
    for i in range(5):
        store.write(_rich_entry(headword=f"word{i}"))

    out_a = tmp_path / "a.jsonl"
    out_b = tmp_path / "b.jsonl"
    export_pretrain(store, out_a, templates=list(TEMPLATES), per_entry=2, seed=1)
    export_pretrain(store, out_b, templates=list(TEMPLATES), per_entry=2, seed=1)
    assert out_a.read_text(encoding="utf-8") == out_b.read_text(encoding="utf-8")


def test_export_pretrain_from_list_restricts_entries(tmp_path: Path):
    store = _store(tmp_path)
    store.write(_rich_entry("abseil"))
    store.write(_rich_entry("rappel"))

    out = tmp_path / "restricted.jsonl"
    summary = export_pretrain(store, out, lexeme_ids=["abseil"])
    assert summary.entries_scanned == 1

    rows = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
    assert {row["headword"] for row in rows} == {"abseil"}


def test_cli_export_pretrain_smoke(tmp_path: Path):
    store_path = tmp_path / "store"
    store = LexemeStore(StoreConfig(root=store_path, fsync_on_write=False))
    store.write(_rich_entry("abseil"))

    out = tmp_path / "cli.jsonl"
    runner = CliRunner()
    result = runner.invoke(
        cli.app,
        [
            "export-pretrain",
            "--store",
            str(store_path),
            "--out",
            str(out),
            "--levels",
            "neutral,grade_5",
            "--templates",
            "dictionary,usage_note",
        ],
    )
    assert result.exit_code == 0, result.output
    assert out.is_file()

    summary = json.loads(result.stdout)
    assert summary["entries_scanned"] == 1
    assert set(summary["documents_by_template"]) <= {"dictionary", "usage_note"}


def test_cli_export_pretrain_rejects_unknown_template(tmp_path: Path):
    runner = CliRunner()
    result = runner.invoke(
        cli.app,
        [
            "export-pretrain",
            "--store",
            str(tmp_path / "store"),
            "--out",
            str(tmp_path / "out.jsonl"),
            "--templates",
            "not_a_template",
        ],
    )
    assert result.exit_code != 0


def test_cli_export_pretrain_rejects_per_entry_below_one(tmp_path: Path):
    runner = CliRunner()
    result = runner.invoke(
        cli.app,
        [
            "export-pretrain",
            "--store",
            str(tmp_path / "store"),
            "--out",
            str(tmp_path / "out.jsonl"),
            "--per-entry",
            "0",
        ],
    )
    assert result.exit_code != 0
